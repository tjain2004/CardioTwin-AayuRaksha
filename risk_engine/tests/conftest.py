# Shared test fixtures. The small reading series built here are SYNTHETIC TEST FIXTURES, used only to exercise the
# engine's logic; they are never used for training and never shown as patient data.
import copy
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from risk_engine import RiskEngine, load_config  # noqa: E402
from risk_engine.alert_service import AlertService  # noqa: E402
from risk_engine.audit_service import AuditService  # noqa: E402
from risk_engine.model_service import ModelService  # noqa: E402

NOW = pd.Timestamp("2026-10-18 12:00:00")
SYNTHETIC = "SYNTHETIC TEST FIXTURE"
PROFILE = {"patient_id": "test-patient-001", "age_at_admit": 67, "gender_male": 1, "smoker": 0, "height_in": 68,
           "weight_lbs": 180, "bmi": 27.4, "log_prior_admissions": 0.69, "days_since_last_discharge": -1,
           "n_active_drugs": 8, "ix_bleeding": 0, "has_bled_partial": 1, "ascvd_10y_risk": 0.12, "hist_af": 0, "is_demo": True}
CLINICAL = [{"name": "ldl", "value": 110, "unit": "mg/dL", "timestamp": "2026-06-01T09:00:00Z", "source": SYNTHETIC},
            {"name": "hba1c", "value": 6.1, "unit": "%", "timestamp": "2026-06-01T09:00:00Z", "source": SYNTHETIC},
            {"name": "egfr", "value": 85, "unit": "mL/min/1.73m2", "timestamp": "2026-06-01T09:00:00Z", "source": SYNTHETIC}]


def make_readings(hours=30, end=NOW - pd.Timedelta(minutes=5), every_min=20, hr=78.0, spo2=97.0, rr=16.0,
                  signals=("hr", "spo2", "rr"), quality=0.95, rhythm="sinus"):
    """Synthetic watch readings ending at `end`, one every `every_min` minutes, with a small deterministic wobble."""
    n = int(hours * 60 / every_min)
    out = []
    for i in range(n):
        t = end - pd.Timedelta(minutes=every_min * (n - 1 - i))
        w = ((i * 7) % 5) - 2
        vals = {"hr": hr + w, "spo2": spo2 - abs(w) * 0.25, "rr": rr + (w % 2)}
        for s in signals:
            out.append({"timestamp": t.isoformat() + "Z", "signal": s, "value": vals[s],
                        "unit": {"hr": "bpm", "spo2": "%", "rr": "breaths/min"}[s], "source": SYNTHETIC, "quality": quality})
        if rhythm and i % 3 == 0:
            out.append({"timestamp": t.isoformat() + "Z", "signal": "rhythm", "value": rhythm, "source": SYNTHETIC})
    return out


@pytest.fixture(scope="session")
def cfg():
    return load_config()


@pytest.fixture(scope="session")
def model(cfg):
    return ModelService(ROOT, cfg)


@pytest.fixture
def engine(cfg, model):
    return RiskEngine(ROOT, cfg, model_service=model, alert_service=AlertService(cfg), audit_service=AuditService(cfg))


@pytest.fixture
def request_ok():
    return {"patient_profile": copy.deepcopy(PROFILE), "clinical_readings": copy.deepcopy(CLINICAL),
            "wearable_readings": make_readings(), "now": NOW}


class FixedModel(ModelService):
    """Real model service whose output is pinned to `p` (to test policy and alert logic at chosen scores)."""

    def __init__(self, base: ModelService, p: float):
        self.__dict__.update(base.__dict__)
        self.p = p

    def raw(self, X):
        self.validate_matrix(X)
        return np.full(len(X), self.p)


@pytest.fixture
def fixed_engine(cfg, model):
    def make(p, alerts=None):
        return RiskEngine(ROOT, cfg, model_service=FixedModel(model, p), alert_service=alerts or AlertService(cfg),
                          audit_service=AuditService(cfg))
    return make
