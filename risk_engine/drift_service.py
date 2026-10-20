# Model observability: input drift against the training reference (population stability index per input) and
# outcome-based performance once verified outcome labels arrive. Labels usually arrive late (after the 24 h horizon),
# so performance is computed only on predictions whose outcome is already known.
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def psi(expected_share: list[float], actual: pd.Series, bins: list[float]) -> float | None:
    a = actual.dropna()
    if not len(a) or not bins:
        return None
    counts = np.histogram(a, bins=np.r_[-np.inf, bins, np.inf])[0]
    act = counts / counts.sum()
    exp = np.asarray(expected_share, float)
    eps = 1e-4
    act, exp = np.clip(act, eps, None), np.clip(exp, eps, None)
    return float(np.sum((act - exp) * np.log(act / exp)))


def input_drift(reference: dict, X: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    warn, alert = cfg["drift"]["psi_warning"], cfg["drift"]["psi_alert"]
    rows = []
    for f in X.columns:
        r = reference["features"].get(f)
        if not r or not r.get("bins"):
            continue
        v = psi(r["bin_share"], X[f], r["bins"])
        miss = round(100 * float(X[f].isna().mean()), 1)
        rows.append({"feature": f, "psi": None if v is None else round(v, 4),
                     "status": "NO_DATA" if v is None else "ALERT" if v >= alert else "WARNING" if v >= warn else "OK",
                     "missing_pct_now": miss, "missing_pct_train": r["missing_pct"],
                     "median_now": None if X[f].dropna().empty else round(float(X[f].median()), 3), "median_train": round(r["median"], 3)})
    return pd.DataFrame(rows).sort_values("psi", ascending=False, na_position="last").reset_index(drop=True)


def outcome_performance(scores: pd.Series, labels: pd.Series, thresholds: dict) -> dict[str, Any]:
    """Performance on predictions whose verified outcome is known. Small samples give wide uncertainty."""
    from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score
    m = labels.notna() & scores.notna()
    y, p = labels[m].astype(int), scores[m].astype(float)
    out = {"n_predictions_with_outcome": int(m.sum()), "events": int(y.sum()), "mean_predicted": round(float(p.mean()), 4),
           "observed_rate": round(float(y.mean()), 4)}
    if y.nunique() == 2:
        out.update(AUROC=round(float(roc_auc_score(y, p)), 4), AUPRC=round(float(average_precision_score(y, p)), 4),
                   Brier=round(float(brier_score_loss(y, p)), 4))
        hi = p >= thresholds["high"]
        out["sensitivity_high"] = round(float((hi & (y == 1)).sum() / max(1, (y == 1).sum())), 4)
        out["ppv_high"] = round(float((hi & (y == 1)).sum() / max(1, hi.sum())), 4)
    out["calibration_in_the_large"] = round(out["observed_rate"] - out["mean_predicted"], 4)
    return out
