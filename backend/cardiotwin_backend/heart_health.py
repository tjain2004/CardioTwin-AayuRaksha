# Heart health (CAD) overview, "Explain my heart health" summary, weekly reports, health alerts and caregiver consent.
#
# Everything here is deterministic and read from what CardioTwin already stores: the risk engine's evaluations (the
# saved model, SHAP reasons, data freshness), watch readings with their rolling baseline (features.py), the EHR and the
# wellness log. Nothing is predicted afresh and no AI writes these numbers.
#
# Important: CardioTwin's model predicts 24-hour acute deterioration (vasopressor start, lactate >= 4 or death), not
# coronary artery disease. The CAD view is therefore a risk-factor profile from the record (plus the ASCVD 10-year
# estimate already stored in the record), and the 24-hour check is always named as what it is. Neither is a diagnosis.
#
# Health alerts go into the existing notifications inbox with a level (Information, Attention, High priority). The
# Emergency level belongs only to the existing emergency system: alerts from it already appear in the inbox and are
# labelled Emergency there; this module never raises an emergency of its own.
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from statistics import median

import pandas as pd

from .auth import AuthError, Principal
from .db import Database, dumps, loads
from .readings import iso, utcnow

SCHEMA = """
CREATE TABLE IF NOT EXISTS heart_alerts (
  alert_key TEXT PRIMARY KEY, patient_id TEXT NOT NULL, level TEXT NOT NULL, rule TEXT NOT NULL, notification_id TEXT,
  simulated INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_heartalert_pt ON heart_alerts (patient_id, created_at);
CREATE TABLE IF NOT EXISTS heart_reports (
  patient_id TEXT NOT NULL, week_start TEXT NOT NULL, created_at TEXT NOT NULL, report_json TEXT NOT NULL,
  PRIMARY KEY (patient_id, week_start));
CREATE TABLE IF NOT EXISTS caregiver_consent (
  patient_id TEXT NOT NULL, caregiver_id TEXT NOT NULL, emergency INTEGER NOT NULL DEFAULT 1, health_alerts INTEGER NOT NULL DEFAULT 0,
  weekly_reports INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL, updated_by TEXT, PRIMARY KEY (patient_id, caregiver_id));
"""

CHECK_NAME = "24-hour deterioration check"
CHECK_MEANING = ("CardioTwin's model estimates the chance of a sudden worsening (needing blood-pressure support drugs, a very high "
                 "lactate or death) in the next 24 hours. It is not a test for coronary artery disease and not a diagnosis.")
LEVELS = {"info": "Information", "attention": "Attention", "high": "High priority", "emergency": "Emergency"}
SOURCES = {"device": "Verified device", "simulated": "SIMULATED", "record": "From your record", "you": "Logged by you",
           "model": "CardioTwin model", "rules": "CardioTwin rules"}
# "better" direction for trends: spo2 higher is better; heart and breathing rate closer to a typical resting value
TREND_TARGET = {"hr": ("Heart rate", "bpm", 75.0, 5.0), "spo2": ("Oxygen", "%", 100.0, 1.0), "resp": ("Breathing rate", "/min", 16.0, 2.0)}
ABNORMAL = {"hr": lambda v: v > 110 or v < 50, "spo2": lambda v: v < 94, "resp": lambda v: v > 24 or v < 9}
STALE_MIN, MISSED_H = 60, 24


def _bmi(prof: dict) -> float | None:
    if isinstance(prof.get("bmi"), (int, float)):
        return float(prof["bmi"])
    h, w = prof.get("height_in"), prof.get("weight_lbs")
    if isinstance(h, (int, float)) and isinstance(w, (int, float)) and h > 0:
        return round(703 * w / (h * h), 1)
    return None


def _ascvd_band(x: float) -> str:
    p = x * 100 if x <= 1 else x
    return "low" if p < 5 else "borderline" if p < 7.5 else "intermediate" if p < 20 else "high"


class HeartHealth:
    def __init__(self, db: Database, svc):
        self.db, self.svc = db, svc
        db.ensure_schema(SCHEMA)

    # ================================================================ caregiver consent
    def _caregivers(self, patient_id: str) -> list[dict]:
        return self.db.all("SELECT u.user_id, u.display_name, u.username FROM care_team c JOIN users u ON u.user_id=c.user_id "
                           "WHERE c.patient_id=? AND u.role='caregiver' ORDER BY u.username", (patient_id,))

    def caregiver_consent(self, p: Principal, patient_id: str) -> list[dict]:
        self.svc.auth.require_access(p, patient_id)
        out = []
        for c in self._caregivers(patient_id):
            r = self.db.one("SELECT * FROM caregiver_consent WHERE patient_id=? AND caregiver_id=?", (patient_id, c["user_id"]))
            out.append({"caregiver_id": c["user_id"], "name": c["display_name"] or c["username"],
                        "emergency": bool(r["emergency"]) if r else True, "health_alerts": bool(r and r["health_alerts"]),
                        "weekly_reports": bool(r and r["weekly_reports"]), "updated_at": r["updated_at"] if r else None})
        if p.kind == "user" and p.role == "caregiver":                  # a caregiver sees only their own sharing
            out = [c for c in out if c["caregiver_id"] == p.id]
        return out

    def filter_notifications(self, p: Principal, patient_id: str, notes: dict) -> dict:
        """The inbox as a caregiver may see it: heart notices, weekly-report notices and emergency alerts only with the
        patient's consent. Patients and clinicians see everything, as before."""
        if not (p.kind == "user" and p.role == "caregiver"):
            return notes
        r = self.db.one("SELECT * FROM caregiver_consent WHERE patient_id=? AND caregiver_id=?", (patient_id, p.id))
        ok = {"emergency": bool(r["emergency"]) if r else True, "health_alerts": bool(r and r["health_alerts"]),
              "weekly_reports": bool(r and r["weekly_reports"])}

        def allowed(n):
            if n["kind"] == "alert" and n.get("severity") == "critical":
                return ok["emergency"]
            if n["kind"] == "heart_info" and n["title"].startswith("Your weekly heart report"):
                return ok["weekly_reports"]
            if n["kind"].startswith("heart_"):
                return ok["health_alerts"]
            return True
        items = [n for n in notes["items"] if allowed(n)]
        return {**notes, "items": items, "unread": sum(not x["read"] for x in items)}

    def set_caregiver_consent(self, p: Principal, patient_id: str, caregiver_id: str, **flags) -> list[dict]:
        self.svc.auth.require_access(p, patient_id)
        if p.kind != "user" or p.role != "patient":
            raise AuthError("Only the patient can choose what a caregiver may see.")
        if caregiver_id not in {c["user_id"] for c in self._caregivers(patient_id)}:
            raise KeyError(caregiver_id)
        cur = {c["caregiver_id"]: c for c in self.caregiver_consent(p, patient_id)}[caregiver_id]
        vals = {k: bool(flags[k]) if flags.get(k) is not None else cur[k] for k in ("emergency", "health_alerts", "weekly_reports")}
        self.db.execute("INSERT OR REPLACE INTO caregiver_consent VALUES (?,?,?,?,?,?,?)",
                        (patient_id, caregiver_id, int(vals["emergency"]), int(vals["health_alerts"]), int(vals["weekly_reports"]),
                         iso(utcnow()), p.id))
        return self.caregiver_consent(p, patient_id)

    def require_caregiver(self, p: Principal, patient_id: str, what: str) -> None:
        """Caregivers see the new heart-health features only with the patient's consent (existing screens are unchanged)."""
        self.svc.auth.require_access(p, patient_id)
        if p.kind == "user" and p.role == "caregiver":
            r = self.db.one("SELECT * FROM caregiver_consent WHERE patient_id=? AND caregiver_id=?", (patient_id, p.id))
            if not (r and r[what]):
                raise AuthError("The patient has not shared this with you. They can turn it on under Profile, My care team.")

    # ================================================================ readings helpers
    def _signals(self, patient_id: str) -> tuple[dict, pd.Timestamp, bool]:
        req, feats = self.svc.build_request(patient_id)
        return feats["signals"], pd.Timestamp(feats["now"]), self.svc.is_simulated(patient_id)

    @staticmethod
    def _trend(sig: dict, name: str, simulated: bool) -> dict:
        label, unit, target, tol = TREND_TARGET[name]
        base = (sig or {}).get("baseline") or {}
        d24 = (sig or {}).get("24h")
        last = (sig or {}).get("last")
        out = {"signal": name, "label": label, "unit": unit, "source": SOURCES["simulated" if simulated else "device"]}
        if not last:
            return {**out, "trend": "MISSING", "text": f"No {label.lower()} readings."}
        out["latest"] = round(last["value"], 1)
        if last["minutes_old"] > STALE_MIN:
            return {**out, "trend": "STALE", "text": f"Last {label.lower()} reading is {int(last['minutes_old'])} minutes old."}
        if base.get("status") not in ("AVAILABLE", "UNRELIABLE") or not d24 or d24["n"] < 3:
            return {**out, "trend": "INSUFFICIENT_DATA", "text": "Not enough history yet to tell a trend."}
        usual, now_mean = base["median"], d24["mean"]
        spread = max(1.4826 * (base.get("mad") or 0), tol)
        out.update(usual=round(usual, 1), last_24h=round(now_mean, 1), baseline_status=base["status"])
        if abs(now_mean - usual) < spread:
            t = "STABLE"
        else:
            closer = abs(now_mean - target) < abs(usual - target) if name != "spo2" else now_mean > usual
            t = "IMPROVING" if closer else "WORSENING"
        txt = {"STABLE": "About the same as usual", "IMPROVING": "Moving towards a healthier range", "WORSENING": "Moving away from your usual range"}[t]
        if base["status"] == "UNRELIABLE":
            txt += " (short history, less certain)"
        return {**out, "trend": t, "text": txt}

    def _risk_trend(self, patient_id: str) -> dict:
        rows = [r for r in self.svc.history(patient_id, 120) if r["status"] == "OK" and r["score"] is not None]
        if len(rows) < 12:
            return {"trend": "INSUFFICIENT_DATA", "text": "Not enough checks yet to tell a trend."}
        recent, before = median(r["score"] for r in rows[-6:]), median(r["score"] for r in rows[:-6])
        if before <= 0 or abs(recent - before) / before < 0.2:
            t = "STABLE"
        else:
            t = "WORSENING" if recent > before else "IMPROVING"
        return {"trend": t, "text": {"STABLE": "Steady over recent checks", "WORSENING": "Higher over the latest checks than before",
                                     "IMPROVING": "Lower over the latest checks than before"}[t], "n_checks": len(rows)}

    # ================================================================ overview
    def overview(self, p: Principal, patient_id: str) -> dict:
        self.require_caregiver(p, patient_id, "health_alerts")
        technical = self.svc.auth.can(p, "view_technical")
        ehr = self.svc.ehr(patient_id)
        prof = ehr["profile"] or {}
        labs = {c.get("name"): c for c in ehr["clinical"] or []}
        st = self.svc.latest_status(patient_id)
        sig, now, sim = self._signals(patient_id)
        wl = self.svc.wellness._logged(patient_id, (date.today() - timedelta(days=6)).isoformat(), date.today().isoformat())

        status = {"check_name": CHECK_NAME, "check_meaning": CHECK_MEANING, "source": SOURCES["model"]}
        if st is None:
            status.update(state="NO_EVALUATION_YET", text="No heart check yet. It starts once the watch sends readings.")
        else:
            pv = st["views"].get("phone_patient") or {}
            status.update(state=st["status"], level=pv.get("tier") if st["status"] == "OK" else None, screen=pv.get("screen"),
                          text=pv.get("message"), emergency_state=st["emergency_state"], checked_at=st["evaluated_at"],
                          simulated=bool(st["simulated"]))
            if technical:
                status["score"] = st["score"]
        status["trend"] = self._risk_trend(patient_id)

        def lab(name, label, unit=None):
            c = labs.get(name)
            if not c or c.get("value") is None:
                return {"label": label, "value": None, "status": "NOT_RECORDED", "source": None, "trend": "MISSING"}
            return {"label": label, "value": c["value"], "unit": c.get("unit") or unit, "source": SOURCES["record"],
                    "trend": "SINGLE_VALUE", "text": "One value on record, so no trend can be shown."}

        sbp, dbp = labs.get("sbp_outpatient", {}).get("value"), labs.get("dbp_outpatient", {}).get("value")
        measurements = {
            "blood_pressure": ({"label": "Blood pressure", "value": f"{sbp:.0f}/{dbp:.0f}" if dbp is not None else f"{sbp:.0f}", "unit": "mmHg",
                                "source": SOURCES["record"], "trend": "SINGLE_VALUE", "text": "One clinic value on record; the watch does not measure blood pressure."}
                               if sbp is not None else {"label": "Blood pressure", "value": None, "status": "NOT_RECORDED", "trend": "MISSING"}),
            "heart_rate": self._trend(sig.get("hr"), "hr", sim), "oxygen": self._trend(sig.get("spo2"), "spo2", sim),
            "breathing": self._trend(sig.get("resp"), "resp", sim),
            "ldl": lab("ldl", "LDL cholesterol", "mg/dL"), "total_cholesterol": lab("total_chol", "Total cholesterol", "mg/dL"),
            "non_hdl": lab("non_hdl", "Non-HDL cholesterol", "mg/dL"), "triglycerides": lab("triglycerides", "Triglycerides", "mg/dL"),
            "hba1c": lab("hba1c", "HbA1c (blood sugar)", "%"), "egfr": lab("egfr", "eGFR (kidney)"),
            "bmi": ({"label": "BMI", "value": _bmi(prof), "source": SOURCES["record"], "trend": "SINGLE_VALUE",
                     "text": "From height and weight on record; no weight history, so no trend."} if _bmi(prof) is not None
                    else {"label": "BMI", "value": None, "status": "NOT_RECORDED", "trend": "MISSING"}),
            "steps_24h": {"label": "Steps, last 24 h", "value": (sig.get("steps") or {}).get("24h"),
                          "source": SOURCES["simulated" if sim else "device"], "trend": "MISSING" if not (sig.get("steps") or {}).get("24h") else "TODAY_ONLY"},
            "sleep": self._sleep(wl),
        }
        return {"patient_id": patient_id, "status": status, "measurements": measurements,
                "risk_factors": self.risk_factors(prof, labs, sig, wl, technical), "ascvd": self._ascvd(prof, technical),
                "data_labels": SOURCES, "simulated": sim,
                "disclaimer": "A summary of the information CardioTwin holds. It is not a diagnosis of coronary artery disease or any other condition."}

    @staticmethod
    def _sleep(wl: dict) -> dict:
        v = [d["sleep_hours"] for d in wl.values() if "sleep_hours" in d]
        if not v:
            return {"label": "Sleep", "value": None, "status": "NOT_LOGGED", "trend": "MISSING", "text": "Log your sleep in Wellness to see it here."}
        return {"label": "Sleep, average", "value": round(sum(v) / len(v), 1), "unit": "h", "nights": len(v), "source": SOURCES["you"],
                "trend": "INSUFFICIENT_DATA" if len(v) < 4 else "LOGGED"}

    @staticmethod
    def _ascvd(prof: dict, technical: bool) -> dict | None:
        x = prof.get("ascvd_10y_risk")
        if not isinstance(x, (int, float)):
            return None
        band = _ascvd_band(x)
        out = {"label": "10-year heart disease and stroke estimate (ASCVD)", "band": band, "source": SOURCES["record"],
               "text": f"Your record holds an ASCVD 10-year estimate in the {band} band. It was calculated before CardioTwin, from "
                       "age, sex, blood pressure, cholesterol, diabetes and smoking. It is a population estimate, not a diagnosis."}
        if technical:
            out["value_pct"] = round(x * 100 if x <= 1 else x, 1)
        return out

    @staticmethod
    def risk_factors(prof: dict, labs: dict, sig: dict, wl: dict, technical: bool = False) -> dict:
        def f(key, label, present, detail, source):
            return {"key": key, "label": label, "status": "UNKNOWN" if present is None else ("PRESENT" if present else "NOT_PRESENT"),
                    "detail": detail, "source": source}
        bmi = _bmi(prof)
        sbp, dbp = labs.get("sbp_outpatient", {}).get("value"), labs.get("dbp_outpatient", {}).get("value")
        hb = labs.get("hba1c", {}).get("value")
        ldl = labs.get("ldl", {}).get("value")
        steps = (sig.get("steps") or {}).get("24h")
        act = [d["active_minutes"] for d in wl.values() if "active_minutes" in d]
        fv = [d["fruit_veg_servings"] for d in wl.values() if "fruit_veg_servings" in d]
        if act:
            inactive, act_detail, act_src = sum(act) / len(act) < 20, f"About {sum(act) / len(act):.0f} active minutes a day logged", SOURCES["you"]
        elif steps:
            inactive, act_detail, act_src = steps < 3000, f"{steps} steps in the last 24 h", SOURCES["device"]
        else:
            inactive, act_detail, act_src = None, "No activity data yet", None
        modifiable = [
            f("activity", "Physical inactivity", inactive, act_detail, act_src),
            f("smoking", "Smoking", None if prof.get("smoker") not in (0, 1) else prof["smoker"] == 1,
              {1: "Smoker on record", 0: "Non-smoker on record"}.get(prof.get("smoker"), "Not recorded"), SOURCES["record"] if prof.get("smoker") in (0, 1) else None),
            f("weight", "Weight", None if not isinstance(bmi, (int, float)) else bmi >= 23,
              (f"BMI {bmi:.1f}" + (" (above 23, the healthy cut-off used for Indian adults)" if bmi >= 23 else " (healthy range)")) if isinstance(bmi, (int, float)) else "Not recorded",
              SOURCES["record"] if isinstance(bmi, (int, float)) else None),
            f("diet", "Diet", None if not fv else sum(fv) / len(fv) < 5, f"About {sum(fv) / len(fv):.0f} fruit and vegetable servings a day logged" if fv else "Log fruit and vegetables in Wellness", SOURCES["you"] if fv else None),
            f("blood_pressure", "Blood pressure", None if sbp is None else (sbp >= 130 or (dbp or 0) >= 80),
              f"{sbp:.0f}/{dbp:.0f} mmHg at the clinic" if sbp is not None and dbp is not None else "Not recorded", SOURCES["record"] if sbp is not None else None),
            f("blood_glucose", "Blood sugar", None if hb is None else hb >= 5.7,
              (f"HbA1c {hb}%: " + ("diabetes range" if hb >= 6.5 else "prediabetes range" if hb >= 5.7 else "normal range")) if hb is not None else "Not recorded",
              SOURCES["record"] if hb is not None else None),
            f("lipids", "Cholesterol", None if ldl is None else ldl >= 100, f"LDL {ldl} mg/dL" if ldl is not None else "No cholesterol test on record",
              SOURCES["record"] if ldl is not None else None),
        ]
        age = prof.get("age_at_admit")
        fam = prof.get("family_history_cad")
        non_mod = [
            f("age", "Age", None if age is None else age >= (55 if prof.get("gender_male") == 1 else 65),
              f"Age {age:.0f}: heart risk rises with age" if age is not None else "Not recorded", SOURCES["record"] if age is not None else None),
            f("sex", "Sex", None if prof.get("gender_male") not in (0, 1) else prof["gender_male"] == 1,
              {1: "Men tend to develop heart disease earlier", 0: "Women's risk rises after menopause"}.get(prof.get("gender_male"), "Not recorded"),
              SOURCES["record"] if prof.get("gender_male") in (0, 1) else None),
            f("family_history", "Family history", None if fam not in (0, 1) else fam == 1,
              {1: "Early heart disease in the family", 0: "None recorded"}.get(fam, "Not recorded. Tell your doctor if a parent or sibling had early heart disease."),
              SOURCES["record"] if fam in (0, 1) else None),
        ]
        other = []
        if prof.get("hist_af") == 1:
            other.append(f("af", "Atrial fibrillation", True, "Irregular heartbeat on record", SOURCES["record"]))
        eg = labs.get("egfr", {}).get("value")
        if isinstance(eg, (int, float)) and eg < 60:
            other.append(f("kidney", "Reduced kidney function", True, f"eGFR {eg:.0f}", SOURCES["record"]))
        return {"modifiable": modifiable, "non_modifiable": non_mod, "other": other}

    # ================================================================ explain my heart health
    def summary(self, p: Principal, patient_id: str) -> dict:
        ov = self.overview(p, patient_id)
        st = self.svc.latest_status(patient_id)
        res = (st or {}).get("result") or {}
        ex = res.get("explanation") or {}
        s = ov["status"]
        if s["state"] == "OK":
            current = f"{(s.get('level') or 'Not shown').title()} on the {CHECK_NAME}. {s.get('text') or ''}".strip()
        elif s["state"] == "NO_EVALUATION_YET":
            current = s["text"]
        else:
            current = f"The {CHECK_NAME} could not give a level: {res.get('status_reason') or 'not enough recent data'}. This is not a low result."
        factors = []
        if ex.get("status") == "AVAILABLE":
            for c in (ex.get("top_contributors") or [])[:4]:
                ins = [i.get("text") for i in c.get("inputs") or [] if i.get("text")]
                factors.append({"factor": c.get("reason"), "effect": c.get("direction"), "detail": ins[0] if ins else None,
                                "modifiable": _modifiable(c)})
        trends = [m for m in ov["measurements"].values() if isinstance(m, dict) and m.get("trend") in ("IMPROVING", "WORSENING", "STALE")]
        positives = [f"{m['label']}: {m['text']}" for m in trends if m["trend"] == "IMPROVING"]
        positives += [f"{r['label']}: {r['detail']}" for r in ov["risk_factors"]["modifiable"] if r["status"] == "NOT_PRESENT"]
        watch = [f"{m['label']}: {m['text']}" for m in trends if m["trend"] in ("WORSENING", "STALE")]
        watch += [f"{r['label']}: {r['detail']}" for r in ov["risk_factors"]["modifiable"] if r["status"] == "PRESENT"]
        if s.get("trend", {}).get("trend") == "WORSENING":
            watch.insert(0, f"{CHECK_NAME.capitalize()}: {s['trend']['text']}")
        missing = [r["label"] for r in ov["risk_factors"]["modifiable"] + ov["risk_factors"]["non_modifiable"] if r["status"] == "UNKNOWN"]
        focus = []
        keys = {r["key"] for r in ov["risk_factors"]["modifiable"] if r["status"] == "PRESENT"}
        gate = self.svc.wellness.plan(patient_id)["activity"]["gate"]
        if "activity" in keys and gate["state"] == "OK":
            focus.append("Build up to a short walk most days (see Wellness for a plan that fits you).")
        if "smoking" in keys:
            focus.append("Stopping smoking is the biggest single step. Quitline 1800-11-2356.")
        if keys & {"blood_pressure", "weight", "diet"}:
            focus.append("Less salt (under a teaspoon a day) and more vegetables, dal and whole grains.")
        if "blood_glucose" in keys:
            focus.append("Keep sweets small and choose whole grains or millets.")
        if not focus:
            focus.append("Keep up regular meals, activity that suits you, and 7 to 9 hours of sleep.")
        em = s.get("emergency_state")
        if em == "TRIGGERED":
            nxt = "Follow the emergency screen now. Call 112 if you feel unwell."
        elif em == "REVIEW_REQUIRED":
            nxt = "Re-measure now (sit still for 2 minutes with the watch snug). If it stays the same or you feel unwell, contact your care team."
        elif s.get("level") == "HIGH":
            nxt = "Your care team can see this result. Contact them today, and sooner if you feel unwell."
        elif s["state"] != "OK":
            nxt = "Check the watch is snug and syncing so the heart check can run."
        else:
            nxt = "Keep wearing the watch. Discuss your risk factors at your next visit" + (f"; missing on your record: {', '.join(missing[:3])}." if missing else ".")
        return {"patient_id": patient_id, "generated_at": iso(utcnow()), "generated_by": "CardioTwin rules from your stored data (not AI)",
                "sections": {"current_risk": current, "main_factors": factors, "positive_trends": positives[:5], "areas_to_watch": watch[:6],
                             "lifestyle_focus": focus, "next_step": nxt, "missing_information": missing},
                "check_meaning": CHECK_MEANING, "simulated": ov["simulated"], "ascvd": ov["ascvd"],
                "disclaimer": "Model results and summaries are not a diagnosis. Talk to your doctor about what they mean for you."}

    # ================================================================ weekly report
    def reports(self, p: Principal, patient_id: str) -> list[dict]:
        self.require_caregiver(p, patient_id, "weekly_reports")
        last_monday = date.today() - timedelta(days=date.today().weekday() + 7)
        if not self.db.one("SELECT 1 FROM heart_reports WHERE patient_id=? AND week_start=?", (patient_id, last_monday.isoformat())):
            self.build_report(patient_id, last_monday)              # the finished week's report appears automatically
        rows = self.db.all("SELECT week_start, created_at, report_json FROM heart_reports WHERE patient_id=? ORDER BY week_start DESC LIMIT 26",
                           (patient_id,))
        return [{"week_start": r["week_start"], "created_at": r["created_at"], "headline": loads(r["report_json"])["headline"]} for r in rows]

    def report(self, p: Principal, patient_id: str, week_start: str) -> dict:
        self.require_caregiver(p, patient_id, "weekly_reports")
        r = self.db.one("SELECT report_json FROM heart_reports WHERE patient_id=? AND week_start=?", (patient_id, week_start))
        if not r:
            raise KeyError(week_start)
        return loads(r["report_json"])

    def generate(self, p: Principal, patient_id: str) -> dict:
        self.require_caregiver(p, patient_id, "weekly_reports")
        monday = date.today() - timedelta(days=date.today().weekday())
        return self.build_report(patient_id, monday)

    def build_report(self, patient_id: str, week_start: date) -> dict:
        """One week of stored data. Watch data follows the patient's clock (the SIMULATED clock for the virtual watch)."""
        end_real = week_start + timedelta(days=6)
        now = self.svc.now_for(patient_id)
        sim = self.svc.is_simulated(patient_id)
        w_end = now if sim else pd.Timestamp(datetime.combine(min(end_real, date.today()), datetime.max.time()))
        w_start = w_end - pd.Timedelta(days=7)
        rows = self.svc.readings(patient_id, since=iso(w_start.to_pydatetime().replace(tzinfo=timezone.utc)),
                                 until=iso(w_end.to_pydatetime().replace(tzinfo=timezone.utc)), types=["hr", "steps"], limit=200000)
        df = pd.DataFrame(rows) if rows else pd.DataFrame(columns=["ts", "type", "value_num"])
        days_hr, days_steps = [], []
        if len(df):
            df["day"] = pd.to_datetime(df["ts"], utc=True).dt.date.astype(str)
            hr = df[df.type == "hr"].groupby("day").value_num.mean()
            days_hr = [{"day": d, "mean_hr": round(v, 1)} for d, v in hr.items()]
            stp = df[df.type == "steps"].groupby("day").value_num.sum()
            days_steps = [{"day": d, "steps": int(v)} for d, v in stp.items()]
        hr_trend = "INSUFFICIENT_DATA"
        if len(days_hr) >= 4:
            a, b = median(x["mean_hr"] for x in days_hr[:len(days_hr) // 2]), median(x["mean_hr"] for x in days_hr[len(days_hr) // 2:])
            hr_trend = "STABLE" if abs(b - a) < 5 else ("WORSENING" if abs(b - 75) > abs(a - 75) else "IMPROVING")
        evals = self.db.all("SELECT tier, status FROM evaluations WHERE patient_id=? AND evaluated_at>=? AND evaluated_at<=?",
                            (patient_id, w_start.isoformat(), w_end.isoformat() + "~"))
        tiers = {t: sum(1 for e in evals if e["tier"] == t and e["status"] == "OK") for t in ("LOW", "MODERATE", "HIGH")}
        wl = self.svc.wellness._logged(patient_id, week_start.isoformat(), end_real.isoformat())
        sleep = [d["sleep_hours"] for d in wl.values() if "sleep_hours" in d]
        active = [d["active_minutes"] for d in wl.values() if "active_minutes" in d]
        adher = self.svc.app.adherence(patient_id, 7, end_real.isoformat())["days"]
        due = sum(a["due"] for a in adher)
        ehr = self.svc.ehr(patient_id)
        labs = {c.get("name"): c for c in ehr["clinical"] or []}
        rf = self.risk_factors(ehr["profile"] or {}, labs, {}, wl)
        positives, attention = [], []
        if hr_trend == "IMPROVING":
            positives.append("Average heart rate moved towards a healthier range over the week.")
        if hr_trend == "WORSENING":
            attention.append("Average heart rate moved away from a healthy range over the week.")
        if tiers["HIGH"]:
            attention.append(f"{tiers['HIGH']} heart checks showed a high-risk pattern this week; your care team could see them.")
        elif sum(tiers.values()):
            positives.append("No heart check this week showed a high-risk pattern.")
        if not evals:
            attention.append("No heart checks this week: wear the watch so CardioTwin can check your readings.")
        if due:
            pct = round(100 * sum(a["taken"] for a in adher) / due)
            (positives if pct >= 80 else attention).append(f"Medicines marked taken: {pct}% of doses.")
        if sleep:
            avg = sum(sleep) / len(sleep)
            (positives if avg >= 7 else attention).append(f"Average logged sleep {avg:.1f} h over {len(sleep)} nights.")
        present = [r["label"] for r in rf["modifiable"] if r["status"] == "PRESENT"]
        headline = (f"{sum(tiers.values())} heart checks" + (f", {tiers['HIGH']} high" if tiers["HIGH"] else ", none high") if evals
                    else "No heart checks this week")
        rep = {"patient_id": patient_id, "week_start": week_start.isoformat(), "week_end": end_real.isoformat(),
               "headline": headline, "created_at": iso(utcnow()), "simulated": sim,
               "watch_window": {"from": str(w_start), "to": str(w_end), "clock": "SIMULATED clock of the virtual watch" if sim else "real"},
               "risk_trend": {"checks_by_level": tiers, "check_name": CHECK_NAME},
               "heart_rate": {"daily": days_hr, "trend": hr_trend}, "activity": {"daily_steps": days_steps,
                                                                                "logged_active_minutes": sum(active) if active else None},
               "sleep": {"nights_logged": len(sleep), "average_h": round(sum(sleep) / len(sleep), 1) if sleep else None},
               "weight": {"value": None, "note": "No weight history on record."},
               "labs": [{"name": k, "value": c.get("value"), "unit": c.get("unit"), "note": "From record; unchanged this week unless your doctor updated it"}
                        for k, c in labs.items()],
               "risk_factors_present": present, "positive_changes": positives, "needs_attention": attention,
               "suggestions": (["Aim for a short walk most days, as your Wellness plan allows."] if "Physical inactivity" in present else []) +
                              (["Cut down salt: skip pickles, papad and namkeen this week."] if "Blood pressure" in present else []) +
                              ["Keep logging water, sleep and activity in Wellness so next week's report is fuller."],
               "disclaimer": "An automatic summary of stored data, not a medical report or a diagnosis."}
        self.db.execute("INSERT OR REPLACE INTO heart_reports VALUES (?,?,?,?)", (patient_id, week_start.isoformat(), rep["created_at"], dumps(rep)))
        self._alert(patient_id, f"report:{week_start.isoformat()}", "info", "report", "Your weekly heart report is ready",
                    f"Week of {week_start.strftime('%d %b')}: {headline}.", sim)
        return rep

    # ================================================================ health alerts (into the existing inbox)
    def _alert(self, patient_id: str, key: str, level: str, rule: str, title: str, body: str, simulated: bool) -> bool:
        full = f"{patient_id}|{key}"
        if self.db.one("SELECT 1 FROM heart_alerts WHERE alert_key=?", (full,)):
            return False
        nid = str(uuid.uuid4())
        self.db.execute("INSERT INTO notifications VALUES (?,?,?,?,?,?,?,NULL)", (nid, patient_id, f"heart_{level}", title, body, None, iso(utcnow())))
        self.db.execute("INSERT INTO heart_alerts VALUES (?,?,?,?,?,?,?)", (full, patient_id, level, rule, nid, int(simulated), iso(utcnow())))
        return True

    def scan(self, patient_id: str, status: dict) -> list[str]:
        """Run after each evaluation, on what it already computed. Deduplicated per rule and period."""
        out = []
        sig = (status.get("features") or {}).get("signals") or {}
        sim = bool(status.get("simulated"))
        day = str(self.svc.now_for(patient_id).date())
        hr_last = (sig.get("hr") or {}).get("last")
        if hr_last:
            if hr_last["minutes_old"] > MISSED_H * 60:
                if self._alert(patient_id, f"missed:{day}", "attention", "missed_monitoring", "No watch readings for a day",
                               "CardioTwin has had no heart-rate readings for over 24 hours. Wear and sync the watch so it can keep checking.", sim):
                    out.append("missed_monitoring")
            elif hr_last["minutes_old"] > STALE_MIN:
                if self._alert(patient_id, f"stale:{day}:{int(hr_last['minutes_old'] // 180)}", "attention", "stale_data", "Watch data is out of date",
                               f"The last heart-rate reading is {int(hr_last['minutes_old'])} minutes old. Check the watch is on and syncing.", sim):
                    out.append("stale_data")
        for name, label in (("hr", "heart rate"), ("spo2", "oxygen"), ("resp", "breathing rate")):
            s = sig.get(name) or {}
            z = (s.get("baseline") or {}).get("robust_z_1h")
            if z is not None and abs(z) >= 3 and (s.get("baseline") or {}).get("status") == "AVAILABLE":
                if self._alert(patient_id, f"change:{name}:{day}", "attention", "significant_change", f"Your {label} is different from usual",
                               f"Your {label} over the last hour is well outside your usual range. If you feel unwell, contact your care team.", sim):
                    out.append(f"change_{name}")
        rows = self.svc.readings(patient_id, since=iso((self.svc.now_for(patient_id) - pd.Timedelta(hours=6)).tz_localize("UTC").to_pydatetime()),
                                 types=["hr", "spo2", "resp"], limit=5000)
        if rows:
            df = pd.DataFrame(rows)
            df["hour"] = pd.to_datetime(df["ts"], utc=True).dt.floor("h")
            for name, bad in ABNORMAL.items():
                hourly = df[df.type == name].groupby("hour").value_num.mean()
                n_bad = int(sum(1 for v in hourly if bad(v)))
                if n_bad >= 3:
                    lvl = "high" if n_bad >= 5 else "attention"
                    if self._alert(patient_id, f"repeat:{name}:{day}", lvl, "repeated_abnormal", "Repeated unusual readings",
                                   f"{n_bad} of the last 6 hours had unusual {TREND_TARGET[name][0].lower()} readings. "
                                   "Your care team can see your readings; contact them if you feel unwell.", sim):
                        out.append(f"repeated_{name}")
        prev = self.db.all("SELECT tier FROM evaluations WHERE patient_id=? AND status='OK' ORDER BY id DESC LIMIT 2", (patient_id,))
        if len(prev) == 2 and prev[0]["tier"] != prev[1]["tier"] and (prev[0]["tier"], prev[1]["tier"]) in (("MODERATE", "LOW"), ("HIGH", "LOW"), ("HIGH", "MODERATE")):
            hour = str(self.svc.now_for(patient_id).floor("h"))
            if self._alert(patient_id, f"tier:{hour}", "info", "risk_change", "Your heart check level went up",
                           f"The {CHECK_NAME} changed from {prev[1]['tier'].title()} to {prev[0]['tier'].title()}. Open Heart health to see why.", sim):
                out.append("risk_change")
        return out

    def preventive_reminders(self, patient_id: str) -> list[str]:
        """Light-touch reminders from the record, at most once per quarter each."""
        ehr = self.svc.ehr(patient_id)
        labs = {c.get("name"): c for c in ehr["clinical"] or []}
        prof = ehr["profile"] or {}
        q = f"{date.today().year}Q{(date.today().month - 1) // 3 + 1}"
        out = []
        if not labs.get("ldl") and self._alert(patient_id, f"prev:lipids:{q}", "info", "preventive", "Ask about a cholesterol test",
                                               "There is no cholesterol result on your record. Ask your doctor whether you need a lipid test.", False):
            out.append("lipids")
        hb = labs.get("hba1c", {}).get("value")
        if isinstance(hb, (int, float)) and hb >= 6.5 and self._alert(patient_id, f"prev:hba1c:{q}", "info", "preventive", "Blood sugar check-up",
                                                                      "Your HbA1c is in the diabetes range. Regular sugar check-ups help protect your heart.", False):
            out.append("hba1c")
        if prof.get("smoker") == 1 and self._alert(patient_id, f"prev:smoking:{q}", "info", "preventive", "Support to stop smoking",
                                                   "Stopping smoking is the biggest single step for your heart. Quitline: 1800-11-2356.", False):
            out.append("smoking")
        return out


def _modifiable(c: dict) -> bool | None:
    feats = " ".join(i.get("feature", "") for i in c.get("inputs") or []).lower()
    if any(k in feats for k in ("smok", "bmi", "weight", "steps", "activity", "sleep")):
        return True
    if any(k in feats for k in ("age", "gender", "sex", "hist_", "prior_admissions", "days_since")):
        return False
    return None
