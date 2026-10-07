"""Persistent single-user conversations and inspectable preference memory."""

from sqlalchemy import select, delete
from sqlalchemy.exc import IntegrityError

from .ai_client import AIClient, FIELDS
from .ai_settings import public_settings
from .db import SessionLocal, now, engine
from .entities import AdviceConversation, AdviceMessage, TradingPreference, Prediction, Snapshot, Stock, Job
from .errors import DomainError, require


def record(item):
    return {c.name: getattr(item, c.name) for c in item.__table__.columns}


def profile(db):
    by_field = {p.field: p for p in db.scalars(select(TradingPreference))}
    return [
        {
            "field": field,
            "label": label,
            **(
                record(by_field[field])
                if field in by_field
                else {
                    "value": "",
                    "source": "unknown",
                    "quote": "",
                    "confirmed": False,
                    "locked": False,
                    "updated_at": None,
                }
            ),
        }
        for field, label in FIELDS.items()
    ]


def serial_write(db):
    # SQLite does not implement SELECT FOR UPDATE. Reserve the writer before checking.
    if engine.dialect.name == "sqlite":
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")


def update_preference(db, body):
    serial_write(db)
    value = body.value.strip()
    if not value:
        raise DomainError("偏好内容不能为空")
    item = db.scalar(
        select(TradingPreference).where(TradingPreference.field == body.field).with_for_update()
    ) or TradingPreference(field=body.field)
    item.value, item.quote, item.source = value, value, "manual"
    item.confirmed, item.locked, item.source_message_id, item.updated_at = True, True, None, now()
    db.add(item)
    db.commit()
    return profile(db)


def learn(db, message, candidates):
    """Only quoted user text can become tentative memory; manual edits win."""
    if message.role != "user" or not isinstance(candidates, list):
        return []
    changed = []
    for candidate in candidates[:7]:
        if not isinstance(candidate, dict):
            continue
        field, value, quote = (candidate.get(k) for k in ("field", "value", "quote"))
        if field not in FIELDS or not isinstance(value, str) or not isinstance(quote, str):
            continue
        value, quote = value.strip(), quote.strip()
        if not (1 <= len(value) <= 500 and 4 <= len(quote) <= 1000 and quote in message.text):
            continue
        item = db.scalar(select(TradingPreference).where(TradingPreference.field == field).with_for_update())
        if item and (item.locked or item.updated_at > message.created_at):
            continue
        item = item or TradingPreference(field=field)
        item.value, item.quote, item.source = value, quote, "conversation"
        item.source_message_id, item.updated_at = message.id, now()
        item.confirmed, item.locked = False, False
        db.add(item)
        changed.append(field)
    return changed


def conversation_detail(db, identifier):
    conversation = require(db.get(AdviceConversation, identifier))
    messages = list(
        db.scalars(
            select(AdviceMessage)
            .where(AdviceMessage.conversation_id == identifier)
            .order_by(AdviceMessage.created_at, AdviceMessage.id)
        )
    )
    result = []
    for message in messages:
        job = db.get(Job, message.job_id) if message.job_id else None
        result.append(
            {
                **record(message),
                "job_status": job.status if job else None,
                "job_error": job.error if job else None,
            }
        )
    return {**record(conversation), "messages": result, "preferences": profile(db)}


def enqueue_message(db, identifier, body):
    serial_write(db)
    # Serializes submissions for the same conversation on PostgreSQL.
    conversation = require(
        db.scalar(select(AdviceConversation).where(AdviceConversation.id == identifier).with_for_update())
    )
    text = body.text.strip()
    if not text:
        raise DomainError("请输入对话内容")
    prediction = require(db.get(Prediction, body.prediction_id)) if body.prediction_id else None
    if prediction and (prediction.stock_id != conversation.stock_id or not prediction.result.get("models")):
        raise DomainError("请选择同一股票已完成的数值预测", "prediction_context_invalid", 409)
    old = db.scalar(
        select(AdviceMessage).where(
            AdviceMessage.conversation_id == identifier, AdviceMessage.client_key == body.client_key
        )
    )
    if old:
        if (
            old.text != text
            or old.prediction_id != body.prediction_id
            or old.metadata_json.get("learn_preferences") != body.learn_preferences
        ):
            raise DomainError("重复请求标识已用于不同内容", "idempotency_conflict", 409)
        return {"job_id": old.job_id, "message_id": old.id}
    if not public_settings()["chat_available"]:
        raise DomainError("请先在AI接入设置中填写服务、模型和本机密钥", "ai_not_configured", 409)
    pending = db.scalar(
        select(AdviceMessage)
        .join(Job, Job.id == AdviceMessage.job_id)
        .where(AdviceMessage.conversation_id == identifier, Job.status.in_(["queued", "running"]))
    )
    if pending:
        raise DomainError("这段对话正在等待回答，请完成后再发送", "conversation_busy", 409)
    job = Job(kind="advice", payload={})
    db.add(job)
    db.flush()
    message = AdviceMessage(
        conversation_id=identifier,
        role="user",
        text=text,
        prediction_id=body.prediction_id,
        job_id=job.id,
        client_key=body.client_key,
        metadata_json={"learn_preferences": body.learn_preferences},
    )
    db.add(message)
    db.flush()
    job.payload = {"message_id": message.id}
    job.result = {"message_id": message.id, "conversation_id": identifier}
    if not db.scalar(
        select(AdviceMessage.id).where(
            AdviceMessage.conversation_id == identifier, AdviceMessage.id != message.id
        )
    ):
        conversation.title = text[:45]
    conversation.updated_at = now()
    db.commit()
    return {"job_id": job.id, "message_id": message.id}


def retry_message(db, identifier):
    serial_write(db)
    message = require(db.get(AdviceMessage, identifier))
    if message.role != "user":
        raise DomainError("只能重试用户提问")
    conversation = require(
        db.scalar(
            select(AdviceConversation)
            .where(AdviceConversation.id == message.conversation_id)
            .with_for_update()
        )
    )
    job = require(db.get(Job, message.job_id))
    if job.status not in ("failed", "cancelled"):
        raise DomainError("该提问没有失败或取消", "retry_unavailable", 409)
    latest = db.scalar(
        select(AdviceMessage)
        .where(AdviceMessage.conversation_id == conversation.id, AdviceMessage.role == "user")
        .order_by(AdviceMessage.created_at.desc())
        .limit(1)
    )
    if latest.id != message.id:
        raise DomainError("只能重试这段对话的最后一次提问", "retry_unavailable", 409)
    if not public_settings()["chat_available"]:
        raise DomainError("AI服务尚未配置", "ai_not_configured", 409)
    job = Job(
        kind="advice",
        payload={"message_id": message.id},
        result={"message_id": message.id, "conversation_id": conversation.id},
    )
    db.add(job)
    db.flush()
    message.job_id = job.id
    db.commit()
    return {"job_id": job.id, "message_id": message.id}


def forget_conversation(db, identifier):
    serial_write(db)
    conversation = require(
        db.scalar(select(AdviceConversation).where(AdviceConversation.id == identifier).with_for_update())
    )
    messages = list(db.scalars(select(AdviceMessage).where(AdviceMessage.conversation_id == identifier)))
    jobs = [db.get(Job, m.job_id) for m in messages if m.job_id]
    if any(j.status in ("queued", "running") for j in jobs):
        raise DomainError("请先完成或取消这段对话的任务", "conversation_busy", 409)
    ids = [m.id for m in messages]
    db.execute(delete(TradingPreference).where(TradingPreference.source_message_id.in_(ids)))
    # Remove replies before user messages because of the self foreign key.
    db.execute(
        delete(AdviceMessage).where(
            AdviceMessage.conversation_id == identifier, AdviceMessage.role == "assistant"
        )
    )
    db.execute(delete(AdviceMessage).where(AdviceMessage.conversation_id == identifier))
    db.delete(conversation)
    db.commit()
    return {"forgotten": True}


def forecast_context(db, prediction):
    snap = require(db.get(Snapshot, prediction.snapshot_id))
    return {
        "prediction_id": prediction.id,
        "created_at": prediction.created_at,
        "data_cutoff": snap.cutoff,
        "price_basis": snap.price_basis,
        "horizon": prediction.horizon,
        "config": prediction.config,
        "forecast": prediction.result,
        "warning": "已保存的历史模型预测，不是实时行情；未来区间不是买卖收益保证。",
    }


def context_for(db, stock_id, prediction_id=None):
    stock = require(db.get(Stock, stock_id))
    return {
        "as_of": now(),
        "stock": {"exchange": stock.exchange, "code": stock.code, "name": stock.name},
        "prediction": forecast_context(db, require(db.get(Prediction, prediction_id)))
        if prediction_id
        else None,
        "preferences": profile(db),
    }


def bounded_history(db, conversation_id, current):
    history = list(
        db.scalars(
            select(AdviceMessage)
            .where(
                AdviceMessage.conversation_id == conversation_id,
                AdviceMessage.created_at <= current.created_at,
            )
            .order_by(AdviceMessage.created_at.desc(), AdviceMessage.id.desc())
            .limit(24)
        )
    )
    retained, remaining = [], 48000
    for message in history:
        if (
            message.id != current.id
            and message.role != "assistant"
            and (not message.job_id or db.get(Job, message.job_id).status != "succeeded")
        ):
            continue
        text = message.text
        if message.role == "assistant":
            cutoff = (message.metadata_json.get("context") or {}).get("prediction") or {}
            text = (
                f"历史回答，生成于{message.created_at}，模型数据截至{cutoff.get('data_cutoff', '未附带预测')}：\n"
                + text
            )
        if len(text) > 12000:
            text = text[:12000] + "\n[旧回答因长度限制缩略]"
        if len(text) > remaining:
            break
        retained.append({"role": message.role, "content": text})
        remaining -= len(text)
    return list(reversed(retained))


def advice_job(job_id, provider=None):
    provider = provider or AIClient()
    with SessionLocal() as db:
        job = require(db.get(Job, job_id))
        message = require(db.get(AdviceMessage, job.payload["message_id"]))
        if job.cancel_requested or job.status == "cancelled":
            return
        if db.scalar(select(AdviceMessage).where(AdviceMessage.reply_to_id == message.id)):
            return
        message_id, text = message.id, message.text
        learning = message.metadata_json.get("learn_preferences", True)
        job.stage = "总结明确偏好并准备研究上下文"
        db.commit()
    candidates, memory_error = [], False
    if learning:
        try:
            candidates = provider.extract_preferences(text)
        except Exception:
            memory_error = True
    with SessionLocal() as db:
        job, message = db.get(Job, job_id), db.get(AdviceMessage, message_id)
        if job.cancel_requested or job.status == "cancelled":
            return
        changed = learn(db, message, candidates)
        db.flush()
        conversation = db.get(AdviceConversation, message.conversation_id)
        context = context_for(db, conversation.stock_id, message.prediction_id)
        messages = bounded_history(db, conversation.id, message)
        job.stage = "生成交易研究回答与来源"
        db.commit()
    try:
        content = provider.advise(context, messages)
        if not isinstance(content.get("text"), str) or not content["text"].strip():
            raise ValueError("回答为空")
    except Exception:
        # Never expose provider exceptions containing headers, keys, URLs or bodies.
        raise DomainError(
            "AI回答失败：请检查密钥、模型权限或网络后重试；已保存的对话和模型结果保持可查", "ai_failed", 502
        ) from None
    with SessionLocal() as db:
        job, message = db.get(Job, job_id), db.get(AdviceMessage, message_id)
        if job.cancel_requested or job.status == "cancelled":
            return
        metadata = {
            **content,
            "text": None,
            "context": context,
            "learned_fields": changed,
            "memory_warning": "偏好总结失败，本次沿用已有偏好" if memory_error else None,
        }
        db.add(
            AdviceMessage(
                conversation_id=message.conversation_id,
                role="assistant",
                text=content["text"][:30000],
                prediction_id=message.prediction_id,
                job_id=job_id,
                reply_to_id=message.id,
                metadata_json=metadata,
            )
        )
        job.status, job.stage, job.progress, job.finished_at = "succeeded", "回答已保存", 100, now()
        db.get(AdviceConversation, message.conversation_id).updated_at = now()
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            if not db.scalar(select(AdviceMessage.id).where(AdviceMessage.reply_to_id == message.id)):
                raise
