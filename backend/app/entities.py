from sqlalchemy import JSON, Boolean, ForeignKey, Integer, Numeric, String, UniqueConstraint, Index
from sqlalchemy.orm import Mapped, mapped_column
from .db import Base, uid, now


class Record:
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    created_at: Mapped[str] = mapped_column(String(40), default=now)


class Stock(Record, Base):
    __tablename__ = "stocks"
    exchange: Mapped[str] = mapped_column(String(4))
    code: Mapped[str] = mapped_column(String(6))
    name: Mapped[str] = mapped_column(String(80))
    __table_args__ = (UniqueConstraint("exchange", "code"),)


class ImportBatch(Record, Base):
    __tablename__ = "imports"
    stock_id: Mapped[str] = mapped_column(ForeignKey("stocks.id"))
    status: Mapped[str] = mapped_column(String(20), default="preview")
    filename: Mapped[str] = mapped_column(String(200))
    checksum: Mapped[str] = mapped_column(String(64))
    metadata_json: Mapped[dict] = mapped_column(JSON)
    report: Mapped[dict] = mapped_column(JSON)
    rows: Mapped[list] = mapped_column(JSON)


class BarRevision(Record, Base):
    __tablename__ = "bar_revisions"
    stock_id: Mapped[str] = mapped_column(ForeignKey("stocks.id"))
    import_id: Mapped[str] = mapped_column(ForeignKey("imports.id"))
    interval: Mapped[str] = mapped_column(String(4))
    price_basis: Mapped[str] = mapped_column(String(16))
    time: Mapped[str] = mapped_column(String(40))
    open: Mapped[float] = mapped_column(Numeric(20, 6))
    high: Mapped[float] = mapped_column(Numeric(20, 6))
    low: Mapped[float] = mapped_column(Numeric(20, 6))
    close: Mapped[float] = mapped_column(Numeric(20, 6))
    volume: Mapped[float] = mapped_column(Numeric(24, 4))
    amount: Mapped[float | None] = mapped_column(Numeric(24, 4), nullable=True)
    __table_args__ = (Index("ix_bar_stock_time", "stock_id", "interval", "price_basis", "time"),)


class BarCurrent(Base):
    __tablename__ = "bar_current"
    stock_id: Mapped[str] = mapped_column(ForeignKey("stocks.id"), primary_key=True)
    interval: Mapped[str] = mapped_column(String(4), primary_key=True)
    price_basis: Mapped[str] = mapped_column(String(16), primary_key=True)
    time: Mapped[str] = mapped_column(String(40), primary_key=True)
    revision_id: Mapped[str] = mapped_column(ForeignKey("bar_revisions.id"))


class Snapshot(Record, Base):
    __tablename__ = "snapshots"
    stock_id: Mapped[str] = mapped_column(ForeignKey("stocks.id"))
    cutoff: Mapped[str] = mapped_column(String(40))
    price_basis: Mapped[str] = mapped_column(String(16))
    checksum: Mapped[str] = mapped_column(String(64))
    path: Mapped[str] = mapped_column(String(200))
    bar_count: Mapped[int] = mapped_column(Integer)


class Job(Record, Base):
    __tablename__ = "jobs"
    kind: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    stage: Mapped[str] = mapped_column(String(200), default="等待工作进程")
    payload: Mapped[dict] = mapped_column(JSON)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    progress: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    started_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    finished_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    heartbeat_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(100), nullable=True, unique=True)


class TrainingRun(Record, Base):
    __tablename__ = "training_runs"
    stock_id: Mapped[str] = mapped_column(ForeignKey("stocks.id"), index=True)
    snapshot_id: Mapped[str] = mapped_column(ForeignKey("snapshots.id"))
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), unique=True)
    config: Mapped[dict] = mapped_column(JSON)
    report: Mapped[dict] = mapped_column(JSON, default=dict)


class Trial(Record, Base):
    __tablename__ = "optimization_trials"
    run_id: Mapped[str] = mapped_column(ForeignKey("training_runs.id"))
    horizon: Mapped[str] = mapped_column(String(20))
    family: Mapped[str] = mapped_column(String(40))
    parameters: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20))
    report: Mapped[dict] = mapped_column(JSON)


class Model(Record, Base):
    __tablename__ = "models"
    run_id: Mapped[str] = mapped_column(ForeignKey("training_runs.id"))
    stock_id: Mapped[str] = mapped_column(ForeignKey("stocks.id"), index=True)
    snapshot_id: Mapped[str] = mapped_column(ForeignKey("snapshots.id"))
    family: Mapped[str] = mapped_column(String(40))
    horizon: Mapped[str] = mapped_column(String(20))
    price_basis: Mapped[str] = mapped_column(String(16))
    selected: Mapped[bool] = mapped_column(Boolean, default=False)
    parameters: Mapped[dict] = mapped_column(JSON)
    metrics: Mapped[dict] = mapped_column(JSON)
    diagnostics: Mapped[dict] = mapped_column(JSON)
    artifact_path: Mapped[str] = mapped_column(String(200))
    artifact_checksum: Mapped[str] = mapped_column(String(64))
    reason: Mapped[str] = mapped_column(String(500))


class Prediction(Record, Base):
    __tablename__ = "predictions"
    stock_id: Mapped[str] = mapped_column(ForeignKey("stocks.id"), index=True)
    snapshot_id: Mapped[str] = mapped_column(ForeignKey("snapshots.id"))
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), unique=True)
    horizon: Mapped[str] = mapped_column(String(20))
    config: Mapped[dict] = mapped_column(JSON)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    ai_status: Mapped[str] = mapped_column(String(30), default="not_requested")


class AIAnalysis(Record, Base):
    __tablename__ = "ai_analyses"
    prediction_id: Mapped[str] = mapped_column(ForeignKey("predictions.id"))
    status: Mapped[str] = mapped_column(String(30))
    content: Mapped[dict] = mapped_column(JSON)


class CalendarDay(Base):
    __tablename__ = "calendar_days"
    date: Mapped[str] = mapped_column(String(10), primary_key=True)
    is_open: Mapped[bool] = mapped_column(Boolean)
    source: Mapped[str] = mapped_column(String(200))


class AdviceConversation(Record, Base):
    __tablename__ = "advice_conversations"
    stock_id: Mapped[str] = mapped_column(ForeignKey("stocks.id"), index=True)
    title: Mapped[str] = mapped_column(String(100))
    updated_at: Mapped[str] = mapped_column(String(40), default=now)


class AdviceMessage(Record, Base):
    __tablename__ = "advice_messages"
    conversation_id: Mapped[str] = mapped_column(ForeignKey("advice_conversations.id"), index=True)
    role: Mapped[str] = mapped_column(String(20))
    text: Mapped[str] = mapped_column(String(30000))
    prediction_id: Mapped[str | None] = mapped_column(ForeignKey("predictions.id"), nullable=True)
    job_id: Mapped[str | None] = mapped_column(ForeignKey("jobs.id"), nullable=True)
    client_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
    reply_to_id: Mapped[str | None] = mapped_column(
        ForeignKey("advice_messages.id"), nullable=True, unique=True
    )
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    __table_args__ = (UniqueConstraint("conversation_id", "client_key"),)


class TradingPreference(Base):
    __tablename__ = "trading_preferences"
    field: Mapped[str] = mapped_column(String(40), primary_key=True)
    value: Mapped[str] = mapped_column(String(500))
    source: Mapped[str] = mapped_column(String(30))
    quote: Mapped[str] = mapped_column(String(1000))
    source_message_id: Mapped[str | None] = mapped_column(ForeignKey("advice_messages.id"), nullable=True)
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    locked: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[str] = mapped_column(String(40), default=now)
