# Backend settings, from environment variables (CARDIOTWIN_*) with demo defaults.
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]


def _env(name: str, default):
    v = os.environ.get(f"CARDIOTWIN_{name}")
    if v is None:
        return default
    if isinstance(default, bool):
        return v.lower() in ("1", "true", "yes", "on")
    return type(default)(v)


@dataclass
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(_env("DATA_DIR", str(BACKEND_DIR / "var"))))
    db_path: str | None = None                       # default <data_dir>/cardiotwin.db; ":memory:" for tests
    project_root: str | None = field(default_factory=lambda: os.environ.get("CARDIOTWIN_PROJECT_ROOT"))
    replay_dir: Path = BACKEND_DIR / "data" / "replay"
    public_url: str = field(default_factory=lambda: _env("PUBLIC_URL", "http://127.0.0.1:8000"))
    seed_demo: bool = field(default_factory=lambda: _env("SEED_DEMO", True))
    demo_password: str = field(default_factory=lambda: _env("DEMO_PASSWORD", "demo1234"))
    eval_interval_s: float = field(default_factory=lambda: _env("EVAL_INTERVAL_S", 2.0))
    run_loop: bool = True                            # background evaluation loop (tests drive evaluation directly)
    history_days: int = 7                            # readings sent to the risk engine and the baseline
    future_skew_minutes: int = 5                     # real-clock devices: timestamps further ahead are rejected
    fall_response_seconds: int = field(default_factory=lambda: _env("FALL_RESPONSE_SECONDS", 30))
    store_audit_snapshots: bool = field(default_factory=lambda: _env("AUDIT_SNAPSHOTS", False))
    keep_evaluations: int = 500                      # per patient
    clinician_sees_all: bool = True                  # demo: every clinician sees every patient
    load_whatif_twin: bool = True
    cors_origins: tuple[str, ...] = ("*",)
    # demo clinic for the patient app's appointment booking (wall-clock times are stored in this offset, e.g. India +05:30)
    clinic_name: str = field(default_factory=lambda: _env("CLINIC_NAME", "CardioTwin Demo Heart Clinic"))
    clinic_utc_offset: str = field(default_factory=lambda: _env("CLINIC_UTC_OFFSET", "+05:30"))
    # AI health assistant (chatbot.py). The Anthropic key stays on the server (ANTHROPIC_API_KEY); with no key, or with
    # CARDIOTWIN_CHAT_PROVIDER=offline, a rule-based assistant answers and every reply is labelled OFFLINE.
    chat_provider: str = field(default_factory=lambda: _env("CHAT_PROVIDER", "auto"))          # auto | anthropic | offline
    chat_model: str = field(default_factory=lambda: _env("CHAT_MODEL", "claude-opus-5-5"))
    chat_effort: str = field(default_factory=lambda: _env("CHAT_EFFORT", "low"))
    chat_timeout_s: float = field(default_factory=lambda: _env("CHAT_TIMEOUT_S", 45.0))
    chat_rate_per_minute: int = field(default_factory=lambda: _env("CHAT_RATE_PER_MINUTE", 12))
    # Nearby hospitals and ambulance (emergency_map.py). The browser key is a referrer-restricted Maps JavaScript key
    # (Google designs it to be visible in the page); the server key (Places API (New), Routes API) never leaves the backend.
    google_maps_browser_key: str = field(default_factory=lambda: _env("GOOGLE_MAPS_BROWSER_KEY", ""))
    google_maps_server_key: str = field(default_factory=lambda: _env("GOOGLE_MAPS_SERVER_KEY", ""))
    ambulance_provider: str = field(default_factory=lambda: _env("AMBULANCE_PROVIDER", "demo"))  # demo | none
    country_code: str = field(default_factory=lambda: _env("COUNTRY_CODE", "IN"))

    @property
    def database(self) -> str:
        return self.db_path or str(Path(self.data_dir) / "cardiotwin.db")
