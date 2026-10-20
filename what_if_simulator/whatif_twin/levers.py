# The "levers" a user can move in the simulator. A lever is a real-world quantity (heart rate, weight, smoking...)
# that maps onto one or more model inputs. Editing a lever updates every model input that is computed from it, with
# the same definitions the training code used, so a scenario never mixes an edited value with stale derived inputs:
#
#   heart rate (recent 6 h)  -> hr, hr_lag1, hr_mean6h, hr_max6h, hr_delta6h, hr_trend_24h, hr_vs_baseline,
#                               shock index (hr / sbp) and the NEWS2 heart-rate points (ICU reference model)
#   weight                   -> weight_lbs, weight_kg, bmi (= 703 * lb / in^2), weight_change_72h
#   total cholesterol / HDL  -> total_chol, non_hdl (= TC - HDL), tc_hdl_ratio (= TC / HDL), ASCVD 10-year risk
#   smoking, age, clinic SBP -> ASCVD 10-year risk, re-anchored on the patient's own recorded score (pooled cohort eq.)
#
# Inputs that cannot be recomputed from a single hour (e.g. the 24 h heart-rate SD) are HELD and reported as held.
# The reading-level twin (readings_twin.py) rebuilds every input from edited raw readings and is used to measure how
# good these feature-level updates are. Quantities the models have no input for (sleep, steps, stress...) are refused.
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from .artifacts import finite

# ---------------------------------------------------------------- NEWS2 points, same bands as twin_preprocess.add_scores
_NEWS = {"rr": ([8, 11, 20, 24], [3, 1, 0, 2, 3]), "spo2": ([91, 93, 95], [3, 2, 1, 0]),
         "hr": ([40, 50, 90, 110, 130], [3, 1, 0, 1, 2, 3]), "temp": ([35, 36, 38, 39], [3, 1, 0, 1, 2]),
         "sbp": ([90, 100, 110], [3, 2, 1, 0])}


def news_points(signal: str, x) -> float:
    if not finite(x):
        return 0.0
    if signal == "sbp" and x >= 220:
        return 3.0
    cuts, pts = _NEWS[signal]
    for c, p in zip(cuts, pts[:-1]):
        if x <= c:
            return float(p)
    return float(pts[-1])


def _shift_news(v: dict, v0: dict, signal: str, old, new, notes: list[str]) -> None:
    if "news_score_mean6h" not in v:
        return
    dp = news_points(signal, new) - news_points(signal, old)
    if dp:
        for f in ("news_score_mean6h", "news_score_min6h"):
            if finite(v.get(f)):
                v[f] = max(0.0, v[f] + dp)
        notes.append(f"NEWS2 {signal} points re-banded at the 6 h average ({dp:+.0f}); approximation for the ICU reference model.")


# ---------------------------------------------------------------- ASCVD (pooled cohort equations, as in notebook 04)
# (ln age, ln age^2, ln TC, ln age*ln TC, ln HDL, ln age*ln HDL, treated ln SBP, ln age*treated, untreated ln SBP,
#  ln age*untreated, smoker, ln age*smoker, diabetes, S0, mean)  -- coefficient sets copied from scripts/make_nb4.py
PCE = {"F": (-29.799, 4.884, 13.540, -3.114, -13.578, 3.149, 2.019, 0, 1.957, 0, 7.574, -1.665, 0.661, 0.9665, -29.18),
       "M": (12.344, 0, 11.853, -2.664, -7.990, 1.769, 1.797, 0, 1.764, 0, 7.837, -1.795, 0.658, 0.9144, 61.18)}
ASCVD_NOTE = ("ASCVD re-anchored on the patient's own recorded score: only the change in the pooled-cohort linear "
              "predictor is applied (white/other coefficients, untreated blood pressure; race and treatment are not inputs).")


def _pce_lp(sex: str, age, tc, hdl, sbp, smoker) -> float:
    c = PCE[sex]
    la = math.log(min(max(age, 40), 79))
    lt, lh, ls = math.log(tc), math.log(hdl), math.log(sbp)
    return (c[0] * la + c[1] * la ** 2 + c[2] * lt + c[3] * la * lt + c[4] * lh + c[5] * la * lh
            + c[8] * ls + c[9] * la * ls + c[10] * smoker + c[11] * la * smoker)


def update_ascvd(v: dict, v0: dict, med: Callable[[str], float | None], notes: list[str]) -> None:
    if not finite(v0.get("ascvd_10y_risk")):
        return
    def inputs(d):
        tc = d.get("total_chol") if finite(d.get("total_chol")) else med("total_chol")
        hdl = (d["total_chol"] / d["tc_hdl_ratio"]) if finite(d.get("total_chol")) and finite(d.get("tc_hdl_ratio")) \
            and d["tc_hdl_ratio"] > 0 else (med("hdl") or 45.0)
        sbp = d.get("sbp_outpatient") if finite(d.get("sbp_outpatient")) else (med("sbp_outpatient") or 125.0)
        return d.get("age_at_admit", 60), tc or 180.0, hdl, sbp, float(d.get("smoker") or 0)
    sex = "M" if (v0.get("gender_male") or 0) >= 0.5 else "F"
    d_lp = _pce_lp(sex, *inputs(v)) - _pce_lp(sex, *inputs(v0))
    r0 = min(max(float(v0["ascvd_10y_risk"]), 1e-6), 1 - 1e-6)
    v["ascvd_10y_risk"] = round(1 - (1 - r0) ** math.exp(d_lp), 4)
    if abs(d_lp) > 1e-12 and ASCVD_NOTE not in notes:
        notes.append(ASCVD_NOTE)


# ---------------------------------------------------------------- lever definition
@dataclass
class Lever:
    key: str
    label: str
    unit: str
    category: str                      # physiological_signal | lifestyle | clinical_lab | care_process | non_modifiable
    primary: tuple[str, ...]           # model inputs that hold the lever's current value (first finite one wins)
    hard_range: tuple[float, float]    # values outside are rejected (risk_policy.yaml whatif ranges where defined)
    sweep_step: float                  # magnitude used for tornado charts (a clinically small but visible change)
    apply: Callable                    # (values, original values, target, change, ctx) -> None, edits values in place
    writes: tuple[str, ...]            # every model input this lever can change
    held: tuple[str, ...] = ()         # inputs that depend on the lever but cannot be recomputed from one hour
    discrete: tuple[float, ...] | None = None
    actionable: bool = False           # may be proposed by the counterfactual search
    healthy: tuple[float, float] | None = None   # counterfactuals only move toward (never past) this usual target band
    current_fn: Callable[[dict], float | None] | None = None   # for levers whose value is derived (weight, HDL, SBP)
    convert: Callable[[float], float] | None = None
    note: str = ""
    aliases: tuple[str, ...] = field(default_factory=tuple)

    def current(self, v: dict) -> float | None:
        if self.current_fn is not None:
            x = self.current_fn(v)
            return float(x) if finite(x) else None
        for f in self.primary:
            if finite(v.get(f)):
                x = float(v[f])
                return self.convert(x) if self.convert else x
        return None


def _vital(prefix: str, levels: tuple[str, ...], deltas: tuple[str, ...], lo: float, hi: float, news: str | None,
           shock: bool = False):
    """Edit for a watch vital sign. recent: the last 6 hourly bins are edited (as risk_engine.whatif does);
    persistent: the whole history is edited, so changes vs 6 h / 18 h earlier and vs the patient's own normal stay."""
    def apply(v, v0, target, ch, ctx):
        notes = ctx["notes"]
        cur = v0.get(prefix) if finite(v0.get(prefix)) else v0.get(f"{prefix}_mean6h")
        for f in levels:
            if f not in v:
                continue
            if ch.op == "set":
                v[f] = target
            elif finite(v0[f]):
                v[f] = v0[f] + (target - cur)
            else:
                notes.append(f"{f} was missing, so a shift cannot be applied to it; it stays missing.")
            if finite(v[f]) and not lo <= v[f] <= hi:
                v[f] = min(max(v[f], lo), hi)
                notes.append(f"{f} clipped to the physiological limit {lo}-{hi}.")
        for f in deltas:
            if f not in v or not finite(v0.get(f)):
                continue
            if ch.mode == "persistent":
                v[f] = 0.0 if ch.op == "set" else v0[f]
            else:
                anchor = v0[f"{prefix}_mean6h"] if f.endswith("trend_24h") else cur
                new_level = v.get(f"{prefix}_mean6h") if f.endswith("trend_24h") else v.get(prefix, target)
                v[f] = v0[f] + (new_level - anchor)
        if shock:
            for si, hf in (("shock_index", "hr"), ("shock_index_lag1", "hr_lag1"), ("shock_index_mean6h", "hr_mean6h")):
                if si in v and finite(v0.get(si)) and finite(v0.get(hf)) and finite(v.get(hf)) and v0[hf] > 0:
                    v[si] = v0[si] * v[hf] / v0[hf]
        if news:
            _shift_news(v, v0, news, v0.get(f"{prefix}_mean6h"), v.get(f"{prefix}_mean6h"), notes)
    return apply


def _direct(*features: str, ascvd: bool = False):
    def apply(v, v0, target, ch, ctx):
        for f in features:
            if f in v:
                v[f] = target
        if ascvd:
            update_ascvd(v, v0, ctx["median"], ctx["notes"])
    return apply


def _weight(v, v0, target_kg, ch, ctx):
    """The profile weight (lb, watch model) and the ICU charted weight (kg, ICU model) can differ for one patient;
    a shift moves both by the same amount, a set gives both the same value."""
    d_kg = target_kg - _weight_kg(v0)
    for f, factor in (("weight_lbs", 2.20462), ("weight_kg", 1.0)):
        if f in v and finite(v0.get(f)):
            v[f] = target_kg * factor if ch.op == "set" else v0[f] + d_kg * factor
        elif f in v and ch.op == "set":
            v[f] = target_kg * factor
    h = v0.get("height_in")
    if finite(h) and h > 0 and finite(v.get("weight_lbs")):
        if "bmi" in v:
            v["bmi"] = round(703 * v["weight_lbs"] / h ** 2, 2)
        if "bmi_missing" in v:
            v["bmi_missing"] = 0.0
    else:
        ctx["notes"].append("Height is unknown, so BMI could not be recomputed from the new weight.")
    if "weight_change_72h" in v and finite(v0.get("weight_change_72h")) and ch.mode == "recent":
        v["weight_change_72h"] = v0["weight_change_72h"] + d_kg


def _bmi(v, v0, target, ch, ctx):
    v["bmi"] = target
    if "bmi_missing" in v:
        v["bmi_missing"] = 0.0
    h = v0.get("height_in")
    if finite(h) and h > 0:
        lbs = target * h ** 2 / 703
        if "weight_lbs" in v:
            v["weight_lbs"] = round(lbs, 1)
        if "weight_kg" in v:
            v["weight_kg"] = round(lbs / 2.20462, 1)


def _hdl_of(d):
    return d["total_chol"] / d["tc_hdl_ratio"] if finite(d.get("total_chol")) and finite(d.get("tc_hdl_ratio")) \
        and d["tc_hdl_ratio"] > 0 else None


def _total_chol(v, v0, target, ch, ctx):
    hdl = _hdl_of(v0)
    v["total_chol"] = target
    if hdl:
        if "non_hdl" in v:
            v["non_hdl"] = target - hdl
        if "tc_hdl_ratio" in v:
            v["tc_hdl_ratio"] = target / hdl
    else:
        ctx["notes"].append("HDL is unknown, so non-HDL cholesterol and the TC/HDL ratio could not be recomputed.")
    update_ascvd(v, v0, ctx["median"], ctx["notes"])


def _hdl(v, v0, target, ch, ctx):
    tc = v.get("total_chol")
    if not finite(tc):
        raise ValueError("HDL only enters the models through total cholesterol, which is missing for this patient. "
                         "Set total cholesterol in the same scenario first.")
    if "non_hdl" in v:
        v["non_hdl"] = tc - target
    if "tc_hdl_ratio" in v:
        v["tc_hdl_ratio"] = tc / target
    update_ascvd(v, v0, ctx["median"], ctx["notes"])


def _inpatient_sbp(v, v0, target, ch, ctx):
    """ICU reference model only. The current SBP is not a model input; it is recovered as HR / shock index."""
    sbp0 = v0["hr"] / v0["shock_index"] if finite(v0.get("hr")) and finite(v0.get("shock_index")) and v0["shock_index"] > 0 else None
    if sbp0 is None:
        raise ValueError("Current systolic BP cannot be recovered for this patient (no heart rate or shock index).")
    d = target - sbp0
    if finite(v.get("sbp_max6h")):
        v["sbp_max6h"] = v0["sbp_max6h"] + d
    if finite(v.get("sbp_delta6h")) and ch.mode == "recent":
        v["sbp_delta6h"] = v0["sbp_delta6h"] + d
    for f in ("map_min6h", "map_max6h") + (("map_vs_baseline",) if ch.mode == "recent" else ()):
        if finite(v.get(f)):
            v[f] = v0[f] + d / 3                   # MAP ~ DBP + (SBP - DBP) / 3 with DBP held
    for f in ("shock_index", "shock_index_lag1", "shock_index_mean6h"):
        if finite(v.get(f)):
            v[f] = v0[f] * sbp0 / target
    _shift_news(v, v0, "sbp", sbp0, target, ctx["notes"])


def _rhythm(v, v0, target, ch, ctx):
    v["rhythm_sinus"] = float(target)
    if "rhythm_unknown" in v:
        v["rhythm_unknown"] = 0.0


def _weight_kg(v):
    if finite(v.get("weight_lbs")):
        return v["weight_lbs"] / 2.20462          # profile weight first (the deployed watch model's input)
    return v["weight_kg"] if finite(v.get("weight_kg")) else None


def _sbp_current(v):
    return v["hr"] / v["shock_index"] if finite(v.get("hr")) and finite(v.get("shock_index")) and v["shock_index"] > 0 else None


def build_levers(cfg: dict) -> dict[str, Lever]:
    allowed = cfg.get("whatif", {}).get("allowed", {})
    rng = lambda k, d: tuple(float(x) for x in allowed.get(k, d))  # noqa: E731
    hr_levels = ("hr", "hr_lag1", "hr_mean6h", "hr_max6h")
    L = [
        Lever("heart_rate", "Heart rate", "bpm", "physiological_signal", ("hr", "hr_mean6h"), rng("hr", (20, 250)), 10,
              _vital("hr", hr_levels, ("hr_delta6h", "hr_trend_24h", "hr_vs_baseline"), 20, 250, "hr", shock=True),
              writes=hr_levels + ("hr_delta6h", "hr_trend_24h", "hr_vs_baseline", "shock_index", "shock_index_lag1",
                                  "shock_index_mean6h", "news_score_mean6h", "news_score_min6h"),
              held=("hr_sd_24h", "ecg_hr_bpm"), healthy=(60, 100), actionable=True, aliases=("hr", "pulse", "heart rate", "resting heart rate"),
              note="Applied to the last 6 hourly bins (recent) or the whole history (persistent)."),
        Lever("spo2", "Oxygen saturation", "%", "physiological_signal", ("spo2", "spo2_mean6h"), rng("spo2", (50, 100)), 3,
              _vital("spo2", ("spo2", "spo2_lag1", "spo2_mean6h", "spo2_min6h", "spo2_max6h"), (), 50, 100, "spo2"),
              writes=("spo2", "spo2_lag1", "spo2_mean6h", "spo2_min6h", "spo2_max6h", "news_score_mean6h", "news_score_min6h"),
              healthy=(95, 100), actionable=True, aliases=("oxygen", "oxygen saturation", "sao2", "blood oxygen")),
        Lever("resp_rate", "Breathing rate", "/min", "physiological_signal", ("rr", "rr_mean6h"), rng("rr", (4, 60)), 4,
              _vital("rr", ("rr", "rr_lag1", "rr_mean6h", "rr_min6h", "rr_max6h"), ("rr_delta6h", "rr_vs_baseline"), 4, 60, "rr"),
              writes=("rr", "rr_lag1", "rr_mean6h", "rr_min6h", "rr_max6h", "rr_delta6h", "rr_vs_baseline",
                      "news_score_mean6h", "news_score_min6h"),
              held=("rr_night_minus_day_24h",), healthy=(12, 20), actionable=True, aliases=("rr", "respiratory rate", "breathing")),
        Lever("rhythm_sinus", "Normal sinus rhythm", "0/1", "physiological_signal", ("rhythm_sinus",), (0, 1), 1, _rhythm,
              writes=("rhythm_sinus", "rhythm_unknown"), held=("tachy_burden_24h",), discrete=(0.0, 1.0),
              aliases=("rhythm", "sinus rhythm")),
        Lever("temperature", "Temperature", "°C", "physiological_signal", ("temp_c",), (30, 43), 1,
              lambda v, v0, t, ch, ctx: (v.__setitem__("temp_c", t), _shift_news(v, v0, "temp", v0.get("temp_c"), t, ctx["notes"])),
              writes=("temp_c", "news_score_mean6h", "news_score_min6h"), aliases=("temp", "body temperature", "fever")),
        Lever("inpatient_sbp", "Systolic BP (bedside)", "mmHg", "physiological_signal", (), (50, 250), 15, _inpatient_sbp,
              writes=("sbp_max6h", "sbp_delta6h", "map_min6h", "map_max6h", "map_vs_baseline", "shock_index",
                      "shock_index_lag1", "shock_index_mean6h", "news_score_mean6h", "news_score_min6h"),
              aliases=("sbp", "systolic", "blood pressure (bedside)"), current_fn=_sbp_current,
              note="ICU reference model only; current SBP is recovered as heart rate / shock index."),
        Lever("weight", "Body weight", "kg", "lifestyle", ("weight_lbs", "weight_kg"), (25, 300), 5, _weight,
              writes=("weight_lbs", "weight_kg", "bmi", "bmi_missing", "weight_change_72h"), actionable=True,
              aliases=("body weight", "weight loss"), current_fn=_weight_kg),
        Lever("bmi", "BMI", "kg/m²", "lifestyle", ("bmi",), rng("bmi", (12, 70)), 3, _bmi,
              writes=("bmi", "bmi_missing", "weight_lbs", "weight_kg"), aliases=("body mass index",)),
        Lever("smoker", "Smoking", "0/1", "lifestyle", ("smoker",), rng("smoker", (0, 1)), 1, _direct("smoker", ascvd=True),
              writes=("smoker", "ascvd_10y_risk"), discrete=(0.0, 1.0), healthy=(0, 0), actionable=True,
              aliases=("smoking", "quit smoking", "tobacco")),
        Lever("total_chol", "Total cholesterol", "mg/dL", "clinical_lab", ("total_chol",), rng("total_chol", (50, 500)), 30,
              _total_chol, writes=("total_chol", "non_hdl", "tc_hdl_ratio", "ascvd_10y_risk"), healthy=(150, 200), actionable=True,
              aliases=("cholesterol", "tc")),
        Lever("hdl", "HDL cholesterol", "mg/dL", "clinical_lab", (), (5, 150), 10, _hdl,
              writes=("non_hdl", "tc_hdl_ratio", "ascvd_10y_risk"), aliases=("good cholesterol",), current_fn=_hdl_of,
              note="Not a model input itself; acts through non-HDL cholesterol, the TC/HDL ratio and ASCVD."),
        Lever("ldl", "LDL cholesterol", "mg/dL", "clinical_lab", ("ldl",), rng("ldl", (10, 400)), 30, _direct("ldl"),
              writes=("ldl",), healthy=(70, 100), actionable=True, aliases=("bad cholesterol",)),
        Lever("triglycerides", "Triglycerides", "mg/dL", "clinical_lab", ("triglycerides",), rng("triglycerides", (10, 2000)),
              50, _direct("triglycerides"), writes=("triglycerides",), healthy=(80, 150), actionable=True, aliases=("tg",)),
        Lever("hba1c", "HbA1c", "%", "clinical_lab", ("hba1c",), rng("hba1c", (3, 20)), 1, _direct("hba1c"),
              writes=("hba1c",), healthy=(5.0, 5.7), actionable=True, aliases=("a1c", "glycated haemoglobin", "diabetes control")),
        Lever("egfr", "Kidney function (eGFR)", "mL/min/1.73m²", "clinical_lab", ("egfr",), (1, 200), 15, _direct("egfr"),
              writes=("egfr",), aliases=("kidney function", "gfr")),
        Lever("clinic_sbp", "Usual clinic systolic BP", "mmHg", "lifestyle", ("sbp_outpatient",), (60, 260), 15,
              _direct("sbp_outpatient", ascvd=True), writes=("sbp_outpatient", "ascvd_10y_risk"), healthy=(110, 130), actionable=True,
              aliases=("clinic blood pressure", "usual blood pressure")),
        Lever("clinic_dbp", "Usual clinic diastolic BP", "mmHg", "lifestyle", ("dbp_outpatient",), (30, 160), 10,
              _direct("dbp_outpatient"), writes=("dbp_outpatient",), healthy=(70, 80), actionable=True),
        Lever("n_active_drugs", "Number of active medicines", "count", "care_process", ("n_active_drugs",), (0, 200), 3,
              _direct("n_active_drugs"), writes=("n_active_drugs",), discrete=None, aliases=("medicines", "medications"),
              note="Set by the care team; exploratory only."),
        Lever("potassium", "Potassium", "mmol/L", "clinical_lab", ("lab_potassium",), (1.5, 9), 0.5, _direct("lab_potassium"),
              writes=("lab_potassium",), aliases=("k",)),
        Lever("platelets", "Platelet count", "×10³/µL", "clinical_lab", ("lab_platelets",), (5, 1500), 50,
              _direct("lab_platelets"), writes=("lab_platelets",)),
        Lever("age", "Age", "years", "non_modifiable", ("age_at_admit",), (18, 120), 10, _direct("age_at_admit", ascvd=True),
              writes=("age_at_admit", "ascvd_10y_risk"), aliases=("older", "years"),
              note="Not modifiable: shown to explore model behaviour only, never proposed as an option."),
    ]
    return {lv.key: lv for lv in L}


# Quantities people often ask about that NO model input represents. Changing them cannot change the output, so the
# simulator refuses them instead of inventing an effect.
UNSUPPORTED = {
    "sleep_duration": "Sleep: no sleep data existed in training (MIMIC-IV ICU stays), so neither model has a sleep input.",
    "steps": "Steps / physical activity: no activity data in training; no model input.",
    "physical_activity": "Physical activity: no activity data in training; no model input.",
    "stress": "Stress: no stress or mood input in either model.",
    "watch_hrv": "Watch heart-rate variability (RMSSD/SDNN from the watch): not measured in training. The ICU model's "
                 "log_sdnn_ms comes from a 12-lead ECG, not the watch.",
    "diet": "Diet / sodium intake: no dietary input in either model.",
    "alcohol": "Alcohol: only enters the HAS-BLED score, which is stored as a finished score; no direct input.",
    "medication_adherence": "Medication adherence: no adherence input in either model.",
    "watch_blood_pressure": "Cuffless watch blood pressure: not an input of the deployed phone + watch model.",
    "glucose_cgm": "Continuous glucose: no CGM data; the ICU model only sees the change between lab glucose tests.",
}
UNSUPPORTED_ALIASES = {"sleep": "sleep_duration", "sleep hours": "sleep_duration", "sleep duration": "sleep_duration",
                       "step count": "steps", "steps": "steps", "exercise": "physical_activity", "activity": "physical_activity",
                       "hrv": "watch_hrv", "stress": "stress", "diet": "diet", "salt": "diet", "alcohol": "alcohol",
                       "adherence": "medication_adherence", "glucose": "glucose_cgm"}


def resolve(name: str, levers: dict[str, Lever]) -> tuple[str | None, str | None]:
    """Map a user-typed name to a lever key. Returns (lever_key, None) or (None, reason it is unsupported)."""
    n = name.strip().lower().replace("-", " ")
    k = n.replace(" ", "_")
    if k in levers:
        return k, None
    for lv in levers.values():
        if n in lv.aliases:
            return lv.key, None
    u = UNSUPPORTED_ALIASES.get(n, k if k in UNSUPPORTED else None)
    if u:
        return None, UNSUPPORTED[u]
    return None, f"'{name}' is not a supported input. Supported: {', '.join(sorted(levers))}."


def supported_models(lever: Lever, model_features: dict[str, list[str]]) -> list[str]:
    return [m for m, fs in model_features.items() if set(lever.writes) & set(fs)]


def plausible_range(lever: Lever, reference: dict) -> tuple[float, float] | None:
    """Training 0.5-99.5 percentile range of the lever's primary input (watch training hours), within the hard range."""
    for f in lever.primary:
        r = reference.get("features", {}).get(f)
        if r and r.get("p005") is not None:
            lo, hi = float(r["p005"]), float(r["p995"])
            if f == "weight_lbs":
                lo, hi = lo / 2.20462, hi / 2.20462
            return max(lo, lever.hard_range[0]), min(hi, lever.hard_range[1])
    return None


def as_float(x) -> float:
    return float(np.asarray(x, float))
