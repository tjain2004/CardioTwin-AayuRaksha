# Plain-English wording for model inputs, reused from notebook 06 (06_shap_explanations) so the engine, the
# notebook reports and the phone app describe every input the same way. MED (typical values) is filled in from the
# training reference artifact when the engine starts.
from __future__ import annotations

import numpy as np
import pandas as pd

MED: dict = {}

TXT = {
    "hr": ("current heart rate", " bpm", "level"), "hr_lag1": ("heart rate in the previous hour", " bpm", "level"),
    "hr_mean6h": ("average heart rate over the last 6 h", " bpm", "level"), "hr_max6h": ("highest heart rate in the last 6 h", " bpm", "level"),
    "hr_sd_24h": ("heart-rate variability across the day (SD over 24 h)", " bpm", "level"),
    "hr_delta6h": ("heart rate over the last 6 h", " bpm", "change"),
    "hr_trend_24h": ("average heart rate compared with 18 h earlier", " bpm", "change"),
    "hr_vs_baseline": ("heart rate compared with this patient's own normal", " bpm", "vs"),
    "spo2": ("current oxygen saturation", "%", "level"), "spo2_lag1": ("oxygen saturation in the previous hour", "%", "level"),
    "spo2_mean6h": ("average oxygen saturation over 6 h", "%", "level"), "spo2_min6h": ("lowest oxygen saturation in the last 6 h", "%", "level"),
    "spo2_max6h": ("highest oxygen saturation in the last 6 h", "%", "level"),
    "rr": ("current breathing rate", "/min", "level"), "rr_lag1": ("breathing rate in the previous hour", "/min", "level"),
    "rr_mean6h": ("average breathing rate over 6 h", "/min", "level"), "rr_min6h": ("lowest breathing rate in the last 6 h", "/min", "level"),
    "rr_max6h": ("highest breathing rate in the last 6 h", "/min", "level"), "rr_delta6h": ("breathing rate over the last 6 h", "/min", "change"),
    "rr_vs_baseline": ("breathing rate compared with this patient's own normal", "/min", "vs"),
    "rr_night_minus_day_24h": ("night-time breathing rate compared with daytime", "/min", "vs"),
    "tachy_burden_24h": ("time in a fast heart rhythm over the last 24 h", " h", "level"),
    "rhythm_sinus": ("is in normal sinus rhythm", "is not in normal sinus rhythm", "flag"),
    "rhythm_unknown": ("heart rhythm could not be classified this hour", "heart rhythm was classified this hour", "flag"),
    "age_at_admit": ("age", " years", "level"), "bmi": ("BMI", "", "level"),
    "bmi_missing": ("BMI is not recorded", "BMI is recorded", "flag"), "weight_lbs": ("weight", " lb", "level"),
    "height_in": ("height", " in", "level"), "gender_male": ("is male", "is female", "flag"),
    "smoker": ("is a smoker", "is a non-smoker", "flag"),
    "ix_bleeding": ("takes a medicine combination that raises bleeding risk", "has no bleeding-risk medicine combination", "flag"),
    "has_bled_partial": ("bleeding-risk score (HAS-BLED, partial)", "", "level"),
    "ascvd_10y_risk": ("10-year cardiovascular risk (ASCVD)", "%", "pct"), "n_active_drugs": ("number of active medicines", "", "level"),
    "non_hdl": ("non-HDL cholesterol", " mg/dL", "level"), "total_chol": ("total cholesterol", " mg/dL", "level"),
    "ldl": ("LDL cholesterol", " mg/dL", "level"), "triglycerides": ("triglycerides", " mg/dL", "level"),
    "tc_hdl_ratio": ("total/HDL cholesterol ratio", "", "level"), "hba1c": ("HbA1c", "%", "level"),
    "egfr": ("kidney function (eGFR)", " mL/min", "level"), "sbp_outpatient": ("usual clinic systolic BP", " mmHg", "level"),
    "dbp_outpatient": ("usual clinic diastolic BP", " mmHg", "level"),
    "days_since_last_discharge": ("days since the last hospital discharge", "", "discharge"),
    "log_prior_admissions": ("previous hospital admissions", "", "log1p"),
    # full (ICU reference) model only
    "gcs_total": ("consciousness score (GCS, 15 = fully alert)", "", "level"), "weight_kg": ("weight", " kg", "level"),
    "hours_since_icu_admit": ("time since ICU admission", " h", "level"), "hours_since_ecg": ("time since the last 12-lead ECG", " h", "level"),
    "ecg_hr_bpm": ("heart rate on the last ECG", " bpm", "level"), "lab_potassium": ("potassium", " mmol/L", "level"),
    "lab_platelets": ("platelet count", " ×10³/µL", "level"), "lab_calcium": ("calcium", " mg/dL", "level"),
    "lab_log_bun": ("blood urea nitrogen (BUN)", " mg/dL", "exp"), "lab_chg_glucose": ("glucose since the previous test", "", "logchg"),
    "lab_chg_wbc": ("white cell count since the previous test", "", "logchg"),
    "news_score_min6h": ("lowest NEWS2 score in the last 6 h", "", "level"), "news_score_mean6h": ("average NEWS2 score over 6 h", "", "level"),
    "shock_index": ("shock index (heart rate ÷ systolic BP)", "", "level2"), "shock_index_lag1": ("shock index in the previous hour", "", "level2"),
    "shock_index_mean6h": ("average shock index over 6 h", "", "level2"), "sbp_delta6h": ("systolic BP over the last 6 h", " mmHg", "change"),
    "sbp_max6h": ("highest systolic BP in the last 6 h", " mmHg", "level"), "map_min6h": ("lowest mean arterial pressure in 6 h", " mmHg", "level"),
    "map_max6h": ("highest mean arterial pressure in 6 h", " mmHg", "level"),
    "map_vs_baseline": ("mean arterial pressure compared with this patient's own normal", " mmHg", "vs"),
    "temp_c": ("temperature", " °C", "level"), "weight_change_72h": ("weight over the last 72 h", " kg", "change"),
    "r_amp_mv": ("R-wave amplitude on the ECG", " mV", "level2"), "qrs_ms": ("QRS duration on the ECG", " ms", "level"),
    "log_sdnn_ms": ("heart-rate variability on the ECG (SDNN)", " ms", "exp"),
}

def num(v, d=0):
    return f"{v:,.{d}f}"

def describe(f, v):
    # one input's value as a short phrase; the typical value comes from all patients in the data
    label, unit, kind = TXT[f]
    if kind == "flag":
        return "the patient " + (label if v == 1 else unit) if not label.startswith(("heart rhythm", "BMI")) else (label if v == 1 else unit)
    if pd.isna(v):
        return f"{label}: not recorded"
    m = MED.get(f, np.nan)
    if kind == "level":
        d = 1 if abs(m) < 20 and f not in ("age_at_admit", "n_active_drugs", "gcs_total") else 0
        return f"{label} is {num(v, d)}{unit} (typical {num(m, d)}{unit})"
    if kind == "level2":
        return f"{label} is {v:.2f}{unit} (typical {m:.2f}{unit})"
    if kind == "pct":
        return f"{label} is {100 * v:.0f}% (typical {100 * m:.0f}%)"
    if kind == "change":
        return f"{label}: " + ("steady" if abs(v) < 0.5 else f"{'up' if v > 0 else 'down'} {num(abs(v), 1 if abs(v) < 10 else 0)}{unit}")
    if kind == "vs":
        return f"{label}: " + ("about the same" if abs(v) < 0.5 else f"{num(abs(v), 1 if abs(v) < 10 else 0)}{unit} {'higher' if v > 0 else 'lower'}")
    if kind == "exp":
        return f"{label} is {np.exp(v):.0f}{unit} (typical {np.exp(m):.0f}{unit})"
    if kind == "logchg":
        return f"{label}: {'up' if v > 0 else 'down'} {100 * abs(np.exp(v) - 1):.0f}%"
    if kind == "log1p":
        return f"{np.expm1(v):.0f} {label}"
    if kind == "discharge":
        return "no earlier hospital stay on record" if v < 0 else f"last discharged from hospital {v:.0f} days ago"
    return f"{label} = {v}"

def effect(phi):
    m = np.exp(phi)
    if m >= 1:
        word = "strongly raises" if m >= 1.5 else "raises" if m >= 1.15 else "slightly raises"
    else:
        word = "strongly lowers" if m <= 1 / 1.5 else "lowers" if m <= 1 / 1.15 else "slightly lowers"
    return word, m

# related inputs are explained together: a model given 7 heart-rate inputs can split one signal into opposite-signed pieces
# (e.g. "highest breathing rate lowers risk" while "average breathing rate raises it"). The sum over a group is still exact SHAP.
GROUPS = {
    "Heart rate": ["hr", "hr_lag1", "hr_mean6h", "hr_max6h", "hr_delta6h", "hr_trend_24h", "hr_vs_baseline", "hr_sd_24h", "ecg_hr_bpm"],
    "Breathing rate": ["rr", "rr_lag1", "rr_mean6h", "rr_min6h", "rr_max6h", "rr_delta6h", "rr_vs_baseline", "rr_night_minus_day_24h"],
    "Oxygen saturation": ["spo2", "spo2_lag1", "spo2_mean6h", "spo2_min6h", "spo2_max6h"],
    "Heart rhythm": ["rhythm_sinus", "rhythm_unknown", "tachy_burden_24h"],
    "Cholesterol": ["total_chol", "ldl", "non_hdl", "triglycerides", "tc_hdl_ratio"],
    "Body size": ["bmi", "bmi_missing", "weight_lbs", "height_in", "weight_kg"],
    "Usual blood pressure": ["sbp_outpatient", "dbp_outpatient"],
    "Blood pressure and shock index": ["sbp_delta6h", "sbp_max6h", "map_min6h", "map_max6h", "map_vs_baseline",
                                       "shock_index", "shock_index_lag1", "shock_index_mean6h"],
    "NEWS2 score": ["news_score_min6h", "news_score_mean6h"],
    "Hospital history": ["log_prior_admissions", "days_since_last_discharge"],
    "Bleeding risk and medicines": ["has_bled_partial", "ix_bleeding", "n_active_drugs"],
    "12-lead ECG": ["r_amp_mv", "qrs_ms", "log_sdnn_ms", "hours_since_ecg"],
}
GROUP_OF = {f: g for g, fs in GROUPS.items() for f in fs}
NAME = {"smoker": "Smoking", "gender_male": "Sex", "age_at_admit": "Age", "hba1c": "HbA1c (diabetes control)",
        "ascvd_10y_risk": "Cardiovascular risk score", "egfr": "Kidney function", "gcs_total": "Consciousness (GCS)",
        "hours_since_icu_admit": "Time in ICU", "temp_c": "Temperature", "weight_change_72h": "Weight change",
        "lab_platelets": "Platelets", "lab_potassium": "Potassium", "lab_calcium": "Calcium", "lab_log_bun": "Urea (BUN)",
        "lab_chg_glucose": "Glucose change", "lab_chg_wbc": "White cell count change"}
def group_of(f):
    return GROUP_OF.get(f, NAME.get(f, TXT[f][0][0].upper() + TXT[f][0][1:]))


EXPECT = {"Heart rate": ("hr", +1), "Breathing rate": ("rr", +1), "Oxygen saturation": ("spo2", -1), "Consciousness (GCS)": ("gcs_total", -1)}
WARN = " ⚠ This direction goes against usual clinical expectation; the model learned it from few patients, so do not rely on it."

def against_expectation(name, x, row):
    if name not in EXPECT:
        return False
    f, e = EXPECT[name]
    v, m = row[f], MED[f]
    return bool(pd.notna(v) and abs(v - m) > 0.1 * abs(m) and np.sign(v - m) * e != np.sign(x))


REF = {
    "hr": ("60-100 bpm", 60, 100, "AHA, adult resting heart rate"), "spo2": ("95-100%", 95, 100, "typical healthy adult; BTS hospital target 94-98%"),
    "rr": ("12-20 /min", 12, 20, "NEWS2 (RCP 2017) scores 12-20 as normal"), "temp_c": ("36.1-38.0 °C", 36.1, 38.0, "NEWS2 (RCP 2017)"),
    "bmi": ("18.5-24.9", 18.5, 24.9, "WHO adult BMI"), "hba1c": ("< 5.7%", None, 5.7, "ADA: 5.7-6.4% prediabetes, >= 6.5% diabetes"),
    "ldl": ("< 100 mg/dL", None, 100, "NCEP ATP III optimal"), "total_chol": ("< 200 mg/dL", None, 200, "NCEP ATP III desirable"),
    "triglycerides": ("< 150 mg/dL", None, 150, "NCEP ATP III normal"), "non_hdl": ("< 130 mg/dL", None, 130, "NCEP ATP III goal"),
    "egfr": (">= 90 mL/min", 90, None, "KDIGO G1"), "sbp_outpatient": ("< 120 mmHg", None, 120, "ACC/AHA 2017 normal"),
    "dbp_outpatient": ("< 80 mmHg", None, 80, "ACC/AHA 2017 normal"), "gcs_total": ("15", 15, 15, "fully alert"),
    "lab_potassium": ("3.5-5.0 mmol/L", 3.5, 5.0, "typical adult lab range; local ranges vary"),
    "lab_platelets": ("150-450 ×10³/µL", 150, 450, "typical adult lab range; local ranges vary"),
    "lab_calcium": ("8.5-10.2 mg/dL", 8.5, 10.2, "typical adult lab range; local ranges vary"),
}
for f in ["hr_lag1", "hr_mean6h", "hr_max6h"]: REF[f] = REF["hr"]
for f in ["spo2_lag1", "spo2_mean6h", "spo2_min6h", "spo2_max6h"]: REF[f] = REF["spo2"]
for f in ["rr_lag1", "rr_mean6h", "rr_min6h", "rr_max6h"]: REF[f] = REF["rr"]

def ref_flag(f, v):
    if f not in REF or pd.isna(v):
        return ""
    _, lo, hi, _ = REF[f]
    return "below range" if lo is not None and v < lo else "above range" if hi is not None and v > hi else "in range"

