"""Reversible watchlist changes; all market/research records remain intact."""

from datetime import datetime
from sqlalchemy import select

from .advice import serial_write
from .db import now
from .entities import Stock, StockWatchEvent, DailyBatch
from .errors import require


def change_state(db, stock, active):
    if stock.is_active == active:
        return False
    stock.is_active = active
    db.add(StockWatchEvent(stock_id=stock.id, is_active=active, changed_at=now()))
    if not active:
        for batch in db.scalars(
            select(DailyBatch).where(
                DailyBatch.stock_id == stock.id,
                DailyBatch.status == "waiting_data",
                DailyBatch.snapshot_id.is_(None),
            )
        ):
            batch.status, batch.error = "paused", "主动移出关注，已暂停等待行情的批次，未生成预测或积分"
    return True


def set_active(db, stock_id, active):
    serial_write(db)
    stock = require(db.scalar(select(Stock).where(Stock.id == stock_id).with_for_update()), "股票不存在")
    changed = change_state(db, stock, active)
    db.commit()
    return stock, changed


def watched_at(events, cutoff):
    active = True
    for event in events:
        if datetime.fromisoformat(event.changed_at) <= cutoff:
            active = event.is_active
    return active
