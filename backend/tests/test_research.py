import numpy as np
import pandas as pd

from backend.app.calendar import calendar
from backend.app.db import SessionLocal
from backend.app.entities import Job
from backend.app.research import feature_matrix, training_indices, encode_prices, interval_prices
from backend.app.worker import execute_job
from .test_imports import stock, commit


def history_upload(client, identifier, count=500, flat=False):
    # Synthetic prices ONLY inside tests, never loaded by the production application.
    rng = np.random.default_rng(42)
    dates = calendar().sessions_in_range("2020-01-01", "2024-12-31")[:count]
    returns = (
        np.zeros(count) if flat else 0.004 * np.sin(np.arange(count) * 0.21) + rng.normal(0, 0.0002, count)
    )
    closes = 20 * np.exp(np.cumsum(returns))
    frame = pd.DataFrame(
        {
            "date": [str(d.date()) for d in dates],
            "open": closes * 0.999,
            "high": closes * 1.005,
            "low": closes * 0.995,
            "close": closes,
            "volume": 10000 + np.arange(count),
        }
    )
    response = client.post(
        "/api/v1/imports/preview",
        data={"stock_id": identifier},
        files={"file": ("test.csv", frame.to_csv(index=False).encode(), "text/csv")},
    )
    assert response.status_code == 200, response.text
    commit(client, response.json())


def test_causal_features_and_target_boundary():
    def row(i):
        return {
            "close": 10 + i / 100,
            "open": 10 + i / 100,
            "high": 11 + i / 100,
            "low": 9 + i / 100,
            "volume": 100 + i,
        }

    rows = [row(i) for i in range(300)]
    before = feature_matrix(rows)
    rows[201]["close"] = 1000
    after = feature_matrix(rows)
    np.testing.assert_array_equal(before[:201], after[:201])
    for horizon in (1, 20, 60):
        indices = training_indices(180, horizon)
        assert indices.max() + horizon <= 180


def test_ohlc_constraints_and_interval_order():
    for vector in (np.array([0.1, -0.1, -0.2, -0.4]), np.array([-0.2, 0.3, 0.1, 0.2])):
        price = encode_prices(vector, 10, "next_day")
        opened, high, low, closed = price
        assert 0 < low <= min(opened, closed) <= max(opened, closed) <= high
        lower, upper = interval_prices(vector, np.ones(4) * 0.2, 10, "next_day")
        assert (lower <= upper).all()


def test_training_prediction_and_unseen_test_guard(client):
    identifier = stock(client)
    history_upload(client, identifier)
    body = {
        "stock_id": identifier,
        "horizons": ["next_day"],
        "families": ["ridge"],
        "max_trials": 3,
        "time_budget_seconds": 60,
        "min_improvement": 0.01,
    }
    response = client.post("/api/v1/training-runs", json=body, headers={"Idempotency-Key": "test-training"})
    assert response.status_code == 202, response.text
    accepted = response.json()
    assert (
        client.post("/api/v1/training-runs", json=body, headers={"Idempotency-Key": "test-training"}).json()
        == accepted
    )
    execute_job(accepted["job_id"])
    job = client.get("/api/v1/jobs/" + accepted["job_id"]).json()
    assert job["status"] == "succeeded", job
    models = client.get("/api/v1/models", params={"stock_id": identifier, "selected_only": True}).json()
    assert len(models) >= 1, client.get("/api/v1/training-runs/" + accepted["run_id"]).json()
    model = models[0]
    assert model["metrics"]["fitted_through"] < model["metrics"]["test"]["evaluation_start"]
    assert client.post("/api/v1/training-runs", json=body).status_code == 409
    pred_body = {"stock_id": identifier, "horizon": "next_day", "model_ids": [model["id"]], "with_ai": True}
    pred = client.post("/api/v1/predictions", json=pred_body)
    assert pred.status_code == 202, pred.text
    execute_job(pred.json()["job_id"])
    result = client.get("/api/v1/predictions/" + pred.json()["prediction_id"]).json()
    assert result["result"]["models"][0]["candle"]["high"] >= result["result"]["models"][0]["candle"]["close"]
    assert result["ai_status"] == "not_configured"
    assert result["ai_analyses"][0]["content"]["message"]
    assert result["result"]["strategy"]["status"] == "not_validated"
    assert client.post("/api/v1/predictions", json={**pred_body, "horizon": "one_month"}).status_code == 409
    other = stock(client, "000002")
    history_upload(client, other)
    assert client.post("/api/v1/predictions", json={**pred_body, "stock_id": other}).status_code == 409


def test_no_model_passes_on_flat_series_and_cancel(client):
    identifier = stock(client)
    history_upload(client, identifier, flat=True)
    request = {
        "stock_id": identifier,
        "horizons": ["next_day"],
        "families": ["ridge"],
        "max_trials": 1,
        "time_budget_seconds": 30,
    }
    response = client.post("/api/v1/training-runs", json=request).json()
    execute_job(response["job_id"])
    assert client.get("/api/v1/jobs/" + response["job_id"]).json()["status"] == "succeeded"
    assert client.get("/api/v1/models", params={"stock_id": identifier, "selected_only": True}).json() == []
    # Cancel a fresh queued AI job; no network call should be made.
    with SessionLocal() as db:
        job = Job(kind="ai", payload={"prediction_id": "nonexistent"})
        db.add(job)
        db.commit()
        job_id = job.id
    assert client.post("/api/v1/jobs/" + job_id + "/cancel").json()["status"] == "cancelled"


def test_long_horizon_skips_when_history_insufficient(client):
    identifier = stock(client)
    history_upload(client, identifier, 220)
    response = client.post(
        "/api/v1/training-runs",
        json={
            "stock_id": identifier,
            "horizons": ["three_months"],
            "families": ["ridge"],
            "max_trials": 1,
            "time_budget_seconds": 30,
        },
    ).json()
    execute_job(response["job_id"])
    run = client.get("/api/v1/training-runs/" + response["run_id"]).json()
    assert run["report"]["horizons"]["three_months"]["status"] == "insufficient_data"


def test_spawned_worker_and_long_horizon_paths(client):
    import os
    import subprocess
    import sys

    identifier = stock(client)
    history_upload(client, identifier, count=1000)
    response = client.post(
        "/api/v1/training-runs",
        json={
            "stock_id": identifier,
            "horizons": ["one_month", "three_months"],
            "families": ["ridge"],
            "max_trials": 1,
            "time_budget_seconds": 60,
        },
    ).json()
    # The real worker claims the DB queue and spawns a fresh computation process.
    environment = {**os.environ}
    outcome = subprocess.run(
        [sys.executable, "-m", "backend.app.worker", "--once"],
        capture_output=True,
        text=True,
        env=environment,
        timeout=90,
    )
    assert outcome.returncode == 0, outcome.stderr
    job = client.get("/api/v1/jobs/" + response["job_id"]).json()
    assert job["status"] == "succeeded", job
    run = client.get("/api/v1/training-runs/" + response["run_id"]).json()
    for horizon, steps in [("one_month", 20), ("three_months", 60)]:
        assert run["report"]["horizons"][horizon]["status"] == "completed"
        models = client.get(
            "/api/v1/models", params={"stock_id": identifier, "horizon": horizon, "selected_only": True}
        ).json()
        if models:
            pred = client.post(
                "/api/v1/predictions",
                json={"stock_id": identifier, "horizon": horizon, "model_ids": [models[0]["id"]]},
            ).json()
            execute_job(pred["job_id"])
            result = client.get("/api/v1/predictions/" + pred["prediction_id"]).json()["result"]
            assert len(result["models"][0]["points"]) == steps
            assert all(p["lower"] <= p["estimate"] <= p["upper"] for p in result["models"][0]["points"])
            assert (
                len(client.get("/api/v1/models/" + models[0]["id"] + "/diagnostics").json()["by_target"])
                == steps
            )


def test_repeat_research_parameter_search_and_experimental_prediction(client, monkeypatch):
    identifier = stock(client)
    history_upload(client, identifier)
    request = {
        "stock_id": identifier,
        "horizons": ["next_day"],
        "families": ["ridge"],
        "evaluation_mode": "research",
        "trials_per_family": 3,
        "time_budget_seconds": 60,
        "ridge_alpha_min": 0.1,
        "ridge_alpha_max": 100,
    }
    first = client.post("/api/v1/training-runs", json=request)
    assert first.status_code == 202
    execute_job(first.json()["job_id"])
    detail = client.get("/api/v1/training-runs/" + first.json()["run_id"]).json()
    parameters = [t["parameters"]["alpha"] for t in detail["trials"] if t["family"] == "ridge"]
    assert len(parameters) == 3 and len(set(parameters)) == 3
    assert parameters[0] == 0.1
    models = client.get("/api/v1/models", params={"stock_id": identifier, "run_id": detail["id"]}).json()
    assert models and not any(m["selected"] for m in models)
    candidate = next(m for m in models if m["family"] == "ridge")
    assert candidate["metrics"]["assessment"]["evaluation_mode"] == "research"
    assert "interval_score" in candidate["metrics"]["test"]
    assert candidate["metrics"]["test"]["sampling"] == "consecutive_trading_origins"
    # Explicit experimental selection is necessary; it doesn't grant wrong-horizon access.
    forecast_request = {"stock_id": identifier, "horizon": "next_day", "model_ids": [candidate["id"]]}
    assert client.post("/api/v1/predictions", json=forecast_request).status_code == 409
    forecast_request["allow_unvalidated"] = True
    prediction = client.post("/api/v1/predictions", json=forecast_request).json()
    assert (
        client.post("/api/v1/predictions/" + prediction["prediction_id"] + "/ai-analysis").status_code == 409
    )
    execute_job(prediction["job_id"])
    saved = client.get("/api/v1/predictions/" + prediction["prediction_id"]).json()
    assert saved["result"]["experimental"] is True and saved["ai_status"] == "not_requested"
    assert saved["ai_analyses"] == []
    assert (
        client.post("/api/v1/predictions", json={**forecast_request, "horizon": "one_month"}).status_code
        == 409
    )
    # Research may repeat with a different budget, but can't be relabeled a fresh holdout.
    assert (
        client.post("/api/v1/training-runs", json={**request, "evaluation_mode": "holdout"}).status_code
        == 409
    )
    repeated = client.post("/api/v1/training-runs", json={**request, "time_budget_seconds": 90})
    assert repeated.status_code == 202
    execute_job(repeated.json()["job_id"])
    assert client.get("/api/v1/jobs/" + repeated.json()["job_id"]).json()["status"] == "succeeded"
    monkeypatch.delenv("AI_ANALYSIS_URL", raising=False)
    assert (
        client.post("/api/v1/predictions/" + prediction["prediction_id"] + "/ai-analysis").json()["code"]
        == "ai_not_configured"
    )
    # The AI action cannot change numeric results, including when the service is missing.
    assert (
        client.get("/api/v1/predictions/" + prediction["prediction_id"]).json()["result"] == saved["result"]
    )
    from backend.app.integrations import HttpAnalysisProvider

    seen = []

    def test_analysis(_provider, body):
        seen.append(body)
        return {"text": "TEST ONLY: explanation", "sources": [{"url": "https://example.com/test"}]}

    monkeypatch.setenv("AI_ANALYSIS_URL", "https://example.com/test-only-analysis")
    monkeypatch.setattr(HttpAnalysisProvider, "analyze", test_analysis)
    ai = client.post("/api/v1/predictions/" + prediction["prediction_id"] + "/ai-analysis").json()
    # Repeated clicks while queued return the same task, avoiding duplicate analysis.
    assert client.post("/api/v1/predictions/" + prediction["prediction_id"] + "/ai-analysis").json() == ai
    execute_job(ai["job_id"])
    explained = client.get("/api/v1/predictions/" + prediction["prediction_id"]).json()
    assert explained["ai_status"] == "succeeded" and seen[0]["forecast"] == saved["result"]
    assert explained["result"] == saved["result"] and len(explained["ai_analyses"]) == 1


def test_skill_test_respects_pairing_dependence_and_parameter_bounds(client):
    from backend.app.assessment import compare_errors
    from backend.app.research import candidates

    rng = np.random.default_rng(14)
    error = rng.uniform(0.1, 0.2, 128)
    base_error = error + rng.uniform(0.01, 0.03, 128)
    series = {"dates": list(range(128)), "residuals": error.tolist(), "origin_indices": list(range(128))}
    base = {**series, "residuals": base_error.tolist()}
    result = compare_errors(series, base, 1)
    assert result["pvalue"] < 0.05 and result["gain_ci95"][0] > 0
    assert compare_errors(series, base, 60)["status"] == "insufficient_samples"
    assert compare_errors({**series, "origin_indices": list(range(0, 256, 2))}, base, 1)["pvalue"] is None
    assert compare_errors(series, {**base, "dates": list(range(1, 129))}, 1)["status"] == "unaligned"
    assert (
        compare_errors({**series, "residuals": [0.1] * 128}, {**base, "residuals": [0.2] * 128}, 1)["pvalue"]
        is None
    )
    assert max(p["order"][0] for p in candidates("arima", {"arima_max_order": 3})) == 3
    bad = client.post(
        "/api/v1/training-runs", json={"stock_id": "anything", "ridge_alpha_min": 10, "ridge_alpha_max": 1}
    )
    assert bad.status_code == 422
