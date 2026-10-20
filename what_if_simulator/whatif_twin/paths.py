# Finds the project's saved artifacts. Two layouts are supported without copying anything:
#   flat     (the project folder on the laptop):   ROOT/models, ROOT/results, ROOT/risk_engine/artifacts, ...
#   split    (the shared project folder):          ROOT/model_training/models, ROOT/risk_engine/risk_engine/artifacts, ...
# Set WHATIF_PROJECT_ROOT to point at the project root explicitly.
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

MODEL_FILE = "cardiotwin_watch_24h.pkl"


def _first(root: Path, *candidates: str) -> Path | None:
    for c in candidates:
        p = root / c
        if p.exists():
            return p
    return None


def find_root(start: str | Path | None = None) -> Path:
    env = os.environ.get("WHATIF_PROJECT_ROOT")
    if env:
        return Path(env).resolve()
    here = Path(start or Path.cwd()).resolve()
    for d in [here, *here.parents, Path(__file__).resolve().parents[1], *Path(__file__).resolve().parents[2:]]:
        if (d / "models" / MODEL_FILE).exists() or (d / "model_training" / "models" / MODEL_FILE).exists():
            return d
    raise FileNotFoundError("Project root not found: no models/cardiotwin_watch_24h.pkl above "
                            f"{here}. Set WHATIF_PROJECT_ROOT to the CardioTwin project folder.")


@dataclass(frozen=True)
class ProjectPaths:
    root: Path
    models: Path                   # notebook 05 model bundles, schema, operating points
    results05: Path                # notebook 05 results (test_predictions.csv, ...)
    engine_parent: Path            # folder that contains the risk_engine/ package (notebook 07)
    explanations: Path | None      # notebook 06 results/explanations
    code_dirs: tuple[Path, ...]    # folders holding cardiotwin_ml.py and twin_preprocess.py
    features_table: Path | None    # data/features/model_final.csv (laptop only)
    dynamic_clean: Path | None     # data/cleaned/dynamic_clean.csv (laptop only)

    @property
    def engine_pkg(self) -> Path:
        return self.engine_parent / "risk_engine"

    @property
    def layout(self) -> str:
        return "flat" if self.models == self.root / "models" else "split"

    def add_to_sys_path(self) -> None:
        for d in (*self.code_dirs, self.engine_parent):
            if str(d) not in sys.path:
                sys.path.insert(0, str(d))


def project_paths(root: str | Path | None = None) -> ProjectPaths:
    root = Path(root).resolve() if root else find_root()
    models = _first(root, "models", "model_training/models")
    if models is None or not (models / MODEL_FILE).exists():
        raise FileNotFoundError(f"No saved models under {root}")
    results05 = _first(root, "results/test_predictions.csv", "model_training/results/test_predictions.csv").parent
    engine_parent = root if (root / "risk_engine" / "artifacts").exists() else root / "risk_engine"
    if not (engine_parent / "risk_engine" / "artifacts").exists():
        raise FileNotFoundError("risk_engine/artifacts not found (run notebook 07 first)")
    explanations = _first(root, "results/explanations", "shap_explanations/results/explanations")
    code_dirs = tuple(dict.fromkeys(p.parent for p in [_first(root, "cardiotwin_ml.py", "src/cardiotwin_ml.py", "model_training/cardiotwin_ml.py"),
                                                         _first(root, "twin_preprocess.py", "src/twin_preprocess.py")] if p is not None))
    return ProjectPaths(root=root, models=models, results05=results05, engine_parent=engine_parent,
                        explanations=explanations, code_dirs=code_dirs,
                        features_table=_first(root, "data/features/model_final.csv"),
                        dynamic_clean=_first(root, "data/cleaned/dynamic_clean.csv"))
