# Patient-level SHAP explanation for the risk engine. For the watch logistic-regression pipeline the SHAP values are
# exact (interventional, independent features, background = training hours) and are computed in closed form from
# the fitted imputer, scaler and coefficients, the same method notebook 06 verified against shap.LinearExplainer.
# Values explain the model's raw log-odds; they are attributions, not causes.
from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from . import explain_text as T

VERSION = "SHAP-linear-exact-1.0"


class ExplanationUnavailable(RuntimeError):
    pass


def _phi_linear(est, X: pd.DataFrame, background_mean: np.ndarray, n: int):
    pre, lr = est[:-1], est[-1]
    Z = pre.transform(X)
    w = lr.coef_[0]
    if Z.shape[1] != len(w) or len(background_mean) != len(w):
        raise ExplanationUnavailable("Background does not match the fitted pipeline.")
    phi_z = (Z - background_mean) * w
    phi = phi_z[:, :n].copy()
    ind = est.named_steps["impute"].indicator_
    if ind is not None:
        for k, j in enumerate(ind.features_):
            phi[:, j] += phi_z[:, n + k]      # each missing-value flag is credited to the input it belongs to
    base = float(lr.intercept_[0] + (background_mean * w).sum())
    return phi[0], base


def _phi_tree(est, X: pd.DataFrame):
    import shap
    ex = shap.TreeExplainer(est)
    sv = ex(X)
    return np.asarray(sv.values)[0], float(np.ravel(ex.expected_value)[-1])


def explain(model, X: pd.DataFrame, top_k: int = 3) -> dict[str, Any]:
    """Return SHAP values, grouped top reasons, top single features and a waterfall structure for one row."""
    b, feats = model.bundle, model.features
    est = b["estimator"]
    if hasattr(est, "named_steps") and "lr" in est.named_steps:
        if not model.background:
            raise ExplanationUnavailable("SHAP background artifact is missing.")
        phi, base = _phi_linear(est, X, np.asarray(model.background["transformed_mean"], float), len(feats))
        method = "exact linear SHAP (interventional, training-hour background)"
    else:
        phi, base = _phi_tree(est, X)
        method = "TreeSHAP"
    p = float(est.predict_proba(X)[:, 1][0])
    out_lo = math.log(p / (1 - p))
    err = abs(base + phi.sum() - out_lo)
    if not np.isfinite(phi).all() or err > 1e-6:
        raise ExplanationUnavailable(f"SHAP values do not add up to the model output (error {err:.1e}).")
    row = X.iloc[0]
    s = pd.Series(phi, index=feats)
    groups = s.groupby([T.group_of(f) for f in feats]).sum()
    top_groups = groups.reindex(groups.abs().sort_values(ascending=False).index)[:top_k]
    contributors = []
    for name, x in top_groups.items():
        members = [f for f in feats if T.group_of(f) == name]
        shown = sorted([f for f in members if np.sign(s[f]) == np.sign(x)] or members, key=lambda f: -abs(s[f]))[:2]
        warn = T.against_expectation(name, x, row) if name in T.EXPECT else False
        contributors.append({
            "rank": len(contributors) + 1, "reason": name, "shap_log_odds": round(float(x), 4),
            "odds_multiplier": round(math.exp(x), 3), "direction": "raises" if x > 0 else "lowers",
            "effect_words": T.effect(x)[0], "clinical_direction_warning": bool(warn),
            "inputs": [{"feature": f, "value": None if pd.isna(row[f]) else round(float(row[f]), 3),
                        "unit": unit(f), "text": T.describe(f, row[f]), "shap_log_odds": round(float(s[f]), 4),
                        "reference_range": T.REF.get(f, ("",))[0] or None, "vs_reference": T.ref_flag(f, row[f]) or None,
                        "was_missing": bool(pd.isna(row[f]))} for f in shown],
        })
    top_feat = s.reindex(s.abs().sort_values(ascending=False).index)[:top_k]
    top_features = [{"rank": i + 1, "feature": f, "label": T.TXT.get(f, (f,))[0],
                     "value": None if pd.isna(row[f]) else round(float(row[f]), 3), "unit": unit(f),
                     "was_missing": bool(pd.isna(row[f])), "shap_log_odds": round(float(v), 4),
                     "direction": "raises" if v > 0 else "lowers"} for i, (f, v) in enumerate(top_feat.items())]
    order = groups.reindex(groups.abs().sort_values(ascending=False).index)
    steps, run = [], base
    for name, x in order.items():
        steps.append({"label": name, "start": round(run, 4), "end": round(run + x, 4), "shap_log_odds": round(float(x), 4)})
        run += x
    return {"method": method, "output_space": "raw model log-odds (logit of the model's uncalibrated output)",
            "baseline_output": round(base, 4), "baseline_probability": round(1 / (1 + math.exp(-base)), 4),
            "explained_output": round(out_lo, 4), "additivity_error": float(err),
            "top_contributors": contributors, "top_features": top_features,
            "waterfall": {"space": "log-odds", "base": round(base, 4), "final": round(out_lo, 4), "steps": steps,
                          "note": "Groups of related inputs; each group's value is the exact sum of its members' SHAP values."},
            "shap_values": {f: round(float(v), 5) for f, v in s.items()}, "version": VERSION}


def unit(f: str) -> str | None:
    t = T.TXT.get(f)
    if not t or t[2] == "flag":
        return None
    return {"pct": "%", "log1p": "count (log)", "discharge": "days"}.get(t[2], t[1].strip() or None)
