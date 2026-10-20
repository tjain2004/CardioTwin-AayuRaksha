# Dashboard integration. `simulate_request` is a pure JSON-in / JSON-out function a backend (or the phone app's
# doctor mode) can call; `result_payload` is the response, versioned so the app can check what it renders. Roles follow
# risk_policy.yaml: only roles with the 'what_if' permission (clinician) may run simulations.
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .scenario import Scenario

SCHEMA_VERSION = "whatif-payload-1.0"


class PermissionDenied(PermissionError):
    pass


def _r(x, n=5):
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return None
    return round(float(x), n)


def result_payload(sim, res, top: int = 8) -> dict[str, Any]:
    T = sim.art.T
    models = {}
    for m, o in res.outcomes.items():
        tm = sim.models[m]
        g = o.group_delta(T)
        d = o.shap_delta
        top_feat = [] if d is None else [
            {"feature": f, "label": T.TXT.get(f, (f,))[0], "group": T.group_of(f),
             "value_original": _r(res.case.values.get(f), 4), "value_simulated": _r(res.applied.values.get(f), 4),
             "shap_original": _r(o.shap_baseline[f]), "shap_simulated": _r(o.shap_scenario[f]), "shap_change": _r(d[f])}
            for f in d.abs().sort_values(ascending=False).index[:top] if abs(d[f]) > 1e-9]
        models[m] = {
            "model": tm.info() | {"sha256": tm.info()["sha256"]},
            "affected": o.affected,
            "baseline": {"probability": _r(o.baseline["prob"]), "tier": o.baseline["tier"], "raw_output": _r(o.baseline["raw"])},
            "scenario": {"probability": _r(o.scenario["prob"]), "tier": o.scenario["tier"], "raw_output": _r(o.scenario["raw"])},
            "difference": {"absolute": _r(o.delta), "relative": _r(o.relative, 4), "tier_change": o.tier_change},
            "uncertainty": {k: (_r(v) if isinstance(v, float) else v) for k, v in (o.uncertainty or {}).items()},
            "explanation": {"space": tm.shap_space, "base_value": _r(o.shap_base_value),
                            "group_changes": [] if g is None else [
                                {"group": k, "shap_original": _r(r.baseline), "shap_simulated": _r(r.scenario), "shap_change": _r(r.delta)}
                                for k, r in g.iterrows() if abs(r.delta) > 1e-9][:top],
                            "top_feature_changes": top_feat,
                            "note": "SHAP values are attributions of the model's output, not causes."},
            "out_of_distribution": o.out_of_distribution, "direction_warnings": o.direction_warnings}
    body = {
        "schema_version": SCHEMA_VERSION, "label": res.label, "status": res.status, "created_at": res.created_at,
        "simulation": True, "writes_to_patient_record": False,
        "patient": res.case.summary() | {"synthetic": res.case.is_synthetic},
        "scenario": res.scenario.to_dict() | {"applied": res.applied.changes_text},
        "edits": [{k: (_r(v, 4) if isinstance(v, float) else v) for k, v in e.items()} for e in res.applied.edits],
        "held_constant": res.applied.held, "notes": res.applied.notes, "rejected": res.applied.rejected,
        "models": models,
        "provenance": {"engine": "whatif_twin 1.0.0", "model_version": sim.art.manifest.get("model_version"),
                       "policy_version": sim.art.cfg.get("policy_version"), "integrity": sim.art.integrity,
                       "thresholds_provisional": True},
    }
    body["payload_sha256"] = hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()
    return body


def simulate_request(sim, request: dict, role: str = "clinician") -> dict[str, Any]:
    """{"case_id": "stay-37093652", "scenario": {"name": ..., "changes": {"heart_rate": {"delta": -10}}}} -> payload."""
    if "what_if" not in sim.art.cfg["privacy"]["roles"].get(role, []):
        raise PermissionDenied(f"Role '{role}' may not run what-if simulations.")
    case = sim.patients.get(request["case_id"])
    res = sim.simulate(case, Scenario.from_dict(request["scenario"]))
    return result_payload(sim, res)


def save_json(obj: Any, path: str | Path) -> None:
    def conv(o):
        if isinstance(o, (np.floating, np.integer)):
            return o.item()
        if isinstance(o, (pd.Series, pd.DataFrame)):
            return o.to_dict()
        return str(o)
    Path(path).write_text(json.dumps(obj, indent=1, default=conv), encoding="utf-8")


PAYLOAD_FIELDS = {
    "schema_version": "payload format version",
    "label": "the hypothetical-simulation label the app must show with every result",
    "status": "OK | NO_MEANINGFUL_CHANGE (all |change| < 0.1 points) | REJECTED (see 'rejected')",
    "patient": "case id, label, data source, synthetic flag, hour, models that can score it",
    "scenario / edits": "the requested changes, and every model input that changed (original -> simulated)",
    "held_constant": "inputs that depend on an edited signal but cannot be recomputed from one hour",
    "models.<m>.baseline / scenario / difference": "probability, LOW / MODERATE / HIGH tier and change",
    "models.<m>.uncertainty": "bootstrap-refit spread of baseline, scenario and change; share of refits agreeing on direction",
    "models.<m>.explanation": "SHAP group and feature changes (attributions, not causes)",
    "models.<m>.out_of_distribution": "edited inputs outside the training range (extrapolation)",
    "models.<m>.direction_warnings": "responses against the usual clinical direction",
    "provenance": "model version, integrity checks, policy version, provisional thresholds",
}
