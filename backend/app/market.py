import hashlib
import io
import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import select

from .config import DATA_DIR
from .db import uid
from .entities import BarCurrent, BarRevision, ImportBatch, Snapshot, Stock
from .errors import DomainError, require
from .calendar import is_open

FIELDS = ["open", "high", "low", "close", "volume", "amount"]
ALIASES = {"日期": "date", "时间": "date", "datetime": "date", "time": "date", "开盘": "open", "开盘价": "open", "最高": "high", "最高价": "high", "最低": "low", "最低价": "low", "收盘": "close", "收盘价": "close", "成交量": "volume", "成交额": "amount"}


def read_file(content, filename):
    if filename.lower().endswith(".xlsx"):
        frame = pd.read_excel(io.BytesIO(content), engine="openpyxl")
    elif filename.lower().endswith(".csv"):
        try:
            frame = pd.read_csv(io.BytesIO(content), encoding="utf-8-sig", dtype=str)
        except UnicodeDecodeError:
            frame = pd.read_csv(io.BytesIO(content), encoding="gb18030", dtype=str)
    else:
        raise DomainError("仅支持CSV或xlsx文件")
    if len(frame) > 100000:
        raise DomainError("单次最多导入100000行，请拆分文件")
    return frame


def bar_dict(bar):
    return {"revision_id": bar.id, "time": bar.time, **{key: float(getattr(bar, key)) if getattr(bar, key) is not None else None for key in FIELDS}}


def current_bars(db, stock_id, interval="1d", price_basis="raw", start=None, end=None):
    query = select(BarRevision).join(BarCurrent, BarCurrent.revision_id == BarRevision.id).where(BarCurrent.stock_id == stock_id, BarCurrent.interval == interval, BarCurrent.price_basis == price_basis)
    if start:
        query = query.where(BarCurrent.time >= start)
    if end:
        query = query.where(BarCurrent.time <= end)
    return [bar_dict(row) for row in db.scalars(query.order_by(BarCurrent.time))]


def preview(db, stock_id, content, filename, interval, price_basis, volume_unit, mapping):
    require(db.get(Stock, stock_id), "股票不存在")
    frame = read_file(content, filename)
    frame.columns = [str(c).strip().lower() for c in frame.columns]
    frame = frame.rename(columns={**ALIASES, **mapping})
    if not frame.columns.is_unique:
        raise DomainError("字段映射产生重复列")
    missing = set(["date", *FIELDS[:5]]) - set(frame.columns)
    if missing:
        raise DomainError("缺少必填列", details=sorted(missing))
    errors, rows, seen = [], [], set()
    existing = {r["time"]: r for r in current_bars(db, stock_id, interval, price_basis)}
    duplicates = conflicts = 0
    for index, row in frame.iterrows():
        try:
            stamp = pd.Timestamp(row["date"])
            if pd.isna(stamp):
                raise ValueError("日期为空")
            if interval == "1d":
                day = stamp.date().isoformat()
                if not is_open(db, day):
                    raise ValueError("日期不是交易日")
                time = day
            else:
                stamp = stamp.tz_localize("Asia/Shanghai") if stamp.tzinfo is None else stamp.tz_convert("Asia/Shanghai")
                hour = stamp.strftime("%H:%M")
                if not is_open(db, stamp.date().isoformat()) or not ("09:30" <= hour <= "11:30" or "13:00" <= hour <= "15:00"):
                    raise ValueError("分钟记录不在交易时段")
                time = stamp.isoformat()
            if time in seen:
                raise ValueError("文件内日期/时间重复")
            seen.add(time)
            values = {key: float(row[key]) for key in FIELDS[:5]}
            values["amount"] = None if "amount" not in row or pd.isna(row["amount"]) or row["amount"] == "" else float(row["amount"])
            if not all(np.isfinite(v) for v in values.values() if v is not None):
                raise ValueError("数值包含空值或无穷值")
            if min(values[k] for k in FIELDS[:4]) <= 0 or values["volume"] < 0 or (values["amount"] is not None and values["amount"] < 0):
                raise ValueError("价格必须为正，成交量/额不得为负")
            if not values["low"] <= min(values["open"], values["close"]) <= max(values["open"], values["close"]) <= values["high"]:
                raise ValueError("开高低收关系不合法")
            if volume_unit == "lots":
                values["volume"] *= 100
            values = {k: round(v, 6 if k in FIELDS[:4] else 4) if v is not None else None for k, v in values.items()}
            record = {"time": time, **values}
            if time in existing:
                if all(record[k] == existing[time][k] for k in FIELDS):
                    duplicates += 1
                else:
                    conflicts += 1
            rows.append(record)
        except DomainError:
            raise
        except (ValueError, TypeError, OverflowError) as exc:
            errors.append({"row": int(index) + 2, "message": str(exc)})
    rows.sort(key=lambda r: r["time"])
    identifier = uid()
    checksum = hashlib.sha256(content).hexdigest()
    path = DATA_DIR / "uploads" / f"{identifier}{Path(filename).suffix.lower()}"
    path.write_bytes(content)
    report = {"total": len(frame), "valid": len(rows), "errors": errors[:100], "error_count": len(errors), "duplicates": duplicates, "conflicts": conflicts, "new": len(rows) - duplicates - conflicts, "sample": rows[:5], "start": rows[0]["time"] if rows else None, "end": rows[-1]["time"] if rows else None}
    batch = ImportBatch(id=identifier, stock_id=stock_id, filename=Path(filename).name[:200], checksum=checksum, metadata_json={"interval": interval, "price_basis": price_basis, "volume_unit": volume_unit, "stored_volume_unit": "shares", "amount_unit": "CNY", "mapping": mapping, "path": str(path.relative_to(DATA_DIR))}, report=report, rows=rows)
    db.add(batch)
    db.commit()
    return batch


def commit_import(db, identifier, policy):
    batch = require(db.get(ImportBatch, identifier))
    if batch.status == "committed":
        return batch
    if batch.report["error_count"]:
        raise DomainError("存在错误行，请修正文件后重新预检", "import_errors", 409)
    if not batch.rows:
        raise DomainError("文件没有有效记录")
    added = skipped = 0
    metadata = batch.metadata_json
    for row in batch.rows:
        key = (batch.stock_id, metadata["interval"], metadata["price_basis"], row["time"])
        current = db.get(BarCurrent, key)
        if current:
            old = db.get(BarRevision, current.revision_id)
            if policy == "skip" or all(row[k] == bar_dict(old)[k] for k in FIELDS):
                skipped += 1
                continue
        revision = BarRevision(stock_id=batch.stock_id, import_id=batch.id, interval=metadata["interval"], price_basis=metadata["price_basis"], **row)
        db.add(revision)
        db.flush()
        if current:
            current.revision_id = revision.id
        else:
            db.add(BarCurrent(stock_id=key[0], interval=key[1], price_basis=key[2], time=key[3], revision_id=revision.id))
        added += 1
    batch.status = "committed"
    batch.report = {**batch.report, "written": added, "skipped": skipped, "policy": policy}
    db.commit()
    return batch


def completed_daily(rows):
    market_now = datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Shanghai"))
    today = market_now.date().isoformat()
    return [r for r in rows if r["time"] < today or (r["time"] == today and market_now.hour >= 15)]


def create_snapshot(db, stock_id, price_basis="raw"):
    rows = completed_daily(current_bars(db, stock_id, "1d", price_basis))
    if not rows:
        raise DomainError("没有可用的完整日线", "no_data", 409)
    identifier = uid()
    blob = json.dumps({"stock_id": stock_id, "price_basis": price_basis, "bars": rows, "transform_version": "daily-v1"}, ensure_ascii=False, sort_keys=True).encode()
    checksum = hashlib.sha256(blob).hexdigest()
    path = DATA_DIR / "snapshots" / f"{identifier}.json"
    path.write_bytes(blob)
    snapshot = Snapshot(id=identifier, stock_id=stock_id, cutoff=rows[-1]["time"], price_basis=price_basis, checksum=checksum, path=str(path.relative_to(DATA_DIR)), bar_count=len(rows))
    db.add(snapshot)
    db.flush()
    return snapshot


def load_snapshot(snapshot):
    content = (DATA_DIR / snapshot.path).read_bytes()
    if hashlib.sha256(content).hexdigest() != snapshot.checksum:
        raise DomainError("数据快照校验失败", "artifact_corrupt", 409)
    return json.loads(content)["bars"]


def aggregate(rows, interval):
    if not rows:
        return []
    if interval == "1mo":
        keys = [r["time"][:7] for r in rows]
    else:
        minutes = {"5m": 5, "30m": 30, "1h": 60}[interval]
        keys = []
        for row in rows:
            stamp = pd.Timestamp(row["time"])
            afternoon = stamp.hour >= 13
            base = stamp.normalize() + pd.Timedelta(hours=13 if afternoon else 9, minutes=0 if afternoon else 30)
            offset = max(0, min(119, int((stamp - base).total_seconds() // 60)))
            keys.append((base + pd.Timedelta(minutes=(offset // minutes) * minutes)).isoformat())
    groups = {}
    for key, row in zip(keys, rows):
        groups.setdefault(key, []).append(row)
    result = []
    for key, group in groups.items():
        result.append({"time": key if interval != "1mo" else key + "-01", "open": group[0]["open"], "high": max(r["high"] for r in group), "low": min(r["low"] for r in group), "close": group[-1]["close"], "volume": sum(r["volume"] for r in group), "amount": sum(r["amount"] for r in group) if all(r["amount"] is not None for r in group) else None, "derived": True})
    return result
