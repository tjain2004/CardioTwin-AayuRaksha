# Doctor-facing SAR (Situation - Assessment - Recommendation) built from a deterministic template. Every sentence
# carries the evidence it rests on (reading, time window, feature, model or policy version). No language model is
# used, so nothing can be invented; a guard still rejects diagnosis, cause or medication wording as a second check.
from __future__ import annotations

import re
from typing import Any

from .schemas import EmergencyState, SarBlock, SarSentence

TEMPLATE_VERSION = "SAR-ENGINE-1.0 (deterministic template)"
REC_POLICY_VERSION = "REC-POLICY-1.0-draft (anchored on the NEWS2 escalation protocol, RCP 2017; needs clinician approval)"
NAMES = {"hr": "Heart rate", "spo2": "SpO2", "rr": "Breathing rate", "rhythm": "Rhythm"}
UNITS = {"hr": "bpm", "spo2": "%", "rr": "/min"}

FORBIDDEN = [
    (r"\bdiagnos(is|ed|e)\b", "diagnosis wording"),
    (r"\byou have (a|an)\b", "diagnosis wording"),
    (r"\b(heart attack|myocardial infarction|sepsis|septic|stroke|heart failure|arrhythmia detected)\b", "disease claim"),
    (r"\b(start|stop|increase|decrease|reduce|double|take|skip|adjust|change)\b[^.]{0,40}\b(dose|doses|medication|medicine|tablet|drug|aspirin|insulin|nitroglycerin)\b", "medication advice"),
    (r"\bcaused by\b|\bis causing\b|\bbecause of the\b", "causal claim"),
]

POLICY = {
    "HIGH": ["Prompt clinical review is appropriate, with a full set of observations (BP, temperature, consciousness, SpO2, breathing rate, heart rate).",
             "Score NEWS2 at the review: 5-6, or any single parameter scoring 3, calls for an urgent response; 7 or more calls for an emergency response, per local escalation policy."],
    "MODERATE": ["Increase the frequency of checks and repeat a full set of observations with a NEWS2 score.",
                 "Escalate per local policy if NEWS2 is 5 or more or the score keeps rising."],
    "LOW": ["Continue routine monitoring at the usual frequency."],
}


def window_of(f: str) -> str:
    if any(t in f for t in ("mean6h", "max6h", "min6h", "delta6h")):
        return "last 6 h"
    if "_24h" in f:
        return "last 24 h"
    if f.endswith("_lag1"):
        return "previous hour"
    if f.endswith("_vs_baseline"):
        return "since monitoring started (expanding median)"
    if f.split("_")[0] in ("hr", "spo2", "rr") or f.startswith("rhythm"):
        return "latest hour"
    return "profile / EHR value"


def _s(text: str, *evidence: dict) -> SarSentence:
    return SarSentence(text=text, evidence=[e for e in evidence if e])


def guard(sentences: dict[str, list[SarSentence]]) -> list[dict[str, str]]:
    bad = []
    for part, ss in sentences.items():
        for s in ss:
            for pat, why in FORBIDDEN:
                if re.search(pat, s.text, flags=re.I):
                    bad.append({"section": part, "sentence": s.text, "problem": why})
    return bad


def generate(r: dict[str, Any]) -> SarBlock:
    """r: dict with status, reason, now, model (info), risk (RiskBlock), dq (DataQuality), baseline, explanation, emergency, policy."""
    mv = {"type": "model", "model_version": r["model"].version, "calibration": r["model"].calibration_status}
    pv = {"type": "policy", "policy_version": r["policy"]["policy_version"], "thresholds": r["policy"]["thresholds"]}
    sit, ass, rec = [], [], []
    dq, risk, em, ex, bl = r["dq"], r["risk"], r["emergency"], r["explanation"], r["baseline"]
    target, horizon = r["model"].target, r["model"].horizon

    # ---------------- SITUATION
    if r["status"] == "OK":
        p = risk.calibrated_probability if risk.calibrated_probability is not None else risk.raw_output
        kind = "calibrated probability" if risk.calibrated_probability is not None else "model probability (uncalibrated)"
        tier_txt = (f"tier {risk.tier.value} (provisional cut-offs: MODERATE >= {risk.thresholds['moderate']:.4f}, "
                    f"HIGH >= {risk.thresholds['high']:.4f})") if risk.tier else "no tier shown (calibrator missing)"
        sit.append(_s(f"Model output {100 * p:.1f}% ({kind}) for {target} within {horizon}; {tier_txt}.", mv, pv,
                      {"type": "model_output", "raw_output": risk.raw_output}))
    else:
        sit.append(_s(f"No risk score was produced: {r['reason']}", {"type": "gate", "reason_code": r["reason_code"]}))
    sit.append(_s(f"Evaluated at {r['now']}.", {"type": "calculation", "name": "evaluation time"}))
    latest = []
    for sig in ("hr", "spo2", "rr"):
        f = dq.freshness_by_signal.get(sig)
        if f and f.last_valid_reading:
            latest.append((sig, f))
    if latest:
        sit.append(_s("Latest valid watch readings: " + "; ".join(
            f"{NAMES[s]} at {f.last_valid_reading} ({f.age_minutes:.0f} min old, {f.status.value.lower()})" for s, f in latest) + ".",
            *[{"type": "reading", "signal": s, "timestamp": f.last_valid_reading, "source": f.source} for s, f in latest]))
    if dq.missing_fields or dq.stale_fields or dq.invalid_fields:
        sit.append(_s("Data problems: " + "; ".join(x for x in [
            f"missing {', '.join(dq.missing_fields)}" if dq.missing_fields else "",
            f"stale {', '.join(dq.stale_fields)}" if dq.stale_fields else "",
            f"invalid {', '.join(dq.invalid_fields)}" if dq.invalid_fields else ""] if x) + ".",
            {"type": "data_quality", "fields": dq.missing_fields + dq.stale_fields + dq.invalid_fields}))
    if dq.imputed_fields:
        names = [i["feature"] for i in dq.imputed_fields]
        sit.append(_s(f"{len(names)} model inputs were not available and were filled with training medians: "
                      f"{', '.join(names[:8])}{' ...' if len(names) > 8 else ''}.",
                      {"type": "imputation", "features": names, "method": "fitted training imputer"}))

    # ---------------- ASSESSMENT
    if ex.status.value == "AVAILABLE":
        for c in ex.top_contributors:
            facts = "; ".join(i["text"] for i in c["inputs"])
            warn = " This direction goes against usual clinical expectation, so it should not be relied on." if c["clinical_direction_warning"] else ""
            ass.append(_s(f"{c['reason']}: {facts}. This {c['effect_words']} the model output (odds x{c['odds_multiplier']:.2f}).{warn}",
                          mv, *[{"type": "feature", "feature": i["feature"], "value": i["value"], "unit": i["unit"],
                                 "time_window": window_of(i["feature"]), "shap_log_odds": i["shap_log_odds"]} for i in c["inputs"]]))
    elif r["status"] == "OK":
        ass.append(_s(f"No explanation is available ({ex.reason}); the score above is still valid.", {"type": "explanation", "status": ex.status.value}))
    for d in bl.deviations:
        if abs(d["deviation"]) >= 0.5:
            ass.append(_s(d["text"], {"type": "baseline", "signal": d["signal"], "window": [d["baseline_from"], d["baseline_to"]],
                                      "n_hours": d["baseline_hours"], "status": d["baseline_status"]},
                          {"type": "reading", "signal": d["signal"], "timestamp": d["latest_hour"], "value": d["latest_value"]}))
    for sig, t in bl.trends.items():
        if t.get("pattern") in ("SUSTAINED", "TRANSIENT", "INTERMITTENT"):
            what = {"SUSTAINED": "a sustained", "TRANSIENT": "a single-hour (possibly noisy)", "INTERMITTENT": "an intermittent"}[t["pattern"]]
            ass.append(_s(f"{NAMES[sig]} shows {what} adverse change: {t['hours_beyond_threshold']} of {t['valid_hours']} valid hours in the last "
                          f"{t['window_hours']} h are at least {t['threshold']} {t['unit']} beyond the {t['reference']} ({t['reference_value']}).",
                          {"type": "calculation", "name": "trend detector", "signal": sig, "window_hours": t["window_hours"]}))
    if bl.corroboration and bl.corroboration["count"]:
        ass.append(_s(f"Signals changing together: {', '.join(NAMES[s] for s in bl.corroboration['signals_with_sustained_adverse_change'])}"
                      f" ({'corroborated' if bl.corroboration['corroborated'] else 'single signal only'}).",
                      {"type": "calculation", "name": "multi-signal corroboration"}))
    if risk.uncertainty:
        u = risk.uncertainty
        ass.append(_s(f"Refitting the model on bootstrap samples of the training patients gives {100 * u['p05']:.1f}%-{100 * u['p95']:.1f}% "
                      "(model variance from a small training set, not a validated interval).",
                      {"type": "calculation", "name": "bootstrap refits", "n": u["n_refits"]}))
    if risk.trajectory_summary and risk.trajectory_summary.get("change_6h") is not None:
        ts = risk.trajectory_summary
        ass.append(_s(f"Model output changed by {100 * ts['change_6h']:+.1f} percentage points over the last 6 h "
                      f"(peak {100 * ts['peak_24h']:.1f}% in the last 24 h).", {"type": "calculation", "name": "risk trajectory"}, mv))
    if dq.out_of_distribution:
        ass.append(_s(f"{len(dq.out_of_distribution)} inputs are outside the range seen in 99% of training hours: "
                      f"{', '.join(o['feature'] for o in dq.out_of_distribution[:6])}. The model is less reliable here.",
                      {"type": "calculation", "name": "out-of-distribution check", "features": [o["feature"] for o in dq.out_of_distribution]}))

    # ---------------- RECOMMENDATION (configured policy, never treatment advice)
    rp = {"type": "policy", "recommendation_policy": REC_POLICY_VERSION}
    if em.state == EmergencyState.TRIGGERED:
        rec.append(_s(f"Emergency rule {em.rule_id} is met ({em.reason}). The patient has been shown the emergency guidance and a "
                      "call option that opens the phone dialler; no ambulance has been contacted by the system.",
                      {"type": "emergency_rule", "rule_id": em.rule_id, "rules_version": em.rules_version}))
    elif em.state == EmergencyState.REVIEW_REQUIRED:
        rec.append(_s(f"Emergency rule {em.rule_id} asks for prompt review: {em.reason}. Re-measure and review now.",
                      {"type": "emergency_rule", "rule_id": em.rule_id, "rules_version": em.rules_version}))
    if dq.flags_for_remeasurement or dq.stale_fields:
        rec.append(_s("Repeat the readings first: check the watch fit and sync, because some readings are flagged or old.",
                      {"type": "data_quality", "flags": len(dq.flags_for_remeasurement), "stale": dq.stale_fields}))
    if r["status"] == "OK" and risk.tier:
        for line in POLICY[risk.tier.value]:
            rec.append(_s(line, rp, pv))
        rec.append(_s("Clinician review is " + ("appropriate." if risk.tier.value != "LOW" or em.state != EmergencyState.NOT_TRIGGERED
                                               else "not specifically prompted by this result."), rp))
    elif r["status"] != "OK":
        rec.append(_s("Refresh or repeat the watch measurements; no score is shown until enough recent data is available. "
                      "This is not a LOW-risk result.", rp))
    rec.append(_s("This output supports, and never replaces, clinical judgement. It suggests no treatment.", rp))

    lim = []
    if dq.stale_fields or dq.missing_fields:
        lim.append("Some signals are stale or missing (see data quality).")
    if dq.imputed_fields:
        lim.append(f"{len(dq.imputed_fields)} inputs were filled in by the model.")
    if risk.uncertainty and risk.tier and risk.uncertainty.get("tier_could_change"):
        lim.append("The bootstrap range crosses a tier cut-off, so the tier is uncertain.")
    lim += ["SHAP shows how the model weighed the inputs; it is not proof of cause.",
            f"Trained on ICU data from 100 patients ({r['model'].population}); thresholds are provisional.",
            "The model output is " + ("calibrated." if r["model"].calibration_status == "calibrated" else
                                      "not separately calibrated (see model.calibration_note).")]
    sentences = {"situation": sit, "assessment": ass, "recommendation": rec}
    bad = guard(sentences)
    if bad:      # never ship a narrative that fails the guard; fall back to the minimal template
        sentences = {"situation": sit[:1], "assessment": [], "recommendation": [_s("Clinician review of the raw result is needed; the narrative failed a safety check.", rp)]}
    one = (f"{risk.tier.value if r['status'] == 'OK' and risk.tier else r['status']}: "
           + (f"{100 * (risk.calibrated_probability or risk.raw_output):.1f}% for {target} within {horizon}" if r["status"] == "OK" else r["reason"])
           + (f"; EMERGENCY rule {em.rule_id}" if em.state == EmergencyState.TRIGGERED else "")
           + (f"; data warnings: {len(dq.warnings)}" if dq.warnings else "") + ".")
    return SarBlock(one_line=one, situation=" ".join(s.text for s in sentences["situation"]),
                    assessment=" ".join(s.text for s in sentences["assessment"]),
                    recommendation=" ".join(s.text for s in sentences["recommendation"]),
                    sentences=sentences, limitations=lim, template_version=TEMPLATE_VERSION, guard_passed=not bad)
