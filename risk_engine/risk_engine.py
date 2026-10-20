# CardioTwin risk engine: the single inference entry point for the phone app (patient and doctor mode) and the Wear OS
# watch. Order of work: validate -> emergency rules (independent of the model) -> freshness gate -> features -> model
# -> policy tier -> uncertainty and trajectory -> SHAP -> SAR -> audit -> alert decision. Every failure is reported as
# INSUFFICIENT_DATA or MODEL_ERROR; nothing is ever turned into a made-up score or a LOW result.
from __future__ import annotations

import copy
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
for _p in (ROOT, ROOT / "src"):          # src/ holds twin_preprocess, twin_rules, cardiotwin_ml
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from . import baseline_engine, data_freshness, emergency_rules, explain_text, feature_builder, input_validator  # noqa: E402
from . import sar_generator, shap_service  # noqa: E402
from .alert_service import AlertService  # noqa: E402
from .audit_service import AuditService, Pseudonymizer, canonical, sha256_text  # noqa: E402
from .model_service import ModelService, ModelUnavailable  # noqa: E402
from .risk_policy import RiskPolicy  # noqa: E402
from .schemas import (AuditBlock, BaselineBlock, DataQuality, EmergencyBlock, EmergencyState, ExplanationBlock,  # noqa: E402
                      ExplanationStatus, ModelInfo, PatientProfile, RiskBlock, RiskResult, SarBlock, Status)

ENGINE_VERSION = "risk-engine-1.0.0"
CONFIG_PATH = Path(__file__).resolve().parent / "config" / "risk_policy.yaml"


def load_config(path: str | Path | None = None, overrides: dict | None = None) -> dict:
    cfg = yaml.safe_load(Path(path or CONFIG_PATH).read_text())
    for dotted, value in (overrides or {}).items():       # e.g. {"alerts.cooldown_minutes": 30}
        d = cfg
        *parts, last = dotted.split(".")
        for p in parts:
            d = d[p]
        d[last] = value
    return cfg


def _to_naive_utc(t) -> pd.Timestamp:
    t = pd.Timestamp(t)
    return t.tz_convert("UTC").tz_localize(None) if t.tzinfo is not None else t


def _jsonable(x):
    return json.loads(json.dumps(x, default=str))


class RiskEngine:
    """Load once at app start-up; call evaluate() per request. Dependencies can be injected for tests."""

    def __init__(self, root: str | Path = ROOT, config: dict | None = None, model_service: ModelService | None = None,
                 alert_service: AlertService | None = None, audit_service: AuditService | None = None,
                 explainer: Callable | None = None, notifier: Callable | None = None, check_integrity: bool = True):
        self.root = Path(root)
        self.cfg = config or load_config()
        self.policy = RiskPolicy(self.root, self.cfg)
        self.pseudonym = Pseudonymizer(self.cfg)
        self.model_error: tuple[str, str] | None = None
        try:
            self.model = model_service or ModelService(self.root, self.cfg, check_integrity=check_integrity)
        except ModelUnavailable as e:
            self.model, self.model_error = None, (e.code, str(e))
        except Exception as e:  # unreadable or missing file
            self.model, self.model_error = None, ("MODEL_LOAD_FAILED", f"Model could not be loaded ({type(e).__name__}).")
        if self.model is not None and self.model.reference:
            explain_text.MED.update(self.model.reference["medians"])
        self.alerts = alert_service if alert_service is not None else AlertService(self.cfg, notifier=notifier)
        self.audit = audit_service if audit_service is not None else AuditService(self.cfg)
        self.explainer = explainer or shap_service.explain

    # ---------------------------------------------------------------- public entry point
    def evaluate(self, patient_profile, clinical_readings=None, wearable_readings=None, baseline: dict | None = None,
                 now=None, simulation: bool = False, patient_reference: str | None = None,
                 record: bool = True) -> RiskResult:
        now = _to_naive_utc(now) if now is not None else pd.Timestamp.now(tz="UTC").tz_localize(None)
        degraded: list[str] = []
        raw_inputs = {"profile": _jsonable(patient_profile if isinstance(patient_profile, dict) else patient_profile.model_dump()),
                      "clinical": _jsonable(clinical_readings or []), "wearable": _jsonable(wearable_readings or []),
                      "baseline": _jsonable(baseline), "now": str(now)}
        # ---- 1. validation
        try:
            v = input_validator.validate(patient_profile, clinical_readings, wearable_readings, now, self.cfg)
            ref = patient_reference or self.pseudonym(v.profile["patient_id"])
        except Exception as e:
            ref = patient_reference or "unknown"
            return self._input_error(ref, now, f"The request could not be read ({type(e).__name__}).", simulation)
        snap = copy.deepcopy(raw_inputs)
        snap["profile"].pop("patient_id", None)
        snap["patient_reference"] = ref
        input_ref = self.audit.input_reference(snap)
        audit_id = self.audit.audit_id(ref, input_ref, now, simulation)

        # ---- 2. emergency rules first, independent of the model
        try:
            em = emergency_rules.evaluate(v.readings, v.profile.get("reported_symptoms"), now, self.cfg)
        except Exception as e:
            em = EmergencyBlock(state=EmergencyState.NOT_ASSESSABLE, reason=f"Emergency rules could not run ({type(e).__name__}).")
            degraded.append("emergency_rules")

        # ---- 3. freshness / sufficiency gate and data quality
        gate = data_freshness.check(v, now, self.cfg)
        dq = DataQuality(status="SUFFICIENT" if gate.allowed else "INSUFFICIENT", reason_code=gate.reason_code, reason=gate.reason,
                         coverage_pct=gate.coverage_pct, quality_weighted_coverage_pct=gate.quality_weighted_coverage_pct,
                         freshness_by_signal=gate.freshness, last_wearable_sync=None if gate.last_sync is None else str(gate.last_sync),
                         minutes_since_last_sync=gate.minutes_since_sync, history_hours=gate.history_hours,
                         missing_fields=gate.missing, stale_fields=gate.stale, invalid_fields=sorted({i["field"] for i in v.invalid}),
                         flags_for_remeasurement=v.remeasure, validation_counts=v.counts,
                         warnings=v.warnings + gate.warnings + [f"Conflicting readings: {c['signal']} at {c['timestamp']} {c['values']}" for c in v.conflicts])
        try:
            bl = baseline_engine.evaluate(v.readings, now, self.cfg, external=baseline)
        except Exception as e:
            bl = BaselineBlock()
            degraded.append("baseline")

        info = ModelInfo(**self.model.info()) if self.model else ModelInfo(target=self.cfg["model"]["target"],
                                                                           horizon=f"{self.cfg['model']['horizon_hours']} hours")
        risk = RiskBlock(policy_version=self.policy.version, provisional_policy=self.policy.provisional,
                         thresholds=self.policy.thresholds)
        ex = ExplanationBlock()
        status, code, reason = Status.OK, None, None
        if not gate.allowed:
            status, code, reason = Status.INSUFFICIENT_DATA, gate.reason_code, gate.reason
            ex.reason = "Not run: no valid score."
        elif self.model is None:
            status, (code, reason) = Status.MODEL_ERROR, self.model_error
            ex.reason = "Not run: model unavailable."
        else:
            # ---- 4. features and model
            try:
                table = feature_builder.hourly_table(v.readings, now, v.profile.get("hist_af"))
                statics = feature_builder.static_values(v)
                X = feature_builder.feature_row(table, statics, self.model.features)
                out = self.model.predict(X)
            except ModelUnavailable as e:
                status, code, reason = Status.MODEL_ERROR, e.code, str(e)
            except Exception as e:
                status, code, reason = Status.MODEL_ERROR, "MODEL_RUN_FAILED", f"The model could not run ({type(e).__name__})."
            if status == Status.OK:
                p = out["calibrated"] if out["calibrated"] is not None else out["raw"]
                scale = "calibrated probability (0-1)" if out["calibrated"] is not None else "model probability (0-1), uncalibrated"
                risk.raw_output = round(out["raw"], 6)
                risk.raw_output_meaning = "predict_proba of the saved watch model for the positive class (event within the horizon)"
                risk.calibrated_probability = None if out["calibrated"] is None else round(out["calibrated"], 6)
                risk.score_scale = scale
                if self.policy.tier_allowed(info.calibration_status):
                    risk.tier = self.policy.tier(p)
                    risk.tier_reason = self.policy.reason(p, risk.tier, "Score")
                else:
                    risk.tier_reason = "Tier withheld: the calibrator is missing and the policy does not allow a tier on this output."
                base_rate = self.model.bundle.get("base_rate")
                risk.times_average_risk = round(p / base_rate, 2) if base_rate else None
                dq.imputed_fields = self.model.imputed(X)
                dq.out_of_distribution = self.model.out_of_distribution(X)
                try:
                    u = self.model.uncertainty(X)
                    if u is not None:
                        u["tier_could_change"] = self.policy.tier(u["p05"]) != self.policy.tier(u["p95"])
                    risk.uncertainty = u
                except Exception:
                    degraded.append("uncertainty")
                try:
                    risk.trajectory, risk.trajectory_summary = self._trajectory(table, statics)
                except Exception:
                    degraded.append("trajectory")
                # ---- 5. explanation (failure keeps the valid score)
                try:
                    e = self.explainer(self.model, X)
                    ex = ExplanationBlock(status=ExplanationStatus.AVAILABLE, **{k: e[k] for k in (
                        "method", "output_space", "baseline_output", "explained_output", "additivity_error", "top_contributors",
                        "top_features", "waterfall", "shap_values", "version")})
                except Exception as err:
                    ex = ExplanationBlock(status=ExplanationStatus.UNAVAILABLE,
                                          reason=f"Explanation service failed ({type(err).__name__}); no reasons are shown rather than guessed.")
                    degraded.append("explanation")

        # ---- 6. narrative, actions, breakdown
        try:
            sar = sar_generator.generate({"status": status.value, "reason": reason, "reason_code": code, "now": now, "model": info,
                                          "risk": risk, "dq": dq, "baseline": bl, "explanation": ex, "emergency": em,
                                          "policy": self.policy.describe()})
        except Exception as err:
            sar = SarBlock(one_line=f"{status.value}: narrative unavailable ({type(err).__name__}).", guard_passed=None)
            degraded.append("sar")
        result = RiskResult(status=status, status_reason_code=code, status_reason=reason, patient_reference=ref,
                            evaluated_at=now.to_pydatetime(), is_demo_data=bool(v.profile.get("is_demo")), simulation=simulation,
                            model=info, risk=risk, data_quality=dq, baseline=bl, explanation=ex, emergency=em, sar=sar,
                            degraded_components=degraded)
        result.next_actions = self._next_actions(result)
        result.policy_breakdown = self._breakdown(result)
        result.audit = AuditBlock(audit_id=audit_id, policy_version=self.policy.version, input_reference=input_ref)
        result.audit.result_sha256 = self.result_hash(result)

        # ---- 7. alerts and audit (never for what-if simulations)
        if not simulation:
            try:
                result.alert = self.alerts.process(result, now, event_id=audit_id, policy=self.policy)
                result.audit.alert_reference = result.alert.get("alert_id")
            except Exception as err:
                result.alert = {"decision": "ALERT_SERVICE_UNAVAILABLE", "reason": type(err).__name__,
                                "note": "The emergency and risk information above is still valid; show it directly."}
                result.degraded_components.append("alerts")
            result.policy_breakdown["alert_decision"] = {k: result.alert.get(k) for k in ("decision", "reason", "popup", "alert_tier", "hysteresis_applied")}
        else:
            result.alert = {"decision": "SIMULATION_NO_ALERT"}
        if record:
            try:
                rec = self.audit.record(result, snap, now)
                result.audit.snapshot_stored = rec["snapshot_stored"]
            except Exception:
                result.degraded_components.append("audit")
        return result

    # ---------------------------------------------------------------- helpers
    @staticmethod
    def result_hash(result: RiskResult) -> str:
        d = result.model_dump(mode="json", exclude={"alert", "audit"})
        d["policy_breakdown"].pop("alert_decision", None)
        return sha256_text(canonical(d))

    def _trajectory(self, table: pd.DataFrame, statics: dict, hours: int = 24):
        """Model output for each of the last 24 hours, using only data up to that hour (features look backward only).
        Hours where a mandatory signal was older than its freshness limit are skipped, not scored."""
        t = table.tail(hours).copy()
        X = feature_builder.feature_matrix(t, statics, self.model.features)
        ok = np.ones(len(t), bool)
        for sig in ("hr", "spo2", "rr"):
            lim = int(self.cfg["freshness"][sig]["max_age_minutes"] // 60)
            seen = table[f"{sig}_measured"].rolling(lim, min_periods=1).max().tail(hours).values
            ok &= seen > 0
        rows = []
        p_raw = self.model.raw(X)
        cal = self.model.calibrate(p_raw)
        p = cal if cal is not None else p_raw
        for i, (h, okk) in enumerate(zip(t.hour, ok)):
            rows.append({"hour": str(h), "score": round(float(p[i]), 4) if okk else None,
                         "tier": self.policy.tier(float(p[i])).value if okk else None,
                         "scored": bool(okk), "why_not": None if okk else "a mandatory signal was stale at this hour"})
        valid = [r for r in rows if r["scored"]]
        summ = None
        if valid:
            now_p = valid[-1]["score"]
            six = [r for r in valid if pd.Timestamp(r["hour"]) <= pd.Timestamp(valid[-1]["hour"]) - pd.Timedelta(hours=6)]
            ys = np.array([r["score"] for r in valid])
            summ = {"hours_scored": len(valid), "peak_24h": float(ys.max()), "min_24h": float(ys.min()),
                    "change_6h": None if not six else round(now_p - six[-1]["score"], 4),
                    "hours_at_high_24h": int(sum(r["tier"] == "HIGH" for r in valid)),
                    "note": "Retrospective scores from the same model on the readings received; not stored past predictions."}
        return rows, summ

    def _next_actions(self, r: RiskResult) -> list[str]:
        acts = []
        if r.emergency.state == EmergencyState.TRIGGERED:
            acts.append(f"Show the emergency alert now (rule {r.emergency.rule_id}) with the call option; do not wait for any other step.")
        elif r.emergency.state == EmergencyState.REVIEW_REQUIRED:
            acts.append(f"Ask the patient to re-measure now and notify the care team (rule {r.emergency.rule_id}).")
        if r.status == Status.INSUFFICIENT_DATA:
            acts.append("Show the 'cannot update' screen: ask the patient to check the watch fit and sync, then re-measure. Do not show LOW.")
        elif r.status == Status.MODEL_ERROR:
            acts.append("Show 'risk score unavailable' (technical problem). Do not show a score or LOW. Log for the engineering team.")
        elif r.risk.tier:
            acts += [s.text for s in r.sar.sentences.get("recommendation", [])
                     if s.evidence and s.evidence[0].get("type") == "policy" and "supports, and never replaces" not in s.text]
        if r.data_quality.flags_for_remeasurement:
            acts.append("Some readings are flagged as unusual: re-measure before acting on them.")
        return acts

    def _breakdown(self, r: RiskResult) -> dict[str, Any]:
        p = r.risk.calibrated_probability if r.risk.calibrated_probability is not None else r.risk.raw_output
        final = ("EMERGENCY_ALERT" if r.emergency.state == EmergencyState.TRIGGERED else
                 "NO_SCORE_" + r.status.value if r.status != Status.OK else
                 f"{r.risk.tier.value}_RISK" if r.risk.tier else "SCORE_WITHOUT_TIER")
        return {"model_output": p, "score_scale": r.risk.score_scale, "thresholds": r.risk.thresholds,
                "model_tier": r.risk.tier.value if r.risk.tier else None, "tier_reason": r.risk.tier_reason,
                "emergency_state": r.emergency.state.value, "emergency_rule": r.emergency.rule_id,
                "emergency_is_independent_of_model": True,
                "final_display_state": final,
                "how_final_is_chosen": "An emergency rule overrides everything; otherwise no score is shown when data is insufficient "
                                       "or the model failed; otherwise the model tier under the provisional policy is shown."}

    def _input_error(self, ref, now, reason, simulation) -> RiskResult:
        r = RiskResult(status=Status.INSUFFICIENT_DATA, status_reason_code="INVALID_INPUT", status_reason=reason,
                       patient_reference=ref, evaluated_at=now.to_pydatetime(), simulation=simulation,
                       model=ModelInfo(**self.model.info()) if self.model else ModelInfo(),
                       risk=RiskBlock(policy_version=self.policy.version, thresholds=self.policy.thresholds),
                       data_quality=DataQuality(status="INSUFFICIENT", reason_code="INVALID_INPUT", reason=reason),
                       baseline=BaselineBlock(), explanation=ExplanationBlock(reason="Not run."), emergency=EmergencyBlock(
                           state=EmergencyState.NOT_ASSESSABLE, reason="The request could not be read."),
                       sar=SarBlock(one_line=f"INSUFFICIENT_DATA: {reason}"))
        r.next_actions = ["Fix the request format; no score and no emergency check could be made from it."]
        return r


_DEFAULT: RiskEngine | None = None


def evaluate_patient_risk(patient_profile, clinical_readings=None, wearable_readings=None, baseline=None, now=None,
                          engine: RiskEngine | None = None) -> RiskResult:
    """The public interface: evaluate_patient_risk(profile, clinical_readings, wearable_readings, baseline=None, now=None)."""
    global _DEFAULT
    if engine is None:
        if _DEFAULT is None:
            _DEFAULT = RiskEngine()
        engine = _DEFAULT
    return engine.evaluate(patient_profile, clinical_readings, wearable_readings, baseline=baseline, now=now)
