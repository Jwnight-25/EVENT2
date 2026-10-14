import copy
import json
import math

import numpy as np
import pytest

from backend.app.research import candidates, feature_matrix, fit, forecast
from backend.app.schemas import TrainingRequest
from backend.app.search import proposal_stream
from backend.app.worker import execute_job
from .test_imports import stock
from .test_research import history_upload


def run_search(families, cap=4, shared=False, flat=False):
    config = TrainingRequest(
        stock_id="test", families=families, trials_per_family=None if shared else cap, max_trials=min(cap, 30)
    ).model_dump()
    histories, counts, bests, seen = {}, {f: 0 for f in families}, {}, []
    grids = {f: candidates(f, config) for f in families}
    for family, params, stage in proposal_stream(families, grids, histories, counts, bests, config):
        if family != "naive" and ((shared and sum(counts.values()) >= cap) or counts[family] >= cap):
            continue
        loss = (
            1.0
            if flat
            else (abs(math.log10(params.get("alpha", 1))) + abs(params.get("feature_lags", 6) - 6) + 1)
        )
        histories.setdefault(family, []).append({"parameters": params, "score": loss})
        if loss < bests.get(family, {}).get("score", math.inf):
            bests[family] = {"parameters": params, "score": loss}
        if family != "naive":
            counts[family] += 1
        seen.append((family, params, stage))
    return config, seen


def test_daily_four_trial_budget_refines_every_available_family():
    config, seen = run_search(["naive", "ridge", "arima", "sarima"])
    for family in ("ridge", "arima", "sarima"):
        items = [(p, s) for f, p, s in seen if f == family]
        assert len(items) == 4 and items[-1][1] == "refinement"
        assert len({json.dumps(p, sort_keys=True) for p, _ in items}) == 4
    ridge = [p for f, p, _ in seen if f == "ridge"]
    assert len({p["alpha"] for p in ridge}) >= 3
    assert len({p["feature_lags"] for p in ridge}) >= 3
    assert all(config["ridge_alpha_min"] <= p["alpha"] <= config["ridge_alpha_max"] for p in ridge)


def test_shared_legacy_budget_terminates_and_plateau_stops_early():
    _, seen = run_search(["naive", "ridge", "arima", "sarima"], cap=3, shared=True)
    assert len([f for f, _, _ in seen if f != "naive"]) == 3
    _, seen = run_search(["naive", "ridge"], cap=30, flat=True)
    assert len([s for f, _, s in seen if f == "ridge" and s == "refinement"]) == 3
    assert len([f for f, _, _ in seen if f == "ridge"]) < 30


def rows():
    return [
        {
            "open": 20 + math.sin(i * 0.2),
            "close": 20 + math.sin(i * 0.2) + 0.1,
            "high": 21 + math.sin(i * 0.2),
            "low": 19 + math.sin(i * 0.2),
            "volume": 1000 + i,
        }
        for i in range(300)
    ]


@pytest.mark.parametrize("lags,features,weights", [(3, 14, 60), (6, 17, 72), (10, 21, 88)])
def test_real_ridge_weight_count_and_prediction_use_same_feature_subset(lags, features, weights):
    data = rows()
    x = feature_matrix(data)
    artifact = fit(data, x, 180, "next_day", "ridge", {"alpha": 1.0, "feature_lags": lags}, 42)
    assert artifact["structure"]["feature_count"] == features
    assert artifact["structure"]["predictive_parameter_count"] == weights
    assert len(artifact["feature_indices"]) == features
    expected = artifact["estimator"].predict(x[200:201][:, artifact["feature_indices"]])[0]
    np.testing.assert_allclose(forecast(artifact, data, x, 200), expected)
    # Fitting may not learn from observations beyond the fitting cutoff.
    changed = copy.deepcopy(data)
    changed[250]["close"] = 1000
    again = fit(changed, feature_matrix(changed), 180, "next_day", "ridge", artifact["parameters"], 42)
    np.testing.assert_allclose(
        artifact["estimator"].steps[-1][1].coef_, again["estimator"].steps[-1][1].coef_
    )


def test_alpha_changes_learned_weight_magnitude_and_legacy_artifacts_still_predict():
    data = rows()
    x = feature_matrix(data)
    weak = fit(data, x, 180, "next_day", "ridge", {"alpha": 0.01}, 42)
    strong = fit(data, x, 180, "next_day", "ridge", {"alpha": 100000.0}, 42)
    assert weak["structure"]["coefficient_abs_max"] > strong["structure"]["coefficient_abs_max"]
    prediction = forecast(weak, data, x, 200)
    legacy = copy.deepcopy(weak)
    legacy.pop("feature_indices")
    legacy.pop("structure")
    np.testing.assert_allclose(forecast(legacy, data, x, 200), prediction)


def test_user_boundaries_override_previous_seed_and_invalid_lag_range_is_rejected():
    config = TrainingRequest(
        stock_id="test", ridge_alpha_min=1, ridge_alpha_max=10, ridge_lags_min=3, ridge_lags_max=6
    ).model_dump()
    config["seed_parameters"] = {"ridge": {"alpha": 1000, "feature_lags": 10}}
    assert all(1 <= p["alpha"] <= 10 and 3 <= p["feature_lags"] <= 6 for p in candidates("ridge", config))
    with pytest.raises(ValueError):
        TrainingRequest(stock_id="test", ridge_lags_min=9, ridge_lags_max=3)


def test_real_training_records_joint_search_and_warm_start_without_changing_prior_models(client):
    identifier = stock(client)
    history_upload(client, identifier)
    body = {
        "stock_id": identifier,
        "horizons": ["next_day"],
        "families": ["ridge"],
        "trials_per_family": 4,
        "time_budget_seconds": 60,
        "evaluation_mode": "research",
        "tuning_samples": 32,
    }
    first = client.post("/api/v1/training-runs", json=body).json()
    execute_job(first["job_id"])
    detail = client.get("/api/v1/training-runs/" + first["run_id"]).json()
    trials = [t for t in detail["trials"] if t["family"] == "ridge"]
    assert len(trials) == 4 and trials[-1]["report"]["search_stage"] == "refinement"
    assert len({t["report"]["structure"]["predictive_parameter_count"] for t in trials}) >= 3
    original = client.get("/api/v1/models", params={"stock_id": identifier, "run_id": first["run_id"]}).json()
    second = client.post(
        "/api/v1/training-runs", json=body, headers={"Idempotency-Key": "next-search"}
    ).json()
    assert (
        client.post("/api/v1/training-runs", json=body, headers={"Idempotency-Key": "next-search"}).json()
        == second
    )
    detail = client.get("/api/v1/training-runs/" + second["run_id"]).json()
    assert detail["config"]["seed_model_ids"]["next_day"]["ridge"] in [m["id"] for m in original]
    execute_job(second["job_id"])
    assert (
        client.get("/api/v1/models", params={"stock_id": identifier, "run_id": first["run_id"]}).json()
        == original
    )
