# The one reading format shared by the virtual watch simulator, the Wear OS watch, the phone and an ESP32 sensor:
#   {patient_id, timestamp, type, value, source}  (+ optional quality, unit, meta)
# Only hr, spo2 and resp (and the rhythm label) are model inputs. The other types are stored, used for the personal
# baseline and the safety layer (fall, rhythm, QTc, dose), and shown in the apps, but they never change the risk score.
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ReadingType(str, Enum):
    hr = "hr"                    # heart rate, bpm
    hrv_rmssd = "hrv_rmssd"      # heart-rate variability (RMSSD), ms
    spo2 = "spo2"                # oxygen saturation, %
    resp = "resp"                # breathing rate, breaths/min
    steps = "steps"              # step count since the previous reading
    sleep_stage = "sleep_stage"  # awake | light | deep | rem
    ecg_qtc = "ecg_qtc"          # corrected QT interval from a single-lead ECG, ms
    fall = "fall"                # 1 = fall detected by the watch
    dose_taken = "dose_taken"    # mg taken (meta.drug names the drug), or 1 = dose taken
    rhythm = "rhythm"            # rhythm label from the watch ECG / irregular-rhythm check: sinus, af_flutter, tachy, brady, paced, other


class Source(str, Enum):
    simulator = "simulator"
    watch = "watch"
    esp32 = "esp32"
    phone = "phone"


UNITS = {"hr": "bpm", "hrv_rmssd": "ms", "spo2": "%", "resp": "breaths/min", "steps": "steps", "ecg_qtc": "ms",
         "fall": "flag", "dose_taken": "mg"}
SLEEP_STAGES = ("awake", "light", "deep", "rem")
RHYTHMS = ("sinus", "af_flutter", "tachy", "brady", "paced", "other", "unknown")
# Hard limits: values outside are rejected at the door (the engine applies its own training limits again).
LIMITS = {"hr": (20, 250), "hrv_rmssd": (1, 500), "spo2": (50, 100), "resp": (4, 60), "steps": (0, 100000),
          "ecg_qtc": (200, 800), "fall": (0, 1), "dose_taken": (0, 100000)}
ENGINE_SIGNAL = {"hr": "hr", "spo2": "spo2", "resp": "rr", "rhythm": "rhythm"}     # reading type -> risk engine signal
ENGINE_UNIT = {"hr": "bpm", "spo2": "%", "rr": "breaths/min"}


class Reading(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)
    patient_id: str = Field(min_length=1, max_length=64)
    timestamp: datetime
    type: ReadingType
    value: Union[float, str]
    source: Source
    quality: Optional[float] = Field(default=None, ge=0, le=1)
    unit: Optional[str] = None
    meta: Optional[dict[str, Any]] = None

    @field_validator("timestamp")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        if v.tzinfo is None:                       # naive timestamps are taken as UTC, as in the risk engine
            v = v.replace(tzinfo=timezone.utc)
        return v.astimezone(timezone.utc)

    @model_validator(mode="after")
    def _check_value(self):
        t = self.type
        if t in ("sleep_stage", "rhythm"):
            allowed = SLEEP_STAGES if t == "sleep_stage" else RHYTHMS
            if not isinstance(self.value, str) or self.value not in allowed:
                raise ValueError(f"{t} must be one of {allowed}")
        else:
            try:
                self.value = float(self.value)
            except (TypeError, ValueError):
                raise ValueError(f"{t} needs a number")
            lo, hi = LIMITS[t]
            if not lo <= self.value <= hi:
                raise ValueError(f"{t}={self.value} is outside the possible range {lo}-{hi}")
        if self.unit is None:
            self.unit = UNITS.get(t)
        return self

    @property
    def is_simulated(self) -> bool:
        return self.source == "simulator" or bool((self.meta or {}).get("simulated"))


def to_engine(row: dict) -> dict | None:
    """A stored reading row -> the risk engine's wearable reading, or None when the type is not a model input."""
    sig = ENGINE_SIGNAL.get(row["type"])
    if sig is None:
        return None
    ts = row["ts"] if isinstance(row["ts"], str) else row["ts"].isoformat()
    out = {"timestamp": ts, "signal": sig, "source": row["source"]}
    if sig == "rhythm":
        out["value"] = row["value_text"]
    else:
        out.update(value=row["value_num"], unit=ENGINE_UNIT[sig])
        if row.get("quality") is not None:
            out["quality"] = row["quality"]
    return out


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
