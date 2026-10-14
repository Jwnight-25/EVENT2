"""Prospective daily OHLC forecasts and causal, auditable error feedback."""

from datetime import datetime, time, timezone
import math
from sqlalchemy import select

from .calendar import future_dates, is_open
from .db import SessionLocal, now
from .entities import (
    Stock,
    MarketFeed,
    DailyBatch,
    DailyTask,
    DailyOutcome,
    Job,
    TrainingRun,
    Model,
    Prediction,
    Snapshot,
    BarCurrent,
    BarRevision,
    ImportBatch,
)
from .errors import DomainError, require
from .market import current_bars, create_snapshot, load_snapshot
from .market_updates import SHANGHAI, market_time, latest_closed_session, update_lock
from .model_catalog import model_families
from .schemas import TrainingRequest
from .storage import storage_lock

FIELDS = ("open", "high", "low", "close")
START = time(7, 0)
DEADLINE = time(9, 15)
POLICY = "daily-error-feedback-v1"


def cutoff_time(day):
    return datetime.combine(datetime.fromisoformat(day).date(), DEADLINE, SHANGHAI)


def target_session(db, stamp, bootstrap=False):
    if bootstrap:
        target = future_dates(db, latest_closed_session(db, stamp), 1)[0]
        if stamp >= cutoff_time(target):
            raise DomainError("当前已过盘前截止，等待本次收盘后才能准备下一交易日")
        return target
    day = stamp.date().isoformat()
    return day if START <= stamp.time() < DEADLINE and is_open(db, day) else None


def feedback_for(db, stock_id, family, target, stamp, reference):
    from .daily_points import latest_rounds, POLICY as POINT_POLICY

    rows = db.execute(
        select(DailyOutcome, DailyTask, DailyBatch)
        .join(DailyTask, DailyOutcome.task_id == DailyTask.id)
        .join(DailyBatch, DailyTask.batch_id == DailyBatch.id)
        .where(
            DailyBatch.stock_id == stock_id,
            DailyBatch.session_date < target,
            DailyTask.family == family,
            DailyOutcome.created_at <= stamp.astimezone(timezone.utc).isoformat(),
        )
        .order_by(DailyOutcome.created_at.desc(), DailyOutcome.id.desc())
        .limit(200)
    ).all()
    # A revised actual quote is another outcome version, not another trading day.
    unique = {}
    for outcome, task, batch in rows:
        unique.setdefault(task.id, (outcome, batch))
    sample = sorted(unique.values(), key=lambda item: item[1].session_date, reverse=True)[:20]
    bias = {field: 0.0 for field in FIELDS}
    if len(sample) >= 5:
        for field in FIELDS:
            bias[field] = sum(o.metrics["raw_signed_errors"][field] for o, _ in sample) / len(sample)
    adjustment = {
        field: max(-reference * 0.005, min(reference * 0.005, -bias[field] * 0.25)) for field in FIELDS
    }
    rewards = [o.metrics["reward"] for o, _ in sample]
    mean_reward = sum(rewards) / len(rewards) if rewards else None
    point_entries = [
        entry
        for round_, _ in latest_rounds(db, stock_id, as_of=stamp, before_session=target)
        for entry in round_.result["entries"]
        if entry["family"] == family
    ][:20]
    mean_points = sum(e["points"] for e in point_entries) / len(point_entries) if point_entries else None
    return {
        "policy": POLICY,
        "sample_count": len(sample),
        "minimum_bias_samples": 5,
        "signed_error_definition": "prediction - actual",
        "mean_raw_bias": bias,
        "adjustments": adjustment,
        "mean_reward": mean_reward,
        "points_policy": POINT_POLICY,
        "point_sample_count": len(point_entries),
        "mean_points": mean_points,
        "recent_points": sum(e["points"] for e in point_entries),
        "outcome_ids": [o.id for o, _ in sample],
        "latest_feedback_session": max((b.session_date for _, b in sample), default=None),
        "frozen_at": stamp.astimezone(SHANGHAI).isoformat(),
        "search_trials": 6 if len(point_entries) >= 5 and mean_points < 0 else 4,
        "note": "只使用已收盘且当时已入库的前序日期；至少5个真实盘前样本才校正，幅度为均值偏差的25%，上限参考价0.5%",
    }


def training_config(db, stock_id, family, target, stamp, snapshot):
    reference = load_snapshot(snapshot)[-1]["close"]
    feedback = feedback_for(db, stock_id, family, target, stamp, reference)
    previous = db.scalar(
        select(Model)
        .join(Snapshot, Model.snapshot_id == Snapshot.id)
        .where(
            Model.stock_id == stock_id,
            Model.family == family,
            Model.horizon == "next_day",
            Model.price_basis == "raw",
            Snapshot.cutoff <= snapshot.cutoff,
        )
        .order_by(Model.created_at.desc())
        .limit(1)
    )
    config = TrainingRequest(
        stock_id=stock_id,
        horizons=["next_day"],
        families=[family],
        price_basis="raw",
        trials_per_family=feedback["search_trials"],
        time_budget_seconds=300,
        evaluation_samples=64,
        tuning_samples=64,
    ).model_dump()
    if previous:
        config["seed_parameters"] = {family: previous.parameters}
        if family == "ridge" and feedback["point_sample_count"] >= 5:
            alpha = previous.parameters["alpha"]
            factor = 100 if feedback["mean_points"] < 0 else 10
            config["ridge_alpha_min"] = max(0.01, alpha / factor)
            config["ridge_alpha_max"] = min(100000, alpha * factor)
    config["daily_target_session"] = target
    config["daily_deadline"] = cutoff_time(target).isoformat()
    config["feedback_policy"] = feedback
    return config, feedback


def refresh_batch(db, batch):
    tasks = list(db.scalars(select(DailyTask).where(DailyTask.batch_id == batch.id)))
    if not tasks:
        return
    statuses = {task.status for task in tasks}
    if statuses.intersection({"training", "predicting"}):
        batch.status = "training" if "training" in statuses else "predicting"
    elif statuses == {"ready"}:
        batch.status, batch.error = "ready", None
    elif "ready" in statuses:
        batch.status = "partial"
        batch.error = "部分模型未完成；已保留其失败原因及成功模型结果"
    else:
        batch.status = "failed"
        batch.error = "本批模型未生成有效盘前结果，请查看逐模型原因"


def _queue_batch(db, stock, target, stamp, bootstrap):
    batch = db.scalar(
        select(DailyBatch).where(DailyBatch.stock_id == stock.id, DailyBatch.session_date == target)
    )
    if not batch:
        batch = DailyBatch(
            stock_id=stock.id,
            session_date=target,
            deadline=cutoff_time(target).isoformat(),
            config={"bootstrap": bootstrap},
        )
        db.add(batch)
        db.flush()
    if batch.snapshot_id or stamp >= cutoff_time(target):
        return batch
    previous_day = latest_closed_session(db, datetime.fromisoformat(target).replace(tzinfo=SHANGHAI))
    feed = db.get(MarketFeed, stock.id)
    rows = current_bars(db, stock.id, "1d", "raw")
    if (
        not feed
        or feed.last_completed_session != previous_day
        or not rows
        or rows[-1]["time"] != previous_day
    ):
        batch.error = f"等待固定来源补齐上一交易日{previous_day}，不使用过期数据冒充盘前预测"
        return batch
    if len(rows) < 220:
        batch.status, batch.error = "failed", "完整日线不足220条，暂不能训练"
        return batch
    snapshot = create_snapshot(db, stock.id, "raw")
    batch.snapshot_id = snapshot.id
    catalog = model_families()
    available = [item["id"] for item in catalog if item["available"]]
    batch.config = {
        **batch.config,
        "prepared_at": stamp.astimezone(SHANGHAI).isoformat(),
        "data_cutoff": previous_day,
        "families": available,
        "unavailable": [item for item in catalog if not item["available"]],
        "budget_per_family_seconds": 300,
        "prediction_budget_seconds": 180,
    }
    batch.status, batch.error = "training", None
    for family in available:
        config, feedback = training_config(db, stock.id, family, target, stamp, snapshot)
        job = Job(
            kind="training", payload={"request": config}, idempotency_key=f"daily-train:{batch.id}:{family}"
        )
        db.add(job)
        db.flush()
        run = TrainingRun(stock_id=stock.id, snapshot_id=snapshot.id, job_id=job.id, config=config)
        db.add(run)
        db.flush()
        job.result = {"run_id": run.id}
        task = DailyTask(batch_id=batch.id, family=family, training_job_id=job.id, feedback=feedback)
        db.add(task)
    return batch


def reconcile_job(job_id):
    """Called by the worker and scheduler; only terminal training jobs create predictions."""
    with SessionLocal() as db:
        from .advice import serial_write

        serial_write(db)
        task = db.scalar(select(DailyTask).where(DailyTask.training_job_id == job_id).with_for_update())
        training = task is not None
        if not task:
            task = db.scalar(
                select(DailyTask)
                .join(Prediction, DailyTask.prediction_id == Prediction.id)
                .where(Prediction.job_id == job_id)
                .with_for_update()
            )
        if not task:
            return
        batch = db.scalar(select(DailyBatch).where(DailyBatch.id == task.batch_id).with_for_update())
        job = db.get(Job, job_id)
        if job.status in ("queued", "running"):
            return
        if job.status != "succeeded":
            task.status, task.error = "failed", job.error or "任务取消或未完成"
        elif training and not task.prediction_id:
            if market_time() >= datetime.fromisoformat(batch.deadline):
                task.status, task.error = "missed", "已过09:15截止，未补造事后盘前预测"
            else:
                model = db.scalar(
                    select(Model).where(
                        Model.run_id == job.result["run_id"],
                        Model.family == task.family,
                        Model.horizon == "next_day",
                    )
                )
                if not model:
                    task.status, task.error = "failed", "本类训练未保存可用模型，不能用旧版本替代"
                else:
                    task.model_id = model.id
                    config = {
                        "stock_id": batch.stock_id,
                        "price_basis": "raw",
                        "horizon": "next_day",
                        "model_ids": [model.id],
                        "with_ai": False,
                        "allow_unvalidated": True,
                        "reference_price": None,
                        "observation_start": None,
                        "daily_batch_id": batch.id,
                        "daily_task_id": task.id,
                        "daily_target_session": batch.session_date,
                        "daily_deadline": batch.deadline,
                        "prediction_budget_seconds": 180,
                        "daily_feedback": task.feedback,
                    }
                    child = Job(
                        kind="prediction",
                        payload={"request": config},
                        idempotency_key=f"daily-predict:{task.id}",
                    )
                    db.add(child)
                    db.flush()
                    prediction = Prediction(
                        stock_id=batch.stock_id,
                        snapshot_id=batch.snapshot_id,
                        job_id=child.id,
                        horizon="next_day",
                        config=config,
                    )
                    db.add(prediction)
                    db.flush()
                    child.result = {"prediction_id": prediction.id}
                    task.prediction_id, task.status = prediction.id, "predicting"
        elif not training:
            prediction = db.get(Prediction, task.prediction_id)
            task.status = "ready" if prediction.result.get("models") else "failed"
            task.error = None if task.status == "ready" else "没有有效的盘前预测结果"
        refresh_batch(db, batch)
        db.commit()


def finalize_daily_result(db, prediction, result):
    config = prediction.config
    if not config.get("daily_batch_id"):
        return result
    stamp = market_time()
    deadline = datetime.fromisoformat(config["daily_deadline"])
    if stamp >= deadline or result["data_cutoff"] >= config["daily_target_session"]:
        raise DomainError("已过盘前截止或数据包含目标日，拒绝发布为盘前预测", "preopen_expired", 409)
    if any(
        point["date"] != config["daily_target_session"] for m in result["models"] for point in m["points"]
    ):
        raise DomainError("目标交易日与盘前批次不符", "daily_target_mismatch", 409)
    feedback = config["daily_feedback"]
    for model in result["models"]:
        original = dict(model["candle"])
        corrected = {field: max(0.01, original[field] + feedback["adjustments"][field]) for field in FIELDS}
        corrected["high"] = max(corrected["high"], corrected["open"], corrected["close"])
        corrected["low"] = min(corrected["low"], corrected["open"], corrected["close"])
        model["uncorrected_candle"], model["candle"] = original, corrected
        model["daily_feedback"] = feedback
        for field in FIELDS:
            delta = corrected[field] - original[field]
            interval = model["ohlc_intervals"][field]
            interval["lower"] = max(0.01, interval["lower"] + delta)
            interval["upper"] = max(interval["lower"], interval["upper"] + delta)
        point = model["points"][0]
        point.update(estimate=corrected["close"], **model["ohlc_intervals"]["close"])
        reference = result["reference_price"]
        model["target_upside"] = {
            "lower": point["lower"] / reference - 1,
            "median": point["estimate"] / reference - 1,
            "upper": point["upper"] / reference - 1,
        }
    return {
        **result,
        "automatic_daily": True,
        "daily_batch_id": config["daily_batch_id"],
        "target_session": config["daily_target_session"],
        "published_at": stamp.isoformat(),
        "preopen_deadline": deadline.isoformat(),
        "feedback_policy": POLICY,
    }


def score_candle(model, actual, previous_close, baseline):
    predicted, raw = model["candle"], model.get("uncorrected_candle", model["candle"])
    errors = {f: predicted[f] - actual[f] for f in FIELDS}
    raw_errors = {f: raw[f] - actual[f] for f in FIELDS}
    mae = sum(abs(v) for v in errors.values()) / 4
    baseline_mae = sum(abs(baseline[f] - actual[f]) for f in FIELDS) / 4
    alpha = 1 - model["nominal_coverage"]
    scores, covered = [], {}
    for field in FIELDS:
        lo, hi = (model["ohlc_intervals"][field][k] for k in ("lower", "upper"))
        value = actual[field]
        scores.append(hi - lo + (2 / alpha) * (max(lo - value, 0) + max(value - hi, 0)))
        covered[field] = lo <= value <= hi
    reward = max(-1.0, min(1.0, (baseline_mae - mae) / max(baseline_mae, previous_close * 0.001)))

    def sign(v):
        return 0 if abs(v) < 1e-8 else (1 if v > 0 else -1)

    return {
        "policy": POLICY,
        "signed_errors": errors,
        "raw_signed_errors": raw_errors,
        "bias": sum(errors.values()) / 4,
        "mae": mae,
        "raw_mae": sum(abs(v) for v in raw_errors.values()) / 4,
        "rmse": math.sqrt(sum(v * v for v in errors.values()) / 4),
        "mape": sum(abs(errors[f]) / actual[f] for f in FIELDS) / 4,
        "close_error": errors["close"],
        "interval_covered": covered,
        "coverage": sum(covered.values()) / 4,
        "interval_score": sum(scores) / 4,
        "direction_correct": sign(predicted["close"] - previous_close)
        == sign(actual["close"] - previous_close),
        "baseline_mae": baseline_mae,
        "baseline_candle": baseline,
        "reward": reward,
        "reward_note": "相对冻结基准的OHLC MAE优势，截断至[-1,1]；不是收益、盈利概率或强化学习策略成绩",
    }


def settle_daily(db, stamp):
    closed = latest_closed_session(db, stamp)
    scored = 0
    for batch in db.scalars(
        select(DailyBatch).where(DailyBatch.session_date <= closed, DailyBatch.snapshot_id.is_not(None))
    ):
        current = db.get(BarCurrent, (batch.stock_id, "1d", "raw", batch.session_date))
        if not current:
            continue
        actual_bar = db.get(BarRevision, current.revision_id)
        feed = db.get(MarketFeed, batch.stock_id)
        imported = db.get(ImportBatch, actual_bar.import_id)
        if not feed or imported.metadata_json.get("source", {}).get("provider") != feed.provider:
            continue
        tasks = list(
            db.scalars(select(DailyTask).where(DailyTask.batch_id == batch.id, DailyTask.status == "ready"))
        )
        previous_close = load_snapshot(db.get(Snapshot, batch.snapshot_id))[-1]["close"]
        baseline = {field: previous_close for field in FIELDS}
        naive = next((t for t in tasks if t.family == "naive"), None)
        if naive:
            baseline = db.get(Prediction, naive.prediction_id).result["models"][0]["candle"]
        actual = {field: float(getattr(actual_bar, field)) for field in FIELDS}
        for task in tasks:
            prediction = db.get(Prediction, task.prediction_id)
            result = prediction.result
            if not result.get("automatic_daily") or datetime.fromisoformat(
                result["published_at"]
            ) >= cutoff_time(batch.session_date):
                continue
            if db.scalar(
                select(DailyOutcome).where(
                    DailyOutcome.task_id == task.id, DailyOutcome.actual_revision_id == actual_bar.id
                )
            ):
                continue
            metrics = score_candle(result["models"][0], actual, previous_close, baseline)
            db.add(
                DailyOutcome(
                    task_id=task.id,
                    actual_revision_id=actual_bar.id,
                    metrics={
                        **metrics,
                        "actual": actual,
                        "target_session": batch.session_date,
                        "baseline_family": "naive" if naive else "previous_close_flat",
                    },
                )
            )
            scored += 1
    db.commit()
    return scored


def daily_tick(at=None, bootstrap=False):
    from .daily_points import award_points

    stamp = market_time(at)
    with update_lock(), storage_lock(exclusive=True), SessionLocal() as db:
        target = target_session(db, stamp, bootstrap)
        expired = list(
            db.scalars(
                select(DailyBatch).where(
                    DailyBatch.deadline <= stamp.isoformat(),
                    DailyBatch.status.in_(["waiting_data", "training", "predicting"]),
                )
            )
        )
        for batch in expired:
            batch.status, batch.error = "missed", "错过盘前窗口，保留已有结果，不补造盘前预测"
            for task in db.scalars(
                select(DailyTask).where(
                    DailyTask.batch_id == batch.id, DailyTask.status.in_(["training", "predicting"])
                )
            ):
                prediction = db.get(Prediction, task.prediction_id) if task.prediction_id else None
                job = db.get(Job, prediction.job_id if prediction else task.training_job_id)
                job.cancel_requested = True
                if job.status == "queued":
                    job.status, job.finished_at, job.error = "cancelled", now(), "已过盘前截止"
                task.status, task.error = "missed", "已过盘前截止"
            if db.scalar(
                select(DailyTask).where(DailyTask.batch_id == batch.id, DailyTask.status == "ready")
            ):
                batch.status = "partial"
                batch.error = "部分模型按时保存，其他模型错过盘前截止"
        db.commit()
        # Catch-up actuals must be scored before freezing the next training configuration.
        scored = settle_daily(db, stamp)
        point_rounds = award_points(db, stamp)
        targets = {(s.id, target) for s in db.scalars(select(Stock))} if target else set()
        for batch in db.scalars(select(DailyBatch).where(DailyBatch.status == "waiting_data")):
            if batch.config.get("bootstrap") and stamp < datetime.fromisoformat(batch.deadline):
                targets.add((batch.stock_id, batch.session_date))
        result = []
        for stock_id, day in sorted(targets):
            batch = _queue_batch(db, db.get(Stock, stock_id), day, stamp, bootstrap)
            result.append(
                {"stock_id": stock_id, "session_date": day, "status": batch.status, "error": batch.error}
            )
        db.commit()
    # Outside the exclusive lock: reconciliation itself may be invoked in the worker.
    with storage_lock(), SessionLocal() as db:
        identifiers = list(
            db.scalars(select(DailyTask.training_job_id).where(DailyTask.status == "training"))
        )
        predictions = list(
            db.scalars(
                select(Prediction.job_id)
                .join(DailyTask, DailyTask.prediction_id == Prediction.id)
                .where(DailyTask.status == "predicting")
            )
        )
        for job_id in identifiers + predictions:
            reconcile_job(job_id)
    return {
        "batches": result,
        "scored": scored,
        "point_rounds": point_rounds,
        "checked_at": stamp.isoformat(),
    }


def daily_status(db, stock_id):
    from .daily_points import latest_rounds, leaderboard, RULE

    require(db.get(Stock, stock_id), "股票不存在")
    rounds = latest_rounds(db, stock_id)
    point_rounds = {batch.id: round_ for round_, batch in rounds}
    batches = list(
        db.scalars(
            select(DailyBatch)
            .where(DailyBatch.stock_id == stock_id)
            .order_by(DailyBatch.session_date.desc())
            .limit(20)
        )
    )
    result = []
    for batch in batches:
        point_round = point_rounds.get(batch.id)
        entries = {e["task_id"]: e for e in point_round.result["entries"]} if point_round else {}
        tasks = []
        for task in db.scalars(
            select(DailyTask).where(DailyTask.batch_id == batch.id).order_by(DailyTask.family)
        ):
            prediction = db.get(Prediction, task.prediction_id) if task.prediction_id else None
            outcome = db.scalar(
                select(DailyOutcome)
                .where(DailyOutcome.task_id == task.id)
                .order_by(DailyOutcome.created_at.desc(), DailyOutcome.id.desc())
                .limit(1)
            )
            tasks.append(
                {
                    "id": task.id,
                    "family": task.family,
                    "status": task.status,
                    "error": task.error,
                    "model_id": task.model_id,
                    "prediction_id": task.prediction_id,
                    "training_job_id": task.training_job_id,
                    "feedback": task.feedback,
                    "prediction": prediction.result if prediction else None,
                    "outcome": outcome.metrics if outcome else None,
                    "score": entries.get(task.id),
                }
            )
        result.append(
            {
                "id": batch.id,
                "session_date": batch.session_date,
                "status": batch.status,
                "error": batch.error,
                "deadline": batch.deadline,
                "config": batch.config,
                "tasks": tasks,
                "points_awarded_at": point_round.created_at if point_round else None,
            }
        )
    return {
        "stock_id": stock_id,
        "schedule": "交易日07:00开始；09:15前冻结预测；15:30后核对真实OHLC",
        "policy": POLICY,
        "scoring_rule": RULE,
        "leaderboard": leaderboard(rounds),
        "points_through": rounds[0][1].session_date if rounds else None,
        "batches": result,
    }
