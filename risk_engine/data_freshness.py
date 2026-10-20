# Freshness and sufficiency gate: runs before the model. Per-signal age, coverage, gaps and sync time, then a
# single decision on whether a prediction is allowed. Insufficient data is never turned into a LOW result.
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from .input_validator import WATCH_SIGNALS, ValidatedInput
from .schemas import SignalFreshness, SignalStatus


@dataclass
class Gate:
    allowed: bool
    reason_code: str | None
    reason: str | None
    freshness: dict[str, SignalFreshness]
    coverage_pct: float | None
    quality_weighted_coverage_pct: float | None
    last_sync: pd.Timestamp | None
    minutes_since_sync: float | None
    history_hours: float | None
    missing: list[str] = field(default_factory=list)
    stale: list[str] = field(default_factory=list)
    invalid: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _minutes(a, b) -> float:
    return round((a - b).total_seconds() / 60, 1)


def check(v: ValidatedInput, now: pd.Timestamp, cfg: dict) -> Gate:
    fcfg, suff = cfg["freshness"], cfg["sufficiency"]
    win = pd.Timedelta(hours=suff["coverage_window_hours"])
    df = v.readings
    invalid_sigs = sorted({i["field"] for i in v.invalid if i["field"] in WATCH_SIGNALS + ("rhythm",)})
    fr: dict[str, SignalFreshness] = {}
    cov_parts, qcov_parts = [], []
    for sig in list(WATCH_SIGNALS) + ["rhythm"]:
        rule = fcfg[sig]
        d = df[df.signal == sig] if len(df) else df
        if not len(d):
            status = SignalStatus.INVALID if sig in invalid_sigs else SignalStatus.MISSING
            fr[sig] = SignalFreshness(status=status, max_age_minutes=rule["max_age_minutes"], mandatory=rule["mandatory"],
                                      coverage_pct_24h=0.0)
            if sig != "rhythm":
                cov_parts.append(0.0); qcov_parts.append(0.0)
            continue
        last = d.timestamp.max()
        age = _minutes(now, last)
        recent = d[d.timestamp > now - win]
        hours = recent.timestamp.dt.floor("h").nunique()
        first = df.timestamp.min()
        expected = max(1, min(suff["coverage_window_hours"], int(round((now - first).total_seconds() / 3600))))
        cov = round(100 * min(hours, expected) / expected, 1)
        q = recent.quality.astype(float)
        mq = float(q.mean()) if q.notna().any() else None
        gaps = d.timestamp.sort_values().diff().dt.total_seconds().div(60)
        fr[sig] = SignalFreshness(
            status=SignalStatus.FRESH if age <= rule["max_age_minutes"] else SignalStatus.STALE,
            last_valid_reading=str(last), age_minutes=age, max_age_minutes=rule["max_age_minutes"], mandatory=rule["mandatory"],
            coverage_pct_24h=cov, source=", ".join(sorted(d.source.dropna().astype(str).unique())) or "not stated",
            mean_quality=None if mq is None else round(mq, 2),
            largest_gap_minutes=None if gaps.dropna().empty else round(float(max(gaps.max(), age)), 1))
        if sig != "rhythm":
            cov_parts.append(cov); qcov_parts.append(cov * (mq if mq is not None else 1.0))

    last_sync = df.timestamp.max() if len(df) else None
    since_sync = _minutes(now, last_sync) if last_sync is not None else None
    history_h = round((now - df.timestamp.min()).total_seconds() / 3600, 1) if len(df) else None
    g = Gate(allowed=True, reason_code=None, reason=None, freshness=fr,
             coverage_pct=round(sum(cov_parts) / len(cov_parts), 1) if cov_parts else None,
             quality_weighted_coverage_pct=round(sum(qcov_parts) / len(qcov_parts), 1) if qcov_parts else None,
             last_sync=last_sync, minutes_since_sync=since_sync, history_hours=history_h, invalid=invalid_sigs)
    g.missing = [s for s, f in fr.items() if f.status in (SignalStatus.MISSING, SignalStatus.INVALID) and f.mandatory]
    g.stale = [s for s, f in fr.items() if f.status == SignalStatus.STALE and f.mandatory]
    missing_profile = [k for k in suff["mandatory_profile"] if v.profile.get(k) is None]
    g.missing += missing_profile

    if since_sync is not None and since_sync > fcfg["wearable_sync_warning_minutes"]:
        g.warnings.append(f"The watch last sent data {since_sync:.0f} min ago (warning after {fcfg['wearable_sync_warning_minutes']} min).")
    for s, f in fr.items():
        if not f.mandatory and f.status != SignalStatus.FRESH:
            g.warnings.append(f"Optional signal '{s}' is {f.status.value.lower()}.")
    old = [k for k, c in v.clinical.items() if c["timestamp"] is not None
           and (now - c["timestamp"]).days > fcfg["clinical_max_age_days"]]
    if old:
        g.warnings.append(f"Clinical values older than {fcfg['clinical_max_age_days']} days: {', '.join(old)}.")
    undated = [k for k, c in v.clinical.items() if c["timestamp"] is None]
    if undated:
        g.warnings.append(f"Clinical values without a date in the source record: {', '.join(undated)}.")

    if not len(df):
        g.allowed, g.reason_code = False, "NO_VALID_READINGS"
        g.reason = "No valid watch readings were received" + (" (some readings were invalid)" if v.invalid else "") + "."
    elif [s for s in g.missing if s in WATCH_SIGNALS]:
        sigs = [s for s in g.missing if s in WATCH_SIGNALS]
        bad = [s for s in sigs if fr[s].status == SignalStatus.INVALID]
        g.allowed = False
        g.reason_code = "INVALID_DATA" if bad and len(bad) == len(sigs) else "MISSING_MANDATORY"
        g.reason = f"No valid reading for: {', '.join(sigs)}" + (" (the readings received were invalid)" if bad else "") + "."
    elif g.stale:
        g.allowed, g.reason_code = False, "STALE_DATA"
        g.reason = "Latest reading too old for: " + ", ".join(
            f"{s} ({fr[s].age_minutes:.0f} min, limit {fr[s].max_age_minutes:.0f} min)" for s in g.stale) + \
            ". The watch may be off the wrist or not synced."
    elif missing_profile:
        g.allowed, g.reason_code = False, "MISSING_MANDATORY"
        g.reason = f"Profile incomplete: {', '.join(missing_profile)}."
    elif history_h is not None and history_h < suff["min_history_hours"]:
        g.allowed, g.reason_code = False, "INSUFFICIENT_HISTORY"
        g.reason = f"Only {history_h:.1f} h of readings; at least {suff['min_history_hours']} h is needed."
    if g.allowed and history_h is not None and history_h < suff["short_history_warning_hours"]:
        g.warnings.append(f"Short history ({history_h:.1f} h): 6-hour and 24-hour trend inputs are partly filled in by the model.")
    return g


def summary(g: Gate) -> dict[str, Any]:
    return {"allowed": g.allowed, "reason_code": g.reason_code, "stale": g.stale, "missing": g.missing}
