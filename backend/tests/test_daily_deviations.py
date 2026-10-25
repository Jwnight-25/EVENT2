"""Synthetic forecasts/actuals live only in the isolated test database."""

import copy
from datetime import date, timedelta
import math

import pytest
from sqlalchemy import select, func

from backend.app import daily_research as daily, daily_deviations as stats
from backend.app.db import SessionLocal
from backend.app.entities import (
    DailyBatch,
    DailyTask,
    DailyOutcome,
    DailyPointRound,
    Prediction,
    MarketFeed,
    Stock,
)
from backend.app.market import preview, commit_import
from .test_daily_research import prepared, freeze_prediction, stamp

ACTUAL = {"open": 10.0, "high": 11.0, "low": 9.0, "close": 10.5}
ERRORS = {
    "naive": {"open": 0.2, "high": -0.1, "low": 0.3, "close": -0.4},
    "ridge": {"open": -0.1, "high": 0.4, "low": -0.2, "close": 0.1},
}


def import_actual(db, stock_id, day="2024-01-04", close=10.5, provider="Tencent"):
    content = f"date,open,high,low,close,volume\n{day},10,11,9,{close},1000\n".encode()
    imported = preview(db, stock_id, content, "test-only.csv", "1d", "raw", "shares", {})
    imported.metadata_json = {**imported.metadata_json, "source": {"provider": provider}}
    db.commit()
    commit_import(db, imported.id, "replace")


def setup(client, monkeypatch):
    identifier, _ = prepared(client, monkeypatch)
    clock = {"value": stamp("2024-01-04")}
    monkeypatch.setattr(daily, "market_time", lambda at=None: at or clock["value"])
    monkeypatch.setattr(stats, "market_time", lambda: clock["value"])
    monkeypatch.setattr(daily, "model_families", lambda: [{"id": f, "available": True} for f in ERRORS])
    daily.daily_tick()
    with SessionLocal() as db:
        batch = db.scalar(select(DailyBatch))
        for task in list(db.scalars(select(DailyTask))):
            freeze_prediction(
                db, batch, task, clock["value"], {f: ACTUAL[f] + ERRORS[task.family][f] for f in daily.FIELDS}
            )
        import_actual(db, identifier)
    clock["value"] = stamp("2024-01-04", 16)
    return identifier, clock


def test_daily_error_values_and_summaries_are_read_only_and_keep_sign(client, monkeypatch):
    identifier, _ = setup(client, monkeypatch)
    with SessionLocal() as db:
        originals = {p.id: copy.deepcopy(p.result) for p in db.scalars(select(Prediction))}
    response = client.get(f"/api/v1/daily-research/{identifier}/deviations")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == data["summary"]["available_count"] == 2
    for item in data["rows"]:
        errors = ERRORS[item["family"]]
        assert item["actual"] == ACTUAL
        assert item["errors"] == pytest.approx(errors)
        assert item["bias"] == pytest.approx(sum(errors.values()) / 4)
        assert item["mae"] == pytest.approx(sum(abs(v) for v in errors.values()) / 4)
        assert item["rmse"] == pytest.approx(math.sqrt(sum(v * v for v in errors.values()) / 4))
        assert item["relative_errors"]["close"] == pytest.approx(errors["close"] / 10.5)
        assert item["actual_revision_id"] and item["model_id"] and item["prediction_id"]
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(DailyOutcome)) == 0
        assert db.scalar(select(func.count()).select_from(DailyPointRound)) == 0
        assert {p.id: p.result for p in db.scalars(select(Prediction))} == originals


def test_history_filters_pagination_and_aggregates_span_more_than_twenty_batches(client, monkeypatch):
    identifier, clock = setup(client, monkeypatch)
    with SessionLocal() as db:
        db.get(MarketFeed, identifier).last_completed_session = "2024-01-04"
        db.commit()
    clock["value"] = stamp("2024-01-05")
    daily.daily_tick()
    with SessionLocal() as db:
        batch = db.scalar(select(DailyBatch).where(DailyBatch.session_date == "2024-01-05"))
        for task in list(db.scalars(select(DailyTask).where(DailyTask.batch_id == batch.id))):
            freeze_prediction(
                db,
                batch,
                task,
                clock["value"],
                {f: ACTUAL[f] + ERRORS[task.family][f] * 2 for f in daily.FIELDS},
            )
        import_actual(db, identifier, day="2024-01-05")
        # Later records must not hide older forecasts behind the daily status page's 20-batch limit.
        for i in range(25):
            day = (date(2024, 1, 6) + timedelta(days=i)).isoformat()
            db.add(
                DailyBatch(
                    stock_id=identifier,
                    session_date=day,
                    deadline=daily.cutoff_time(day).isoformat(),
                    status="missed",
                    config={},
                )
            )
        db.commit()
    clock["value"] = stamp("2024-02-05", 16)
    base = f"/api/v1/daily-research/{identifier}/deviations"
    pages = [client.get(base, params={"offset": i, "limit": 1}).json() for i in range(4)]
    assert all(p["total"] == 4 and p["summary"]["available_count"] == 4 for p in pages)
    assert len({p["rows"][0]["task_id"] for p in pages}) == 4
    models = {m["family"]: m for m in pages[0]["summary"]["models"]}
    assert models["naive"]["sample_count"] == 2
    assert models["naive"]["mean_errors"]["close"] == pytest.approx(-0.6)
    assert models["naive"]["rmse"] == pytest.approx(math.sqrt((0.3 + 1.2) / 8))
    filtered = client.get(base, params={"start": "2024-01-04", "end": "2024-01-04", "family": "ridge"}).json()
    assert filtered["total"] == 1 and filtered["rows"][0]["family"] == "ridge"
    assert filtered["summary"]["models"][0]["sample_count"] == 1
    assert client.get(base, params={"offset": 100}).json()["rows"] == []


def test_revision_recomputes_statistics_without_rewriting_forecasts_or_scores(client, monkeypatch):
    identifier, clock = setup(client, monkeypatch)
    with SessionLocal() as db:
        daily.settle_daily(db, clock["value"])
        from backend.app.daily_points import award_points

        award_points(db, clock["value"])
        old_outcomes = {o.id: copy.deepcopy(o.metrics) for o in db.scalars(select(DailyOutcome))}
        old_points = {p.id: copy.deepcopy(p.result) for p in db.scalars(select(DailyPointRound))}
    base = f"/api/v1/daily-research/{identifier}/deviations"
    before = client.get(base).json()["rows"][0]
    with SessionLocal() as db:
        import_actual(db, identifier, close=10.7)
    after = client.get(base).json()["rows"][0]
    assert after["actual_revision_id"] != before["actual_revision_id"]
    assert after["predicted"] == before["predicted"]
    assert after["errors"]["close"] == pytest.approx(before["errors"]["close"] - 0.2)
    with SessionLocal() as db:
        assert {o.id: o.metrics for o in db.scalars(select(DailyOutcome))} == old_outcomes
        assert {p.id: p.result for p in db.scalars(select(DailyPointRound))} == old_points


def test_future_actuals_wrong_source_gaps_and_stock_isolation(client, monkeypatch):
    identifier, clock = setup(client, monkeypatch)
    base = f"/api/v1/daily-research/{identifier}/deviations"
    clock["value"] = stamp("2024-01-04", 15)
    data = client.get(base, params={"only_available": False}).json()
    assert data["summary"]["available_count"] == 0
    assert all(r["actual"] is None and r["errors"] is None for r in data["rows"])
    clock["value"] = stamp("2024-01-05", 16)
    with SessionLocal() as db:
        import_actual(db, identifier, close=10.6, provider="Eastmoney")
        other = Stock(code="000002", exchange="SZSE", name="other")
        db.add(other)
        db.add(
            DailyBatch(
                stock_id=identifier,
                session_date="2024-01-05",
                deadline=daily.cutoff_time("2024-01-05").isoformat(),
                status="missed",
                error="test-only gap",
                config={},
            )
        )
        db.commit()
        other_id = other.id
    data = client.get(base, params={"only_available": False}).json()
    assert data["total"] == 3 and data["summary"]["unavailable_count"] == 3
    assert data["rows"][0]["family"] is None and data["rows"][0]["status"] == "no_forecast"
    assert all(r["errors"] is None for r in data["rows"])
    assert client.get(f"/api/v1/daily-research/{other_id}/deviations").json()["total"] == 0
    assert client.get("/api/v1/daily-research/missing/deviations").status_code == 404


@pytest.mark.parametrize(
    "change",
    [
        {"published_at": "2024-01-04T09:15:00+08:00"},
        {"data_cutoff": "2024-01-04"},
        {"target_session": "2024-01-05"},
        {"automatic_daily": False},
    ],
)
def test_statistics_exclude_non_prospective_predictions(client, monkeypatch, change):
    identifier, _ = setup(client, monkeypatch)
    with SessionLocal() as db:
        for p in db.scalars(select(Prediction)):
            p.result = {**p.result, **change}
        db.commit()
    data = client.get(
        f"/api/v1/daily-research/{identifier}/deviations", params={"only_available": False}
    ).json()
    assert data["summary"]["available_count"] == 0
    assert all(r["errors"] is None and r["status"] == "no_forecast" for r in data["rows"])


def test_statistics_validate_filters_and_page_bounds(client, monkeypatch):
    identifier, _ = setup(client, monkeypatch)
    base = f"/api/v1/daily-research/{identifier}/deviations"
    for params in [
        {"start": "2024-02-01", "end": "2024-01-01"},
        {"start": "bad"},
        {"limit": 201},
        {"offset": -1},
    ]:
        assert client.get(base, params=params).status_code == 422
