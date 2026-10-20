# Risk policy: maps the model output to LOW / MODERATE / HIGH using versioned, configurable cut-offs. The ML tier is
# kept separate from the emergency state: a HIGH tier is a warning for review, never a diagnosis or an ambulance call.
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .schemas import Tier

ORDER = {Tier.LOW: 0, Tier.MODERATE: 1, Tier.HIGH: 2}


class RiskPolicy:
    def __init__(self, root: Path, cfg: dict):
        rc = cfg["risk_tiers"]
        ops = json.loads((Path(root) / rc["source"]).read_text())[cfg["model"]["feature_set"]]
        self.thresholds = {"moderate": float(ops["operating_points"]["moderate"]), "high": float(ops["operating_points"]["high"])}
        if not 0 < self.thresholds["moderate"] < self.thresholds["high"] < 1:
            raise ValueError("Risk thresholds must satisfy 0 < moderate < high < 1.")
        self.version = cfg["policy_version"]
        self.provisional = bool(rc["provisional"])
        self.rule = rc["rule"]
        self.margin = float(rc["hysteresis_margin"])
        self.allow_uncalibrated = bool(rc["allow_tier_on_uncalibrated"])
        self.allow_missing_calibrator = bool(rc["allow_tier_when_calibrator_missing"])

    def tier(self, p: float) -> Tier:
        """Boundaries are inclusive: p equal to a cut-off goes to the higher tier."""
        if p >= self.thresholds["high"]:
            return Tier.HIGH
        if p >= self.thresholds["moderate"]:
            return Tier.MODERATE
        return Tier.LOW

    def tier_allowed(self, calibration_status: str) -> bool:
        return {"calibrated": True, "uncalibrated": self.allow_uncalibrated,
                "unavailable": self.allow_missing_calibrator}[calibration_status]

    def reason(self, p: float, t: Tier, scale: str) -> str:
        m, h = self.thresholds["moderate"], self.thresholds["high"]
        if t == Tier.HIGH:
            return f"{scale} {p:.4f} is at or above the HIGH cut-off {h:.4f}."
        if t == Tier.MODERATE:
            return f"{scale} {p:.4f} is at or above the MODERATE cut-off {m:.4f} and below the HIGH cut-off {h:.4f}."
        return f"{scale} {p:.4f} is below the MODERATE cut-off {m:.4f}."

    def with_hysteresis(self, p: float, previous: Tier | None) -> Tier:
        """Tier used for alerting: an open alert only steps down when the score falls clearly below its cut-off,
        so a score hovering at a boundary does not make the alert flicker. Step-ups are never delayed."""
        t = self.tier(p)
        if previous is None or ORDER[t] >= ORDER[previous]:
            return t
        cut = self.thresholds["high" if previous == Tier.HIGH else "moderate"]
        return previous if p >= cut * (1 - self.margin) else t

    def describe(self) -> dict[str, Any]:
        return {"policy_version": self.version, "thresholds": self.thresholds, "provisional": self.provisional,
                "rule": self.rule, "hysteresis_margin": self.margin}
