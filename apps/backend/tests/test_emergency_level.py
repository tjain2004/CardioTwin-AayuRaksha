# The emergency-number (112) call prompt is only for patients classified as critical: an engine emergency rule in the
# TRIGGERED state, or a confirmed safety emergency (an unanswered fall or "I need help"). High-risk, moderate and
# urgent-review patients get the softer "Contact your doctor" action and no 112 prompt. Readings are SIMULATED.
import json
import time

from conftest import login, pair_device

from simulator.watch_sim import WatchSimulator, load_replay


def new_patient(client, doc, pid, stay_id):
    """A fresh patient with a test stay's EHR, so these tests never share state with the other tests."""
    ehr = client.get(f"/api/patients/stay-{stay_id}/ehr", headers=doc).json()
    r = client.post("/api/patients", headers=doc, json={"patient_id": pid, "display_name": f"Test {pid}",
                                                        "profile": ehr["profile"], "clinical": ehr.get("clinical") or []})
    assert r.status_code == 201, r.text
    return pair_device(client, doc, pid)


def send(client, dev, readings):
    r = client.post("/api/readings", headers={"Authorization": f"Bearer {dev['device_token']}"}, json={"readings": readings})
    assert r.status_code == 200, r.text
    return r.json()


def backfill(client, dev, pid, stay_id, hours=10):
    sim = WatchSimulator(load_replay(stay_id), pid, backfill_hours=hours, seed=0)
    rd = sim.backfill()
    for x in rd:                                         # keep the history inside the normal range
        if x["type"] == "hr":
            x["value"] = min(x["value"], 110)
        elif x["type"] == "resp":
            x["value"] = min(x["value"], 22)
        elif x["type"] == "spo2":
            x["value"] = max(x["value"], 94)
    send(client, dev, rd)
    return sim


def vitals(sim, pid, values, minutes_ago=(0,)):
    from datetime import timedelta
    out = []
    for m in minutes_ago:
        t = sim.sim_now - timedelta(minutes=m)
        ts = t.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        out += [{"patient_id": pid, "timestamp": ts, "type": k, "value": v, "source": "simulator", "quality": 0.95,
                 "meta": {"simulated": True, "label": "SIMULATED"}} for k, v in values.items()]
    return out


def assert_no_112(pv, watch):
    assert pv["screen"] != "EMERGENCY" and pv["emergency_modal"] is None
    assert pv["emergency_level"] != "critical" and pv["call_action"] is None
    assert "tel:112" not in json.dumps(pv) and watch["type"] != "EMERGENCY_WATCH"


def test_urgent_review_patient_gets_contact_doctor_not_112(client, doc, service):
    pid = "emg-review-1"
    dev = new_patient(client, doc, pid, 30876334)
    sim = backfill(client, dev, pid, 30876334)
    send(client, dev, vitals(sim, pid, {"hr": 142}))      # one reading in a NEWS2 red band: re-measure, not critical
    out = service.evaluate(pid)
    assert out["emergency_state"] == "REVIEW_REQUIRED"
    pv = out["views"]["phone_patient"]
    assert_no_112(pv, out["views"]["watch"])
    assert pv["emergency_level"] == "review"
    assert pv["care_action"]["type"] == "contact_care_team" and pv["care_action"]["label"] == "Contact your doctor"
    assert pv["review_banner"]["care_action"]["type"] == "contact_care_team" and "call_action" not in pv["review_banner"]
    em = out["views"]["phone_doctor"]["emergency"]
    assert em["level"] == "review" and em["call_action"] is None and not em["call_action_available"]
    assert not any("112" in g for g in em["guidance"])
    assert not [a for a in client.get(f"/api/patients/{pid}/alerts?open=true", headers=doc).json() if a["severity"] == "critical"]
    assert client.get(f"/api/patients/{pid}", headers=doc).json()["critical_open"] is False


def test_high_or_moderate_patient_gets_contact_doctor_not_112(client, doc, service):
    pid = "emg-tier-1"
    dev = new_patient(client, doc, pid, 37093652)
    sim = WatchSimulator(load_replay(37093652), pid, backfill_hours=12, seed=0)
    send(client, dev, sim.backfill())
    out = service.evaluate(pid)
    assert out["emergency_state"] != "TRIGGERED" and out["tier"] in ("HIGH", "MODERATE")
    pv = out["views"]["phone_patient"]
    assert_no_112(pv, out["views"]["watch"])
    assert pv["care_action"]["label"] == "Contact your doctor"


def test_critical_patient_gets_user_confirmed_112_prompt(client, doc, service):
    pid = "emg-critical-1"
    dev = new_patient(client, doc, pid, 30876334)
    sim = backfill(client, dev, pid, 30876334)
    send(client, dev, vitals(sim, pid, {"hr": 142, "resp": 29}, minutes_ago=(2, 0)))   # two signals confirmed red
    out = service.evaluate(pid)
    assert out["emergency_state"] == "TRIGGERED"
    pv = out["views"]["phone_patient"]
    assert pv["screen"] == "EMERGENCY" and pv["emergency_level"] == "critical" and pv["care_action"] is None
    call = pv["call_action"]
    assert call["uri"] == "tel:112" and call["auto_dial"] is False and call["requires_user_confirmation"] is True
    assert call["dispatch_status"] == "NOT_DISPATCHED"
    assert any(a["id"] == "call_emergency" for a in pv["emergency_modal"]["actions"])
    assert out["views"]["watch"]["type"] == "EMERGENCY_WATCH"
    crit = [a for a in client.get(f"/api/patients/{pid}/alerts?open=true", headers=doc).json() if a["severity"] == "critical"]
    assert len(crit) == 1 and crit[0]["kind"] == "EMERGENCY"
    send(client, dev, vitals(sim, pid, {"hr": 140, "resp": 28}, minutes_ago=(1,)))
    service.evaluate(pid)                                 # a later evaluation must not drop the stored emergency screen
    a = service.alerts.get(crit[0]["alert_id"])
    assert a["state"] != "RESOLVED" and a["payload"]["emergency"]["actions"][0]["uri"] == "tel:112"
    assert client.get(f"/api/patients/{pid}", headers=doc).json()["critical_open"] is True


def test_unanswered_fall_is_critical_and_gets_112_prompt(client, doc, service):
    pid = "emg-fall-1"
    dev = new_patient(client, doc, pid, 30876334)
    sim = backfill(client, dev, pid, 30876334, hours=4)
    aid = send(client, dev, sim.trigger_fall())["alerts"][0]
    out = service.evaluate(pid)
    assert out["views"]["phone_patient"]["call_action"] is None        # still waiting for the wearer's answer
    time.sleep(1.2)                                                     # the test response window is 1 s
    assert aid in [a["alert_id"] for a in service.safety.overdue()]
    out = service.evaluate(pid)
    pv = out["views"]["phone_patient"]
    assert pv["screen"] == "EMERGENCY" and pv["emergency_level"] == "critical"
    assert pv["call_action"]["uri"] == "tel:112" and pv["call_action"]["auto_dial"] is False
    assert service.alerts.get(aid)["payload"]["dispatch_status"] == "NOT_DISPATCHED"
