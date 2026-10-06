import json
import os
from contextlib import asynccontextmanager
from typing import Annotated, Literal
from uuid import uuid4
from fastapi import Depends, FastAPI, File, Form, Header, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from .config import API_PREFIX, MAX_UPLOAD
from .db import get_db, engine, now
from .entities import (
    Stock,
    ImportBatch,
    Snapshot,
    Job,
    TrainingRun,
    Model,
    Prediction,
    AIAnalysis,
    BarCurrent,
    CalendarDay,
)
from .errors import DomainError, require
from .market import preview, commit_import, current_bars, aggregate, create_snapshot, read_file
from .schemas import (
    StockRequest,
    CommitRequest,
    SnapshotRequest,
    TrainingRequest,
    PredictionRequest,
    PriceBasis,
)
from .calendar import calendar, future_dates

DB = Annotated[Session, Depends(get_db)]


def encode(record, exclude=()):
    return {c.name: getattr(record, c.name) for c in record.__table__.columns if c.name not in exclude}


@asynccontextmanager
async def lifespan(_):
    yield
    engine.dispose()


app = FastAPI(title="EVENT2 · 股票研究", version="0.3.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "Idempotency-Key"],
)


@app.middleware("http")
async def request_id(request: Request, call_next):
    request.state.request_id = str(uuid4())
    origin = request.headers.get("origin")
    if (
        request.method not in ("GET", "HEAD", "OPTIONS")
        and origin
        and origin
        not in (
            "http://127.0.0.1:5173",
            "http://localhost:5173",
            "http://127.0.0.1:8000",
            "http://localhost:8000",
        )
    ):
        return JSONResponse(
            status_code=403,
            content={
                "code": "origin_rejected",
                "message": "仅允许本机研究界面修改数据",
                "details": None,
                "request_id": request.state.request_id,
            },
        )
    response = await call_next(request)
    response.headers["X-Request-ID"] = request.state.request_id
    return response


@app.exception_handler(DomainError)
async def domain_error(request: Request, error: DomainError):
    return JSONResponse(
        status_code=error.status,
        content={
            "code": error.code,
            "message": error.message,
            "details": error.details,
            "request_id": request.state.request_id,
        },
    )


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, error):
    return JSONResponse(
        status_code=422,
        content={
            "code": "validation_error",
            "message": "输入参数不合法",
            "details": [{"loc": list(e["loc"]), "msg": e["msg"]} for e in error.errors()],
            "request_id": request.state.request_id,
        },
    )


@app.get(API_PREFIX + "/health")
def health(db: DB):
    db.execute(select(func.count()).select_from(Stock))
    cal = calendar()
    return {
        "status": "ok",
        "version": app.version,
        "database": engine.dialect.name,
        "market_timezone": "Asia/Shanghai",
        "calendar_start": str(cal.first_session.date()),
        "calendar_end": str(cal.last_session.date()),
    }


@app.get(API_PREFIX + "/stocks")
def stocks(db: DB):
    result = []
    for stock in db.scalars(select(Stock).order_by(Stock.code)):
        latest = db.scalar(
            select(func.max(BarCurrent.time)).where(
                BarCurrent.stock_id == stock.id, BarCurrent.interval == "1d"
            )
        )
        intervals = list(
            db.scalars(select(BarCurrent.interval).where(BarCurrent.stock_id == stock.id).distinct())
        )
        result.append({**encode(stock), "data_cutoff": latest, "intervals": intervals})
    return result


@app.post(API_PREFIX + "/stocks", status_code=201)
def add_stock(body: StockRequest, db: DB):
    stock = Stock(**body.model_dump())
    db.add(stock)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise DomainError("该股票已存在", "duplicate_stock", 409)
    return encode(stock)


@app.post(API_PREFIX + "/imports/preview")
async def import_preview(
    db: DB,
    file: UploadFile = File(),
    stock_id: str = Form(),
    interval: Literal["1d", "1m"] = Form("1d"),
    price_basis: PriceBasis = Form("raw"),
    volume_unit: Literal["shares", "lots"] = Form("shares"),
    mapping: str = Form("{}"),
):
    content = await file.read(MAX_UPLOAD + 1)
    if len(content) > MAX_UPLOAD:
        raise DomainError("文件超过20MB")
    try:
        fields = json.loads(mapping)
        if not isinstance(fields, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in fields.items()
        ):
            raise ValueError("映射必须为字符串键值对象")
        batch = preview(
            db, stock_id, content, file.filename or "upload.csv", interval, price_basis, volume_unit, fields
        )
    except (ValueError, KeyError, OSError) as exc:
        raise DomainError(f"无法解析文件或字段映射：{exc}")
    return encode(batch, ("rows",))


@app.post(API_PREFIX + "/imports/{identifier}/commit")
def import_commit(identifier: str, body: CommitRequest, db: DB):
    return encode(commit_import(db, identifier, body.policy), ("rows",))


@app.get(API_PREFIX + "/imports/{identifier}")
def import_detail(identifier: str, db: DB):
    return encode(require(db.get(ImportBatch, identifier)), ("rows",))


@app.post(API_PREFIX + "/calendar/import")
async def calendar_import(db: DB, file: UploadFile = File(), source: str = Form()):
    from datetime import date

    content = await file.read(MAX_UPLOAD + 1)
    if len(content) > MAX_UPLOAD:
        raise DomainError("文件超过20MB")
    frame = read_file(content, file.filename or "calendar.csv")
    if not {"date", "is_open"}.issubset(frame.columns):
        raise DomainError("日历需要date和is_open列，包含期间全部自然日")
    records = {}
    for _, row in frame.iterrows():
        try:
            day = date.fromisoformat(str(row["date"])[:10]).isoformat()
        except ValueError:
            raise DomainError("日历日期无效")
        value = str(row["is_open"]).lower()
        if value not in ("1", "0", "true", "false") or day in records:
            raise DomainError("is_open仅允许0/1或true/false，日期不得重复")
        records[day] = value in ("1", "true")
    if not records:
        raise DomainError("日历为空")
    if len(records) != (date.fromisoformat(max(records)) - date.fromisoformat(min(records))).days + 1:
        raise DomainError("日历必须包含范围内全部自然日")
    for day, opened in records.items():
        record = db.get(CalendarDay, day)
        if record:
            record.is_open, record.source = opened, source[:200]
        else:
            db.add(CalendarDay(date=day, is_open=opened, source=source[:200]))
    db.commit()
    return {"count": len(records), "start": min(records), "end": max(records)}


@app.get(API_PREFIX + "/market/bars")
def bars(
    db: DB,
    stock_id: str,
    interval: Literal["1m", "5m", "30m", "1h", "1d", "1mo"] = "1d",
    price_basis: PriceBasis = "raw",
    start: str | None = None,
    end: str | None = None,
    limit: int = Query(1000, ge=1, le=10000),
):
    require(db.get(Stock, stock_id))
    source_interval = "1d" if interval == "1mo" else "1m" if interval in ("5m", "30m", "1h") else interval
    rows = current_bars(db, stock_id, source_interval, price_basis, start, end)
    if source_interval != interval:
        rows = aggregate(rows, interval)
    if interval == "1mo" and rows:
        from datetime import datetime
        from zoneinfo import ZoneInfo

        rows[-1]["incomplete"] = rows[-1]["time"][:7] == datetime.now(ZoneInfo("Asia/Shanghai")).strftime(
            "%Y-%m"
        )
    return {
        "bars": rows[-limit:],
        "total": len(rows),
        "interval": interval,
        "price_basis": price_basis,
        "message": None if rows else "该周期和价格口径暂无数据",
        "aggregation": "session-start-v1；不跨午休；末尾保留不足周期"
        if source_interval == "1m" and source_interval != interval
        else None,
    }


@app.post(API_PREFIX + "/snapshots", status_code=201)
def snapshot_create(body: SnapshotRequest, db: DB):
    require(db.get(Stock, body.stock_id))
    snap = create_snapshot(db, body.stock_id, body.price_basis)
    db.commit()
    return encode(snap, ("path",))


def resolve_snapshot(db, body):
    require(db.get(Stock, body.stock_id))
    snap = (
        require(db.get(Snapshot, body.data_snapshot_id))
        if body.data_snapshot_id
        else create_snapshot(db, body.stock_id, body.price_basis)
    )
    if snap.stock_id != body.stock_id or snap.price_basis != body.price_basis:
        raise DomainError("快照与股票或价格口径不匹配", "snapshot_mismatch", 409)
    return snap


def existing_job(db, key, kind, payload):
    if not key:
        return None
    if len(key) > 100:
        raise DomainError("幂等键最多100字符")
    job = db.scalar(select(Job).where(Job.idempotency_key == key))
    if job and (job.kind != kind or job.payload.get("request") != payload):
        raise DomainError("幂等键已用于不同请求", "idempotency_conflict", 409)
    return job


@app.post(API_PREFIX + "/training-runs", status_code=202)
def training_create(body: TrainingRequest, db: DB, idempotency_key: str | None = Header(None)):
    payload = body.model_dump()
    old = existing_job(db, idempotency_key, "training", payload)
    if old:
        return {"job_id": old.id, **old.result}
    snap = resolve_snapshot(db, body)
    if snap.bar_count < 220:
        raise DomainError("训练至少需要220条完整日线；长周期需要更多历史数据", "insufficient_data", 409)
    for previous in db.scalars(
        select(TrainingRun)
        .join(Snapshot, TrainingRun.snapshot_id == Snapshot.id)
        .where(TrainingRun.stock_id == body.stock_id, Snapshot.checksum == snap.checksum)
    ):
        previous_job = db.get(Job, previous.job_id)
        if previous_job.status in ("queued", "running"):
            raise DomainError("同一数据版本已有训练任务", "duplicate_training", 409)
        tested = set(previous.report.get("test_started_horizons", [])) | {
            h
            for h, result in previous.report.get("horizons", {}).items()
            if result.get("status") == "completed"
        }
        if tested.intersection(body.horizons):
            raise DomainError(
                "同一数据已完成该周期的最终测试，请追加新行情后重训，避免反复调参污染测试",
                "test_already_used",
                409,
            )
    job = Job(kind="training", payload={"request": payload}, idempotency_key=idempotency_key)
    db.add(job)
    db.flush()
    run = TrainingRun(stock_id=body.stock_id, snapshot_id=snap.id, job_id=job.id, config=payload)
    db.add(run)
    db.flush()
    job.result = {"run_id": run.id}
    db.commit()
    return {"job_id": job.id, "run_id": run.id}


@app.get(API_PREFIX + "/training-runs")
def training_list(db: DB, stock_id: str, limit: int = Query(20, ge=1, le=100)):
    return [
        encode(r)
        for r in db.scalars(
            select(TrainingRun)
            .where(TrainingRun.stock_id == stock_id)
            .order_by(TrainingRun.created_at.desc())
            .limit(limit)
        )
    ]


@app.get(API_PREFIX + "/training-runs/{identifier}")
def training_detail(identifier: str, db: DB):
    from .entities import Trial

    run = require(db.get(TrainingRun, identifier))
    return {
        **encode(run),
        "trials": [
            encode(t)
            for t in db.scalars(select(Trial).where(Trial.run_id == run.id).order_by(Trial.created_at))
        ],
    }


@app.get(API_PREFIX + "/models")
def models(
    db: DB,
    stock_id: str,
    horizon: str | None = None,
    price_basis: PriceBasis = "raw",
    selected_only: bool = False,
):
    query = select(Model).where(Model.stock_id == stock_id, Model.price_basis == price_basis)
    if horizon:
        query = query.where(Model.horizon == horizon)
    if selected_only:
        query = query.where(Model.selected.is_(True))
    return [
        encode(m, ("artifact_path", "diagnostics"))
        for m in db.scalars(query.order_by(Model.created_at.desc()).limit(100))
    ]


@app.get(API_PREFIX + "/models/{identifier}/diagnostics")
def diagnostics(identifier: str, db: DB):
    return require(db.get(Model, identifier)).diagnostics


@app.post(API_PREFIX + "/predictions", status_code=202)
def prediction_create(body: PredictionRequest, db: DB, idempotency_key: str | None = Header(None)):
    payload = body.model_dump()
    old = existing_job(db, idempotency_key, "prediction", payload)
    if old:
        return {"job_id": old.id, **old.result}
    if len(set(body.model_ids)) != len(body.model_ids):
        raise DomainError("模型不能重复选择")
    snap = resolve_snapshot(db, body)
    from .research import STEPS

    future_dates(db, snap.cutoff, STEPS[body.horizon])
    for identifier in body.model_ids:
        model = require(db.get(Model, identifier), "模型不存在")
        if (
            not model.selected
            or model.stock_id != body.stock_id
            or model.horizon != body.horizon
            or model.price_basis != body.price_basis
        ):
            raise DomainError("模型未达标或与股票、周期、价格口径不匹配", "model_unavailable", 409)
        if snap.cutoff < db.get(Snapshot, model.snapshot_id).cutoff:
            raise DomainError("预测数据早于模型训练数据", "future_model", 409)
    job = Job(kind="prediction", payload={"request": payload}, idempotency_key=idempotency_key)
    db.add(job)
    db.flush()
    prediction = Prediction(
        stock_id=body.stock_id, snapshot_id=snap.id, job_id=job.id, horizon=body.horizon, config=payload
    )
    db.add(prediction)
    db.flush()
    job.result = {"prediction_id": prediction.id}
    db.commit()
    return {"job_id": job.id, "prediction_id": prediction.id}


@app.get(API_PREFIX + "/predictions")
def prediction_list(db: DB, stock_id: str, limit: int = Query(30, ge=1, le=100)):
    return [
        encode(p)
        for p in db.scalars(
            select(Prediction)
            .where(Prediction.stock_id == stock_id)
            .order_by(Prediction.created_at.desc())
            .limit(limit)
        )
    ]


@app.get(API_PREFIX + "/predictions/{identifier}")
def prediction_detail(identifier: str, db: DB):
    prediction = require(db.get(Prediction, identifier))
    sources = [
        encode(a)
        for a in db.scalars(
            select(AIAnalysis).where(AIAnalysis.prediction_id == identifier).order_by(AIAnalysis.created_at)
        )
    ]
    snap = db.get(Snapshot, prediction.snapshot_id)
    actual = current_bars(db, prediction.stock_id, "1d", snap.price_basis, snap.cutoff)
    return {**encode(prediction), "ai_analyses": sources, "actual_bars": actual[-300:]}


@app.post(API_PREFIX + "/predictions/{identifier}/ai-analysis", status_code=202)
def ai_retry(identifier: str, db: DB):
    prediction = require(db.get(Prediction, identifier))
    if not prediction.result:
        raise DomainError("数值预测尚未完成", "prediction_not_ready", 409)
    job = Job(kind="ai", payload={"prediction_id": identifier})
    db.add(job)
    db.commit()
    return {"job_id": job.id}


@app.get(API_PREFIX + "/jobs/{identifier}")
def job_detail(identifier: str, db: DB):
    return encode(require(db.get(Job, identifier)))


@app.post(API_PREFIX + "/jobs/{identifier}/cancel")
def job_cancel(identifier: str, db: DB):
    job = require(db.get(Job, identifier))
    if job.status in ("queued", "running"):
        job.cancel_requested = True
        if job.status == "queued":
            job.status, job.finished_at = "cancelled", now()
        db.commit()
    return encode(job)


@app.get(API_PREFIX + "/integrations")
def integrations():
    return {
        "market_data": "not_configured",
        "ai": "configured" if os.getenv("AI_ANALYSIS_URL") else "not_configured",
        "deep_models": "optional",
        "trading": "not_supported",
    }
