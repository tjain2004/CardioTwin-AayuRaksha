# Builds the engine's start-up artifacts from the TRAINING split only (never the test split):
#   training_reference.json  per-feature ranges, medians and drift bins
#   shap_background.json     mean of the transformed training hours (SHAP background)
#   bootstrap_ensemble.joblib  watch model refitted on patient-level bootstrap samples (uncertainty display)
#   manifest.json            sha256 of the model, config and artifacts (checked at start-up)
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.base import clone

from .model_service import sha256_file


def training_frame(root: Path) -> pd.DataFrame:
    keys = pd.read_csv(root / "results" / "cv_oof_predictions.csv")
    mf = pd.read_csv(root / "data" / "features" / "model_final.csv")
    tr = keys[["subject_id", "stay_id", "hour", "y_24h"]].merge(mf, on=["subject_id", "stay_id", "hour"], how="left",
                                                                suffixes=("", "_mf"))
    assert (mf.set_index(["subject_id", "stay_id", "hour"]).loc[list(zip(tr.subject_id, tr.stay_id, tr.hour))].split == "train").all()
    return tr


def build(root: Path, cfg: dict, n_boot: int = 50, seed: int = 42) -> dict:
    root = Path(root)
    art = root / "risk_engine" / "artifacts"
    art.mkdir(parents=True, exist_ok=True)
    bundle_path = root / cfg["model"]["bundle"]
    b = joblib.load(bundle_path)
    feats = b["features"]
    tr = training_frame(root)
    X = tr[feats].astype(float)

    ref = {"built_from": "training split only (notebook 05 cross-validation hours)", "n_hours": int(len(tr)),
           "n_patients": int(tr.subject_id.nunique()), "features": {}}
    for f in feats:
        s = X[f].dropna()
        if s.empty:
            ref["features"][f] = {"p005": None, "p995": None, "median": None, "bins": None, "missing_pct": 100.0}
            continue
        edges = np.unique(np.quantile(s, np.linspace(0, 1, 11)))
        counts = np.histogram(s, bins=np.r_[-np.inf, edges[1:-1], np.inf])[0] if len(edges) > 2 else np.array([len(s)])
        ref["features"][f] = {"p005": float(s.quantile(0.005)), "p995": float(s.quantile(0.995)), "median": float(s.median()),
                              "bins": [float(e) for e in edges[1:-1]], "bin_share": (counts / counts.sum()).round(5).tolist(),
                              "missing_pct": round(100 * float(X[f].isna().mean()), 2)}
    med_cols = [c for c in tr.columns if tr[c].dtype.kind in "fi"]
    ref["medians"] = {c: float(v) for c, v in tr[med_cols].median(numeric_only=True).items() if pd.notna(v)}
    (art / "training_reference.json").write_text(json.dumps(ref, indent=1))

    est = b["estimator"]
    if hasattr(est, "named_steps") and "lr" in est.named_steps:
        Zb = est[:-1].transform(X)
        (art / "shap_background.json").write_text(json.dumps({
            "built_from": "training split", "n_hours": int(len(X)), "transformed_mean": Zb.mean(0).tolist()}))

    rng = np.random.default_rng(seed)
    pats = tr.subject_id.unique()
    models = []
    while len(models) < n_boot:
        pick = rng.choice(pats, len(pats), replace=True)
        d = pd.concat([tr[tr.subject_id == p] for p in pick])
        if d.y_24h.nunique() < 2:
            continue
        models.append(clone(est).fit(d[feats].astype(float), d.y_24h))
    joblib.dump({"models": models, "seed": seed, "n": len(models), "note": "patient-level bootstrap refits, training split"},
                art / "bootstrap_ensemble.joblib")

    cfg_path = root / "risk_engine" / "config" / "risk_policy.yaml"
    manifest = {"created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "model_file": cfg["model"]["bundle"], "model_sha256": sha256_file(bundle_path),
                "model_version": b["version"], "config_sha256": sha256_file(cfg_path),
                "artifacts": {p.name: sha256_file(p) for p in sorted(art.glob("*")) if p.name != "manifest.json"}}
    (art / "manifest.json").write_text(json.dumps(manifest, indent=1))
    return manifest
