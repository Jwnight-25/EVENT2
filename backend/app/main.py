import json
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
    AdviceConversation,
    TradingPreference,
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
    AISettingsRequest,
    ConversationRequest,
    AdviceRequest,
    PreferenceRequest,
    PreferenceField,
)
from .calendar import calendar, future_dates

DB = Annotated[Session, Depends(get_db)]


def encode(record, exclude=()):
    return {c.name: getattr(record, c.name) for c in record.__table__.columns if c.name not in exclude}


@asynccontextmanager
async def lifespan(_):
    yield
    engine.dispose()


app = FastAPI(title="EVENT2 · 股票研究", version="0.9.0", lifespan=lifespan)
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
    if request.method == "POST" and not request.url.path.startswith(API_PREFIX + "/maintenance/backups"):
        from .storage import storage_lock

        try:
            with storage_lock():
                response = await call_next(request)
        except DomainError as error:
            return await domain_error(request, error)
    else:
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
        if body.evaluation_mode == "holdout" and tested.intersection(body.horizons):
            raise DomainError(
                "此数据的评估区段已经使用；可切换研究模式反复调参，但不能再次声称独立测试",
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
    run_id: str | None = None,
):
    query = select(Model).where(Model.stock_id == stock_id, Model.price_basis == price_basis)
    if horizon:
        query = query.where(Model.horizon == horizon)
    if selected_only:
        query = query.where(Model.selected.is_(True))
    if run_id:
        query = query.where(Model.run_id == run_id)
    records = list(db.scalars(query.order_by(Model.created_at.desc()).limit(100)))
    from .assessment import assess_models, CHECK_LABELS
    from .reliability import reliability
    from .market import load_snapshot
    from .research import STEPS

    evidence = {}
    for group in {(m.run_id, m.horizon) for m in records}:
        peers = list(db.scalars(select(Model).where(Model.run_id == group[0], Model.horizon == group[1])))
        rows = load_snapshot(db.get(Snapshot, peers[0].snapshot_id))
        evidence.update(assess_models(peers, rows, STEPS[group[1]]))
    response = []
    for model in records:
        item = encode(model, ("artifact_path", "diagnostics"))
        item["metrics"] = {**model.metrics, "assessment": evidence[model.id]}
        baseline = db.scalar(
            select(Model).where(
                Model.run_id == model.run_id, Model.horizon == model.horizon, Model.family == "naive"
            )
        )
        item["metrics"]["reliability"] = reliability(model, baseline)
        failed = [
            CHECK_LABELS.get(key, key)
            for key, passed in model.metrics.get("acceptance_checks", {}).items()
            if not passed
        ]
        if failed and model.metrics.get("evaluation_mode", "holdout") == "holdout":
            item["reason"] = "未通过：" + "、".join(failed)
        response.append(item)
    return response


@app.get(API_PREFIX + "/model-families")
def family_catalog():
    from .model_catalog import model_families

    return model_families()


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
            (not model.selected and not body.allow_unvalidated)
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
    from .ai_settings import public_settings

    if not public_settings()["explanation_available"]:
        raise DomainError(
            "尚未配置联网AI服务，请先设置分析接口；已保存的模型预测可独立查看", "ai_not_configured", 409
        )
    for old in db.scalars(select(Job).where(Job.kind == "ai", Job.status.in_(["queued", "running"]))):
        if old.payload.get("prediction_id") == identifier:
            return {"job_id": old.id}
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
    from .ai_settings import public_settings

    config = public_settings()
    return {
        "market_data": "not_configured",
        "ai": "configured" if config["explanation_available"] else "not_configured",
        "ai_chat": "configured" if config["chat_available"] else "not_configured",
        "ai_web_search": config["web_search"],
        "deep_models": "optional",
        "trading": "not_supported",
    }


@app.get(API_PREFIX + "/ai/settings")
def ai_settings():
    from .ai_settings import public_settings

    return public_settings()


@app.post(API_PREFIX + "/ai/settings")
def ai_settings_save(body: AISettingsRequest, db: DB):
    from .ai_settings import save_settings

    if db.scalar(select(Job.id).where(Job.kind.in_(["ai", "advice"]), Job.status.in_(["queued", "running"]))):
        raise DomainError("请先完成或取消AI任务，再更换服务设置", "ai_busy", 409)
    return save_settings(body)


@app.post(API_PREFIX + "/ai/test")
def ai_connection_test():
    from .ai_client import AIClient
    from .ai_settings import public_settings

    if not public_settings()["chat_available"]:
        raise DomainError("请先保存AI配置", "ai_not_configured", 409)
    try:
        result = AIClient().complete("用中文简短回答。", [{"role": "user", "content": "回复：连接成功"}])
    except Exception:
        raise DomainError("连接失败，请检查服务地址、密钥、模型权限和网络", "ai_test_failed", 502) from None
    return {"connected": True, "model": result.get("model"), "web_search_tested": False}


@app.get(API_PREFIX + "/advice/preferences")
def advice_preferences(db: DB):
    from .advice import profile

    return profile(db)


@app.post(API_PREFIX + "/advice/preferences")
def advice_preference_save(body: PreferenceRequest, db: DB):
    from .advice import update_preference

    return update_preference(db, body)


@app.post(API_PREFIX + "/advice/preferences/{field}/forget")
def advice_preference_forget(field: PreferenceField, db: DB):
    from .advice import profile, serial_write

    serial_write(db)
    item = db.scalar(
        select(TradingPreference).where(TradingPreference.field == field).with_for_update()
    ) or TradingPreference(field=field)
    item.value, item.source, item.quote = "", "forgotten", ""
    item.confirmed, item.locked, item.source_message_id, item.updated_at = False, True, None, now()
    db.add(item)
    db.commit()
    return profile(db)


@app.post(API_PREFIX + "/advice/preferences/{field}/resume")
def advice_preference_resume(field: PreferenceField, db: DB):
    from .advice import profile, serial_write

    serial_write(db)
    item = db.scalar(select(TradingPreference).where(TradingPreference.field == field).with_for_update())
    if item:
        item.locked, item.updated_at = False, now()
        db.commit()
    return profile(db)


@app.get(API_PREFIX + "/advice/conversations")
def advice_conversations(db: DB, stock_id: str):
    require(db.get(Stock, stock_id))
    return [
        encode(c)
        for c in db.scalars(
            select(AdviceConversation)
            .where(AdviceConversation.stock_id == stock_id)
            .order_by(AdviceConversation.updated_at.desc())
            .limit(100)
        )
    ]


@app.post(API_PREFIX + "/advice/conversations", status_code=201)
def advice_conversation_create(body: ConversationRequest, db: DB):
    require(db.get(Stock, body.stock_id))
    conversation = AdviceConversation(stock_id=body.stock_id, title="新的交易研究对话")
    db.add(conversation)
    db.commit()
    return encode(conversation)


@app.get(API_PREFIX + "/advice/conversations/{identifier}")
def advice_conversation_detail(identifier: str, db: DB):
    from .advice import conversation_detail

    return conversation_detail(db, identifier)


@app.post(API_PREFIX + "/advice/conversations/{identifier}/messages", status_code=202)
def advice_message_create(identifier: str, body: AdviceRequest, db: DB):
    from .advice import enqueue_message

    return enqueue_message(db, identifier, body)


@app.post(API_PREFIX + "/advice/messages/{identifier}/retry", status_code=202)
def advice_message_retry(identifier: str, db: DB):
    from .advice import retry_message

    return retry_message(db, identifier)


@app.post(API_PREFIX + "/advice/conversations/{identifier}/forget")
def advice_conversation_forget(identifier: str, db: DB):
    from .advice import forget_conversation

    return forget_conversation(db, identifier)


@app.get(API_PREFIX + "/maintenance/status")
def maintenance_status(db: DB, stock_id: str, price_basis: PriceBasis = "raw"):
    from .maintenance import data_status

    return data_status(db, stock_id, price_basis)


@app.post(API_PREFIX + "/maintenance/backups", status_code=201)
def maintenance_backup():
    from .backup import backup
    from .config import BACKUP_DIR
    from .db import uid
    from pathlib import Path

    identifier = now().replace(":", "").replace(".", "-") + "-" + uid()[:8]
    destination = BACKUP_DIR / identifier
    try:
        result = backup(destination)
    except (ValueError, OSError) as exc:
        raise DomainError(str(exc), "backup_failed", 409)
    return {"id": identifier, "path": str(Path(destination)), **result}


@app.post(API_PREFIX + "/maintenance/backups/{identifier}/verify")
def maintenance_verify(identifier: str):
    from .backup import verify
    from .config import BACKUP_DIR

    if "/" in identifier or "\\" in identifier or identifier in (".", ".."):
        raise DomainError("备份名称无效")
    destination = (BACKUP_DIR / identifier).resolve()
    if destination.parent != BACKUP_DIR:
        raise DomainError("备份名称无效")
    try:
        return verify(destination)
    except (ValueError, OSError, KeyError) as exc:
        raise DomainError(str(exc), "backup_invalid", 409)


@app.post(API_PREFIX + "/maintenance/compare")
async def maintenance_compare(
    db: DB,
    file: UploadFile = File(),
    stock_id: str = Form(),
    price_basis: PriceBasis = Form("raw"),
    source: str = Form(min_length=1, max_length=200),
    volume_unit: Literal["shares", "lots"] = Form("shares"),
):
    from .maintenance import compare_upload

    content = await file.read(MAX_UPLOAD + 1)
    if len(content) > MAX_UPLOAD:
        raise DomainError("文件超过20MB")
    return compare_upload(
        db, stock_id, price_basis, content, file.filename or "comparison.csv", source, volume_unit
    )
