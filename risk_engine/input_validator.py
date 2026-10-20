# Input validation: required fields, types, units, timestamps, duplicates, conflicts, physiologically impossible
# values (excluded, as in training) and unusual-but-possible values (kept and flagged for re-measurement).
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from twin_preprocess import RANGES, RHYTHMS, rhythm_group

from .schemas import ClinicalReading, PatientProfile, WearableReading

WATCH_SIGNALS = ("hr", "spo2", "rr")

# unit -> function converting the value to the training unit; None means "unit not given"
WEARABLE_UNITS = {
    "hr": {"bpm": lambda v: v, "beats/min": lambda v: v, "/min": lambda v: v, "1/min": lambda v: v},
    "spo2": {"%": lambda v: v, "percent": lambda v: v, "fraction": lambda v: 100 * v},
    "rr": {"breaths/min": lambda v: v, "/min": lambda v: v, "brpm": lambda v: v, "1/min": lambda v: v},
}
DEFAULT_UNIT = {"hr": "bpm", "spo2": "%", "rr": "breaths/min"}

MGDL_CHOL = 38.67   # mmol/L -> mg/dL for cholesterol
MGDL_TG = 88.57     # mmol/L -> mg/dL for triglycerides
CLINICAL_UNITS = {
    "ldl": {"mg/dL": lambda v: v, "mmol/L": lambda v: v * MGDL_CHOL},
    "hdl": {"mg/dL": lambda v: v, "mmol/L": lambda v: v * MGDL_CHOL},
    "total_chol": {"mg/dL": lambda v: v, "mmol/L": lambda v: v * MGDL_CHOL},
    "non_hdl": {"mg/dL": lambda v: v, "mmol/L": lambda v: v * MGDL_CHOL},
    "triglycerides": {"mg/dL": lambda v: v, "mmol/L": lambda v: v * MGDL_TG},
    "hba1c": {"%": lambda v: v, "mmol/mol": lambda v: v / 10.929 + 2.15},
    "egfr": {"mL/min/1.73m2": lambda v: v, "mL/min": lambda v: v},
    "sbp_outpatient": {"mmHg": lambda v: v},
    "dbp_outpatient": {"mmHg": lambda v: v},
    "tc_hdl_ratio": {"ratio": lambda v: v, "": lambda v: v},
}
CLINICAL_LIMITS = {"ldl": (10, 400), "hdl": (5, 150), "total_chol": (50, 500), "non_hdl": (10, 450),
                   "triglycerides": (10, 3000), "hba1c": (3, 20), "egfr": (1, 200), "sbp_outpatient": (60, 260),
                   "dbp_outpatient": (30, 160), "tc_hdl_ratio": (0.5, 30)}
PROFILE_LIMITS = {"age_at_admit": (18, 120), "gender_male": (0, 1), "height_in": (40, 96), "weight_lbs": (50, 700),
                  "bmi": (10, 80), "smoker": (0, 1), "log_prior_admissions": (0, 10), "days_since_last_discharge": (-1, 36500),
                  "n_active_drugs": (0, 200), "ix_bleeding": (0, 1), "has_bled_partial": (0, 9), "ascvd_10y_risk": (0, 1),
                  "hist_af": (0, 1)}


@dataclass
class ValidatedInput:
    readings: pd.DataFrame                    # timestamp (UTC, tz-naive), signal, value (training units), source, quality
    profile: dict[str, Any]                   # validated profile values (None when unknown)
    clinical: dict[str, dict[str, Any]]       # name -> {value, unit, timestamp, source}
    invalid: list[dict[str, Any]] = field(default_factory=list)
    remeasure: list[dict[str, Any]] = field(default_factory=list)
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    sources: list[str] = field(default_factory=list)


def _to_utc(ts, assume_tz: str):
    t = pd.Timestamp(ts)
    if t.tzinfo is None:
        t = t.tz_localize(assume_tz)
    return t.tz_convert("UTC").tz_localize(None)


def _finite(v) -> bool:
    try:
        return v is not None and not isinstance(v, bool) and math.isfinite(float(v))
    except (TypeError, ValueError):
        return False


def validate(profile: PatientProfile | dict, clinical_readings, wearable_readings, now: pd.Timestamp, cfg: dict) -> ValidatedInput:
    """Validate everything the engine receives. Never deletes a reading for being unusual; only impossible values,
    unparseable timestamps, unknown units and low-quality readings are excluded, and each exclusion is recorded."""
    suff, plaus = cfg["sufficiency"], cfg["plausibility"]
    tz = suff.get("assume_timezone_for_naive", "UTC")
    prof = profile if isinstance(profile, PatientProfile) else PatientProfile.model_validate(profile)
    out = ValidatedInput(readings=pd.DataFrame(), profile={}, clinical={})
    c = {k: 0 for k in ["received", "valid", "bad_timestamp", "future_timestamp", "unknown_signal", "unknown_unit",
                        "non_finite", "impossible_value", "low_quality", "duplicates_removed", "conflicts",
                        "flagged_for_remeasurement", "unit_assumed", "timezone_assumed"]}

    # ---- profile
    for name, (lo, hi) in PROFILE_LIMITS.items():
        v = getattr(prof, name, None)
        if v is None:
            out.profile[name] = None
            continue
        if not _finite(v) or not (lo <= float(v) <= hi):
            out.invalid.append({"field": name, "value": v, "reason": f"outside accepted range {lo}-{hi} or not a number"})
            out.profile[name] = None
        else:
            out.profile[name] = float(v)
    out.profile["patient_id"] = prof.patient_id
    out.profile["reported_symptoms"] = prof.reported_symptoms
    out.profile["is_demo"] = prof.is_demo

    # ---- clinical readings
    for r in clinical_readings or []:
        r = r if isinstance(r, ClinicalReading) else ClinicalReading.model_validate(r)
        name = r.name
        if name not in CLINICAL_UNITS:
            out.warnings.append(f"Clinical reading '{name}' is not used by the model and was ignored.")
            continue
        units = CLINICAL_UNITS[name]
        unit = r.unit if r.unit is not None else next(iter(units))
        if unit not in units or not _finite(r.value):
            out.invalid.append({"field": name, "value": r.value, "unit": r.unit, "reason": "unknown unit or not a number"})
            continue
        v = units[unit](float(r.value))
        lo, hi = CLINICAL_LIMITS[name]
        if not lo <= v <= hi:
            out.invalid.append({"field": name, "value": r.value, "unit": unit, "reason": f"outside accepted range {lo}-{hi}"})
            continue
        ts = None
        if r.timestamp is not None:
            try:
                ts = _to_utc(r.timestamp, tz)
            except Exception:
                out.invalid.append({"field": name, "value": r.timestamp, "reason": "timestamp could not be parsed"})
        prev = out.clinical.get(name)
        if prev is None or (ts is not None and (prev["timestamp"] is None or ts > prev["timestamp"])):
            out.clinical[name] = {"value": v, "unit": "mg/dL" if name in MGDL_NAMES else unit, "timestamp": ts,
                                  "source": r.source or "not stated"}

    # ---- wearable readings (long format)
    rows = []
    for r in wearable_readings or []:
        c["received"] += 1
        r = r if isinstance(r, WearableReading) else WearableReading.model_validate(r)
        sig = str(r.signal).lower()
        if sig not in WATCH_SIGNALS + ("rhythm",):
            c["unknown_signal"] += 1
            continue
        try:
            t = pd.Timestamp(r.timestamp)
            if t.tzinfo is None:
                c["timezone_assumed"] += 1
            t = _to_utc(t, tz)
        except Exception:
            c["bad_timestamp"] += 1
            out.invalid.append({"field": sig, "value": str(r.timestamp), "reason": "timestamp could not be parsed"})
            continue
        if t > now + pd.Timedelta(minutes=suff.get("future_skew_minutes", 5)):
            c["future_timestamp"] += 1
            out.invalid.append({"field": sig, "timestamp": str(t), "reason": "timestamp is in the future"})
            continue
        if r.quality is not None and _finite(r.quality) and float(r.quality) < suff["min_signal_quality"]:
            c["low_quality"] += 1
            out.remeasure.append({"signal": sig, "timestamp": str(t), "value": r.value,
                                  "reason": f"device signal quality {float(r.quality):.2f} below {suff['min_signal_quality']}"})
            continue
        if sig == "rhythm":
            if r.value is None or (isinstance(r.value, float) and math.isnan(r.value)):
                continue
            name = str(r.value).strip().lower()
            name = name if name in RHYTHMS else rhythm_group(str(r.value))
            rows.append({"timestamp": t, "signal": sig, "value": np.nan, "rhythm": name, "source": r.source,
                         "quality": r.quality})
            continue
        if not _finite(r.value):
            c["non_finite"] += 1
            out.invalid.append({"field": sig, "timestamp": str(t), "value": r.value, "reason": "missing or not a finite number"})
            continue
        units = WEARABLE_UNITS[sig]
        unit = r.unit
        if unit is None:
            unit = DEFAULT_UNIT[sig]
            c["unit_assumed"] += 1
        if unit not in units:
            c["unknown_unit"] += 1
            out.invalid.append({"field": sig, "timestamp": str(t), "unit": r.unit, "reason": "unsupported unit"})
            continue
        v = units[unit](float(r.value))
        lo, hi = RANGES[sig]
        if not lo <= v <= hi:
            c["impossible_value"] += 1
            out.invalid.append({"field": sig, "timestamp": str(t), "value": v,
                                "reason": f"physiologically impossible (outside {lo}-{hi}); excluded as in training"})
            continue
        p = plaus[sig]
        if v < p["low"] or v > p["high"]:
            c["flagged_for_remeasurement"] += 1
            out.remeasure.append({"signal": sig, "timestamp": str(t), "value": v,
                                  "reason": f"possible but unusual (outside {p['low']}-{p['high']}); kept, please re-measure"})
        rows.append({"timestamp": t, "signal": sig, "value": v, "rhythm": None, "source": r.source, "quality": r.quality})

    df = pd.DataFrame(rows, columns=["timestamp", "signal", "value", "rhythm", "source", "quality"])
    if len(df):
        before = len(df)
        df = df.drop_duplicates(subset=["timestamp", "signal", "value", "rhythm"])
        c["duplicates_removed"] = before - len(df)
        num = df[df.signal != "rhythm"]
        clash = num.groupby(["timestamp", "signal"]).value.nunique()
        for (ts, sig), n in clash[clash > 1].items():
            vals = num[(num.timestamp == ts) & (num.signal == sig)].value.tolist()
            out.conflicts.append({"signal": sig, "timestamp": str(ts), "values": vals,
                                  "handling": "averaged within the hour, as in training; flagged for review"})
        c["conflicts"] = len(out.conflicts)
        df = df.sort_values("timestamp").reset_index(drop=True)
        out.sources = sorted(df.source.dropna().astype(str).unique().tolist())
    if c["unit_assumed"]:
        out.warnings.append(f"{c['unit_assumed']} readings had no unit; the standard unit was assumed.")
    if c["timezone_assumed"]:
        out.warnings.append(f"{c['timezone_assumed']} readings had no time zone; {tz} was assumed.")
    c["valid"] = int(len(df))
    out.readings, out.counts = df, c
    return out


MGDL_NAMES = {"ldl", "hdl", "total_chol", "non_hdl", "triglycerides"}
