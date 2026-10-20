# Reading-level digital twin. Instead of editing model inputs, it edits the raw readings (watch HR / SpO2 / RR,
# profile, clinical values) and rebuilds every model input with the risk engine's own validator and feature builder,
# which reuse the training feature code (twin_preprocess). Derived inputs (6 h means, 24 h SD, trends, change vs the
# patient's own normal) are therefore exact for the edited history. It also gives the hour-by-hour risk trajectory.
from __future__ import annotations

import copy
from typing import Any

import numpy as np
import pandas as pd

from .artifacts import Artifacts
from .patients import SYNTHETIC_SOURCE, PatientCase

SIGNALS = ("hr", "spo2", "rr")
UNITS = {"hr": "bpm", "spo2": "%", "rr": "breaths/min"}
LIMITS = {"hr": (20, 250), "spo2": (50, 100), "rr": (4, 60)}


def _ts(x) -> pd.Timestamp:
    t = pd.Timestamp(x)
    return t.tz_convert("UTC").tz_localize(None) if t.tzinfo is not None else t


def synthetic_request(now: str = "2026-10-18T12:00:00", hours: int = 48, seed: int = 7, profile: str = "deteriorating") -> dict:
    """A SYNTHETIC demonstration patient: 48 h of watch readings every 15 min with a circadian rhythm, sensor noise and
    (profile='deteriorating') a gradual rise in HR and RR with a small SpO2 drift over the last 12 h. Labelled synthetic
    in every field; never used for training."""
    rng = np.random.default_rng(seed)
    now = _ts(now)
    t = pd.date_range(now - pd.Timedelta(hours=hours), now - pd.Timedelta(minutes=5), freq="15min")
    h = (t.hour + t.minute / 60).to_numpy()
    circ = np.sin((h - 16) / 24 * 2 * np.pi)                    # peak late afternoon, trough early morning
    ramp = np.clip((t - (now - pd.Timedelta(hours=12))) / pd.Timedelta(hours=12), 0, 1) if profile == "deteriorating" else 0 * h
    ramp = np.asarray(ramp, float)
    hr = 82 + 6 * circ + 22 * ramp + rng.normal(0, 3, len(t))
    rr = 17 + 1.0 * circ + 7 * ramp + rng.normal(0, 1.0, len(t))
    spo2 = 96.5 - 0.3 * circ - 3.0 * ramp + rng.normal(0, 0.6, len(t))
    readings = []
    for i, ts in enumerate(t):
        for s, arr in (("hr", hr), ("spo2", spo2), ("rr", rr)):
            readings.append({"timestamp": ts.isoformat() + "Z", "signal": s, "value": round(float(np.clip(arr[i], *LIMITS[s])), 1),
                             "unit": UNITS[s], "source": SYNTHETIC_SOURCE, "quality": 0.95})
        if i % 4 == 0:
            readings.append({"timestamp": ts.isoformat() + "Z", "signal": "rhythm",
                             "value": "tachy" if hr[i] > 100 else "sinus", "source": SYNTHETIC_SOURCE})
    prof = {"patient_id": "synthetic-demo-001", "age_at_admit": 71, "gender_male": 1, "smoker": 1, "height_in": 69,
            "weight_lbs": 205, "bmi": 30.3, "log_prior_admissions": 0.69, "days_since_last_discharge": 120,
            "n_active_drugs": 9, "ix_bleeding": 0, "has_bled_partial": 2, "ascvd_10y_risk": 0.28, "hist_af": 0, "is_demo": True}
    clin = [{"name": n, "value": v, "unit": u, "timestamp": "2026-08-01T09:00:00Z", "source": SYNTHETIC_SOURCE}
            for n, v, u in [("total_chol", 215, "mg/dL"), ("ldl", 138, "mg/dL"), ("non_hdl", 173, "mg/dL"),
                            ("tc_hdl_ratio", 5.1, "ratio"), ("triglycerides", 180, "mg/dL"), ("hba1c", 6.6, "%"),
                            ("egfr", 68, "mL/min/1.73m2"), ("sbp_outpatient", 142, "mmHg"), ("dbp_outpatient", 86, "mmHg")]]
    return {"patient_profile": prof, "clinical_readings": clin, "wearable_readings": readings, "now": now.isoformat(),
            "label": SYNTHETIC_SOURCE}


class ReadingTwin:
    def __init__(self, art: Artifacts, model):
        self.art, self.model = art, model           # model: the watch TwinModel (the reading path is watch-only)
        self.V, self.F = art.engine["input_validator"], art.engine["feature_builder"]

    def features(self, req: dict) -> tuple[dict, pd.DataFrame, dict]:
        now = _ts(req["now"])
        v = self.V.validate(req["patient_profile"], req.get("clinical_readings"), req.get("wearable_readings"), now, self.art.cfg)
        table = self.F.hourly_table(v.readings, now, v.profile.get("hist_af"))
        statics = self.F.static_values(v)
        X = self.F.feature_row(table, statics, self.model.features)
        return {f: float(X.iloc[0][f]) for f in self.model.features}, table, statics

    def trajectory(self, req: dict, hours: int = 24) -> pd.DataFrame:
        _, table, statics = self.features(req)
        X = self.F.feature_matrix(table, statics, self.model.features).iloc[-hours:]
        p = self.model.prob(X)
        return pd.DataFrame({"hour": table.hour.iloc[-hours:].values, "prob": p,
                             "tier": [self.model.tier(x) for x in p], "hr": table.hr.iloc[-hours:].values,
                             "spo2": table.spo2.iloc[-hours:].values, "rr": table.rr.iloc[-hours:].values})

    def case(self, req: dict, case_id: str = "synthetic-demo", label: str = "Synthetic demonstration patient") -> PatientCase:
        vals, _, _ = self.features(req)
        full = {f: np.nan for f in self.art.bundles["full"]["features"]}
        return PatientCase(case_id, label, req.get("label", SYNTHETIC_SOURCE), True, {**full, **vals}, ["watch"],
                           hour=str(_ts(req["now"]) - pd.Timedelta(hours=1)), meta={"synthetic": True}, request=copy.deepcopy(req))

    @staticmethod
    def edit(req: dict, edits: list[dict]) -> dict:
        """Return an edited deep copy. Each edit is one of
            {"signal": "hr", "delta": -10, "hours": 6}          shift readings in the last `hours`
            {"signal": "spo2", "value": 97, "hours": 6}          set readings in the last `hours`
            {"signal": "spo2", "floor": 92, "hours": 24}         remove dips below a floor
            {"profile": "smoker", "value": 0}                    profile field
            {"clinical": "ldl", "value": 100, "unit": "mg/dL"}   clinical value
        The caller's request is never modified."""
        out = copy.deepcopy(req)
        now = _ts(out["now"])
        for e in edits:
            if "signal" in e:
                start = now - pd.Timedelta(hours=e.get("hours", 6))
                lo, hi = LIMITS[e["signal"]]
                for r in out["wearable_readings"]:
                    if r["signal"] != e["signal"] or _ts(r["timestamp"]) < start:
                        continue
                    x = r["value"]
                    x = x + e["delta"] if "delta" in e else e["value"] if "value" in e else max(x, e["floor"])
                    r["value"] = float(np.clip(x, lo, hi))
                    r["source"] = f"{r.get('source', '')} | what-if edit (hypothetical)"
            elif "profile" in e:
                out["patient_profile"][e["profile"]] = e["value"]
            elif "clinical" in e:
                out["clinical_readings"] = [c for c in out.get("clinical_readings") or [] if c["name"] != e["clinical"]] + [
                    {"name": e["clinical"], "value": float(e["value"]), "unit": e.get("unit", "mg/dL"),
                     "timestamp": None, "source": "what-if (hypothetical)"}]
        return out

    def compare(self, req: dict, edits: list[dict], hours: int = 24) -> dict[str, Any]:
        hyp = self.edit(req, edits)
        b_vals, _, _ = self.features(req)
        s_vals, _, _ = self.features(hyp)
        tb, ts = self.trajectory(req, hours), self.trajectory(hyp, hours)
        return {"baseline_values": b_vals, "scenario_values": s_vals, "trajectory_baseline": tb, "trajectory_scenario": ts,
                "request_scenario": hyp, "edits": edits}
