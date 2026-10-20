# Request and response schemas for the CardioTwin risk engine (pydantic v2). The response is what the phone app
# (patient and doctor mode) and the Wear OS watch render; it is JSON-serialisable with RiskResult.model_dump(mode="json").
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = "risk-result-1.0"


# ---------------------------------------------------------------- enums
class Status(str, Enum):
    OK = "OK"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    MODEL_ERROR = "MODEL_ERROR"


class Tier(str, Enum):
    LOW = "LOW"
    MODERATE = "MODERATE"
    HIGH = "HIGH"


class EmergencyState(str, Enum):
    NOT_TRIGGERED = "NOT_TRIGGERED"
    TRIGGERED = "TRIGGERED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    NOT_ASSESSABLE = "NOT_ASSESSABLE"


class SignalStatus(str, Enum):
    FRESH = "FRESH"
    STALE = "STALE"
    MISSING = "MISSING"
    INVALID = "INVALID"


class BaselineStatus(str, Enum):
    AVAILABLE = "AVAILABLE"
    UNRELIABLE = "UNRELIABLE"
    UNAVAILABLE = "UNAVAILABLE"


class ExplanationStatus(str, Enum):
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"
    NOT_RUN = "NOT_RUN"


class AlertState(str, Enum):
    NEW = "NEW"
    DISPLAYED = "DISPLAYED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    ESCALATED = "ESCALATED"
    RESOLVED = "RESOLVED"


# ---------------------------------------------------------------- request
class WearableReading(BaseModel):
    """One timestamped watch measurement. signal: hr | spo2 | rr | rhythm."""
    model_config = ConfigDict(extra="ignore")
    timestamp: Any
    signal: str
    value: Any
    unit: Optional[str] = None
    source: Optional[str] = None            # e.g. "galaxy-watch-6", "replay: MIMIC-IV ICU charted vitals"
    quality: Optional[float] = None          # 0-1 signal quality from the device, when it reports one
    device_id: Optional[str] = None


class ClinicalReading(BaseModel):
    """One EHR / clinic measurement, e.g. ldl, hba1c, egfr, sbp_outpatient."""
    model_config = ConfigDict(extra="ignore")
    name: str
    value: Any
    unit: Optional[str] = None
    timestamp: Optional[Any] = None
    source: Optional[str] = None


class PatientProfile(BaseModel):
    """Entered once in the phone app or taken from the EHR. Unknown values stay None (never 0)."""
    model_config = ConfigDict(extra="ignore")
    patient_id: str
    age_at_admit: Optional[float] = None
    gender_male: Optional[int] = None
    height_in: Optional[float] = None
    weight_lbs: Optional[float] = None
    bmi: Optional[float] = None
    smoker: Optional[int] = None
    log_prior_admissions: Optional[float] = None
    days_since_last_discharge: Optional[float] = None   # -1 = no earlier stay on record (training convention)
    n_active_drugs: Optional[float] = None
    ix_bleeding: Optional[int] = None
    has_bled_partial: Optional[float] = None
    ascvd_10y_risk: Optional[float] = None
    hist_af: Optional[int] = None
    reported_symptoms: Optional[dict[str, Any]] = None  # from the phone symptom check, with "timestamp"
    is_demo: bool = False                                # True for replayed or synthetic data


# ---------------------------------------------------------------- response parts
class ModelInfo(BaseModel):
    name: Optional[str] = None
    version: Optional[str] = None
    sha256: Optional[str] = None
    target: Optional[str] = None
    horizon: Optional[str] = None
    population: Optional[str] = None
    calibration_status: Literal["calibrated", "uncalibrated", "unavailable"] = "unavailable"
    calibration_method: Optional[str] = None
    calibration_note: Optional[str] = None
    calibration_version: Optional[str] = None


class RiskBlock(BaseModel):
    raw_output: Optional[float] = None
    raw_output_meaning: Optional[str] = None
    calibrated_probability: Optional[float] = None
    score_scale: Optional[str] = None
    tier: Optional[Tier] = None
    thresholds: Optional[dict[str, float]] = None
    tier_reason: Optional[str] = None
    policy_version: Optional[str] = None
    provisional_policy: bool = True
    times_average_risk: Optional[float] = None
    uncertainty: Optional[dict[str, Any]] = None
    trajectory: list[dict[str, Any]] = Field(default_factory=list)
    trajectory_summary: Optional[dict[str, Any]] = None


class SignalFreshness(BaseModel):
    status: SignalStatus
    last_valid_reading: Optional[str] = None
    age_minutes: Optional[float] = None
    max_age_minutes: Optional[float] = None
    mandatory: bool = False
    coverage_pct_24h: Optional[float] = None
    source: Optional[str] = None
    mean_quality: Optional[float] = None
    largest_gap_minutes: Optional[float] = None


class DataQuality(BaseModel):
    status: Literal["SUFFICIENT", "INSUFFICIENT"]
    reason_code: Optional[str] = None
    reason: Optional[str] = None
    coverage_pct: Optional[float] = None
    quality_weighted_coverage_pct: Optional[float] = None
    freshness_by_signal: dict[str, SignalFreshness] = Field(default_factory=dict)
    last_wearable_sync: Optional[str] = None
    minutes_since_last_sync: Optional[float] = None
    history_hours: Optional[float] = None
    missing_fields: list[str] = Field(default_factory=list)
    stale_fields: list[str] = Field(default_factory=list)
    invalid_fields: list[str] = Field(default_factory=list)
    imputed_fields: list[dict[str, Any]] = Field(default_factory=list)
    out_of_distribution: list[dict[str, Any]] = Field(default_factory=list)
    flags_for_remeasurement: list[dict[str, Any]] = Field(default_factory=list)
    validation_counts: dict[str, int] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class BaselineBlock(BaseModel):
    status: BaselineStatus = BaselineStatus.UNAVAILABLE
    window: Optional[dict[str, Any]] = None
    confidence: Optional[str] = None
    by_signal: dict[str, Any] = Field(default_factory=dict)
    deviations: list[dict[str, Any]] = Field(default_factory=list)
    trends: dict[str, Any] = Field(default_factory=dict)
    corroboration: Optional[dict[str, Any]] = None


class ExplanationBlock(BaseModel):
    status: ExplanationStatus = ExplanationStatus.NOT_RUN
    reason: Optional[str] = None
    method: Optional[str] = None
    output_space: Optional[str] = None
    baseline_output: Optional[float] = None
    explained_output: Optional[float] = None
    additivity_error: Optional[float] = None
    top_contributors: list[dict[str, Any]] = Field(default_factory=list)
    top_features: list[dict[str, Any]] = Field(default_factory=list)
    waterfall: Optional[dict[str, Any]] = None
    shap_values: Optional[dict[str, float]] = None
    version: Optional[str] = None
    caveat: str = "SHAP shows how the model arrived at its output. It is not proof that any input caused a medical problem."


class EmergencyBlock(BaseModel):
    state: EmergencyState = EmergencyState.NOT_ASSESSABLE
    rule_id: Optional[str] = None
    rules_triggered: list[dict[str, Any]] = Field(default_factory=list)
    reason: Optional[str] = None
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    guidance: list[str] = Field(default_factory=list)
    call_action_available: bool = False
    call_action: Optional[dict[str, Any]] = None     # emergency-number call: only when level is "critical"
    care_action: Optional[dict[str, Any]] = None     # "Contact your doctor": for the review level
    level: Optional[str] = None                      # "critical" (TRIGGERED), "review" (REVIEW_REQUIRED) or None
    modal: Optional[dict[str, Any]] = None
    rules_version: Optional[str] = None
    review_status: Optional[str] = None
    independent_of_model: bool = True


class SarSentence(BaseModel):
    text: str
    evidence: list[dict[str, Any]] = Field(default_factory=list)


class SarBlock(BaseModel):
    one_line: str = ""
    situation: str = ""
    assessment: str = ""
    recommendation: str = ""
    sentences: dict[str, list[SarSentence]] = Field(default_factory=dict)
    limitations: list[str] = Field(default_factory=list)
    template_version: Optional[str] = None
    guard_passed: Optional[bool] = None


class AuditBlock(BaseModel):
    audit_id: Optional[str] = None
    policy_version: Optional[str] = None
    input_reference: Optional[str] = None
    alert_reference: Optional[str] = None
    result_sha256: Optional[str] = None
    snapshot_stored: bool = False


class RiskResult(BaseModel):
    schema_version: str = SCHEMA_VERSION
    status: Status
    status_reason_code: Optional[str] = None
    status_reason: Optional[str] = None
    patient_reference: str
    evaluated_at: datetime
    is_demo_data: bool = False
    simulation: bool = False
    model: ModelInfo
    risk: RiskBlock
    data_quality: DataQuality
    baseline: BaselineBlock
    explanation: ExplanationBlock
    emergency: EmergencyBlock
    sar: SarBlock
    policy_breakdown: dict[str, Any] = Field(default_factory=dict)
    next_actions: list[str] = Field(default_factory=list)
    alert: dict[str, Any] = Field(default_factory=dict)
    audit: AuditBlock = Field(default_factory=AuditBlock)
    degraded_components: list[str] = Field(default_factory=list)
    disclaimer: str = ("Research prototype trained on ICU data from 100 patients (MIMIC-IV demo). Not a medical device and not "
                       "validated for clinical use. A LOW result never means 'safe'.")
