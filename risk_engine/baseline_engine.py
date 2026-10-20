# Personal digital-twin baseline and early-warning trend detector. Uses only valid, timestamped readings taken before
# the evaluation time. A baseline that has too few readings is reported as UNRELIABLE or UNAVAILABLE, never invented.
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from .input_validator import WATCH_SIGNALS
from .schemas import BaselineBlock, BaselineStatus

NAMES = {"hr": "Heart rate", "spo2": "Oxygen saturation", "rr": "Breathing rate"}
UNITS = {"hr": "bpm", "spo2": "%", "rr": "breaths/min"}


def _hourly(readings: pd.DataFrame, sig: str, now: pd.Timestamp) -> pd.Series:
    d = readings[(readings.signal == sig) & (readings.timestamp <= now)]
    if not len(d):
        return pd.Series(dtype=float)
    return d.groupby(d.timestamp.dt.floor("h")).value.mean()


def evaluate(readings: pd.DataFrame, now: pd.Timestamp, cfg: dict, external: dict | None = None) -> BaselineBlock:
    """external: optional longer-term baseline from the caller, {signal: {median, mad, n_hours, from, to}}. It is used
    only for signals whose baseline cannot be built from the readings received, and is labelled as provided."""
    bc, tc = cfg["baseline"], cfg["trends"]
    start = now - pd.Timedelta(days=bc["lookback_days"])
    cut = now - pd.Timedelta(hours=bc["exclude_recent_hours"])
    out = BaselineBlock(window={"from": str(start), "to": str(cut), "excludes_last_hours": bc["exclude_recent_hours"],
                                "statistic": "median of hourly means of valid readings",
                                "min_samples_available": bc["min_samples_available"]})
    statuses = []
    if readings is None or not len(readings):
        out.status = BaselineStatus.UNAVAILABLE
        out.trends = {s: {"pattern": "NOT_ENOUGH_DATA"} for s in WATCH_SIGNALS}
        return out
    sustained_adverse = []
    for sig in WATCH_SIGNALS:
        h = _hourly(readings, sig, now)
        base = h[(h.index >= start) & (h.index < cut)]
        n = int(base.notna().sum())
        st = (BaselineStatus.AVAILABLE if n >= bc["min_samples_available"] else
              BaselineStatus.UNRELIABLE if n >= bc["min_samples_unreliable"] else BaselineStatus.UNAVAILABLE)
        statuses.append(st)
        info: dict[str, Any] = {"status": st.value, "n_hours": n, "source": "readings received"}
        med = mad = None
        ext = (external or {}).get(sig)
        if st == BaselineStatus.UNAVAILABLE and ext and ext.get("median") is not None and int(ext.get("n_hours", 0)) >= bc["min_samples_unreliable"] \
                and (ext.get("to") is None or pd.Timestamp(ext["to"]) <= now):
            n = int(ext["n_hours"])
            st = BaselineStatus.AVAILABLE if n >= bc["min_samples_available"] else BaselineStatus.UNRELIABLE
            statuses[-1] = st
            med, mad = float(ext["median"]), float(ext.get("mad") or 0.0)
            info.update(status=st.value, n_hours=n, source="provided by caller", median=round(med, 1), mad=round(mad, 2),
                        first=str(ext.get("from")), last=str(ext.get("to")),
                        confidence="HIGH" if n >= bc["high_confidence_samples"] else "MEDIUM" if st == BaselineStatus.AVAILABLE else "LOW")
        elif st != BaselineStatus.UNAVAILABLE:
            med = float(base.median())
            mad = float((base - med).abs().median())
            info.update(median=round(med, 1), mad=round(mad, 2), first=str(base.index.min()), last=str(base.index.max()),
                        confidence="HIGH" if n >= bc["high_confidence_samples"] else "MEDIUM" if st == BaselineStatus.AVAILABLE else "LOW")
        out.by_signal[sig] = info

        # ---- deviation of the latest valid hour from the patient's own baseline
        if len(h) and med is not None:
            latest_t, latest_v = h.index.max(), float(h.loc[h.index.max()])
            delta = latest_v - med
            scale = 1.4826 * mad if mad and mad > 0 else None
            out.deviations.append({
                "signal": sig, "latest_value": round(latest_v, 1), "latest_hour": str(latest_t), "unit": UNITS[sig],
                "baseline_median": round(med, 1), "deviation": round(delta, 1),
                "robust_z": None if scale is None else round(delta / scale, 2), "baseline_status": st.value,
                "baseline_hours": n, "baseline_from": info["first"], "baseline_to": info["last"],
                "text": (f"{NAMES[sig]} is {abs(delta):.0f} {UNITS[sig]} {'above' if delta > 0 else 'below'} this patient's "
                         f"baseline (median {med:.0f} {UNITS[sig]} of {n} hourly readings, {info['first']} to {info['last']})"
                         if abs(delta) >= 0.5 else f"{NAMES[sig]} is at this patient's baseline (median {med:.0f} {UNITS[sig]} of {n} hourly readings)")
                        + f". Latest reading hour: {latest_t}." + (" Baseline is UNRELIABLE (few readings)." if st == BaselineStatus.UNRELIABLE else "")})

        # ---- trend: sustained vs transient change over the recent window
        recent = h[h.index > now - pd.Timedelta(hours=tc["recent_hours"])]
        ref, ref_name = (med, "personal baseline") if med is not None else (None, None)
        if ref is None:
            earlier = h[(h.index <= now - pd.Timedelta(hours=tc["recent_hours"])) & (h.index > now - pd.Timedelta(hours=24))]
            if earlier.notna().sum() >= 3:
                ref, ref_name = float(earlier.median()), "earlier hours of the last 24 h"
        thr, adv = tc["thresholds"][sig], tc["adverse_direction"][sig]
        t: dict[str, Any] = {"window_hours": tc["recent_hours"], "valid_hours": int(recent.notna().sum()),
                             "threshold": thr, "unit": UNITS[sig], "reference": ref_name,
                             "reference_value": None if ref is None else round(ref, 1)}
        if ref is None or recent.notna().sum() < 2:
            t["pattern"] = "NOT_ENOUGH_DATA"
        else:
            dev = (recent - ref) * adv
            beyond = dev >= thr
            t["hours_beyond_threshold"] = int(beyond.sum())
            t["latest_beyond"] = bool(beyond.iloc[-1])
            if beyond.sum() >= tc["sustained_min_hours"]:
                t["pattern"] = "SUSTAINED"
            elif beyond.iloc[-1] and beyond.sum() == 1:
                t["pattern"] = "TRANSIENT"
            elif beyond.any():
                t["pattern"] = "INTERMITTENT"
            else:
                t["pattern"] = "NONE"
            if recent.notna().sum() >= 3:
                x = (recent.index - recent.index[0]).total_seconds() / 3600
                t["slope_per_hour"] = round(float(np.polyfit(x, recent.values, 1)[0]), 2)
            t["direction_meaning"] = f"adverse = {'higher' if adv > 0 else 'lower'} {NAMES[sig].lower()}"
            if t["pattern"] == "SUSTAINED":
                sustained_adverse.append(sig)
        out.trends[sig] = t

    out.status = (BaselineStatus.AVAILABLE if all(s == BaselineStatus.AVAILABLE for s in statuses) else
                  BaselineStatus.UNRELIABLE if any(s != BaselineStatus.UNAVAILABLE for s in statuses) else BaselineStatus.UNAVAILABLE)
    confs = [out.by_signal[s].get("confidence") for s in WATCH_SIGNALS if out.by_signal[s].get("confidence")]
    out.confidence = min(confs, key=["LOW", "MEDIUM", "HIGH"].index) if confs else None
    out.corroboration = {
        "signals_with_sustained_adverse_change": sustained_adverse, "count": len(sustained_adverse),
        "corroborated": len(sustained_adverse) >= tc["corroboration_min_signals"],
        "note": ("Independent signals changing together make a warning more credible. This check never delays an "
                 "emergency alert: emergency rules are evaluated separately and first.")}
    return out
