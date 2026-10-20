# Shared fixtures: one backend service (the real saved model and risk engine, nothing mocked) on a temporary database.
# Each test uses its own demo patient so tests stay independent. Readings come from the virtual watch simulator and are
# labelled SIMULATED.
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from cardiotwin_backend.app import create_app  # noqa: E402
from cardiotwin_backend.config import Settings  # noqa: E402
from cardiotwin_backend.service import CardioTwinService  # noqa: E402

PW = "demo1234"


@pytest.fixture(scope="session")
def service(tmp_path_factory):
    d = tmp_path_factory.mktemp("cardiotwin")
    s = Settings(data_dir=d, run_loop=False, fall_response_seconds=1, demo_password=PW)
    return CardioTwinService(s)


@pytest.fixture(scope="session")
def client(service):
    with TestClient(create_app(service=service)) as c:
        yield c


def login(client, user, pw=PW):
    r = client.post("/api/auth/login", json={"username": user, "password": pw})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture(scope="session")
def doc(client):
    return login(client, "dr.demo")


def pair_device(client, headers, patient_id, kind="watch", source="simulator"):
    code = client.post(f"/api/patients/{patient_id}/pairing", headers=headers).json()["pairing_code"]
    r = client.post("/api/devices/pair", json={"code": code, "kind": kind, "source": source, "name": "test device"})
    assert r.status_code == 200, r.text
    return r.json()


def stream(client, device, stay_id, backfill_hours=12, steps=0, seed=0):
    """Send a backfill (and optionally live steps) from the virtual watch; returns the simulator."""
    from simulator.watch_sim import WatchSimulator, load_replay
    sim = WatchSimulator(load_replay(stay_id), f"stay-{stay_id}", backfill_hours=backfill_hours, seed=seed)
    h = {"Authorization": f"Bearer {device['device_token']}"}
    r = client.post("/api/readings", json={"readings": sim.backfill()}, headers=h)
    assert r.status_code == 200, r.text
    for _ in range(steps):
        client.post("/api/readings", json={"readings": sim.next()}, headers=h)
    return sim
