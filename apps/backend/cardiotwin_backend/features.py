# Rolling features against the personal baseline, for every stored reading type (not only the model inputs).
# Windows look backward from `now` only. The baseline is the median of hourly means over the last 7 days, excluding the
# most recent 6 hours (the same rule as the risk engine's baseline_engine), with a robust spread (MAD). Too few hours
# give UNRELIABLE or UNAVAILABLE, never an invented baseline. These features are descriptive: the risk score comes only
# from the risk engine.
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

NUMERIC = ("hr", "hrv_rmssd", "spo2", "resp", "ecg_qtc")
WINDOWS_H = (1, 6, 24)
ENGINE_SIGNAL = {"hr": "hr", "spo2": "spo2", "resp": "rr"}


def frame(rows: list[dict]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=["ts", "type", "value_num", "value_text"])
    df = pd.DataFrame(rows)
    df["ts"] = pd.to_datetime(df["ts"], utc=True).dt.tz_localize(None)
    return df


def compute(rows: list[dict], now: pd.Timestamp, cfg: dict) -> dict[str, Any]:
    bc = cfg["baseline"]
    now = pd.Timestamp(now)
    now = now.tz_convert("UTC").tz_localize(None) if now.tzinfo is not None else now
    df = frame(rows)
    df = df[df.ts <= now] if len(df) else df
    start, cut = now - pd.Timedelta(days=bc["lookback_days"]), now - pd.Timedelta(hours=bc["exclude_recent_hours"])
    out: dict[str, Any] = {"now": str(now), "baseline_window": {"from": str(start), "to": str(cut),
                                                                 "statistic": "median of hourly means; spread = MAD"},
                           "signals": {}}
    for t in NUMERIC:
        d = df[df.type == t] if len(df) else df
        s: dict[str, Any] = {"n_total": int(len(d))}
        if len(d):
            last = d.sort_values("ts").iloc[-1]
            s["last"] = {"value": float(last.value_num), "at": str(last.ts), "minutes_old": round((now - last.ts).total_seconds() / 60, 1)}
        for w in WINDOWS_H:
            x = d[d.ts > now - pd.Timedelta(hours=w)].value_num.astype(float) if len(d) else pd.Series(dtype=float)
            s[f"{w}h"] = None if x.empty else {"mean": round(float(x.mean()), 2), "min": float(x.min()), "max": float(x.max()),
                                               "sd": round(float(x.std(ddof=0)), 2), "n": int(len(x))}
        hourly = d[(d.ts >= start) & (d.ts < cut)].groupby(d.ts.dt.floor("h")).value_num.mean() if len(d) else pd.Series(dtype=float)
        n = int(hourly.notna().sum())
        status = ("AVAILABLE" if n >= bc["min_samples_available"] else "UNRELIABLE" if n >= bc["min_samples_unreliable"] else "UNAVAILABLE")
        base = {"status": status, "n_hours": n}
        if n:
            med = float(hourly.median())
            mad = float((hourly - med).abs().median())
            base.update(median=round(med, 2), mad=round(mad, 2), first_hour=str(hourly.index.min()), last_hour=str(hourly.index.max()))
            recent = s.get("1h")
            if recent and status != "UNAVAILABLE":
                spread = max(1.4826 * mad, 1e-6)
                base["deviation_1h"] = round(recent["mean"] - med, 2)
                base["robust_z_1h"] = round((recent["mean"] - med) / spread, 2) if mad > 0 else None
        s["baseline"] = base
        out["signals"][t] = s
    # steps and sleep are summaries, not baselined values
    st = df[df.type == "steps"] if len(df) else df
    out["signals"]["steps"] = {w: (int(st[st.ts > now - pd.Timedelta(hours=int(w[:-1]))].value_num.sum()) if len(st) else 0)
                               for w in ("1h", "24h")}
    sl = df[(df.type == "sleep_stage") & (df.ts > now - pd.Timedelta(hours=24))] if len(df) else df
    out["signals"]["sleep_stage_24h"] = {} if not len(sl) else {k: int(v) for k, v in sl.value_text.value_counts().items()}
    rh = df[df.type == "rhythm"].sort_values("ts") if len(df) else df
    out["signals"]["rhythm"] = None if not len(rh) else {"last": rh.iloc[-1].value_text, "at": str(rh.iloc[-1].ts),
                                                         "af_readings_6h": int(((rh.ts > now - pd.Timedelta(hours=6)) &
                                                                                (rh.value_text == "af_flutter")).sum())}
    return out


def engine_baseline(features: dict) -> dict | None:
    """The risk engine's optional external baseline {signal: {median, mad, n_hours, from, to}} for hr, spo2 and rr."""
    out = {}
    for t, sig in ENGINE_SIGNAL.items():
        b = features["signals"].get(t, {}).get("baseline", {})
        if b.get("status") in ("AVAILABLE", "UNRELIABLE") and b.get("median") is not None:
            out[sig] = {"median": b["median"], "mad": b["mad"], "n_hours": b["n_hours"],
                        "from": features["baseline_window"]["from"], "to": features["baseline_window"]["to"]}
    return out or None
