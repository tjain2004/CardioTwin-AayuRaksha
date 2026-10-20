# Emergency escalation rules, evaluated on validated readings and reported symptoms only, independently of the ML
# score, SHAP or any other model step. They are DRAFT rules built from NEWS2 single-parameter red bands and the
# project's symptom rule; the way they are combined is a hackathon default and needs clinical review.
from __future__ import annotations

from typing import Any

import pandas as pd

import twin_rules

from .input_validator import WATCH_SIGNALS
from .schemas import EmergencyBlock, EmergencyState

RULES_VERSION = "EMG-RULES-1.0-draft"
UNITS = {"hr": "bpm", "spo2": "%", "rr": "breaths/min"}
NAMES = {"hr": "Heart rate", "spo2": "Oxygen saturation", "rr": "Breathing rate"}
SYMPTOM_MAX_AGE_MIN = 60

RULES = {
    "EMG-SYM-01": ("TRIGGERED", "Reported symptoms match the urgent pattern of the project's symptom rule (chest pain with "
                                "pain spreading to arm/jaw/back, cold sweat or breathlessness, or a symptom score of 6 or more)."),
    "EMG-SYM-02": ("REVIEW_REQUIRED", "Reported symptoms score 3 or more on the project's symptom rule."),
    "EMG-VIT-02": ("TRIGGERED", "Two or more watch signals are confirmed in a NEWS2 single-parameter red band (score 3)."),
    "EMG-VIT-01": ("REVIEW_REQUIRED", "One watch signal is confirmed in a NEWS2 single-parameter red band (score 3); "
                                      "NEWS2 asks for an urgent clinical review."),
    "EMG-VIT-00": ("REVIEW_REQUIRED", "A single, not yet confirmed reading is in a NEWS2 red band: re-measure now."),
}


def _in_band(sig: str, v: float, bands: dict) -> str | None:
    b = bands[sig]
    if "low" in b and v <= b["low"]:
        return "low"
    if "high" in b and v >= b["high"]:
        return "high"
    return None


def _band_text(sig, side, bands):
    b = bands[sig]
    return f"<= {b['low']}" if side == "low" else f">= {b['high']}"


def evaluate(readings: pd.DataFrame, symptoms: dict | None, now: pd.Timestamp, cfg: dict) -> EmergencyBlock:
    e = cfg["emergency"]
    bands, win = e["vitals_bands"], pd.Timedelta(minutes=e["confirm_window_minutes"])
    max_age = pd.Timedelta(minutes=e["max_reading_age_minutes"])
    hits: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []
    confirmed, unconfirmed = [], []
    assessable_vitals = False

    # ---- vital signs
    if readings is not None and len(readings):
        for sig in WATCH_SIGNALS:
            d = readings[(readings.signal == sig) & (readings.timestamp >= now - max_age)].sort_values("timestamp")
            if not len(d):
                continue
            assessable_vitals = True
            last = d.iloc[-1]
            side = _in_band(sig, last.value, bands)
            if side is None:
                continue
            same = d[(d.timestamp >= last.timestamp - win) & d.value.apply(lambda v: _in_band(sig, v, bands) == side)]
            obs = [{"signal": sig, "value": round(float(r.value), 1), "unit": UNITS[sig], "timestamp": str(r.timestamp),
                    "source": r.source or "not stated", "band": f"{_band_text(sig, side, bands)} (NEWS2 score 3)"}
                   for r in same.itertuples()]
            (confirmed if len(same) >= e["confirm_min_readings"] else unconfirmed).append(sig)
            evidence += obs
    if len(confirmed) >= 2:
        hits.append({"rule_id": "EMG-VIT-02", "signals": confirmed})
    elif len(confirmed) == 1:
        hits.append({"rule_id": "EMG-VIT-01", "signals": confirmed})
    if unconfirmed:
        hits.append({"rule_id": "EMG-VIT-00", "signals": unconfirmed})

    # ---- reported symptoms (phone symptom check)
    assessable_symptoms = False
    if symptoms:
        ts = symptoms.get("timestamp")
        t = None
        try:
            t = pd.Timestamp(ts)
            t = (t.tz_convert("UTC").tz_localize(None) if t.tzinfo else t) if ts is not None else None
        except Exception:
            t = None
        if t is not None and pd.Timedelta(0) <= now - t <= pd.Timedelta(minutes=SYMPTOM_MAX_AGE_MIN):
            assessable_symptoms = True
            answers = {k: bool(v) for k, v in symptoms.items() if k in twin_rules.SYMPTOMS}
            sc = twin_rules.symptom_check(answers)
            reported = [k for k, v in answers.items() if v]
            if sc["tier"] == "red":
                hits.append({"rule_id": "EMG-SYM-01", "symptoms": reported, "score": sc["score"]})
            elif sc["tier"] == "amber":
                hits.append({"rule_id": "EMG-SYM-02", "symptoms": reported, "score": sc["score"]})
            if reported:
                evidence.append({"signal": "reported_symptoms", "value": ", ".join(reported), "timestamp": str(t),
                                 "source": "phone symptom check (patient-reported)", "score": sc["score"]})

    out = EmergencyBlock(rules_version=RULES_VERSION, review_status=e["review_status"], evidence=evidence)
    states = [RULES[h["rule_id"]][0] for h in hits]
    for h in hits:
        h["meaning"] = RULES[h["rule_id"]][1]
        h["state"] = RULES[h["rule_id"]][0]
    out.rules_triggered = hits
    if "TRIGGERED" in states:
        out.state = EmergencyState.TRIGGERED
    elif "REVIEW_REQUIRED" in states:
        out.state = EmergencyState.REVIEW_REQUIRED
    elif assessable_vitals or assessable_symptoms:
        out.state = EmergencyState.NOT_TRIGGERED
    else:
        out.state = EmergencyState.NOT_ASSESSABLE
    order = ["EMG-SYM-01", "EMG-VIT-02", "EMG-VIT-01", "EMG-SYM-02", "EMG-VIT-00"]
    if hits:
        top = sorted(hits, key=lambda h: order.index(h["rule_id"]))[0]
        out.rule_id = top["rule_id"]
        parts = []
        for h in sorted(hits, key=lambda h: order.index(h["rule_id"])):
            if "signals" in h:
                obs = [x for x in evidence if x["signal"] in h["signals"]]
                latest = {}
                for x in obs:
                    latest[x["signal"]] = x
                parts.append("; ".join(f"{NAMES[s]} {x['value']} {x['unit']} at {x['timestamp']} ({x['band']})"
                                       for s, x in latest.items()) + f" [{h['rule_id']}]")
            else:
                parts.append(f"Reported: {', '.join(h['symptoms']).replace('_', ' ')} (score {h['score']}) [{h['rule_id']}]")
        out.reason = " | ".join(parts)
    elif out.state == EmergencyState.NOT_ASSESSABLE:
        out.reason = "No recent vital signs or symptom report to check against the emergency rules."
    else:
        out.reason = "No emergency rule is met by the recent readings."

    # Only a TRIGGERED state is "critical" and carries the emergency-number call action. REVIEW_REQUIRED (one red band,
    # an unconfirmed reading or an amber symptom score) is urgent but not critical: the patient is asked to re-measure
    # and contact the care team, and gets no emergency-number prompt.
    ca = e["call_action"]
    out.level = LEVELS[out.state]
    if out.state == EmergencyState.TRIGGERED:
        out.call_action_available = bool(ca["jurisdiction_verified"])
        out.call_action = call_action(ca)
        out.guidance = list(e["guidance"])
        out.modal = modal(out, now, ca)
    elif out.state == EmergencyState.REVIEW_REQUIRED:
        out.care_action = care_action()
        out.guidance = [
            "Re-measure now: sit still for 2 minutes with the watch snug on your wrist.",
            "If the reading stays the same or you feel unwell, contact your doctor or care team now.",
            "If you get chest pain, trouble breathing or feel faint, report it in How I feel straight away."]
    return out


# Emergency state -> the classification the apps show. Only "critical" gets the emergency-number call prompt.
LEVELS = {EmergencyState.TRIGGERED: "critical", EmergencyState.REVIEW_REQUIRED: "review",
          EmergencyState.NOT_TRIGGERED: None, EmergencyState.NOT_ASSESSABLE: None}


def care_action() -> dict[str, Any]:
    """The softer action for patients who are not critical: get in touch with the doctor or care team."""
    return {"type": "contact_care_team", "label": "Contact your doctor",
            "text": "Get in touch with your doctor or care team today. Your care team can already see these readings."}


def call_action(ca: dict) -> dict[str, Any]:
    verified = bool(ca["jurisdiction_verified"])
    return {"type": ca["method"], "number": ca["number"] if verified else None,
            "uri": f"tel:{ca['number']}" if verified else None, "country": ca["country"],
            "label": f"Call {ca['number']}" if verified else "Call your local emergency number",
            "requires_user_confirmation": True, "auto_dial": False,
            "confirm_text": (f"This opens your phone's dialler with {ca['number']} filled in. You press Call yourself. "
                             "CardioTwin does not contact emergency services and cannot send an ambulance."),
            "dispatch_status": "NOT_DISPATCHED",
            "dispatch_note": "Opening the dialler is not an ambulance dispatch. Automatic dispatch would need explicit consent, "
                             "an authorised emergency-service integration and a clinical, legal and operational safety review."}


def modal(block: EmergencyBlock, now: pd.Timestamp, ca: dict) -> dict[str, Any]:
    """Payload the phone renders as a full-screen red alert dialog, and a short version for the watch."""
    verified = bool(ca["jurisdiction_verified"])
    return {
        "type": "EMERGENCY_MODAL", "severity": "critical", "rule_id": block.rule_id,
        "title": "Emergency warning", "icon": "warning_triangle", "icon_glyph": "⚠",
        "headline": "Your readings or symptoms meet an emergency rule.",
        "reason": block.reason, "triggered_at": str(now),
        "observations": block.evidence, "instructions": block.guidance,
        "colors": {"background": "#B00020", "text": "#FFFFFF"},
        "not_colour_only": "Title text, the warning icon and the rule reason are always shown, so the alert does not rely on red.",
        "actions": [
            {"id": "call_emergency", **call_action(ca), "primary": True},
            {"id": "acknowledge", "label": "I have seen this", "type": "acknowledge",
             "note": "Acknowledging records that the alert was seen. It does not mean the condition has improved."},
            {"id": "contact_care_team", "label": "Message my care team", "type": "notify"},
        ],
        "accessibility": {"role": "alertdialog", "aria_live": "assertive", "aria_modal": True,
                          "aria_label": "Emergency warning. " + (block.reason or ""),
                          "initial_focus": "call_emergency", "keyboard": {"Enter": "call_emergency", "Tab": "next action"},
                          "min_touch_target_dp": 48, "haptics": "long repeating", "sound": "alarm, respects Do Not Disturb override setting",
                          "text_scale": "follows system font size"},
        "dismissible": False,
        "persistent_until": "acknowledged on the phone; the emergency state stays open until a clinician resolves it",
        "dispatch_status": "NOT_DISPATCHED",
        "watch": {"type": "EMERGENCY_WATCH", "title": "EMERGENCY", "icon_glyph": "⚠",
                  "text": "Check your phone now", "haptics": "long repeating",
                  "action": {"label": f"Call {ca['number']}" if verified else "Open phone", "type": "open_phone_dialer" if verified else "open_phone"}},
    }
