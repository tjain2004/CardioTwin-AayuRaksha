# Model service: loads the saved notebook 05 bundle once, checks its integrity and feature schema, and runs the
# exact fitted pipeline (imputer -> scaler -> model) and calibrator. Never fits anything at inference time.
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd


class ModelUnavailable(RuntimeError):
    """Raised when the model cannot be used; the engine turns it into status MODEL_ERROR."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class ModelService:
    def __init__(self, root: Path, cfg: dict, bundle: dict | None = None, check_integrity: bool = True):
        self.root, self.cfg = Path(root), cfg
        mcfg = cfg["model"]
        self.bundle_path = self.root / mcfg["bundle"]
        self.sha256 = None
        if bundle is None:
            if check_integrity:
                man_path = self.root / mcfg["integrity_manifest"]
                if not man_path.exists():
                    raise ModelUnavailable("INTEGRITY_MANIFEST_MISSING", "Model integrity manifest not found; run the artifact build step.")
                expected = json.loads(man_path.read_text())["model_sha256"]
                self.sha256 = sha256_file(self.bundle_path)
                if self.sha256 != expected:
                    raise ModelUnavailable("INTEGRITY_FAILED", "Model file does not match its recorded checksum.")
            bundle = joblib.load(self.bundle_path)
        self.bundle = bundle
        self.features: list[str] = list(bundle["features"])
        schema = json.loads((self.root / mcfg["feature_schema"]).read_text())[mcfg["feature_set"]]["features"]
        if [f["name"] for f in sorted(schema, key=lambda f: f["position"])] != self.features:
            raise ModelUnavailable("FEATURE_SCHEMA_MISMATCH", "Model features do not match the saved feature schema.")
        self.calibrator = bundle.get("calibrator")
        art = self.root / "risk_engine" / "artifacts"
        self.reference = json.loads((art / "training_reference.json").read_text()) if (art / "training_reference.json").exists() else None
        self.background = json.loads((art / "shap_background.json").read_text()) if (art / "shap_background.json").exists() else None
        ens = art / "bootstrap_ensemble.joblib"
        self.ensemble = joblib.load(ens) if ens.exists() else None

    # ---------------------------------------------------------------- metadata
    @property
    def calibration_status(self) -> str:
        if self.calibrator is None:
            return "unavailable"
        return "calibrated" if getattr(self.calibrator, "method", "none") in ("sigmoid", "isotonic") else "uncalibrated"

    def info(self) -> dict[str, Any]:
        b, m = self.bundle, self.cfg["model"]
        method = getattr(self.calibrator, "method", None)
        note = {"calibrated": f"{method} calibrator fitted on cross-validation predictions in notebook 05.",
                "uncalibrated": ("No calibrator applied: notebook 05 compared none, sigmoid and isotonic and kept 'none' because "
                                 "calibrating did not lower the cross-validated Brier score. The output is the logistic "
                                 "regression's own probability, not a separately calibrated one."),
                "unavailable": "The calibrator is missing from the model file; the output must not be read as a probability."}
        return {"name": b.get("name"), "version": b.get("version"), "sha256": self.sha256, "target": m["target"],
                "horizon": f"{m['horizon_hours']} hours", "population": m["population"],
                "calibration_status": self.calibration_status, "calibration_method": method,
                "calibration_note": note[self.calibration_status],
                "calibration_version": f"{b.get('version')}:{method}" if method else None}

    # ---------------------------------------------------------------- inference
    def validate_matrix(self, X: pd.DataFrame) -> None:
        if list(X.columns) != self.features:
            raise ModelUnavailable("FEATURE_SCHEMA_MISMATCH", "Feature vector columns or order differ from training.")
        vals = X.to_numpy(dtype=float)
        if np.isinf(vals).any():
            raise ModelUnavailable("NON_FINITE_FEATURE", "Feature vector contains infinite values.")

    def raw(self, X: pd.DataFrame) -> np.ndarray:
        self.validate_matrix(X)
        p = self.bundle["estimator"].predict_proba(X)[:, 1]
        if not np.all(np.isfinite(p)) or np.any((p < 0) | (p > 1)):
            raise ModelUnavailable("MODEL_OUTPUT_INVALID", "Model returned a value outside 0-1.")
        return p

    def calibrate(self, p_raw: np.ndarray) -> np.ndarray | None:
        if self.calibration_status != "calibrated":
            return None
        p = self.calibrator.transform(p_raw)
        if not np.all(np.isfinite(p)) or np.any((p < 0) | (p > 1)):
            raise ModelUnavailable("CALIBRATION_OUTPUT_INVALID", "Calibrator returned a value outside 0-1.")
        return p

    def predict(self, X: pd.DataFrame) -> dict[str, Any]:
        p_raw = float(self.raw(X)[0])
        cal = self.calibrate(np.array([p_raw]))
        return {"raw": p_raw, "calibrated": None if cal is None else float(cal[0]),
                "log_odds": math.log(max(p_raw, 1e-12) / max(1 - p_raw, 1e-12))}

    def imputed(self, X: pd.DataFrame) -> list[dict[str, Any]]:
        """Inputs that were missing and filled in by the fitted training imputer (value = training median)."""
        est = self.bundle["estimator"]
        stats = None
        if hasattr(est, "named_steps") and "impute" in est.named_steps:
            stats = est.named_steps["impute"].statistics_
        out = []
        for j, f in enumerate(self.features):
            if pd.isna(X.iloc[0, j]):
                out.append({"feature": f, "filled_with": None if stats is None else round(float(stats[j]), 4),
                            "method": "training median (fitted SimpleImputer)" if stats is not None else "model's native missing-value handling",
                            "source": self.bundle.get("feature_sources", {}).get(f, "unknown")})
        return out

    def out_of_distribution(self, X: pd.DataFrame) -> list[dict[str, Any]]:
        """Inputs outside the 0.5-99.5 percentile range of the training hours. A simple, transparent check."""
        if not self.reference:
            return []
        out = []
        for f in self.features:
            v = X.iloc[0][f]
            r = self.reference["features"].get(f)
            if r is None or pd.isna(v) or r["p005"] is None:
                continue
            if v < r["p005"] or v > r["p995"]:
                out.append({"feature": f, "value": round(float(v), 3), "training_range_99pct": [r["p005"], r["p995"]]})
        return out

    def uncertainty(self, X: pd.DataFrame) -> dict[str, Any] | None:
        """Spread of the same model refitted on bootstrap samples of the training patients. This shows model variance
        from the small training set; it is NOT a validated confidence interval."""
        if not self.ensemble:
            return None
        ps = np.array([m.predict_proba(X)[:, 1][0] for m in self.ensemble["models"]])
        return {"p05": round(float(np.percentile(ps, 5)), 4), "p50": round(float(np.percentile(ps, 50)), 4),
                "p95": round(float(np.percentile(ps, 95)), 4), "n_refits": int(len(ps)),
                "method": "patient-level bootstrap refits of the watch model (notebook 07 artifact)",
                "validated": False,
                "note": "Shows how much the score moves when the model is retrained on other samples of the 59 training "
                        "patients. Not a validated confidence interval."}
