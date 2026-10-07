from types import SimpleNamespace
import fcntl
import sqlite3
import pytest
from backend.app.config import DATA_DIR
from backend.app.db import SessionLocal
from backend.app.entities import Job
from backend.app.errors import DomainError
from backend.app.reliability import reliability
from backend.app.search import refine_ridge, choose_fittable
from backend.app.backup import backup, restore_new, verify
from backend.app.research import feature_matrix, load_artifact
from .test_imports import stock, preview, commit
from .test_research import history_upload
from backend.app.worker import execute_job


def test_match_pass_does_not_claim_baseline_advantage_or_stable_intervals():
    def model(family, mae, rmse, coverage):
        test = {
            "mae": mae,
            "rmse": rmse,
            "coverage": coverage,
            "mean_width": 12,
            "interval_score": 12,
            "price_match": 0.97,
            "nonoverlap_sample_count": 6,
        }
        return SimpleNamespace(
            family=family,
            metrics={
                "test": test,
                "nominal_coverage": 0.9,
                "passes_thresholds": True,
                "cross_validation": {
                    "folds": [{"metrics": {**test, "coverage": 0.84}}, {"metrics": {**test, "coverage": 1}}]
                },
            },
        )

    result = reliability(model("ridge", 1.1, 1.2, 0.95), model("naive", 1, 1.1, 0.9))
    assert result["price_match_passed"] and result["baseline_status"] == "underperforming"
    assert not result["interval_stable"] and result["min_fold_coverage"] == 0.84
    assert result["nonoverlap_windows"] == 6


def test_refinement_uses_score_and_respects_bounds():
    history = [{"parameters": {"alpha": a}, "score": loss} for a, loss in ((0.1, 3), (1, 1), (100, 2))]
    assert refine_ridge(history, 0.1, 100) == {"alpha": 10}
    assert refine_ridge([{"parameters": {"alpha": 1}, "score": None}], 0.1, 100) is None
    history.append({"parameters": {"alpha": 10}, "score": 0.5})
    assert 0.1 <= refine_ridge(history, 0.1, 100)["alpha"] <= 100


def test_preflight_fallback_never_reads_final_scores(monkeypatch):
    from backend.app import research

    seen = []
    monkeypatch.setattr(research, "checkpoint", lambda *a, **kw: None)

    def fit(rows, features, end, horizon, family, parameters, seed):
        seen.append((end, parameters["alpha"]))
        if parameters["alpha"] == 1 and end == 200:
            raise ValueError("fitting failure")
        return {"fit_end": end}

    monkeypatch.setattr(research, "fit", fit)
    ranked = [{"parameters": {"alpha": a}, "score": a} for a in (1, 2, 3)]
    selected, artifacts, failures = choose_fittable(
        [], [], "next_day", "ridge", ranked, [100, 200], 42, "job", None
    )
    assert selected["parameters"]["alpha"] == 2
    assert seen == [(100, 1), (200, 1), (100, 2), (200, 2)]
    assert len(failures) == 1 and len(artifacts) == 2


def test_latest_refit_has_separate_artifact_causal_calibration_and_frozen_history(client, monkeypatch):
    identifier = stock(client)
    history_upload(client, identifier, count=500, flat=True)
    created = client.post(
        "/api/v1/training-runs",
        json={
            "stock_id": identifier,
            "horizons": ["next_day"],
            "families": ["ridge"],
            "trials_per_family": 1,
            "time_budget_seconds": 60,
        },
    ).json()
    execute_job(created["job_id"])
    model = client.get("/api/v1/models", params={"stock_id": identifier, "selected_only": True}).json()[0]
    old_metrics = model["metrics"]
    pred = client.post(
        "/api/v1/predictions",
        json={"stock_id": identifier, "horizon": "next_day", "model_ids": [model["id"]]},
    ).json()
    execute_job(pred["job_id"])
    result = client.get("/api/v1/predictions/" + pred["prediction_id"]).json()["result"]
    deployed = result["models"][0]
    assert deployed["fitted_through"] == result["data_cutoff"] > model["metrics"]["fitted_through"]
    assert deployed["artifact_checksum"] != deployed["evaluation_artifact_checksum"]
    assert deployed["deployment"]["calibration_end"] < deployed["deployment"]["audit_start"]
    assert deployed["deployment"]["recent_audit"]["sample_count"] == 32
    assert (
        client.get("/api/v1/models", params={"stock_id": identifier, "selected_only": True}).json()[0][
            "metrics"
        ]
        == old_metrics
    )
    artifact = load_artifact(
        SimpleNamespace(
            artifact_path=deployed["deployment"]["artifact_path"],
            artifact_checksum=deployed["artifact_checksum"],
        )
    )
    assert artifact["fit_end"] == 499

    from backend.app import deployment

    original = deployment.prepare_deployment

    def partial_failure(rows, features, candidate, *args):
        if candidate.family == "naive":
            raise ValueError("test-only fitting failure")
        return original(rows, features, candidate, *args)

    monkeypatch.setattr(deployment, "prepare_deployment", partial_failure)
    peers = client.get("/api/v1/models", params={"stock_id": identifier, "run_id": created["run_id"]}).json()
    naive = next(m for m in peers if m["family"] == "naive")
    second = client.post(
        "/api/v1/predictions",
        json={
            "stock_id": identifier,
            "horizon": "next_day",
            "model_ids": [model["id"], naive["id"]],
            "allow_unvalidated": True,
        },
    ).json()
    execute_job(second["job_id"])
    partial = client.get("/api/v1/predictions/" + second["prediction_id"]).json()["result"]
    assert len(partial["models"]) == 1 and partial["model_failures"][0]["family"] == "naive"
    assert client.get("/api/v1/predictions/" + pred["prediction_id"]).json()["result"] == result


def test_rolling_fit_never_sees_future_labels(monkeypatch):
    from backend.app import research
    from backend.app.deployment import prepare_deployment

    rows = [
        {"time": f"{i:04}", "open": 20, "close": 20, "high": 21, "low": 19, "volume": 100} for i in range(500)
    ]
    fits, evaluations = [], []
    original_fit = research.fit

    def fit(*args):
        fits.append(args[2])
        return original_fit(*args)

    original_evaluate = research.evaluate

    def evaluate(artifact, rows, features, start, end, *args, **kw):
        evaluations.append((artifact["fit_end"], start, end))
        return original_evaluate(artifact, rows, features, start, end, *args, **kw)

    monkeypatch.setattr(research, "fit", fit)
    monkeypatch.setattr(research, "evaluate", evaluate)
    monkeypatch.setattr(research, "checkpoint", lambda *a, **kw: None)
    model = SimpleNamespace(
        horizon="one_month", family="ridge", parameters={"alpha": 1}, metrics={"nominal_coverage": 0.9}
    )
    _, detail = prepare_deployment(rows, feature_matrix(rows), model, {"evaluation_samples": 64}, "job", None)
    assert all(fit_end <= start < end for fit_end, start, end in evaluations)
    assert fits[-1] == 499 and all(end < 499 for end in fits[:-1])
    assert detail["calibration_end"] < detail["audit_start"]


def test_backup_blocks_active_tasks_and_storage_writes(client, tmp_path):
    from backend.app.db import engine

    if engine.dialect.name != "sqlite":
        pytest.skip("Postgres backup requires pg_dump")
    with SessionLocal() as db:
        db.add(Job(kind="prediction", payload={}))
        db.commit()
    with pytest.raises(DomainError, match="等待"):
        backup(tmp_path / "active")
    with open(DATA_DIR / "storage.lock", "a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        response = client.post("/api/v1/stocks", json={"code": "000001", "name": "test", "exchange": "SZSE"})
        assert response.status_code == 409 and response.json()["code"] == "storage_busy"


def test_complete_backup_restore_new_and_corruption_detection(client, tmp_path):
    from backend.app.db import engine

    if engine.dialect.name != "sqlite":
        pytest.skip("Postgres restore uses pg_restore")
    identifier = stock(client)
    commit(client, preview(client, identifier))
    client.post("/api/v1/snapshots", json={"stock_id": identifier})
    (DATA_DIR / "models" / "test-only.joblib").write_bytes(b"test-only")
    source = tmp_path / "complete"
    backup(source)
    restored = tmp_path / "new-data"
    restore_new(source, restored)
    assert (restored / "models" / "test-only.joblib").read_bytes() == b"test-only"
    with sqlite3.connect(restored / "research.db") as db:
        assert db.execute("select count(*) from bar_current").fetchone()[0] == 2
    with pytest.raises(ValueError, match="新目录"):
        restore_new(source, restored)
    (source / "data" / "models" / "test-only.joblib").write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="校验失败"):
        verify(source)


def test_second_source_comparison_does_not_mutate_prices(client):
    identifier = stock(client)
    commit(client, preview(client, identifier))
    before = client.get("/api/v1/market/bars", params={"stock_id": identifier}).json()
    assert len(before["bars"]) == 2
    content = b"date,open,high,low,close,volume\n2024-01-02,10,12,9,11,100\n2024-01-03,11,13,10,12,100\n"
    result = client.post(
        "/api/v1/maintenance/compare",
        data={"stock_id": identifier, "source": "test comparison only"},
        files={"file": ("other.csv", content)},
    )
    assert result.status_code == 200, result.text
    assert result.json()["compared"] == 2
    assert (DATA_DIR / result.json()["input_path"]).read_bytes() == content
    assert client.get("/api/v1/market/bars", params={"stock_id": identifier}).json() == before
    status = client.get("/api/v1/maintenance/status", params={"stock_id": identifier}).json()
    assert status["bars"] == 2 and status["audits"]
