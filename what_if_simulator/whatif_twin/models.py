# Thin wrapper around one saved notebook 05 bundle: the exact fitted pipeline + calibrator, the versioned operating
# points, exact SHAP (notebook 06 method: closed-form linear SHAP for the watch logistic regression, TreeSHAP for the
# LightGBM reference model) and, for the watch model, the 50 bootstrap refits from notebook 07.
from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from .artifacts import Artifacts

TIERS = ("LOW", "MODERATE", "HIGH")


def logit(p: float) -> float:
    p = min(max(float(p), 1e-12), 1 - 1e-12)
    return math.log(p / (1 - p))


def sigmoid(x: float) -> float:
    return 1 / (1 + math.exp(-x))


class TwinModel:
    def __init__(self, art: Artifacts, name: str):
        self.art, self.name = art, name
        self.bundle = art.bundles[name]
        self.features: list[str] = list(self.bundle["features"])
        self.estimator = self.bundle["estimator"]
        self.calibrator = self.bundle["calibrator"]
        self.thresholds = {k: float(v) for k, v in art.operating_points[name]["operating_points"].items()}
        self.base_rate = float(self.bundle.get("base_rate") or np.nan)
        self.is_linear = hasattr(self.estimator, "named_steps") and "lr" in self.estimator.named_steps
        self._tree_explainer = None
        self.ensemble = art.ensemble["models"] if (art.ensemble and name == "watch") else None
        self.sources = self.bundle.get("feature_sources", {})

    # ---------------------------------------------------------------- metadata
    @property
    def calibration(self) -> str:
        return getattr(self.calibrator, "method", "none")

    @property
    def label(self) -> str:
        return {"watch": "Phone + watch model (deployed)", "full": "ICU reference model (needs hospital data)"}[self.name]

    def info(self) -> dict[str, Any]:
        b = self.bundle
        return {"model": self.name, "label": self.label, "bundle_name": b.get("name"), "version": b.get("version"),
                "type": b.get("model_type"), "n_features": len(self.features), "calibration": self.calibration,
                "output": ("calibrated probability (sigmoid / Platt)" if self.calibration != "none" else
                           "model probability (no calibrator: notebook 05 kept 'none' as it had the lowest CV Brier)"),
                "operating_points": self.thresholds, "target": b.get("label_definition"), "horizon_h": b.get("horizon_h"),
                "sha256": self.art.hashes.get(self.name)}

    # ---------------------------------------------------------------- inference
    def matrix(self, values: dict | pd.DataFrame) -> pd.DataFrame:
        if isinstance(values, pd.DataFrame):
            X = values.reindex(columns=self.features)
        else:
            X = pd.DataFrame([{f: values.get(f, np.nan) for f in self.features}])
        X = X.apply(pd.to_numeric, errors="coerce").astype(float)
        if np.isinf(X.to_numpy()).any():
            raise ValueError("Feature vector contains infinite values.")
        return X

    def raw(self, X: pd.DataFrame) -> np.ndarray:
        p = self.estimator.predict_proba(X)[:, 1]
        if not np.all(np.isfinite(p)) or np.any((p < 0) | (p > 1)):
            raise ValueError("Model returned a value outside 0-1.")
        return p

    def prob(self, X: pd.DataFrame) -> np.ndarray:
        return np.asarray(self.calibrator.transform(self.raw(X)), float)

    def tier(self, p: float) -> str:
        """Same rule as risk_policy.RiskPolicy.tier: a value exactly on a cut-off goes to the higher tier."""
        if p >= self.thresholds["high"]:
            return "HIGH"
        if p >= self.thresholds["moderate"]:
            return "MODERATE"
        return "LOW"

    def predict(self, values: dict) -> dict[str, Any]:
        X = self.matrix(values)
        raw = float(self.raw(X)[0])
        p = float(self.calibrator.transform(np.array([raw]))[0])
        return {"raw": raw, "prob": p, "raw_log_odds": logit(raw), "tier": self.tier(p),
                "times_base_rate": round(p / self.base_rate, 2) if self.base_rate == self.base_rate else None,
                "missing_inputs": [f for f in self.features if pd.isna(X.iloc[0][f])]}

    # ---------------------------------------------------------------- explanation
    def shap(self, values: dict) -> tuple[pd.Series, float]:
        """Exact SHAP values in the model's raw log-odds, and the base value. Checked for additivity."""
        X = self.matrix(values)
        if self.is_linear:
            phi, base = self.art.engine["shap_service"]._phi_linear(
                self.estimator, X, np.asarray(self.art.background["transformed_mean"], float), len(self.features))
        else:
            if self._tree_explainer is None:
                import shap
                self._tree_explainer = shap.TreeExplainer(self.estimator)
            sv = self._tree_explainer(X)
            phi, base = np.asarray(sv.values)[0], float(np.ravel(self._tree_explainer.expected_value)[-1])
        err = abs(base + float(np.sum(phi)) - logit(self.raw(X)[0]))
        if not np.isfinite(phi).all() or err > 1e-5:
            raise ValueError(f"SHAP values do not add up to the model output (error {err:.1e}).")
        return pd.Series(phi, index=self.features), base

    @property
    def shap_space(self) -> str:
        if self.calibration == "sigmoid":
            return "raw model log-odds (the sigmoid calibrator rescales them by its slope; signs and ranking are unchanged)"
        return "model log-odds (no calibrator, so these add up exactly to the displayed probability)"

    # ---------------------------------------------------------------- model variance
    def ensemble_probs(self, values_list: list[dict]) -> np.ndarray | None:
        """(n_refits, n_rows) probabilities from the bootstrap refits; None for the model without refits."""
        if not self.ensemble:
            return None
        X = pd.concat([self.matrix(v) for v in values_list], ignore_index=True)
        return np.vstack([m.predict_proba(X)[:, 1] for m in self.ensemble])
