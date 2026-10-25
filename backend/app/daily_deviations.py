"""Read-only daily errors against the current, pinned-source actual revision."""

from datetime import date, datetime, time
import math
from sqlalchemy import and_, select

from .daily_research import FIELDS, cutoff_time
from .entities import (
    Stock,
    DailyBatch,
    DailyTask,
    Prediction,
    BarCurrent,
    BarRevision,
    ImportBatch,
    MarketFeed,
)
from .errors import DomainError, require
from .market_updates import SHANGHAI, market_time


def valid_candle(value):
    return isinstance(value, dict) and all(
        isinstance(value.get(f), (int, float))
        and not isinstance(value[f], bool)
        and math.isfinite(value[f])
        and value[f] > 0
        for f in FIELDS
    )


def deviation_row(row, stamp):
    actual = {
        f: float(row._mapping[f"actual_{f}"]) if row._mapping[f"actual_{f}"] is not None else None
        for f in FIELDS
    }
    closed = stamp >= datetime.combine(date.fromisoformat(row.session_date), time(15, 30), SHANGHAI)
    actual_ok = closed and valid_candle(actual) and row.provider and row.actual_provider == row.provider
    eligible = False
    try:
        eligible = (
            row.task_status == "ready"
            and row.automatic_daily is True
            and row.price_basis == "raw"
            and row.target_session == row.session_date
            and row.data_cutoff < row.session_date
            and datetime.fromisoformat(row.published_at) < cutoff_time(row.session_date)
            and valid_candle(row.candle)
        )
    except (TypeError, ValueError):
        pass
    result = {
        "batch_id": row.batch_id,
        "task_id": row.task_id,
        "session_date": row.session_date,
        "family": row.family,
        "model_id": row.model_id,
        "prediction_id": row.prediction_id,
        "published_at": row.published_at if eligible else None,
        "actual_revision_id": row.actual_revision_id if actual_ok else None,
        "predicted": row.candle if eligible else None,
        "actual": actual if actual_ok else None,
        "errors": None,
        "relative_errors": None,
        "bias": None,
        "mae": None,
        "rmse": None,
        "mape": None,
    }
    if not eligible:
        pending = row.task_status in ("training", "predicting") and stamp < cutoff_time(row.session_date)
        status = (
            "preparing"
            if pending or row.batch_status == "waiting_data" and stamp < cutoff_time(row.session_date)
            else "no_forecast"
        )
        reason = row.task_error or row.batch_error or "缺少有效盘前预测，无法计算偏差"
    elif not actual_ok:
        status = "awaiting_actual"
        reason = "尚未完整收盘" if not closed else "等待该股票固定来源的完整收盘行情"
    else:
        errors = {f: row.candle[f] - actual[f] for f in FIELDS}
        relative = {f: errors[f] / actual[f] for f in FIELDS}
        result.update(
            errors=errors,
            relative_errors=relative,
            bias=sum(errors.values()) / 4,
            mae=sum(abs(v) for v in errors.values()) / 4,
            rmse=math.sqrt(sum(v * v for v in errors.values()) / 4),
            mape=sum(abs(v) for v in relative.values()) / 4,
        )
        status, reason = "available", "已发布预测与当前同源真实行情比较"
    return {**result, "status": status, "reason": reason}


def daily_deviations(
    db, stock_id, start=None, end=None, family=None, only_available=True, limit=50, offset=0
):
    require(db.get(Stock, stock_id), "股票不存在")
    if start and end and start > end:
        raise DomainError("开始日期不能晚于结束日期")
    query = (
        select(
            DailyBatch.id.label("batch_id"),
            DailyBatch.session_date,
            DailyBatch.status.label("batch_status"),
            DailyBatch.error.label("batch_error"),
            DailyTask.id.label("task_id"),
            DailyTask.family,
            DailyTask.status.label("task_status"),
            DailyTask.error.label("task_error"),
            DailyTask.model_id,
            DailyTask.prediction_id,
            Prediction.result["models"][0]["candle"].label("candle"),
            Prediction.result["automatic_daily"].as_boolean().label("automatic_daily"),
            Prediction.result["target_session"].as_string().label("target_session"),
            Prediction.result["data_cutoff"].as_string().label("data_cutoff"),
            Prediction.result["published_at"].as_string().label("published_at"),
            Prediction.config["price_basis"].as_string().label("price_basis"),
            BarRevision.id.label("actual_revision_id"),
            *(getattr(BarRevision, f).label(f"actual_{f}") for f in FIELDS),
            ImportBatch.metadata_json["source"]["provider"].as_string().label("actual_provider"),
            MarketFeed.provider,
        )
        .select_from(DailyBatch)
        .outerjoin(DailyTask, DailyTask.batch_id == DailyBatch.id)
        .outerjoin(
            Prediction,
            and_(
                Prediction.id == DailyTask.prediction_id,
                Prediction.stock_id == stock_id,
                Prediction.horizon == "next_day",
            ),
        )
        .outerjoin(
            BarCurrent,
            and_(
                BarCurrent.stock_id == stock_id,
                BarCurrent.interval == "1d",
                BarCurrent.price_basis == "raw",
                BarCurrent.time == DailyBatch.session_date,
            ),
        )
        .outerjoin(BarRevision, BarRevision.id == BarCurrent.revision_id)
        .outerjoin(ImportBatch, ImportBatch.id == BarRevision.import_id)
        .outerjoin(MarketFeed, MarketFeed.stock_id == DailyBatch.stock_id)
        .where(DailyBatch.stock_id == stock_id)
        .order_by(DailyBatch.session_date.desc(), DailyTask.family, DailyTask.id)
    )
    if start:
        query = query.where(DailyBatch.session_date >= start.isoformat())
    if end:
        query = query.where(DailyBatch.session_date <= end.isoformat())
    if family:
        query = query.where(DailyTask.family == family)
    stamp = market_time()
    rows, totals, reasons, groups = [], 0, {}, {}
    available_count = 0
    for record in db.execute(query.execution_options(yield_per=500)):
        item = deviation_row(record, stamp)
        reasons[item["status"]] = reasons.get(item["status"], 0) + 1
        if item["status"] == "available":
            available_count += 1
            group = groups.setdefault(
                item["family"],
                {
                    "family": item["family"],
                    "sample_count": 0,
                    "error_sums": {f: 0.0 for f in FIELDS},
                    "mae_sum": 0.0,
                    "squared_sum": 0.0,
                    "mape_sum": 0.0,
                    "first_session": item["session_date"],
                    "last_session": item["session_date"],
                },
            )
            group["sample_count"] += 1
            group["first_session"] = min(group["first_session"], item["session_date"])
            for f in FIELDS:
                group["error_sums"][f] += item["errors"][f]
                group["squared_sum"] += item["errors"][f] ** 2
            group["mae_sum"] += item["mae"]
            group["mape_sum"] += item["mape"]
        if not only_available or item["status"] == "available":
            if offset <= totals < offset + limit:
                rows.append(item)
            totals += 1
    summaries = []
    for name, group in sorted(groups.items()):
        n = group["sample_count"]
        means = {f: group["error_sums"][f] / n for f in FIELDS}
        summaries.append(
            {
                "family": name,
                "sample_count": n,
                "mean_errors": means,
                "bias": sum(means.values()) / 4,
                "mae": group["mae_sum"] / n,
                "rmse": math.sqrt(group["squared_sum"] / (4 * n)),
                "mape": group["mape_sum"] / n,
                "first_session": group["first_session"],
                "last_session": group["last_session"],
            }
        )
    families = list(
        db.scalars(
            select(DailyTask.family)
            .join(DailyBatch, DailyTask.batch_id == DailyBatch.id)
            .where(DailyBatch.stock_id == stock_id)
            .distinct()
            .order_by(DailyTask.family)
        )
    )
    return {
        "stock_id": stock_id,
        "price_basis": "raw",
        "definition": "预测值−真实值；正值为高估，负值为低估",
        "checked_at": stamp.isoformat(),
        "families": families,
        "rows": rows,
        "total": totals,
        "offset": offset,
        "limit": limit,
        "summary": {
            "available_count": available_count,
            "unavailable_count": sum(reasons.values()) - available_count,
            "status_counts": reasons,
            "models": summaries,
        },
    }
