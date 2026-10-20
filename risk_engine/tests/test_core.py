# Scenarios A-H, L, M, Q, R, S (inference, data sufficiency, calibration, failures, determinism, boundaries).
import copy
import json
import threading

import numpy as np
import pandas as pd
import pytest

from conftest import NOW, PROFILE, ROOT, FixedModel, make_readings
from risk_engine import RiskEngine, feature_builder, input_validator, load_config
from risk_engine.alert_service import AlertService
from risk_engine.audit_service import AuditService
from risk_engine.model_service import ModelService, ModelUnavailable
from risk_engine.replay import ReplayData
from risk_engine.risk_policy import RiskPolicy
from risk_engine.schemas import EmergencyState, ExplanationStatus, Status, Tier


def ev(engine, req, **kw):
    r = dict(req)
    r.update(kw)
    return engine.evaluate(r["patient_profile"], r["clinical_readings"], r["wearable_readings"], now=r["now"])


# ---------------------------------------------------------------- A. valid, fresh readings
def test_A_valid_fresh_readings_give_ok(engine, request_ok):
    r = ev(engine, request_ok)
    assert r.status == Status.OK
    assert 0 < r.risk.raw_output < 1 and r.risk.tier in (Tier.LOW, Tier.MODERATE, Tier.HIGH)
    assert r.data_quality.status == "SUFFICIENT" and r.risk.policy_version and r.model.version
    json.dumps(r.model_dump(mode="json"))       # serialisable


def test_A_uses_exact_training_pipeline(engine, model, request_ok):
    req = request_ok
    v = input_validator.validate(req["patient_profile"], req["clinical_readings"], req["wearable_readings"], NOW, engine.cfg)
    t = feature_builder.hourly_table(v.readings, NOW, 0)
    X = feature_builder.feature_row(t, feature_builder.static_values(v), model.features)
    assert list(X.columns) == model.features
    p = model.bundle["estimator"].predict_proba(X)[:, 1][0]
    assert abs(ev(engine, req).risk.raw_output - p) < 1e-6


def test_A_replay_matches_notebook05_on_stays_without_gaps(engine):
    R = ReplayData(ROOT)
    gaps = R.dyn.groupby("stay_id").hour.apply(lambda x: (x.diff() > pd.Timedelta(hours=1)).any())
    stays = [s for s in R.test_stays() if not gaps[s]][:3]
    assert stays
    for sid in stays:
        h = R.last_hour(sid)
        r = engine.evaluate(**R.request(sid, h))
        ref = R.test[(R.test.stay_id == sid) & (R.test.hour == h)].risk_watch.iloc[0]
        if r.status == Status.OK:
            assert abs(r.risk.raw_output - ref) < 2e-3, (sid, r.risk.raw_output, ref)


# ---------------------------------------------------------------- B. stale mandatory data
def test_B_stale_data_is_insufficient_never_low(engine, request_ok):
    req = dict(request_ok, wearable_readings=make_readings(end=NOW - pd.Timedelta(hours=5)))
    r = ev(engine, req)
    assert r.status == Status.INSUFFICIENT_DATA and r.status_reason_code == "STALE_DATA"
    assert r.risk.tier is None and r.risk.raw_output is None and r.risk.calibrated_probability is None
    assert set(r.data_quality.stale_fields) == {"hr", "spo2", "rr"}
    assert "not a LOW-risk result" in r.sar.recommendation


# ---------------------------------------------------------------- C. missing mandatory input
def test_C_missing_signal(engine, request_ok):
    req = dict(request_ok, wearable_readings=make_readings(signals=("hr", "rr")))
    r = ev(engine, req)
    assert r.status == Status.INSUFFICIENT_DATA and r.status_reason_code == "MISSING_MANDATORY" and "spo2" in r.data_quality.missing_fields


def test_C_missing_profile_field(engine, request_ok):
    prof = dict(PROFILE, age_at_admit=None)
    r = ev(engine, request_ok, patient_profile=prof)
    assert r.status == Status.INSUFFICIENT_DATA and "age_at_admit" in r.data_quality.missing_fields and r.risk.tier is None


def test_C_no_readings(engine, request_ok):
    r = ev(engine, request_ok, wearable_readings=[])
    assert r.status == Status.INSUFFICIENT_DATA and r.status_reason_code == "NO_VALID_READINGS"


# ---------------------------------------------------------------- D. invalid units, timestamps, values
def test_D_validation_rules(cfg):
    t = (NOW - pd.Timedelta(minutes=10)).isoformat() + "Z"
    rd = [{"timestamp": t, "signal": "hr", "value": 80, "unit": "mmHg"},            # unsupported unit -> invalid
          {"timestamp": t, "signal": "spo2", "value": 0.97, "unit": "fraction"},    # normalised to 97 %
          {"timestamp": t, "signal": "hr", "value": 300, "unit": "bpm"},            # impossible -> excluded
          {"timestamp": t, "signal": "hr", "value": 190, "unit": "bpm"},            # unusual -> kept and flagged
          {"timestamp": "not a time", "signal": "rr", "value": 16},                 # bad timestamp
          {"timestamp": (NOW + pd.Timedelta(hours=2)).isoformat() + "Z", "signal": "rr", "value": 16},   # future
          {"timestamp": t, "signal": "rr", "value": float("nan")},                  # not finite
          {"timestamp": t, "signal": "temperature", "value": 37},                   # unknown signal
          {"timestamp": t, "signal": "rr", "value": 18, "unit": "breaths/min"},
          {"timestamp": t, "signal": "rr", "value": 18, "unit": "breaths/min"},      # duplicate
          {"timestamp": t, "signal": "rr", "value": 22, "unit": "breaths/min"}]      # conflict
    v = input_validator.validate(PROFILE, [], rd, NOW, cfg)
    c = v.counts
    assert c["unknown_unit"] == 1 and c["impossible_value"] == 1 and c["bad_timestamp"] == 1 and c["future_timestamp"] == 1
    assert c["non_finite"] == 1 and c["unknown_signal"] == 1 and c["duplicates_removed"] == 1 and c["conflicts"] == 1
    assert c["flagged_for_remeasurement"] == 1
    assert 97.0 in v.readings[v.readings.signal == "spo2"].value.tolist()
    assert 190.0 in v.readings[v.readings.signal == "hr"].value.tolist()          # unusual reading is not deleted
    assert 300.0 not in v.readings.value.tolist()


def test_D_clinical_units_normalised(cfg):
    v = input_validator.validate(PROFILE, [{"name": "ldl", "value": 3.0, "unit": "mmol/L"},
                                           {"name": "hba1c", "value": 48, "unit": "mmol/mol"},
                                           {"name": "ldl_bad", "value": 1}], [], NOW, cfg)
    assert abs(v.clinical["ldl"]["value"] - 116.01) < 0.01 and abs(v.clinical["hba1c"]["value"] - 6.54) < 0.01


def test_D_all_invalid_signal_gives_invalid_data(engine, request_ok):
    rd = [r for r in make_readings() if r["signal"] != "spo2"]
    rd += [dict(r, unit="mmHg") for r in make_readings() if r["signal"] == "spo2"]
    r = ev(engine, request_ok, wearable_readings=rd)
    assert r.status == Status.INSUFFICIENT_DATA and r.status_reason_code == "INVALID_DATA"


def test_D_low_quality_readings_excluded(engine, request_ok):
    r = ev(engine, request_ok, wearable_readings=make_readings(quality=0.2))
    assert r.status == Status.INSUFFICIENT_DATA and r.data_quality.validation_counts["low_quality"] > 0


# ---------------------------------------------------------------- E. calibrated output uses the right calibrator
def test_E_calibrated_model_uses_its_fitted_calibrator():
    cfg = load_config(overrides={"model.bundle": "models/cardiotwin_full_24h.pkl", "model.feature_set": "full"})
    ms = ModelService(ROOT, cfg, check_integrity=False)
    assert ms.calibration_status == "calibrated" and ms.calibrator.method == "sigmoid"
    R = ReplayData(ROOT)
    rows = R.test.head(25).merge(R.mf, on=["subject_id", "stay_id", "hour"], suffixes=("", "_mf"))
    X = rows[ms.features].astype(float)
    raw = ms.raw(X)
    cal = ms.calibrate(raw)
    assert np.allclose(cal, ms.calibrator.transform(raw)) and not np.allclose(cal, raw)
    assert np.allclose(cal, rows.risk_full.values, atol=1e-5)          # notebook 05 saved 5 decimals


# ---------------------------------------------------------------- F. missing calibrator
def test_F_identity_calibrator_is_labelled_uncalibrated(engine, request_ok):
    r = ev(engine, request_ok)
    assert r.model.calibration_status == "uncalibrated" and r.risk.calibrated_probability is None
    assert "uncalibrated" in r.risk.score_scale


def test_F_missing_calibrator_withholds_tier(cfg, model, request_ok):
    b = dict(model.bundle)
    b.pop("calibrator")
    ms = ModelService(ROOT, cfg, bundle=b)
    e = RiskEngine(ROOT, cfg, model_service=ms, alert_service=AlertService(cfg), audit_service=AuditService(cfg))
    r = ev(e, request_ok)
    assert r.status == Status.OK and r.model.calibration_status == "unavailable"
    assert r.risk.calibrated_probability is None and r.risk.tier is None and "withheld" in r.risk.tier_reason


# ---------------------------------------------------------------- G. SHAP failure
def test_G_shap_failure_keeps_score_and_invents_nothing(cfg, model, request_ok):
    def broken(*a, **k):
        raise RuntimeError("shap service down")
    e = RiskEngine(ROOT, cfg, model_service=model, explainer=broken, alert_service=AlertService(cfg), audit_service=AuditService(cfg))
    r = ev(e, request_ok)
    assert r.status == Status.OK and r.risk.raw_output is not None
    assert r.explanation.status == ExplanationStatus.UNAVAILABLE and r.explanation.top_contributors == []
    assert r.explanation.shap_values is None and "explanation" in r.degraded_components
    assert "No explanation is available" in r.sar.assessment


def test_G_shap_values_add_up(engine, request_ok):
    r = ev(engine, request_ok)
    assert r.explanation.status == ExplanationStatus.AVAILABLE and r.explanation.additivity_error < 1e-9
    assert len(r.explanation.top_contributors) == 3 and len(r.explanation.top_features) == 3
    tops = r.explanation.top_features
    assert abs(tops[0]["shap_log_odds"]) >= abs(tops[1]["shap_log_odds"]) >= abs(tops[2]["shap_log_odds"])


# ---------------------------------------------------------------- H. model failure
def test_H_model_failure_gives_model_error(cfg, model, request_ok):
    class Broken(FixedModel):
        def raw(self, X):
            raise ModelUnavailable("MODEL_OUTPUT_INVALID", "boom")
    e = RiskEngine(ROOT, cfg, model_service=Broken(model, 0.5), alert_service=AlertService(cfg), audit_service=AuditService(cfg))
    r = ev(e, request_ok)
    assert r.status == Status.MODEL_ERROR and r.risk.raw_output is None and r.risk.tier is None
    assert r.explanation.status != ExplanationStatus.AVAILABLE


def test_H_nan_output_rejected(cfg, model, request_ok):
    class NanEstimator:
        def predict_proba(self, X):
            return np.column_stack([np.full(len(X), np.nan), np.full(len(X), np.nan)])
    ms = ModelService(ROOT, cfg, bundle=dict(model.bundle, estimator=NanEstimator()))
    e = RiskEngine(ROOT, cfg, model_service=ms, alert_service=AlertService(cfg), audit_service=AuditService(cfg))
    r = ev(e, request_ok)
    assert r.status == Status.MODEL_ERROR and r.status_reason_code == "MODEL_OUTPUT_INVALID" and r.risk.raw_output is None


def test_H_integrity_failure(tmp_path, cfg, request_ok):
    (tmp_path / "manifest.json").write_text(json.dumps({"model_sha256": "0" * 64}))
    c = load_config(overrides={"model.integrity_manifest": str(tmp_path / "manifest.json")})
    e = RiskEngine(ROOT, c, alert_service=AlertService(c), audit_service=AuditService(c))
    r = ev(e, request_ok)
    assert r.status == Status.MODEL_ERROR and r.status_reason_code == "INTEGRITY_FAILED" and r.risk.raw_output is None


def test_H_schema_mismatch(cfg, model):
    b = dict(model.bundle, features=list(reversed(model.features)))
    with pytest.raises(ModelUnavailable) as e:
        ModelService(ROOT, cfg, bundle=b)
    assert e.value.code == "FEATURE_SCHEMA_MISMATCH"


# ---------------------------------------------------------------- L. missing wearable sync
def test_L_late_sync_warns(engine, request_ok):
    r = ev(engine, request_ok, wearable_readings=make_readings(end=NOW - pd.Timedelta(minutes=90)))
    assert r.status == Status.OK
    assert any("last sent data" in w for w in r.data_quality.warnings) and r.data_quality.minutes_since_last_sync == 90


# ---------------------------------------------------------------- M. insufficient personal history
def test_M_short_history_has_no_reliable_baseline(engine, request_ok):
    r = ev(engine, request_ok, wearable_readings=make_readings(hours=8))
    assert r.baseline.status.value in ("UNAVAILABLE", "UNRELIABLE")
    assert all(d["baseline_status"] != "AVAILABLE" for d in r.baseline.deviations)
    r2 = ev(engine, request_ok, wearable_readings=make_readings(hours=5))
    assert r2.baseline.status.value == "UNAVAILABLE" and r2.baseline.deviations == []


def test_M_external_baseline_is_labelled(engine, request_ok):
    base = {"hr": {"median": 70, "mad": 3, "n_hours": 100, "from": "2026-10-01", "to": "2026-10-15"}}
    r = engine.evaluate(request_ok["patient_profile"], request_ok["clinical_readings"], make_readings(hours=5),
                        baseline=base, now=NOW)
    assert r.baseline.by_signal["hr"]["source"] == "provided by caller"


# ---------------------------------------------------------------- Q. deterministic output
def test_Q_deterministic(cfg, model, request_ok):
    mk = lambda: RiskEngine(ROOT, cfg, model_service=model, alert_service=AlertService(cfg), audit_service=AuditService(cfg))
    a, b = ev(mk(), request_ok), ev(mk(), copy.deepcopy(request_ok))
    assert a.audit.result_sha256 == b.audit.result_sha256 and a.audit.audit_id == b.audit.audit_id
    assert a.model_dump(mode="json", exclude={"alert"}) == b.model_dump(mode="json", exclude={"alert"})


# ---------------------------------------------------------------- R. out-of-distribution input
def test_R_out_of_distribution_warns_without_claiming_confidence(engine, request_ok):
    r = ev(engine, request_ok, patient_profile=dict(PROFILE, age_at_admit=110))
    assert r.status == Status.OK
    assert any(o["feature"] == "age_at_admit" for o in r.data_quality.out_of_distribution)
    assert r.risk.uncertainty["validated"] is False and "not a validated" in r.risk.uncertainty["note"].lower()
    assert "outside the range seen" in r.sar.assessment


# ---------------------------------------------------------------- S. boundaries and concurrency
def test_S_threshold_boundaries(cfg):
    pol = RiskPolicy(ROOT, cfg)
    m, h = pol.thresholds["moderate"], pol.thresholds["high"]
    assert pol.tier(m) == Tier.MODERATE and pol.tier(m - 1e-9) == Tier.LOW
    assert pol.tier(h) == Tier.HIGH and pol.tier(h - 1e-9) == Tier.MODERATE
    assert pol.with_hysteresis(h * 0.95, Tier.HIGH) == Tier.HIGH            # inside the margin: stays HIGH
    assert pol.with_hysteresis(h * 0.80, Tier.HIGH) == Tier.MODERATE
    assert pol.with_hysteresis(h, Tier.LOW) == Tier.HIGH                   # step-ups are immediate


def test_S_tier_from_engine_at_boundary(fixed_engine, request_ok, cfg):
    pol = RiskPolicy(ROOT, cfg)
    assert ev(fixed_engine(pol.thresholds["high"]), request_ok).risk.tier == Tier.HIGH
    assert ev(fixed_engine(pol.thresholds["moderate"] - 1e-6), request_ok).risk.tier == Tier.LOW


def test_S_repeated_and_concurrent_requests(fixed_engine, request_ok, cfg):
    alerts = AlertService(cfg)
    e = fixed_engine(0.5, alerts)
    results = []
    th = [threading.Thread(target=lambda: results.append(ev(e, request_ok))) for _ in range(12)]
    [t.start() for t in th]
    [t.join() for t in th]
    assert len(results) == 12 and len({r.audit.result_sha256 for r in results}) == 1
    assert len(alerts.alerts) == 1                           # one alert, however many identical requests


def test_insufficient_never_low_across_failures(engine, request_ok):
    for rd in ([], make_readings(end=NOW - pd.Timedelta(hours=6)), make_readings(signals=("hr",))):
        r = ev(engine, request_ok, wearable_readings=rd)
        assert r.status != Status.OK and r.risk.tier is None
