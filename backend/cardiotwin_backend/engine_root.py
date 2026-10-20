# Finds the CardioTwin project and gives the risk engine the folder layout it expects, without copying or retraining
# anything. The engine reads ROOT/models/..., ROOT/risk_engine/artifacts/... and imports twin_preprocess / twin_rules.
# Two project layouts exist:
#   flat  (the laptop folder):  ROOT/models, ROOT/risk_engine/artifacts, ROOT/twin_preprocess.py
#   split (the shared folder):  ROOT/model_training/models, ROOT/risk_engine/risk_engine/artifacts, ROOT/twin_preprocess.py
# For the split layout a small runtime folder of links (or copies, where links are not allowed) is assembled.
from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
MODEL_FILE = "cardiotwin_watch_24h.pkl"


@dataclass(frozen=True)
class EngineRoot:
    project: Path          # the CardioTwin project folder
    engine_root: Path      # the folder passed to RiskEngine(root=...)
    layout: str            # "flat" or "split"

    @property
    def whatif_dir(self) -> Path | None:
        p = self.project / "what_if_simulator"
        return p if (p / "whatif_twin").exists() else None


def find_project(start: Path | None = None) -> Path:
    env = os.environ.get("CARDIOTWIN_PROJECT_ROOT")
    if env:
        return Path(env).resolve()
    for d in [Path(start or BACKEND_DIR).resolve(), *Path(start or BACKEND_DIR).resolve().parents]:
        if (d / "models" / MODEL_FILE).exists() or (d / "model_training" / "models" / MODEL_FILE).exists():
            return d
    raise FileNotFoundError("CardioTwin project not found above the backend folder; set CARDIOTWIN_PROJECT_ROOT.")


def _link(src: Path, dst: Path) -> None:
    if dst.is_symlink() or dst.exists():
        if dst.is_symlink() and dst.resolve() == src.resolve():
            return
        shutil.rmtree(dst) if dst.is_dir() and not dst.is_symlink() else dst.unlink()
    try:
        dst.symlink_to(src, target_is_directory=src.is_dir())
    except OSError:                                   # e.g. Windows without developer mode
        shutil.copytree(src, dst) if src.is_dir() else shutil.copy2(src, dst)


MODULES = ("twin_preprocess", "twin_rules", "cardiotwin_ml")     # training code the engine and the pickled models import


def _find_module(project: Path, name: str) -> Path:
    for p in (project / f"{name}.py", project / "model_training" / f"{name}.py", BACKEND_DIR / "vendor" / f"{name}.py"):
        if p.exists():
            return p
    raise FileNotFoundError(f"{name}.py not found in the project or backend/vendor")


def resolve(project: Path | None = None, runtime_dir: Path | None = None) -> EngineRoot:
    project = Path(project).resolve() if project else find_project()
    if (project / "models" / MODEL_FILE).exists() and (project / "risk_engine" / "artifacts").exists():
        root, layout = project, "flat"
        for mod in MODULES:
            src = _find_module(project, mod)
            if src.parent != project and str(src.parent) not in sys.path:
                sys.path.insert(0, str(src.parent))
    else:
        root, layout = Path(runtime_dir or BACKEND_DIR / ".runtime" / "engine_root"), "split"
        root.mkdir(parents=True, exist_ok=True)
        _link(project / "model_training" / "models", root / "models")
        _link(project / "risk_engine" / "risk_engine", root / "risk_engine")
        for mod in MODULES:
            _link(_find_module(project, mod), root / f"{mod}.py")
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    return EngineRoot(project=project, engine_root=root, layout=layout)
