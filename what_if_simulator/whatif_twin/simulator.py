# The what-if simulator: baseline vs hypothetical outputs of both saved models for one patient, with exact SHAP
# deltas, model-variance bands from the bootstrap refits, out-of-distribution flags, a direction sanity check,
# multi-scenario comparison, sensitivity curves, a tornado ranking, 2-D response surfaces and a counterfactual search.
from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

import numpy as np
import pandas as pd

from .artifacts import Artifacts, finite, load_artifacts
from .levers import Lever, UNSUPPORTED, build_levers, plausible_range, supported_models
from .models import TwinModel, sigmoid
from .patients import PatientCase, PatientRegistry
from .readings_twin import ReadingTwin, synthetic_request
from .scenario import AppliedScenario, Change, Scenario, apply_scenario

LABEL = ("HYPOTHETICAL MODEL SIMULATION. It shows how this model's output responds to edited inputs. It does not change "
         "the patient's record, does not predict the effect of any treatment and is not medical advice.")
# usual clinical direction of risk when the quantity goes UP (used only to flag surprising model behaviour)
EXPECTED_DIRECTION = {"heart_rate": +1, "resp_rate": +1, "spo2": -1, "temperature": +1, "smoker": +1}
NEGLIGIBLE = 0.001     # absolute probability change below which a scenario is reported as "no meaningful change"


@dataclass
class ModelOutcome:
    model: str
    affected: bool
    baseline: dict[str, Any]
    scenario: dict[str, Any]
    shap_baseline: pd.Series | None = None
    shap_scenario: pd.Series | None = None
    shap_base_value: float | None = None
    uncertainty: dict[str, Any] | None = None
    out_of_distribution: list[dict] = field(default_factory=list)
    direction_warnings: list[str] = field(default_factory=list)

    @property
    def delta(self) -> float:
        return self.scenario["prob"] - self.baseline["prob"]

    @property
    def relative(self) -> float | None:
        return self.delta / self.baseline["prob"] if self.baseline["prob"] > 0 else None

    @property
    def tier_change(self) -> str:
        a, b = self.baseline["tier"], self.scenario["tier"]
        return "unchanged" if a == b else f"{a} -> {b}"

    @property
    def shap_delta(self) -> pd.Series | None:
        if self.shap_baseline is None or self.shap_scenario is None:
            return None
        return (self.shap_scenario - self.shap_baseline)

    def group_delta(self, T) -> pd.DataFrame | None:
        d = self.shap_delta
        if d is None:
            return None
        g = pd.DataFrame({"group": [T.group_of(f) for f in d.index], "baseline": self.shap_baseline.values,
                          "scenario": self.shap_scenario.values, "delta": d.values}).groupby("group").sum()
        return g.reindex(g.delta.abs().sort_values(ascending=False).index)


@dataclass
class SimulationResult:
    case: PatientCase
    scenario: Scenario
    applied: AppliedScenario
    outcomes: dict[str, ModelOutcome]
    status: str
    created_at: str
    label: str = LABEL

    def table(self) -> pd.DataFrame:
        rows = []
        for m, o in self.outcomes.items():
            u = o.uncertainty or {}
            rows.append({"scenario": self.scenario.name, "model": m, "affected": o.affected,
                         "baseline_prob": o.baseline["prob"], "scenario_prob": o.scenario["prob"], "delta": o.delta,
                         "relative_%": None if o.relative is None else 100 * o.relative,
                         "baseline_tier": o.baseline["tier"], "scenario_tier": o.scenario["tier"],
                         "tier_change": o.tier_change, "delta_p05": u.get("delta_p05"), "delta_p95": u.get("delta_p95"),
                         "refits_same_direction_%": u.get("same_direction_%"), "robustness": u.get("verdict"),
                         "n_ood_inputs": len(o.out_of_distribution), "direction_warning": bool(o.direction_warnings)})
        return pd.DataFrame(rows)

    def headline(self) -> str:
        if self.status == "REJECTED":
            return "Scenario rejected: " + "; ".join(r["reason"] for r in self.applied.rejected)
        parts = []
        for m, o in self.outcomes.items():
            if not o.affected:
                parts.append(f"{m}: not affected (none of its inputs change)")
                continue
            parts.append(f"{m}: {100 * o.baseline['prob']:.1f}% -> {100 * o.scenario['prob']:.1f}% "
                         f"({100 * o.delta:+.1f} points, tier {o.tier_change})")
        return f"{self.scenario.name}: " + " | ".join(parts)


class WhatIfSimulator:
    def __init__(self, art: Artifacts):
        self.art = art
        self.models = {s: TwinModel(art, s) for s in ("watch", "full")}
        self.levers: dict[str, Lever] = build_levers(art.cfg)
        self.patients = PatientRegistry(art)
        self.reading_twin = ReadingTwin(art, self.models["watch"])
        self._engine = None
        self._synthetic_request = synthetic_request()
        self.patients.add(self.reading_twin.case(self._synthetic_request))

    # ---------------------------------------------------------------- catalogue
    def lever_table(self, case: PatientCase | None = None) -> pd.DataFrame:
        feats = {m: tm.features for m, tm in self.models.items()}
        rows = []
        for lv in self.levers.values():
            pr = plausible_range(lv, self.art.reference)
            rows.append({"lever": lv.key, "label": lv.label, "unit": lv.unit, "category": lv.category,
                         "models": ", ".join(supported_models(lv, feats)), "accepted_range": f"{lv.hard_range[0]:g}-{lv.hard_range[1]:g}",
                         "training_range_99%": None if pr is None else f"{pr[0]:.4g}-{pr[1]:.4g}",
                         "counterfactual_candidate": lv.actionable,
                         "model_inputs_updated": ", ".join(f for f in lv.writes if any(f in fs for fs in feats.values())),
                         "held_constant": ", ".join(lv.held),
                         **({"current_value": lv.current(case.values)} if case is not None else {}), "note": lv.note})
        return pd.DataFrame(rows)

    @staticmethod
    def unsupported_table() -> pd.DataFrame:
        return pd.DataFrame([{"quantity": k, "why it cannot be simulated": v} for k, v in UNSUPPORTED.items()])

    def available_levers(self, case: PatientCase, model: str | None = None, actionable_only: bool = False) -> list[str]:
        out = []
        for lv in self.levers.values():
            ms = supported_models(lv, {m: self.models[m].features for m in case.models})
            if not ms or (model and model not in ms) or (actionable_only and not lv.actionable):
                continue
            if lv.current(case.values) is None:
                continue
            out.append(lv.key)
        return out

    # ---------------------------------------------------------------- core
    def baseline(self, case: PatientCase) -> dict[str, dict]:
        out = {}
        for m in case.models:
            tm = self.models[m]
            pred = tm.predict(case.values)
            phi, base = tm.shap(case.values)
            pred["shap"], pred["shap_base"] = phi, base
            if tm.ensemble:
                ps = tm.ensemble_probs([case.values])[:, 0]
                pred["p05"], pred["p95"] = float(np.percentile(ps, 5)), float(np.percentile(ps, 95))
            out[m] = pred
        return out

    def simulate(self, case: PatientCase, scenario: Scenario | dict, explain: bool = True) -> SimulationResult:
        scenario = Scenario.from_dict(scenario) if isinstance(scenario, dict) else scenario
        applied = apply_scenario(case.values, scenario, self.levers, self.art.median)
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        if not applied.ok:
            return SimulationResult(case, scenario, applied, {}, "REJECTED", now)
        changed = {e["feature"] for e in applied.edits}
        outcomes = {}
        for m in case.models:
            tm = self.models[m]
            b, s = tm.predict(case.values), tm.predict(applied.values)
            o = ModelOutcome(m, bool(changed & set(tm.features)), b, s)
            if explain:
                o.shap_baseline, o.shap_base_value = tm.shap(case.values)
                o.shap_scenario, _ = tm.shap(applied.values)
            o.uncertainty = self._uncertainty(tm, case.values, applied.values, o)
            o.out_of_distribution = self._ood(tm, applied.values, changed)
            o.direction_warnings = self._direction(case, scenario, o)
            outcomes[m] = o
        any_change = any(o.affected and abs(o.delta) >= NEGLIGIBLE for o in outcomes.values())
        return SimulationResult(case, scenario, applied, outcomes, "OK" if any_change else "NO_MEANINGFUL_CHANGE", now)

    def compare(self, case: PatientCase, scenarios: Iterable[Scenario | dict]) -> tuple[pd.DataFrame, list[SimulationResult]]:
        res = [self.simulate(case, s) for s in scenarios]
        rows = []
        for r in res:
            if r.status == "REJECTED":
                rows.append(pd.DataFrame([{"scenario": r.scenario.name, "model": "-", "tier_change": "REJECTED",
                                           "robustness": "; ".join(x["reason"] for x in r.applied.rejected)}]))
            else:
                rows.append(r.table())
        return pd.concat(rows, ignore_index=True), res

    # ---------------------------------------------------------------- checks attached to every result
    def _uncertainty(self, tm: TwinModel, v0: dict, v1: dict, o: ModelOutcome) -> dict[str, Any]:
        if not tm.ensemble:
            return {"available": False, "verdict": "no refits for this model (point estimate only)"}
        ps = tm.ensemble_probs([v0, v1])
        d = ps[:, 1] - ps[:, 0]
        main = np.sign(o.delta)
        same = float(np.mean(np.sign(d) == main)) if main != 0 else float(np.mean(np.abs(d) < 1e-9))
        lo, hi = float(np.percentile(d, 5)), float(np.percentile(d, 95))
        if abs(o.delta) < NEGLIGIBLE:
            verdict = "negligible change"
        elif same >= 0.9 and (lo > 0 or hi < 0):
            verdict = "direction consistent across refits"
        else:
            verdict = "within model variance (direction not stable)"
        tiers = {tm.tier(float(np.percentile(ps[:, 1], q))) for q in (5, 95)}
        return {"available": True, "n_refits": int(ps.shape[0]), "baseline_p05": float(np.percentile(ps[:, 0], 5)),
                "baseline_p95": float(np.percentile(ps[:, 0], 95)), "scenario_p05": float(np.percentile(ps[:, 1], 5)),
                "scenario_p95": float(np.percentile(ps[:, 1], 95)), "delta_p05": lo, "delta_p95": hi,
                "same_direction_%": round(100 * same, 1), "scenario_tier_could_differ": len(tiers) > 1, "verdict": verdict,
                "note": "Spread of 50 patient-level bootstrap refits (notebook 07). Model variance, not a validated interval."}

    def _ood(self, tm: TwinModel, v: dict, changed: set) -> list[dict]:
        out = []
        for f in sorted(changed & set(tm.features)):
            r = self.art.reference["features"].get(f)
            if not r or r.get("p005") is None or not finite(v.get(f)):
                continue
            if not r["p005"] <= v[f] <= r["p995"]:
                out.append({"feature": f, "value": round(float(v[f]), 3), "training_range_99%": [round(r["p005"], 3), round(r["p995"], 3)],
                            "warning": "outside the training range: the model is extrapolating"})
        return out

    def _direction(self, case: PatientCase, sc: Scenario, o: ModelOutcome) -> list[str]:
        if not o.affected or abs(o.delta) < NEGLIGIBLE or len(sc.changes) != 1:
            return []
        ch = sc.changes[0]
        e = EXPECTED_DIRECTION.get(ch.lever)
        lv = self.levers.get(ch.lever)
        if e is None or lv is None:
            return []
        cur = lv.current(case.values)
        move = (ch.value - cur) if ch.op == "set" else ch.delta
        if cur is None or move == 0:
            return []
        if np.sign(move) * e != np.sign(o.delta):
            return [f"The {o.model} model's response to {lv.label.lower()} goes against the usual clinical direction. It "
                    "learned this association from 59 training patients; it is not evidence that the change is harmful "
                    "or helpful."]
        return []

    # ---------------------------------------------------------------- exploration tools
    def _batch_prob(self, model: str, values_list: list[dict]) -> np.ndarray:
        tm = self.models[model]
        return tm.prob(pd.concat([tm.matrix(v) for v in values_list], ignore_index=True))

    def sensitivity(self, case: PatientCase, lever: str, values: Iterable[float] | None = None, mode: str = "recent",
                    n: int = 41) -> pd.DataFrame:
        """One-way response curve for this patient (an individual conditional expectation curve)."""
        lv = self.levers[lever]
        cur = lv.current(case.values)
        if values is None:
            if lv.discrete:
                values = list(lv.discrete)
            else:
                pr = plausible_range(lv, self.art.reference) or lv.hard_range
                lo, hi = min(pr[0], cur if cur is not None else pr[0]), max(pr[1], cur if cur is not None else pr[1])
                values = np.linspace(lo, hi, n)
        rows, vals = [], []
        for x in values:
            a = apply_scenario(case.values, Scenario("s", [Change(lever, value=float(x), mode=mode)]), self.levers, self.art.median)
            if a.ok:
                vals.append(a.values)
                rows.append({"lever": lever, "value": float(x)})
        df = pd.DataFrame(rows)
        pr = plausible_range(lv, self.art.reference)
        df["in_training_range"] = True if pr is None else df.value.between(*pr)
        for m in case.models:
            if not set(lv.writes) & set(self.models[m].features):
                continue
            p = self._batch_prob(m, vals)
            df[f"prob_{m}"] = p
            df[f"tier_{m}"] = [self.models[m].tier(x) for x in p]
            ens = self.models[m].ensemble_probs(vals)
            if ens is not None:
                df[f"p05_{m}"], df[f"p95_{m}"] = np.percentile(ens, 5, axis=0), np.percentile(ens, 95, axis=0)
        df.attrs.update({"current": cur, "lever": lever, "label": lv.label, "unit": lv.unit})
        return df

    def tornado(self, case: PatientCase, model: str = "watch", levers: list[str] | None = None) -> pd.DataFrame:
        """Change in risk for one standard step down and up of every available lever (ranked by the larger effect)."""
        base = self.models[model].predict(case.values)["prob"]
        rows = []
        for k in levers or self.available_levers(case, model):
            lv = self.levers[k]
            cur = lv.current(case.values)
            if lv.discrete:
                steps = [("toggle", 1 - cur)]
            else:
                steps = [("down", max(cur - lv.sweep_step, lv.hard_range[0])), ("up", min(cur + lv.sweep_step, lv.hard_range[1]))]
            out = {"lever": k, "label": lv.label, "unit": lv.unit, "current": cur, "step": lv.sweep_step}
            for name, x in steps:
                a = apply_scenario(case.values, Scenario("t", [Change(k, value=float(x))]), self.levers, self.art.median)
                out[f"delta_{name}"] = self.models[model].predict(a.values)["prob"] - base if a.ok else np.nan
            rows.append(out)
        df = pd.DataFrame(rows)
        df["max_abs"] = df.filter(like="delta_").abs().max(axis=1)
        return df.sort_values("max_abs", ascending=False).reset_index(drop=True)

    def surface(self, case: PatientCase, lever_x: str, lever_y: str, model: str = "watch", n: int = 25) -> dict[str, Any]:
        """2-D response surface over two levers (all other inputs held at the patient's values)."""
        axes = []
        for k in (lever_x, lever_y):
            lv = self.levers[k]
            if lv.discrete:
                axes.append(np.array(lv.discrete))
            else:
                pr = plausible_range(lv, self.art.reference) or lv.hard_range
                cur = lv.current(case.values)
                axes.append(np.linspace(min(pr[0], cur), max(pr[1], cur), n))
        vals, idx = [], []
        for i, y in enumerate(axes[1]):
            for j, x in enumerate(axes[0]):
                a = apply_scenario(case.values, Scenario("g", [Change(lever_x, value=float(x)), Change(lever_y, value=float(y))]),
                                   self.levers, self.art.median)
                vals.append(a.values if a.ok else case.values)
                idx.append((i, j, a.ok))
        p = self._batch_prob(model, vals)
        Z = np.full((len(axes[1]), len(axes[0])), np.nan)
        for (i, j, ok), x in zip(idx, p):
            Z[i, j] = x if ok else np.nan
        return {"x": axes[0], "y": axes[1], "z": Z, "lever_x": lever_x, "lever_y": lever_y, "model": model,
                "current": (self.levers[lever_x].current(case.values), self.levers[lever_y].current(case.values)),
                "thresholds": self.models[model].thresholds}

    def counterfactual(self, case: PatientCase, model: str = "watch", target_tier: str = "LOW", max_changes: int = 2,
                       levers: list[str] | None = None, n_grid: int = 6, top: int = 8) -> pd.DataFrame:
        """Smallest changes (in standard steps) to actionable levers that bring the model output below the target tier's
        upper cut-off, searched on a grid within the training range. Model counterfactuals, not treatment options."""
        tm = self.models[model]
        limit = {"LOW": tm.thresholds["moderate"], "MODERATE": tm.thresholds["high"]}[target_tier]
        base = tm.predict(case.values)["prob"]
        if base < limit:
            out = pd.DataFrame()
            out.attrs.update({"baseline_prob": base, "target_tier": target_tier, "limit": limit, "model": model,
                              "searched": 0, "message": f"Already at or below {target_tier}: nothing to search."})
            return out
        keys = levers or self.available_levers(case, model, actionable_only=True)
        cand: dict[str, list[tuple[float, float]]] = {}
        for k in keys:
            lv = self.levers[k]
            cur = lv.current(case.values)
            band = self._healthy_band(case, lv)
            if band is None or band[0] <= cur <= band[1]:
                continue                          # already in the usual target band: never pushed past it
            goal = band[0] if cur < band[0] else band[1]
            if lv.discrete:
                cand[k] = [(float(goal), 1.0)]
                continue
            pr = plausible_range(lv, self.art.reference) or lv.hard_range
            goal = min(max(goal, pr[0]), pr[1])
            grid = np.linspace(cur, goal, n_grid + 1)[1:]
            cand[k] = [(float(round(x, 2)), abs(x - cur) / lv.sweep_step) for x in grid if abs(x - cur) > 1e-9]
        combos = [((k, x, c),) for k in cand for x, c in cand[k]]
        if max_changes >= 2:
            for k1, k2 in itertools.combinations(cand, 2):
                combos += [((k1, x1, c1), (k2, x2, c2)) for x1, c1 in cand[k1] for x2, c2 in cand[k2]]
        vals, ok = [], []
        for combo in combos:
            a = apply_scenario(case.values, Scenario("cf", [Change(k, value=x) for k, x, _ in combo]), self.levers, self.art.median)
            vals.append(a.values)
            ok.append(a.ok)
        p = self._batch_prob(model, vals) if vals else np.array([])
        rows = []
        for combo, pp, good in zip(combos, p, ok):
            if not good:
                continue
            rows.append({"changes": " + ".join(self._fmt(case, k, x) for k, x, _ in combo), "n_changes": len(combo),
                         "cost_steps": round(sum(c for *_, c in combo), 2), "prob": float(pp), "delta": float(pp - base),
                         "tier": tm.tier(float(pp)), "reaches_target": bool(pp < limit),
                         "scenario": {k: x for k, x, _ in combo}})
        df = pd.DataFrame(rows)
        if df.empty:
            return df
        df["route"] = df.scenario.map(lambda d: tuple(sorted(d)))
        hit = df[df.reaches_target].sort_values(["n_changes", "cost_steps", "prob"])
        # keep the best option per set of levers, so the list shows different routes rather than one route many times
        if hit.empty:
            out = df.sort_values("prob").drop_duplicates("route").head(top).assign(
                note="target not reachable by moving these levers toward their usual target bands; closest options")
        else:
            out = hit.drop_duplicates("route").head(top).assign(note="model counterfactual (not a treatment recommendation)")
        out = out.drop(columns="route")
        out.attrs.update({"baseline_prob": base, "target_tier": target_tier, "limit": limit, "model": model,
                          "searched": len(combos)})
        return out.reset_index(drop=True)

    @staticmethod
    def _healthy_band(case: PatientCase, lv: Lever) -> tuple[float, float] | None:
        if lv.key == "weight":                    # BMI 18.5-24.9 expressed in kg for this patient's height
            h = case.values.get("height_in")
            return None if not finite(h) else (18.5 * h ** 2 / 703 / 2.20462, 24.9 * h ** 2 / 703 / 2.20462)
        return lv.healthy

    def _fmt(self, case: PatientCase, k: str, x: float) -> str:
        lv = self.levers[k]
        cur = lv.current(case.values)
        if lv.discrete == (0.0, 1.0):
            return f"{lv.label}: {'yes' if x else 'no'}"
        return f"{lv.label} {cur:.4g}->{x:.4g} {lv.unit}"

    def population(self, scenarios: list[Scenario], model: str = "watch", include_synthetic: bool = False) -> pd.DataFrame:
        """Run the same scenarios for every available test patient (each patient's own baseline)."""
        rows = []
        for cid in self.patients.ids(include_synthetic):
            case = self.patients.get(cid)
            if model not in case.models:
                continue
            for s in scenarios:
                r = self.simulate(case, s, explain=False)
                if r.status == "REJECTED" or model not in r.outcomes:
                    rows.append({"case_id": cid, "scenario": s.name, "status": "REJECTED"})
                    continue
                o = r.outcomes[model]
                rows.append({"case_id": cid, "scenario": s.name, "status": r.status, "baseline_prob": o.baseline["prob"],
                             "scenario_prob": o.scenario["prob"], "delta": o.delta, "baseline_tier": o.baseline["tier"],
                             "scenario_tier": o.scenario["tier"], "robustness": (o.uncertainty or {}).get("verdict")})
        return pd.DataFrame(rows)

    # ---------------------------------------------------------------- reading-level twin
    def synthetic_case(self) -> PatientCase:
        return self.patients.get("synthetic-demo")

    def reading_scenario(self, case: PatientCase, edits: list[dict], hours: int = 24) -> dict[str, Any]:
        if case.request is None:
            raise ValueError("This case has no raw readings; use the feature-level simulate() instead.")
        out = self.reading_twin.compare(case.request, edits, hours)
        tm = self.models["watch"]
        b, s = tm.predict(out["baseline_values"]), tm.predict(out["scenario_values"])
        phi_b, base = tm.shap(out["baseline_values"])
        phi_s, _ = tm.shap(out["scenario_values"])
        out.update({"baseline": b, "scenario": s, "delta": s["prob"] - b["prob"], "shap_baseline": phi_b,
                    "shap_scenario": phi_s, "shap_base_value": base, "label": LABEL})
        return out

    def engine_whatif(self, request: dict, changes: dict[str, float], role: str = "clinician") -> dict[str, Any]:
        """The notebook 07 engine's own what-if wrapper (full pipeline: validation, emergency rules, freshness gate,
        policy, SAR) when the complete engine is importable; otherwise a clear UNAVAILABLE result."""
        if not self.art.full_engine_available:
            return {"status": "UNAVAILABLE", "reason": "The full risk engine needs twin_rules.py (emergency symptom rules), "
                    "which is not in this folder. The simulator's own reading-level twin is used instead."}
        if self._engine is None:
            import risk_engine
            self._engine = risk_engine.RiskEngine(self.art.paths.root, self.art.cfg)
        return self.art.engine["whatif"].simulate(self._engine, request, changes, role=role)


def load_simulator(root=None) -> WhatIfSimulator:
    return WhatIfSimulator(load_artifacts(root))


def prob_from_log_odds(x: float) -> float:
    return sigmoid(x) if math.isfinite(x) else float("nan")
