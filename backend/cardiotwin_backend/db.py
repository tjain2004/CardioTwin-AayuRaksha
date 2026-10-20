# SQLite storage: users and tokens, patients and their EHR, paired devices, readings, alerts and evaluations, plus the
# patient app's appointments, medicines, check-ins, contacts, notifications and settings.
# One connection guarded by a lock (the service writes from the event loop and from the evaluation thread).
# Timestamps are stored as ISO-8601 UTC strings ("...Z"), which sort correctly as text.
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  user_id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL, role TEXT NOT NULL,
  display_name TEXT, patient_id TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS care_team (user_id TEXT NOT NULL, patient_id TEXT NOT NULL, PRIMARY KEY (user_id, patient_id));
CREATE TABLE IF NOT EXISTS tokens (
  token_hash TEXT PRIMARY KEY, kind TEXT NOT NULL, subject_id TEXT NOT NULL, created_at TEXT NOT NULL,
  expires_at TEXT, revoked INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS patients (
  patient_id TEXT PRIMARY KEY, display_name TEXT, is_demo INTEGER NOT NULL DEFAULT 0, replay_stay_id INTEGER,
  clock_mode TEXT NOT NULL DEFAULT 'real', created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ehr (
  patient_id TEXT PRIMARY KEY REFERENCES patients(patient_id), profile_json TEXT NOT NULL, clinical_json TEXT NOT NULL,
  dose_limits_json TEXT, source TEXT, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS pairing_codes (
  code TEXT PRIMARY KEY, patient_id TEXT NOT NULL, created_by TEXT NOT NULL, created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL, used_at TEXT, device_id TEXT);
CREATE TABLE IF NOT EXISTS devices (
  device_id TEXT PRIMARY KEY, patient_id TEXT NOT NULL, kind TEXT NOT NULL, source TEXT NOT NULL, name TEXT,
  paired_at TEXT NOT NULL, last_seen TEXT, revoked INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS readings (
  id INTEGER PRIMARY KEY AUTOINCREMENT, patient_id TEXT NOT NULL, device_id TEXT, ts TEXT NOT NULL, type TEXT NOT NULL,
  value_num REAL, value_text TEXT, source TEXT NOT NULL, quality REAL, unit TEXT, meta_json TEXT, received_at TEXT NOT NULL,
  UNIQUE (patient_id, ts, type, source));
CREATE INDEX IF NOT EXISTS ix_readings_pt ON readings (patient_id, ts);
CREATE TABLE IF NOT EXISTS alerts (
  alert_id TEXT PRIMARY KEY, patient_id TEXT NOT NULL, origin TEXT NOT NULL, kind TEXT NOT NULL, severity TEXT NOT NULL,
  state TEXT NOT NULL, rule_id TEXT, title TEXT, message TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  deadline_at TEXT, simulated INTEGER NOT NULL DEFAULT 0, payload_json TEXT, history_json TEXT NOT NULL DEFAULT '[]');
CREATE INDEX IF NOT EXISTS ix_alerts_pt ON alerts (patient_id, state);
CREATE TABLE IF NOT EXISTS evaluations (
  id INTEGER PRIMARY KEY AUTOINCREMENT, patient_id TEXT NOT NULL, evaluated_at TEXT NOT NULL, status TEXT NOT NULL,
  tier TEXT, score REAL, emergency_state TEXT, audit_id TEXT, simulated INTEGER NOT NULL DEFAULT 0,
  result_json TEXT NOT NULL, views_json TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_eval_pt ON evaluations (patient_id, id);
-- patient app extras (patient_app.py): demo appointments, medicine list, check-in history, contacts, notifications, settings
CREATE TABLE IF NOT EXISTS appointments (
  appointment_id TEXT PRIMARY KEY, patient_id TEXT NOT NULL, clinician_id TEXT NOT NULL, start_at TEXT NOT NULL,
  minutes INTEGER NOT NULL, mode TEXT NOT NULL, reason TEXT NOT NULL, status TEXT NOT NULL, booked_by TEXT NOT NULL,
  note TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, history_json TEXT NOT NULL DEFAULT '[]');
CREATE UNIQUE INDEX IF NOT EXISTS ux_appt_slot ON appointments (clinician_id, start_at) WHERE status='BOOKED';
CREATE INDEX IF NOT EXISTS ix_appt_pt ON appointments (patient_id, start_at);
CREATE TABLE IF NOT EXISTS medications (
  med_id TEXT PRIMARY KEY, patient_id TEXT NOT NULL, name TEXT NOT NULL, strength TEXT, instructions TEXT,
  times_json TEXT NOT NULL DEFAULT '[]', reminders INTEGER NOT NULL DEFAULT 1, active INTEGER NOT NULL DEFAULT 1,
  source TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS med_intake (
  med_id TEXT NOT NULL, patient_id TEXT NOT NULL, day TEXT NOT NULL, slot TEXT NOT NULL, taken_at TEXT NOT NULL,
  PRIMARY KEY (med_id, day, slot));
CREATE TABLE IF NOT EXISTS symptom_checkins (
  id INTEGER PRIMARY KEY AUTOINCREMENT, patient_id TEXT NOT NULL, created_at TEXT NOT NULL, engine_ts TEXT,
  symptoms_json TEXT NOT NULL, note TEXT, display_state TEXT, emergency_state TEXT, simulated INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS ix_checkin_pt ON symptom_checkins (patient_id, id);
CREATE TABLE IF NOT EXISTS emergency_contacts (
  contact_id TEXT PRIMARY KEY, patient_id TEXT NOT NULL, name TEXT NOT NULL, relation TEXT, phone TEXT NOT NULL,
  is_primary INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS notifications (
  notification_id TEXT PRIMARY KEY, patient_id TEXT NOT NULL, kind TEXT NOT NULL, title TEXT NOT NULL, body TEXT,
  ref_id TEXT, created_at TEXT NOT NULL, read_at TEXT);
CREATE INDEX IF NOT EXISTS ix_notif_pt ON notifications (patient_id, created_at);
CREATE TABLE IF NOT EXISTS app_settings (
  patient_id TEXT PRIMARY KEY, preferred_name TEXT, name_is_demo INTEGER NOT NULL DEFAULT 0, language TEXT NOT NULL DEFAULT 'en',
  med_reminders INTEGER NOT NULL DEFAULT 1, appointment_updates INTEGER NOT NULL DEFAULT 1, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS demo_seeds (key TEXT PRIMARY KEY, created_at TEXT NOT NULL);
"""


class Database:
    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock:
            if self.path != ":memory:":
                self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA foreign_keys=ON")
            self.conn.executescript(SCHEMA)

    def ensure_schema(self, sql: str) -> None:
        """Extra tables owned by an add-on module (chatbot, emergency map, wellness). CREATE ... IF NOT EXISTS only, so it
        also upgrades a database created before the module existed."""
        with self.lock:
            self.conn.executescript(sql)

    # ---------------------------------------------------------------- primitives
    def execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self.lock:
            return self.conn.execute(sql, tuple(params))

    def executemany(self, sql: str, rows: list[tuple]) -> int:
        with self.lock:
            self.conn.execute("BEGIN")
            try:
                before = self.conn.total_changes
                self.conn.executemany(sql, rows)
                self.conn.execute("COMMIT")
                return self.conn.total_changes - before
            except Exception:
                self.conn.execute("ROLLBACK")
                raise

    def one(self, sql: str, params: Iterable[Any] = ()) -> dict | None:
        with self.lock:
            r = self.conn.execute(sql, tuple(params)).fetchone()
        return dict(r) if r else None

    def all(self, sql: str, params: Iterable[Any] = ()) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, tuple(params)).fetchall()]

    def close(self):
        with self.lock:
            self.conn.close()


def dumps(x: Any) -> str:
    return json.dumps(x, default=str, separators=(",", ":"))


def loads(s: str | None, default: Any = None) -> Any:
    return default if s is None else json.loads(s)
