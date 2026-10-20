# Backend safety layer for the reading types the model does not use. Runs on every batch of new readings, before and
# independently of the risk score, next to the risk engine's own emergency rules (NEWS2 red bands and symptoms).
#   fall        -> "Are you OK?" check on the watch; no answer within the window, or "I need help" -> emergency
#   rhythm AF   -> confirmed irregular rhythm (>= 2 AF readings in 30 min) -> clinician review alert
#   ecg_qtc     -> QTc >= 500 ms -> clinician review alert
#   dose_taken  -> today's total over the clinic's limit (twin_rules.dose_check) -> tell-the-doctor alert
# DRAFT rules: thresholds are hackathon defaults and need clinical review. Nothing here contacts emergency services:
# the emergency modal opens the phone dialler and the user presses Call; dispatch_status is always NOT_DISPATCHED.
from __future__ import annotations

from datetime import timedelta
from typing import Any

import pandas as pd

from .alerts import AlertStore
from .readings import iso, utcnow

RULES_VERSION = "BACKEND-SAFETY-1.0-draft"
DEFAULTS = {"fall_response_seconds": 30, "af_confirm_readings": 2, "af_window_minutes": 30, "qtc_review_ms": 500}


class SafetyLayer:
    def __init__(self, store: AlertStore, engine_cfg: dict, settings: dict | None = None):
        self.store, self.cfg = store, engine_cfg
        self.s = {**DEFAULTS, **(settings or {})}

    # ---------------------------------------------------------------- entry point
    def on_readings(self, patient_id: str, new: list[dict], recent: list[dict], ehr: dict) -> list[dict]:
        """new: just-stored readings; recent: stored readings of the last few hours (including new). Returns raised or
        changed alerts."""
        out: list[dict] = []
        for r in new:
            if r["type"] == "fall" and (r["value_num"] or 0) >= 1:
                out.append(self._fall(patient_id, r))
            elif r["type"] == "ecg_qtc" and r["value_num"] >= self.s["qtc_review_ms"]:
                a = self._qtc(patient_id, r)
                if a:
                    out.append(a)
            elif r["type"] == "dose_taken":
                a = self._dose(patient_id, r, recent, ehr)
                if a:
                    out.append(a)
        if any(r["type"] == "rhythm" for r in new):
            a = self._af(patient_id, recent, ehr)
            if a:
                out.append(a)
        return out

    # ---------------------------------------------------------------- fall
    def _fall(self, pid: str, r: dict) -> dict:
        open_ = self.store.open_of_kind(pid, "FALL")
        if open_:
            return self.store.transition(open_[0]["alert_id"], None, "FALL_REPEATED", "watch", reading_at=r["ts"])
        secs = int(self.s["fall_response_seconds"])
        payload = {"rules_version": RULES_VERSION, "reading_at": r["ts"], "impact": (r.get("meta") or {}).get("impact_g"),
                   "response_window_seconds": secs,
                   "watch": {"type": "FALL_CHECK", "title": "Fall detected", "text": "Are you OK?", "haptic": "long repeating",
                             "countdown_seconds": secs, "actions": [{"id": "im_ok", "label": "I'm OK"},
                                                                     {"id": "need_help", "label": "I need help"}]},
                   "phone": {"type": "FALL_CHECK", "text": "A fall was detected. If there is no answer on the watch within "
                                                           f"{secs} seconds, an emergency alert is shown to you and your care team."},
                   "dispatch_status": "NOT_DISPATCHED"}
        return self.store.create(pid, "FALL", "high", "Fall detected", "Fall detected by the watch. Waiting for the wearer to answer.",
                                 payload, state="AWAITING_RESPONSE", rule_id="SAF-FALL-01",
                                 deadline_at=iso(utcnow() + timedelta(seconds=secs)), simulated=_sim(r))

    def fall_response(self, alert_id: str, answer: str, actor: str) -> dict:
        a = self.store.get(alert_id)
        if not a or a["kind"] != "FALL":
            raise KeyError(alert_id)
        if a["state"] != "AWAITING_RESPONSE":
            raise ValueError("This fall check is no longer waiting for an answer.")
        if answer == "im_ok":
            return self.store.transition(alert_id, "RESOLVED", "WEARER_CONFIRMED_OK", actor,
                                         note="The wearer answered 'I'm OK' on the watch within the window.")
        if answer == "need_help":
            return self.escalate_fall(alert_id, "The wearer asked for help after a fall.", actor)
        raise ValueError("answer must be im_ok or need_help")

    def escalate_fall(self, alert_id: str, reason: str, actor: str = "system") -> dict:
        a = self.store.get(alert_id)
        modal = emergency_modal(self.cfg, "SAF-FALL-02", reason, [{"type": "reading", "signal": "fall", "at": a["payload"]["reading_at"]}])
        payload = {**a["payload"], "emergency": modal}
        self.store.update_payload(alert_id, severity="critical", title="Emergency warning: fall", message=reason,
                                  rule_id="SAF-FALL-02", payload=payload)
        return self.store.transition(alert_id, "ESCALATED", "EMERGENCY_SHOWN", actor, reason=reason, dispatch_status="NOT_DISPATCHED")

    def overdue(self) -> list[dict]:
        return [self.escalate_fall(a["alert_id"], "No answer on the watch after a detected fall.")
                for a in self.store.overdue_fall_checks(iso(utcnow()))]

    # ---------------------------------------------------------------- rhythm
    def _af(self, pid: str, recent: list[dict], ehr: dict) -> dict | None:
        rh = [r for r in recent if r["type"] == "rhythm"]
        if not rh:
            return None
        last = max(rh, key=lambda r: r["ts"])
        win_start = pd.Timestamp(last["ts"]) - pd.Timedelta(minutes=self.s["af_window_minutes"])
        af = [r for r in rh if r["value_text"] == "af_flutter" and pd.Timestamp(r["ts"]) >= win_start]
        if len(af) < self.s["af_confirm_readings"] or self.store.open_of_kind(pid, "RHYTHM_AF"):
            return None
        known = bool((ehr.get("profile") or {}).get("hist_af"))
        msg = ("The watch rhythm check reported an irregular rhythm consistent with atrial fibrillation "
               f"({len(af)} readings in {self.s['af_window_minutes']} min). "
               + ("AF is already on record for this patient; the care team can check rate and symptoms."
                  if known else "No AF is on record: record a 30-second ECG on the watch and contact the care team."))
        payload = {"rules_version": RULES_VERSION, "af_readings": len(af), "first_at": min(r["ts"] for r in af),
                   "last_at": max(r["ts"] for r in af), "known_af": known,
                   "watch": {"type": "WATCH_NOTICE", "title": "Irregular rhythm", "text": "Record ECG, see phone", "haptic": "double"},
                   "not_a_diagnosis": "A watch rhythm check is a screening signal, not a diagnosis of atrial fibrillation."}
        return self.store.create(pid, "RHYTHM_AF", "moderate" if known else "high", "Possible atrial fibrillation", msg,
                                 payload, rule_id="SAF-AF-01", simulated=any(_sim(r) for r in af))

    # ---------------------------------------------------------------- QTc
    def _qtc(self, pid: str, r: dict) -> dict | None:
        if self.store.open_of_kind(pid, "QTC_LONG"):
            return None
        msg = (f"Watch ECG QTc {r['value_num']:.0f} ms (>= {self.s['qtc_review_ms']} ms). A long QTc raises the risk of "
               "dangerous rhythms and needs clinician review, including a check of QT-prolonging medicines.")
        return self.store.create(pid, "QTC_LONG", "high", "Long QTc on watch ECG", msg,
                                 {"rules_version": RULES_VERSION, "qtc_ms": r["value_num"], "at": r["ts"]},
                                 rule_id="SAF-QTC-01", simulated=_sim(r))

    # ---------------------------------------------------------------- dose
    def _dose(self, pid: str, r: dict, recent: list[dict], ehr: dict) -> dict | None:
        drug = (r.get("meta") or {}).get("drug")
        limits = ehr.get("dose_limits") or {}
        if not drug or drug not in limits:
            return None
        import twin_rules
        day = r["ts"][:10]
        total = sum(x["value_num"] for x in recent if x["type"] == "dose_taken" and x["ts"][:10] == day
                    and (x.get("meta") or {}).get("drug") == drug)
        chk = twin_rules.dose_check(drug, total, limits, age=(ehr.get("profile") or {}).get("age_at_admit"))
        if not str(chk.get("status", "")).startswith("over limit"):
            return None
        return self.store.create(pid, "DOSE_OVER_LIMIT", "moderate", f"{drug}: over today's limit",
                                 f"{drug} taken today: {total:g} mg ({chk['pct_of_limit']}% of the clinic's daily limit). Tell the doctor. "
                                 "The app never suggests a dose.", {"rules_version": RULES_VERSION, "check": chk, "drug": drug},
                                 rule_id="SAF-DOSE-01", simulated=_sim(r))


def emergency_modal(cfg: dict, rule_id: str, reason: str, evidence: list[dict]) -> dict[str, Any]:
    """The risk engine's own emergency modal (same layout, call action and no-dispatch guarantees)."""
    from risk_engine.emergency_rules import modal
    from risk_engine.schemas import EmergencyBlock, EmergencyState
    ca = cfg["emergency"]["call_action"]
    block = EmergencyBlock(state=EmergencyState.TRIGGERED, rule_id=rule_id, reason=reason, evidence=evidence,
                           guidance=list(cfg["emergency"]["guidance"]))
    return modal(block, pd.Timestamp(utcnow()).tz_localize(None), ca)


def _sim(r: dict) -> bool:
    return r.get("source") == "simulator" or bool((r.get("meta") or {}).get("simulated"))
