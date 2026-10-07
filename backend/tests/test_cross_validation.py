import numpy as np
import pytest

from backend.app.cross_validation import fold_boundaries
from backend.app.worker import execute_job
from .test_imports import stock
from .test_research import history_upload


def test_statistical_optimizer_retry_is_bounded_and_sees_only_fitting_history(monkeypatch):
    from types import SimpleNamespace
    from backend.app import research

    calls, observations = [], []
    still_fails = False

    class FittingOnlyARIMA:
        def __init__(self, values, **settings):
            observations.append(len(values))

        def fit(self, **settings):
            calls.append(settings)
            return SimpleNamespace(
                params=np.array([0.2]), mle_retvals={"converged": len(calls) == 2 and not still_fails}
            )

    monkeypatch.setattr(research, "ARIMA", FittingOnlyARIMA)
    rows = [
        {
            "open": 20 + i / 100,
            "close": 20 + i / 100,
            "high": 21 + i / 100,
            "low": 19 + i / 100,
            "volume": 100,
        }
        for i in range(300)
    ]
    artifact = research.fit(
        rows, research.feature_matrix(rows), 180, "next_day", "arima", {"order": [1, 0, 1]}, 42
    )
    assert observations == [181]
    assert [c["method_kwargs"]["maxiter"] for c in calls] == [80, 400]
    np.testing.assert_array_equal(calls[1]["start_params"], [0.2])
    assert artifact["fit_diagnostics"]["optimizer_retry"] is True
    still_fails = True
    calls.clear()
    with pytest.raises(ValueError, match="未收敛"):
        research.fit(rows, research.feature_matrix(rows), 180, "next_day", "arima", {"order": [1, 0, 1]}, 42)
    assert len(calls) == 2


def test_forward_folds_keep_fitting_calibration_and_all_labels_in_order():
    count, steps = 2611, 60
    folds = fold_boundaries(count, steps)
    assert folds[0]["start"] == int(count * 0.85)
    assert folds[-1]["end"] == count - 1
    origins = []
    for fold in folds:
        assert fold["fit_end"] < fold["start"] - 1
        local = list(range(fold["start"] - 1, fold["end"] - steps + 1))
        assert local[0] + 1 == fold["start"]
        assert local[-1] + steps == fold["end"]
        assert fold["fit_end"] < local[0]
        origins.extend(local)
    assert len(set(origins)) == len(origins) == 215
    assert np.all(np.diff([f["fit_end"] for f in folds]) > 0)
    assert fold_boundaries(501, 1)[0]["fit_end"] == int(501 * 0.7) - 1
    with pytest.raises(ValueError):
        fold_boundaries(1000, 60)


def test_price_match_validation_qualifies_without_new_independent_data(client):
    identifier = stock(client)
    history_upload(client, identifier, flat=True)
    body = {
        "stock_id": identifier,
        "horizons": ["next_day"],
        "families": ["ridge"],
        "trials_per_family": 1,
        "time_budget_seconds": 60,
        "match_threshold": 0.95,
        "min_improvement": 0.5,
    }
    created = client.post("/api/v1/training-runs", json=body)
    assert created.status_code == 202
    execute_job(created.json()["job_id"])
    job = client.get("/api/v1/jobs/" + created.json()["job_id"]).json()
    assert job["status"] == "succeeded", job
    models = client.get("/api/v1/models", params={"stock_id": identifier, "selected_only": True}).json()
    assert len(models) == 1
    model = models[0]
    assert model["metrics"]["evaluation_mode"] == "cross_validation"
    assert model["metrics"]["acceptance_checks"] == {"price_match": True}
    assert model["metrics"]["reference_checks"]["test_improvement"] is False
    cv = model["metrics"]["cross_validation"]
    assert cv["validation_bars"] == 75 and len(cv["folds"]) == 3
    assert model["metrics"]["test"]["sample_count"] == 75
    assert all(f["fitted_through"] < f["calibration_end"] < f["validation_start"] for f in cv["folds"])
    diagnostic = client.get("/api/v1/models/" + model["id"] + "/diagnostics").json()
    mape = np.mean(np.abs(np.array(diagnostic["actual"]) - diagnostic["predicted"]) / diagnostic["actual"])
    assert model["metrics"]["test"]["price_match"] == pytest.approx(max(0, 1 - mape))
    predicted = client.post(
        "/api/v1/predictions",
        json={"stock_id": identifier, "horizon": "next_day", "model_ids": [model["id"]]},
    )
    assert predicted.status_code == 202
    execute_job(predicted.json()["job_id"])
    result = client.get("/api/v1/predictions/" + predicted.json()["prediction_id"]).json()["result"]
    assert result["experimental"] is False
    assert result["models"][0]["evaluation_mode"] == "cross_validation"
    assert result["models"][0]["validation_price_match"] == pytest.approx(
        model["metrics"]["test"]["price_match"]
    )
    repeated = client.post("/api/v1/training-runs", json={**body, "time_budget_seconds": 90})
    assert repeated.status_code == 202
    execute_job(repeated.json()["job_id"])
    assert client.get("/api/v1/jobs/" + repeated.json()["job_id"]).json()["status"] == "succeeded"
    selected = client.get("/api/v1/models", params={"stock_id": identifier, "selected_only": True}).json()
    assert selected and all(m["run_id"] == repeated.json()["run_id"] for m in selected)


def test_miss_match_threshold_does_not_force_selection(client):
    identifier = stock(client)
    history_upload(client, identifier)
    created = client.post(
        "/api/v1/training-runs",
        json={
            "stock_id": identifier,
            "horizons": ["next_day"],
            "families": ["ridge"],
            "trials_per_family": 1,
            "time_budget_seconds": 60,
            "match_threshold": 1,
        },
    ).json()
    execute_job(created["job_id"])
    assert client.get("/api/v1/jobs/" + created["job_id"]).json()["status"] == "succeeded"
    assert client.get("/api/v1/models", params={"stock_id": identifier, "selected_only": True}).json() == []
