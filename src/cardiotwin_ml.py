# CardioTwin training helpers shared by notebook 05 and the saved model files: where each feature comes from
# (watch, phone profile, hospital) and the probability calibrator. Saved .pkl models need this file to load.
from __future__ import annotations

import numpy as np

# ---------------------------------------------------------------- where each feature can come from
WATCH_EXACT = {"af_burden_24h", "af_burden_pct_24h", "af_new_onset", "brady_burden_24h", "tachy_burden_24h",
               "rhythm_changes_24h", "hr_sd_24h", "hr_trend_24h", "rr_night_minus_day_24h", "spo2_dip_hours_24h",
               "spo2_night_dip_hours_24h", "rhythm_af_flutter", "rhythm_brady", "rhythm_sinus", "rhythm_tachy",
               "rhythm_unknown"}
PROFILE_EXACT = {"age_at_admit", "gender_male", "bmi", "bmi_missing", "weight_lbs", "height_in", "log_prior_admissions",
                 "days_since_last_discharge", "smoker", "fam_hx_cad", "hyperlipidemia", "obesity", "prior_mi_or_revasc",
                 "cad_history", "n_active_drugs", "n_qt_drugs", "n_anticoagulant", "n_beta_blocker", "n_loop_diuretic",
                 "ix_bleeding", "ix_qt_combo", "dup_beta_blocker", "cha2ds2_vasc", "has_bled_partial", "egfr",
                 "ascvd_10y_risk", "diabetes", "ckd", "total_chol", "hdl", "ldl", "triglycerides", "non_hdl",
                 "tc_hdl_ratio", "hba1c", "sbp_outpatient", "dbp_outpatient"}
HOME_DEVICE_EXACT = {"sbp", "dbp", "map", "sbp_measured", "temp_c", "temp_c_measured", "pulse_pressure", "weight_kg",
                     "weight_change_72h", "hf_warning_count", "shock_index", "news_score"}
SOURCE_LABEL = {
    "watch": "Smartwatch sensors (HR, SpO2, breathing rate, rhythm / AF check)",
    "phone_profile": "Phone profile or EHR history (entered once, updated by the doctor)",
    "home_device": "Needs a BP cuff, scale or thermometer (not in the phone + watch plan)",
    "hospital_context": "Hospital admission context or race (left out of the watch model)",
    "hospital_only": "Hospital only: blood tests, 12-lead ECG, ICU monitoring",
}


def feature_source(name: str) -> str:
    """Return where a model feature can come from in the phone + watch deployment."""
    if name in WATCH_EXACT or name.split("_")[0] in ("hr", "spo2", "rr") and name != "rr_cv":
        return "watch"
    if name in PROFILE_EXACT or name.startswith(("hist_", "hx_", "on_")):
        return "phone_profile"
    if name in HOME_DEVICE_EXACT or name.startswith(("sbp_", "map_", "shock_index_", "news_score_")):
        return "home_device"
    if name.startswith(("race_", "admission_type_")):
        return "hospital_context"
    return "hospital_only"


# ---------------------------------------------------------------- calibration (picklable)
class Calibrator:
    """Maps a raw model probability to a calibrated probability: 'none', 'sigmoid' (Platt) or 'isotonic'."""

    def __init__(self, method: str = "none"):
        self.method, self.model = method, None

    @staticmethod
    def _logit(p):
        p = np.clip(np.asarray(p, float), 1e-6, 1 - 1e-6)
        return np.log(p / (1 - p)).reshape(-1, 1)

    def fit(self, p_raw, y):
        if self.method == "sigmoid":
            from sklearn.linear_model import LogisticRegression
            self.model = LogisticRegression(C=1e6, max_iter=1000).fit(self._logit(p_raw), y)
        elif self.method == "isotonic":
            from sklearn.isotonic import IsotonicRegression
            self.model = IsotonicRegression(out_of_bounds="clip", y_min=0, y_max=1).fit(np.asarray(p_raw, float), y)
        return self

    def transform(self, p_raw):
        p_raw = np.asarray(p_raw, float)
        if self.method == "sigmoid":
            return self.model.predict_proba(self._logit(p_raw))[:, 1]
        if self.method == "isotonic":
            return self.model.predict(p_raw)
        return p_raw
