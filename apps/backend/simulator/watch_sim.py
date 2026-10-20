#!/usr/bin/env python3
# CardioTwin VIRTUAL WATCH SIMULATOR. Every reading it sends is labelled SIMULATED (source "simulator", meta.simulated).
#
# It replays a held-out test patient (MIMIC-IV demo ICU charted vitals, hourly) as a live watch stream: one reading of
# each signal every 5 simulated minutes, interpolated between the charted hours with small sensor noise. Hours with no
# charted value send no reading, as in the data. HRV, steps, sleep stage and QTc are SYNTHETIC (the data has none).
# Controls: "af" starts a simulated atrial-fibrillation episode, "fall" sends a simulated fall, "ok"/"help" answer the
# watch's fall check, "stop-af" ends the episode.
#
#   python simulator/watch_sim.py --server http://127.0.0.1:8000 --stay 37093652 --user dr.demo --password demo1234
#   python simulator/watch_sim.py --offline --stay 37093652 --steps 5          # print readings, no server
from __future__ import annotations

import argparse
import asyncio
import json
import math
import random
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPLAY_DIR = Path(__file__).resolve().parents[1] / "data" / "replay"
LABEL = "SIMULATED"
NOISE = {"hr": 1.5, "spo2": 0.4, "rr": 0.8}
LIMITS = {"hr": (20, 250), "spo2": (50, 100), "resp": (4, 60), "hrv_rmssd": (1, 500), "ecg_qtc": (200, 800)}


def load_replay(stay_id: int | str, replay_dir: Path = REPLAY_DIR) -> dict:
    return json.loads((Path(replay_dir) / f"stay_{int(stay_id)}.json").read_text())


def available_stays(replay_dir: Path = REPLAY_DIR) -> list[dict]:
    return json.loads((Path(replay_dir) / "index.json").read_text())


def _iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


class WatchSimulator:
    """Generates SIMULATED readings in the backend's reading format. Pure and deterministic for a given seed; the
    caller decides how fast to call step()."""

    def __init__(self, replay: dict, patient_id: str, step_minutes: int = 5, backfill_hours: int = 12,
                 seed: int = 0, t0: datetime | None = None, loop: bool = False):
        self.replay, self.patient_id = replay, patient_id
        self.hours = {h["offset_h"]: h for h in replay["hours"]}
        self.last_offset = max(self.hours)
        self.step = timedelta(minutes=step_minutes)
        self.start_offset_h = max(0, min(backfill_hours, self.last_offset - 1))
        now = (t0 or datetime.now(timezone.utc)).replace(second=0, microsecond=0)
        now -= timedelta(minutes=now.minute % step_minutes)            # align steps so :00, :15 and :30 readings fall on a step
        self.t0 = now - timedelta(hours=self.start_offset_h)           # simulated time of replay offset 0
        self.sim_now = now
        self.rng = random.Random(seed)
        self.loop = loop
        self.af_until: datetime | None = None
        self.still_until: datetime | None = None
        self.finished = False
        self.events: list[dict] = []
        self.basis = f"replay of MIMIC-IV demo test stay {replay['stay_id']} (ICU charted vitals, not real watch data)"

    # ---------------------------------------------------------------- controls
    def trigger_af(self, minutes: int = 60) -> dict:
        self.af_until = self.sim_now + timedelta(minutes=minutes)
        ev = {"event": "af_started", "sim_time": _iso(self.sim_now), "until": _iso(self.af_until), "label": LABEL}
        self.events.append(ev)
        return ev

    def stop_af(self) -> dict:
        self.af_until = None
        ev = {"event": "af_stopped", "sim_time": _iso(self.sim_now), "label": LABEL}
        self.events.append(ev)
        return ev

    def trigger_fall(self) -> list[dict]:
        self.still_until = self.sim_now + timedelta(minutes=30)
        self.events.append({"event": "fall", "sim_time": _iso(self.sim_now), "label": LABEL})
        return [self._reading(self.sim_now, "fall", 1.0, extra={"impact_g": round(self.rng.uniform(2.8, 4.2), 1)})]

    @property
    def in_af(self) -> bool:
        return self.af_until is not None and self.sim_now < self.af_until

    @property
    def offset_h(self) -> float:
        return (self.sim_now - self.t0) / timedelta(hours=1)

    def status(self) -> dict:
        return {"label": LABEL, "patient_id": self.patient_id, "stay_id": self.replay["stay_id"], "sim_time": _iso(self.sim_now),
                "replay_hour": round(self.offset_h, 2), "replay_last_hour": self.last_offset, "in_af": self.in_af,
                "finished": self.finished, "basis": self.basis}

    # ---------------------------------------------------------------- generation
    def _reading(self, t: datetime, typ: str, value, quality: float | None = 0.95, synthetic: bool = False,
                 extra: dict | None = None) -> dict:
        meta = {"simulated": True, "label": LABEL, "basis": "synthetic" if synthetic else self.basis, **(extra or {})}
        r = {"patient_id": self.patient_id, "timestamp": _iso(t), "type": typ, "value": value, "source": "simulator", "meta": meta}
        if quality is not None and typ in ("hr", "spo2", "resp", "hrv_rmssd"):
            r["quality"] = quality
        return r

    def _charted(self, sig: str, offset: float) -> float | None:
        h = int(math.floor(offset))
        cur = self.hours.get(h, {}).get(sig)
        if cur is None:
            return None
        nxt = self.hours.get(h + 1, {}).get(sig)
        f = offset - h
        return cur if nxt is None else cur + (nxt - cur) * f

    def _clip(self, typ: str, v: float, nd: int = 1) -> float:
        lo, hi = LIMITS[typ]
        return round(min(max(v, lo), hi), nd)

    def readings_at(self, t: datetime) -> list[dict]:
        off = (t - self.t0) / timedelta(hours=1)
        out: list[dict] = []
        af = self.af_until is not None and t < self.af_until
        hr = self._charted("hr", off)
        if af:
            base = hr if hr is not None else 90.0
            hr = max(base + self.rng.uniform(35, 55), 132 + self.rng.uniform(0, 20))   # rapid AF: always in the NEWS2 red band
            out.append(self._reading(t, "hr", self._clip("hr", hr), extra={"episode": "simulated AF"}))
        elif hr is not None:
            out.append(self._reading(t, "hr", self._clip("hr", hr + self.rng.gauss(0, NOISE["hr"]))))
        sp = self._charted("spo2", off)
        if sp is not None:
            out.append(self._reading(t, "spo2", self._clip("spo2", sp + self.rng.gauss(0, NOISE["spo2"]))))
        rr = self._charted("rr", off)
        if rr is not None:
            out.append(self._reading(t, "resp", self._clip("resp", rr + self.rng.gauss(0, NOISE["rr"]))))
        # synthetic signals the ICU data does not have
        ref_hr = hr if hr is not None else 80.0
        hrv = self.rng.uniform(90, 180) if af else 42 - 0.3 * (ref_hr - 70) + self.rng.gauss(0, 4)
        out.append(self._reading(t, "hrv_rmssd", self._clip("hrv_rmssd", max(hrv, 5)), synthetic=True))
        local_h = (t.hour + 5.5) % 24                                       # IST clock for the daily pattern
        still = self.still_until is not None and t < self.still_until
        steps = 0 if (still or local_h < 7 or local_h >= 22) else max(0, int(self.rng.gauss(25, 15)))
        out.append(self._reading(t, "steps", steps, quality=None, synthetic=True))
        if t.minute % 30 == 0:
            stage = "awake" if 7 <= local_h < 22 else self.rng.choice(["light", "light", "deep", "rem"])
            out.append(self._reading(t, "sleep_stage", stage, quality=None, synthetic=True))
        if t.minute % 15 == 0:
            rh = "af_flutter" if af else self.hours.get(int(math.floor(off)), {}).get("rhythm")
            if rh:
                out.append(self._reading(t, "rhythm", rh, quality=None, synthetic=af,
                                         extra={"episode": "simulated AF"} if af else None))
        if t.minute == 0:
            qtc = 440 + self.rng.gauss(0, 12) + (15 if af else 0)
            out.append(self._reading(t, "ecg_qtc", self._clip("ecg_qtc", qtc, 0), quality=None, synthetic=True))
        return out

    def backfill(self) -> list[dict]:
        """Readings for replay hours before the live start, so the risk engine has history from the first update."""
        out, t = [], self.t0
        while t < self.sim_now:
            out += self.readings_at(t)
            t += self.step
        return out

    def next(self) -> list[dict]:
        """Readings for the next step of simulated time."""
        if self.offset_h > self.last_offset + 1:
            if not self.loop:
                self.finished = True
                return []
            self.t0 = self.sim_now - timedelta(hours=self.start_offset_h)
        out = self.readings_at(self.sim_now)
        self.sim_now += self.step
        return out


# ---------------------------------------------------------------------- command-line client
def _post(server: str, path: str, body: dict, token: str | None = None) -> dict:
    req = urllib.request.Request(server.rstrip("/") + path, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", **({"Authorization": f"Bearer {token}"} if token else {})})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def pair_with_server(server: str, user: str, password: str, patient_id: str) -> dict:
    tok = _post(server, "/api/auth/login", {"username": user, "password": password})["access_token"]
    code = _post(server, f"/api/patients/{patient_id}/pairing", {}, tok)["pairing_code"]
    return _post(server, "/api/devices/pair", {"code": code, "kind": "watch", "source": "simulator", "name": "Virtual watch (SIMULATED)"})


async def run_client(args) -> None:
    import websockets
    replay = load_replay(args.stay)
    patient_id = args.patient or f"stay-{args.stay}"
    token = args.device_token
    if not token:
        if args.pairing_code:
            token = _post(args.server, "/api/devices/pair", {"code": args.pairing_code, "kind": "watch", "source": "simulator",
                                                              "name": "Virtual watch (SIMULATED)"})["device_token"]
        else:
            token = pair_with_server(args.server, args.user, args.password, patient_id)["device_token"]
    sim = WatchSimulator(replay, patient_id, backfill_hours=args.backfill_hours, seed=args.seed, loop=args.loop)
    ws_url = args.server.replace("http", "ws", 1).rstrip("/") + f"/ws/device?token={token}"
    print(f"[{LABEL}] virtual watch for {patient_id} ({sim.basis}); speed x{args.speed}")
    print("commands: af | stop-af | fall | ok | help | status | quit")
    async with websockets.connect(ws_url, max_size=2 ** 22) as ws:
        back = sim.backfill()
        for i in range(0, len(back), 500):
            await ws.send(json.dumps({"type": "readings", "readings": back[i:i + 500]}))
        print(f"[{LABEL}] backfilled {len(back)} readings ({sim.start_offset_h} replay hours)")
        state = {"fall_alert": None}

        async def listen():
            async for raw in ws:
                m = json.loads(raw)
                t = m.get("type")
                if t == "status":
                    w = m.get("views", {}).get("watch", {})
                    print(f"  status  {m.get('display_state')}  watch: {w.get('title')} / {w.get('text')}")
                elif t in ("alert", "emergency"):
                    a = m.get("alert", {})
                    if a.get("kind") == "FALL" and a.get("state") == "AWAITING_RESPONSE":
                        state["fall_alert"] = a["alert_id"]
                    print(f"  {t.upper()}  {a.get('kind')} {a.get('state')}: {a.get('title')}")
                elif t == "error":
                    print("  error", m.get("detail"))

        async def commands():
            loop = asyncio.get_running_loop()
            while True:
                line = (await loop.run_in_executor(None, sys.stdin.readline)).strip().lower()
                if line in ("q", "quit", "exit") or line == "" and sys.stdin.closed:
                    raise asyncio.CancelledError
                if line == "af":
                    print(sim.trigger_af(args.af_minutes))
                elif line == "stop-af":
                    print(sim.stop_af())
                elif line == "fall":
                    await ws.send(json.dumps({"type": "readings", "readings": sim.trigger_fall()}))
                    print(f"[{LABEL}] fall sent; answer with 'ok' or 'help'")
                elif line in ("ok", "help") and state["fall_alert"]:
                    await ws.send(json.dumps({"type": "fall_response", "alert_id": state["fall_alert"],
                                              "answer": "im_ok" if line == "ok" else "need_help"}))
                elif line == "status":
                    print(sim.status())

        async def stream():
            while not sim.finished:
                batch = sim.next()
                if batch:
                    await ws.send(json.dumps({"type": "readings", "readings": batch}))
                await asyncio.sleep(sim.step.total_seconds() / args.speed)
            print(f"[{LABEL}] replay finished")

        tasks = [asyncio.create_task(c()) for c in (listen, commands, stream)]
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for t in pending:
            t.cancel()


def main(argv=None):
    ap = argparse.ArgumentParser(description="CardioTwin virtual watch (SIMULATED readings)")
    ap.add_argument("--server", default="http://127.0.0.1:8000")
    ap.add_argument("--stay", type=int, default=37093652, help="test stay to replay (see data/replay/index.json)")
    ap.add_argument("--patient", help="backend patient id (default stay-<stay>)")
    ap.add_argument("--user", default="dr.demo")
    ap.add_argument("--password", default="demo1234")
    ap.add_argument("--pairing-code", help="pair with a code from the app instead of logging in")
    ap.add_argument("--device-token", help="reuse an already paired device token")
    ap.add_argument("--speed", type=float, default=360.0, help="simulated seconds per real second (360 = 1 h per 10 s)")
    ap.add_argument("--backfill-hours", type=int, default=12)
    ap.add_argument("--af-minutes", type=int, default=60)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--loop", action="store_true", help="restart the replay when it ends")
    ap.add_argument("--offline", action="store_true", help="print readings as JSON lines instead of sending them")
    ap.add_argument("--steps", type=int, default=12, help="offline: number of steps to print")
    args = ap.parse_args(argv)
    if args.offline:
        sim = WatchSimulator(load_replay(args.stay), args.patient or f"stay-{args.stay}", backfill_hours=args.backfill_hours, seed=args.seed)
        for _ in range(args.steps):
            for r in sim.next():
                print(json.dumps(r))
        return
    try:
        asyncio.run(run_client(args))
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass


if __name__ == "__main__":
    main()
