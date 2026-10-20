# One alert table for the apps. Two origins:
#   engine  - risk and emergency alerts decided by the risk engine's AlertService (mirrored here; its lifecycle rules apply)
#   safety  - the backend safety layer's alerts: fall check, possible AF, long QTc, dose over limit
# Nothing here contacts emergency services. Every emergency carries dispatch_status NOT_DISPATCHED.
from __future__ import annotations

import uuid
from typing import Any

from .db import Database, dumps, loads
from .readings import iso, utcnow

OPEN_STATES = ("NEW", "DISPLAYED", "AWAITING_RESPONSE", "ACKNOWLEDGED", "ESCALATED")


class AlertStore:
    def __init__(self, db: Database):
        self.db = db

    # ---------------------------------------------------------------- read
    def get(self, alert_id: str) -> dict | None:
        r = self.db.one("SELECT * FROM alerts WHERE alert_id=?", (alert_id,))
        return self._public(r) if r else None

    def list(self, patient_id: str, open_only: bool = False, limit: int = 100) -> list[dict]:
        q = "SELECT * FROM alerts WHERE patient_id=?"
        if open_only:
            q += f" AND state IN ({','.join('?' * len(OPEN_STATES))})"
        q += " ORDER BY created_at DESC LIMIT ?"
        params = [patient_id, *(OPEN_STATES if open_only else ()), limit]
        return [self._public(r) for r in self.db.all(q, params)]

    def open_of_kind(self, patient_id: str, kind: str) -> list[dict]:
        return [a for a in self.list(patient_id, open_only=True) if a["kind"] == kind]

    def overdue_fall_checks(self, now_iso: str) -> list[dict]:
        rows = self.db.all("SELECT * FROM alerts WHERE kind='FALL' AND state='AWAITING_RESPONSE' AND deadline_at<=?", (now_iso,))
        return [self._public(r) for r in rows]

    @staticmethod
    def _public(r: dict) -> dict:
        a = dict(r)
        a["payload"] = loads(a.pop("payload_json"), {})
        a["history"] = loads(a.pop("history_json"), [])
        a["simulated"] = bool(a["simulated"])
        return a

    # ---------------------------------------------------------------- write
    def create(self, patient_id: str, kind: str, severity: str, title: str, message: str, payload: dict,
               state: str = "NEW", rule_id: str | None = None, deadline_at: str | None = None, simulated: bool = False,
               origin: str = "safety", alert_id: str | None = None) -> dict:
        now = iso(utcnow())
        aid = alert_id or str(uuid.uuid4())
        hist = [{"event": state, "at": now, "actor": "system"}]
        self.db.execute("INSERT INTO alerts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (aid, patient_id, origin, kind, severity, state, rule_id, title, message, now, now, deadline_at,
                         int(simulated), dumps(payload), dumps(hist)))
        return self.get(aid)

    def transition(self, alert_id: str, state: str | None, event: str, actor: str, **info) -> dict:
        with self.db.lock:
            a = self.db.one("SELECT * FROM alerts WHERE alert_id=?", (alert_id,))
            if not a:
                raise KeyError(alert_id)
            hist = loads(a["history_json"], [])
            hist.append({"event": event, "at": iso(utcnow()), "actor": actor, **info})
            self.db.execute("UPDATE alerts SET state=?, updated_at=?, history_json=? WHERE alert_id=?",
                            (state or a["state"], iso(utcnow()), dumps(hist), alert_id))
        return self.get(alert_id)

    def update_payload(self, alert_id: str, **changes: Any) -> dict:
        with self.db.lock:
            a = self.db.one("SELECT * FROM alerts WHERE alert_id=?", (alert_id,))
            sets = {k: v for k, v in changes.items() if k in ("kind", "severity", "title", "message", "rule_id", "deadline_at")}
            if "payload" in changes:
                sets["payload_json"] = dumps(changes["payload"])
            if sets:
                cols = ", ".join(f"{k}=?" for k in sets)
                self.db.execute(f"UPDATE alerts SET {cols}, updated_at=? WHERE alert_id=?", (*sets.values(), iso(utcnow()), alert_id))
        return self.get(alert_id)

    def mirror_engine(self, patient_id: str, engine_alert: dict, simulated: bool) -> dict:
        """Upsert an alert owned by the risk engine's AlertService. Its state and history are the engine's."""
        a = engine_alert
        kind = a["kind"]
        title = "Emergency warning" if kind == "EMERGENCY" else f"{a.get('tier') or ''} risk".strip().capitalize()
        payload = {k: a.get(k) for k in ("key", "tier", "audit_id", "model_version", "policy_version", "notifications",
                                         "user_actions", "review", "resolution", "superseded_by", "acknowledgements")}
        row = (patient_id, "engine", kind, a["severity"], a["state"], a.get("rule_id"), title, None, a["created_at"],
               iso(utcnow()), None, int(simulated), dumps(payload), dumps(a.get("history", [])))
        with self.db.lock:
            prev = self.db.one("SELECT payload_json FROM alerts WHERE alert_id=?", (a["alert_id"],))
            if prev:
                # keep the emergency modal stored when the alert opened, so a critical patient keeps the 112 screen
                old = loads(prev["payload_json"], {})
                if old.get("emergency") and not payload.get("emergency"):
                    payload["emergency"] = old["emergency"]
                self.db.execute("UPDATE alerts SET state=?, severity=?, updated_at=?, payload_json=?, history_json=? WHERE alert_id=?",
                                (a["state"], a["severity"], iso(utcnow()), dumps(payload), dumps(a.get("history", [])), a["alert_id"]))
            else:
                self.db.execute("INSERT INTO alerts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (a["alert_id"], *row))
        return self.get(a["alert_id"])
