# Feature functions shared by the training notebook (02_merge_and_clean) and the live digital twin.
# Every function only looks BACKWARD in time, so the same code works on a live stream, hour by hour.
import numpy as np
import pandas as pd

RANGES = {"hr": (20, 250), "spo2": (50, 100), "rr": (4, 60), "sbp": (50, 250),
          "dbp": (20, 150), "map": (30, 200), "temp_c": (30, 43), "gcs_total": (3, 15)}
FFILL_LIMIT = {"hr": 4, "spo2": 4, "rr": 4, "sbp": 4, "dbp": 4, "map": 4, "temp_c": 8, "gcs_total": 8}
RHYTHMS = ["af_flutter", "brady", "other", "paced", "sinus", "tachy", "unknown"]
TREND_COLS = ["hr", "map", "spo2", "rr", "sbp", "news_score", "shock_index"]


def clean_vitals(v):
    # raw hourly vitals (one or more ICU stays) -> cleaned, same rules as step 2c
    v = v[v.hours_since_icu_admit >= 0].copy()
    f = v.temp_c.between(86, 110)
    v.loc[f, "temp_c"] = (v.loc[f, "temp_c"] - 32) * 5 / 9
    for col, (lo, hi) in RANGES.items():
        v.loc[v[col].notna() & ~v[col].between(lo, hi), col] = np.nan
    v = v.sort_values(["stay_id", "hour"])
    for col, lim in FFILL_LIMIT.items():
        v[f"{col}_measured"] = v[col].notna().astype(int)
        v[col] = v.groupby("stay_id")[col].ffill(limit=lim)
    v["arterial_line"] = (v.groupby("stay_id").bp_source.ffill() == "arterial").astype(int)
    return v


def _band(x, cuts, pts):
    return np.select([x <= c for c in cuts], pts[:-1], pts[-1]).astype(float)


def add_scores(v):
    # shock index and NEWS2 (without the oxygen-therapy item)
    v = v.copy()
    v["shock_index"] = (v.hr / v.sbp).round(3)
    news = pd.DataFrame({
        "rr": _band(v.rr, [8, 11, 20, 24], [3, 1, 0, 2, 3]),
        "spo2": _band(v.spo2, [91, 93, 95], [3, 2, 1, 0]),
        "sbp": np.where(v.sbp >= 220, 3, _band(v.sbp, [90, 100, 110], [3, 2, 1, 0])),
        "hr": _band(v.hr, [40, 50, 90, 110, 130], [3, 1, 0, 1, 2, 3]),
        "temp": _band(v.temp_c, [35, 36, 38, 39], [3, 1, 0, 1, 2]),
        "gcs": np.where(v.gcs_total < 15, 3, 0)}, index=v.index)
    for col, src in [("rr", "rr"), ("spo2", "spo2"), ("sbp", "sbp"), ("hr", "hr"), ("temp", "temp_c"), ("gcs", "gcs_total")]:
        news.loc[v[src].isna(), col] = 0
    v["news_score"] = news.sum(axis=1).astype(int)
    return v


def rhythm_group(r):
    if pd.isna(r):
        return "unknown"
    r = r.strip().lower()
    return ("af_flutter" if r.startswith(("af ", "a flut")) else "paced" if "paced" in r else
            "tachy" if r.startswith(("st ", "svt", "pat", "vt ")) else "brady" if r.startswith(("sb ", "jr ")) else
            "sinus" if r.startswith(("sr ", "sa ", "1st av")) else "other")


def one_hot_rhythm(v):
    v = v.copy()
    g = v.rhythm.map(rhythm_group)
    for r in RHYTHMS:
        v[f"rhythm_{r}"] = (g == r).astype(int)
    return v.drop(columns=["rhythm", "bp_source"])


def add_time_features(t):
    # per ICU stay, using only the current and earlier hours
    t = t.sort_values(["stay_id", "hour"]).copy()
    for col in TREND_COLS:
        g = t.groupby("stay_id")[col]
        t[f"{col}_mean6h"] = g.transform(lambda x: x.rolling(6, min_periods=1).mean())
        t[f"{col}_min6h"] = g.transform(lambda x: x.rolling(6, min_periods=1).min())
        t[f"{col}_max6h"] = g.transform(lambda x: x.rolling(6, min_periods=1).max())
        t[f"{col}_delta6h"] = t[col] - g.shift(6)
        t[f"{col}_lag1"] = g.shift(1)
        t[f"{col}_vs_baseline"] = t[col] - g.transform(lambda x: x.expanding().median())   # vs the patient's own normal so far
    return t


def add_labs(t, lab, tolerance_h, log_labs=(), value=True, change=True):
    # lab: long table with hadm_id, time, item, v. Adds the latest value and the change since the previous result.
    lab = lab.sort_values("time").copy()
    lab["chg"] = lab.groupby(["hadm_id", "item"]).v.diff()
    t = t.sort_values("hour")
    for name, tol in tolerance_h.items():
        cols = {"time": "hour"}
        if value:
            cols["v"] = f"lab_log_{name}" if name in log_labs else f"lab_{name}"
        if change:
            cols["chg"] = f"lab_chg_{name}"
        l = lab[lab.item == name][["hadm_id"] + list(cols)].rename(columns=cols).sort_values("hour")
        t = pd.merge_asof(t, l, on="hour", by="hadm_id", direction="backward", tolerance=pd.Timedelta(hours=tol))
    return t


def build_features(vitals_raw, static_feats, labs=None, lab_tolerance_h=None, log_labs=(), extra=None):
    # one ICU stay: raw hourly vitals + static values (from the record or entered by the user) -> model features
    v = add_time_features(add_scores(one_hot_rhythm(clean_vitals(vitals_raw))))
    for k, val in static_feats.items():
        v[k] = val
    if labs is not None:
        v = add_labs(v, labs, lab_tolerance_h, log_labs)
    if extra is not None:
        v = v.merge(extra, on="hour", how="left")
    return v.sort_values("hour").reset_index(drop=True)


def to_matrix(t, config, scaled=False):
    # same columns, same order as training; optional scaling with the TRAIN mean/std
    X = t.reindex(columns=config["features"]).astype(float)
    if scaled:
        for col, (mu, sd) in config["scaler"].items():
            X[col] = (X[col] - mu) / sd
        X = X.fillna(0)
    return X
