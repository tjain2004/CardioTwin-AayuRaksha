# What each screen receives: phone patient mode, phone doctor mode and the Wear OS watch. The apps render these
# payloads; they never compute a score, a tier or an explanation themselves, so a backend failure can never be shown
# as a made-up number.
from __future__ import annotations

from typing import Any

from .emergency_rules import care_action
from .schemas import EmergencyState, RiskResult, Status

PATIENT_TEXT = {
    "HIGH": "Your readings match a pattern the model links to higher risk. Your care team has been told. "
            "If you feel unwell, contact them now.",
    "MODERATE": "Some readings are outside your usual pattern. Your care team can see this.",
    "LOW": "No high-risk pattern in your latest readings. This is not a guarantee: contact your doctor if you feel unwell.",
}


def phone_patient(r: RiskResult) -> dict[str, Any]:
    em = r.emergency
    if em.state == EmergencyState.TRIGGERED:
        screen, msg = "EMERGENCY", em.modal["headline"]
    elif r.status == Status.INSUFFICIENT_DATA:
        screen, msg = "CANNOT_UPDATE", ("We can't update your heart check right now: " + (r.status_reason or "") +
                                        " Check the watch is snug and synced, then tap Re-measure.")
    elif r.status == Status.MODEL_ERROR:
        screen, msg = "UNAVAILABLE", "Your risk check is unavailable because of a technical problem. This is not a low result."
    elif r.risk.tier is None:
        screen, msg = "SCORE_WITHOUT_TIER", "Your readings were checked, but no risk level can be shown right now."
    else:
        screen, msg = r.risk.tier.value, PATIENT_TEXT[r.risk.tier.value]
    fr = r.data_quality.freshness_by_signal
    return {
        "screen": screen, "message": msg,
        "emergency_modal": em.modal if em.state == EmergencyState.TRIGGERED else None,
        "review_banner": ({"text": "Please re-measure now. " + (em.guidance[0] if em.guidance else ""), "rule_id": em.rule_id,
                           "care_action": em.care_action} if em.state == EmergencyState.REVIEW_REQUIRED else None),
        # The emergency-number call prompt is only for a critical (TRIGGERED) state. Everyone else whose readings need
        # attention (HIGH or MODERATE tier, or an urgent review) gets the softer "Contact your doctor" action.
        "emergency_level": em.level,
        "call_action": em.call_action if em.state == EmergencyState.TRIGGERED else None,
        "care_action": care_action_for(r),
        "tier": r.risk.tier.value if r.status == Status.OK and r.risk.tier else None,
        "freshness_badges": {s: {"status": f.status.value, "minutes_old": f.age_minutes} for s, f in fr.items()},
        "last_sync": r.data_quality.last_wearable_sync,
        "actions": (["re_measure", "check_watch_fit"] if r.status != Status.OK else ["view_details"]),
        "shows_score_number": False,
        "evaluated_at": r.evaluated_at.isoformat(), "demo_data": r.is_demo_data,
    }


def care_action_for(r: RiskResult) -> dict[str, Any] | None:
    em = r.emergency
    if em.state == EmergencyState.TRIGGERED:
        return None
    if em.state == EmergencyState.REVIEW_REQUIRED or (r.status == Status.OK and r.risk.tier and r.risk.tier.value in ("HIGH", "MODERATE")):
        return em.care_action or care_action()
    return None


def phone_doctor(r: RiskResult) -> dict[str, Any]:
    ok = r.status == Status.OK
    return {
        "header": {"status": r.status.value, "reason": r.status_reason, "tier": r.risk.tier.value if ok and r.risk.tier else None,
                   "score": (r.risk.calibrated_probability or r.risk.raw_output) if ok else None, "score_scale": r.risk.score_scale,
                   "target": r.model.target, "horizon": r.model.horizon, "evaluated_at": r.evaluated_at.isoformat(),
                   "model_version": r.model.version, "policy_version": r.risk.policy_version,
                   "provisional_policy": r.risk.provisional_policy, "calibration": r.model.calibration_status,
                   "demo_data": r.is_demo_data},
        "emergency": r.emergency.model_dump(mode="json"),
        "sar": {"one_line": r.sar.one_line, "situation": r.sar.situation, "assessment": r.sar.assessment,
                "recommendation": r.sar.recommendation, "limitations": r.sar.limitations},
        "top_reasons": r.explanation.top_contributors, "explanation_status": r.explanation.status.value,
        "waterfall": r.explanation.waterfall,
        "technical_details_collapsed": {"top_features": r.explanation.top_features, "output_space": r.explanation.output_space,
                                        "baseline_output": r.explanation.baseline_output, "uncertainty": r.risk.uncertainty,
                                        "imputed": r.data_quality.imputed_fields, "out_of_distribution": r.data_quality.out_of_distribution},
        "baseline": {"status": r.baseline.status.value, "deviations": r.baseline.deviations, "trends": r.baseline.trends,
                     "corroboration": r.baseline.corroboration},
        "trajectory": r.risk.trajectory, "trajectory_summary": r.risk.trajectory_summary,
        "data_quality": r.data_quality.model_dump(mode="json", include={"status", "reason", "coverage_pct", "freshness_by_signal",
                                                                         "last_wearable_sync", "missing_fields", "stale_fields",
                                                                         "invalid_fields", "warnings", "flags_for_remeasurement"}),
        "alert": r.alert, "policy_breakdown": r.policy_breakdown, "next_actions": r.next_actions,
        "actions": ["acknowledge", "record_review", "escalate", "resolve", "what_if", "download_report"],
        "audit_id": r.audit.audit_id,
    }


def watch(r: RiskResult) -> dict[str, Any]:
    em = r.emergency
    if em.state == EmergencyState.TRIGGERED:
        return em.modal["watch"] | {"evaluated_at": r.evaluated_at.isoformat()}
    hr = r.data_quality.freshness_by_signal.get("hr")
    if r.status != Status.OK:
        face = {"title": "No update", "text": "Check watch fit", "icon_glyph": "⟳", "tier": None}
    elif r.risk.tier is None:
        face = {"title": "Checked", "text": "See phone", "icon_glyph": "ⓘ", "tier": None}
    else:
        face = {"title": {"HIGH": "High risk", "MODERATE": "Check phone", "LOW": "No alert"}[r.risk.tier.value],
                "text": {"HIGH": "See phone now", "MODERATE": "Readings changed", "LOW": "Keep wearing"}[r.risk.tier.value],
                "icon_glyph": {"HIGH": "▲", "MODERATE": "●", "LOW": "✓"}[r.risk.tier.value], "tier": r.risk.tier.value}
    if em.state == EmergencyState.REVIEW_REQUIRED:
        face = {"title": "Re-measure", "text": "Sit still 2 min", "icon_glyph": "!", "tier": face.get("tier")}
    return {"type": "WATCH_FACE", **face, "haptic": "double" if face.get("tier") == "HIGH" or em.state == EmergencyState.REVIEW_REQUIRED else None,
            "last_sync_minutes": r.data_quality.minutes_since_last_sync,
            "stale": r.status == Status.INSUFFICIENT_DATA, "hr_fresh": bool(hr and hr.status.value == "FRESH"),
            "evaluated_at": r.evaluated_at.isoformat()}


def all_views(r: RiskResult) -> dict[str, Any]:
    return {"phone_patient": phone_patient(r), "phone_doctor": phone_doctor(r), "watch": watch(r)}
