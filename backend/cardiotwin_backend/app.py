# FastAPI app: REST for the phone app, watch app, ESP32 and dashboard, plus two WebSockets:
#   /ws/device?token=...         paired device (watch, simulator, ESP32): sends readings, receives its patient's updates
#   /ws/live/{patient_id}?token= phone or dashboard: receives status, alerts and emergencies for one patient
# Every message about simulated data carries simulated=true and label "SIMULATED".
from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional, Union

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field, ValidationError

from .auth import AuthError, Principal
from .config import Settings
from .readings import Reading
from .service import CardioTwinService, ServiceError

STATIC = Path(__file__).resolve().parent / "static"


# ---------------------------------------------------------------------- request bodies
class LoginBody(BaseModel):
    username: str
    password: str


class PairBody(BaseModel):
    code: str
    kind: str = "watch"
    source: Optional[str] = None
    name: Optional[str] = None


class ReadingsBody(BaseModel):
    readings: list[Reading]


class PatientBody(BaseModel):
    patient_id: str = Field(min_length=1, max_length=64)
    display_name: Optional[str] = None
    profile: dict[str, Any]
    clinical: list[dict[str, Any]] = []
    dose_limits: Optional[dict[str, float]] = None


class EhrBody(BaseModel):
    profile: Optional[dict[str, Any]] = None
    clinical: Optional[list[dict[str, Any]]] = None
    dose_limits: Optional[dict[str, float]] = None


class SymptomsBody(BaseModel):
    symptoms: dict[str, bool]
    note: Optional[str] = Field(default=None, max_length=500)


class AppointmentBody(BaseModel):
    start_at: str                       # YYYY-MM-DDTHH:MM, clinic wall-clock time
    reason: str = Field(min_length=1, max_length=500)
    mode: str = "in_person"             # in_person | video | phone
    clinician_id: Optional[str] = None


class RescheduleBody(BaseModel):
    start_at: str


class CancelBody(BaseModel):
    reason: Optional[str] = None


class CompleteBody(BaseModel):
    note: Optional[str] = None
    no_show: bool = False


class MedicationBody(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    strength: Optional[str] = None
    instructions: Optional[str] = None
    times: list[str] = []
    reminders: bool = True


class MedicationPatch(BaseModel):
    name: Optional[str] = None
    strength: Optional[str] = None
    instructions: Optional[str] = None
    times: Optional[list[str]] = None
    reminders: Optional[bool] = None


class TakenBody(BaseModel):
    day: str                            # YYYY-MM-DD on the patient's phone
    slot: str                           # one of the medicine's times, or "extra"
    taken: bool = True


class ContactBody(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    phone: str
    relation: Optional[str] = None
    is_primary: bool = False


class ContactPatch(BaseModel):
    name: Optional[str] = None
    phone: Optional[str] = None
    relation: Optional[str] = None
    is_primary: Optional[bool] = None


class ReadBody(BaseModel):
    ids: Optional[list[str]] = None     # none = mark all read


class AppSettingsBody(BaseModel):
    preferred_name: Optional[str] = None
    language: Optional[str] = None
    med_reminders: Optional[bool] = None
    appointment_updates: Optional[bool] = None


class ChatBody(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    conversation_id: Optional[str] = None
    language: Optional[str] = Field(default=None, pattern="^(en|hi)$")


class ChatConsentBody(BaseModel):
    share_health_data: bool


class LocationBody(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    radius_m: int = Field(default=5000, ge=500, le=50000)
    alert_id: Optional[str] = None


class RouteBody(BaseModel):
    origin_lat: float = Field(ge=-90, le=90)
    origin_lng: float = Field(ge=-180, le=180)
    dest_lat: float = Field(ge=-90, le=90)
    dest_lng: float = Field(ge=-180, le=180)


class AmbulanceBody(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    alert_id: Optional[str] = None
    confirm_demo: bool = False


class WellnessPrefsBody(BaseModel):
    diet: Optional[str] = None
    region: Optional[str] = None
    activity_level: Optional[str] = None
    limitations: Optional[list[str]] = None
    avoid_foods: Optional[list[str]] = Field(default=None, max_length=10)
    goals: Optional[dict[str, float]] = None
    reminders: Optional[dict[str, Union[bool, str]]] = None


class WellnessLogBody(BaseModel):
    day: str                            # YYYY-MM-DD on the patient's phone
    metric: str
    value: float
    mode: str = "set"                   # set | add


class CaregiverConsentBody(BaseModel):
    emergency: Optional[bool] = None
    health_alerts: Optional[bool] = None
    weekly_reports: Optional[bool] = None


class AlertActionBody(BaseModel):
    note: Optional[str] = None
    review_status: Optional[str] = None
    to: Optional[str] = None
    user_action: Optional[str] = None


class FallResponseBody(BaseModel):
    answer: str            # im_ok | need_help


class WhatIfBody(BaseModel):
    changes: dict[str, float]
    hours: Optional[int] = None


class TwinBody(BaseModel):
    case_id: str
    scenario: dict[str, Any]


class SimStartBody(BaseModel):
    patient_id: str
    stay_id: Optional[int] = None
    speed: float = Field(default=360.0, gt=0, le=36000)
    backfill_hours: int = Field(default=12, ge=0, le=72)
    seed: int = 0


class SimTriggerBody(BaseModel):
    event: str             # af | af_stop | fall
    minutes: int = Field(default=60, ge=5, le=720)


# ---------------------------------------------------------------------- role-shaped views
def shape_for(svc: CardioTwinService, p: Principal):
    technical = svc.auth.can(p, "view_technical")

    def shape(msg: dict) -> dict | None:
        if msg.get("type") != "status" or technical:
            return msg
        v = msg.get("views", {})
        keep = {k: msg.get(k) for k in ("type", "patient_id", "simulated", "label", "evaluated_at", "status", "display_state",
                                       "tier", "emergency_state")}
        if p.kind == "device":
            return {**keep, "views": {"watch": v.get("watch")}}
        return {**keep, "views": {"phone_patient": v.get("phone_patient"), "watch": v.get("watch")}, "features": msg.get("features")}
    return shape


def create_app(settings: Settings | None = None, service: CardioTwinService | None = None) -> FastAPI:
    svc = service or CardioTwinService(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        svc.hub.bind(asyncio.get_running_loop())
        task = asyncio.create_task(svc.run_loop()) if svc.settings.run_loop else None
        yield
        if task:
            task.cancel()
        for s in svc.sim_sessions.values():
            if "task" in s:
                s["task"].cancel()

    app = FastAPI(title="CardioTwin backend", version="1.0.0", lifespan=lifespan,
                  description="Research prototype. Not a medical device. Calls the saved CardioTwin risk engine; nothing is retrained.")
    app.state.service = svc
    app.add_middleware(CORSMiddleware, allow_origins=list(svc.settings.cors_origins), allow_methods=["*"], allow_headers=["*"])

    @app.exception_handler(AuthError)
    async def _auth(_, e: AuthError):
        return JSONResponse({"detail": str(e)}, status_code=getattr(e, "status", 403))

    @app.exception_handler(KeyError)
    async def _nf(_, e: KeyError):
        return JSONResponse({"detail": f"Not found: {e.args[0] if e.args else ''}"}, status_code=404)

    @app.exception_handler(ServiceError)
    async def _bad(_, e: ServiceError):
        return JSONResponse({"detail": str(e)}, status_code=400)

    @app.exception_handler(ValueError)
    async def _val(_, e: ValueError):
        return JSONResponse({"detail": str(e)}, status_code=400)

    def principal(authorization: Optional[str] = Header(default=None)) -> Principal:
        tok = authorization.split(" ", 1)[1] if authorization and authorization.lower().startswith("bearer ") else None
        return svc.auth.principal(tok)

    def access(pid: str, p: Principal) -> None:
        svc.auth.require_access(p, pid)

    # ------------------------------------------------------------------ system and auth
    @app.get("/api/health")
    def health():
        return svc.health()

    @app.post("/api/auth/login")
    def login(b: LoginBody):
        return svc.auth.login(b.username, b.password)

    @app.post("/api/auth/logout")
    def logout(authorization: Optional[str] = Header(default=None)):
        if authorization and authorization.lower().startswith("bearer "):
            svc.auth.logout(authorization.split(" ", 1)[1])
        return {"ok": True}

    @app.get("/api/auth/me")
    def me(p: Principal = Depends(principal)):
        return {"kind": p.kind, "id": p.id, "role": p.role, "patient_id": p.patient_id, "name": p.name, "source": p.source,
                "permissions": svc.cfg["privacy"]["roles"].get(p.role, []), "patients": svc.auth.patients_for(p)}

    # ------------------------------------------------------------------ patients and EHR
    @app.get("/api/patients")
    def patients(p: Principal = Depends(principal)):
        return [svc.patient(pid) for pid in svc.auth.patients_for(p)]

    @app.post("/api/patients", status_code=201)
    def create_patient(b: PatientBody, p: Principal = Depends(principal)):
        if p.role != "clinician":
            raise AuthError("Only a clinician can register a patient.")
        out = svc.create_patient(b.patient_id, b.display_name, b.profile, b.clinical, dose_limits=b.dose_limits, source="manual")
        svc.auth.link(p.id, b.patient_id)
        return out

    @app.get("/api/patients/{pid}")
    def patient(pid: str, p: Principal = Depends(principal)):
        access(pid, p)
        return svc.patient(pid)

    @app.get("/api/patients/{pid}/ehr")
    def get_ehr(pid: str, p: Principal = Depends(principal)):
        access(pid, p)
        return svc.ehr(pid)

    @app.put("/api/patients/{pid}/ehr")
    def put_ehr(pid: str, b: EhrBody, p: Principal = Depends(principal)):
        access(pid, p)
        if p.role != "clinician":
            raise AuthError("Only a clinician can change the EHR.")
        return svc.update_ehr(pid, b.profile, b.clinical, b.dose_limits)

    # ------------------------------------------------------------------ devices
    @app.post("/api/patients/{pid}/pairing")
    def pairing(pid: str, request: Request, p: Principal = Depends(principal)):
        return svc.auth.create_pairing(p, pid, svc.settings.public_url or str(request.base_url).rstrip("/"))

    @app.post("/api/devices/pair")
    def pair(b: PairBody):
        return svc.auth.pair(b.code, b.kind, b.name, b.source)

    @app.delete("/api/devices/{device_id}")
    def unpair(device_id: str, p: Principal = Depends(principal)):
        svc.auth.unpair(p, device_id)
        return {"ok": True}

    # ------------------------------------------------------------------ readings, features, status
    @app.post("/api/readings")
    def post_readings(b: Union[ReadingsBody, list[Reading]], p: Principal = Depends(principal)):
        rs = b.readings if isinstance(b, ReadingsBody) else b
        return svc.ingest(p, rs)

    @app.get("/api/patients/{pid}/readings")
    def get_readings(pid: str, since: Optional[str] = None, until: Optional[str] = None, types: Optional[str] = None,
                     limit: int = Query(default=1000, le=20000), p: Principal = Depends(principal)):
        access(pid, p)
        return svc.readings(pid, since, until, types.split(",") if types else None, limit)

    @app.get("/api/patients/{pid}/features")
    def get_features(pid: str, p: Principal = Depends(principal)):
        access(pid, p)
        return svc.build_request(pid)[1]

    @app.get("/api/patients/{pid}/status")
    def status(pid: str, p: Principal = Depends(principal)):
        access(pid, p)
        st = svc.latest_status(pid)
        if st is None:
            return {"status": "NO_EVALUATION_YET", "patient_id": pid}
        if not svc.auth.can(p, "view_technical"):
            st.pop("result", None)
            st.pop("score", None)
            st["views"] = {k: st["views"][k] for k in (("watch",) if p.kind == "device" else ("phone_patient", "watch"))}
        return st

    @app.post("/api/patients/{pid}/evaluate")
    async def evaluate(pid: str, p: Principal = Depends(principal)):
        access(pid, p)
        out = await asyncio.get_running_loop().run_in_executor(None, svc.evaluate, pid)
        return shape_for(svc, p)(out) if not svc.auth.can(p, "view_technical") else out

    @app.get("/api/patients/{pid}/history")
    def history(pid: str, limit: int = Query(default=200, le=500), p: Principal = Depends(principal)):
        access(pid, p)
        rows = svc.history(pid, limit)
        if not svc.auth.can(p, "view_technical"):
            for r in rows:
                r.pop("score", None)
        return rows

    @app.post("/api/patients/{pid}/symptoms")
    async def symptoms(pid: str, b: SymptomsBody, p: Principal = Depends(principal)):
        access(pid, p)
        out = await asyncio.get_running_loop().run_in_executor(None, svc.report_symptoms, pid, b.symptoms, b.note)
        return {**shape_for(svc, p)(out), "checkin": out["checkin"]}

    @app.get("/api/patients/{pid}/symptoms")
    def symptom_history(pid: str, limit: int = Query(default=50, le=200), p: Principal = Depends(principal)):
        access(pid, p)
        return svc.app.checkins(pid, limit)

    # ------------------------------------------------------------------ patient app: care team, profile, settings (DEMO)
    @app.get("/api/patients/{pid}/care-team")
    def care_team(pid: str, p: Principal = Depends(principal)):
        access(pid, p)
        return svc.app.care_team(pid)

    @app.get("/api/patients/{pid}/health-profile")
    def health_profile(pid: str, p: Principal = Depends(principal)):
        access(pid, p)
        return svc.app.health_profile(svc.ehr(pid))

    @app.get("/api/patients/{pid}/app-settings")
    def app_settings(pid: str, p: Principal = Depends(principal)):
        access(pid, p)
        return svc.app.settings(pid)

    @app.put("/api/patients/{pid}/app-settings")
    def put_app_settings(pid: str, b: AppSettingsBody, p: Principal = Depends(principal)):
        return svc.app.update_settings(p, pid, **b.model_dump())

    # ------------------------------------------------------------------ patient app: appointments (DEMO, nothing is sent anywhere)
    @app.get("/api/patients/{pid}/appointments")
    def appointments(pid: str, p: Principal = Depends(principal)):
        access(pid, p)
        return svc.app.appointments(pid)

    @app.get("/api/patients/{pid}/appointments/slots")
    def appointment_slots(pid: str, date: str, clinician_id: Optional[str] = None, p: Principal = Depends(principal)):
        access(pid, p)
        return svc.app.slots(pid, date, clinician_id)

    @app.post("/api/patients/{pid}/appointments", status_code=201)
    def book_appointment(pid: str, b: AppointmentBody, p: Principal = Depends(principal)):
        return svc.app.book(p, pid, b.start_at, b.reason, b.mode, b.clinician_id)

    @app.get("/api/appointments")
    def clinic_appointments(since: Optional[str] = None, status: Optional[str] = None, p: Principal = Depends(principal)):
        return svc.app.clinician_appointments(p, since, status)

    @app.post("/api/appointments/{aid}/cancel")
    def cancel_appointment(aid: str, b: CancelBody = CancelBody(), p: Principal = Depends(principal)):
        return svc.app.cancel(p, aid, b.reason)

    @app.post("/api/appointments/{aid}/reschedule")
    def reschedule_appointment(aid: str, b: RescheduleBody, p: Principal = Depends(principal)):
        return svc.app.reschedule(p, aid, b.start_at)

    @app.post("/api/appointments/{aid}/complete")
    def complete_appointment(aid: str, b: CompleteBody = CompleteBody(), p: Principal = Depends(principal)):
        return svc.app.complete(p, aid, b.note, b.no_show)

    # ------------------------------------------------------------------ patient app: medicines and reminders
    @app.get("/api/patients/{pid}/medications")
    def medications(pid: str, day: Optional[str] = None, p: Principal = Depends(principal)):
        access(pid, p)
        return svc.app.medications(pid, day)

    @app.post("/api/patients/{pid}/medications", status_code=201)
    def add_medication(pid: str, b: MedicationBody, p: Principal = Depends(principal)):
        return svc.app.add_medication(p, pid, **b.model_dump())

    @app.put("/api/medications/{mid}")
    def update_medication(mid: str, b: MedicationPatch, p: Principal = Depends(principal)):
        return svc.app.update_medication(p, mid, **b.model_dump())

    @app.delete("/api/medications/{mid}")
    def remove_medication(mid: str, p: Principal = Depends(principal)):
        return svc.app.remove_medication(p, mid)

    @app.post("/api/medications/{mid}/taken")
    def medication_taken(mid: str, b: TakenBody, p: Principal = Depends(principal)):
        return svc.app.mark_taken(p, mid, b.day, b.slot, b.taken)

    @app.get("/api/patients/{pid}/medications/adherence")
    def adherence(pid: str, days: int = Query(default=7, ge=1, le=60), today: Optional[str] = None, p: Principal = Depends(principal)):
        access(pid, p)
        return svc.app.adherence(pid, days, today)

    # ------------------------------------------------------------------ patient app: emergency contacts and notifications
    @app.get("/api/patients/{pid}/contacts")
    def contacts(pid: str, p: Principal = Depends(principal)):
        access(pid, p)
        return svc.app.contacts(pid)

    @app.post("/api/patients/{pid}/contacts", status_code=201)
    def add_contact(pid: str, b: ContactBody, p: Principal = Depends(principal)):
        return svc.app.add_contact(p, pid, b.name, b.phone, b.relation, b.is_primary)

    @app.put("/api/contacts/{cid}")
    def update_contact(cid: str, b: ContactPatch, p: Principal = Depends(principal)):
        return svc.app.update_contact(p, cid, **b.model_dump())

    @app.delete("/api/contacts/{cid}")
    def remove_contact(cid: str, p: Principal = Depends(principal)):
        return svc.app.remove_contact(p, cid)

    @app.get("/api/patients/{pid}/notifications")
    def notifications(pid: str, p: Principal = Depends(principal)):
        access(pid, p)
        return svc.heart.filter_notifications(p, pid, svc.app.notifications(pid))

    @app.post("/api/patients/{pid}/notifications/read")
    def notifications_read(pid: str, b: ReadBody = ReadBody(), p: Principal = Depends(principal)):
        return svc.app.mark_read(p, pid, b.ids)

    # ------------------------------------------------------------------ AI health assistant (chatbot.py)
    @app.get("/api/assistant/status")
    def assistant_status(p: Principal = Depends(principal)):
        return svc.chat.status()

    @app.get("/api/patients/{pid}/assistant/consent")
    def assistant_consent(pid: str, p: Principal = Depends(principal)):
        access(pid, p)
        return svc.chat.consent(pid)

    @app.put("/api/patients/{pid}/assistant/consent")
    def put_assistant_consent(pid: str, b: ChatConsentBody, p: Principal = Depends(principal)):
        return svc.chat.set_consent(p, pid, b.share_health_data)

    @app.post("/api/patients/{pid}/assistant/messages")
    async def assistant_send(pid: str, b: ChatBody, p: Principal = Depends(principal)):
        return await asyncio.get_running_loop().run_in_executor(None, svc.chat.send, p, pid, b.message, b.conversation_id, b.language)

    @app.get("/api/patients/{pid}/assistant/conversations")
    def assistant_conversations(pid: str, p: Principal = Depends(principal)):
        return svc.chat.conversations(p, pid)

    @app.get("/api/assistant/conversations/{cid}")
    def assistant_conversation(cid: str, p: Principal = Depends(principal)):
        return svc.chat.messages(p, cid)

    @app.delete("/api/assistant/conversations/{cid}")
    def assistant_delete(cid: str, p: Principal = Depends(principal)):
        return svc.chat.delete(p, cid)

    # ------------------------------------------------------------------ nearby hospitals, medical stores and ambulance (emergency_map.py)
    @app.get("/api/maps/config")
    def maps_config(p: Principal = Depends(principal)):
        return svc.maps.config()

    @app.post("/api/patients/{pid}/nearby-hospitals")
    async def nearby_hospitals(pid: str, b: LocationBody, p: Principal = Depends(principal)):
        return await asyncio.get_running_loop().run_in_executor(None, svc.maps.nearby, p, pid, b.lat, b.lng, b.radius_m, b.alert_id)

    @app.post("/api/patients/{pid}/nearby-pharmacies")
    async def nearby_pharmacies(pid: str, b: LocationBody, p: Principal = Depends(principal)):
        return await asyncio.get_running_loop().run_in_executor(None, svc.maps.nearby_pharmacies, p, pid, b.lat, b.lng, b.radius_m)

    @app.post("/api/patients/{pid}/route")
    async def route(pid: str, b: RouteBody, p: Principal = Depends(principal)):
        return await asyncio.get_running_loop().run_in_executor(None, svc.maps.route, p, pid, (b.origin_lat, b.origin_lng),
                                                                (b.dest_lat, b.dest_lng))

    @app.post("/api/patients/{pid}/ambulance", status_code=201)
    def ambulance_request(pid: str, b: AmbulanceBody, p: Principal = Depends(principal)):
        return svc.maps.request(p, pid, b.lat, b.lng, b.alert_id, b.confirm_demo)

    @app.get("/api/patients/{pid}/ambulance/active")
    def ambulance_active(pid: str, p: Principal = Depends(principal)):
        return {"request": svc.maps.active(p, pid)}

    @app.get("/api/ambulance/{rid}")
    def ambulance_get(rid: str, p: Principal = Depends(principal)):
        return svc.maps.get(p, rid)

    @app.post("/api/ambulance/{rid}/cancel")
    def ambulance_cancel(rid: str, p: Principal = Depends(principal)):
        return svc.maps.cancel(p, rid)

    # ------------------------------------------------------------------ wellness (wellness.py)
    @app.get("/api/patients/{pid}/wellness")
    async def wellness_plan(pid: str, day: Optional[str] = None, p: Principal = Depends(principal)):
        access(pid, p)
        return await asyncio.get_running_loop().run_in_executor(None, svc.wellness.plan, pid, day)

    @app.get("/api/patients/{pid}/wellness/progress")
    async def wellness_progress(pid: str, day: Optional[str] = None, p: Principal = Depends(principal)):
        access(pid, p)
        return await asyncio.get_running_loop().run_in_executor(None, svc.wellness.progress, pid, day)

    @app.get("/api/patients/{pid}/wellness/preferences")
    def wellness_prefs(pid: str, p: Principal = Depends(principal)):
        access(pid, p)
        return svc.wellness.prefs(pid)

    @app.put("/api/patients/{pid}/wellness/preferences")
    def put_wellness_prefs(pid: str, b: WellnessPrefsBody, p: Principal = Depends(principal)):
        return svc.wellness.update_prefs(p, pid, b.model_dump(exclude_none=True))

    @app.post("/api/patients/{pid}/wellness/log")
    async def wellness_log(pid: str, b: WellnessLogBody, p: Principal = Depends(principal)):
        return await asyncio.get_running_loop().run_in_executor(None, svc.wellness.log, p, pid, b.day, b.metric, b.value, b.mode)

    @app.get("/api/patients/{pid}/wellness/reminders")
    def wellness_reminders(pid: str, p: Principal = Depends(principal)):
        access(pid, p)
        return svc.wellness.reminders(pid)

    # ------------------------------------------------------------------ heart health (heart_health.py)
    def _run(fn, *a):
        return asyncio.get_running_loop().run_in_executor(None, fn, *a)

    @app.get("/api/patients/{pid}/heart/overview")
    async def heart_overview(pid: str, p: Principal = Depends(principal)):
        return await _run(svc.heart.overview, p, pid)

    @app.get("/api/patients/{pid}/heart/summary")
    async def heart_summary(pid: str, p: Principal = Depends(principal)):
        return await _run(svc.heart.summary, p, pid)

    @app.get("/api/patients/{pid}/heart/reports")
    async def heart_reports(pid: str, p: Principal = Depends(principal)):
        return await _run(svc.heart.reports, p, pid)

    @app.post("/api/patients/{pid}/heart/reports", status_code=201)
    async def heart_report_now(pid: str, p: Principal = Depends(principal)):
        return await _run(svc.heart.generate, p, pid)

    @app.get("/api/patients/{pid}/heart/reports/{week}")
    def heart_report(pid: str, week: str, p: Principal = Depends(principal)):
        return svc.heart.report(p, pid, week)

    @app.get("/api/patients/{pid}/caregiver-consent")
    def caregiver_consent(pid: str, p: Principal = Depends(principal)):
        return svc.heart.caregiver_consent(p, pid)

    @app.put("/api/patients/{pid}/caregiver-consent/{cid}")
    def put_caregiver_consent(pid: str, cid: str, b: CaregiverConsentBody, p: Principal = Depends(principal)):
        return svc.heart.set_caregiver_consent(p, pid, cid, **b.model_dump())

    # ------------------------------------------------------------------ alerts
    @app.get("/api/patients/{pid}/alerts")
    def alerts(pid: str, open: bool = False, p: Principal = Depends(principal)):
        access(pid, p)
        return svc.alerts.list(pid, open_only=open)

    @app.post("/api/alerts/{aid}/fall-response")
    def fall_response(aid: str, b: FallResponseBody, p: Principal = Depends(principal)):
        return svc.fall_response(p, aid, b.answer)

    @app.post("/api/alerts/{aid}/{action}")
    def alert_action(aid: str, action: str, b: AlertActionBody = AlertActionBody(), p: Principal = Depends(principal)):
        if action not in ("display", "acknowledge", "review", "escalate", "resolve", "user_action"):
            raise HTTPException(404, "Unknown alert action")
        from risk_engine.alert_service import PermissionDenied
        try:
            return svc.alert_action(p, aid, action, b.note, b.review_status, b.to, b.user_action)
        except PermissionDenied as e:
            raise AuthError(str(e))

    # ------------------------------------------------------------------ what-if
    @app.post("/api/patients/{pid}/whatif")
    async def whatif(pid: str, b: WhatIfBody, p: Principal = Depends(principal)):
        access(pid, p)
        return await asyncio.get_running_loop().run_in_executor(None, svc.whatif_live, p, pid, b.changes, b.hours)

    @app.get("/api/whatif/cases")
    async def twin_cases(p: Principal = Depends(principal)):
        svc.auth.require(p, "what_if")
        return await asyncio.get_running_loop().run_in_executor(None, svc.whatif_cases)

    @app.post("/api/whatif/simulate")
    async def twin_simulate(b: TwinBody, p: Principal = Depends(principal)):
        return await asyncio.get_running_loop().run_in_executor(None, svc.whatif_case, p, b.model_dump())

    # ------------------------------------------------------------------ virtual watch (SIMULATED)
    @app.get("/api/simulator/stays")
    def sim_stays(p: Principal = Depends(principal)):
        from simulator.watch_sim import available_stays
        return available_stays(svc.settings.replay_dir)

    @app.post("/api/simulator/sessions", status_code=201)
    async def sim_start(b: SimStartBody, p: Principal = Depends(principal)):
        return svc.start_simulation(p, b.patient_id, b.stay_id, b.speed, b.backfill_hours, b.seed)

    @app.get("/api/simulator/sessions")
    def sim_list(p: Principal = Depends(principal)):
        return [svc.sim_status(s) for s, v in svc.sim_sessions.items() if svc.auth.can_access(p, v["patient_id"])]

    @app.get("/api/simulator/sessions/{sid}")
    def sim_get(sid: str, p: Principal = Depends(principal)):
        st = svc.sim_status(sid)
        access(st["patient_id"], p)
        return st

    @app.post("/api/simulator/sessions/{sid}/trigger")
    def sim_trigger(sid: str, b: SimTriggerBody, p: Principal = Depends(principal)):
        return svc.sim_trigger(p, sid, b.event, b.minutes)

    @app.delete("/api/simulator/sessions/{sid}")
    def sim_stop(sid: str, p: Principal = Depends(principal)):
        return svc.stop_simulation(p, sid)

    @app.get("/simulator", response_class=HTMLResponse)
    def sim_page():
        return (STATIC / "simulator.html").read_text(encoding="utf-8")

    @app.get("/", include_in_schema=False)
    def root():
        return {"service": "CardioTwin backend", "docs": "/docs", "simulator": "/simulator", "health": "/api/health"}

    # ------------------------------------------------------------------ WebSockets
    async def _ws_principal(ws: WebSocket, token: str | None) -> Principal | None:
        try:
            return svc.auth.principal(token)
        except AuthError as e:
            await ws.close(code=4401, reason=str(e))
            return None

    @app.websocket("/ws/device")
    async def ws_device(ws: WebSocket, token: Optional[str] = None):
        await ws.accept()
        p = await _ws_principal(ws, token)
        if p is None:
            return
        if p.kind != "device":
            await ws.close(code=4403, reason="Use /ws/live/{patient_id} for user sessions.")
            return
        pid = p.patient_id
        svc.hub.subscribe(pid, ws, shape_for(svc, p))
        loop = asyncio.get_running_loop()
        try:
            await ws.send_text(json.dumps({"type": "hello", "patient_id": pid, "device_id": p.id, "source": p.source,
                                           "simulated": p.source == "simulator", "label": "SIMULATED" if p.source == "simulator" else None}))
            while True:
                msg = json.loads(await ws.receive_text())
                t = msg.get("type")
                try:
                    if t == "readings":
                        rs = [Reading(**r) for r in msg.get("readings", [])]
                        res = await loop.run_in_executor(None, svc.ingest, p, rs)
                        await ws.send_text(json.dumps({"type": "ack", **res}))
                    elif t == "fall_response":
                        a = await loop.run_in_executor(None, svc.fall_response, p, msg["alert_id"], msg["answer"])
                        await ws.send_text(json.dumps({"type": "ack", "alert_id": a["alert_id"], "state": a["state"]}))
                    elif t == "ping":
                        await ws.send_text(json.dumps({"type": "pong"}))
                    else:
                        await ws.send_text(json.dumps({"type": "error", "detail": f"unknown message type {t}"}))
                except (ValidationError, ValueError, KeyError, AuthError) as e:
                    await ws.send_text(json.dumps({"type": "error", "detail": str(e)[:500]}))
        except WebSocketDisconnect:
            pass
        finally:
            svc.hub.unsubscribe(pid, ws)

    @app.websocket("/ws/live/{pid}")
    async def ws_live(ws: WebSocket, pid: str, token: Optional[str] = None):
        await ws.accept()
        p = await _ws_principal(ws, token)
        if p is None:
            return
        if not svc.auth.can_access(p, pid):
            await ws.close(code=4403, reason="No access to this patient.")
            return
        shape = shape_for(svc, p)
        svc.hub.subscribe(pid, ws, shape)
        try:
            st = svc.latest_status(pid)
            hello = {"type": "hello", "patient_id": pid, "role": p.role,
                     "open_alerts": svc.alerts.list(pid, open_only=True)}
            await ws.send_text(json.dumps(hello, default=str))
            if st:
                st.pop("result", None)
                m = shape({"type": "status", "patient_id": pid, **st})
                if m:
                    await ws.send_text(json.dumps(m, default=str))
            while True:
                msg = json.loads(await ws.receive_text())
                if msg.get("type") == "ping":
                    await ws.send_text(json.dumps({"type": "pong"}))
                elif msg.get("type") == "displayed" and msg.get("alert_id"):
                    try:
                        svc.alert_action(p, msg["alert_id"], "display")
                    except Exception as e:
                        await ws.send_text(json.dumps({"type": "error", "detail": str(e)[:300]}))
        except WebSocketDisconnect:
            pass
        finally:
            svc.hub.unsubscribe(pid, ws)

    return app
