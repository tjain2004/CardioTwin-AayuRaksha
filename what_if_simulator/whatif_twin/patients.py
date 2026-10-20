# Patient cases for the simulator.
#   * Test patients: the 19 held-out test stays (15 patients) of the MIMIC-IV demo split used by notebooks 05-07.
#     Source 1 (always available): notebook 06's patient explanation records, which hold every model input of both
#     models at each stay's last test hour (verified to reproduce the saved risks exactly).
#     Source 2 (laptop): data/features/model_final.csv, which gives every test hour of every stay.
#   * A synthetic demonstration patient, clearly labelled, built from synthetic watch readings through the engine's
#     own feature code (readings_twin.py). It is never used for training or presented as a real patient.
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from .artifacts import Artifacts

TEST_SOURCE = "MIMIC-IV demo, held-out test split (de-identified, open-access PhysioNet demo data)"
SYNTHETIC_SOURCE = "SYNTHETIC DEMONSTRATION PATIENT (generated readings; not a real person)"


@dataclass
class PatientCase:
    case_id: str
    label: str
    source: str
    is_synthetic: bool
    values: dict[str, float]                  # union of both models' inputs at this hour (NaN = not recorded)
    models: list[str]                         # models that can score this case
    hour: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)   # outcome etc. (shown in technical mode only)
    request: dict | None = None               # raw-readings request (reading-level twin), when available

    def summary(self) -> dict[str, Any]:
        return {"case_id": self.case_id, "label": self.label, "source": self.source, "synthetic": self.is_synthetic,
                "hour": self.hour, "models": self.models}


class PatientRegistry:
    def __init__(self, art: Artifacts):
        self.art = art
        self.feats = {s: list(b["features"]) for s, b in art.bundles.items()}
        self.union = list(dict.fromkeys(self.feats["watch"] + self.feats["full"]))
        self.cases: dict[str, PatientCase] = {}
        self.test_table: pd.DataFrame | None = None
        self._load_snapshots()
        self._load_full_table()

    # ---------------------------------------------------------------- loading
    def _load_snapshots(self) -> None:
        exp = self.art.paths.explanations
        if exp is None or not (exp / "patient_explanation_records.csv").exists():
            return
        rec = pd.read_csv(exp / "patient_explanation_records.csv")
        tp = pd.read_csv(self.art.paths.results05 / "test_predictions.csv", parse_dates=["hour"])
        for sid, g in rec.groupby("stay_id"):
            vals = {f: np.nan for f in self.union}
            for r in g.itertuples():
                vals[r.feature] = float(r.value) if pd.notna(r.value) else np.nan
            saved = g.groupby("model").saved_risk.first().to_dict()
            t = tp[tp.stay_id == sid]
            m = t[(t.risk_watch.sub(saved.get("watch", np.nan)).abs() < 6e-5) & (t.risk_full.sub(saved.get("full", np.nan)).abs() < 6e-5)]
            row = m.iloc[-1] if len(m) else t.iloc[-1]
            self.cases[f"stay-{sid}"] = PatientCase(
                case_id=f"stay-{sid}", label=f"Test stay {sid} (patient {int(row.subject_id)})", source=TEST_SOURCE,
                is_synthetic=False, values=vals, models=[s for s in ("watch", "full") if s in saved], hour=str(row.hour),
                meta={"stay_id": int(sid), "subject_id": int(row.subject_id), "snapshot": "last test hour of the stay",
                      "event_within_24h": int(row.y_24h), "hours_to_event": None if pd.isna(row.hours_to_event) else float(row.hours_to_event),
                      "saved_risk": saved, "n_test_hours": int(len(t))})

    def _load_full_table(self) -> None:
        p = self.art.paths.features_table
        if p is None:
            return
        mf = pd.read_csv(p, parse_dates=["hour"])
        tp = pd.read_csv(self.art.paths.results05 / "test_predictions.csv", parse_dates=["hour"])
        self.test_table = tp[["subject_id", "stay_id", "hour", "y_24h", "hours_to_event"]].merge(
            mf, on=["subject_id", "stay_id", "hour"], how="left", suffixes=("", "_mf"))

    # ---------------------------------------------------------------- access
    def list(self) -> pd.DataFrame:
        return pd.DataFrame([c.summary() | {k: c.meta.get(k) for k in ("event_within_24h", "n_test_hours")} for c in self.cases.values()])

    def ids(self, include_synthetic: bool = True) -> list[str]:
        return [k for k, c in self.cases.items() if include_synthetic or not c.is_synthetic]

    def get(self, case_id: str) -> PatientCase:
        if case_id not in self.cases:
            raise KeyError(f"Unknown case '{case_id}'. Available: {', '.join(self.cases)}")
        return self.cases[case_id]

    def at_hour(self, stay_id: int, hour) -> PatientCase:
        """Any test hour (needs the feature table, i.e. the full project folder)."""
        if self.test_table is None:
            raise FileNotFoundError("data/features/model_final.csv is not available here; only each stay's last "
                                    "test hour (the notebook 06 snapshot) can be simulated.")
        hour = pd.Timestamp(hour)
        r = self.test_table[(self.test_table.stay_id == stay_id) & (self.test_table.hour == hour)]
        if r.empty:
            raise KeyError(f"No test hour {hour} for stay {stay_id}")
        r = r.iloc[0]
        vals = {f: (float(r[f]) if f in r and pd.notna(r[f]) else np.nan) for f in self.union}
        cid = f"stay-{stay_id}@{hour:%Y-%m-%dT%H}"
        case = PatientCase(cid, f"Test stay {stay_id} at {hour}", TEST_SOURCE, False, vals, ["watch", "full"], str(hour),
                           meta={"stay_id": int(stay_id), "event_within_24h": int(r.y_24h)})
        self.cases[cid] = case
        return case

    def add(self, case: PatientCase) -> PatientCase:
        self.cases[case.case_id] = case
        return case
