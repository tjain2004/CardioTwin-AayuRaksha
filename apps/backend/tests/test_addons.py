# Add-on modules: AI health assistant (chatbot.py), Nearby help and DEMO ambulance (emergency_map.py), Wellness
# (wellness.py) and Heart health (heart_health.py). The AI model and Google APIs are replaced by fakes here, so no key is
# needed and nothing leaves the machine.
import json
from datetime import date, timedelta

import httpx
import pytest

from cardiotwin_backend.chatbot import emergency_card, offline_answer, triage
from conftest import login, pair_device, stream

BLR = {"lat": 12.9716, "lng": 77.5946}


@pytest.fixture
def fake_llm(service):
    calls = []

    def llm(system, messages):
        calls.append({"system": system, "messages": messages})
        return {"text": "Fake AI answer.", "model": "fake"}
    old = service.chat.llm
    service.chat.llm = llm
    yield calls
    service.chat.llm = old


@pytest.fixture
def maps_key(service):
    """A Google server key plus a fake Google transport for Places and Routes."""
    seen = []

    def handler(req: httpx.Request):
        seen.append(req)
        assert req.headers["X-Goog-Api-Key"] == "test-server-key" and req.headers["X-Goog-FieldMask"]
        if "places" in str(req.url):
            return httpx.Response(200, json={"places": [
                {"id": "far", "displayName": {"text": "Far Hospital"}, "location": {"latitude": 12.99, "longitude": 77.61},
                 "businessStatus": "OPERATIONAL", "nationalPhoneNumber": "080 1111"},
                {"id": "near", "displayName": {"text": "Near Hospital"}, "location": {"latitude": 12.972, "longitude": 77.595},
                 "businessStatus": "OPERATIONAL", "currentOpeningHours": {"openNow": True}},
                {"id": "closed", "displayName": {"text": "Closed Clinic"}, "location": {"latitude": 12.973, "longitude": 77.596},
                 "businessStatus": "CLOSED_PERMANENTLY"}]})
        return httpx.Response(200, json={"routes": [{"distanceMeters": 4200, "duration": "780s", "polyline": {"encodedPolyline": "abc"}}]})
    old_http, old_key = service.maps.http, service.settings.google_maps_server_key
    service.maps.http = httpx.Client(transport=httpx.MockTransport(handler))
    service.settings.google_maps_server_key = "test-server-key"
    service.maps._cache.clear()
    yield seen
    service.maps.http, service.settings.google_maps_server_key = old_http, old_key


# ------------------------------------------------------------------------------------------------ chatbot: triage rules
@pytest.mark.parametrize("text,level", [
    ("I have crushing chest pain and I'm sweating", "urgent"),
    ("seene mein dard ho raha hai aur saans phool rahi hai", "urgent"),
    ("मेरे सीने में दर्द है", "urgent"),
    ("my face is drooping and my speech is slurred", "urgent"),
    ("I want to end my life", "urgent"),
    ("I don't have any chest pain today", "none"),
    ("what are the warning signs of a heart attack?", "possible"),
    ("what should I eat for breakfast?", "none"),
])
def test_triage_rules(text, level):
    assert triage(text)["level"] == level


def test_emergency_card_is_fixed_and_never_dispatches():
    card = emergency_card(triage("chest pain and sweating"))
    assert card["call_action"]["uri"] == "tel:112" and card["call_action"]["auto_dial"] is False
    assert "112" in card["message"] and card["checkin_symptoms"]
    hi = emergency_card(triage("chest pain and sweating"), "hi")
    assert hi["language"] == "hi" and "112" in hi["message"] and hi["call_action"]["uri"] == "tel:112"
    sh = emergency_card(triage("I want to kill myself"))
    assert any(n["number"] == "14416" for n in sh["other_numbers"])


def test_offline_answers_cover_cad_topics_and_refuse_doses():
    assert "plaque" in offline_answer("What is CAD?", None, "patient")
    assert "24-hour deterioration" in offline_answer("what is coronary artery disease", None, "patient")
    assert "LDL" in offline_answer("explain cholesterol", None, "patient")
    assert "ECG" in offline_answer("which tests check the heart?", None, "patient")
    assert "can't change" in offline_answer("can I stop my metoprolol dose", None, "patient")
    assert "can't diagnose" in offline_answer("do I have heart disease?", None, "patient")


# ------------------------------------------------------------------------------------------------ chatbot: API
def test_assistant_offline_consent_and_history(client, service):
    pt = login(client, "p31316840")
    pid = "stay-31316840"
    st = client.get("/api/assistant/status", headers=pt).json()
    assert st["mode"] == "offline" and st["label"] == "OFFLINE"            # no key in tests
    assert client.get(f"/api/patients/{pid}/assistant/consent", headers=pt).json()["share_health_data"] is False
    r = client.post(f"/api/patients/{pid}/assistant/messages", headers=pt, json={"message": "Explain my risk level"}).json()
    assert r["reply"]["source"] == "offline" and not r["reply"]["used_health_data"] and "Use my health data" in r["reply"]["content"]
    cid = r["conversation_id"]
    assert client.put(f"/api/patients/{pid}/assistant/consent", headers=pt, json={"share_health_data": True}).json()["share_health_data"]
    r2 = client.post(f"/api/patients/{pid}/assistant/messages", headers=pt,
                     json={"message": "Why is my level what it is?", "conversation_id": cid}).json()
    assert r2["conversation_id"] == cid and r2["reply"]["used_health_data"]
    convs = client.get(f"/api/patients/{pid}/assistant/conversations", headers=pt).json()
    assert convs[0]["conversation_id"] == cid and convs[0]["n_messages"] == 4
    msgs = client.get(f"/api/assistant/conversations/{cid}", headers=pt).json()["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant", "user", "assistant"] and all(m["created_at"] for m in msgs)
    # other users cannot read it, even the doctor; empty messages are refused
    doc = login(client, "dr.demo")
    assert client.get(f"/api/assistant/conversations/{cid}", headers=doc).status_code == 404
    assert client.post(f"/api/patients/{pid}/assistant/messages", headers=pt, json={"message": "   "}).status_code == 400
    assert client.post(f"/api/patients/stay-38229329/assistant/messages", headers=pt, json={"message": "hi"}).status_code == 403
    assert client.delete(f"/api/assistant/conversations/{cid}", headers=pt).json()["ok"]
    assert client.get(f"/api/assistant/conversations/{cid}", headers=pt).status_code == 404


def test_urgent_message_never_waits_for_the_ai(client, service, fake_llm):
    pt = login(client, "p31316840")
    r = client.post("/api/patients/stay-31316840/assistant/messages", headers=pt,
                    json={"message": "I have severe chest pain spreading to my left arm"}).json()
    assert fake_llm == []                                                   # the model was not called at all
    assert r["reply"]["source"] == "safety_rules" and r["emergency"]["level"] == "urgent"
    assert r["emergency"]["call_action"]["uri"] == "tel:112" and r["emergency"]["checkin_symptoms"]
    hi = client.post("/api/patients/stay-31316840/assistant/messages", headers=pt,
                     json={"message": "seene mein dard hai", "language": "hi", "conversation_id": r["conversation_id"]}).json()
    assert fake_llm == [] and hi["language"] == "hi" and hi["emergency"]["language"] == "hi"


def test_ai_context_has_no_score_for_patients_but_does_for_clinicians(client, doc, service, fake_llm):
    pid = "stay-38229329"
    dev = pair_device(client, doc, pid)
    stream(client, dev, 38229329, backfill_hours=8)
    service.evaluate(pid)
    pt = login(client, "p38229329")
    client.put(f"/api/patients/{pid}/assistant/consent", headers=pt, json={"share_health_data": True})
    r = client.post(f"/api/patients/{pid}/assistant/messages", headers=pt, json={"message": "Explain my result", "language": "hi"}).json()
    assert r["reply"]["source"] == "ai" and r["reply"]["content"] == "Fake AI answer."
    ctx = fake_llm[-1]["system"][1]["text"]
    data = json.loads(ctx.split("<cardiotwin_context>\n")[1].split("\n</cardiotwin_context>")[0])
    assert data["shows_score_number"] is False and "score" not in data["heart_check"]
    assert "stay-" not in ctx and "Demo patient" not in ctx                # no names or ids go to the model
    assert "<language>" in ctx and "Hindi" in ctx
    assert "24-hour deterioration" in fake_llm[-1]["system"][0]["text"]
    client.post(f"/api/patients/{pid}/assistant/messages", headers=doc, json={"message": "Summarise the result"})
    dctx = json.loads(fake_llm[-1]["system"][1]["text"].split("<cardiotwin_context>\n")[1].split("\n</cardiotwin_context>")[0])
    assert dctx["shows_score_number"] is True and "score" in dctx["heart_check"]
    # a clinician cannot switch the patient's data sharing
    assert client.put(f"/api/patients/{pid}/assistant/consent", headers=doc, json={"share_health_data": False}).status_code == 403


def test_ai_failure_falls_back_to_offline(client, service):
    old = service.chat.llm
    service.chat.llm = lambda system, messages: {"error": "timeout"}
    try:
        pt = login(client, "p31316840")
        r = client.post("/api/patients/stay-31316840/assistant/messages", headers=pt, json={"message": "How much salt is ok?"}).json()
        assert r["reply"]["source"] == "offline" and "unavailable right now" in r["reply"]["content"] and "5 g" in r["reply"]["content"]
    finally:
        service.chat.llm = old


# ------------------------------------------------------------------------------------------------ maps and DEMO ambulance
def test_maps_without_keys_degrades_to_a_list(client):
    pt = login(client, "p39492446")
    cfg = client.get("/api/maps/config", headers=pt).json()
    assert cfg["maps"]["configured"] is False and "server" not in json.dumps(cfg).lower().replace("server_key", "")
    out = client.post("/api/patients/stay-39492446/nearby-hospitals", headers=pt, json=BLR).json()
    assert out["available"] is False and out["reason"] == "no_server_key" and out["maps_search_url"].startswith("https://www.google.com/maps")
    assert {n["number"] for n in out["emergency_numbers"]} >= {"112", "108"}
    assert out["ambulances"]["label"] == "DEMO" and all(v["simulated"] for v in out["ambulances"]["vehicles"])
    assert client.post("/api/patients/stay-39492446/nearby-hospitals", headers=pt, json={"lat": 999, "lng": 0}).status_code == 422


def test_places_and_routes_parsing(client, maps_key):
    pt = login(client, "p39492446")
    out = client.post("/api/patients/stay-39492446/nearby-hospitals", headers=pt, json=BLR).json()
    assert out["available"] and [h["name"] for h in out["hospitals"]] == ["Near Hospital", "Far Hospital"]
    rt = client.post("/api/patients/stay-39492446/route", headers=pt,
                     json={"origin_lat": BLR["lat"], "origin_lng": BLR["lng"], "dest_lat": 12.99, "dest_lng": 77.61}).json()
    assert rt["available"] and rt["duration_s"] == 780 and rt["distance_m"] == 4200
    assert all("test-server-key" not in json.dumps(x) for x in (out, rt))   # the key never reaches the app


def test_medical_stores_from_places_only(client, maps_key):
    pt = login(client, "p39492446")
    out = client.post("/api/patients/stay-39492446/nearby-pharmacies", headers=pt, json=BLR).json()
    assert out["available"] and [h["name"] for h in out["pharmacies"]] == ["Near Hospital", "Far Hospital"]
    places_body = json.loads(maps_key[-1].content)
    assert places_body["includedTypes"] == ["pharmacy", "drugstore"]
    assert "does not tell you which medicine" in out["medicine_note"] and out["maps_search_url"].endswith("query=pharmacy")
    # hospitals and medical stores are cached separately
    hosp = client.post("/api/patients/stay-39492446/nearby-hospitals", headers=pt, json=BLR).json()
    assert json.loads(maps_key[-1].content)["includedTypes"] == ["hospital"] and not hosp.get("cached")
    assert "test-server-key" not in json.dumps(out)


def test_medical_stores_without_key_offer_a_search_not_made_up_shops(client):
    pt = login(client, "p39492446")
    out = client.post("/api/patients/stay-39492446/nearby-pharmacies", headers=pt, json=BLR).json()
    assert out["available"] is False and out["pharmacies"] == [] and out["reason"] == "no_server_key"
    assert out["maps_search_url"] == "https://www.google.com/maps/search/?api=1&query=pharmacy"
    assert client.post("/api/patients/stay-39492446/nearby-pharmacies", headers=login(client, "p39544395"), json=BLR).status_code == 403


def test_demo_ambulance_needs_confirmation_and_links_to_the_emergency(client, doc, service):
    pid = "stay-39544395"
    dev = pair_device(client, doc, pid)
    stream(client, dev, 39544395, backfill_hours=6)
    pt = login(client, "p39544395")
    em = client.post(f"/api/patients/{pid}/symptoms", headers=pt, json={"symptoms": {"chest_pain": True, "cold_sweat": True}}).json()
    assert em["emergency_state"] == "TRIGGERED"
    assert client.post(f"/api/patients/{pid}/ambulance", headers=pt, json=BLR).status_code == 400   # not confirmed
    r = client.post(f"/api/patients/{pid}/ambulance", headers=pt, json={**BLR, "confirm_demo": True})
    assert r.status_code == 201, r.text
    a = r.json()
    assert a["simulated"] and a["label"] == "DEMO" and a["status"] == "REQUESTED" and a["stages"][-1] == "COMPLETED"
    assert a["alert_id"]                                                     # recorded on the existing emergency alert
    alert = service.alerts.get(a["alert_id"])
    acts = [h.get("action") or h.get("user_action") for h in alert["history"]]
    assert "started_demo_ambulance_simulation_not_dispatched" in acts
    assert client.get(f"/api/patients/{pid}/ambulance/active", headers=pt).json()["request"]["request_id"] == a["request_id"]
    # later stages, by moving the clock of the stored request back
    from cardiotwin_backend.readings import iso, utcnow
    for secs, stage in ((8, "ASSIGNED"), (60, "EN_ROUTE"), (175, "ARRIVING"), (200, "ARRIVED"), (400, "COMPLETED")):
        service.db.execute("UPDATE ambulance_requests SET created_at=? WHERE request_id=?",
                           (iso(utcnow() - timedelta(seconds=secs)), a["request_id"]))
        v = client.get(f"/api/ambulance/{a['request_id']}", headers=pt).json()
        assert v["status"] == stage and (v["vehicle"] is None or v["vehicle"]["simulated"])
    c = client.post(f"/api/ambulance/{a['request_id']}/cancel", headers=pt).json()
    assert c["status"] == "CANCELLED" and c["destination"] is None          # location is not kept after cancelling
    other = login(client, "p39635619")
    assert client.get(f"/api/ambulance/{a['request_id']}", headers=other).status_code == 403


# ------------------------------------------------------------------------------------------------ wellness
def test_wellness_plan_respects_diet_kidney_fluid_and_exercise_limits(client, doc, service):
    pid = "stay-39635619"
    pt = login(client, "p39635619")
    prefs = client.put(f"/api/patients/{pid}/wellness/preferences", headers=pt, json={"diet": "veg", "region": "south"})
    assert prefs.status_code == 200, prefs.text
    plan = client.get(f"/api/patients/{pid}/wellness", headers=pt).json()
    assert plan["meals"] and all(m["diet"] == "veg" for m in plan["meals"])
    assert client.put(f"/api/patients/{pid}/wellness/preferences", headers=pt, json={"diet": "keto"}).status_code == 400
    # a clinician's restrictions on the record take priority
    service.update_ehr(pid, profile={"wellness_restrictions": {"fluid_limit_ml": 1000, "no_exercise": True, "note": "After surgery."}},
                       clinical=[*service.ehr(pid)["clinical"], {"name": "egfr", "value": 25.0, "unit": "mL/min/1.73m2"}])
    plan = client.get(f"/api/patients/{pid}/wellness", headers=pt).json()
    assert plan["activity"]["gate"]["state"] == "PAUSED" and plan["activity"]["suggestions"] == []
    assert plan["goals"]["water_glasses"] <= 4 and plan["fluid_note"]
    from cardiotwin_backend.wellness import MEALS
    high_k = {m[3] for m in MEALS if "k" in m[6]}
    assert not {m["name"] for m in plan["meals"]} & high_k


def test_wellness_log_progress_and_reminders(client):
    pid, pt = "stay-39635619", login(client, "p39635619")
    today = date.today().isoformat()
    assert client.post(f"/api/patients/{pid}/wellness/log", headers=pt, json={"day": today, "metric": "water_glasses", "value": 2}).status_code == 200
    client.post(f"/api/patients/{pid}/wellness/log", headers=pt, json={"day": today, "metric": "water_glasses", "value": 1, "mode": "add"})
    client.post(f"/api/patients/{pid}/wellness/log", headers=pt, json={"day": today, "metric": "sleep_hours", "value": 7.5})
    pr = client.get(f"/api/patients/{pid}/wellness/progress?day={today}", headers=pt).json()
    water = next(i for i in pr["today"] if i["metric"] == "water_glasses")
    assert water["value"] == 3 and len(pr["week"]) == 7 and isinstance(pr["insights"], list)
    assert client.post(f"/api/patients/{pid}/wellness/log", headers=pt, json={"day": today, "metric": "sleep_hours", "value": 30}).status_code == 400
    assert client.post(f"/api/patients/{pid}/wellness/log", headers=pt, json={"day": today, "metric": "beer", "value": 1}).status_code == 400
    assert client.get(f"/api/patients/{pid}/wellness/reminders", headers=pt).status_code == 200


# ------------------------------------------------------------------------------------------------ heart health
def test_heart_overview_and_summary_without_inventing_data(client, doc, service):
    pid = "stay-38229329"                                                   # streamed and evaluated above
    pt = login(client, "p38229329")
    ov = client.get(f"/api/patients/{pid}/heart/overview", headers=pt).json()
    assert ov["status"]["check_name"] == "24-hour deterioration check" and "not a test for coronary" in ov["status"]["check_meaning"]
    assert "score" not in ov["status"]                                      # patients see words only
    assert ov["measurements"]["ldl"]["value"] == 156.0 and ov["measurements"]["ldl"]["source"] == "From your record"
    assert ov["measurements"]["ldl"]["trend"] == "SINGLE_VALUE"             # one value: no trend is inferred
    assert ov["measurements"]["heart_rate"]["source"] == "SIMULATED"
    fam = next(f for f in ov["risk_factors"]["non_modifiable"] if f["key"] == "family_history")
    assert fam["status"] == "UNKNOWN"
    dov = client.get(f"/api/patients/{pid}/heart/overview", headers=doc).json()
    assert "score" in dov["status"]
    sm = client.get(f"/api/patients/{pid}/heart/summary", headers=pt).json()
    sec = sm["sections"]
    assert set(sec) >= {"current_risk", "main_factors", "positive_trends", "areas_to_watch", "lifestyle_focus", "next_step"}
    assert "Family history" in sec["missing_information"] and "not AI" in sm["generated_by"]
    assert any("Smoking" in w for w in sec["areas_to_watch"]) and any("Quitline" in f for f in sec["lifestyle_focus"])
    other = client.get("/api/patients/stay-31316840/heart/overview", headers=login(client, "p31316840")).json()
    assert other["measurements"]["ldl"]["status"] == "NOT_RECORDED"        # not in the record: shown as missing, never guessed
    assert other["status"]["state"] == "NO_EVALUATION_YET"
    assert "0." not in sec["current_risk"]                                  # no probability in the patient's words


def test_weekly_report_history(client, service):
    pid, pt = "stay-38229329", login(client, "p38229329")
    lst = client.get(f"/api/patients/{pid}/heart/reports", headers=pt).json()
    assert lst and lst[0]["week_start"] == (date.today() - timedelta(days=date.today().weekday() + 7)).isoformat()
    now = client.post(f"/api/patients/{pid}/heart/reports", headers=pt)
    assert now.status_code == 201 and now.json()["simulated"] and "SIMULATED" in now.json()["watch_window"]["clock"]
    rep = client.get(f"/api/patients/{pid}/heart/reports/{now.json()['week_start']}", headers=pt).json()
    assert rep["risk_trend"]["check_name"] == "24-hour deterioration check" and rep["disclaimer"]
    assert len(client.get(f"/api/patients/{pid}/heart/reports", headers=pt).json()) == 2
    assert client.get(f"/api/patients/{pid}/heart/reports/2001-01-01", headers=pt).status_code == 404
    notes = client.get(f"/api/patients/{pid}/notifications", headers=pt).json()["items"]
    assert any(n["kind"] == "heart_info" and "weekly heart report" in n["title"] for n in notes)


def test_heart_alerts_go_to_the_inbox_once(client, doc, service):
    pid = "stay-38229329"
    status = service.latest_status(pid)
    feats = service.build_request(pid)[1]
    fake = {**status, "features": {"signals": {**feats["signals"], "hr": {**feats["signals"]["hr"],
                                                                         "last": {"value": 80, "minutes_old": 200}}}}}
    first = service.heart.scan(pid, fake)
    assert "stale_data" in first and "stale_data" not in service.heart.scan(pid, fake)        # deduplicated
    assert service.heart.preventive_reminders(pid) and not service.heart.preventive_reminders(pid)
    notes = client.get(f"/api/patients/{pid}/notifications", headers=login(client, "p38229329")).json()["items"]
    kinds = {n["kind"] for n in notes}
    assert {"heart_attention", "heart_info"} <= kinds and "heart_emergency" not in kinds      # emergencies stay with the existing system


def test_caregiver_consent_is_the_patients_choice(client, service):
    pid = "stay-37093652"                                                   # the demo caregiver is linked to this patient
    pt, carer = login(client, "p37093652"), login(client, "carer.demo")
    cons = client.get(f"/api/patients/{pid}/caregiver-consent", headers=pt).json()
    assert cons and cons[0]["emergency"] is True and cons[0]["health_alerts"] is False
    cid = cons[0]["caregiver_id"]
    assert client.get(f"/api/patients/{pid}/heart/overview", headers=carer).status_code == 403
    assert client.put(f"/api/patients/{pid}/caregiver-consent/{cid}", headers=carer, json={"health_alerts": True}).status_code == 403
    on = client.put(f"/api/patients/{pid}/caregiver-consent/{cid}", headers=pt, json={"health_alerts": True}).json()
    assert on[0]["health_alerts"] is True and on[0]["weekly_reports"] is False
    assert client.get(f"/api/patients/{pid}/heart/overview", headers=carer).status_code == 200
    assert client.get(f"/api/patients/{pid}/heart/reports", headers=carer).status_code == 403
    service.heart._alert(pid, "test:attention", "attention", "test", "Test attention notice", "x", False)
    seen = lambda h: {n["title"] for n in client.get(f"/api/patients/{pid}/notifications", headers=h).json()["items"]}
    assert "Test attention notice" in seen(carer) and "Test attention notice" in seen(pt)
    assert len(client.get(f"/api/patients/{pid}/caregiver-consent", headers=carer).json()) == 1      # only their own row
    client.put(f"/api/patients/{pid}/caregiver-consent/{cid}", headers=pt, json={"health_alerts": False})
    assert client.get(f"/api/patients/{pid}/heart/overview", headers=carer).status_code == 403   # revoked
    assert "Test attention notice" not in seen(carer) and "Test attention notice" in seen(pt)


def test_health_reports_add_on_status(client):
    h = client.get("/api/health").json()
    assert h["assistant"] == "offline" and h["maps_configured"] is False and h["ambulance_provider"] == "demo"
