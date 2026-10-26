"""One short launchd tick; heavy jobs belong to the independent worker."""

from sqlalchemy import select
from .db import SessionLocal
from .entities import Stock
from .market_updates import market_time, update_due, update_lock
from .daily_research import daily_tick
from .intelligence import enqueue_intelligence
from .storage import storage_lock
from .errors import DomainError


def market_cycle(bootstrap=False):
    stamp = market_time()
    result = {}
    for name, action in (("market", update_due), ("daily", lambda: daily_tick(bootstrap=bootstrap))):
        try:
            result[name] = action()
        except DomainError as exc:
            result[name] = {"code": exc.code, "message": exc.message}
            if exc.code in ("storage_busy", "update_busy"):
                return result
            if exc.code != "calendar_unavailable":
                raise
    # News does not depend on a future exchange calendar being available.
    if 7 <= stamp.hour < 22:
        try:
            with update_lock(), storage_lock(exclusive=True), SessionLocal() as db:
                ids = []
                for stock in db.scalars(select(Stock).where(Stock.is_active.is_(True)).order_by(Stock.code)):
                    key = f"intel:{stock.id}:{stamp.date().isoformat()}:{(stamp.hour - 7) // 2}"
                    ids.append(enqueue_intelligence(db, stock.id, key).id)
                result["intelligence_jobs"] = ids
        except DomainError as exc:
            if exc.code not in ("storage_busy", "update_busy"):
                raise
            result["intelligence"] = {"code": exc.code, "message": exc.message}
    return result
