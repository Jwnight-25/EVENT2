"""Visible data provenance, comparison audits and complete local backup status."""

import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import numpy as np
import pandas as pd
from sqlalchemy import select, func
from .config import DATA_DIR, BACKUP_DIR
from .db import now, uid, engine
from .entities import Stock, CalendarDay, ImportBatch, Model, Snapshot, BarCurrent
from .market import current_bars, read_file, ALIASES
from .calendar import calendar, is_open, future_dates
from .errors import DomainError, require


def backup_list():
    if not BACKUP_DIR.exists():
        return []
    results = []
    for path in BACKUP_DIR.glob("*/manifest.json"):
        try:
            manifest = json.loads(path.read_text())
            results.append(
                {
                    "id": path.parent.name,
                    "created_at": manifest["created_at"],
                    "file_count": len(manifest["files"]),
                    "database": manifest["database"],
                }
            )
        except (ValueError, KeyError):
            continue
    return sorted(results, key=lambda item: item["created_at"], reverse=True)[:20]


def data_status(db, stock_id, basis):
    stock = require(db.get(Stock, stock_id))
    rows = current_bars(db, stock.id, "1d", basis)
    present = {r["time"] for r in rows}
    missing, unknown = [], []
    if rows:
        for day in pd.date_range(rows[0]["time"], rows[-1]["time"]):
            value = day.date().isoformat()
            try:
                if is_open(db, value) and value not in present:
                    missing.append(value)
            except DomainError:
                unknown.append(value)
    custom_end = db.scalar(select(func.max(CalendarDay.date)))
    built_end = str(calendar().last_session.date())
    cutoff = rows[-1]["time"] if rows else None
    support = {}
    for steps in (1, 20, 60):
        try:
            support[str(steps)] = future_dates(db, cutoff, steps)[-1] if cutoff else None
        except DomainError:
            support[str(steps)] = None
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
    complete_day = (
        today
        if datetime.now(ZoneInfo("Asia/Shanghai")).hour >= 15
        else (datetime.now(ZoneInfo("Asia/Shanghai")).date() - timedelta(days=1)).isoformat()
    )
    pending = []
    if cutoff:
        for day in pd.date_range(pd.Timestamp(cutoff) + pd.Timedelta(days=1), complete_day):
            try:
                if is_open(db, day.date().isoformat()):
                    pending.append(day.date().isoformat())
            except DomainError:
                break
    audits = []
    for path in sorted((DATA_DIR / "audits").glob("*.json"), reverse=True):
        try:
            value = json.loads(path.read_text())
            if value.get("stock_id") == stock_id and value.get("price_basis") == basis:
                audits.append(value)
        except ValueError:
            continue
        if len(audits) >= 5:
            break
    imports = list(
        db.scalars(
            select(ImportBatch)
            .where(ImportBatch.stock_id == stock_id, ImportBatch.status == "committed")
            .order_by(ImportBatch.created_at.desc())
            .limit(10)
        )
    )
    return {
        "data_dir": str(DATA_DIR),
        "database": engine.dialect.name,
        "backup_dir": str(BACKUP_DIR),
        "bars": len(rows),
        "data_start": rows[0]["time"] if rows else None,
        "data_cutoff": cutoff,
        "calendar_end": max(custom_end or "", built_end),
        "forecast_dates_supported": support,
        "missing_open_days": missing[:30],
        "missing_open_day_count": len(missing),
        "unknown_calendar_days": len(unknown),
        "pending_open_days": pending[:30],
        "pending_open_day_count": len(pending),
        "minute_bars": db.scalar(
            select(func.count())
            .select_from(BarCurrent)
            .where(BarCurrent.stock_id == stock_id, BarCurrent.interval == "1m")
        ),
        "price_basis": basis,
        "raw_price_warning": basis == "raw",
        "snapshots": db.scalar(
            select(func.count()).select_from(Snapshot).where(Snapshot.stock_id == stock_id)
        ),
        "models": db.scalar(select(func.count()).select_from(Model).where(Model.stock_id == stock_id)),
        "imports": [
            {
                "filename": i.filename,
                "checksum": i.checksum,
                "created_at": i.created_at,
                "source": i.metadata_json.get("source"),
            }
            for i in imports
        ],
        "audits": audits,
        "backups": backup_list(),
    }


def compare_upload(db, stock_id, basis, content, filename, source, volume_unit="shares"):
    require(db.get(Stock, stock_id))
    frame = read_file(content, filename)
    frame.columns = [str(c).strip().lower() for c in frame.columns]
    frame = frame.rename(columns=ALIASES)
    fields = ["open", "high", "low", "close", "volume"]
    if not frame.columns.is_unique or not set(["date", *fields]).issubset(frame.columns):
        raise DomainError("对照文件需要唯一的date/open/high/low/close/volume列")
    existing = {r["time"]: r for r in current_bars(db, stock_id, "1d", basis)}
    differences, missing, seen = [], [], set()
    compared = 0
    for _, row in frame.iterrows():
        try:
            stamp = pd.Timestamp(row["date"])
            if pd.isna(stamp):
                raise ValueError("日期为空")
            day = stamp.date().isoformat()
            if day in seen:
                raise ValueError("日期重复")
            seen.add(day)
            values = {field: float(row[field]) for field in fields}
            if volume_unit == "lots":
                values["volume"] *= 100
            if (
                not all(np.isfinite(v) for v in values.values())
                or min(values[f] for f in fields[:4]) <= 0
                or values["volume"] < 0
            ):
                raise ValueError("数值不合法")
            if (
                not values["low"]
                <= min(values["open"], values["close"])
                <= max(values["open"], values["close"])
                <= values["high"]
            ):
                raise ValueError("开高低收关系错误")
        except (ValueError, TypeError, OverflowError):
            raise DomainError("对照文件含无效、重复日期或不合法行情数值")
        current = existing.get(day)
        if current is None:
            missing.append(day)
            continue
        compared += 1
        mismatch = {
            f: {"stored": current[f], "comparison": values[f]}
            for f in fields
            if abs(current[f] - values[f]) > (0.011 if f != "volume" else max(1, current[f] * 0.001))
        }
        if mismatch:
            differences.append({"date": day, "fields": mismatch})
    if not compared:
        raise DomainError("对照文件与当前口径日线没有重合日期")
    audit = {
        "id": uid(),
        "created_at": now(),
        "stock_id": stock_id,
        "price_basis": basis,
        "source": source,
        "filename": Path(filename).name,
        "checksum": hashlib.sha256(content).hexdigest(),
        "compared": compared,
        "different_days": len(differences),
        "matching_days": compared - len(differences),
        "absent_days": len(missing),
        "differences": differences[:30],
        "note": "仅核对重合日期；价格容差0.011元、成交量容差0.1%或1股。来源由用户提供，未证明供应商独立；不修改原行情",
    }
    input_path = DATA_DIR / "audits" / (audit["id"] + Path(filename).suffix.lower())
    input_path.write_bytes(content)
    audit["input_path"] = str(input_path.relative_to(DATA_DIR))
    (DATA_DIR / "audits" / f"{audit['created_at'].replace(':', '')}-{audit['id']}.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2)
    )
    return audit
