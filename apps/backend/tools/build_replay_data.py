# Builds backend/data/replay/stay_<id>.json for the 19 held-out test stays, so the virtual watch simulator can replay a
# real test patient without the large data tables. The vitals are MIMIC-IV demo ICU charted values (hourly);
# the simulator replays them AS IF they came from a watch and labels every reading SIMULATED.
#
#   python tools/build_replay_data.py --project <Happiest_Health folder>     (needs data/cleaned/dynamic_clean.csv,
#                                                                             data/features/model_final.csv)
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

PROFILE_FIELDS = ["age_at_admit", "gender_male", "height_in", "weight_lbs", "bmi", "smoker", "log_prior_admissions",
                  "days_since_last_discharge", "n_active_drugs", "ix_bleeding", "has_bled_partial", "ascvd_10y_risk", "hist_af"]
CLINICAL_FIELDS = {"ldl": "mg/dL", "total_chol": "mg/dL", "non_hdl": "mg/dL", "triglycerides": "mg/dL", "hba1c": "%",
                   "egfr": "mL/min/1.73m2", "sbp_outpatient": "mmHg", "dbp_outpatient": "mmHg", "tc_hdl_ratio": "ratio"}
SOURCE = "replay: MIMIC-IV demo ICU charted vitals (not real watch data)"


def _first(root: Path, *cands: str) -> Path:
    for c in cands:
        if (root / c).exists():
            return root / c
    raise FileNotFoundError(f"None of {cands} under {root}")


def build(project: Path, out: Path, predictions: Path | None = None) -> list[int]:
    dyn = pd.read_csv(_first(project, "data/cleaned/dynamic_clean.csv"), parse_dates=["hour"])
    mf = pd.read_csv(_first(project, "data/features/model_final.csv"), parse_dates=["hour"])
    tp = pd.read_csv(predictions or _first(project, "results/test_predictions.csv", "model_training/results/test_predictions.csv"),
                     parse_dates=["hour"])
    out.mkdir(parents=True, exist_ok=True)
    stays = sorted(int(s) for s in tp.stay_id.unique())
    index = []
    for sid in stays:
        d = dyn[dyn.stay_id == sid].sort_values("hour")
        t = tp[tp.stay_id == sid].sort_values("hour")
        last = mf[(mf.stay_id == sid) & (mf.hour == t.hour.max())].iloc[0]
        prof = {k: (None if pd.isna(last[k]) else float(last[k])) for k in PROFILE_FIELDS if k in last}
        if last.get("bmi_missing", 0) == 1:
            prof["bmi"] = None
        for k in ("gender_male", "smoker", "ix_bleeding", "hist_af"):
            if prof.get(k) is not None:
                prof[k] = int(prof[k])
        clinical = [{"name": k, "value": float(last[k]), "unit": u} for k, u in CLINICAL_FIELDS.items()
                    if k in last and pd.notna(last[k])]
        h0 = d.hour.min()
        hours = []
        for r in d.itertuples():
            row = {"offset_h": int((r.hour - h0) / pd.Timedelta(hours=1))}
            for s in ("hr", "spo2", "rr"):
                row[s] = float(getattr(r, s)) if getattr(r, f"{s}_measured") == 1 and pd.notna(getattr(r, s)) else None
            row["rhythm"] = None if r.rhythm_group == "unknown" else r.rhythm_group
            hours.append(row)
        test_hours = [{"offset_h": int((r.hour - h0) / pd.Timedelta(hours=1)), "y_24h": int(r.y_24h),
                       "hours_to_event": None if pd.isna(r.hours_to_event) else float(r.hours_to_event),
                       "risk_watch_nb05": round(float(r.risk_watch), 6)} for r in t.itertuples()]
        doc = {"stay_id": sid, "subject_id": int(last.subject_id), "source": SOURCE,
               "note": "Hour offsets are relative to the first ICU hour; real dates are not kept.",
               "profile": prof, "clinical": clinical, "hours": hours, "test_hours": test_hours,
               "event_within_stay": int(t.y_24h.max())}
        (out / f"stay_{sid}.json").write_text(json.dumps(doc, indent=0))
        index.append({"stay_id": sid, "n_hours": len(hours), "first_test_offset_h": test_hours[0]["offset_h"],
                      "event_within_24h_in_test": int(t.y_24h.max()), "age": prof.get("age_at_admit"),
                      "male": prof.get("gender_male")})
    (out / "index.json").write_text(json.dumps(index, indent=1))
    return stays


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True, type=Path)
    ap.add_argument("--predictions", type=Path, default=None)
    ap.add_argument("--out", type=Path, default=Path(__file__).resolve().parents[1] / "data" / "replay")
    a = ap.parse_args()
    print(f"wrote {len(build(a.project, a.out, a.predictions))} stays to {a.out}")
