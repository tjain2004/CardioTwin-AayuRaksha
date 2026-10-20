# Rule functions for the digital-twin app. Not trained models: transparent rules with sources.
import json

def alert_tier(row, rules):
    # row: dict of the latest hourly features; rules: alert_rules.json["rules"]
    for tier in ("red", "amber"):
        hits = [name for name, cond in rules[tier].items() if _eval(cond, row)]
        if hits:
            return tier, hits
    return "green", []

def _eval(cond, row):
    try:
        return bool(eval(cond, {"__builtins__": {}}, {k: (v if v == v else float("nan")) for k, v in row.items()}))
    except Exception:
        return False

SYMPTOMS = {"chest_pain": 3, "pain_spreads_arm_jaw_back": 2, "breathless": 2, "cold_sweat": 2, "nausea": 1, "dizzy_or_fainted": 2}

def symptom_check(answers):
    # answers from the chatbot, e.g. {"chest_pain": True, "cold_sweat": True}; a triage aid, not a diagnosis
    score = sum(w for k, w in SYMPTOMS.items() if answers.get(k))
    urgent = answers.get("chest_pain") and (answers.get("pain_spreads_arm_jaw_back") or answers.get("cold_sweat") or answers.get("breathless"))
    return {"score": score, "tier": "red" if urgent or score >= 6 else "amber" if score >= 3 else "green"}

def safe_exercise_zone(age, resting_hr, on_beta_blocker=False, low=0.4, high=0.6):
    # Karvonen heart-rate reserve; on beta blockers HRmax = 164 - 0.7 x age (Brawner 2004), else 220 - age
    hr_max = 164 - 0.7 * age if on_beta_blocker else 220 - age
    reserve = hr_max - resting_hr
    return {"hr_max": round(hr_max), "zone": (round(resting_hr + low * reserve), round(resting_hr + high * reserve))}

def hr_recovery_flag(hr_peak, hr_after_1_min):
    # heart-rate recovery < 12 bpm after 1 minute is abnormal (Cole 1999)
    return int(hr_peak - hr_after_1_min < 12)

def dose_check(generic, mg_today, limits, age=None):
    # compares today's total of a prescribed drug with the clinic's daily limit (resets at midnight); never suggests a new dose
    limit = limits.get(generic)
    if generic == "citalopram" and age and age > 60:
        limit = 20
    if limit is None:
        return {"status": "no limit on file"}
    pct = 100 * mg_today / limit
    return {"pct_of_limit": round(pct), "status": "over limit: tell the doctor" if pct > 100 else "near limit" if pct > 80 else "ok"}
