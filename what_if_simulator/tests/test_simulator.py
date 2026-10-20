import copy
import json
import math

import numpy as np
import pandas as pd
import pytest

from whatif_twin import Scenario
from whatif_twin.export import PermissionDenied, result_payload, simulate_request


def test_integrity_and_reproduces_saved_risks(sim):
    assert sim.art.integrity["watch_model_sha256_matches_manifest"]
    for cid in sim.patients.ids(include_synthetic=False):
        c = sim.patients.get(cid)
        for m, saved in c.meta["saved_risk"].items():
            assert abs(sim.models[m].predict(c.values)["prob"] - saved) < 1e-4, (cid, m)


def test_shap_additivity_every_case(sim):
    for cid in sim.patients.ids():
        c = sim.patients.get(cid)
        for m in c.models:
            phi, base = sim.models[m].shap(c.values)
            assert abs(base + phi.sum() - sim.models[m].predict(c.values)["raw_log_odds"]) < 1e-5


def test_no_change_means_no_difference_and_no_mutation(sim, case):
    before = copy.deepcopy(case.values)
    r = sim.simulate(case, Scenario("nothing", []))
    assert r.status == "NO_MEANINGFUL_CHANGE"
    assert all(abs(o.delta) < 1e-12 for o in r.outcomes.values())
    sim.simulate(case, Scenario.of("hr", heart_rate={"delta": -10}))
    assert all((pd.isna(before[k]) and pd.isna(v)) or before[k] == v for k, v in case.values.items())


def test_heart_rate_shift_updates_all_derived_inputs(sim, case):
    r = sim.simulate(case, Scenario.of("hr", heart_rate={"delta": -10}))
    v0, v1 = case.values, r.applied.values
    for f in ("hr", "hr_lag1", "hr_mean6h", "hr_max6h", "hr_delta6h", "hr_vs_baseline"):
        assert v1[f] == pytest.approx(v0[f] - 10)
    assert v1["shock_index"] == pytest.approx(v0["shock_index"] * v1["hr"] / v0["hr"])
    assert "hr_sd_24h" in r.applied.held


def test_persistent_shift_keeps_change_features(sim, case):
    r = sim.simulate(case, Scenario.of("hr", heart_rate={"delta": -10, "mode": "persistent"}))
    assert r.applied.values["hr_delta6h"] == case.values["hr_delta6h"]
    assert r.applied.values["hr_mean6h"] == pytest.approx(case.values["hr_mean6h"] - 10)


def test_set_mode_makes_recent_window_flat(sim, case):
    r = sim.simulate(case, Scenario.of("hr", heart_rate={"value": 80}))
    for f in ("hr", "hr_lag1", "hr_mean6h", "hr_max6h"):
        assert r.applied.values[f] == 80


@pytest.mark.parametrize("scenario, words", [
    (Scenario.of("sleep", sleep=8), "sleep"),
    (Scenario.of("hr", heart_rate={"value": 300}), "outside the accepted range"),
    (Scenario.of("w", weight={"delta": -5}, bmi=22), "same quantity"),
    (Scenario.of("bp", clinic_sbp=90, clinic_dbp=95), "Diastolic"),
    (Scenario.of("hdl", hdl=60), "total cholesterol"),
    (Scenario.of("both", heart_rate={"value": 80, "delta": -5}), "exactly one"),
])
def test_guardrails(sim, case, scenario, words):
    r = sim.simulate(case, scenario)
    assert r.status == "REJECTED"
    assert words.lower() in " ".join(x["reason"] for x in r.applied.rejected).lower()


def test_spo2_never_above_100(sim, case):
    r = sim.simulate(case, Scenario.of("o2", spo2={"delta": 10}))
    assert max(r.applied.values[f] for f in ("spo2", "spo2_mean6h", "spo2_max6h")) <= 100


def test_weight_recomputes_bmi(sim, case):
    r = sim.simulate(case, Scenario.of("w", weight={"delta": -5}))
    v = r.applied.values
    assert v["bmi"] == pytest.approx(703 * v["weight_lbs"] / case.values["height_in"] ** 2, abs=0.01)
    assert v["weight_kg"] == pytest.approx(case.values["weight_kg"] - 5)


def test_cholesterol_keeps_hdl_constant(sim):
    c = sim.synthetic_case()
    hdl = c.values["total_chol"] / c.values["tc_hdl_ratio"]
    r = sim.simulate(c, Scenario.of("tc", total_chol=180))
    v = r.applied.values
    assert v["non_hdl"] == pytest.approx(180 - hdl)
    assert v["total_chol"] / v["tc_hdl_ratio"] == pytest.approx(hdl)


def test_ascvd_responds_to_smoking(sim):
    c = sim.synthetic_case()                       # smoker with a recorded ASCVD score
    r = sim.simulate(c, Scenario.of("quit", smoker=0))
    assert r.applied.values["ascvd_10y_risk"] < c.values["ascvd_10y_risk"]


def test_unaffected_model_reports_no_change(sim, case):
    r = sim.simulate(case, Scenario.of("bp", inpatient_sbp={"delta": 20}))
    assert not r.outcomes["watch"].affected and r.outcomes["watch"].delta == 0
    assert r.outcomes["full"].affected


def test_shap_delta_explains_change_exactly(sim, case):
    r = sim.simulate(case, Scenario.of("multi", heart_rate={"delta": -10}, spo2={"value": 98}, smoker=1))
    for m, o in r.outcomes.items():
        d_lo = o.scenario["raw_log_odds"] - o.baseline["raw_log_odds"]
        assert o.shap_delta.sum() == pytest.approx(d_lo, abs=1e-5)


def test_tier_boundary_goes_up(sim):
    tm = sim.models["watch"]
    assert tm.tier(tm.thresholds["high"]) == "HIGH" and tm.tier(tm.thresholds["moderate"]) == "MODERATE"


def test_feature_level_matches_reading_level_within_one_point(sim):
    c = sim.synthetic_case()
    for d in (-10, 10):
        rd = sim.reading_scenario(c, [{"signal": "hr", "delta": d, "hours": 6}])
        ft = sim.simulate(c, Scenario.of("hr", heart_rate={"delta": d})).outcomes["watch"]
        assert abs(rd["scenario"]["prob"] - ft.scenario["prob"]) < 0.01


def test_reading_edit_does_not_mutate_request(sim):
    c = sim.synthetic_case()
    before = json.dumps(c.request, sort_keys=True, default=str)
    sim.reading_twin.edit(c.request, [{"signal": "hr", "delta": -20, "hours": 6}, {"profile": "smoker", "value": 0}])
    assert json.dumps(c.request, sort_keys=True, default=str) == before


def test_counterfactuals_move_toward_target_band_and_reach_target(sim):
    c = sim.patients.get("stay-31248398")
    cf = sim.counterfactual(c, target_tier="MODERATE")
    hit = cf[cf.reaches_target]
    assert len(hit) and (hit.prob < cf.attrs["limit"]).all()
    for sc in cf.scenario:
        for k, x in sc.items():
            lv = sim.levers[k]
            cur, band = lv.current(c.values), sim._healthy_band(c, lv)
            assert abs(x - min(max(x, band[0]), band[1])) < 1e-6 or (x - cur) * (np.clip(cur, *band) - cur) > 0


def test_payload_contract(sim, case):
    p = simulate_request(sim, {"case_id": case.case_id, "scenario": {"name": "hr", "changes": {"heart_rate": {"delta": -10}}}})
    json.dumps(p)
    assert p["simulation"] and not p["writes_to_patient_record"] and p["label"].startswith("HYPOTHETICAL")
    assert set(p["models"]) == {"watch", "full"}
    with pytest.raises(PermissionDenied):
        simulate_request(sim, {"case_id": case.case_id, "scenario": {"name": "x", "changes": {}}}, role="patient")


def test_synthetic_patient_is_labelled(sim):
    c = sim.synthetic_case()
    assert c.is_synthetic and "SYNTHETIC" in c.source
    assert all("SYNTHETIC" in r["source"] for r in c.request["wearable_readings"])
