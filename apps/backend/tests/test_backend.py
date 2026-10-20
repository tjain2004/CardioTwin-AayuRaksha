# Backend tests on the real saved model, risk engine and what-if simulators. Stay ids are the held-out test stays.
import time

import pytest
from pydantic import ValidationError

from cardiotwin_backend.readings import Reading
from conftest import login, pair_device, stream


# ---------------------------------------------------------------------- reading format
def test_reading_format_accepts_every_type():
    base = {"patient_id": "p1", "timestamp": "2026-10-19T10:00:00", "source": "simulator"}
    for t, v in [("hr", 72), ("hrv_rmssd", 40), ("spo2", 97), ("resp", 16), ("steps", 120), ("sleep_stage", "deep"),
                 ("ecg_qtc", 430), ("fall", 1), ("dose_taken", 50), ("rhythm", "af_flutter")]:
        r = Reading(**base, type=t, value=v)
        assert r.timestamp.tzinfo is not None and r.timestamp.utcoffset().total_seconds() == 0   # naive = UTC
    assert Reading(**base, type="hr", value=72).unit == "bpm"


@pytest.mark.parametrize("bad", [{"type": "hr", "value": 400}, {"type": "temperature", "value": 37},
                                 {"type": "sleep_stage", "value": "nap"}, {"type": "spo2", "value": "high"},
                                 {"type": "hr", "value": 70, "source": "fitbit"}, {"type": "hr", "value": 70, "extra": 1}])
def test_reading_format_rejects_bad_values(bad):
    with pytest.raises(ValidationError):
        Reading(**{"patient_id": "p1", "timestamp": "2026-10-19T10:00:00Z", "source": "watch", **bad})


def test_engine_root_uses_saved_artifacts(service):
    h = service.health()
    assert h["model_loaded"] and h["model_error"] is None
    assert (service.root.engine_root / "models" / "cardiotwin_watch_24h.pkl").exists()
    assert (service.root.engine_root / "risk_engine" / "artifacts" / "manifest.json").exists()


# ---------------------------------------------------------------------- auth and pairing
def test_login_and_patient_scoping(client, doc):
    assert client.post("/api/auth/login", json={"username": "dr.demo", "password": "nope"}).status_code == 401
    assert client.get("/api/patients").status_code == 401
    assert len(client.get("/api/patients", headers=doc).json()) == 19
    pt = login(client, "p30876334")
    mine = client.get("/api/patients", headers=pt).json()
    assert [p["patient_id"] for p in mine] == ["stay-30876334"]
    assert client.get("/api/patients/stay-31248398", headers=pt).status_code == 403
    assert client.get("/api/patients/stay-30876334/ehr", headers=pt).json()["profile"]["age_at_admit"] == 59.0
    assert client.put("/api/patients/stay-30876334/ehr", json={"profile": {"smoker": 1}}, headers=pt).status_code == 403


def test_qr_pairing_is_single_use_and_device_is_bound(client, doc):
    r = client.post("/api/patients/stay-31248398/pairing", headers=doc).json()
    assert r["qr_payload"].startswith("cardiotwin://pair?code=") and r["qr_png_base64"]
    dev = client.post("/api/devices/pair", json={"code": r["pairing_code"], "kind": "watch", "source": "watch"}).json()
    assert client.post("/api/devices/pair", json={"code": r["pairing_code"], "kind": "watch"}).status_code == 401
    h = {"Authorization": f"Bearer {dev['device_token']}"}
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    ok = client.post("/api/readings", headers=h, json={"readings": [
        {"patient_id": "stay-31248398", "timestamp": now, "type": "hr", "value": 80, "source": "watch"},
        {"patient_id": "stay-31248398", "timestamp": now, "type": "spo2", "value": 97, "source": "simulator"}]}).json()
    assert ok["accepted"] == 1 and ok["rejected"][0]["reason"].startswith("this device may only send")
    other = client.post("/api/readings", headers=h, json=[{"patient_id": "stay-30876334", "timestamp": now, "type": "hr",
                                                          "value": 80, "source": "watch"}])
    assert other.status_code == 403
    future = client.post("/api/readings", headers=h, json=[{"patient_id": "stay-31248398", "timestamp": "2030-01-01T00:00:00Z",
                                                           "type": "hr", "value": 80, "source": "watch"}]).json()
    assert future["accepted"] == 0 and future["rejected"][0]["reason"] == "timestamp is in the future"
    assert client.delete(f"/api/devices/{dev['device_id']}", headers=doc).status_code == 200
    assert client.post("/api/readings", headers=h, json=[]).status_code == 401


# ---------------------------------------------------------------------- stream -> features -> risk engine
def test_replay_stream_is_scored_by_the_risk_engine(client, doc, service):
    dev = pair_device(client, doc, "stay-37093652")
    stream(client, dev, 37093652, backfill_hours=12, steps=6)
    out = client.post("/api/patients/stay-37093652/evaluate", headers=doc).json()
    assert out["status"] == "OK" and out["simulated"] and out["label"] == "SIMULATED"
    assert out["tier"] in ("LOW", "MODERATE", "HIGH") and 0 < out["score"] < 1
    assert out["result"]["model"]["version"] and out["result"]["explanation"]["status"] == "AVAILABLE"
    assert set(out["views"]) == {"phone_patient", "phone_doctor", "watch"}
    f = out["features"]["signals"]
    assert f["hr"]["1h"]["n"] >= 6 and f["hr"]["baseline"]["status"] in ("UNRELIABLE", "AVAILABLE")
    assert "robust_z_1h" in f["hr"]["baseline"] and f["steps"]["24h"] >= 0
    # the dashboard history and the stored readings are there
    assert client.get("/api/patients/stay-37093652/history", headers=doc).json()[-1]["status"] == "OK"
    rs = client.get("/api/patients/stay-37093652/readings?types=hr&limit=5", headers=doc).json()
    assert len(rs) == 5 and all(r["source"] == "simulator" and r["meta"]["label"] == "SIMULATED" for r in rs)


def test_patient_view_hides_score_and_technical_detail(client, doc, service):
    dev = pair_device(client, doc, "stay-32145159")
    stream(client, dev, 32145159, backfill_hours=8)
    service.evaluate("stay-32145159")
    pt = login(client, "p32145159")
    st = client.get("/api/patients/stay-32145159/status", headers=pt).json()
    assert "score" not in st and "result" not in st and set(st["views"]) == {"phone_patient", "watch"}
    assert st["views"]["phone_patient"]["shows_score_number"] is False
    full = client.get("/api/patients/stay-32145159/status", headers=doc).json()
    assert "result" in full and "phone_doctor" in full["views"]
    assert "score" not in client.get("/api/patients/stay-32145159/history", headers=pt).json()[-1]


def test_no_readings_gives_insufficient_data_never_low(client, doc):
    out = client.post("/api/patients/stay-39623478/evaluate", headers=doc).json()
    assert out["status"] == "INSUFFICIENT_DATA" and out["tier"] is None
    assert out["views"]["phone_patient"]["screen"] == "CANNOT_UPDATE"


# ---------------------------------------------------------------------- safety layer
def test_simulated_af_raises_rhythm_alert_and_engine_review(client, doc, service):
    dev = pair_device(client, doc, "stay-32506122")
    sim = stream(client, dev, 32506122, backfill_hours=10)
    sim.trigger_af(60)
    h = {"Authorization": f"Bearer {dev['device_token']}"}
    for _ in range(6):                                    # 30 simulated minutes of AF
        client.post("/api/readings", headers=h, json={"readings": sim.next()})
    kinds = {a["kind"]: a for a in client.get("/api/patients/stay-32506122/alerts?open=true", headers=doc).json()}
    assert "RHYTHM_AF" in kinds and kinds["RHYTHM_AF"]["simulated"] and kinds["RHYTHM_AF"]["severity"] == "high"
    out = service.evaluate("stay-32506122")
    assert out["emergency_state"] in ("REVIEW_REQUIRED", "TRIGGERED")          # HR >= 131 confirmed (NEWS2 red band)
    assert out["features"]["signals"]["rhythm"]["af_readings_6h"] >= 2


def test_fall_check_im_ok_then_no_answer_escalates_without_dispatch(client, doc, service):
    dev = pair_device(client, doc, "stay-33083787")
    sim = stream(client, dev, 33083787, backfill_hours=4)
    h = {"Authorization": f"Bearer {dev['device_token']}"}
    a = client.post("/api/readings", headers=h, json={"readings": sim.trigger_fall()}).json()["alerts"]
    assert len(a) == 1
    first = client.post(f"/api/alerts/{a[0]}/fall-response", headers=h, json={"answer": "im_ok"}).json()
    assert first["state"] == "RESOLVED" and first["history"][-1]["event"] == "WEARER_CONFIRMED_OK"
    sim.next()
    a2 = client.post("/api/readings", headers=h, json={"readings": sim.trigger_fall()}).json()["alerts"][0]
    assert service.alerts.get(a2)["state"] == "AWAITING_RESPONSE"
    time.sleep(1.2)                                       # the test response window is 1 s
    esc = service.safety.overdue()
    assert [e["alert_id"] for e in esc] == [a2]
    e = service.alerts.get(a2)
    modal = e["payload"]["emergency"]
    call = [x for x in modal["actions"] if x["id"] == "call_emergency"][0]
    assert e["state"] == "ESCALATED" and e["severity"] == "critical" and modal["dispatch_status"] == "NOT_DISPATCHED"
    assert call["uri"] == "tel:112" and call["auto_dial"] is False and modal["dismissible"] is False
    st = service.evaluate("stay-33083787")                 # the open fall emergency takes over the screens
    assert st["display_state"] == "EMERGENCY_ALERT" and st["views"]["watch"]["type"] == "EMERGENCY_WATCH"
    assert st["views"]["phone_patient"]["screen"] == "EMERGENCY"
    assert client.post(f"/api/alerts/{a2}/fall-response", headers=h, json={"answer": "im_ok"}).status_code == 400
    bad = client.post(f"/api/alerts/{a2}/user_action", headers=doc, json={"user_action": "ambulance_dispatched"})
    assert bad.status_code == 400 and "never contacts emergency services" in bad.json()["detail"]
    assert client.post(f"/api/alerts/{a2}/user_action", headers=doc, json={"user_action": "opened_dialer_112"}).status_code == 200
    pt = login(client, "p33083787")
    assert client.post(f"/api/alerts/{a2}/resolve", headers=pt, json={"note": "fine"}).status_code == 403
    assert client.post(f"/api/alerts/{a2}/resolve", headers=doc, json={}).status_code == 400
    assert client.post(f"/api/alerts/{a2}/resolve", headers=doc, json={"note": "Called patient, uninjured"}).json()["state"] == "RESOLVED"


def test_symptom_emergency_from_risk_engine(client, doc, service):
    dev = pair_device(client, doc, "stay-35026312")
    stream(client, dev, 35026312, backfill_hours=6)
    pt = login(client, "p35026312")
    out = client.post("/api/patients/stay-35026312/symptoms", headers=pt,
                      json={"symptoms": {"chest_pain": True, "cold_sweat": True}}).json()
    assert out["emergency_state"] == "TRIGGERED" and out["display_state"] == "EMERGENCY_ALERT"
    em = [a for a in client.get("/api/patients/stay-35026312/alerts?open=true", headers=doc).json() if a["kind"] == "EMERGENCY"]
    assert em and em[0]["origin"] == "engine" and em[0]["payload"]["emergency"]["dispatch_status"] == "NOT_DISPATCHED"
    ack = client.post(f"/api/alerts/{em[0]['alert_id']}/acknowledge", headers=pt, json={}).json()
    assert ack["state"] == "ACKNOWLEDGED"
    # the emergency rule is still met, so even a clinician cannot resolve it yet
    r = client.post(f"/api/alerts/{em[0]['alert_id']}/resolve", headers=doc, json={"note": "x"})
    assert r.status_code == 400 and "still met" in r.json()["detail"]


def test_long_qtc_and_dose_over_limit(client, doc, service):
    client.put("/api/patients/stay-36496303/ehr", headers=doc, json={"dose_limits": {"metoprolol": 200}})
    dev = pair_device(client, doc, "stay-36496303")
    h = {"Authorization": f"Bearer {dev['device_token']}"}
    stream(client, dev, 36496303, backfill_hours=2)
    t = service.now_for("stay-36496303").isoformat() + "Z"
    base = {"patient_id": "stay-36496303", "timestamp": t, "source": "simulator"}
    out = client.post("/api/readings", headers=h, json=[{**base, "type": "ecg_qtc", "value": 512},
                                                        {**base, "type": "dose_taken", "value": 150, "meta": {"drug": "metoprolol"}}]).json()
    assert len(out["alerts"]) == 1
    t2 = (service.now_for("stay-36496303")).isoformat() + "Z"
    out2 = client.post("/api/readings", headers=h, json=[{**base, "timestamp": t2, "type": "dose_taken", "value": 100,
                                                         "meta": {"drug": "metoprolol"}}]).json()
    kinds = {a["kind"] for a in client.get("/api/patients/stay-36496303/alerts", headers=doc).json()}
    assert {"QTC_LONG", "DOSE_OVER_LIMIT"} <= kinds and len(out2["alerts"]) == 1


# ---------------------------------------------------------------------- what-if
def test_whatif_on_live_readings_does_not_alert_or_record(client, doc, service):
    dev = pair_device(client, doc, "stay-36893762")
    stream(client, dev, 36893762, backfill_hours=10)
    n_alerts = len(client.get("/api/patients/stay-36893762/alerts", headers=doc).json())
    out = client.post("/api/patients/stay-36893762/whatif", headers=doc, json={"changes": {"hr": 75}}).json()
    assert out["status"] == "OK" and "HYPOTHETICAL" in out["label"] and "difference" in out
    assert len(client.get("/api/patients/stay-36893762/alerts", headers=doc).json()) == n_alerts
    pt = login(client, "p36893762")
    assert client.post("/api/patients/stay-36893762/whatif", headers=pt, json={"changes": {"hr": 75}}).status_code == 403
    bad = client.post("/api/patients/stay-36893762/whatif", headers=doc, json={"changes": {"hr": 500}}).json()
    assert bad["status"] == "INVALID_CHANGE"


def test_whatif_twin_case(client, doc):
    cases = client.get("/api/whatif/cases", headers=doc).json()
    assert any(c["case_id"] == "stay-37093652" for c in cases)
    out = client.post("/api/whatif/simulate", headers=doc, json={"case_id": "stay-37093652", "scenario": {
        "name": "calmer", "changes": {"heart_rate": {"delta": -15}}}}).json()
    assert out["status"] == "OK" and out["simulation"] is True and out["writes_to_patient_record"] is False


# ---------------------------------------------------------------------- WebSockets and the in-process simulator
def recv(ws, kind, n=50):
    """Next message of a type; the device socket also receives its patient's alerts and status pushes."""
    for _ in range(n):
        m = ws.receive_json()
        if m["type"] == kind:
            return m
    raise AssertionError(f"no {kind} message")


def test_websocket_device_and_live_push(client, doc, service):
    dev = pair_device(client, doc, "stay-37267577")
    tok = doc["Authorization"].split()[1]
    from simulator.watch_sim import WatchSimulator, load_replay
    sim = WatchSimulator(load_replay(37267577), "stay-37267577", backfill_hours=6)
    with client.websocket_connect(f"/ws/live/stay-37267577?token={tok}") as live, \
            client.websocket_connect(f"/ws/device?token={dev['device_token']}") as d:
        assert live.receive_json()["type"] == "hello"
        hello = d.receive_json()
        assert hello["type"] == "hello" and hello["label"] == "SIMULATED"
        d.send_json({"type": "readings", "readings": sim.backfill()})
        assert recv(d, "ack")["accepted"] > 0
        service.evaluate("stay-37267577")
        m = recv(live, "status")
        assert m["simulated"] and "phone_doctor" in m["views"]
        w = recv(d, "status")                              # the watch gets its own (reduced) view
        assert set(w["views"]) == {"watch"} and "score" not in w
        d.send_json({"type": "readings", "readings": sim.trigger_fall()})
        fall = recv(d, "alert")
        assert fall["alert"]["kind"] == "FALL" and fall["watch"]["type"] == "FALL_CHECK"
        d.send_json({"type": "fall_response", "alert_id": fall["alert"]["alert_id"], "answer": "im_ok"})
    with pytest.raises(Exception):
        with client.websocket_connect("/ws/live/stay-37267577?token=wrong") as bad:
            bad.receive_json()


def test_in_process_virtual_watch_session(client, doc, service):
    r = client.post("/api/simulator/sessions", headers=doc, json={"patient_id": "stay-38333427", "speed": 30000, "backfill_hours": 6})
    assert r.status_code == 201, r.text
    s = r.json()
    assert s["label"] == "SIMULATED" and s["readings_sent"] > 0 and s["running"]
    assert client.post("/api/simulator/sessions", headers=doc, json={"patient_id": "stay-38333427"}).status_code == 400
    sid = s["session_id"]
    assert client.post(f"/api/simulator/sessions/{sid}/trigger", headers=doc, json={"event": "af"}).json()["in_af"]
    time.sleep(1.0)
    st = client.get(f"/api/simulator/sessions/{sid}", headers=doc).json()
    assert st["readings_sent"] > s["readings_sent"]
    fall = client.post(f"/api/simulator/sessions/{sid}/trigger", headers=doc, json={"event": "fall"}).json()
    assert fall["last_event"]["alerts"]
    assert client.delete(f"/api/simulator/sessions/{sid}", headers=doc).json()["running"] is False
    assert client.get("/simulator").status_code == 200 and "SIMULATED" in client.get("/simulator").text
