# Alert lifecycle: NEW -> DISPLAYED -> ACKNOWLEDGED -> ESCALATED -> RESOLVED, with de-duplication, a cooldown for
# repeated non-emergency popups, notification tracking and role checks. A cooldown never holds back a newly detected
# emergency, acknowledging never means the patient is better, and missing data never resolves an alert.
from __future__ import annotations

import json
import threading
import uuid
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from .schemas import AlertState, EmergencyState, Status, Tier

NAMESPACE = uuid.UUID("6f1c3d2e-8a4b-4c5d-9e6f-a1b2c3d4e5f6")
TIER_ORDER = {"LOW": 0, "MODERATE": 1, "HIGH": 2}


class PermissionDenied(PermissionError):
    pass


class AlertService:
    def __init__(self, cfg: dict, store_path: str | Path | None = None, notifier: Callable[[dict], bool] | None = None,
                 roles: dict | None = None):
        self.cooldown = pd.Timedelta(minutes=cfg["alerts"]["cooldown_minutes"])
        self.resolve_role = cfg["alerts"]["resolve_requires_role"]
        self.roles = roles or cfg["privacy"]["roles"]
        self.path = Path(store_path) if store_path else None
        self.notifier = notifier
        self._lock = threading.RLock()
        self.alerts: dict[str, dict[str, Any]] = {}
        self._seen_events: dict[str, str] = {}
        if self.path and self.path.exists():
            data = json.loads(self.path.read_text())
            self.alerts, self._seen_events = data["alerts"], data["events"]

    # ---------------------------------------------------------------- helpers
    def _save(self):
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"alerts": self.alerts, "events": self._seen_events}, indent=1, default=str))
            tmp.replace(self.path)

    def _check(self, role: str, permission: str):
        if permission not in self.roles.get(role, []):
            raise PermissionDenied(f"Role '{role}' may not '{permission}'.")

    def _history(self, a, event, now, actor=None, **kw):
        a["history"].append({"event": event, "at": str(now), "actor": actor, **kw})

    def open_alerts(self, patient_ref: str, kind: str | None = None) -> list[dict]:
        return [a for a in self.alerts.values() if a["patient_reference"] == patient_ref
                and a["state"] != AlertState.RESOLVED.value and (kind is None or a["kind"] == kind)]

    # ---------------------------------------------------------------- creation / decision
    def process(self, result, now: pd.Timestamp, event_id: str, policy=None) -> dict[str, Any]:
        """Decide what alert (if any) this evaluation raises. Idempotent: the same event_id returns the same decision."""
        with self._lock:
            if event_id in self._seen_events:
                return json.loads(self._seen_events[event_id])
            ref = result.patient_reference
            decision: dict[str, Any] = {"popup": False, "alert_id": None, "kind": None, "decision": "NO_ALERT", "reason": None}
            em = result.emergency
            if em.state == EmergencyState.TRIGGERED:
                key = f"EMERGENCY:{em.rule_id}"
                same = [a for a in self.open_alerts(ref, "EMERGENCY") if a["key"] == key]
                if same:
                    a = same[0]
                    a["last_seen"] = str(now)
                    self._history(a, "RETRIGGERED", now)
                    decision.update(alert_id=a["alert_id"], kind="EMERGENCY", decision="DEDUPLICATED",
                                    reason="The same emergency is already open; the red alert stays open, no second popup.")
                else:
                    a = self._new(ref, "EMERGENCY", key, now, result, severity="critical")
                    decision.update(popup=True, alert_id=a["alert_id"], kind="EMERGENCY", decision="NEW_EMERGENCY",
                                    reason="New emergency rule met: shown immediately; cooldowns never apply to emergencies.")
            elif result.status == Status.OK and result.risk.tier in (Tier.MODERATE, Tier.HIGH):
                p = result.risk.calibrated_probability if result.risk.calibrated_probability is not None else result.risk.raw_output
                opens = self.open_alerts(ref, "RISK")
                prev = Tier(max((a["tier"] for a in opens), key=TIER_ORDER.get)) if opens else None
                tier = policy.with_hysteresis(p, prev) if policy else result.risk.tier
                key = f"RISK:{tier.value}"
                recent = [a for a in self.alerts.values() if a["patient_reference"] == ref and a["kind"] == "RISK"
                          and now - pd.Timestamp(a["created_at"]) < self.cooldown]
                escalation = prev is not None and TIER_ORDER[tier.value] > TIER_ORDER[prev.value]
                same_open = [a for a in opens if a["key"] == key]
                if same_open:
                    a = same_open[0]
                    a["last_seen"] = str(now)
                    decision.update(alert_id=a["alert_id"], kind="RISK", decision="DEDUPLICATED",
                                    reason=f"An open {tier.value} alert already exists for this patient.")
                elif prev is not None and TIER_ORDER[tier.value] < TIER_ORDER[prev.value]:
                    higher = [a for a in opens if a["tier"] == prev.value][0]
                    decision.update(alert_id=higher["alert_id"], kind="RISK", decision="LOWER_TIER_NO_POPUP",
                                    reason=f"Score fell below the open {prev.value} alert; that alert stays open until a clinician resolves it.")
                elif recent and not escalation:
                    decision.update(kind="RISK", decision="SUPPRESSED_COOLDOWN",
                                    reason=f"A risk alert was raised less than {int(self.cooldown.total_seconds() // 60)} min ago and the tier did not rise.")
                else:
                    a = self._new(ref, "RISK", key, now, result, severity="high" if tier == Tier.HIGH else "moderate", tier=tier.value)
                    for o in opens:                      # the lower open alert is linked, not silently resolved
                        o["superseded_by"] = a["alert_id"]
                        self._history(o, "SUPERSEDED", now, reason=f"a {tier.value} alert was raised")
                    decision.update(popup=True, alert_id=a["alert_id"], kind="RISK",
                                    decision="ESCALATED_TIER" if escalation else "NEW_RISK_ALERT",
                                    reason="Tier rose; shown even inside the cooldown." if escalation else "New risk alert.")
                decision["alert_tier"] = tier.value
                decision["hysteresis_applied"] = tier != result.risk.tier
            elif result.status != Status.OK:
                decision.update(decision="DATA_NOTICE", reason="No score: the phone shows a refresh / re-measure screen, not an alert.")
            if decision["alert_id"] and decision["popup"]:
                self._notify(self.alerts[decision["alert_id"]], now)
            if decision["alert_id"]:
                decision["alert"] = self.public(self.alerts[decision["alert_id"]])
            self._seen_events[event_id] = json.dumps(decision, default=str)
            self._save()
            return decision

    def _new(self, ref, kind, key, now, result, severity, tier=None) -> dict[str, Any]:
        aid = str(uuid.uuid5(NAMESPACE, f"{ref}|{key}|{now.isoformat()}"))
        a = {"alert_id": aid, "patient_reference": ref, "kind": kind, "key": key, "severity": severity, "tier": tier,
             "rule_id": result.emergency.rule_id if kind == "EMERGENCY" else None,
             "state": AlertState.NEW.value, "created_at": str(now), "last_seen": str(now),
             "model_version": result.model.version, "policy_version": result.risk.policy_version,
             "audit_id": result.audit.audit_id, "notifications": [], "user_actions": [], "history": []}
        self._history(a, AlertState.NEW.value, now)
        self.alerts[aid] = a
        return a

    def _notify(self, a, now):
        ok, err = None, None
        if self.notifier is None:
            ok, err = False, "no notification channel configured (demo)"
        else:
            try:
                ok = bool(self.notifier(self.public(a)))
            except Exception as e:  # a failed notification is recorded, never hidden
                ok, err = False, type(e).__name__
        a["notifications"].append({"at": str(now), "channel": "push", "success": ok, "error": err})

    # ---------------------------------------------------------------- lifecycle actions
    def mark_displayed(self, alert_id: str, now, actor="phone-app"):
        with self._lock:
            a = self.alerts[alert_id]
            if a["state"] == AlertState.NEW.value:
                a["state"] = AlertState.DISPLAYED.value
            self._history(a, "DISPLAYED", now, actor)
            self._save()
            return self.public(a)

    def acknowledge(self, alert_id: str, now, actor: str, role: str, note: str | None = None):
        with self._lock:
            self._check(role, "acknowledge_display")
            a = self.alerts[alert_id]
            if a["state"] in (AlertState.NEW.value, AlertState.DISPLAYED.value):
                a["state"] = AlertState.ACKNOWLEDGED.value
            a.setdefault("acknowledgements", []).append({"at": str(now), "actor": actor, "role": role, "note": note})
            self._history(a, "ACKNOWLEDGED", now, actor, role=role,
                          meaning="the alert was seen; this says nothing about whether the patient is better")
            self._save()
            return self.public(a)

    def clinician_review(self, alert_id: str, now, actor: str, role: str, review_status: str, note: str):
        with self._lock:
            self._check(role, "clinician_review")
            a = self.alerts[alert_id]
            a["review"] = {"status": review_status, "at": str(now), "by": actor, "note": note}
            self._history(a, "REVIEWED", now, actor, review_status=review_status)
            self._save()
            return self.public(a)

    def escalate(self, alert_id: str, now, actor: str, role: str, to: str):
        with self._lock:
            self._check(role, "escalate")
            a = self.alerts[alert_id]
            a["state"] = AlertState.ESCALATED.value
            self._history(a, "ESCALATED", now, actor, to=to)
            self._save()
            return self.public(a)

    def record_user_action(self, alert_id: str, now, actor: str, role: str, action: str):
        """e.g. 'opened_dialer_112'. Opening the dialler is recorded as exactly that, never as a dispatch."""
        with self._lock:
            self._check(role, "record_user_action")
            if "dispatch" in action.lower() and "not" not in action.lower():
                raise ValueError("The app cannot record an ambulance dispatch; it never contacts emergency services.")
            a = self.alerts[alert_id]
            a["user_actions"].append({"at": str(now), "actor": actor, "action": action})
            self._history(a, "USER_ACTION", now, actor, action=action)
            self._save()
            return self.public(a)

    def resolve(self, alert_id: str, now, actor: str, role: str, note: str, latest_result=None):
        """Only a clinician can resolve, with a note, and only when the latest evaluation is valid and no longer meets
        the alert condition. A missing or stale reading is never enough to resolve."""
        with self._lock:
            self._check(role, "resolve")
            a = self.alerts[alert_id]
            if not note:
                raise ValueError("A resolution note is required.")
            if latest_result is None or latest_result.status != Status.OK:
                raise ValueError("Cannot resolve: the latest evaluation is not a valid result (missing or stale data does not resolve an alert).")
            if a["kind"] == "EMERGENCY" and latest_result.emergency.state in (EmergencyState.TRIGGERED, EmergencyState.NOT_ASSESSABLE):
                raise ValueError("Cannot resolve: the emergency rule is still met or cannot be assessed.")
            a["state"] = AlertState.RESOLVED.value
            a["resolution"] = {"at": str(now), "by": actor, "note": note, "evidence_audit_id": latest_result.audit.audit_id}
            self._history(a, "RESOLVED", now, actor)
            self._save()
            return self.public(a)

    @staticmethod
    def public(a: dict) -> dict:
        return json.loads(json.dumps(a, default=str))
