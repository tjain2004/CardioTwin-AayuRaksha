# Turns the project's real data into engine requests, for the notebook and the integration tests. The vitals are
# MIMIC-IV demo ICU charted values (hourly), replayed AS IF they came from the watch: they are labelled as replay
# data, never presented as real watch readings.
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

REPLAY_SOURCE = "replay: MIMIC-IV demo ICU charted vitals (not real watch data)"
PROFILE_FIELDS = ["age_at_admit", "gender_male", "height_in", "weight_lbs", "bmi", "smoker", "log_prior_admissions",
                  "days_since_last_discharge", "n_active_drugs", "ix_bleeding", "has_bled_partial", "ascvd_10y_risk", "hist_af"]
CLINICAL_FIELDS = {"ldl": "mg/dL", "total_chol": "mg/dL", "non_hdl": "mg/dL", "triglycerides": "mg/dL", "hba1c": "%",
                   "egfr": "mL/min/1.73m2", "sbp_outpatient": "mmHg", "dbp_outpatient": "mmHg", "tc_hdl_ratio": "ratio"}
UNITS = {"hr": "bpm", "spo2": "%", "rr": "breaths/min"}


class ReplayData:
    def __init__(self, root: Path):
        root = Path(root)
        self.dyn = pd.read_csv(root / "data" / "cleaned" / "dynamic_clean.csv", parse_dates=["hour"])
        self.mf = pd.read_csv(root / "data" / "features" / "model_final.csv", parse_dates=["hour"])
        self.test = pd.read_csv(root / "results" / "test_predictions.csv", parse_dates=["hour"])

    def readings(self, stay_id: int, until: pd.Timestamp | None = None) -> list[dict]:
        """Measured values only (forward-filled hours are not readings); timestamps are the start of each hourly bin."""
        d = self.dyn[self.dyn.stay_id == stay_id].sort_values("hour")
        if until is not None:
            d = d[d.hour <= until]
        out = []
        for r in d.itertuples():
            for s in ("hr", "spo2", "rr"):
                if getattr(r, f"{s}_measured") == 1 and pd.notna(getattr(r, s)):
                    out.append({"timestamp": r.hour.isoformat(), "signal": s, "value": float(getattr(r, s)),
                                "unit": UNITS[s], "source": REPLAY_SOURCE})
            if r.rhythm_group != "unknown":
                out.append({"timestamp": r.hour.isoformat(), "signal": "rhythm", "value": r.rhythm_group, "source": REPLAY_SOURCE})
        return out

    def row(self, stay_id: int, hour: pd.Timestamp) -> pd.Series:
        return self.mf[(self.mf.stay_id == stay_id) & (self.mf.hour == hour)].iloc[0]

    def profile(self, stay_id: int, hour: pd.Timestamp) -> dict:
        r = self.row(stay_id, hour)
        p = {k: (None if pd.isna(r[k]) else float(r[k])) for k in PROFILE_FIELDS if k in r}
        p["bmi"] = None if r.get("bmi_missing", 0) == 1 else p.get("bmi")
        for k in ("gender_male", "smoker", "ix_bleeding", "hist_af"):
            if p.get(k) is not None:
                p[k] = int(p[k])
        return {"patient_id": f"mimic-subject-{int(r.subject_id)}", **p, "is_demo": True}

    def clinical(self, stay_id: int, hour: pd.Timestamp) -> list[dict]:
        r = self.row(stay_id, hour)
        return [{"name": k, "value": float(r[k]), "unit": u, "timestamp": None, "source": "MIMIC-IV demo record (undated in source)"}
                for k, u in CLINICAL_FIELDS.items() if k in r and pd.notna(r[k])]

    def request(self, stay_id: int, hour: pd.Timestamp) -> dict:
        """Everything the engine needs for one stay at one hour. `now` = end of that hourly bin."""
        hour = pd.Timestamp(hour)
        return {"patient_profile": self.profile(stay_id, hour), "clinical_readings": self.clinical(stay_id, hour),
                "wearable_readings": self.readings(stay_id, hour), "now": hour + pd.Timedelta(hours=1)}

    def test_stays(self) -> list[int]:
        return [int(s) for s in self.test.stay_id.unique()]

    def last_hour(self, stay_id: int) -> pd.Timestamp:
        return self.test[self.test.stay_id == stay_id].hour.max()
