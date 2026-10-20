# Loads every saved artifact the simulator reuses, once, and checks it before use. Nothing is fitted here.
#   * notebook 05: both model bundles (fitted imputer/scaler/model + calibrator), feature schema, operating points
#   * notebook 07: risk-policy config (what-if ranges, roles), training reference (ranges, medians), SHAP background,
#                  bootstrap refits of the watch model, integrity manifest (SHA-256 of the watch model file)
#   * notebook 07 engine code: explain_text (wording, groups), shap_service (exact linear SHAP), input_validator and
#                  feature_builder (the training feature code applied to raw readings)
from __future__ import annotations

import hashlib
import importlib
import importlib.machinery
import importlib.util
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import yaml

from .paths import ProjectPaths, project_paths

ENGINE_MODULES = ("schemas", "explain_text", "shap_service", "input_validator", "feature_builder", "risk_policy")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_engine_modules(paths: ProjectPaths) -> tuple[dict[str, Any], bool]:
    """Import the risk-engine modules the simulator reuses. Returns (modules, full_engine_available).

    The full engine package also needs twin_rules.py (emergency symptom rules). Where that file is not present, the
    package's __init__ cannot run, so the reused submodules are imported directly from the package folder instead;
    the full engine (and its what-if wrapper) is then reported as unavailable rather than imitated."""
    paths.add_to_sys_path()
    full = False
    try:
        import risk_engine  # noqa: F401
        full = hasattr(risk_engine, "RiskEngine")
    except ImportError:
        for k in [k for k in sys.modules if k == "risk_engine" or k.startswith("risk_engine.")]:
            del sys.modules[k]
        spec = importlib.machinery.ModuleSpec("risk_engine", None, is_package=True)
        pkg = importlib.util.module_from_spec(spec)
        pkg.__path__ = [str(paths.engine_pkg)]
        sys.modules["risk_engine"] = pkg
    mods = {m: importlib.import_module(f"risk_engine.{m}") for m in ENGINE_MODULES}
    if full:
        mods["whatif"] = importlib.import_module("risk_engine.whatif")
    return mods, full


@dataclass
class Artifacts:
    paths: ProjectPaths
    bundles: dict[str, dict]
    schema: dict
    operating_points: dict
    metadata: dict
    cfg: dict
    reference: dict
    background: dict
    ensemble: dict | None
    manifest: dict
    engine: dict[str, Any]
    full_engine_available: bool
    hashes: dict[str, str] = field(default_factory=dict)
    integrity: dict[str, Any] = field(default_factory=dict)

    @property
    def T(self):                       # explain_text module (labels, groups, reference ranges)
        return self.engine["explain_text"]

    def feature_ranges(self) -> dict[str, dict]:
        return self.reference["features"]

    def median(self, f: str) -> float | None:
        v = self.reference.get("medians", {}).get(f)
        return None if v is None else float(v)


def load_artifacts(root: str | Path | None = None, check_integrity: bool = True) -> Artifacts:
    paths = project_paths(root)
    paths.add_to_sys_path()
    import cardiotwin_ml  # noqa: F401  (the saved bundles pickle its Calibrator class)

    art_dir = paths.engine_pkg / "artifacts"
    manifest = json.loads((art_dir / "manifest.json").read_text())
    hashes = {s: sha256_file(paths.models / f"cardiotwin_{s}_24h.pkl") for s in ("watch", "full")}
    integrity = {"watch_model_sha256_matches_manifest": hashes["watch"] == manifest["model_sha256"],
                 "manifest_model_version": manifest.get("model_version")}
    if check_integrity and not integrity["watch_model_sha256_matches_manifest"]:
        raise RuntimeError("The watch model file does not match the SHA-256 recorded by notebook 07; refusing to simulate.")
    for name, expected in manifest.get("artifacts", {}).items():
        p = art_dir / name
        integrity[f"{name}_sha256_matches"] = p.exists() and sha256_file(p) == expected

    bundles = {s: joblib.load(paths.models / f"cardiotwin_{s}_24h.pkl") for s in ("watch", "full")}
    schema = json.loads((paths.models / "feature_schema.json").read_text())
    for s, b in bundles.items():
        order = [f["name"] for f in sorted(schema[s]["features"], key=lambda f: f["position"])]
        if order != list(b["features"]):
            raise RuntimeError(f"{s} model features do not match models/feature_schema.json")
    engine, full = load_engine_modules(paths)
    reference = json.loads((art_dir / "training_reference.json").read_text())
    engine["explain_text"].MED.update(reference["medians"])
    ens_path = art_dir / "bootstrap_ensemble.joblib"
    return Artifacts(
        paths=paths, bundles=bundles, schema=schema,
        operating_points=json.loads((paths.models / "operating_points.json").read_text()),
        metadata=json.loads((paths.models / "model_metadata.json").read_text()),
        cfg=yaml.safe_load((paths.engine_pkg / "config" / "risk_policy.yaml").read_text()),
        reference=reference, background=json.loads((art_dir / "shap_background.json").read_text()),
        ensemble=joblib.load(ens_path) if ens_path.exists() else None, manifest=manifest,
        engine=engine, full_engine_available=full, hashes=hashes, integrity=integrity)


def finite(x) -> bool:
    try:
        return x is not None and np.isfinite(float(x))
    except (TypeError, ValueError):
        return False
