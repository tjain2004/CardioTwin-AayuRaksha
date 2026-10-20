#!/usr/bin/env python3
"""Records what the REAL CardioTwin backend sends to a paired watch, for the Wear OS app's offline demo
(app/src/main/assets/mock/server_replies.json, played back by OfflineMockLink).

It pairs a simulator device, streams the same mock replay the app uses (backend/simulator/watch_sim.py, seed 0) at the
app's default speed, and keeps: every distinct `status` (with the app batch number it arrived after), the first
possible-AF alert, every other alert during the normal replay (with its batch number), the fall check, and the
emergency after an unanswered fall.

    cd backend && python -m cardiotwin_backend --port 8000          # in one terminal
    python wear_os_watch/tools/record_mock_replies.py --server http://127.0.0.1:8000 --backend backend
"""
import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("--server", default="http://127.0.0.1:8000")
ap.add_argument("--backend", default=str(Path(__file__).resolve().parents[2] / "backend"))
ap.add_argument("--stay", type=int, default=37093652)
ap.add_argument("--steps", type=int, default=240, help="normal 5-minute steps to record statuses for")
ap.add_argument("--step-seconds", type=float, default=0.83, help="real seconds per step (app default: 6 sim min/s)")
ap.add_argument("--out", default=str(Path(__file__).resolve().parents[1] / "app/src/main/assets/mock/server_replies.json"))
a = ap.parse_args()
sys.path.insert(0, a.backend)
from simulator.watch_sim import WatchSimulator, load_replay, pair_with_server  # noqa: E402
import websockets  # noqa: E402


async def main():
    pid = f"stay-{a.stay}"
    dev = pair_with_server(a.server, "dr.demo", "demo1234", pid)
    sim = WatchSimulator(load_replay(a.stay), pid, backfill_hours=12, seed=0)
    url = a.server.replace("http", "ws", 1) + f"/ws/device?token={dev['device_token']}"
    rec = {"statuses": [], "alerts": [], "af_alert": None, "fall_alert": None, "fall_emergency": None}
    batch = 0
    seen_status = None
    async with websockets.connect(url, max_size=2 ** 22) as ws:
        async def listen():
            nonlocal seen_status
            async for raw in ws:
                m = json.loads(raw)
                t = m.get("type")
                if t == "status" and phase == "normal":
                    key = json.dumps(m.get("views", {}).get("watch", {}) | {"evaluated_at": None}, sort_keys=True)
                    if key != seen_status:
                        seen_status = key
                        rec["statuses"].append({"after_batch": batch, "message": m})
                        w = m["views"]["watch"]
                        print(f"  status after batch {batch}: {w.get('title')} / {w.get('text')} tier={m.get('tier')}")
                elif t in ("alert", "emergency"):
                    al = m["alert"]
                    print(f"  {t} {al['kind']} {al['state']}: {al['title']}")
                    if phase == "normal" and al["kind"] != "FALL":
                        rec["alerts"].append({"after_batch": batch, "message": m})
                    if al["kind"] == "RHYTHM_AF" and rec["af_alert"] is None:
                        rec["af_alert"] = m
                    elif al["kind"] == "FALL" and al["state"] == "AWAITING_RESPONSE":
                        rec["fall_alert"] = m
                    elif al["kind"] == "FALL" and t == "emergency":
                        rec["fall_emergency"] = m

        phase = "normal"
        task = asyncio.create_task(listen())
        back = sim.backfill()
        for i in range(0, len(back), 500):
            await ws.send(json.dumps({"type": "readings", "readings": back[i:i + 500]}))
        batch = 1                                     # the app sends the whole backfill as its first batch
        await asyncio.sleep(4)
        for _ in range(a.steps):
            await ws.send(json.dumps({"type": "readings", "readings": sim.next()}))
            batch += 1
            await asyncio.sleep(a.step_seconds)
        await asyncio.sleep(4)
        phase = "events"
        sim.trigger_af(60)
        for _ in range(12):
            await ws.send(json.dumps({"type": "readings", "readings": sim.next()}))
            await asyncio.sleep(a.step_seconds)
        await ws.send(json.dumps({"type": "readings", "readings": sim.trigger_fall()}))
        deadline = time.time() + 45
        while rec["fall_emergency"] is None and time.time() < deadline:
            await asyncio.sleep(1)
        task.cancel()
    rec.update(label="SIMULATED", recorded_from=f"CardioTwin backend, simulator device, stay {a.stay}",
               recorded_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    missing = [k for k in ("af_alert", "fall_alert", "fall_emergency") if rec[k] is None]
    Path(a.out).write_text(json.dumps(rec, indent=1))
    print(f"wrote {a.out}: {len(rec['statuses'])} statuses, {len(rec['alerts'])} alerts; missing: {missing or 'none'}")


asyncio.run(main())
