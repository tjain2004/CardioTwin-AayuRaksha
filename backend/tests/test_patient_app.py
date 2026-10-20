# Patient app extras: greeting name, appointments (DEMO bookings shown to the doctor), medicines and reminders,
# check-in history, emergency contacts, notifications, settings and the health profile.
from datetime import date, timedelta

from conftest import login, pair_device, stream


def next_open_day(days=3):
    d = date.today() + timedelta(days=days)
    return d + timedelta(days=1) if d.weekday() == 6 else d


def test_demo_seed_gives_name_medicines_and_visits(client, doc, service):
    pt = login(client, "p37093652")
    s = client.get("/api/patients/stay-37093652/app-settings", headers=pt).json()
    assert s["preferred_name"] and s["name_is_demo"] and s["language"] == "en"
    meds = client.get("/api/patients/stay-37093652/medications", headers=pt).json()
    assert meds and all(m["is_demo"] and m["times"] for m in meds)
    appts = client.get("/api/patients/stay-37093652/appointments", headers=pt).json()
    assert {a["status"] for a in appts} >= {"COMPLETED", "BOOKED"}
    assert all(a["label"] == "DEMO" and a["simulated"] and "No real appointment" in a["demo_note"] for a in appts)
    team = client.get("/api/patients/stay-37093652/care-team", headers=pt).json()
    assert team["doctors"][0]["name"].startswith("Dr") and team["caregivers"]
    # seeding runs once per patient, also when the service starts again on the same database
    service.seed_patient_app()
    assert len(client.get("/api/patients/stay-37093652/medications", headers=pt).json()) == len(meds)


def test_book_reschedule_cancel_and_doctor_sees_it(client, doc):
    pt = login(client, "p31248398")
    day = next_open_day().isoformat()
    slots = client.get(f"/api/patients/stay-31248398/appointments/slots?date={day}", headers=pt).json()
    assert not slots["closed"] and slots["label"] == "DEMO"
    free = [s["time"] for s in slots["slots"] if s["available"]]
    assert len(free) >= 2
    r = client.post("/api/patients/stay-31248398/appointments", headers=pt,
                    json={"start_at": f"{day}T{free[0]}", "reason": "Feeling breathless on stairs", "mode": "video"})
    assert r.status_code == 201, r.text
    a = r.json()
    assert a["status"] == "BOOKED" and a["upcoming"] and a["mode_label"] == "Video call"
    # the slot is gone for everyone, and the same doctor cannot be double-booked by another patient
    again = client.get(f"/api/patients/stay-31248398/appointments/slots?date={day}", headers=pt).json()
    assert next(s for s in again["slots"] if s["time"] == free[0]) == {"time": free[0], "available": False, "yours": True}
    other = login(client, "p32145159")
    clash = client.post("/api/patients/stay-32145159/appointments", headers=other, json={"start_at": f"{day}T{free[0]}", "reason": "x"})
    assert clash.status_code == 400
    # the doctor sees it in the clinic list
    clinic = client.get(f"/api/appointments?since={day}", headers=doc).json()
    assert any(x["appointment_id"] == a["appointment_id"] and x["patient_id"] == "stay-31248398" for x in clinic)
    # move it, then cancel it; the slot frees up
    moved = client.post(f"/api/appointments/{a['appointment_id']}/reschedule", headers=pt, json={"start_at": f"{day}T{free[1]}"}).json()
    assert moved["start_at"].endswith(free[1]) and moved["history"][-1]["event"] == "RESCHEDULED"
    c = client.post(f"/api/appointments/{a['appointment_id']}/cancel", headers=pt, json={"reason": "feeling better"}).json()
    assert c["status"] == "CANCELLED" and not c["upcoming"]
    assert client.post(f"/api/appointments/{a['appointment_id']}/cancel", headers=pt, json={}).status_code == 400
    after = client.get(f"/api/patients/stay-31248398/appointments/slots?date={day}", headers=pt).json()
    assert next(s for s in after["slots"] if s["time"] == free[1])["available"]
    notes = client.get("/api/patients/stay-31248398/notifications", headers=pt).json()
    titles = [n["title"] for n in notes["items"]]
    assert "Appointment booked (demo)" in titles and "Appointment moved (demo)" in titles and "Appointment cancelled (demo)" in titles


def test_booking_rules_and_permissions(client, doc):
    pt = login(client, "p32166508")
    sunday = date.today() + timedelta(days=(6 - date.today().weekday()) % 7 or 7)
    s = client.get(f"/api/patients/stay-32166508/appointments/slots?date={sunday}", headers=pt).json()
    assert s["closed"] and not s["slots"]
    past = (date.today() - timedelta(days=2)).isoformat()
    assert client.post("/api/patients/stay-32166508/appointments", headers=pt, json={"start_at": f"{past}T10:00", "reason": "x"}).status_code == 400
    day = next_open_day(5).isoformat()
    assert client.post("/api/patients/stay-32166508/appointments", headers=pt, json={"start_at": f"{day}T10:10", "reason": "x"}).status_code == 400
    # another patient cannot see or book for this patient
    other = login(client, "p32374504")
    assert client.get("/api/patients/stay-32166508/appointments", headers=other).status_code == 403
    assert client.post("/api/patients/stay-32166508/appointments", headers=other, json={"start_at": f"{day}T10:00", "reason": "x"}).status_code == 403
    # only the doctor can mark a visit done
    a = client.post("/api/patients/stay-32166508/appointments", headers=pt, json={"start_at": f"{day}T15:00", "reason": "Check-up"}).json()
    assert client.post(f"/api/appointments/{a['appointment_id']}/complete", headers=pt, json={}).status_code == 403
    done = client.post(f"/api/appointments/{a['appointment_id']}/complete", headers=doc, json={"note": "All fine"}).json()
    assert done["status"] == "COMPLETED" and done["note"] == "All fine"
    assert client.get("/api/appointments", headers=pt).status_code == 403


def test_medicines_reminders_and_taken(client, doc):
    pt = login(client, "p32506122")
    day = date.today().isoformat()
    m = client.post("/api/patients/stay-32506122/medications", headers=pt,
                    json={"name": "Ramipril", "strength": "5 mg", "times": ["20:00", "08:00"]}).json()
    assert m["times"] == ["08:00", "20:00"] and m["source"] == "patient" and not m["is_demo"]
    assert client.post("/api/patients/stay-32506122/medications", headers=pt, json={"name": "X", "times": ["8am"]}).status_code == 400
    t = client.post(f"/api/medications/{m['med_id']}/taken", headers=pt, json={"day": day, "slot": "08:00"}).json()
    assert "08:00" in t["taken"]
    assert client.post(f"/api/medications/{m['med_id']}/taken", headers=pt, json={"day": day, "slot": "09:00"}).status_code == 400
    listed = {x["med_id"]: x for x in client.get(f"/api/patients/stay-32506122/medications?day={day}", headers=pt).json()}
    assert "08:00" in listed[m["med_id"]]["taken"] and "20:00" not in listed[m["med_id"]]["taken"]
    adh = client.get(f"/api/patients/stay-32506122/medications/adherence?days=3&today={day}", headers=pt).json()
    assert adh["days"][-1]["taken"] >= 1
    u = client.put(f"/api/medications/{m['med_id']}", headers=pt, json={"times": ["09:00"], "reminders": False}).json()
    assert u["times"] == ["09:00"] and u["reminders"] is False
    # a medicine added by the doctor lands in the patient's notifications
    client.post("/api/patients/stay-32506122/medications", headers=doc, json={"name": "Furosemide", "strength": "40 mg", "times": ["08:00"]})
    assert any(n["title"] == "Medicine added by your doctor" for n in client.get("/api/patients/stay-32506122/notifications", headers=pt).json()["items"])
    assert client.delete(f"/api/medications/{m['med_id']}", headers=pt).json()["ok"]
    assert m["med_id"] not in {x["med_id"] for x in client.get("/api/patients/stay-32506122/medications", headers=pt).json()}


def test_checkin_history_contacts_settings_profile(client, doc):
    dev = pair_device(client, doc, "stay-33083787")
    stream(client, dev, 33083787, backfill_hours=4)
    pt = login(client, "p33083787")
    r = client.post("/api/patients/stay-33083787/symptoms", headers=pt, json={"symptoms": {"nausea": True, "chest_pain": False}, "note": "after lunch"}).json()
    assert r["checkin"]["symptoms"] == ["nausea"] and "score" not in r
    hist = client.get("/api/patients/stay-33083787/symptoms", headers=pt).json()
    assert hist[0]["note"] == "after lunch" and hist[0]["outcome"]
    # contacts: the first becomes primary; phone numbers are checked
    c1 = client.post("/api/patients/stay-33083787/contacts", headers=pt, json={"name": "Ravi", "relation": "Son", "phone": "+91 99999 00001"}).json()
    assert c1["is_primary"]
    assert client.post("/api/patients/stay-33083787/contacts", headers=pt, json={"name": "Bad", "phone": "call me"}).status_code == 400
    c2 = client.post("/api/patients/stay-33083787/contacts", headers=pt, json={"name": "Asha", "phone": "0802 555 0101", "is_primary": True}).json()
    cs = client.get("/api/patients/stay-33083787/contacts", headers=pt).json()
    assert [c["contact_id"] for c in cs if c["is_primary"]] == [c2["contact_id"]]
    assert client.delete(f"/api/contacts/{c1['contact_id']}", headers=pt).json()["ok"]
    # settings
    s = client.put("/api/patients/stay-33083787/app-settings", headers=pt, json={"preferred_name": "Kamala", "language": "hi"}).json()
    assert s["preferred_name"] == "Kamala" and not s["name_is_demo"] and s["language_label"].startswith("हिन्दी")
    assert client.put("/api/patients/stay-33083787/app-settings", headers=pt, json={"language": "xx"}).status_code == 400
    # plain-language profile without any score
    hp = client.get("/api/patients/stay-33083787/health-profile", headers=pt).json()
    assert hp["measures"] and "score" not in str(hp).lower()
    # notifications: mark all read
    n = client.post("/api/patients/stay-33083787/notifications/read", headers=pt, json={}).json()
    assert all(x["read"] for x in n["items"] if x["kind"] != "alert")
