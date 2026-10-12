"""Daily closed bars, pinned to each stock's original provider and raw price basis."""

import csv
import fcntl
import hashlib
import io
import json
from contextlib import contextmanager
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import select, text

from .calendar import future_dates, is_open
from .config import DATA_DIR
from .db import SessionLocal, engine, now, uid
from .entities import ImportBatch, MarketFeed, MarketUpdateRun, Stock
from .errors import DomainError, require
from .market import FIELDS, commit_import, current_bars, preview
from .storage import storage_lock

SHANGHAI = ZoneInfo("Asia/Shanghai")
CLOSE_CHECK = time(15, 30)
RETRY_DELAY = timedelta(minutes=15)
MAX_ATTEMPTS = 6
PROVIDERS = {"Tencent": "腾讯", "Eastmoney": "东方财富"}
ENDPOINTS = {
    "Tencent": "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
    "Eastmoney": "https://push2his.eastmoney.com/api/qt/stock/kline/get",
}


def market_time(at=None):
    value = at or datetime.now(timezone.utc)
    if value.tzinfo is None:
        raise ValueError("Update time must include a timezone")
    return value.astimezone(SHANGHAI)


def latest_closed_session(db, at=None):
    stamp = market_time(at)
    day = stamp.date() - (timedelta(days=1) if stamp.time() < CLOSE_CHECK else timedelta())
    for _ in range(366):
        if is_open(db, day.isoformat()):
            return day.isoformat()
        day -= timedelta(days=1)
    raise DomainError("无法确定最近已收盘交易日，请核对交易日历", "calendar_unavailable", 409)


def ensure_feeds(db):
    """The first known committed raw import pins the source; never follow later imports."""
    for stock in db.scalars(select(Stock).order_by(Stock.code)):
        if db.get(MarketFeed, stock.id):
            continue
        for batch in db.scalars(
            select(ImportBatch)
            .where(ImportBatch.stock_id == stock.id, ImportBatch.status == "committed")
            .order_by(ImportBatch.created_at, ImportBatch.id)
        ):
            source = batch.metadata_json.get("source")
            if (
                batch.metadata_json.get("interval") == "1d"
                and batch.metadata_json.get("price_basis") == "raw"
                and isinstance(source, dict)
                and source.get("provider") in PROVIDERS
            ):
                db.add(
                    MarketFeed(
                        stock_id=stock.id,
                        provider=source["provider"],
                        source_import_id=batch.id,
                        price_basis="raw",
                        enabled=True,
                    )
                )
                break
    db.commit()


@contextmanager
def update_lock():
    with open(DATA_DIR / "market-update.lock", "a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise DomainError("收盘数据更新正在进行，请稍后查看", "update_busy", 409)
        connection = None
        try:
            if engine.dialect.name == "postgresql":
                connection = engine.connect()
                if not connection.scalar(text("SELECT pg_try_advisory_lock(20261012)")):
                    raise DomainError("该数据库正在更新行情", "update_busy", 409)
            yield
        finally:
            if connection is not None:
                connection.execute(text("SELECT pg_advisory_unlock(20261012)"))
                connection.close()
            fcntl.flock(handle, fcntl.LOCK_UN)


def _number(value):
    try:
        result = Decimal(str(value))
        if not result.is_finite():
            raise InvalidOperation
        return result
    except (InvalidOperation, ValueError):
        raise DomainError("行情来源返回无效数值，未写入行情", "invalid_market_data", 502)


def parse_response(provider, stock, value, start, end):
    rows = []
    if provider == "Tencent":
        symbol = ("sh" if stock.exchange == "SSE" else "sz") + stock.code
        if value.get("code") != 0 or symbol not in value.get("data", {}):
            raise DomainError("腾讯接口未返回指定股票数据", "market_source_failed", 502)
        data = value["data"][symbol]
        if "day" not in data:
            raise DomainError("腾讯接口未返回不复权日线，已拒绝混用复权数据", "invalid_market_data", 502)
        for row in data["day"]:
            if not isinstance(row, list) or len(row) < 6:
                raise DomainError("腾讯行情字段不完整", "invalid_market_data", 502)
            day, opening, closing, high, low, volume = row[:6]
            rows.append([day, opening, high, low, closing, str(_number(volume) * 100), ""])
    else:
        data = value.get("data")
        if value.get("rc") != 0:
            raise DomainError("东方财富接口返回失败", "market_source_failed", 502)
        if data is None:
            return []
        market = 1 if stock.exchange == "SSE" else 0
        if data.get("code") != stock.code or data.get("market") != market:
            raise DomainError("东方财富返回的股票标识不符", "invalid_market_data", 502)
        for line in data.get("klines", []):
            parts = line.split(",")
            if len(parts) < 7:
                raise DomainError("东方财富行情字段不完整", "invalid_market_data", 502)
            day, opening, closing, high, low, volume, amount = parts[:7]
            rows.append([day, opening, high, low, closing, str(_number(volume) * 100), amount])
    if len(rows) >= 320:
        raise DomainError("行情响应可能被截断，未写入行情", "invalid_market_data", 502)
    for row in rows:
        try:
            day = datetime.strptime(row[0], "%Y-%m-%d").date().isoformat()
        except (ValueError, TypeError):
            raise DomainError("行情日期格式无效", "invalid_market_data", 502)
        if day != row[0] or not start <= day <= end:
            raise DomainError("行情日期超出已收盘的请求范围", "invalid_market_data", 502)
    return rows


def download_daily(provider, stock, start, end, directory):
    # Other board conventions need verification before extending this lots-to-shares adapter.
    if stock.exchange not in ("SSE", "SZSE") or stock.code.startswith("688"):
        raise DomainError("此市场的行情单位尚未核验，暂不自动更新", "unsupported_market", 409)
    records, sources, seen = [], [], set()
    with httpx.Client(timeout=20, follow_redirects=False, trust_env=False) as client:
        for year in range(int(start[:4]), int(end[:4]) + 1):
            lower, upper = max(start, f"{year}-01-01"), min(end, f"{year}-12-31")
            if provider == "Tencent":
                symbol = ("sh" if stock.exchange == "SSE" else "sz") + stock.code
                params = {"param": f"{symbol},day,{lower},{upper},320,"}
            else:
                params = {
                    "fields1": "f1,f2,f3,f4,f5,f6",
                    "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
                    "ut": "7eea3edcaed734bea9cbfc24409ed989",
                    "klt": "101",
                    "fqt": "0",
                    "secid": f"{1 if stock.exchange == 'SSE' else 0}.{stock.code}",
                    "beg": lower.replace("-", ""),
                    "end": upper.replace("-", ""),
                    "lmt": "320",
                }
            response = client.get(ENDPOINTS[provider], params=params)
            blob = response.content
            (directory / f"{year}-response.json").write_bytes(blob)
            sources.append(
                {
                    "url": str(response.url),
                    "downloaded_at": now(),
                    "http_status": response.status_code,
                    "sha256": hashlib.sha256(blob).hexdigest(),
                }
            )
            (directory / "requests.json").write_text(json.dumps(sources, ensure_ascii=False, indent=2))
            response.raise_for_status()
            parsed = parse_response(provider, stock, response.json(), lower, upper)
            for row in parsed:
                if row[0] in seen:
                    raise DomainError("来源存在重复日期，未写入行情", "invalid_market_data", 502)
                seen.add(row[0])
                records.append(row)
    records.sort(key=lambda row: row[0])
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["date", *FIELDS])
    writer.writerows(records)
    content = output.getvalue().encode("utf-8-sig")
    (directory / "daily-raw.csv").write_bytes(content)
    return content, {
        "provider": provider,
        "price_basis": "raw",
        "original_volume_unit": "lots",
        "stored_volume_unit": "shares",
        "amount_unit": "CNY",
        "amount_available": provider == "Eastmoney",
        "sources": sources,
        "csv_sha256": hashlib.sha256(content).hexdigest(),
        "row_count": len(records),
    }


def _attempts(db, stock_id, target):
    return list(
        db.scalars(
            select(MarketUpdateRun)
            .where(MarketUpdateRun.stock_id == stock_id, MarketUpdateRun.session_date == target)
            .order_by(MarketUpdateRun.started_at.desc(), MarketUpdateRun.id.desc())
        )
    )


def _update_one(db, feed, target, stamp):
    stock = db.get(Stock, feed.stock_id)
    identifier = uid()
    run = MarketUpdateRun(id=identifier, stock_id=stock.id, session_date=target, started_at=stamp.isoformat())
    db.add(run)
    db.commit()
    directory = DATA_DIR / "audits" / "market-updates" / run.id
    directory.mkdir(parents=True)
    try:
        prior = current_bars(db, stock.id, "1d", feed.price_basis)
        if not prior:
            raise DomainError("请先导入该股票的基础日线，自动更新只负责补齐后续数据")
        cutoff = prior[-1]["time"]
        if cutoff > target:
            raise DomainError("已有数据晚于最近完整收盘日，请核对本机时间和数据")
        start = (datetime.fromisoformat(cutoff).date() - timedelta(days=7)).isoformat()
        run.result = {
            "provider": feed.provider,
            "previous_cutoff": cutoff,
            "requested_end": target,
            "artifacts": str(directory.relative_to(DATA_DIR)),
        }
        db.commit()
        content, source = download_daily(feed.provider, stock, start, target, directory)
        source.update({"source_import_id": feed.source_import_id, "update_run_id": run.id})
        if not source["row_count"]:
            run.status, run.error = "waiting", "来源暂无已收盘行情，可能延迟或停牌；稍后重试"
        else:
            batch = preview(db, stock.id, content, f"{stock.code}-{target}.csv", "1d", "raw", "shares", {})
            batch.metadata_json = {**batch.metadata_json, "source": source}
            db.commit()
            run.result = {**run.result, "import_id": batch.id, "report": batch.report}
            if batch.report["error_count"]:
                raise DomainError("来源行情未通过交易日或价格校验，未写入行情", "invalid_market_data", 502)
            if any(row["time"] > target for row in batch.rows):
                raise DomainError("响应含未收盘日期，未写入行情", "invalid_market_data", 502)
            if batch.report["conflicts"]:
                known = {row["time"]: row for row in prior}
                differences = [
                    {
                        "date": row["time"],
                        "fields": {
                            field: {"stored": known[row["time"]][field], "source": row[field]}
                            for field in FIELDS
                            if row[field] != known[row["time"]][field]
                        },
                    }
                    for row in batch.rows
                    if row["time"] in known
                    and any(row[field] != known[row["time"]][field] for field in FIELDS)
                ]
                run.result = {**run.result, "conflicts": differences[:30]}
                raise DomainError("来源改写了已有行情，本批次已暂停写入，请核对差异", "market_conflict", 409)
            observed = {row["time"] for row in batch.rows}
            gaps = [
                day.date().isoformat()
                for day in (
                    datetime.fromisoformat(cutoff) + timedelta(days=i)
                    for i in range(
                        1, (datetime.fromisoformat(target) - datetime.fromisoformat(cutoff)).days + 1
                    )
                )
                if is_open(db, day.date().isoformat()) and day.date().isoformat() not in observed
            ]
            committed = commit_import(db, batch.id, "skip")
            latest = current_bars(db, stock.id, "1d", "raw")[-1]["time"]
            run.result = {
                **run.result,
                "written": committed.report["written"],
                "skipped": committed.report["skipped"],
                "data_cutoff": latest,
                "missing_open_days": gaps,
            }
            if latest == target:
                feed.last_completed_session, run.status = target, "succeeded"
            else:
                run.status, run.error = "waiting", "已补齐可获取数据；当日行情尚未发布或停牌，稍后重试"
    except Exception as exc:
        details = dict(run.__dict__.get("result") or {})
        db.rollback()
        run = db.get(MarketUpdateRun, identifier)
        run.result = {**details, "error_type": type(exc).__name__}
        run.status = "failed"
        run.error = (
            exc.message if isinstance(exc, DomainError) else "行情请求或解析失败，保留原数据，等待重试"
        )
    run.finished_at = now()
    db.commit()
    (directory / "result.json").write_text(
        json.dumps(
            {"id": run.id, "session_date": target, "status": run.status, "error": run.error, **run.result},
            ensure_ascii=False,
            indent=2,
        )
    )
    return {
        "stock_id": feed.stock_id,
        "provider": feed.provider,
        "status": run.status,
        "error": run.error,
        **run.result,
    }


def update_due(stock_id=None, force=False, at=None):
    stamp = market_time(at)
    # Exclusive storage access also protects imports/snapshots and prevents incomplete backups.
    with update_lock(), storage_lock(exclusive=True), SessionLocal() as db:
        if stock_id:
            require(db.get(Stock, stock_id), "股票不存在")
        ensure_feeds(db)
        for run in db.scalars(select(MarketUpdateRun).where(MarketUpdateRun.status == "running")):
            run.status, run.error, run.finished_at = "interrupted", "上次更新进程中断，将重新检查", now()
        db.commit()
        target = latest_closed_session(db, stamp)
        feeds = list(db.scalars(select(MarketFeed).order_by(MarketFeed.stock_id)))
        if stock_id:
            feeds = [feed for feed in feeds if feed.stock_id == stock_id]
            if not feeds:
                raise DomainError("没有可核验的历史来源，暂不能自动更新该股票", "source_unconfigured", 409)
        results = []
        for feed in feeds:
            if not feed.enabled or feed.last_completed_session == target:
                continue
            attempts = _attempts(db, feed.stock_id, target)
            if (
                not force
                and attempts
                and (
                    len(attempts) >= MAX_ATTEMPTS
                    or stamp < datetime.fromisoformat(attempts[0].started_at) + RETRY_DELAY
                )
            ):
                continue
            results.append(_update_one(db, feed, target, stamp))
        return {"session_date": target, "checked_at": stamp.isoformat(), "results": results}


def update_status(db, stock_id, at=None):
    require(db.get(Stock, stock_id), "股票不存在")
    feed = db.get(MarketFeed, stock_id)
    if not feed:
        return {"configured": False, "message": "待更新服务核验导入来源；未知来源不会自动抓取"}
    runs = list(
        db.scalars(
            select(MarketUpdateRun)
            .where(MarketUpdateRun.stock_id == stock_id)
            .order_by(MarketUpdateRun.started_at.desc())
            .limit(10)
        )
    )
    stamp = market_time(at)
    calendar_error, next_check, target = None, None, None
    try:
        target = latest_closed_session(db, stamp)
        attempts = _attempts(db, stock_id, target)
        if feed.last_completed_session == target or len(attempts) >= MAX_ATTEMPTS:
            next_check = datetime.combine(
                datetime.fromisoformat(future_dates(db, target, 1)[0]).date(), CLOSE_CHECK, SHANGHAI
            )
        elif attempts:
            next_check = max(stamp, datetime.fromisoformat(attempts[0].started_at) + RETRY_DELAY)
        else:
            next_check = stamp
    except DomainError as exc:
        calendar_error = exc.message
    return {
        "configured": True,
        "enabled": feed.enabled,
        "provider": feed.provider,
        "provider_label": PROVIDERS[feed.provider],
        "price_basis": feed.price_basis,
        "schedule": "交易日15:30（北京时间）；后台每5分钟检查，失败间隔15分钟、每交易日最多6次",
        "last_completed_session": feed.last_completed_session,
        "target_session": target,
        "next_check_at": next_check.isoformat() if next_check else None,
        "calendar_error": calendar_error,
        "runs": [
            {
                "id": run.id,
                "session_date": run.session_date,
                "status": run.status,
                "started_at": run.started_at,
                "finished_at": run.finished_at,
                "error": run.error,
                "written": run.result.get("written", 0),
                "data_cutoff": run.result.get("data_cutoff"),
                "result": run.result,
            }
            for run in runs
        ],
    }
