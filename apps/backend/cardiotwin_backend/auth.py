# Token login for people (patient, caregiver, clinician, admin) and QR pairing for devices (watch, phone, ESP32,
# simulator). Only hashes of passwords and tokens are stored. Role permissions come from the risk engine's
# risk_policy.yaml (privacy.roles), so the backend and the engine enforce the same rules.
from __future__ import annotations

import base64
import hashlib
import hmac
import io
import secrets
import uuid
from dataclasses import dataclass
from datetime import timedelta
from urllib.parse import urlencode

from .db import Database
from .readings import iso, utcnow

PBKDF2_ROUNDS = 200_000
ROLES = ("patient", "caregiver", "clinician", "admin")
DEVICE_KINDS = {"watch": ("watch", "simulator"), "phone": ("phone",), "esp32": ("esp32",), "simulator": ("simulator",)}


class AuthError(PermissionError):
    """status 401: not signed in / bad credentials; 403: signed in but not allowed."""

    def __init__(self, message: str, status: int = 403):
        super().__init__(message)
        self.status = status


def hash_password(pw: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, PBKDF2_ROUNDS)
    return f"pbkdf2_sha256${PBKDF2_ROUNDS}${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"


def verify_password(pw: str, stored: str) -> bool:
    try:
        _, rounds, salt, dk = stored.split("$")
        got = hashlib.pbkdf2_hmac("sha256", pw.encode(), base64.b64decode(salt), int(rounds))
        return hmac.compare_digest(got, base64.b64decode(dk))
    except Exception:
        return False


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass
class Principal:
    kind: str                   # "user" or "device"
    id: str                     # user_id or device_id
    role: str                   # user role; devices act as "device"
    patient_id: str | None      # patient users and devices are bound to one patient
    name: str | None = None
    source: str | None = None   # devices: the reading source they may send


class Auth:
    def __init__(self, db: Database, roles: dict, token_hours: int = 24 * 7, pairing_minutes: int = 10,
                 clinician_sees_all: bool = True):
        self.db, self.roles = db, roles
        self.token_ttl = timedelta(hours=token_hours)
        self.pairing_ttl = timedelta(minutes=pairing_minutes)
        self.clinician_sees_all = clinician_sees_all

    # ---------------------------------------------------------------- users
    def create_user(self, username: str, password: str, role: str, display_name: str | None = None,
                    patient_id: str | None = None) -> str:
        if role not in ROLES:
            raise ValueError(f"role must be one of {ROLES}")
        if role == "patient" and not patient_id:
            raise ValueError("a patient user needs a patient_id")
        uid = str(uuid.uuid4())
        self.db.execute("INSERT INTO users VALUES (?,?,?,?,?,?,?)",
                        (uid, username, hash_password(password), role, display_name, patient_id, iso(utcnow())))
        return uid

    def link(self, user_id: str, patient_id: str) -> None:
        self.db.execute("INSERT OR IGNORE INTO care_team VALUES (?,?)", (user_id, patient_id))

    def login(self, username: str, password: str) -> dict:
        u = self.db.one("SELECT * FROM users WHERE username=?", (username,))
        if not u or not verify_password(password, u["password_hash"]):
            raise AuthError("Wrong username or password.", 401)
        token = self._issue("user", u["user_id"], self.token_ttl)
        return {"access_token": token, "token_type": "bearer", "role": u["role"], "user_id": u["user_id"],
                "patient_id": u["patient_id"], "display_name": u["display_name"],
                "expires_at": iso(utcnow() + self.token_ttl)}

    def _issue(self, kind: str, subject: str, ttl: timedelta | None) -> str:
        token = secrets.token_urlsafe(32)
        self.db.execute("INSERT INTO tokens VALUES (?,?,?,?,?,0)", (_token_hash(token), kind, subject, iso(utcnow()),
                                                                    iso(utcnow() + ttl) if ttl else None))
        return token

    def logout(self, token: str) -> None:
        self.db.execute("UPDATE tokens SET revoked=1 WHERE token_hash=?", (_token_hash(token),))

    def principal(self, token: str | None) -> Principal:
        if not token:
            raise AuthError("Missing token.", 401)
        t = self.db.one("SELECT * FROM tokens WHERE token_hash=?", (_token_hash(token),))
        if not t or t["revoked"] or (t["expires_at"] and t["expires_at"] < iso(utcnow())):
            raise AuthError("Invalid or expired token.", 401)
        if t["kind"] == "user":
            u = self.db.one("SELECT * FROM users WHERE user_id=?", (t["subject_id"],))
            if not u:
                raise AuthError("Unknown user.", 401)
            return Principal("user", u["user_id"], u["role"], u["patient_id"], u["display_name"] or u["username"])
        d = self.db.one("SELECT * FROM devices WHERE device_id=?", (t["subject_id"],))
        if not d or d["revoked"]:
            raise AuthError("Device not paired or revoked.", 401)
        return Principal("device", d["device_id"], "device", d["patient_id"], d["name"], d["source"])

    # ---------------------------------------------------------------- permissions
    def can(self, p: Principal, permission: str) -> bool:
        return p.kind == "user" and permission in self.roles.get(p.role, [])

    def require(self, p: Principal, permission: str) -> None:
        if not self.can(p, permission):
            raise AuthError(f"Role '{p.role}' may not '{permission}'.")

    def can_access(self, p: Principal, patient_id: str) -> bool:
        if p.kind == "device" or p.role == "patient":
            return p.patient_id == patient_id
        if p.role == "clinician" and self.clinician_sees_all:
            return True
        if p.role == "admin":
            return True
        return self.db.one("SELECT 1 FROM care_team WHERE user_id=? AND patient_id=?", (p.id, patient_id)) is not None

    def require_access(self, p: Principal, patient_id: str) -> None:
        if not self.can_access(p, patient_id):
            raise AuthError("No access to this patient.")

    def patients_for(self, p: Principal) -> list[str]:
        if p.kind == "device" or p.role == "patient":
            return [p.patient_id] if p.patient_id else []
        if p.role == "admin" or (p.role == "clinician" and self.clinician_sees_all):
            return [r["patient_id"] for r in self.db.all("SELECT patient_id FROM patients ORDER BY patient_id")]
        return [r["patient_id"] for r in self.db.all("SELECT patient_id FROM care_team WHERE user_id=?", (p.id,))]

    # ---------------------------------------------------------------- QR pairing
    def create_pairing(self, p: Principal, patient_id: str, server_url: str) -> dict:
        self.require_access(p, patient_id)
        if p.kind != "user" or p.role == "admin":
            raise AuthError("Only the patient, a caregiver or a clinician can pair a device.")
        code = "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(8))
        exp = utcnow() + self.pairing_ttl
        self.db.execute("INSERT INTO pairing_codes VALUES (?,?,?,?,?,NULL,NULL)", (code, patient_id, p.id, iso(utcnow()), iso(exp)))
        uri = "cardiotwin://pair?" + urlencode({"code": code, "server": server_url})
        return {"pairing_code": code, "patient_id": patient_id, "expires_at": iso(exp), "qr_payload": uri,
                "qr_png_base64": qr_png(uri), "instructions": "Scan this QR code in the CardioTwin watch or phone app, "
                                                              "or type the code. It works once and expires in "
                                                              f"{int(self.pairing_ttl.total_seconds() // 60)} minutes."}

    def pair(self, code: str, kind: str, name: str | None = None, source: str | None = None) -> dict:
        if kind not in DEVICE_KINDS:
            raise ValueError(f"kind must be one of {tuple(DEVICE_KINDS)}")
        source = source or DEVICE_KINDS[kind][0]
        if source not in DEVICE_KINDS[kind]:
            raise ValueError(f"a {kind} may send source {DEVICE_KINDS[kind]}")
        with self.db.lock:
            pc = self.db.one("SELECT * FROM pairing_codes WHERE code=?", (code.strip().upper(),))
            if not pc or pc["used_at"] or pc["expires_at"] < iso(utcnow()):
                raise AuthError("Pairing code is wrong, already used or expired.", 401)
            did = str(uuid.uuid4())
            self.db.execute("INSERT INTO devices VALUES (?,?,?,?,?,?,NULL,0)", (did, pc["patient_id"], kind, source, name, iso(utcnow())))
            self.db.execute("UPDATE pairing_codes SET used_at=?, device_id=? WHERE code=?", (iso(utcnow()), did, pc["code"]))
            if source == "simulator":
                self.db.execute("UPDATE patients SET clock_mode='simulated' WHERE patient_id=?", (pc["patient_id"],))
        token = self._issue("device", did, None)      # device tokens last until the device is unpaired
        return {"device_id": did, "device_token": token, "patient_id": pc["patient_id"], "kind": kind, "source": source}

    def unpair(self, p: Principal, device_id: str) -> None:
        d = self.db.one("SELECT * FROM devices WHERE device_id=?", (device_id,))
        if not d:
            raise KeyError(device_id)
        self.require_access(p, d["patient_id"])
        self.db.execute("UPDATE devices SET revoked=1 WHERE device_id=?", (device_id,))
        self.db.execute("UPDATE tokens SET revoked=1 WHERE kind='device' AND subject_id=?", (device_id,))


def qr_png(text: str) -> str | None:
    """Base64 PNG of a QR code, or None when the optional `qrcode` package is not installed (the payload still works)."""
    try:
        import qrcode
    except ImportError:
        return None
    img = qrcode.make(text)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()
