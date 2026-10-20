# Patient app extras: appointments with the patient's doctor, the medicine list with reminder times, symptom check-in
# history, emergency contacts, in-app notifications, app settings and a plain-language health profile.
# Everything here is DEMO / SIMULATED: a booking is stored in this database and shown on the doctor dashboard; nothing is
# sent to a real clinic, calendar, phone or pharmacy, and there are no payments.
from __future__ import annotations

import re
import sqlite3
import uuid
from datetime import date, datetime, timedelta, timezone

from .auth import Auth, AuthError, Principal
from .db import Database, dumps, loads
from .readings import iso, utcnow

DEMO_LABEL = "DEMO"
DEMO_NOTE = "Demo booking stored in CardioTwin only. No real appointment was made and nothing was sent to a clinic."
SLOT_MINUTES = 30
CLINIC_SESSIONS = (("09:30", "13:00"), ("15:00", "17:30"))   # Monday to Saturday; closed on Sunday
BOOK_AHEAD_DAYS = 60
MIN_NOTICE_MINUTES = 60
MODES = {"in_person": "At the clinic", "video": "Video call", "phone": "Phone call"}
LANGUAGES = {"en": "English", "hi": "हिन्दी (Hindi)", "ta": "தமிழ் (Tamil)", "te": "తెలుగు (Telugu)", "kn": "ಕನ್ನಡ (Kannada)",
             "bn": "বাংলা (Bengali)", "mr": "मराठी (Marathi)"}
WRITERS = ("patient", "caregiver", "clinician")
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")

# Fictional first names for the demo patients' greeting ("Hello, Arjun"), picked from the stay id. Not real people.
DEMO_NAMES = {1: ("Arjun", "Rahul", "Vikram", "Suresh", "Anil", "Rajesh", "Kiran", "Manoj", "Deepak", "Ravi"),
              0: ("Priya", "Anita", "Sunita", "Meera", "Kavita", "Lakshmi", "Neha", "Pooja", "Asha", "Geeta")}


def _offset(s: str) -> timezone:
    m = re.fullmatch(r"([+-])(\d\d):?(\d\d)", s.strip())
    if not m:
        raise ValueError(f"clinic_utc_offset must look like +05:30, got {s!r}")
    mins = int(m[2]) * 60 + int(m[3])
    return timezone(timedelta(minutes=mins if m[1] == "+" else -mins))


class PatientApp:
    def __init__(self, db: Database, auth: Auth, clinic_name: str, clinic_utc_offset: str, alerts=None):
        self.db, self.auth, self.alerts = db, auth, alerts
        self.clinic_name = clinic_name
        self.tz_label = clinic_utc_offset
        self.tz = _offset(clinic_utc_offset)

    # ================================================================ helpers
    def clinic_now(self) -> datetime:
        return datetime.now(self.tz).replace(tzinfo=None, second=0, microsecond=0)

    def _writer(self, p: Principal, patient_id: str) -> None:
        self.auth.require_access(p, patient_id)
        if p.kind != "user" or p.role not in WRITERS:
            raise AuthError("Only the patient, a caregiver or a clinician can change this.")

    def _user_name(self, user_id: str) -> str | None:
        u = self.db.one("SELECT display_name, username FROM users WHERE user_id=?", (user_id,))
        return (u["display_name"] or u["username"]) if u else None

    def notify(self, patient_id: str, kind: str, title: str, body: str | None = None, ref_id: str | None = None) -> None:
        self.db.execute("INSERT INTO notifications VALUES (?,?,?,?,?,?,?,NULL)",
                        (str(uuid.uuid4()), patient_id, kind, title, body, ref_id, iso(utcnow())))

    # ================================================================ care team
    def doctors(self, patient_id: str) -> list[dict]:
        rows = self.db.all("SELECT u.user_id, u.display_name, u.username FROM care_team c JOIN users u ON u.user_id=c.user_id "
                           "WHERE c.patient_id=? AND u.role='clinician' ORDER BY u.username", (patient_id,))
        if not rows:   # demo fallback: a patient registered without a link sees the first clinician
            rows = self.db.all("SELECT user_id, display_name, username FROM users WHERE role='clinician' ORDER BY username LIMIT 1")
        return [{"user_id": r["user_id"], "name": (r["display_name"] or r["username"]).replace(" (clinician)", ""),
                 "role": "Doctor", "specialty": "Cardiology (demo)", "clinic": self.clinic_name} for r in rows]

    def care_team(self, patient_id: str) -> dict:
        carers = self.db.all("SELECT u.user_id, u.display_name, u.username FROM care_team c JOIN users u ON u.user_id=c.user_id "
                             "WHERE c.patient_id=? AND u.role='caregiver' ORDER BY u.username", (patient_id,))
        return {"patient_id": patient_id, "label": DEMO_LABEL, "doctors": self.doctors(patient_id),
                "caregivers": [{"user_id": c["user_id"], "name": c["display_name"] or c["username"], "role": "Caregiver"} for c in carers],
                "clinic": {"name": self.clinic_name, "hours": "Mon to Sat, 09:30 to 13:00 and 15:00 to 17:30",
                           "utc_offset": self.tz_label, "note": "Demo clinic. Not a real practice."}}

    # ================================================================ appointments
    def _clinician(self, patient_id: str, clinician_id: str | None) -> str:
        docs = self.doctors(patient_id)
        if not docs:
            raise ValueError("No doctor is linked to this patient yet.")
        if clinician_id is None:
            return docs[0]["user_id"]
        if clinician_id not in {d["user_id"] for d in docs}:
            raise ValueError("That doctor is not on this patient's care team.")
        return clinician_id

    def _parse_start(self, start_at: str) -> datetime:
        try:
            dt = datetime.strptime(start_at.strip()[:16], "%Y-%m-%dT%H:%M")
        except ValueError:
            raise ValueError("start_at must be YYYY-MM-DDTHH:MM in clinic time.") from None
        return dt

    def _slot_times(self, d: date) -> list[str]:
        if d.weekday() == 6:
            return []
        out = []
        for a, b in CLINIC_SESSIONS:
            t = datetime.combine(d, datetime.strptime(a, "%H:%M").time())
            end = datetime.combine(d, datetime.strptime(b, "%H:%M").time())
            while t + timedelta(minutes=SLOT_MINUTES) <= end:
                out.append(t.strftime("%H:%M"))
                t += timedelta(minutes=SLOT_MINUTES)
        return out

    def _check_bookable(self, dt: datetime) -> None:
        now = self.clinic_now()
        if dt < now + timedelta(minutes=MIN_NOTICE_MINUTES):
            raise ValueError("That time has passed or is too soon. Pick a slot at least an hour from now.")
        if dt.date() > (now + timedelta(days=BOOK_AHEAD_DAYS)).date():
            raise ValueError(f"Bookings open up to {BOOK_AHEAD_DAYS} days ahead.")
        if dt.strftime("%H:%M") not in self._slot_times(dt.date()):
            raise ValueError("The clinic has no slot at that time.")

    def slots(self, patient_id: str, day: str, clinician_id: str | None = None) -> dict:
        cid = self._clinician(patient_id, clinician_id)
        try:
            d = date.fromisoformat(day)
        except ValueError:
            raise ValueError("date must be YYYY-MM-DD.") from None
        times = self._slot_times(d)
        taken = {r["start_at"][11:16] for r in self.db.all(
            "SELECT start_at FROM appointments WHERE clinician_id=? AND status='BOOKED' AND substr(start_at,1,10)=?", (cid, day))}
        mine = {r["start_at"][11:16] for r in self.db.all(
            "SELECT start_at FROM appointments WHERE patient_id=? AND status='BOOKED' AND substr(start_at,1,10)=?", (patient_id, day))}
        now = self.clinic_now()
        out = []
        for t in times:
            dt = datetime.combine(d, datetime.strptime(t, "%H:%M").time())
            ok = dt >= now + timedelta(minutes=MIN_NOTICE_MINUTES) and d <= (now + timedelta(days=BOOK_AHEAD_DAYS)).date()
            out.append({"time": t, "available": ok and t not in taken, "yours": t in mine})
        return {"date": day, "clinician_id": cid, "utc_offset": self.tz_label, "minutes": SLOT_MINUTES,
                "closed": not times, "closed_reason": "The clinic is closed on Sundays." if not times else None, "slots": out,
                "label": DEMO_LABEL}

    def _appt(self, r: dict) -> dict:
        a = dict(r)
        a["history"] = loads(a.pop("history_json"), [])
        a["doctor_name"] = (self._user_name(a["clinician_id"]) or "Doctor").replace(" (clinician)", "")
        a["booked_by_name"] = self._user_name(a["booked_by"])
        a["mode_label"] = MODES.get(a["mode"], a["mode"])
        a["utc_offset"] = self.tz_label
        a["upcoming"] = a["status"] == "BOOKED" and a["start_at"] >= self.clinic_now().strftime("%Y-%m-%dT%H:%M")
        a["label"], a["simulated"], a["demo_note"] = DEMO_LABEL, True, DEMO_NOTE
        return a

    def get_appointment(self, appointment_id: str) -> dict:
        r = self.db.one("SELECT * FROM appointments WHERE appointment_id=?", (appointment_id,))
        if not r:
            raise KeyError(appointment_id)
        return self._appt(r)

    def appointments(self, patient_id: str) -> list[dict]:
        rows = self.db.all("SELECT * FROM appointments WHERE patient_id=? ORDER BY start_at DESC", (patient_id,))
        return [self._appt(r) for r in rows]

    def clinician_appointments(self, p: Principal, since: str | None = None, status: str | None = None) -> list[dict]:
        if p.kind != "user" or p.role != "clinician":
            raise AuthError("Only a clinician has a clinic list.")
        q, args = "SELECT * FROM appointments WHERE clinician_id=?", [p.id]
        if since:
            q, args = q + " AND start_at>=?", [*args, since]
        if status:
            q, args = q + " AND status=?", [*args, status]
        return [self._appt(r) for r in self.db.all(q + " ORDER BY start_at", args)]

    def book(self, p: Principal, patient_id: str, start_at: str, reason: str, mode: str = "in_person",
             clinician_id: str | None = None) -> dict:
        self._writer(p, patient_id)
        cid = self._clinician(patient_id, clinician_id)
        dt = self._parse_start(start_at)
        reason = (reason or "").strip()
        if not reason:
            raise ValueError("Tell the doctor briefly why you want to be seen.")
        if mode not in MODES:
            raise ValueError(f"mode must be one of {tuple(MODES)}")
        self._check_bookable(dt)
        s = dt.strftime("%Y-%m-%dT%H:%M")
        if self.db.one("SELECT 1 FROM appointments WHERE patient_id=? AND start_at=? AND status='BOOKED'", (patient_id, s)):
            raise ValueError("You already have an appointment at that time.")
        aid, now = str(uuid.uuid4()), iso(utcnow())
        hist = [{"event": "BOOKED", "at": now, "by": p.id, "by_role": p.role, "start_at": s}]
        try:
            self.db.execute("INSERT INTO appointments VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (aid, patient_id, cid, s, SLOT_MINUTES, mode, reason[:500], "BOOKED", p.id, None, now, now, dumps(hist)))
        except sqlite3.IntegrityError:
            raise ValueError("Sorry, that slot was just taken. Pick another time.") from None
        a = self.get_appointment(aid)
        self.notify(patient_id, "appointment", "Appointment booked (demo)",
                    f"{a['doctor_name']}, {_nice(s)}, {a['mode_label'].lower()}. {DEMO_NOTE}", aid)
        return a

    def _own(self, p: Principal, appointment_id: str) -> dict:
        a = self.get_appointment(appointment_id)
        self._writer(p, a["patient_id"])
        return a

    def _update(self, a: dict, status: str, event: dict, start_at: str | None = None, note: str | None = None) -> dict:
        hist = a["history"] + [event]
        try:
            self.db.execute("UPDATE appointments SET status=?, start_at=?, note=COALESCE(?, note), updated_at=?, history_json=? "
                            "WHERE appointment_id=?", (status, start_at or a["start_at"], note, iso(utcnow()), dumps(hist), a["appointment_id"]))
        except sqlite3.IntegrityError:
            raise ValueError("Sorry, that slot was just taken. Pick another time.") from None
        return self.get_appointment(a["appointment_id"])

    def cancel(self, p: Principal, appointment_id: str, reason: str | None = None) -> dict:
        a = self._own(p, appointment_id)
        if a["status"] != "BOOKED":
            raise ValueError(f"This appointment is already {a['status'].lower()}.")
        out = self._update(a, "CANCELLED", {"event": "CANCELLED", "at": iso(utcnow()), "by": p.id, "by_role": p.role,
                                            "reason": (reason or "")[:300] or None})
        who = "You" if p.role in ("patient", "caregiver") else out["doctor_name"]
        self.notify(a["patient_id"], "appointment", "Appointment cancelled (demo)",
                    f"{who} cancelled the {_nice(a['start_at'])} appointment with {a['doctor_name']}.", appointment_id)
        return out

    def reschedule(self, p: Principal, appointment_id: str, start_at: str) -> dict:
        a = self._own(p, appointment_id)
        if a["status"] != "BOOKED":
            raise ValueError("Only a booked appointment can be moved.")
        dt = self._parse_start(start_at)
        self._check_bookable(dt)
        s = dt.strftime("%Y-%m-%dT%H:%M")
        if s == a["start_at"]:
            raise ValueError("That is the time it is already booked for.")
        out = self._update(a, "BOOKED", {"event": "RESCHEDULED", "at": iso(utcnow()), "by": p.id, "by_role": p.role,
                                         "from": a["start_at"], "to": s}, start_at=s)
        self.notify(a["patient_id"], "appointment", "Appointment moved (demo)",
                    f"Now {_nice(s)} with {a['doctor_name']} (was {_nice(a['start_at'])}).", appointment_id)
        return out

    def complete(self, p: Principal, appointment_id: str, note: str | None = None, no_show: bool = False) -> dict:
        a = self.get_appointment(appointment_id)
        self.auth.require_access(p, a["patient_id"])
        if p.kind != "user" or p.role != "clinician":
            raise AuthError("Only a clinician can close an appointment.")
        if a["status"] != "BOOKED":
            raise ValueError(f"This appointment is already {a['status'].lower()}.")
        st = "NO_SHOW" if no_show else "COMPLETED"
        out = self._update(a, st, {"event": st, "at": iso(utcnow()), "by": p.id, "by_role": p.role}, note=(note or None))
        if not no_show:
            self.notify(a["patient_id"], "appointment", "Visit summary available (demo)",
                        note or f"{a['doctor_name']} marked your {_nice(a['start_at'])} visit as done.", appointment_id)
        return out

    # ================================================================ medicines
    def _med(self, r: dict, day: str | None) -> dict:
        m = dict(r)
        m["times"] = loads(m.pop("times_json"), [])
        m["reminders"], m["active"] = bool(m["reminders"]), bool(m["active"])
        m["is_demo"] = m["source"] == "demo"
        if day:
            m["taken"] = {x["slot"]: x["taken_at"] for x in self.db.all("SELECT slot, taken_at FROM med_intake WHERE med_id=? AND day=?",
                                                                       (m["med_id"], day))}
        return m

    def medications(self, patient_id: str, day: str | None = None) -> list[dict]:
        rows = self.db.all("SELECT * FROM medications WHERE patient_id=? AND active=1 ORDER BY created_at, name", (patient_id,))
        return [self._med(r, day) for r in rows]

    @staticmethod
    def _times(times: list[str] | None) -> list[str]:
        out = sorted({t.strip() for t in (times or [])})
        bad = [t for t in out if not TIME_RE.match(t)]
        if bad:
            raise ValueError(f"Reminder times must be HH:MM, got {bad}.")
        if len(out) > 8:
            raise ValueError("At most 8 reminder times a day.")
        return out

    def add_medication(self, p: Principal, patient_id: str, name: str, strength: str | None = None, instructions: str | None = None,
                       times: list[str] | None = None, reminders: bool = True, source: str | None = None) -> dict:
        self._writer(p, patient_id)
        name = (name or "").strip()
        if not name:
            raise ValueError("Medicine name is required.")
        mid, now = str(uuid.uuid4()), iso(utcnow())
        src = source or ("doctor" if p.role == "clinician" else "patient")
        self.db.execute("INSERT INTO medications VALUES (?,?,?,?,?,?,?,1,?,?,?)",
                        (mid, patient_id, name[:120], (strength or "").strip()[:60] or None, (instructions or "").strip()[:300] or None,
                         dumps(self._times(times)), int(bool(reminders)), src, now, now))
        if p.role == "clinician":
            self.notify(patient_id, "medication", "Medicine added by your doctor", f"{name} {strength or ''}".strip(), mid)
        return self._med(self.db.one("SELECT * FROM medications WHERE med_id=?", (mid,)), None)

    def _own_med(self, p: Principal, med_id: str) -> dict:
        m = self.db.one("SELECT * FROM medications WHERE med_id=?", (med_id,))
        if not m:
            raise KeyError(med_id)
        self._writer(p, m["patient_id"])
        return m

    def update_medication(self, p: Principal, med_id: str, **changes) -> dict:
        m = self._own_med(p, med_id)
        sets, args = [], []
        for k in ("name", "strength", "instructions"):
            if changes.get(k) is not None:
                v = str(changes[k]).strip()
                if k == "name" and not v:
                    raise ValueError("Medicine name is required.")
                sets.append(f"{k}=?"); args.append(v or None)
        if changes.get("times") is not None:
            sets.append("times_json=?"); args.append(dumps(self._times(changes["times"])))
        if changes.get("reminders") is not None:
            sets.append("reminders=?"); args.append(int(bool(changes["reminders"])))
        if sets:
            self.db.execute(f"UPDATE medications SET {', '.join(sets)}, updated_at=? WHERE med_id=?", (*args, iso(utcnow()), med_id))
        return self._med(self.db.one("SELECT * FROM medications WHERE med_id=?", (med_id,)), None)

    def remove_medication(self, p: Principal, med_id: str) -> dict:
        self._own_med(p, med_id)
        self.db.execute("UPDATE medications SET active=0, updated_at=? WHERE med_id=?", (iso(utcnow()), med_id))
        return {"ok": True, "med_id": med_id}

    def mark_taken(self, p: Principal, med_id: str, day: str, slot: str, taken: bool = True) -> dict:
        m = self._own_med(p, med_id)
        try:
            date.fromisoformat(day)
        except ValueError:
            raise ValueError("day must be YYYY-MM-DD.") from None
        if slot not in loads(m["times_json"], []) and slot != "extra":
            raise ValueError("slot must be one of the medicine's reminder times, or 'extra'.")
        if taken:
            self.db.execute("INSERT OR REPLACE INTO med_intake VALUES (?,?,?,?,?)", (med_id, m["patient_id"], day, slot, iso(utcnow())))
        else:
            self.db.execute("DELETE FROM med_intake WHERE med_id=? AND day=? AND slot=?", (med_id, day, slot))
        return self._med(self.db.one("SELECT * FROM medications WHERE med_id=?", (med_id,)), day)

    def adherence(self, patient_id: str, days: int = 7, today: str | None = None) -> dict:
        end = date.fromisoformat(today) if today else self.clinic_now().date()
        meds = self.medications(patient_id)
        out = []
        for i in range(days - 1, -1, -1):
            d = (end - timedelta(days=i)).isoformat()
            due = sum(len(m["times"]) for m in meds if m["created_at"][:10] <= d)
            taken = self.db.one("SELECT COUNT(*) n FROM med_intake WHERE patient_id=? AND day=? AND slot!='extra'", (patient_id, d))["n"]
            out.append({"day": d, "due": due, "taken": min(taken, due)})
        return {"patient_id": patient_id, "days": out}

    # ================================================================ symptom check-ins
    def record_checkin(self, patient_id: str, symptoms: dict, note: str | None, engine_ts: str | None, out: dict) -> dict:
        self.db.execute("INSERT INTO symptom_checkins (patient_id, created_at, engine_ts, symptoms_json, note, display_state, "
                        "emergency_state, simulated) VALUES (?,?,?,?,?,?,?,?)",
                        (patient_id, iso(utcnow()), engine_ts, dumps(symptoms), (note or "").strip()[:500] or None,
                         out.get("display_state"), out.get("emergency_state"), int(bool(out.get("simulated")))))
        return self.checkins(patient_id, 1)[0]

    def checkins(self, patient_id: str, limit: int = 50) -> list[dict]:
        rows = self.db.all("SELECT * FROM symptom_checkins WHERE patient_id=? ORDER BY id DESC LIMIT ?", (patient_id, limit))
        out = []
        for r in rows:
            r = dict(r)
            s = loads(r.pop("symptoms_json"), {})
            r["symptoms"] = [k for k, v in s.items() if v]
            r["simulated"] = bool(r["simulated"])
            r["outcome"] = ("Emergency screen shown. Your care team was alerted." if r["emergency_state"] not in (None, "NOT_TRIGGERED")
                            else "Sent to your care team." if r["symptoms"] else "No symptoms reported.")
            out.append(r)
        return out

    # ================================================================ emergency contacts
    def contacts(self, patient_id: str) -> list[dict]:
        rows = self.db.all("SELECT * FROM emergency_contacts WHERE patient_id=? ORDER BY is_primary DESC, created_at", (patient_id,))
        return [{**r, "is_primary": bool(r["is_primary"])} for r in rows]

    @staticmethod
    def _phone(phone: str) -> str:
        phone = (phone or "").strip()
        if not re.fullmatch(r"\+?[\d\s\-()]{6,20}", phone):
            raise ValueError("Enter a phone number with digits only (spaces, +, - and brackets are fine).")
        return phone

    def add_contact(self, p: Principal, patient_id: str, name: str, phone: str, relation: str | None = None,
                    is_primary: bool = False) -> dict:
        self._writer(p, patient_id)
        if not (name or "").strip():
            raise ValueError("Contact name is required.")
        if self.db.one("SELECT COUNT(*) n FROM emergency_contacts WHERE patient_id=?", (patient_id,))["n"] >= 5:
            raise ValueError("Up to 5 emergency contacts.")
        cid = str(uuid.uuid4())
        first = not self.contacts(patient_id)
        if is_primary:
            self.db.execute("UPDATE emergency_contacts SET is_primary=0 WHERE patient_id=?", (patient_id,))
        self.db.execute("INSERT INTO emergency_contacts VALUES (?,?,?,?,?,?,?)",
                        (cid, patient_id, name.strip()[:80], (relation or "").strip()[:40] or None, self._phone(phone),
                         int(is_primary or first), iso(utcnow())))
        return next(c for c in self.contacts(patient_id) if c["contact_id"] == cid)

    def update_contact(self, p: Principal, contact_id: str, name: str | None = None, phone: str | None = None,
                       relation: str | None = None, is_primary: bool | None = None) -> dict:
        c = self.db.one("SELECT * FROM emergency_contacts WHERE contact_id=?", (contact_id,))
        if not c:
            raise KeyError(contact_id)
        self._writer(p, c["patient_id"])
        if is_primary:
            self.db.execute("UPDATE emergency_contacts SET is_primary=0 WHERE patient_id=?", (c["patient_id"],))
        self.db.execute("UPDATE emergency_contacts SET name=?, phone=?, relation=?, is_primary=? WHERE contact_id=?",
                        ((name or c["name"]).strip()[:80], self._phone(phone) if phone is not None else c["phone"],
                         relation.strip()[:40] or None if relation is not None else c["relation"],
                         int(is_primary) if is_primary is not None else c["is_primary"], contact_id))
        return next(x for x in self.contacts(c["patient_id"]) if x["contact_id"] == contact_id)

    def remove_contact(self, p: Principal, contact_id: str) -> dict:
        c = self.db.one("SELECT * FROM emergency_contacts WHERE contact_id=?", (contact_id,))
        if not c:
            raise KeyError(contact_id)
        self._writer(p, c["patient_id"])
        self.db.execute("DELETE FROM emergency_contacts WHERE contact_id=?", (contact_id,))
        return {"ok": True, "contact_id": contact_id}

    # ================================================================ notifications
    def notifications(self, patient_id: str, limit: int = 50) -> dict:
        rows = self.db.all("SELECT * FROM notifications WHERE patient_id=? ORDER BY created_at DESC LIMIT ?", (patient_id, limit))
        items = [{**r, "read": r["read_at"] is not None, "simulated": False} for r in rows]
        if self.alerts is not None:   # watch and safety alerts also show in the inbox; they are read once acknowledged
            for a in self.alerts.list(patient_id, limit=20):
                items.append({"notification_id": f"alert:{a['alert_id']}", "patient_id": patient_id, "kind": "alert",
                              "title": (a.get("payload") or {}).get("watch", {}).get("title") or a["title"], "body": a["message"],
                              "ref_id": a["alert_id"], "created_at": a["created_at"], "read_at": None,
                              "read": a["state"] not in ("NEW", "DISPLAYED", "AWAITING_RESPONSE"), "simulated": a["simulated"],
                              "severity": a["severity"], "state": a["state"]})
        items.sort(key=lambda x: x["created_at"], reverse=True)
        return {"patient_id": patient_id, "unread": sum(not x["read"] for x in items), "items": items[:limit]}

    def mark_read(self, p: Principal, patient_id: str, ids: list[str] | None = None) -> dict:
        self.auth.require_access(p, patient_id)
        now = iso(utcnow())
        if ids:
            for i in ids:
                self.db.execute("UPDATE notifications SET read_at=COALESCE(read_at, ?) WHERE patient_id=? AND notification_id=?", (now, patient_id, i))
        else:
            self.db.execute("UPDATE notifications SET read_at=? WHERE patient_id=? AND read_at IS NULL", (now, patient_id))
        return self.notifications(patient_id)

    # ================================================================ settings and profile
    def settings(self, patient_id: str) -> dict:
        s = self.db.one("SELECT * FROM app_settings WHERE patient_id=?", (patient_id,))
        if not s:
            s = {"patient_id": patient_id, "preferred_name": None, "name_is_demo": 0, "language": "en", "med_reminders": 1,
                 "appointment_updates": 1, "updated_at": None}
        s = dict(s)
        for k in ("name_is_demo", "med_reminders", "appointment_updates"):
            s[k] = bool(s[k])
        s["language_label"] = LANGUAGES.get(s["language"], s["language"])
        s["languages"] = [{"code": k, "label": v} for k, v in LANGUAGES.items()]
        return s

    def update_settings(self, p: Principal, patient_id: str, preferred_name: str | None = None, language: str | None = None,
                        med_reminders: bool | None = None, appointment_updates: bool | None = None) -> dict:
        self._writer(p, patient_id)
        cur = self.settings(patient_id)
        if language is not None and language not in LANGUAGES:
            raise ValueError(f"language must be one of {tuple(LANGUAGES)}")
        name = cur["preferred_name"] if preferred_name is None else (preferred_name.strip()[:40] or None)
        self.db.execute("INSERT OR REPLACE INTO app_settings VALUES (?,?,?,?,?,?,?)",
                        (patient_id, name, int(cur["name_is_demo"] and preferred_name is None),
                         language or cur["language"], int(cur["med_reminders"] if med_reminders is None else med_reminders),
                         int(cur["appointment_updates"] if appointment_updates is None else appointment_updates), iso(utcnow())))
        return self.settings(patient_id)

    @staticmethod
    def health_profile(ehr: dict) -> dict:
        """Plain-language profile for the patient from the EHR: measurements, conditions on record and labs. No risk score."""
        pr = ehr.get("profile") or {}
        def has(k):
            return pr.get(k) in (1, True, 1.0)
        conds = []
        if has("hist_af"):
            conds.append({"code": "hist_af", "name": "Atrial fibrillation (irregular heartbeat)", "note": "On your record"})
        if has("ix_bleeding"):
            conds.append({"code": "ix_bleeding", "name": "Past bleeding problem", "note": "On your record"})
        if has("smoker"):
            conds.append({"code": "smoker", "name": "Smoking", "note": "On your record"})
        bmi = pr.get("bmi")
        if isinstance(bmi, (int, float)) and bmi >= 30:
            conds.append({"code": "bmi_30", "name": "Weight above the healthy range", "note": f"BMI {bmi:.0f}"})
        labs = {c.get("name"): c for c in ehr.get("clinical") or []}
        hb = labs.get("hba1c", {}).get("value")
        if isinstance(hb, (int, float)) and hb >= 6.5:
            conds.append({"code": "hba1c", "name": "High blood sugar (diabetes range)", "note": f"HbA1c {hb}%"})
        eg = labs.get("egfr", {}).get("value")
        if isinstance(eg, (int, float)) and eg < 60:
            conds.append({"code": "egfr", "name": "Reduced kidney function", "note": f"eGFR {eg:.0f}"})
        lab_names = {"hba1c": "HbA1c (blood sugar, 3 months)", "egfr": "eGFR (kidney function)", "sbp_outpatient": "Blood pressure, top number",
                     "dbp_outpatient": "Blood pressure, bottom number", "ldl": "LDL cholesterol", "potassium": "Potassium",
                     "creatinine": "Creatinine", "hemoglobin": "Haemoglobin"}
        measures = []
        if pr.get("age_at_admit") is not None:
            measures.append({"label": "Age", "value": f"{pr['age_at_admit']:.0f}"})
        if pr.get("gender_male") in (0, 1):
            measures.append({"label": "Sex", "value": "Male" if pr["gender_male"] == 1 else "Female"})
        if pr.get("height_in"):
            measures.append({"label": "Height", "value": f"{pr['height_in'] * 2.54:.0f} cm"})
        if pr.get("weight_lbs"):
            measures.append({"label": "Weight", "value": f"{pr['weight_lbs'] * 0.4536:.0f} kg"})
        if isinstance(bmi, (int, float)):
            measures.append({"label": "BMI", "value": f"{bmi:.1f}"})
        if pr.get("smoker") in (0, 1):
            measures.append({"label": "Smoker", "value": "Yes" if pr["smoker"] == 1 else "No"})
        return {"patient_id": ehr.get("patient_id"), "measures": measures, "conditions": conds,
                "labs": [{"name": lab_names.get(k, k), "value": c.get("value"), "unit": c.get("unit"), "code": k} for k, c in labs.items()],
                "medicines_on_record": pr.get("n_active_drugs"), "source": ehr.get("source"), "is_demo": bool(pr.get("is_demo")),
                "note": "From your medical record. Ask your doctor if something looks wrong."}

    # ================================================================ demo seed
    def seed_demo(self, patients: list[dict], profiles: dict[str, dict]) -> None:
        """Per demo patient, once: a fictional first name, a demo medicine list, one past visit and a welcome note.
        Patient 37093652 (the default demo login) also gets an upcoming visit, so the home screen shows one."""
        now = self.clinic_now()
        for pt in patients:
            pid = pt["patient_id"]
            key = f"patient_app:{pid}"
            if not pt.get("is_demo") or self.db.one("SELECT 1 FROM demo_seeds WHERE key=?", (key,)):
                continue
            prof = profiles.get(pid, {})
            stay = pt.get("replay_stay_id") or sum(map(ord, pid))
            names = DEMO_NAMES[1 if prof.get("gender_male") == 1 else 0]
            ts = iso(utcnow())
            self.db.execute("INSERT OR IGNORE INTO app_settings VALUES (?,?,1,'en',1,1,?)", (pid, names[stay % len(names)], ts))
            meds = [("Metoprolol", "25 mg tablet", "Once a day with breakfast", ["08:00"]),
                    ("Atorvastatin", "20 mg tablet", "Once a day at night", ["21:00"])]
            if prof.get("hist_af") == 1:
                meds.insert(0, ("Apixaban", "5 mg tablet", "Twice a day, morning and evening", ["08:00", "20:00"]))
            for n, (name, strength, instr, times) in enumerate(meds):
                self.db.execute("INSERT INTO medications VALUES (?,?,?,?,?,?,1,1,'demo',?,?)",
                                (str(uuid.uuid4()), pid, name, strength, instr, dumps(times), ts, ts))
            docs = self.doctors(pid)
            if docs:
                cid = docs[0]["user_id"]
                past = _clinic_day(now.date() - timedelta(days=21 + stay % 7)).strftime("%Y-%m-%dT10:00")
                hist = [{"event": "BOOKED", "at": ts, "by": cid, "by_role": "clinician", "start_at": past},
                        {"event": "COMPLETED", "at": ts, "by": cid, "by_role": "clinician"}]
                self.db.execute("INSERT INTO appointments VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                (str(uuid.uuid4()), pid, cid, past, SLOT_MINUTES, "in_person", "Follow-up after hospital stay",
                                 "COMPLETED", cid, "Demo visit. Keep wearing the watch and log your medicines.", ts, ts, dumps(hist)))
                if stay == 37093652:
                    up = _clinic_day(now.date() + timedelta(days=4)).strftime("%Y-%m-%dT11:00")
                    try:
                        self.db.execute("INSERT INTO appointments VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                        (str(uuid.uuid4()), pid, cid, up, SLOT_MINUTES, "in_person", "Routine heart check-up",
                                         "BOOKED", cid, None, ts, ts, dumps([{"event": "BOOKED", "at": ts, "by": cid, "by_role": "clinician", "start_at": up}])))
                    except sqlite3.IntegrityError:
                        pass
            self.notify(pid, "welcome", "Welcome to CardioTwin",
                        "Your heart readings, medicines and appointments in one place. This is a demo app; appointments here are not real bookings.")
            self.db.execute("INSERT INTO demo_seeds VALUES (?,?)", (key, ts))


def _clinic_day(d: date) -> date:
    return d + timedelta(days=1) if d.weekday() == 6 else d


def _nice(s: str) -> str:
    dt = datetime.strptime(s[:16], "%Y-%m-%dT%H:%M")
    return dt.strftime("%a %d %b, %H:%M")
