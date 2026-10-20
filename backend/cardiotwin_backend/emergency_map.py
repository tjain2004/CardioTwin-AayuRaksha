# Nearby help: hospitals and medical stores (pharmacies) around the user's location (Google Places API (New)), driving
# route and time to a chosen place (Google Routes API), and ambulance request and tracking through a pluggable provider.
# Medical stores are listed only from Google Places; with no server key the app offers a Google Maps search instead,
# never made-up shops. CardioTwin does not suggest which medicine to buy or take.
#
# Google Maps does not know where ambulances are. Live ambulance positions need an authorised dispatch provider or a
# fleet-tracking backend; none is connected here. So:
# * provider "none": no vehicles are shown; the screen says so and offers 112 / 108 through the phone dialler.
# * provider "demo" (default for this prototype): a DEMO simulation the user starts on purpose. Every vehicle, position,
#   status and arrival time it returns is labelled simulated and DEMO, and the text says no ambulance is coming.
# A real provider plugs in by implementing AmbulanceProvider (request / status / cancel / nearby) and setting
# CARDIOTWIN_AMBULANCE_PROVIDER to its name. This module never contacts emergency services and is not a second
# emergency system: it is reached from the existing emergency screen and records what the user did on the existing
# alert (alert user_action), the same way "Call 112" is recorded.
from __future__ import annotations

import hashlib
import math
import threading
import time
import uuid
from datetime import datetime

import httpx

from .auth import AuthError, Principal
from .db import Database, dumps, loads
from .readings import iso, utcnow

SCHEMA = """
CREATE TABLE IF NOT EXISTS ambulance_requests (
  request_id TEXT PRIMARY KEY, patient_id TEXT NOT NULL, created_by TEXT NOT NULL, provider TEXT NOT NULL, alert_id TEXT,
  lat REAL, lng REAL, created_at TEXT NOT NULL, cancelled_at TEXT, simulated INTEGER NOT NULL DEFAULT 1, vehicle_json TEXT,
  history_json TEXT NOT NULL DEFAULT '[]');
CREATE INDEX IF NOT EXISTS ix_amb_pt ON ambulance_requests (patient_id, created_at);
"""

PLACES_URL = "https://places.googleapis.com/v1/places:searchNearby"
ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"
PLACES_FIELDS = ("places.id,places.displayName,places.formattedAddress,places.location,places.nationalPhoneNumber,"
                 "places.internationalPhoneNumber,places.currentOpeningHours.openNow,places.googleMapsUri,places.businessStatus,"
                 "places.primaryType")
ROUTES_FIELDS = "routes.duration,routes.distanceMeters,routes.polyline.encodedPolyline"
EMERGENCY_NUMBERS = {"IN": [{"label": "Emergency (police, fire, ambulance)", "number": "112", "uri": "tel:112", "primary": True},
                            {"label": "Ambulance (most states)", "number": "108", "uri": "tel:108", "primary": False}]}
DEMO_LABEL = "DEMO"
DEMO_NOTE = ("DEMO simulation. These are not real ambulances and nobody has been sent. For a real emergency call 112 "
             "(or 108 for an ambulance).")
NO_PROVIDER_NOTE = ("No live ambulance service is connected to CardioTwin, so ambulance positions cannot be shown. "
                    "To get an ambulance, call 112 or 108.")
MAPS_SEARCH_URL = "https://www.google.com/maps/search/?api=1&query=hospital"
PHARMACY_SEARCH_URL = "https://www.google.com/maps/search/?api=1&query=pharmacy"
# what each kind of place searches for in Places API (New), and the words the app shows for it
PLACE_KINDS = {"hospital": {"types": ["hospital"], "noun": "hospitals", "fallback_name": "Hospital", "search_url": MAPS_SEARCH_URL},
               "pharmacy": {"types": ["pharmacy", "drugstore"], "noun": "medical stores", "fallback_name": "Medical store",
                            "search_url": PHARMACY_SEARCH_URL}}
MEDICINE_NOTE = ("CardioTwin does not tell you which medicine to take for symptoms. Take only the medicines your doctor "
                 "prescribed, as prescribed. If you feel unwell, call your doctor; for chest pain, fainting or trouble "
                 "breathing call 112 (or 108 for an ambulance).")


def haversine_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    r = 6371000.0
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dp, dl = p2 - p1, math.radians(b[1] - a[1])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def offset(lat: float, lng: float, metres: float, bearing_deg: float) -> tuple[float, float]:
    b = math.radians(bearing_deg)
    dlat = metres * math.cos(b) / 111_320
    dlng = metres * math.sin(b) / (111_320 * max(math.cos(math.radians(lat)), 1e-6))
    return lat + dlat, lng + dlng


def check_location(lat: float, lng: float) -> None:
    if not (-90 <= lat <= 90 and -180 <= lng <= 180) or (lat == 0 and lng == 0):
        raise ValueError("That location is not valid.")


# ---------------------------------------------------------------------------------------------------- providers
class AmbulanceProvider:
    name = "none"
    live = False
    simulated = False

    def nearby(self, lat: float, lng: float) -> dict:
        return {"available": False, "vehicles": [], "note": NO_PROVIDER_NOTE}

    def create(self, request_id: str, lat: float, lng: float) -> dict:
        raise ValueError(NO_PROVIDER_NOTE)

    def status(self, row: dict, now: float) -> dict:
        raise ValueError(NO_PROVIDER_NOTE)


class DemoAmbulanceProvider(AmbulanceProvider):
    """Simulated fleet for demonstrations. Deterministic from the request id, moves in real time, always labelled."""
    name = "demo"
    simulated = True
    REQUEST_S, SEARCH_S, ASSIGN_S, DRIVE_S = 2, 6, 4, 180  # requested, searching, assigned, then drives for 3 minutes (demo speed)
    ARRIVING_S, ON_SCENE_S = 30, 120                     # "arriving" in the last 30 s; "completed" 2 minutes after arrival

    def nearby(self, lat: float, lng: float) -> dict:
        h = int(hashlib.sha256(f"{lat:.3f},{lng:.3f}".encode()).hexdigest()[:6], 16)
        vehicles = []
        for i in range(3):
            vlat, vlng = offset(lat, lng, 1500 + (h >> (i * 4)) % 2500, (h + i * 127) % 360)
            vehicles.append({"vehicle_id": f"DEMO-{i + 1:02d}", "label": f"DEMO ambulance {i + 1} (not real)", "lat": vlat, "lng": vlng,
                             "status": "AVAILABLE", "simulated": True})
        return {"available": True, "vehicles": vehicles, "label": DEMO_LABEL, "note": DEMO_NOTE}

    def create(self, request_id: str, lat: float, lng: float) -> dict:
        h = int(hashlib.sha256(request_id.encode()).hexdigest()[:6], 16)
        slat, slng = offset(lat, lng, 2500 + h % 1500, h % 360)
        return {"vehicle_id": f"DEMO-{h % 90 + 10}", "label": "DEMO ambulance (not real)", "start": [slat, slng],
                "crew": "Simulated crew", "phone": None}

    def status(self, row: dict, now: float) -> dict:
        v = loads(row["vehicle_json"], {})
        t0 = datetime.fromisoformat(row["created_at"].replace("Z", "+00:00")).timestamp()
        el = now - t0
        dest = (row["lat"], row["lng"]) if row["lat"] is not None else None
        if row["cancelled_at"]:
            return {"status": "CANCELLED", "vehicle": None, "eta_seconds": None}
        if el < self.REQUEST_S:
            return {"status": "REQUESTED", "vehicle": None, "eta_seconds": None}
        if el < self.SEARCH_S:
            return {"status": "SEARCHING", "vehicle": None, "eta_seconds": None}
        start = tuple(v["start"])
        total = self.ASSIGN_S + self.DRIVE_S
        if el < self.SEARCH_S + self.ASSIGN_S:
            st, frac = "ASSIGNED", 0.0
        elif el < self.SEARCH_S + total:
            frac = (el - self.SEARCH_S - self.ASSIGN_S) / self.DRIVE_S
            st = "ARRIVING" if self.SEARCH_S + total - el <= self.ARRIVING_S else "EN_ROUTE"
        elif el < self.SEARCH_S + total + self.ON_SCENE_S:
            st, frac = "ARRIVED", 1.0
        else:
            st, frac = "COMPLETED", 1.0
        pos = (start[0] + (dest[0] - start[0]) * frac, start[1] + (dest[1] - start[1]) * frac) if dest else start
        eta = None if st in ("ARRIVED", "COMPLETED") else int(self.SEARCH_S + total - el)
        return {"status": st, "eta_seconds": max(eta, 0) if eta is not None else None,
                "vehicle": {"vehicle_id": v["vehicle_id"], "label": v["label"], "lat": pos[0], "lng": pos[1], "simulated": True},
                "route": [list(start), list(dest)] if dest else None,
                "distance_m": round(haversine_m(pos, dest)) if dest else None}


PROVIDERS = {"none": AmbulanceProvider, "demo": DemoAmbulanceProvider}
STAGES = ["REQUESTED", "SEARCHING", "ASSIGNED", "EN_ROUTE", "ARRIVING", "ARRIVED", "COMPLETED"]
STATUS_TEXT = {"REQUESTED": "Emergency requested (DEMO)", "SEARCHING": "Looking for the nearest ambulance (DEMO)",
               "ASSIGNED": "DEMO ambulance assigned", "EN_ROUTE": "DEMO ambulance on the way", "ARRIVING": "DEMO ambulance arriving",
               "ARRIVED": "DEMO ambulance has arrived", "COMPLETED": "DEMO simulation finished", "CANCELLED": "Request cancelled"}


# ---------------------------------------------------------------------------------------------------- service
class EmergencyMap:
    def __init__(self, db: Database, svc, settings, transport: httpx.BaseTransport | None = None):
        self.db, self.svc, self.settings = db, svc, settings
        db.ensure_schema(SCHEMA)
        prov = (settings.ambulance_provider or "none").lower()
        if prov not in PROVIDERS:
            raise ValueError(f"CARDIOTWIN_AMBULANCE_PROVIDER must be one of {tuple(PROVIDERS)}")
        self.provider: AmbulanceProvider = PROVIDERS[prov]()
        self.http = httpx.Client(timeout=8.0, transport=transport)
        self._cache: dict[str, tuple[float, dict]] = {}
        self._cache_lock = threading.Lock()

    # ------------------------------------------------------------------ configuration for the app
    def config(self) -> dict:
        s = self.settings
        return {"maps": {"browser_key": s.google_maps_browser_key or None, "configured": bool(s.google_maps_browser_key)},
                "places_configured": bool(s.google_maps_server_key), "routes_configured": bool(s.google_maps_server_key),
                "ambulance": {"provider": self.provider.name, "live": self.provider.live, "simulated": self.provider.simulated,
                              "label": DEMO_LABEL if self.provider.simulated else None,
                              "note": DEMO_NOTE if self.provider.simulated else (None if self.provider.live else NO_PROVIDER_NOTE)},
                "emergency_numbers": EMERGENCY_NUMBERS.get(s.country_code, EMERGENCY_NUMBERS["IN"]),
                "maps_search_url": MAPS_SEARCH_URL, "pharmacy_search_url": PHARMACY_SEARCH_URL,
                "medicine_note": MEDICINE_NOTE,
                "privacy": "Your location is used only for this search and is not stored, except while a DEMO ambulance "
                           "simulation runs (then it is kept with that request and cleared when you cancel)."}

    def _record_on_alert(self, p: Principal, patient_id: str, alert_id: str | None, action: str) -> str | None:
        """Note on the existing emergency alert what the user did here (never 'dispatched')."""
        if not alert_id:
            crit = [a for a in self.svc.alerts.list(patient_id, open_only=True) if a["severity"] == "critical"]
            alert_id = crit[0]["alert_id"] if crit else None
        if not alert_id:
            return None
        try:
            a = self.svc.alert_for(p, alert_id)
            if a["patient_id"] != patient_id:
                return None
            self.svc.alert_action(p, alert_id, "user_action", user_action=action)
            return alert_id
        except Exception:
            return None

    # ------------------------------------------------------------------ hospitals
    def nearby(self, p: Principal, patient_id: str, lat: float, lng: float, radius_m: int = 5000, alert_id: str | None = None) -> dict:
        self.svc.auth.require_access(p, patient_id)
        check_location(lat, lng)
        radius_m = int(min(max(radius_m, 500), 50000))
        linked = self._record_on_alert(p, patient_id, alert_id, "opened_nearby_hospitals")
        out = {"origin": {"lat": round(lat, 5), "lng": round(lng, 5)}, "radius_m": radius_m, "linked_alert_id": linked,
               "ambulances": self.provider.nearby(lat, lng), "maps_search_url": MAPS_SEARCH_URL,
               "emergency_numbers": self.config()["emergency_numbers"]}
        res = self._places(lat, lng, radius_m, "hospital")
        return {**out, **res, "hospitals": res.pop("places")}

    # ------------------------------------------------------------------ medical stores (pharmacies)
    def nearby_pharmacies(self, p: Principal, patient_id: str, lat: float, lng: float, radius_m: int = 3000) -> dict:
        """Medical stores near the user, from Google Places only. No advice on what to buy: the app shows the medicines
        already prescribed on the record and MEDICINE_NOTE."""
        self.svc.auth.require_access(p, patient_id)
        check_location(lat, lng)
        radius_m = int(min(max(radius_m, 500), 50000))
        out = {"origin": {"lat": round(lat, 5), "lng": round(lng, 5)}, "radius_m": radius_m,
               "maps_search_url": PHARMACY_SEARCH_URL, "medicine_note": MEDICINE_NOTE,
               "emergency_numbers": self.config()["emergency_numbers"]}
        res = self._places(lat, lng, radius_m, "pharmacy")
        return {**out, **res, "pharmacies": res.pop("places")}

    def _places(self, lat: float, lng: float, radius_m: int, kind: str) -> dict:
        """Places API (New) nearby search for one kind of place, nearest first, cached for 10 minutes per ~100 m."""
        k = PLACE_KINDS[kind]
        key = self.settings.google_maps_server_key
        if not key:
            return {"available": False, "places": [], "reason": "no_server_key",
                    "note": f"Search for {k['noun']} is not set up on the server (no Google Maps server key). Use 'Search in Google Maps' instead."}
        ck = f"{kind},{lat:.3f},{lng:.3f},{radius_m}"
        with self._cache_lock:
            hit = self._cache.get(ck)
            if hit and time.monotonic() - hit[0] < 600:
                return {**hit[1], "places": list(hit[1]["places"]), "cached": True}
        body = {"includedTypes": k["types"], "maxResultCount": 15, "rankPreference": "DISTANCE",
                "locationRestriction": {"circle": {"center": {"latitude": lat, "longitude": lng}, "radius": float(radius_m)}}}
        try:
            r = self.http.post(PLACES_URL, json=body, headers={"X-Goog-Api-Key": key, "X-Goog-FieldMask": PLACES_FIELDS})
        except httpx.HTTPError as e:
            return {"available": False, "places": [], "reason": "network",
                    "note": f"Could not reach Google Places ({type(e).__name__}). Use 'Search in Google Maps' or call 112."}
        if r.status_code != 200:
            return {"available": False, "places": [], "reason": f"places_http_{r.status_code}",
                    "note": "Google Places did not answer (check the server key and that Places API (New) is enabled)."}
        places = []
        for pl in r.json().get("places", []):
            loc = pl.get("location") or {}
            if "latitude" not in loc:
                continue
            if pl.get("businessStatus") not in (None, "OPERATIONAL"):
                continue
            places.append({"place_id": pl.get("id"), "name": (pl.get("displayName") or {}).get("text") or k["fallback_name"],
                           "address": pl.get("formattedAddress"), "lat": loc["latitude"], "lng": loc["longitude"],
                           "phone": pl.get("nationalPhoneNumber") or pl.get("internationalPhoneNumber"),
                           "open_now": (pl.get("currentOpeningHours") or {}).get("openNow"), "maps_url": pl.get("googleMapsUri"),
                           "distance_m": round(haversine_m((lat, lng), (loc["latitude"], loc["longitude"])))})
        places.sort(key=lambda h: h["distance_m"])
        res = {"available": True, "places": places, "source": "Google Places API (New)",
               "note": None if places else f"No {k['noun']} found in this radius. Try a wider search or Google Maps."}
        with self._cache_lock:
            self._cache[ck] = (time.monotonic(), res)
            if len(self._cache) > 500:
                self._cache.pop(next(iter(self._cache)))
        return {**res, "places": list(places)}

    def route(self, p: Principal, patient_id: str, origin: tuple[float, float], dest: tuple[float, float]) -> dict:
        self.svc.auth.require_access(p, patient_id)
        check_location(*origin)
        check_location(*dest)
        straight = round(haversine_m(origin, dest))
        key = self.settings.google_maps_server_key
        if not key:
            return {"available": False, "straight_line_m": straight, "reason": "no_server_key"}
        body = {"origin": {"location": {"latLng": {"latitude": origin[0], "longitude": origin[1]}}},
                "destination": {"location": {"latLng": {"latitude": dest[0], "longitude": dest[1]}}},
                "travelMode": "DRIVE", "routingPreference": "TRAFFIC_AWARE"}
        try:
            r = self.http.post(ROUTES_URL, json=body, headers={"X-Goog-Api-Key": key, "X-Goog-FieldMask": ROUTES_FIELDS})
        except httpx.HTTPError as e:
            return {"available": False, "straight_line_m": straight, "reason": f"network: {type(e).__name__}"}
        if r.status_code != 200 or not r.json().get("routes"):
            return {"available": False, "straight_line_m": straight, "reason": f"routes_http_{r.status_code}"}
        rt = r.json()["routes"][0]
        dur = rt.get("duration", "0s")
        secs = int(float(dur[:-1])) if isinstance(dur, str) and dur.endswith("s") else None
        return {"available": True, "distance_m": rt.get("distanceMeters"), "duration_s": secs,
                "polyline": (rt.get("polyline") or {}).get("encodedPolyline"), "source": "Google Routes API",
                "straight_line_m": straight}

    # ------------------------------------------------------------------ ambulance requests
    def _row(self, request_id: str) -> dict:
        r = self.db.one("SELECT * FROM ambulance_requests WHERE request_id=?", (request_id,))
        if not r:
            raise KeyError(request_id)
        return r

    def _view(self, r: dict) -> dict:
        prov = PROVIDERS.get(r["provider"], AmbulanceProvider)()
        st = prov.status(r, time.time())
        return {"request_id": r["request_id"], "patient_id": r["patient_id"], "provider": r["provider"], "alert_id": r["alert_id"],
                "created_at": r["created_at"], "simulated": bool(r["simulated"]), "label": DEMO_LABEL if r["simulated"] else None,
                "note": DEMO_NOTE if r["simulated"] else None, "status_text": STATUS_TEXT.get(st["status"], st["status"]), "stages": STAGES,
                "destination": {"lat": r["lat"], "lng": r["lng"]} if r["lat"] is not None else None,
                "history": loads(r["history_json"], []), **st}

    def request(self, p: Principal, patient_id: str, lat: float, lng: float, alert_id: str | None = None,
                confirm_demo: bool = False) -> dict:
        self.svc.auth.require_access(p, patient_id)
        if p.kind != "user" or p.role not in ("patient", "caregiver", "clinician"):
            raise AuthError("Only the patient, a caregiver or a clinician can do this.")
        check_location(lat, lng)
        if not self.provider.live and not self.provider.simulated:
            raise ValueError(NO_PROVIDER_NOTE)
        if self.provider.simulated and not confirm_demo:
            raise ValueError("This starts a DEMO simulation only; no ambulance will come. Confirm that you understand (confirm_demo).")
        rid = str(uuid.uuid4())
        vehicle = self.provider.create(rid, lat, lng)
        linked = self._record_on_alert(p, patient_id, alert_id,
                                       "started_demo_ambulance_simulation_not_dispatched" if self.provider.simulated else "requested_ambulance_via_provider")
        now = iso(utcnow())
        hist = [{"event": "REQUESTED", "at": now, "by": p.id, "by_role": p.role}]
        self.db.execute("INSERT INTO ambulance_requests VALUES (?,?,?,?,?,?,?,?,NULL,?,?,?)",
                        (rid, patient_id, p.id, self.provider.name, linked, round(lat, 4), round(lng, 4), now,
                         int(self.provider.simulated), dumps(vehicle), dumps(hist)))
        if self.provider.simulated:
            self.svc.app.notify(patient_id, "ambulance", "DEMO ambulance simulation started",
                                "This is a demonstration only. No ambulance was sent. Call 112 in a real emergency.", rid)
        out = self._view(self._row(rid))
        self.svc.hub.publish(patient_id, {"type": "ambulance", "request": out, "simulated": out["simulated"]})
        return out

    def get(self, p: Principal, request_id: str) -> dict:
        r = self._row(request_id)
        self.svc.auth.require_access(p, r["patient_id"])
        return self._view(r)

    def active(self, p: Principal, patient_id: str) -> dict | None:
        self.svc.auth.require_access(p, patient_id)
        r = self.db.one("SELECT * FROM ambulance_requests WHERE patient_id=? AND cancelled_at IS NULL ORDER BY created_at DESC LIMIT 1",
                        (patient_id,))
        if not r:
            return None
        v = self._view(r)
        return v if v["status"] not in ("COMPLETED", "CANCELLED") else None

    def cancel(self, p: Principal, request_id: str) -> dict:
        r = self._row(request_id)
        self.svc.auth.require_access(p, r["patient_id"])
        if r["cancelled_at"]:
            raise ValueError("This request is already cancelled.")
        now = iso(utcnow())
        hist = loads(r["history_json"], []) + [{"event": "CANCELLED", "at": now, "by": p.id, "by_role": p.role}]
        self.db.execute("UPDATE ambulance_requests SET cancelled_at=?, lat=NULL, lng=NULL, history_json=? WHERE request_id=?",
                        (now, dumps(hist), request_id))     # the location is not kept once the request ends
        out = self._view(self._row(request_id))
        self.svc.hub.publish(r["patient_id"], {"type": "ambulance", "request": out, "simulated": out["simulated"]})
        return out
