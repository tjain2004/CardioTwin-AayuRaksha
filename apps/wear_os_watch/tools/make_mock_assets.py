#!/usr/bin/env python3
"""Builds the Wear OS app's mock replay (app/src/main/assets/mock/replay_stay_<id>.json) from the backend's own virtual
watch (backend/simulator/watch_sim.py), so the watch's mock mode sends exactly what the backend simulator sends.

Compact format: one object per 5-minute step, offset "o" in minutes from the live start (negative = backfill),
with hr, spo2, resp (charted vitals, interpolated + noise), and the SYNTHETIC hrv, steps, sleep, rhythm, qtc.

    python tools/make_mock_assets.py --backend ../backend --stay 37093652 --hours 48
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("--backend", default=str(Path(__file__).resolve().parents[2] / "backend"))
ap.add_argument("--stay", type=int, default=37093652)
ap.add_argument("--hours", type=int, default=48, help="live hours to keep after the backfill (the app loops them)")
ap.add_argument("--backfill-hours", type=int, default=12)
ap.add_argument("--out", default=str(Path(__file__).resolve().parents[1] / "app/src/main/assets/mock"))
a = ap.parse_args()

sys.path.insert(0, a.backend)
from simulator.watch_sim import WatchSimulator, load_replay  # noqa: E402

replay = load_replay(a.stay)
t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)          # fixed so the file is reproducible; the app re-stamps times
sim = WatchSimulator(replay, f"stay-{a.stay}", backfill_hours=a.backfill_hours, seed=0, t0=t0)
live_start = sim.sim_now
KEY = {"hr": "hr", "spo2": "spo2", "resp": "resp", "hrv_rmssd": "hrv", "steps": "steps", "sleep_stage": "sleep",
       "rhythm": "rhythm", "ecg_qtc": "qtc"}


def steps_from(readings):
    by_t = {}
    for r in readings:
        by_t.setdefault(r["timestamp"], {})[KEY[r["type"]]] = r["value"]
    out = []
    for ts in sorted(by_t):
        t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        out.append({"o": int((t - live_start).total_seconds() // 60), **by_t[ts]})
    return out


steps = steps_from(sim.backfill())
for _ in range(a.hours * 12):
    batch = sim.next()
    if not batch:
        break
    steps += steps_from(batch)

idx = {s["stay_id"]: s for s in json.loads((Path(a.backend) / "data/replay/index.json").read_text())}
info = idx.get(a.stay, {})
doc = {
    "label": "SIMULATED",
    "stay_id": a.stay,
    "title": f"test stay {a.stay}",
    "basis": sim.basis,
    "synthetic": ["hrv_rmssd", "steps", "sleep_stage", "ecg_qtc"],
    "made_by": "wear_os_watch/tools/make_mock_assets.py from backend/simulator/watch_sim.py (seed 0)",
    "step_minutes": 5,
    "backfill_hours": sim.start_offset_h,
    "index_entry": info,
    "steps": steps,
}
out = Path(a.out)
out.mkdir(parents=True, exist_ok=True)
p = out / f"replay_stay_{a.stay}.json"
p.write_text(json.dumps(doc, separators=(",", ":")))
print(f"{p}: {len(steps)} steps ({sum(1 for s in steps if s['o'] < 0)} backfill), {p.stat().st_size // 1024} KB")
