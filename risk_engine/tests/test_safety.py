# Scenarios I-K, N-P plus role checks, graceful fallback and the phone / watch payloads.
import copy
import json

import pandas as pd
import pytest

from conftest import NOW, PROFILE, ROOT, SYNTHETIC, make_readings
from risk_engine import RiskEngine, views, whatif
from risk_engine.alert_service import AlertService, PermissionDenied
from risk_engine.audit_service import AuditService
from risk_engine.sar_generator import guard
from risk_engine.schemas import EmergencyState, SarSentence, Status


def ev(engine, req, now=NOW, **kw):
    r = dict(req)
    r.update(kw)
    return engine.evaluate(r["patient_profile"], r["clinical_readings"], r["wearable_readings"], now=now)


def vitals(sig_vals: dict, minutes=(12, 2), now=NOW):
    """Readings in a NEWS2 red band, newer than the routine synthetic series (which ends 5 min before NOW)."""
    out = []
    for m in minutes:
        t = (now - pd.Timedelta(minutes=m)).isoformat() + "Z"
        for s, v in sig_vals.items():
            out.append({"timestamp": t, "signal": s, "value": v, "unit": {"hr": "bpm", "spo2": "%", "rr": "breaths/min"}[s],
                        "source": SYNTHETIC})
    return out


# ---------------------------------------------------------------- I. emergency rules
def test_I_confirmed_vitals_trigger_emergency_with_evidence(engine, request_ok):
    rd = make_readings() + vitals({"hr": 142, "rr": 29})
    r = ev(engine, request_ok, wearable_readings=rd)
    em = r.emergency
    assert em.state == EmergencyState.TRIGGERED and em.rule_id == "EMG-VIT-02"
    assert {e["signal"] for e in em.evidence} >= {"hr", "rr"} and all(e["timestamp"] for e in em.evidence)
    m = em.modal
    assert m["type"] == "EMERGENCY_MODAL" and m["accessibility"]["role"] == "alertdialog" and m["icon"] and m["title"]
    assert m["dismissible"] is False and m["dispatch_status"] == "NOT_DISPATCHED"
    call = em.call_action
    assert call["uri"] == "tel:112" and call["auto_dial"] is False and call["requires_user_confirmation"] is True
    assert "does not contact emergency services" in call["confirm_text"]
    assert views.phone_patient(r)["screen"] == "EMERGENCY" and views.watch(r)["type"] == "EMERGENCY_WATCH"


def test_I_single_reading_is_review_not_emergency(engine, request_ok):
    rd = make_readings() + vitals({"hr": 142}, minutes=(2,))
    r = ev(engine, request_ok, wearable_readings=rd)
    assert r.emergency.state == EmergencyState.REVIEW_REQUIRED and r.emergency.rule_id == "EMG-VIT-00"
    assert r.emergency.modal is None


def test_I_symptoms_trigger_even_without_valid_score(engine, request_ok):
    prof = dict(PROFILE, reported_symptoms={"chest_pain": True, "cold_sweat": True,
                                            "timestamp": (NOW - pd.Timedelta(minutes=3)).isoformat() + "Z"})
    r = ev(engine, request_ok, patient_profile=prof, wearable_readings=make_readings(end=NOW - pd.Timedelta(hours=6)))
    assert r.status == Status.INSUFFICIENT_DATA and r.risk.tier is None
    assert r.emergency.state == EmergencyState.TRIGGERED and r.emergency.rule_id == "EMG-SYM-01"
    assert r.alert["decision"] == "NEW_EMERGENCY" and r.alert["popup"] is True


def test_I_emergency_does_not_need_model_or_shap(cfg, request_ok):
    def broken(*a, **k):
        raise RuntimeError("down")
    c = dict(cfg)
    e = RiskEngine(ROOT, c, explainer=broken, alert_service=AlertService(cfg), audit_service=AuditService(cfg))
    e.model, e.model_error = None, ("MODEL_LOAD_FAILED", "Model could not be loaded.")
    r = ev(e, request_ok, wearable_readings=make_readings() + vitals({"spo2": 86, "hr": 135}))
    assert r.status == Status.MODEL_ERROR and r.emergency.state == EmergencyState.TRIGGERED


def test_I_no_recent_data_is_not_assessable(engine, request_ok):
    r = ev(engine, request_ok, wearable_readings=make_readings(end=NOW - pd.Timedelta(hours=5)))
    assert r.emergency.state == EmergencyState.NOT_ASSESSABLE


def test_I_unverified_jurisdiction_hides_number(cfg, model, request_ok):
    from risk_engine import load_config
    c = load_config(overrides={"emergency.call_action.jurisdiction_verified": False})
    e = RiskEngine(ROOT, c, model_service=model, alert_service=AlertService(c), audit_service=AuditService(c))
    r = ev(e, request_ok, wearable_readings=make_readings() + vitals({"hr": 142, "rr": 29}))
    assert r.emergency.call_action_available is False and r.emergency.call_action["uri"] is None


# ---------------------------------------------------------------- J. repeated alerts, cooldown, lifecycle
def test_J_dedup_and_cooldown(fixed_engine, request_ok, cfg):
    alerts = AlertService(cfg)
    e = fixed_engine(0.05, alerts)                                  # MODERATE
    r1 = ev(e, request_ok)
    assert r1.alert["decision"] == "NEW_RISK_ALERT" and r1.alert["popup"]
    r2 = ev(e, request_ok, now=NOW + pd.Timedelta(minutes=10),
            wearable_readings=make_readings(end=NOW + pd.Timedelta(minutes=5)))
    assert r2.alert["decision"] == "DEDUPLICATED" and not r2.alert["popup"]
    aid = r1.alert["alert_id"]
    alerts.acknowledge(aid, NOW, "patient-app", "patient")
    with pytest.raises(PermissionDenied):
        alerts.resolve(aid, NOW, "patient-app", "patient", "ok", r2)
    alerts.resolve(aid, NOW + pd.Timedelta(minutes=12), "dr-a", "clinician", "Reviewed, observations repeated", r2)
    r3 = ev(e, request_ok, now=NOW + pd.Timedelta(minutes=20), wearable_readings=make_readings(end=NOW + pd.Timedelta(minutes=15)))
    assert r3.alert["decision"] == "SUPPRESSED_COOLDOWN" and not r3.alert["popup"]
    r4 = ev(e, request_ok, now=NOW + pd.Timedelta(minutes=75), wearable_readings=make_readings(end=NOW + pd.Timedelta(minutes=70)))
    assert r4.alert["decision"] == "NEW_RISK_ALERT" and r4.alert["popup"]


def test_J_tier_rise_bypasses_cooldown(fixed_engine, request_ok, cfg):
    alerts = AlertService(cfg)
    ev(fixed_engine(0.05, alerts), request_ok)
    r = ev(fixed_engine(0.5, alerts), request_ok, now=NOW + pd.Timedelta(minutes=5),
           wearable_readings=make_readings(end=NOW + pd.Timedelta(minutes=1)))
    assert r.alert["decision"] == "ESCALATED_TIER" and r.alert["popup"]


def test_J_lifecycle_and_resolution_rules(engine, request_ok, cfg):
    rd = make_readings() + vitals({"hr": 142, "rr": 29})
    r = ev(engine, request_ok, wearable_readings=rd)
    aid, al = r.alert["alert_id"], engine.alerts
    assert al.alerts[aid]["state"] == "NEW"
    al.mark_displayed(aid, NOW)
    a = al.acknowledge(aid, NOW, "patient-app", "patient")
    assert a["state"] == "ACKNOWLEDGED" and "says nothing about whether the patient is better" in a["history"][-1]["meaning"]
    al.escalate(aid, NOW, "dr-a", "clinician", to="on-call team")
    stale = ev(engine, request_ok, now=NOW + pd.Timedelta(hours=6))
    with pytest.raises(ValueError):                              # missing data never resolves an alert
        al.resolve(aid, NOW + pd.Timedelta(hours=6), "dr-a", "clinician", "note", stale)
    still = ev(engine, request_ok, wearable_readings=rd)
    with pytest.raises(ValueError):                              # rule still met
        al.resolve(aid, NOW, "dr-a", "clinician", "note", still)
    calm = ev(engine, request_ok, now=NOW + pd.Timedelta(hours=2), wearable_readings=make_readings(end=NOW + pd.Timedelta(hours=2) - pd.Timedelta(minutes=5)))
    a = al.resolve(aid, NOW + pd.Timedelta(hours=2), "dr-a", "clinician", "Seen in clinic; repeat readings normal", calm)
    assert a["state"] == "RESOLVED" and a["resolution"]["by"] == "dr-a"
    with pytest.raises(ValueError):
        al.record_user_action(aid, NOW, "patient-app", "patient", "ambulance_dispatched")
    al.record_user_action(aid, NOW, "patient-app", "patient", "opened_dialer_112")


def test_J_notification_failure_is_recorded(cfg, model, request_ok):
    def notifier(a):
        raise ConnectionError("push service down")
    e = RiskEngine(ROOT, cfg, model_service=model, notifier=notifier, audit_service=AuditService(cfg))
    r = ev(e, request_ok, wearable_readings=make_readings() + vitals({"hr": 142, "rr": 29}))
    n = e.alerts.alerts[r.alert["alert_id"]]["notifications"][-1]
    assert n["success"] is False and n["error"] == "ConnectionError"


# ---------------------------------------------------------------- K. emergency during cooldown
def test_K_emergency_never_suppressed_by_cooldown(fixed_engine, request_ok, cfg):
    alerts = AlertService(cfg)
    e = fixed_engine(0.05, alerts)
    ev(e, request_ok)
    rd = make_readings(end=NOW + pd.Timedelta(minutes=5)) + vitals({"hr": 142, "rr": 29}, now=NOW + pd.Timedelta(minutes=10))
    r = ev(e, request_ok, now=NOW + pd.Timedelta(minutes=10), wearable_readings=rd)
    assert r.alert["decision"] == "NEW_EMERGENCY" and r.alert["popup"] is True
    prof = dict(PROFILE, reported_symptoms={"chest_pain": True, "breathless": True,
                                            "timestamp": (NOW + pd.Timedelta(minutes=14)).isoformat() + "Z"})
    r2 = ev(e, request_ok, now=NOW + pd.Timedelta(minutes=15), wearable_readings=rd, patient_profile=prof)
    assert r2.alert["decision"] == "NEW_EMERGENCY"            # a different emergency rule is new, never held back


# ---------------------------------------------------------------- N. SAR grounded and safe
def test_N_every_sentence_has_evidence_and_guard_passes(engine, request_ok):
    r = ev(engine, request_ok, wearable_readings=make_readings() + vitals({"hr": 142, "rr": 29}))
    assert r.sar.guard_passed is True
    for part, ss in r.sar.sentences.items():
        for s in ss:
            assert s.evidence, (part, s.text)
    text = " ".join([r.sar.situation, r.sar.assessment, r.sar.recommendation]).lower()
    for bad in ("diagnos", "heart attack", "increase the dose", "stop taking", "aspirin"):
        assert bad not in text


def test_N_guard_rejects_unsafe_wording():
    bad = {"assessment": [SarSentence(text="The patient has a diagnosis of sepsis."),
                          SarSentence(text="Increase the beta blocker dose tonight.")],
           "recommendation": [SarSentence(text="High heart rate is caused by the infection.")]}
    probs = {b["problem"] for b in guard(bad)}
    assert {"diagnosis wording", "medication advice", "causal claim"} <= probs


# ---------------------------------------------------------------- O. what-if
def test_O_whatif_never_changes_the_record(engine, request_ok):
    before = copy.deepcopy(request_ok)
    n_alerts, n_audit = len(engine.alerts.alerts), len(engine.audit.records)
    out = whatif.simulate(engine, request_ok, {"hr": 70, "smoker": 0}, role="clinician")
    assert request_ok == before
    assert len(engine.alerts.alerts) == n_alerts and len(engine.audit.records) == n_audit
    assert "HYPOTHETICAL" in out["label"] and out["status"] == "OK" and "difference" in out
    assert out["changed_model_inputs"]
    with pytest.raises(PermissionDenied):
        whatif.simulate(engine, request_ok, {"hr": 70}, role="patient")
    assert whatif.simulate(engine, request_ok, {"hr": 400}, role="clinician")["status"] == "INVALID_CHANGE"


# ---------------------------------------------------------------- P. audit record
def test_P_audit_has_versions_and_no_identifiers(engine, request_ok):
    r = ev(engine, request_ok)
    rec = engine.audit.records[r.audit.audit_id]
    assert rec["model_version"] and rec["policy_version"] and rec["evaluated_at"] and rec["result_sha256"]
    blob = json.dumps(rec)
    assert PROFILE["patient_id"] not in blob and "readings" not in blob
    assert rec["patient_reference"].startswith("pt_") and rec["input_reference"].startswith("in_")
    snap = engine.audit.snapshots[r.audit.audit_id]["snapshot"]
    assert "patient_id" not in snap["profile"] and snap["patient_reference"] == r.patient_reference


def test_P_audit_replay_reproduces_result(engine, request_ok):
    r = ev(engine, request_ok)
    snap = engine.audit.snapshots[r.audit.audit_id]["snapshot"]
    prof = dict(snap["profile"], patient_id="replay")
    again = engine.evaluate(prof, snap["clinical"], snap["wearable"], baseline=snap["baseline"], now=snap["now"],
                            patient_reference=snap["patient_reference"], record=False)
    assert again.audit.result_sha256 == r.audit.result_sha256


def test_P_retention_purge(engine, request_ok):
    r = ev(engine, request_ok)
    out = engine.audit.purge(NOW + pd.Timedelta(days=40))
    assert out["snapshots_purged"] >= 1 and r.audit.audit_id in engine.audit.records


# ---------------------------------------------------------------- graceful fallback and payloads
def test_alert_store_failure_keeps_result(cfg, model, request_ok):
    class BrokenAlerts(AlertService):
        def process(self, *a, **k):
            raise OSError("database unavailable")
    e = RiskEngine(ROOT, cfg, model_service=model, alert_service=BrokenAlerts(cfg), audit_service=AuditService(cfg))
    r = ev(e, request_ok, wearable_readings=make_readings() + vitals({"hr": 142, "rr": 29}))
    assert r.alert["decision"] == "ALERT_SERVICE_UNAVAILABLE" and "alerts" in r.degraded_components
    assert r.emergency.state == EmergencyState.TRIGGERED and r.emergency.modal is not None


def test_insufficient_data_screens(engine, request_ok):
    r = ev(engine, request_ok, wearable_readings=make_readings(end=NOW - pd.Timedelta(hours=5)))
    p, w = views.phone_patient(r), views.watch(r)
    assert p["screen"] == "CANNOT_UPDATE" and p["tier"] is None and p["shows_score_number"] is False
    assert w["title"] == "No update" and w["stale"] is True
    d = views.phone_doctor(r)
    assert d["header"]["score"] is None and d["header"]["tier"] is None
