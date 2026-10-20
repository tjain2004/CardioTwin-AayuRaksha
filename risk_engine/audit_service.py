# Privacy-aware audit trail. Each evaluation is recorded with a pseudonymous patient reference, a hash of the input
# snapshot (not the readings themselves), model / policy / template versions, the decision and the alert outcome.
# Input snapshots for audit replay are optional, stored separately and purged after the configured retention.
from __future__ import annotations

import hashlib
import hmac
import json
import os
import threading
import uuid
from pathlib import Path
from typing import Any

import pandas as pd

NAMESPACE = uuid.UUID("0b7e1a52-3c4d-4e8f-9a1b-2c3d4e5f6a7b")


def canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


class Pseudonymizer:
    def __init__(self, cfg: dict):
        key = os.environ.get(cfg["privacy"]["pseudonym_key_env"])
        self.demo_key = key is None
        self.key = (key or cfg["privacy"]["demo_pseudonym_key"]).encode()

    def __call__(self, patient_id: str) -> str:
        return "pt_" + hmac.new(self.key, str(patient_id).encode(), hashlib.sha256).hexdigest()[:16]


class AuditService:
    def __init__(self, cfg: dict, directory: str | Path | None = None):
        p = cfg["privacy"]
        self.dir = Path(directory) if directory else None
        self.store_snapshots = bool(p["store_input_snapshots"])
        self.snapshot_days = int(p["snapshot_retention_days"])
        self.audit_days = int(p["audit_retention_days"])
        self.records: dict[str, dict] = {}
        self.snapshots: dict[str, dict] = {}
        self._lock = threading.RLock()
        if self.dir:
            (self.dir / "records").mkdir(parents=True, exist_ok=True)
            (self.dir / "snapshots").mkdir(parents=True, exist_ok=True)

    def input_reference(self, snapshot: dict) -> str:
        return "in_" + sha256_text(canonical(snapshot))[:32]

    def audit_id(self, patient_ref: str, input_ref: str, now: pd.Timestamp, simulation: bool) -> str:
        return str(uuid.uuid5(NAMESPACE, f"{patient_ref}|{input_ref}|{now.isoformat()}|{simulation}"))

    def record(self, result, snapshot: dict | None, now: pd.Timestamp) -> dict:
        """Write the audit record for a finished result. Contains no names, ids or raw readings."""
        r = result
        rec = {
            "audit_id": r.audit.audit_id, "evaluated_at": str(r.evaluated_at), "recorded_at": str(now),
            "patient_reference": r.patient_reference, "input_reference": r.audit.input_reference,
            "is_demo_data": r.is_demo_data, "simulation": r.simulation,
            "status": r.status.value, "status_reason_code": r.status_reason_code,
            "model_version": r.model.version, "model_sha256": r.model.sha256, "calibration": r.model.calibration_status,
            "policy_version": r.risk.policy_version, "thresholds": r.risk.thresholds,
            "raw_output": r.risk.raw_output, "calibrated_probability": r.risk.calibrated_probability,
            "tier": r.risk.tier.value if r.risk.tier else None,
            "emergency_state": r.emergency.state.value, "emergency_rule": r.emergency.rule_id,
            "emergency_rules_version": r.emergency.rules_version,
            "explanation_status": r.explanation.status.value, "explanation_version": r.explanation.version,
            "top_reasons": [{"reason": c["reason"], "shap_log_odds": c["shap_log_odds"]} for c in r.explanation.top_contributors],
            "sar_template": r.sar.template_version, "sar_guard_passed": r.sar.guard_passed,
            "data_quality": {"status": r.data_quality.status, "stale": r.data_quality.stale_fields,
                             "missing": r.data_quality.missing_fields, "invalid": r.data_quality.invalid_fields,
                             "n_imputed": len(r.data_quality.imputed_fields), "n_ood": len(r.data_quality.out_of_distribution)},
            "alert_decision": {k: r.alert.get(k) for k in ("decision", "alert_id", "kind", "popup", "alert_tier")},
            "degraded_components": r.degraded_components, "result_sha256": r.audit.result_sha256,
            "snapshot_stored": bool(snapshot is not None and self.store_snapshots),
            "retention": {"audit_days": self.audit_days, "snapshot_days": self.snapshot_days},
        }
        with self._lock:
            self.records[rec["audit_id"]] = rec
            if snapshot is not None and self.store_snapshots:
                self.snapshots[rec["audit_id"]] = {"stored_at": str(now), "input_reference": r.audit.input_reference,
                                                   "evaluated_at": str(r.evaluated_at), "snapshot": snapshot}
            if self.dir:
                (self.dir / "records" / f"{rec['audit_id']}.json").write_text(json.dumps(rec, indent=1, default=str))
                if rec["snapshot_stored"]:
                    (self.dir / "snapshots" / f"{rec['audit_id']}.json").write_text(
                        json.dumps(self.snapshots[rec["audit_id"]], default=str))
        return rec

    def purge(self, now: pd.Timestamp) -> dict[str, int]:
        """Delete snapshots and records older than their retention period."""
        n_s = n_r = 0
        with self._lock:
            for aid, s in list(self.snapshots.items()):
                if now - pd.Timestamp(s["stored_at"]) > pd.Timedelta(days=self.snapshot_days):
                    del self.snapshots[aid]; n_s += 1
                    if self.dir and (self.dir / "snapshots" / f"{aid}.json").exists():
                        (self.dir / "snapshots" / f"{aid}.json").unlink()
            for aid, r in list(self.records.items()):
                if now - pd.Timestamp(r["recorded_at"]) > pd.Timedelta(days=self.audit_days):
                    del self.records[aid]; n_r += 1
                    if self.dir and (self.dir / "records" / f"{aid}.json").exists():
                        (self.dir / "records" / f"{aid}.json").unlink()
        return {"snapshots_purged": n_s, "records_purged": n_r}
