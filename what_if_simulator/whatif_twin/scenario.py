# Scenarios: one or more lever changes applied together to a deep copy of the patient's model inputs. The original
# values are never modified. Every edit is validated (supported, within the accepted range, no conflicting levers,
# physiologically consistent) and recorded with its old and new value so the result is fully traceable.
from __future__ import annotations

import copy
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np

from .artifacts import finite
from .levers import Lever, resolve

CONFLICTS = [{"weight", "bmi"}]


@dataclass
class Change:
    lever: str
    value: float | None = None          # set the lever to this value ...
    delta: float | None = None          # ... or shift it by this amount
    mode: str = "recent"                # recent: last 6 hourly bins | persistent: the whole history (vital signs)

    @property
    def op(self) -> str:
        return "set" if self.value is not None else "shift"

    def describe(self, lever: Lever | None = None) -> str:
        name, unit = (lever.label, lever.unit) if lever else (self.lever, "")
        unit = "" if unit in ("0/1", "count") else f" {unit}"
        if lever and lever.discrete == (0.0, 1.0) and self.value is not None:
            return f"{name}: {'yes' if self.value else 'no'}"
        txt = f"{name} set to {self.value:g}{unit}" if self.op == "set" else f"{name} {self.delta:+g}{unit}"
        return txt + (" (whole history)" if self.mode == "persistent" and lever and lever.category == "physiological_signal" else "")


@dataclass
class Scenario:
    name: str
    changes: list[Change] = field(default_factory=list)
    description: str = ""

    @classmethod
    def of(cls, name: str, description: str = "", **changes: dict | float) -> "Scenario":
        """Scenario.of("Healthier", heart_rate={"delta": -10}, smoker=0, weight={"delta": -5})"""
        out = []
        for k, c in changes.items():
            c = {"value": c} if not isinstance(c, dict) else c
            out.append(Change(lever=k, **c))
        return cls(name=name, changes=out, description=description)

    @classmethod
    def from_dict(cls, d: dict) -> "Scenario":
        ch = d.get("changes", {})
        items = ch.items() if isinstance(ch, dict) else [(c["lever"], {k: v for k, v in c.items() if k != "lever"}) for c in ch]
        return cls(name=d.get("name", "scenario"), description=d.get("description", ""),
                   changes=[Change(lever=k, **({"value": c} if not isinstance(c, dict) else c)) for k, c in items])

    def to_dict(self) -> dict:
        return {"name": self.name, "description": self.description, "changes": [asdict(c) for c in self.changes]}


@dataclass
class AppliedScenario:
    values: dict[str, float]
    edits: list[dict[str, Any]]
    notes: list[str]
    held: list[str]
    rejected: list[dict[str, str]]
    changes_text: list[str]

    @property
    def ok(self) -> bool:
        return not self.rejected


def apply_scenario(values: dict, scenario: Scenario, levers: dict[str, Lever], median) -> AppliedScenario:
    """Apply every change in order to a deep copy of `values` (union of both models' inputs for one patient-hour)."""
    v0 = copy.deepcopy(values)
    v = copy.deepcopy(values)
    notes: list[str] = []
    rejected: list[dict[str, str]] = []
    held: list[str] = []
    texts: list[str] = []
    keys = []
    for ch in scenario.changes:
        key, why = resolve(ch.lever, levers)
        if key is None:
            rejected.append({"lever": ch.lever, "reason": why})
            continue
        ch.lever = key
        keys.append(key)
    for pair in CONFLICTS:
        if pair <= set(keys):
            rejected.append({"lever": " + ".join(sorted(pair)), "reason": "These levers describe the same quantity; change one."})
    if len(keys) != len(set(keys)):
        rejected.append({"lever": ",".join(keys), "reason": "The same lever appears twice in one scenario."})
    if rejected:
        return AppliedScenario(v0, [], notes, held, rejected, texts)

    for ch in scenario.changes:
        lv = levers[ch.lever]
        if (ch.value is None) == (ch.delta is None):
            rejected.append({"lever": lv.key, "reason": "Give exactly one of value (set) or delta (shift)."})
            continue
        if ch.mode not in ("recent", "persistent"):
            rejected.append({"lever": lv.key, "reason": "mode must be 'recent' or 'persistent'."})
            continue
        cur = lv.current(v)
        if ch.op == "shift" and cur is None:
            rejected.append({"lever": lv.key, "reason": f"{lv.label} is not recorded for this patient, so it cannot be "
                                                        "shifted; set a value instead."})
            continue
        target = float(ch.value) if ch.op == "set" else cur + float(ch.delta)
        lo, hi = lv.hard_range
        if not np.isfinite(target) or not lo <= target <= hi:
            rejected.append({"lever": lv.key, "reason": f"{lv.label} {target:g} is outside the accepted range {lo:g}-{hi:g} {lv.unit}."})
            continue
        if lv.discrete and target not in lv.discrete:
            rejected.append({"lever": lv.key, "reason": f"{lv.label} takes only the values {lv.discrete}."})
            continue
        try:
            lv.apply(v, dict(v), target, ch, {"notes": notes, "median": median})
        except ValueError as e:
            rejected.append({"lever": lv.key, "reason": str(e)})
            continue
        held += [h for h in lv.held if h in v and h not in held]
        texts.append(ch.describe(lv))

    # physiological consistency of the edited hour
    if finite(v.get("sbp_outpatient")) and finite(v.get("dbp_outpatient")) and v["dbp_outpatient"] >= v["sbp_outpatient"]:
        rejected.append({"lever": "clinic_sbp/clinic_dbp", "reason": "Diastolic BP must be below systolic BP."})
    for p in ("hr", "rr", "spo2"):
        lo_, mean_, hi_ = v.get(f"{p}_min6h"), v.get(f"{p}_mean6h"), v.get(f"{p}_max6h")
        if all(finite(x) for x in (lo_, mean_)) and lo_ > mean_ + 1e-9:
            v[f"{p}_min6h"] = mean_
        if all(finite(x) for x in (hi_, mean_)) and hi_ < mean_ - 1e-9:
            v[f"{p}_max6h"] = mean_

    edits = []
    for f in v:
        a, b = v0.get(f), v.get(f)
        same = (not finite(a) and not finite(b)) or (finite(a) and finite(b) and abs(float(a) - float(b)) < 1e-9)
        if not same:
            edits.append({"feature": f, "original": None if not finite(a) else float(a), "simulated": None if not finite(b) else float(b),
                          "was_missing": not finite(a)})
    return AppliedScenario(v, edits, list(dict.fromkeys(notes)), held, rejected, texts)
