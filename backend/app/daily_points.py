"""User-defined 5/2/0/-2 points for frozen, prospective daily OHLC forecasts."""

from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
from sqlalchemy import select

from .entities import DailyBatch, DailyTask, DailyOutcome, DailyPointRound, Prediction, BarCurrent
from .market_updates import latest_closed_session

POLICY = "daily-ohlc-rank-points-v1"
FIELDS = ("open", "high", "low", "close")
RULE = {
    "policy": POLICY,
    "metric": "四项OHLC按分四舍五入后的平均绝对误差，越小越接近",
    "price_precision": "0.01 CNY; ROUND_HALF_UP",
    "best": 2,
    "exact": 5,
    "worst": -2,
    "other": 0,
    "note": "四价全对+5（不叠加+2）；并列最好/最差同分；全部误差相同且非全对、或仅一项非全对模型时均0分；包括naive基准，失败无预测不计分",
}


def price_cents(value):
    number = Decimal(str(value))
    if not number.is_finite() or number <= 0:
        raise ValueError("评分价格必须为有限正数")
    return int((number * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def rank_predictions(candidates, actual):
    """Rank integer cent errors to avoid floating point ties or exact-match ambiguity."""
    actual_cents = {f: price_cents(actual[f]) for f in FIELDS}
    entries = []
    for candidate in candidates:
        predicted = {f: price_cents(candidate["candle"][f]) for f in FIELDS}
        errors = {f: abs(predicted[f] - actual_cents[f]) for f in FIELDS}
        total = sum(errors.values())
        entries.append(
            {
                **{k: candidate[k] for k in ("task_id", "model_id", "family", "outcome_id")},
                "predicted_cents": predicted,
                "absolute_errors_cents": errors,
                "sum_absolute_error_cents": total,
                "ranking_mae": total / 400,
            }
        )
    if not entries:
        return {"rule": RULE, "actual_cents": actual_cents, "entries": []}
    best = min(e["sum_absolute_error_cents"] for e in entries)
    worst = max(e["sum_absolute_error_cents"] for e in entries)
    for entry in entries:
        error = entry["sum_absolute_error_cents"]
        if error == 0:
            points, reason = 5, "四项价格全对"
        elif best == worst:
            points, reason = 0, "没有可区分的最好与最差"
        elif error == best:
            points, reason = 2, "四项平均误差最小"
        elif error == worst:
            points, reason = -2, "四项平均误差最大"
        else:
            points, reason = 0, "其余模型"
        entry.update(
            points=points,
            reason=reason,
            rank=1 + sum(e["sum_absolute_error_cents"] < error for e in entries),
        )
    return {"rule": RULE, "actual_cents": actual_cents, "entries": entries}


def latest_rounds(db, stock_id, as_of=None, before_session=None):
    query = (
        select(DailyPointRound, DailyBatch)
        .join(DailyBatch, DailyBatch.id == DailyPointRound.batch_id)
        .where(DailyBatch.stock_id == stock_id, DailyPointRound.policy == POLICY)
    )
    if as_of:
        query = query.where(DailyPointRound.created_at <= as_of.astimezone(timezone.utc).isoformat())
    if before_session:
        query = query.where(DailyBatch.session_date < before_session)
    query = query.order_by(DailyPointRound.created_at.desc(), DailyPointRound.id.desc())
    unique = {}
    for round_, batch in db.execute(query):
        unique.setdefault(batch.id, (round_, batch))
    return sorted(unique.values(), key=lambda item: item[1].session_date, reverse=True)


def award_points(db, stamp):
    """Append a round per actual revision/cohort; caller holds the storage/update locks."""
    closed = latest_closed_session(db, stamp)
    added = 0
    for batch in db.scalars(select(DailyBatch).where(DailyBatch.session_date <= closed)):
        current = db.get(BarCurrent, (batch.stock_id, "1d", "raw", batch.session_date))
        if not current:
            continue
        candidates, actual = [], None
        outcomes = db.execute(
            select(DailyOutcome, DailyTask)
            .join(DailyTask, DailyTask.id == DailyOutcome.task_id)
            .where(DailyTask.batch_id == batch.id, DailyOutcome.actual_revision_id == current.revision_id)
            .order_by(DailyTask.family, DailyTask.id)
        ).all()
        for outcome, task in outcomes:
            prediction = db.get(Prediction, task.prediction_id) if task.prediction_id else None
            result = prediction.result if prediction else {}
            if (
                task.status != "ready"
                or not result.get("automatic_daily")
                or result.get("target_session") != batch.session_date
                or result.get("data_cutoff", batch.session_date) >= batch.session_date
                or datetime.fromisoformat(result["published_at"]) >= datetime.fromisoformat(batch.deadline)
                or len(result.get("models", [])) != 1
            ):
                continue
            actual = outcome.metrics["actual"]
            candidates.append(
                {
                    "task_id": task.id,
                    "model_id": task.model_id,
                    "family": task.family,
                    "outcome_id": outcome.id,
                    "candle": result["models"][0]["candle"],
                }
            )
        if not candidates:
            continue
        ranked = rank_predictions(candidates, actual)
        checksum = hashlib.sha256(json.dumps(ranked, sort_keys=True).encode()).hexdigest()
        prior = db.scalar(
            select(DailyPointRound).where(
                DailyPointRound.batch_id == batch.id,
                DailyPointRound.actual_revision_id == current.revision_id,
                DailyPointRound.policy == POLICY,
                DailyPointRound.cohort_checksum == checksum,
            )
        )
        if prior:
            continue
        db.add(
            DailyPointRound(
                batch_id=batch.id,
                actual_revision_id=current.revision_id,
                policy=POLICY,
                cohort_checksum=checksum,
                result={**ranked, "target_session": batch.session_date},
            )
        )
        added += 1
    db.commit()
    return added


def leaderboard(rounds):
    totals = {}
    for round_, batch in rounds:
        for entry in round_.result["entries"]:
            family = entry["family"]
            total = totals.setdefault(
                family, {"family": family, "points": 0, "sessions": 0, "exact": 0, "wins": 0, "losses": 0}
            )
            points = entry["points"]
            total["points"] += points
            total["sessions"] += 1
            total["exact"] += points == 5
            total["wins"] += points > 0
            total["losses"] += points < 0
    return sorted(totals.values(), key=lambda item: (-item["points"], item["family"]))
