from datetime import date, timedelta
from functools import lru_cache
import exchange_calendars as xcals
import pandas as pd
from .entities import CalendarDay
from .errors import DomainError


@lru_cache
def calendar():
    return xcals.get_calendar("XSHG", start="1991-01-01", end="2025-12-31")


def is_open(db, day: str):
    custom = db.get(CalendarDay, day)
    if custom:
        return custom.is_open
    cal = calendar()
    timestamp = pd.Timestamp(day)
    if timestamp < cal.first_session or timestamp > cal.last_session:
        raise DomainError(
            f"交易日历未覆盖{day}，请上传包含日期及is_open的完整日历", "calendar_unavailable", 409
        )
    return cal.is_session(timestamp)


def future_dates(db, after: str, count: int):
    day = date.fromisoformat(after[:10])
    result = []
    for _ in range(count * 4 + 40):
        day += timedelta(days=1)
        if is_open(db, day.isoformat()):
            result.append(day.isoformat())
            if len(result) == count:
                return result
    raise DomainError("未来交易日历不足", "calendar_unavailable", 409)
