import copy
import csv
import io
from datetime import datetime

import pytest
from sqlalchemy import select, func

from backend.app import daily_research as daily
from backend.app.db import SessionLocal
from backend.app.entities import (
    Stock,
    MarketFeed,
    DailyBatch,
    DailyTask,
    DailyOutcome,
    DailyPointRound,
    Job,
    TrainingRun,
    Model,
    Prediction,
    Snapshot,
    BarCurrent,
)
from backend.app.errors import DomainError
from backend.app.market import preview, commit_import
from backend.app.calendar import calendar
from backend.app.research import candidates


def stamp(day, hour=8):
    return datetime.fromisoformat(f"{day}T{hour:02d}:00:00+08:00")


def prepared(client, monkeypatch):
    monkeypatch.setattr(daily, "model_families", lambda: [{"id": "naive", "available": True}])
    days = [str(d.date()) for d in calendar().sessions if str(d.date()) < "2024-01-04"][-260:]
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(["date", "open", "high", "low", "close", "volume", "amount"])
    for i, day in enumerate(days):
        price = 10 + i * 0.01
        writer.writerow([day, price, price + 0.2, price - 0.2, price + 0.05, 1000, ""])
    with SessionLocal() as db:
        stock = Stock(code="000001", exchange="SZSE", name="Test only")
        db.add(stock)
        db.commit()
        batch = preview(db, stock.id, stream.getvalue().encode(), "test.csv", "1d", "raw", "shares", {})
        batch.metadata_json = {**batch.metadata_json, "source": {"provider": "Tencent"}}
        db.commit()
        commit_import(db, batch.id, "skip")
        db.add(
            MarketFeed(
                stock_id=stock.id,
                provider="Tencent",
                source_import_id=batch.id,
                last_completed_session=days[-1],
            )
        )
        db.commit()
    return stock.id, days[-1]


def make_result(target, cutoff, feedback=None):
    candle = {"open": 10.0, "high": 11.0, "low": 9.0, "close": 10.5}
    return {
        "data_cutoff": cutoff,
        "reference_price": 10.0,
        "generated_at": "2024-01-04T00:00:00+00:00",
        "models": [
            {
                "family": "naive",
                "candle": candle,
                "points": [{"date": target, "estimate": 10.5, "lower": 9.5, "upper": 11.5}],
                "ohlc_intervals": {f: {"lower": v - 1, "upper": v + 1} for f, v in candle.items()},
                "nominal_coverage": 0.9,
            }
        ],
    }


def freeze_prediction(db, batch, task, clock, candle=None):
    training = db.get(Job, task.training_job_id)
    run = db.get(TrainingRun, training.result["run_id"])
    model = Model(
        run_id=run.id,
        stock_id=batch.stock_id,
        snapshot_id=batch.snapshot_id,
        family=task.family,
        horizon="next_day",
        price_basis="raw",
        selected=False,
        parameters={},
        metrics={},
        diagnostics={},
        artifact_path="models/test-only",
        artifact_checksum="0" * 64,
        reason="test fixture",
    )
    db.add(model)
    training.status = "succeeded"
    db.commit()
    daily.reconcile_job(training.id)
    db.expire_all()
    task = db.get(DailyTask, task.id)
    prediction = db.get(Prediction, task.prediction_id)
    snapshot = db.get(Snapshot, batch.snapshot_id)
    result = make_result(batch.session_date, snapshot.cutoff)
    result["models"][0]["family"] = task.family
    if candle:
        result["models"][0]["candle"] = candle
    prediction.result = daily.finalize_daily_result(db, prediction, result)
    db.get(Job, prediction.job_id).status = "succeeded"
    db.commit()
    daily.reconcile_job(prediction.job_id)
    db.expire_all()
    return db.get(Prediction, prediction.id)


def test_schedule_and_initial_prospective_batch(client):
    with SessionLocal() as db:
        assert daily.target_session(db, stamp("2024-01-04", 6)) == "2024-01-04"
        assert daily.target_session(db, stamp("2024-01-04", 7)) == "2024-01-04"
        assert daily.target_session(db, stamp("2024-01-04", 10)) is None
        assert daily.target_session(db, stamp("2024-01-06", 8)) == "2024-01-08"
        assert daily.target_session(db, stamp("2024-01-03", 17)) == "2024-01-04"
        assert daily.target_session(db, datetime.fromisoformat("2024-01-03T15:29:59+08:00")) is None
        assert daily.target_session(db, datetime.fromisoformat("2024-01-03T15:30:00+08:00")) == "2024-01-04"
        assert daily.target_session(db, datetime.fromisoformat("2024-01-04T09:15:00+08:00")) is None
        assert daily.target_session(db, stamp("2024-01-01", 8)) == "2024-01-02"
        assert daily.target_session(db, stamp("2024-01-03", 17), True) == "2024-01-04"
        with pytest.raises(DomainError, match="截止"):
            daily.target_session(db, stamp("2024-01-04", 10), True)


def test_evening_preparation_survives_overnight_and_reuses_same_frozen_batch(client, monkeypatch):
    identifier, cutoff = prepared(client, monkeypatch)
    clock = stamp("2024-01-03", 17)
    monkeypatch.setattr(daily, "market_time", lambda at=None: at or clock)
    first = daily.daily_tick()
    assert first["batches"][0]["session_date"] == "2024-01-04"
    with SessionLocal() as db:
        batch = db.scalar(select(DailyBatch))
        frozen = (batch.id, batch.snapshot_id, copy.deepcopy(batch.config))
        assert batch.config["data_cutoff"] == cutoff
    for clock in [stamp("2024-01-04", 0), stamp("2024-01-04", 6), stamp("2024-01-04", 8)]:
        assert daily.daily_tick()["batches"][0]["status"] == "training"
    with SessionLocal() as db:
        batch = db.scalar(select(DailyBatch))
        assert (batch.id, batch.snapshot_id, batch.config) == frozen
        assert db.scalar(select(func.count()).select_from(DailyTask)) == 1
        assert db.scalar(select(func.count()).select_from(Snapshot)) == 1


def test_pending_evening_batch_retries_when_pinned_data_arrives(client, monkeypatch):
    identifier, cutoff = prepared(client, monkeypatch)
    clock = stamp("2024-01-03", 17)
    monkeypatch.setattr(daily, "market_time", lambda at=None: at or clock)
    with SessionLocal() as db:
        db.get(MarketFeed, identifier).last_completed_session = "2024-01-02"
        db.commit()
    assert daily.daily_tick()["batches"][0]["status"] == "waiting_data"
    with SessionLocal() as db:
        db.get(MarketFeed, identifier).last_completed_session = cutoff
        db.commit()
    clock = stamp("2024-01-03", 23)
    assert daily.daily_tick()["batches"][0]["status"] == "training"
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(DailyBatch)) == 1
        assert db.scalar(select(func.count()).select_from(DailyTask)) == 1


def test_gap_audit_catches_offline_sessions_without_forecasts_scores_or_weekends(client, monkeypatch):
    identifier, _ = prepared(client, monkeypatch)
    clock = stamp("2024-01-04")
    monkeypatch.setattr(daily, "market_time", lambda at=None: at or clock)
    daily.daily_tick()
    with SessionLocal() as db:
        original = db.scalar(select(DailyBatch))
        prediction = freeze_prediction(db, original, db.scalar(select(DailyTask)), clock)
        frozen = copy.deepcopy(prediction.result)
        new_stock = Stock(code="000002", exchange="SZSE", name="no monitoring history")
        db.add(new_stock)
        content = b"date,open,high,low,close,volume\n2024-01-05,10,11,9,10.5,1000\n"
        imported = preview(db, identifier, content, "actual.csv", "1d", "raw", "shares", {})
        imported.metadata_json = {**imported.metadata_json, "source": {"provider": "Tencent"}}
        db.commit()
        commit_import(db, imported.id, "replace")
    clock = stamp("2024-01-09", 10)
    result = daily.daily_tick()
    assert result["gaps_recorded"] == 3
    assert result["scored"] == result["point_rounds"] == 0
    assert daily.daily_tick()["gaps_recorded"] == 0
    with SessionLocal() as db:
        status = daily.daily_status(db, identifier)
        gaps = [b for b in status["batches"] if b["config"].get("gap_record")]
        assert {b["session_date"] for b in gaps} == {"2024-01-05", "2024-01-08", "2024-01-09"}
        assert all(b["status"] == "missed" and b["tasks"] == [] for b in gaps)
        assert all(b["coverage"]["settlement"] == "no_forecast" for b in gaps)
        actual = next(b for b in gaps if b["session_date"] == "2024-01-05")["coverage"]
        assert actual["actual_available"] and actual["actual"]["close"] == 10.5
        assert daily.daily_status(db, new_stock.id)["batches"] == []
        assert db.scalar(select(func.count()).select_from(Prediction)) == 1
        assert db.scalar(select(func.count()).select_from(DailyOutcome)) == 0
        assert db.get(Prediction, prediction.id).result == frozen
        # Even an explicit request for a recorded gap cannot enqueue historical work.
        late = daily._queue_batch(db, db.get(Stock, identifier), "2024-01-05", clock, False)
        assert late.snapshot_id is None
        assert db.scalar(select(func.count()).select_from(DailyTask)) == 1


def test_waiting_data_expiry_keeps_specific_failure_reason(client, monkeypatch):
    identifier, _ = prepared(client, monkeypatch)
    clock = stamp("2024-01-03", 17)
    monkeypatch.setattr(daily, "market_time", lambda at=None: at or clock)
    with SessionLocal() as db:
        db.get(MarketFeed, identifier).last_completed_session = "2024-01-02"
        db.commit()
    daily.daily_tick()
    clock = stamp("2024-01-04", 10)
    daily.daily_tick()
    with SessionLocal() as db:
        batch = db.scalar(select(DailyBatch))
        assert batch.status == "missed"
        assert batch.config["reason_code"] == "waiting_data_expired"
        assert "2024-01-03" in batch.error and "截止" in batch.error


def test_coverage_only_exposes_closed_actuals_from_pinned_source(client, monkeypatch):
    identifier, _ = prepared(client, monkeypatch)
    clock = stamp("2024-01-04")
    monkeypatch.setattr(daily, "market_time", lambda at=None: at or clock)
    daily.daily_tick()
    with SessionLocal() as db:
        batch = db.scalar(select(DailyBatch))
        freeze_prediction(db, batch, db.scalar(select(DailyTask)), clock)

        def actual(provider, close):
            content = f"date,open,high,low,close,volume\n2024-01-04,10,11,9,{close},1000\n".encode()
            imported = preview(db, identifier, content, "actual.csv", "1d", "raw", "shares", {})
            imported.metadata_json = {**imported.metadata_json, "source": {"provider": provider}}
            db.commit()
            commit_import(db, imported.id, "replace")

        actual("Tencent", 10.5)
        clock = stamp("2024-01-04", 15)
        coverage = daily.daily_status(db, identifier)["batches"][0]["coverage"]
        assert not coverage["actual_available"] and coverage["actual"] is None
        clock = stamp("2024-01-04", 16)
        coverage = daily.daily_status(db, identifier)["batches"][0]["coverage"]
        assert coverage["actual_available"] and coverage["actual"]["close"] == 10.5
        assert coverage["settlement"] == "settlement_pending"
        actual("Eastmoney", 10.6)
        coverage = daily.daily_status(db, identifier)["batches"][0]["coverage"]
        assert not coverage["actual_available"] and coverage["actual"] is None


def test_daily_idempotency_and_current_source_requirement(client, monkeypatch):
    identifier, cutoff = prepared(client, monkeypatch)
    clock = stamp("2024-01-04")
    monkeypatch.setattr(daily, "market_time", lambda at=None: at or clock)
    assert daily.daily_tick(at=clock)["batches"][0]["status"] == "training"
    daily.daily_tick(at=clock)
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(DailyBatch)) == 1
        assert db.scalar(select(func.count()).select_from(DailyTask)) == 1
        batch = db.scalar(select(DailyBatch))
        assert db.get(Snapshot, batch.snapshot_id).cutoff == cutoff
        assert db.scalar(select(DailyTask)).feedback["sample_count"] == 0
    # Another stock with stale data must never borrow this stock's successful update.
    with SessionLocal() as db:
        stock = Stock(code="000002", exchange="SZSE", name="stale")
        db.add(stock)
        db.commit()
        pending = daily._queue_batch(db, stock, "2024-01-04", clock, False)
        db.commit()
        assert pending.snapshot_id is None and pending.status == "waiting_data"


def test_training_completion_queues_one_prediction_and_keeps_raw_estimate(client, monkeypatch):
    identifier, _ = prepared(client, monkeypatch)
    clock = stamp("2024-01-04")
    monkeypatch.setattr(daily, "market_time", lambda at=None: at or clock)
    daily.daily_tick(at=clock)
    with SessionLocal() as db:
        batch = db.scalar(select(DailyBatch))
        task = db.scalar(select(DailyTask))
        prediction = freeze_prediction(db, batch, task, clock)
        result = prediction.result
        assert result["automatic_daily"] and result["target_session"] == "2024-01-04"
        assert result["models"][0]["uncorrected_candle"] == result["models"][0]["candle"]
        daily.reconcile_job(task.training_job_id)
        assert db.scalar(select(func.count()).select_from(Prediction)) == 1
        db.expire_all()
        assert db.get(DailyBatch, batch.id).status == "ready"


def test_finalize_rejects_late_same_day_data_and_wrong_calendar(client, monkeypatch):
    feedback = {"adjustments": {"open": 0.1, "high": -0.4, "low": 0.4, "close": -0.1}}
    config = {
        "daily_batch_id": "test",
        "daily_target_session": "2024-01-04",
        "daily_deadline": daily.cutoff_time("2024-01-04").isoformat(),
        "daily_feedback": feedback,
    }
    prediction = Prediction(config=config)
    monkeypatch.setattr(daily, "market_time", lambda: stamp("2024-01-04"))
    with SessionLocal() as db:
        result = daily.finalize_daily_result(db, prediction, make_result("2024-01-04", "2024-01-03"))
        c = result["models"][0]["candle"]
        assert c["low"] <= min(c["open"], c["close"]) <= max(c["open"], c["close"]) <= c["high"]
        assert result["models"][0]["uncorrected_candle"]["close"] == 10.5
        with pytest.raises(DomainError, match="目标日"):
            daily.finalize_daily_result(db, prediction, make_result("2024-01-04", "2024-01-04"))
        with pytest.raises(DomainError, match="目标交易日"):
            daily.finalize_daily_result(db, prediction, make_result("2024-01-05", "2024-01-03"))
        monkeypatch.setattr(daily, "market_time", lambda: stamp("2024-01-04", 10))
        with pytest.raises(DomainError, match="截止"):
            daily.finalize_daily_result(db, prediction, make_result("2024-01-04", "2024-01-03"))


def test_reward_is_measured_relative_error_not_profit():
    actual = {"open": 10.0, "high": 13.0, "low": 9.0, "close": 12.0}
    model = {
        "candle": actual,
        "uncorrected_candle": actual,
        "nominal_coverage": 0.9,
        "ohlc_intervals": {f: {"lower": v - 0.5, "upper": v + 0.5} for f, v in actual.items()},
    }
    score = daily.score_candle(model, actual, 10.0, {f: 10.0 for f in actual})
    assert score["mae"] == 0 and score["bias"] == 0 and score["reward"] == 1
    assert score["coverage"] == 1 and score["interval_score"] == 1 and score["direction_correct"]
    model = {**model, "candle": {f: v + 10 for f, v in actual.items()}}
    assert daily.score_candle(model, actual, 10.0, {f: 10.0 for f in actual})["reward"] == -1


def test_close_settlement_is_idempotent_versioned_and_does_not_change_prediction(client, monkeypatch):
    identifier, _ = prepared(client, monkeypatch)
    clock = stamp("2024-01-04")
    monkeypatch.setattr(daily, "market_time", lambda at=None: at or clock)
    daily.daily_tick(at=clock)
    with SessionLocal() as db:
        batch = db.scalar(select(DailyBatch))
        task = db.scalar(select(DailyTask))
        prediction = freeze_prediction(db, batch, task, clock)
        frozen = copy.deepcopy(prediction.result)

        def actual(close):
            content = f"date,open,high,low,close,volume\n2024-01-04,10,14,9,{close},1000\n".encode()
            imported = preview(db, identifier, content, "actual.csv", "1d", "raw", "shares", {})
            imported.metadata_json = {**imported.metadata_json, "source": {"provider": "Tencent"}}
            db.commit()
            commit_import(db, imported.id, "replace")

        actual(12)
        assert daily.settle_daily(db, stamp("2024-01-04", 15)) == 0
        assert daily.settle_daily(db, stamp("2024-01-04", 16)) == 1
        assert daily.settle_daily(db, stamp("2024-01-04", 16)) == 0
        actual(13)
        assert daily.settle_daily(db, stamp("2024-01-04", 16)) == 1
        assert db.scalar(select(func.count()).select_from(DailyOutcome)) == 2
        db.expire_all()
        assert db.get(Prediction, prediction.id).result == frozen


def test_feedback_requires_five_distinct_prior_days_and_respects_availability_time(client, monkeypatch):
    identifier, _ = prepared(client, monkeypatch)
    target = "2024-01-04"
    clock = stamp(target)
    with SessionLocal() as db:
        snapshot = daily.create_snapshot(db, identifier)
        db.commit()
        for i, day in enumerate(["2023-12-25", "2023-12-26", "2023-12-27", "2023-12-28", "2023-12-29"]):
            batch = DailyBatch(
                stock_id=identifier,
                session_date=day,
                snapshot_id=snapshot.id,
                deadline=daily.cutoff_time(day).isoformat(),
                config={},
            )
            db.add(batch)
            db.flush()
            job = Job(kind="training", payload={})
            db.add(job)
            db.flush()
            task = DailyTask(batch_id=batch.id, family="ridge", training_job_id=job.id, feedback={})
            db.add(task)
            db.flush()
            revision = db.get(BarCurrent, (identifier, "1d", "raw", day)).revision_id
            metrics = {"raw_signed_errors": {f: 2.0 for f in daily.FIELDS}, "reward": -0.5}
            db.add(
                DailyOutcome(
                    task_id=task.id,
                    actual_revision_id=revision,
                    metrics=metrics,
                    created_at="2024-01-03T09:00:00+00:00",
                )
            )
            # Same-day future availability (China 12:00) must not leak into an 08:00 run.
            db.add(
                DailyOutcome(
                    task_id=task.id,
                    actual_revision_id=db.get(
                        BarCurrent, (identifier, "1d", "raw", "2024-01-03")
                    ).revision_id,
                    metrics={"raw_signed_errors": {f: 99.0 for f in daily.FIELDS}, "reward": 1},
                    created_at="2024-01-04T04:00:00+00:00",
                )
            )
        db.commit()
        feedback = daily.feedback_for(db, identifier, "ridge", target, clock, 10.0)
        assert feedback["sample_count"] == 5 and feedback["mean_raw_bias"]["close"] == 2
        assert feedback["adjustments"]["close"] == -0.05
        assert feedback["point_sample_count"] == 0 and feedback["search_trials"] == 4
        from backend.app.daily_points import POLICY as POINT_POLICY

        for batch in db.scalars(select(DailyBatch)):
            task = db.scalar(select(DailyTask).where(DailyTask.batch_id == batch.id))
            revision = db.get(BarCurrent, (identifier, "1d", "raw", batch.session_date)).revision_id
            for created, points in [("2024-01-03T09:00:00+00:00", -2), ("2024-01-04T04:00:00+00:00", 5)]:
                db.add(
                    DailyPointRound(
                        batch_id=batch.id,
                        actual_revision_id=revision,
                        policy=POINT_POLICY,
                        cohort_checksum=("a" if points < 0 else "b") * 64,
                        created_at=created,
                        result={"entries": [{"task_id": task.id, "family": "ridge", "points": points}]},
                    )
                )
        db.commit()
        feedback = daily.feedback_for(db, identifier, "ridge", target, clock, 10.0)
        assert feedback["point_sample_count"] == 5 and feedback["mean_points"] == -2
        assert feedback["recent_points"] == -10 and feedback["search_trials"] == 6
        early = daily.feedback_for(db, identifier, "ridge", target, stamp("2024-01-03", 10), 10.0)
        assert early["sample_count"] == 0 and early["adjustments"]["close"] == 0
        assert early["point_sample_count"] == 0 and early["search_trials"] == 4


def test_previous_best_parameters_seed_future_search():
    config = {
        "seed_parameters": {"ridge": {"alpha": 7.0}},
        "ridge_alpha_min": 0.01,
        "ridge_alpha_max": 100000,
    }
    values = candidates("ridge", config)
    assert values[0] == {"alpha": 7.0} and len({str(v) for v in values}) == len(values)


def test_expired_queued_batch_cannot_turn_into_a_backfilled_prediction(client, monkeypatch):
    identifier, _ = prepared(client, monkeypatch)
    clock = stamp("2024-01-04")
    monkeypatch.setattr(daily, "market_time", lambda at=None: at or clock)
    daily.daily_tick(at=clock)
    daily.daily_tick(at=stamp("2024-01-04", 10))
    with SessionLocal() as db:
        task = db.scalar(select(DailyTask))
        assert task.status == "missed" and task.prediction_id is None
        assert db.get(Job, task.training_job_id).status == "cancelled"
        assert db.scalar(select(DailyBatch)).status == "missed"
        job_id = task.training_job_id
    daily.reconcile_job(job_id)
    with SessionLocal() as db:
        assert db.get(DailyTask, task.id).status == "missed"
        assert db.scalar(select(DailyBatch)).config["reason_code"] == "jobs_expired"


def test_real_naive_training_and_prediction_chain_without_manual_generation(client, monkeypatch):
    from backend.app.worker import execute_job

    identifier, _ = prepared(client, monkeypatch)
    clock = stamp("2024-01-04")
    monkeypatch.setattr(daily, "market_time", lambda at=None: at or clock)
    daily.daily_tick(at=clock)
    with SessionLocal() as db:
        task = db.scalar(select(DailyTask))
        train_id = task.training_job_id
    execute_job(train_id)
    with SessionLocal() as db:
        task = db.get(DailyTask, task.id)
        assert db.get(Job, train_id).status == "succeeded"
        assert task.status == "predicting" and task.prediction_id
        prediction = db.get(Prediction, task.prediction_id)
        predict_id = prediction.job_id
        assert db.get(Model, task.model_id).metrics["automatic_candidate"] is True
    execute_job(predict_id)
    with SessionLocal() as db:
        task = db.get(DailyTask, task.id)
        assert db.get(Job, predict_id).status == "succeeded" and task.status == "ready"
        result = db.get(Prediction, task.prediction_id).result
        assert result["target_session"] == "2024-01-04" and result["data_cutoff"] == "2024-01-03"
        assert (
            result["models"][0]["candle"]["low"]
            <= result["models"][0]["candle"]["close"]
            <= result["models"][0]["candle"]["high"]
        )


def test_concurrent_completion_hooks_create_only_one_child_prediction(client, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    identifier, _ = prepared(client, monkeypatch)
    clock = stamp("2024-01-04")
    monkeypatch.setattr(daily, "market_time", lambda at=None: at or clock)
    daily.daily_tick(at=clock)
    with SessionLocal() as db:
        task = db.scalar(select(DailyTask))
        batch = db.get(DailyBatch, task.batch_id)
        job = db.get(Job, task.training_job_id)
        run = db.get(TrainingRun, job.result["run_id"])
        db.add(
            Model(
                run_id=run.id,
                stock_id=identifier,
                snapshot_id=batch.snapshot_id,
                family="naive",
                horizon="next_day",
                price_basis="raw",
                selected=False,
                parameters={},
                metrics={},
                diagnostics={},
                artifact_path="models/test-only",
                artifact_checksum="0" * 64,
                reason="test fixture",
            )
        )
        job.status = "succeeded"
        db.commit()
        job_id = job.id
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(daily.reconcile_job, [job_id, job_id]))
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Prediction)) == 1
        assert db.scalar(select(DailyTask)).status == "predicting"


def test_news_scheduler_is_independent_of_missing_market_calendar(client, monkeypatch):
    from backend.app import market_cycle as cycle, market_updates

    identifier, _ = prepared(client, monkeypatch)
    clock = stamp("2027-01-04")
    monkeypatch.setattr(cycle, "market_time", lambda: clock)
    monkeypatch.setattr(daily, "market_time", lambda at=None: at or clock)
    original = market_updates.market_time
    monkeypatch.setattr(market_updates, "market_time", lambda at=None: original(at or clock))
    result = cycle.market_cycle()
    assert result["market"]["code"] == "calendar_unavailable"
    assert result["daily"]["code"] == "calendar_unavailable"
    assert len(result["intelligence_jobs"]) == 1


def test_repeated_scheduler_handles_two_existing_news_jobs(client, monkeypatch):
    from backend.app import market_cycle as cycle, market_updates

    identifier, _ = prepared(client, monkeypatch)
    second = client.post(
        "/api/v1/stocks", json={"code": "600036", "name": "other test stock", "exchange": "SSE"}
    )
    assert second.status_code == 201
    clock = stamp("2027-01-04")
    monkeypatch.setattr(cycle, "market_time", lambda: clock)
    monkeypatch.setattr(daily, "market_time", lambda at=None: at or clock)
    original = market_updates.market_time
    monkeypatch.setattr(market_updates, "market_time", lambda at=None: original(at or clock))
    first = cycle.market_cycle()
    repeated = cycle.market_cycle()
    assert len(first["intelligence_jobs"]) == 2
    assert first["intelligence_jobs"] == repeated["intelligence_jobs"]
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Job).where(Job.kind == "intelligence")) == 2


def test_points_are_idempotent_revisioned_stock_isolated_and_keep_originals(client, monkeypatch):
    from backend.app.daily_points import award_points

    identifier, _ = prepared(client, monkeypatch)
    offsets = {"naive": 0.1, "arima": 0.1, "ridge": 0.02, "sarima": 0.3}
    monkeypatch.setattr(daily, "model_families", lambda: [{"id": f, "available": True} for f in offsets])
    clock = stamp("2024-01-04")
    monkeypatch.setattr(daily, "market_time", lambda at=None: at or clock)
    daily.daily_tick(at=clock)
    with SessionLocal() as db:
        batch = db.scalar(select(DailyBatch))
        base = {"open": 10.0, "high": 11.0, "low": 9.0, "close": 10.5}
        frozen = {}
        for task in list(db.scalars(select(DailyTask))):
            p = freeze_prediction(
                db, batch, task, clock, {f: v + offsets[task.family] for f, v in base.items()}
            )
            frozen[p.id] = copy.deepcopy(p.result)

        def actual(offset):
            values = [base[f] + offset for f in ["open", "high", "low", "close"]]
            content = (
                "date,open,high,low,close,volume\n2024-01-04," + ",".join(map(str, values)) + ",1000\n"
            ).encode()
            imported = preview(db, identifier, content, "actual.csv", "1d", "raw", "shares", {})
            imported.metadata_json = {**imported.metadata_json, "source": {"provider": "Tencent"}}
            db.commit()
            commit_import(db, imported.id, "replace")

        actual(0)
        assert award_points(db, stamp("2024-01-04", 15)) == 0
        assert daily.settle_daily(db, stamp("2024-01-04", 16)) == 4
        old_outcomes = {o.id: copy.deepcopy(o.metrics) for o in db.scalars(select(DailyOutcome))}
        assert award_points(db, stamp("2024-01-04", 16)) == 1
        assert award_points(db, stamp("2024-01-04", 16)) == 0
        status = daily.daily_status(db, identifier)
        assert {e["family"]: e["points"] for e in status["leaderboard"]} == {
            "ridge": 2,
            "arima": 0,
            "naive": 0,
            "sarima": -2,
        }
        other = Stock(code="000002", exchange="SZSE", name="other test")
        db.add(other)
        db.commit()
        assert daily.daily_status(db, other.id)["leaderboard"] == []

        actual(0.3)
        assert daily.settle_daily(db, stamp("2024-01-04", 16)) == 4
        assert award_points(db, stamp("2024-01-04", 16)) == 1
        assert db.scalar(select(func.count()).select_from(DailyPointRound)) == 2
        status = daily.daily_status(db, identifier)
        assert all(e["sessions"] == 1 for e in status["leaderboard"])
        assert {e["family"]: e["points"] for e in status["leaderboard"]} == {
            "ridge": -2,
            "arima": 0,
            "naive": 0,
            "sarima": 5,
        }
        db.expire_all()
        for key, saved in frozen.items():
            assert db.get(Prediction, key).result == saved
        for key, saved in old_outcomes.items():
            assert db.get(DailyOutcome, key).metrics == saved


@pytest.mark.parametrize(
    "change",
    [
        {"published_at": "2024-01-04T09:15:00+08:00"},
        {"data_cutoff": "2024-01-04"},
        {"target_session": "2024-01-05"},
    ],
)
def test_points_reject_non_prospective_results(client, monkeypatch, change):
    from backend.app.daily_points import award_points

    identifier, _ = prepared(client, monkeypatch)
    clock = stamp("2024-01-04")
    monkeypatch.setattr(daily, "market_time", lambda at=None: at or clock)
    daily.daily_tick(at=clock)
    with SessionLocal() as db:
        batch, task = db.scalar(select(DailyBatch)), db.scalar(select(DailyTask))
        prediction = freeze_prediction(db, batch, task, clock)
        content = b"date,open,high,low,close,volume\n2024-01-04,10,11,9,10.5,1000\n"
        imported = preview(db, identifier, content, "actual.csv", "1d", "raw", "shares", {})
        imported.metadata_json = {**imported.metadata_json, "source": {"provider": "Tencent"}}
        db.commit()
        commit_import(db, imported.id, "replace")
        daily.settle_daily(db, stamp("2024-01-04", 16))
        prediction.result = {**prediction.result, **change}
        db.commit()
        assert award_points(db, stamp("2024-01-04", 16)) == 0
