# What-if digital-twin simulator. Works on deep copies of the inputs, re-runs the full engine in simulation mode (no
# alerts, nothing written to the patient's record) and shows original and simulated output side by side. Results are
# hypothetical model simulations: not predicted treatment effects and not medical advice.
from __future__ import annotations

import copy
from typing import Any

import pandas as pd

from .alert_service import PermissionDenied
from .input_validator import WATCH_SIGNALS

LABEL = ("HYPOTHETICAL MODEL SIMULATION. It shows how this model's output responds to edited inputs. It does not change "
         "the patient's record, does not predict the effect of any treatment and is not medical advice.")


def simulate(engine, request: dict, changes: dict[str, float], role: str, hours: int | None = None) -> dict[str, Any]:
    if "what_if" not in engine.cfg["privacy"]["roles"].get(role, []):
        raise PermissionDenied(f"Role '{role}' may not run what-if simulations.")
    allowed = engine.cfg["whatif"]["allowed"]
    hours = hours or engine.cfg["whatif"]["default_hours"]
    rejected = {k: f"not allowed or outside {allowed.get(k)}" for k, v in changes.items()
                if k not in allowed or v is None or not (allowed[k][0] <= float(v) <= allowed[k][1])}
    if rejected:
        return {"status": "INVALID_CHANGE", "rejected": rejected, "label": LABEL}
    original = copy.deepcopy(request)                    # the caller's request is never modified
    hyp = copy.deepcopy(request)
    now = _ts(hyp["now"])
    start = now - pd.Timedelta(hours=hours)
    clinical_names = {c["name"] for c in hyp.get("clinical_readings") or []}
    for k, v in changes.items():
        if k in WATCH_SIGNALS:
            for r in hyp["wearable_readings"]:
                if r["signal"] == k and _ts(r["timestamp"]) >= start:
                    r["value"] = float(v)
        elif k in clinical_names or k in ("ldl", "total_chol", "triglycerides", "hba1c"):
            hyp["clinical_readings"] = [c for c in hyp.get("clinical_readings") or [] if c["name"] != k] + [
                {"name": k, "value": float(v), "unit": {"hba1c": "%"}.get(k, "mg/dL"), "source": "what-if (hypothetical)"}]
        else:
            hyp["patient_profile"][k] = v
    kw = dict(simulation=True, record=False)
    base = engine.evaluate(original["patient_profile"], original.get("clinical_readings"), original.get("wearable_readings"),
                           now=original["now"], **kw)
    sim = engine.evaluate(hyp["patient_profile"], hyp.get("clinical_readings"), hyp.get("wearable_readings"), now=hyp["now"], **kw)
    out: dict[str, Any] = {"label": LABEL, "changes": changes, "applied_to_last_hours": hours,
                           "status": "OK" if base.status.value == sim.status.value == "OK" else "UNABLE",
                           "original": _summary(base), "simulated": _summary(sim)}
    if out["status"] == "OK":
        po = base.risk.calibrated_probability or base.risk.raw_output
        ps = sim.risk.calibrated_probability or sim.risk.raw_output
        out["difference"] = round(ps - po, 4)
        sv_o, sv_s = base.explanation.shap_values or {}, sim.explanation.shap_values or {}
        out["changed_model_inputs"] = sorted(
            [{"feature": f, "shap_original": sv_o[f], "shap_simulated": sv_s.get(f), "shap_change": round(sv_s.get(f, 0) - sv_o[f], 4)}
             for f in sv_o if abs(sv_s.get(f, 0) - sv_o[f]) > 1e-6], key=lambda d: -abs(d["shap_change"]))
        go = {c["reason"]: c["shap_log_odds"] for c in base.explanation.top_contributors}
        gs = {c["reason"]: c["shap_log_odds"] for c in sim.explanation.top_contributors}
        out["top_reasons_original"], out["top_reasons_simulated"] = go, gs
    return out


def _ts(x) -> pd.Timestamp:
    t = pd.Timestamp(x)
    return t.tz_convert("UTC").tz_localize(None) if t.tzinfo is not None else t


def _summary(r) -> dict:
    return {"status": r.status.value, "reason": r.status_reason,
            "score": r.risk.calibrated_probability or r.risk.raw_output,
            "tier": r.risk.tier.value if r.risk.tier else None, "emergency_state": r.emergency.state.value}
