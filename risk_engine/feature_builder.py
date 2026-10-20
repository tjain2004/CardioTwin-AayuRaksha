# Feature builder: validated readings + profile -> the exact feature vector the saved watch model was trained on.
# It reuses twin_preprocess (the training feature code) and the same rules as training: hourly means, physiologic
# limits, forward fill of at most 4 hours, rhythm = latest rhythm at or before the hour within 4 h, and rolling
# windows over the current and earlier hours only (no look-ahead, so no leakage).
from __future__ import annotations

import numpy as np
import pandas as pd

from twin_preprocess import FFILL_LIMIT, RHYTHMS, add_time_features

from .input_validator import WATCH_SIGNALS, ValidatedInput

RHYTHM_TOLERANCE_H = 4


def hourly_table(readings: pd.DataFrame, now: pd.Timestamp, hist_af: float | None = 0.0) -> pd.DataFrame:
    """Long validated readings -> one row per hour (from the first reading to the hour that contains `now`)."""
    if readings is None or not len(readings):
        return pd.DataFrame()
    df = readings.copy()
    df["hour"] = df.timestamp.dt.floor("h")
    num = df[df.signal.isin(WATCH_SIGNALS)]
    wide = num.pivot_table(index="hour", columns="signal", values="value", aggfunc="mean")
    last_hour = (now - pd.Timedelta(microseconds=1)).floor("h")
    grid = pd.DataFrame({"hour": pd.date_range(df.hour.min(), max(last_hour, df.hour.min()), freq="h")})
    h = grid.merge(wide.reset_index(), on="hour", how="left")
    for c in WATCH_SIGNALS:
        if c not in h:
            h[c] = np.nan
    rh = df[df.signal == "rhythm"][["timestamp", "rhythm"]].rename(columns={"timestamp": "hour"}).sort_values("hour", kind="stable")
    h = pd.merge_asof(h.sort_values("hour"), rh, on="hour", direction="backward",
                      tolerance=pd.Timedelta(hours=RHYTHM_TOLERANCE_H)) if len(rh) else h.assign(rhythm=np.nan)
    h["stay_id"] = 0
    for c in WATCH_SIGNALS:
        h[f"{c}_measured"] = h[c].notna().astype(int)
        h[c] = h[c].ffill(limit=FFILL_LIMIT[c])
    h["rhythm_group"] = h.rhythm.fillna("unknown")
    for r in RHYTHMS:
        h[f"rhythm_{r}"] = (h.rhythm_group == r).astype(int)
    for c in ["sbp", "map", "news_score", "shock_index"]:      # not measured by the watch; add_time_features expects them
        h[c] = np.nan
    h = add_time_features(h)
    g = h.groupby("stay_id")

    def roll(col, w, how="sum"):
        return g[col].transform(lambda x: x.rolling(w, min_periods=1).agg(how))

    h["af_burden_24h"] = roll("rhythm_af_flutter", 24)
    h["af_burden_pct_24h"] = (h.af_burden_24h / g.cumcount().add(1).clip(upper=24)).round(3)
    af_first = (h.rhythm_af_flutter == 1) & (g.rhythm_af_flutter.cummax().groupby(h.stay_id).shift(1).fillna(0) == 0)
    h["af_new_onset"] = (af_first & (float(hist_af or 0) == 0) & (g.cumcount() > 0)).astype(int)
    h["brady_burden_24h"] = roll("rhythm_brady", 24)
    h["tachy_burden_24h"] = roll("rhythm_tachy", 24)
    h["_change"] = (h.rhythm_group != g.rhythm_group.shift(1)).astype(int).where(g.cumcount() > 0, 0)
    h["rhythm_changes_24h"] = roll("_change", 24)
    h["hr_sd_24h"] = roll("hr", 24, "std")
    h["hr_trend_24h"] = roll("hr", 6, "mean") - g.hr.transform(lambda x: x.rolling(6, min_periods=1).mean().shift(18))
    night, day = h.hour.dt.hour < 6, h.hour.dt.hour.between(8, 20)
    h["_rr_night"], h["_rr_day"] = h.rr.where(night), h.rr.where(day)
    h["rr_night_minus_day_24h"] = roll("_rr_night", 24, "mean") - roll("_rr_day", 24, "mean")
    h["_dip"] = ((h.spo2 < 90) & (h.spo2_measured == 1)).astype(int)
    h["spo2_dip_hours_24h"] = roll("_dip", 24)
    h["_dip_night"] = h._dip.where(night, 0)
    h["spo2_night_dip_hours_24h"] = roll("_dip_night", 24)
    return h.drop(columns=[c for c in h.columns if c.startswith("_")]).reset_index(drop=True)


def static_values(v: ValidatedInput) -> dict:
    """Profile + clinical values in training units. Unknown values stay NaN (the model's fitted imputer fills them)."""
    p = {k: (np.nan if val is None else val) for k, val in v.profile.items()
         if k not in ("patient_id", "reported_symptoms", "is_demo")}
    p["bmi_missing"] = float(np.isnan(p.get("bmi", np.nan)))
    for k, c in v.clinical.items():
        p[k] = c["value"]
    return p


def feature_row(table: pd.DataFrame, statics: dict, features: list[str], at: int = -1) -> pd.DataFrame:
    """One model row (exact training columns and order) for hour `at` of the hourly table."""
    row = dict(statics)
    last = table.iloc[at]
    for k in table.columns:
        if k in features:
            row[k] = last[k]
    return pd.DataFrame([row]).reindex(columns=features).apply(pd.to_numeric, errors="coerce").astype(float)


def feature_matrix(table: pd.DataFrame, statics: dict, features: list[str]) -> pd.DataFrame:
    """Model rows for every hour of the table (used for the risk trajectory)."""
    X = table.reindex(columns=features).copy()
    for k, val in statics.items():
        if k in features:
            X[k] = val
    return X.apply(pd.to_numeric, errors="coerce").astype(float)
