# CardioTwin backend service: ingest readings -> store -> safety layer -> rolling features against the personal baseline
# -> risk engine (the saved models, nothing retrained) -> alerts -> WebSocket push. Also hosts the what-if simulators and
# the in-process virtual watch sessions used by the demo control page.
from __future__ import annotations

import asyncio
import json
import logging
import sys
import threading
import time
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from . import engine_root, features
from .alerts import AlertStore
from .auth import Auth, AuthError, Principal
from .config import Settings
from .db import Database, dumps, loads
from .hub import Hub
from .chatbot import HealthChat
from .emergency_map import EmergencyMap
from .patient_app import PatientApp
from .readings import Reading, iso, to_engine, utcnow
from .safety import SafetyLayer
from .wellness import Wellness
from .heart_health import HeartHealth

SIM_LABEL = "SIMULATED"
log = logging.getLogger("cardiotwin.service")


class ServiceError(ValueError):
    pass


class CardioTwinService:
    def __init__(self, settings: Settings | None = None):
        self.settings = s = settings or Settings()
        s.data_dir = Path(s.data_dir)
        s.data_dir.mkdir(parents=True, exist_ok=True)
        self.root = engine_root.resolve(s.project_root, runtime_dir=s.data_dir / "engine_root")
        from risk_engine import RiskEngine, load_config
        from risk_engine.alert_service import AlertService
        from risk_engine.audit_service import AuditService
        self.cfg = load_config(overrides={"privacy.store_input_snapshots": bool(s.store_audit_snapshots)})
        self.hub = Hub()
        self._ref_to_pid: dict[str, str] = {}
        self.engine = RiskEngine(self.root.engine_root, self.cfg,
                                 alert_service=AlertService(self.cfg, store_path=s.data_dir / "engine_alerts.json", notifier=self._notify),
                                 audit_service=AuditService(self.cfg, directory=s.data_dir / "audit"))
        self.engine_lock = threading.RLock()
        self.db = Database(s.database)
        self.auth = Auth(self.db, self.cfg["privacy"]["roles"], clinician_sees_all=s.clinician_sees_all)
        self.alerts = AlertStore(self.db)
        self.app = PatientApp(self.db, self.auth, s.clinic_name, s.clinic_utc_offset, alerts=self.alerts)
        self.safety = SafetyLayer(self.alerts, self.cfg, {"fall_response_seconds": s.fall_response_seconds})
        # add-on modules: wellness plan, AI health assistant, nearby hospitals and ambulance (each owns its own tables)
        self.wellness = Wellness(self.db, self.app, self)
        self.heart = HeartHealth(self.db, self)
        self.chat = HealthChat(self.db, self, s)
        self.maps = EmergencyMap(self.db, self, s)
        self.latest: dict[str, Any] = {}                 # patient_id -> latest RiskResult
        self.dirty: set[str] = set()
        self._dirty_lock = threading.Lock()
        self.sim_sessions: dict[str, dict] = {}
        self._twin = None
        self._twin_error: str | None = None
        if s.seed_demo:
            self.seed_demo()
            self.seed_patient_app()

    # ================================================================ engine status
    def health(self) -> dict:
        m, s = self.engine.model, self.settings
        return {"status": "ok" if m is not None else "degraded", "model_loaded": m is not None,
                "model_error": self.engine.model_error, "model": m.info() if m else None,
                "policy_version": self.cfg["policy_version"], "provisional_policy": True,
                "engine_root_layout": self.root.layout, "project": str(self.root.project),
                "whatif_twin": "loaded" if self._twin else (self._twin_error or "not loaded yet"),
                "assistant": self.chat.status()["mode"], "maps_configured": bool(s.google_maps_browser_key),
                "places_configured": bool(s.google_maps_server_key), "ambulance_provider": self.maps.provider.name,
                "disclaimer": "Research prototype trained on MIMIC-IV demo ICU data. Not a medical device. "
                              "Thresholds are provisional and the emergency rules are a draft without clinical review."}

    # ================================================================ patients and EHR
    def create_patient(self, patient_id: str, display_name: str | None, profile: dict, clinical: list[dict],
                       is_demo: bool = False, replay_stay_id: int | None = None, source: str = "manual",
                       dose_limits: dict | None = None) -> dict:
        if self.db.one("SELECT 1 FROM patients WHERE patient_id=?", (patient_id,)):
            raise ServiceError(f"Patient {patient_id} already exists.")
        now = iso(utcnow())
        self.db.execute("INSERT INTO patients VALUES (?,?,?,?,?,?)", (patient_id, display_name, int(is_demo), replay_stay_id, "real", now))
        self.db.execute("INSERT INTO ehr VALUES (?,?,?,?,?,?)", (patient_id, dumps(profile), dumps(clinical),
                                                                 dumps(dose_limits) if dose_limits else None, source, now))
        return self.patient(patient_id)

    def patient(self, patient_id: str) -> dict:
        p = self.db.one("SELECT * FROM patients WHERE patient_id=?", (patient_id,))
        if not p:
            raise KeyError(patient_id)
        p["is_demo"] = bool(p["is_demo"])
        p["devices"] = self.db.all("SELECT device_id, kind, source, name, paired_at, last_seen, revoked FROM devices "
                                   "WHERE patient_id=? ORDER BY paired_at", (patient_id,))
        last = self.db.one("SELECT evaluated_at, status, tier, emergency_state FROM evaluations WHERE patient_id=? "
                           "ORDER BY id DESC LIMIT 1", (patient_id,))
        p["latest_evaluation"] = last
        open_ = self.alerts.list(patient_id, open_only=True)
        p["open_alerts"] = len(open_)
        p["critical_open"] = any(a["severity"] == "critical" for a in open_)   # classified critical until a clinician resolves it
        return p

    def ehr(self, patient_id: str) -> dict:
        e = self.db.one("SELECT * FROM ehr WHERE patient_id=?", (patient_id,))
        if not e:
            raise KeyError(patient_id)
        return {"patient_id": patient_id, "profile": loads(e["profile_json"], {}), "clinical": loads(e["clinical_json"], []),
                "dose_limits": loads(e["dose_limits_json"], None), "source": e["source"], "updated_at": e["updated_at"]}

    def update_ehr(self, patient_id: str, profile: dict | None = None, clinical: list[dict] | None = None,
                   dose_limits: dict | None = None) -> dict:
        cur = self.ehr(patient_id)
        prof = {**cur["profile"], **(profile or {})}
        clin = clinical if clinical is not None else cur["clinical"]
        dl = dose_limits if dose_limits is not None else cur["dose_limits"]
        self.db.execute("UPDATE ehr SET profile_json=?, clinical_json=?, dose_limits_json=?, updated_at=? WHERE patient_id=?",
                        (dumps(prof), dumps(clin), dumps(dl) if dl else None, iso(utcnow()), patient_id))
        self.mark_dirty(patient_id)
        return self.ehr(patient_id)

    def report_symptoms(self, patient_id: str, symptoms: dict[str, bool], note: str | None = None) -> dict:
        """Symptoms from the phone check-in. The risk engine's symptom rule runs on them at the next evaluation.
        Each check-in is also kept in the patient app's check-in history."""
        stamp = iso(self.now_for(patient_id).tz_localize("UTC").to_pydatetime())
        sym = {k: bool(v) for k, v in symptoms.items()}
        self.update_ehr(patient_id, profile={"reported_symptoms": {**sym, "timestamp": stamp}})
        out = self.evaluate(patient_id)
        out["checkin"] = self.app.record_checkin(patient_id, sym, note, stamp, out)
        return out

    # ================================================================ ingest
    def ingest(self, principal: Principal, readings: list[Reading]) -> dict:
        if not readings:
            return {"accepted": 0, "duplicates": 0, "rejected": []}
        pids = {r.patient_id for r in readings}
        for pid in pids:
            if principal.kind == "device":
                if pid != principal.patient_id:
                    raise AuthError("This device is paired to a different patient.")
            else:
                self.auth.require_access(principal, pid)
                if not (principal.role == "clinician" or principal.role == "patient"):
                    raise AuthError("Only devices, the patient or a clinician can send readings.")
            if not self.db.one("SELECT 1 FROM patients WHERE patient_id=?", (pid,)):
                raise KeyError(pid)
        rejected, rows = [], []
        now = utcnow()
        clocks = {pid: self.db.one("SELECT clock_mode FROM patients WHERE patient_id=?", (pid,))["clock_mode"] for pid in pids}
        for i, r in enumerate(readings):
            if principal.kind == "device" and r.source != principal.source:
                rejected.append({"index": i, "reason": f"this device may only send source '{principal.source}'"})
                continue
            clock = clocks[r.patient_id]
            if clock == "real" and r.timestamp > now + timedelta(minutes=self.settings.future_skew_minutes):
                rejected.append({"index": i, "reason": "timestamp is in the future"})
                continue
            if r.source == "simulator" and clock != "simulated":
                self.db.execute("UPDATE patients SET clock_mode='simulated' WHERE patient_id=?", (r.patient_id,))
                clocks[r.patient_id] = "simulated"
            num = r.value if isinstance(r.value, float) else None
            txt = r.value if isinstance(r.value, str) else None
            rows.append((r.patient_id, principal.id if principal.kind == "device" else None, iso(r.timestamp), r.type, num, txt,
                         r.source, r.quality, r.unit, dumps(r.meta) if r.meta else None, iso(now)))
        n = self.db.executemany("INSERT OR IGNORE INTO readings (patient_id, device_id, ts, type, value_num, value_text, source, "
                                "quality, unit, meta_json, received_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows) if rows else 0
        if principal.kind == "device":
            self.db.execute("UPDATE devices SET last_seen=? WHERE device_id=?", (iso(now), principal.id))
        raised = []
        for pid in pids:
            new = [self._row(r) for r in rows if r[0] == pid]
            if not new:
                continue
            since = min(x["ts"] for x in new)
            since = iso(pd.Timestamp(since).to_pydatetime() - timedelta(hours=6))
            recent = self.readings(pid, since=since, limit=20000)
            for a in self.safety.on_readings(pid, new, recent, self.ehr(pid)):
                raised.append(a)
                self._publish_alert(pid, a)
            self.mark_dirty(pid)
        return {"accepted": n, "duplicates": len(rows) - n, "rejected": rejected, "alerts": [a["alert_id"] for a in raised]}

    @staticmethod
    def _row(t: tuple) -> dict:
        return {"patient_id": t[0], "device_id": t[1], "ts": t[2], "type": t[3], "value_num": t[4], "value_text": t[5],
                "source": t[6], "quality": t[7], "unit": t[8], "meta": loads(t[9], None)}

    def readings(self, patient_id: str, since: str | None = None, until: str | None = None, types: list[str] | None = None,
                 limit: int = 5000) -> list[dict]:
        q, params = "SELECT * FROM readings WHERE patient_id=?", [patient_id]
        if since:
            q += " AND ts>=?"; params.append(since)
        if until:
            q += " AND ts<=?"; params.append(until)
        if types:
            q += f" AND type IN ({','.join('?' * len(types))})"; params += types
        q += " ORDER BY ts DESC LIMIT ?"; params.append(limit)
        rows = self.db.all(q, params)[::-1]
        for r in rows:
            r["meta"] = loads(r.pop("meta_json"), None)
        return rows

    # ================================================================ clock
    def now_for(self, patient_id: str) -> pd.Timestamp:
        """Real UTC now, or for a simulated patient the simulated clock (the newest reading time + 1 minute)."""
        p = self.db.one("SELECT clock_mode FROM patients WHERE patient_id=?", (patient_id,))
        if p and p["clock_mode"] == "simulated":
            last = self.db.one("SELECT MAX(ts) AS ts FROM readings WHERE patient_id=?", (patient_id,))
            if last and last["ts"]:
                return pd.Timestamp(last["ts"]).tz_convert("UTC").tz_localize(None) + pd.Timedelta(minutes=1)
        return pd.Timestamp(utcnow()).tz_convert("UTC").tz_localize(None)

    def is_simulated(self, patient_id: str) -> bool:
        p = self.db.one("SELECT clock_mode FROM patients WHERE patient_id=?", (patient_id,))
        return bool(p and p["clock_mode"] == "simulated")

    # ================================================================ evaluation
    def mark_dirty(self, patient_id: str) -> None:
        with self._dirty_lock:
            self.dirty.add(patient_id)

    def take_dirty(self) -> list[str]:
        with self._dirty_lock:
            d, self.dirty = list(self.dirty), set()
        return d

    def build_request(self, patient_id: str) -> tuple[dict, dict]:
        now = self.now_for(patient_id)
        since = iso((now - pd.Timedelta(days=self.settings.history_days)).tz_localize("UTC").to_pydatetime())
        until = iso((now + pd.Timedelta(minutes=1)).tz_localize("UTC").to_pydatetime())
        rows = self.readings(patient_id, since=since, until=until, limit=200000)
        e = self.ehr(patient_id)
        p = self.db.one("SELECT is_demo FROM patients WHERE patient_id=?", (patient_id,))
        profile = {**e["profile"], "patient_id": patient_id, "is_demo": bool(p["is_demo"]) or self.is_simulated(patient_id)}
        clinical = [{**c, "source": c.get("source", e["source"])} for c in e["clinical"]]
        feats = features.compute(rows, now, self.cfg)
        wear = [w for w in (to_engine(r) for r in rows) if w is not None]
        req = {"patient_profile": profile, "clinical_readings": clinical, "wearable_readings": wear,
               "baseline": features.engine_baseline(feats), "now": now.isoformat()}
        return req, feats

    def evaluate(self, patient_id: str) -> dict:
        from risk_engine import views
        req, feats = self.build_request(patient_id)
        sim = self.is_simulated(patient_id)
        with self.engine_lock:
            self._ref_to_pid[self.engine.pseudonym(patient_id)] = patient_id
            result = self.engine.evaluate(req["patient_profile"], req["clinical_readings"], req["wearable_readings"],
                                          baseline=req["baseline"], now=req["now"])
            self.latest[patient_id] = result
            # the audit trail is on disk; keep the engine's in-memory copy small in a long-running server
            for d in (self.engine.audit.records, self.engine.audit.snapshots):
                for k in list(d)[:-50]:
                    del d[k]
        v = self._safety_views(patient_id, views.all_views(result))
        res_json = result.model_dump(mode="json")
        status = {"type": "status", "simulated": sim, "label": SIM_LABEL if sim else None,
                  "evaluated_at": res_json["evaluated_at"], "status": result.status.value,
                  "display_state": v.pop("_display_state", None) or result.policy_breakdown.get("final_display_state"),
                  "tier": result.risk.tier.value if result.risk.tier else None,
                  "score": result.risk.calibrated_probability if result.risk.calibrated_probability is not None else result.risk.raw_output,
                  "emergency_state": result.emergency.state.value, "views": v, "features": feats,
                  "alert_decision": result.policy_breakdown.get("alert_decision")}
        self.db.execute("INSERT INTO evaluations (patient_id, evaluated_at, status, tier, score, emergency_state, audit_id, simulated, "
                        "result_json, views_json, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (patient_id, res_json["evaluated_at"], status["status"], status["tier"], status["score"],
                         status["emergency_state"], result.audit.audit_id, int(sim), dumps(res_json), dumps(v), iso(utcnow())))
        self.db.execute("DELETE FROM evaluations WHERE patient_id=? AND id NOT IN (SELECT id FROM evaluations WHERE patient_id=? "
                        "ORDER BY id DESC LIMIT ?)", (patient_id, patient_id, self.settings.keep_evaluations))
        self.hub.publish(patient_id, status)
        try:                                   # heart-health inbox notices; never allowed to affect the evaluation itself
            self.heart.scan(patient_id, status)
        except Exception:                      # noqa: BLE001
            log.exception("heart-health scan failed for a patient")
        al = result.alert or {}
        if al.get("alert_id") and al.get("alert"):
            a = self.alerts.mirror_engine(patient_id, al["alert"], sim)
            if al.get("popup"):
                if a["kind"] == "EMERGENCY":
                    a = self.alerts.update_payload(a["alert_id"], message=result.emergency.reason,
                                                   payload={**a["payload"], "emergency": result.emergency.modal})
                self._publish_alert(patient_id, a)
        return {**status, "result": res_json}

    def _safety_views(self, patient_id: str, v: dict) -> dict:
        """An open safety-layer emergency (an unanswered fall) or fall check takes over the watch face and phone screen,
        exactly as an engine emergency does, so an app that only renders the latest status still shows it."""
        open_ = self.alerts.list(patient_id, open_only=True)
        em = [a for a in open_ if a["origin"] == "safety" and a["severity"] == "critical" and a["payload"].get("emergency")]
        check = [a for a in open_ if a["kind"] == "FALL" and a["state"] == "AWAITING_RESPONSE"]
        if em and v["phone_patient"]["screen"] != "EMERGENCY":
            modal = em[0]["payload"]["emergency"]
            call = next((a for a in modal.get("actions", []) if a.get("id") == "call_emergency"), None)
            v["phone_patient"] = {**v["phone_patient"], "screen": "EMERGENCY", "message": modal["headline"], "emergency_modal": modal,
                                  "emergency_alert_id": em[0]["alert_id"], "emergency_level": "critical", "call_action": call,
                                  "care_action": None}
            v["watch"] = {**modal["watch"], "evaluated_at": v["watch"].get("evaluated_at")}
            v["phone_doctor"]["safety_emergency"] = {"alert_id": em[0]["alert_id"], "rule_id": em[0]["rule_id"], "reason": em[0]["message"]}
            v["_display_state"] = "EMERGENCY_ALERT"
        elif check and v["watch"].get("type") != "EMERGENCY_WATCH":
            v["watch"] = {**check[0]["payload"]["watch"], "alert_id": check[0]["alert_id"], "evaluated_at": v["watch"].get("evaluated_at")}
        return v

    def latest_status(self, patient_id: str) -> dict | None:
        r = self.db.one("SELECT * FROM evaluations WHERE patient_id=? ORDER BY id DESC LIMIT 1", (patient_id,))
        if not r:
            return None
        return {"evaluated_at": r["evaluated_at"], "status": r["status"], "tier": r["tier"], "score": r["score"],
                "emergency_state": r["emergency_state"], "simulated": bool(r["simulated"]), "label": SIM_LABEL if r["simulated"] else None,
                "audit_id": r["audit_id"], "views": loads(r["views_json"]), "result": loads(r["result_json"])}

    def history(self, patient_id: str, limit: int = 200) -> list[dict]:
        return self.db.all("SELECT evaluated_at, status, tier, score, emergency_state, simulated FROM evaluations "
                           "WHERE patient_id=? ORDER BY id DESC LIMIT ?", (patient_id, limit))[::-1]

    def _notify(self, alert: dict) -> bool:
        """Engine notifier: 'sent' means pushed over WebSocket to at least one connected app for this patient."""
        pid = self._ref_to_pid.get(alert.get("patient_reference"))
        return bool(pid and self.hub.subscribers(pid) > 0)

    def _publish_alert(self, patient_id: str, a: dict) -> None:
        kind = "emergency" if a["severity"] == "critical" and a["state"] != "RESOLVED" else "alert"
        msg = {"type": kind, "alert": a, "simulated": a.get("simulated", False)}
        if kind == "emergency":
            msg["modal"] = a["payload"].get("emergency")
        elif a["kind"] == "FALL" and a["state"] == "AWAITING_RESPONSE":
            msg["watch"] = a["payload"].get("watch")
        self.hub.publish(patient_id, msg)

    async def run_loop(self) -> None:
        """Background loop: escalate unanswered fall checks, then evaluate patients with new data."""
        loop = asyncio.get_running_loop()
        while True:
            try:
                for a in self.safety.overdue():
                    self._publish_alert(a["patient_id"], a)
                for pid in self.take_dirty():
                    await loop.run_in_executor(None, self.evaluate, pid)
            except Exception as e:  # keep the loop alive; the failure is visible in /api/health logs
                print(f"[cardiotwin] evaluation loop error: {type(e).__name__}: {e}", file=sys.stderr)
            await asyncio.sleep(self.settings.eval_interval_s)

    # ================================================================ alert actions
    def alert_for(self, principal: Principal, alert_id: str) -> dict:
        a = self.alerts.get(alert_id)
        if not a:
            raise KeyError(alert_id)
        self.auth.require_access(principal, a["patient_id"])
        return a

    def _engine_role(self, p: Principal) -> str:
        return p.role if p.kind == "user" else "patient"     # the paired watch acts for the wearer

    def alert_action(self, principal: Principal, alert_id: str, action: str, note: str | None = None,
                     review_status: str | None = None, to: str | None = None, user_action: str | None = None) -> dict:
        a = self.alert_for(principal, alert_id)
        pid, actor, role = a["patient_id"], principal.name or principal.id, self._engine_role(principal)
        now = self.now_for(pid)
        if a["origin"] == "engine":
            svc = self.engine.alerts
            with self.engine_lock:
                if action == "display":
                    out = svc.mark_displayed(alert_id, now, actor)
                elif action == "acknowledge":
                    out = svc.acknowledge(alert_id, now, actor, role, note)
                elif action == "review":
                    out = svc.clinician_review(alert_id, now, actor, role, review_status or "reviewed", note or "")
                elif action == "escalate":
                    out = svc.escalate(alert_id, now, actor, role, to or "care team")
                elif action == "resolve":
                    out = svc.resolve(alert_id, now, actor, role, note or "", latest_result=self.latest.get(pid))
                elif action == "user_action":
                    out = svc.record_user_action(alert_id, now, actor, role, user_action or "")
                else:
                    raise ServiceError(f"Unknown action {action}")
            res = self.alerts.mirror_engine(pid, out, a["simulated"])
        else:
            res = self._safety_action(principal, a, action, note, review_status, to, user_action, actor, role)
        self._publish_alert(pid, res)
        return res

    def _safety_action(self, p, a, action, note, review_status, to, user_action, actor, role) -> dict:
        roles = self.cfg["privacy"]["roles"]

        def need(perm):
            if perm not in roles.get(role, []):
                raise AuthError(f"Role '{role}' may not '{perm}'.")
        aid = a["alert_id"]
        if action == "display":
            return self.alerts.transition(aid, "DISPLAYED" if a["state"] == "NEW" else None, "DISPLAYED", actor)
        if action == "acknowledge":
            need("acknowledge_display")
            st = "ACKNOWLEDGED" if a["state"] in ("NEW", "DISPLAYED") else None
            return self.alerts.transition(aid, st, "ACKNOWLEDGED", actor, role=role, note=note,
                                          meaning="the alert was seen; this says nothing about whether the patient is better")
        if action == "review":
            need("clinician_review")
            return self.alerts.transition(aid, None, "REVIEWED", actor, review_status=review_status or "reviewed", note=note)
        if action == "escalate":
            need("escalate")
            return self.alerts.transition(aid, "ESCALATED", "ESCALATED", actor, to=to or "care team")
        if action == "resolve":
            need("resolve")
            if not note:
                raise ServiceError("A resolution note is required.")
            return self.alerts.transition(aid, "RESOLVED", "RESOLVED", actor, note=note)
        if action == "user_action":
            need("record_user_action")
            ua = (user_action or "").lower()
            if "dispatch" in ua and "not" not in ua:
                raise ServiceError("The app cannot record an ambulance dispatch; it never contacts emergency services.")
            return self.alerts.transition(aid, None, "USER_ACTION", actor, action=user_action)
        raise ServiceError(f"Unknown action {action}")

    def fall_response(self, principal: Principal, alert_id: str, answer: str) -> dict:
        a = self.alert_for(principal, alert_id)
        if principal.kind == "user" and principal.role not in ("patient", "caregiver", "clinician"):
            raise AuthError("Only the wearer or their carers can answer a fall check.")
        res = self.safety.fall_response(alert_id, answer, principal.name or principal.id)
        self._publish_alert(a["patient_id"], res)
        return res

    # ================================================================ what-if
    def whatif_live(self, principal: Principal, patient_id: str, changes: dict[str, float], hours: int | None = None) -> dict:
        """The risk engine's what-if on this patient's current stored readings (simulation mode: no alerts, no record)."""
        from risk_engine import whatif
        from risk_engine.alert_service import PermissionDenied
        req, _ = self.build_request(patient_id)
        req.pop("baseline", None)
        try:
            with self.engine_lock:
                return whatif.simulate(self.engine, req, changes, role=self._engine_role(principal), hours=hours)
        except PermissionDenied as e:
            raise AuthError(str(e))

    def twin(self):
        if self._twin is None and self._twin_error is None:
            try:
                wd = self.root.whatif_dir
                if wd is None:
                    raise FileNotFoundError("what_if_simulator/ not found in the project")
                if str(wd) not in sys.path:
                    sys.path.insert(0, str(wd))
                from whatif_twin import load_simulator
                self._twin = load_simulator(self.root.project)
            except Exception as e:
                self._twin_error = f"what-if twin unavailable ({type(e).__name__}: {e})"
        if self._twin is None:
            raise ServiceError(self._twin_error)
        return self._twin

    def whatif_cases(self) -> list[dict]:
        t = self.twin()
        return [t.patients.get(c).summary() for c in t.patients.ids()]

    def whatif_case(self, principal: Principal, request: dict) -> dict:
        """The notebook 08 digital-twin simulator (both models, SHAP deltas, uncertainty) on a saved test case."""
        from whatif_twin import export
        try:
            return json.loads(json.dumps(export.simulate_request(self.twin(), request, role=self._engine_role(principal)), default=str))
        except export.PermissionDenied as e:
            raise AuthError(str(e))

    # ================================================================ virtual watch sessions (in-process)
    def start_simulation(self, principal: Principal, patient_id: str, stay_id: int | None = None, speed: float = 360.0,
                         backfill_hours: int = 12, seed: int = 0) -> dict:
        from simulator.watch_sim import WatchSimulator, load_replay
        self.auth.require_access(principal, patient_id)
        if principal.kind != "user" or principal.role not in ("clinician", "patient", "caregiver"):
            raise AuthError("Only a signed-in user can start the virtual watch.")
        for s in self.sim_sessions.values():
            if s["patient_id"] == patient_id and not s["task"].done() and not s.get("stopped"):
                raise ServiceError("A virtual watch is already running for this patient; stop it first.")
        p = self.db.one("SELECT replay_stay_id FROM patients WHERE patient_id=?", (patient_id,))
        stay_id = stay_id or p["replay_stay_id"]
        if not stay_id:
            raise ServiceError("Give stay_id: this patient has no replay data of its own.")
        sim = WatchSimulator(load_replay(stay_id, self.settings.replay_dir), patient_id, backfill_hours=backfill_hours, seed=seed)
        did = str(uuid.uuid4())
        self.db.execute("INSERT INTO devices VALUES (?,?,?,?,?,?,NULL,0)",
                        (did, patient_id, "watch", "simulator", "Virtual watch (SIMULATED, in-process)", iso(utcnow())))
        self.db.execute("UPDATE patients SET clock_mode='simulated' WHERE patient_id=?", (patient_id,))
        dev = Principal("device", did, "device", patient_id, "Virtual watch (SIMULATED)", "simulator")
        sid = str(uuid.uuid4())[:8]
        session = {"session_id": sid, "patient_id": patient_id, "device_id": did, "sim": sim, "speed": float(speed),
                   "principal": dev, "started_at": iso(utcnow()), "sent": 0}
        session["sent"] += self.ingest(dev, [Reading(**r) for r in sim.backfill()])["accepted"]
        session["task"] = asyncio.get_running_loop().create_task(self._sim_task(session))
        self.sim_sessions[sid] = session
        self.hub.publish(patient_id, {"type": "simulator", "event": "started", "label": SIM_LABEL, **sim.status()})
        return self.sim_status(sid)

    async def _sim_task(self, s: dict) -> None:
        sim, loop = s["sim"], asyncio.get_running_loop()
        while not sim.finished:
            batch = sim.next()
            if batch:
                res = await loop.run_in_executor(None, self.ingest, s["principal"], [Reading(**r) for r in batch])
                s["sent"] += res["accepted"]
            await asyncio.sleep(sim.step.total_seconds() / s["speed"])
        self.hub.publish(s["patient_id"], {"type": "simulator", "event": "finished", "label": SIM_LABEL, **sim.status()})

    def sim_status(self, sid: str) -> dict:
        s = self.sim_sessions.get(sid)
        if not s:
            raise KeyError(sid)
        return {"session_id": sid, "patient_id": s["patient_id"], "device_id": s["device_id"], "speed": s["speed"],
                "running": bool("task" in s and not s["task"].done() and not s.get("stopped")), "readings_sent": s["sent"],
                "events": s["sim"].events[-20:], **s["sim"].status()}

    def sim_trigger(self, principal: Principal, sid: str, event: str, minutes: int = 60) -> dict:
        s = self.sim_sessions.get(sid)
        if not s:
            raise KeyError(sid)
        self.auth.require_access(principal, s["patient_id"])
        sim = s["sim"]
        if event == "af":
            ev = sim.trigger_af(minutes)
        elif event == "af_stop":
            ev = sim.stop_af()
        elif event == "fall":
            res = self.ingest(s["principal"], [Reading(**r) for r in sim.trigger_fall()])
            ev = {"event": "fall", "label": SIM_LABEL, "alerts": res["alerts"]}
        else:
            raise ServiceError("event must be af, af_stop or fall")
        self.hub.publish(s["patient_id"], {"type": "simulator", **ev})
        return {**self.sim_status(sid), "last_event": ev}

    def stop_simulation(self, principal: Principal, sid: str) -> dict:
        s = self.sim_sessions.get(sid)
        if not s:
            raise KeyError(sid)
        self.auth.require_access(principal, s["patient_id"])
        s["stopped"] = True
        s["task"].cancel()
        s["sim"].finished = True
        self.db.execute("UPDATE devices SET revoked=1 WHERE device_id=?", (s["device_id"],))
        return self.sim_status(sid)

    # ================================================================ demo data
    def seed_patient_app(self) -> None:
        """Patient app demo data (names, medicines, visits). Runs on every start but seeds each demo patient only once,
        so an existing database from before the patient app extras gets them too."""
        pts = self.db.all("SELECT patient_id, is_demo, replay_stay_id FROM patients ORDER BY patient_id")
        self.app.seed_demo(pts, {p["patient_id"]: self.ehr(p["patient_id"])["profile"] for p in pts if p["is_demo"]})

    def seed_demo(self) -> None:
        """Demo accounts and one patient per held-out test stay, with the stay's profile and labs as the EHR. Labelled demo."""
        if self.db.one("SELECT 1 FROM users LIMIT 1"):
            return
        from simulator.watch_sim import available_stays, load_replay
        pw = self.settings.demo_password
        doc = self.auth.create_user("dr.demo", pw, "clinician", "Dr Demo (clinician)")
        self.auth.create_user("admin.demo", pw, "admin", "Admin (demo)")
        carer = self.auth.create_user("carer.demo", pw, "caregiver", "Carer (demo)")
        try:
            stays = available_stays(self.settings.replay_dir)
        except FileNotFoundError:
            stays = []
        for i, st in enumerate(stays):
            rp = load_replay(st["stay_id"], self.settings.replay_dir)
            pid = f"stay-{st['stay_id']}"
            clinical = [{**c, "timestamp": None, "source": "MIMIC-IV demo record (undated in source)"} for c in rp["clinical"]]
            self.create_patient(pid, f"Demo patient {st['stay_id']} (MIMIC-IV demo test stay)", {**rp["profile"], "is_demo": True},
                                clinical, is_demo=True, replay_stay_id=st["stay_id"], source="MIMIC-IV demo (replay)")
            self.auth.create_user(f"p{st['stay_id']}", pw, "patient", f"Demo patient {st['stay_id']}", patient_id=pid)
            if i == 0 or st["stay_id"] == 37093652:
                self.auth.link(carer, pid)
            self.auth.link(doc, pid)
