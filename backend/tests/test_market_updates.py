import csv
import io
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select, func

from backend.app import market_updates as updates
from backend.app.db import SessionLocal
from backend.app.entities import ImportBatch, MarketFeed, MarketUpdateRun, BarRevision, Stock, Snapshot
from backend.app.errors import DomainError
from backend.app.market import current_bars, load_snapshot
from backend.app.storage import storage_lock
from .test_imports import stock, preview, commit


def moment(day, hour=16, minute=0):
    return datetime.fromisoformat(f"{day}T{hour:02d}:{minute:02d}:00+08:00")


def setup_stock(client, provider="Tencent", code="000001"):
    identifier = stock(client, code)
    batch = commit(client, preview(client, identifier))
    with SessionLocal() as db:
        record = db.get(ImportBatch, batch["id"])
        record.metadata_json = {**record.metadata_json, "source": {"provider": provider}}
        db.commit()
    return identifier


def fake_download(monkeypatch, extra=None):
    calls = []

    def download(provider, stock, start, end, directory):
        calls.append((provider, stock.code, start, end))
        with SessionLocal() as db:
            rows = current_bars(db, stock.id)
        values = [[r["time"], r["open"], r["high"], r["low"], r["close"], r["volume"], ""] for r in rows]
        values.extend(extra if extra is not None else [["2024-01-04", 10, 11, 9, 10.5, 13000, ""]])
        buffer = io.StringIO(newline="")
        writer = csv.writer(buffer)
        writer.writerow(["date", "open", "high", "low", "close", "volume", "amount"])
        writer.writerows(values)
        return buffer.getvalue().encode("utf-8-sig"), {"row_count": len(values), "provider": provider}

    monkeypatch.setattr(updates, "download_daily", download)
    return calls


def test_close_timezone_weekends_and_calendar_boundary(client):
    with SessionLocal() as db:
        assert updates.latest_closed_session(db, moment("2024-01-04", 15, 29)) == "2024-01-03"
        assert updates.latest_closed_session(db, moment("2024-01-04", 15, 30)) == "2024-01-04"
        assert (
            updates.latest_closed_session(db, datetime.fromisoformat("2024-01-04T07:30:00+00:00"))
            == "2024-01-04"
        )
        assert updates.latest_closed_session(db, moment("2024-01-07")) == "2024-01-05"
        assert updates.latest_closed_session(db, moment("2024-01-01")) == "2023-12-29"
        with pytest.raises(DomainError, match="交易日历"):
            updates.latest_closed_session(db, moment("2027-01-04"))


def test_source_pin_append_only_once_and_frozen_snapshot(client, monkeypatch):
    identifier = setup_stock(client)
    second = setup_stock(client, "Eastmoney", "000002")
    frozen = client.post("/api/v1/snapshots", json={"stock_id": identifier}).json()
    calls = fake_download(monkeypatch)
    result = updates.update_due(identifier, at=moment("2024-01-04"))
    assert result["results"][0]["written"] == 1
    assert result["results"][0]["status"] == "succeeded"
    assert updates.update_due(identifier, force=True, at=moment("2024-01-04", 17))["results"] == []
    assert len(calls) == 1 and calls[0][0] == "Tencent"
    with SessionLocal() as db:
        assert len(current_bars(db, identifier)) == 3
        assert len(current_bars(db, second)) == 2
        assert len(load_snapshot(db.get(Snapshot, frozen["id"]))) == 2
        batch = db.scalar(
            select(ImportBatch)
            .where(ImportBatch.stock_id == identifier)
            .order_by(ImportBatch.created_at.desc())
        )
        batch.metadata_json = {**batch.metadata_json, "source": {"provider": "Eastmoney"}}
        db.commit()
        updates.ensure_feeds(db)
        assert db.get(MarketFeed, identifier).provider == "Tencent"
        assert db.get(MarketFeed, second).provider == "Eastmoney"
        state = updates.update_status(db, identifier, moment("2024-01-04"))
        assert state["last_completed_session"] == "2024-01-04"
        assert state["next_check_at"].startswith("2024-01-05T15:30:00")


def test_failure_retry_budget_and_no_fallback(client, monkeypatch):
    identifier = setup_stock(client, "Eastmoney")
    calls = []

    def failed(provider, *_):
        calls.append(provider)
        raise RuntimeError("untrusted response that must not appear in the stored error")

    monkeypatch.setattr(updates, "download_daily", failed)
    stamp = moment("2024-01-04")
    for attempt in range(6):
        result = updates.update_due(identifier, at=stamp + timedelta(minutes=15 * attempt))
        assert result["results"][0]["status"] == "failed"
        assert "untrusted" not in result["results"][0]["error"]
        assert updates.update_due(identifier, at=stamp + timedelta(minutes=15 * attempt + 1))["results"] == []
    assert updates.update_due(identifier, at=stamp + timedelta(hours=5))["results"] == []
    assert calls == ["Eastmoney"] * 6
    with SessionLocal() as db:
        assert len(current_bars(db, identifier)) == 2
        assert db.get(MarketFeed, identifier).last_completed_session is None
    fake_download(monkeypatch)
    assert (
        updates.update_due(identifier, force=True, at=stamp + timedelta(hours=6))["results"][0]["status"]
        == "succeeded"
    )


def test_missing_target_remains_pending_and_invalid_rows_are_not_published(client, monkeypatch):
    identifier = setup_stock(client)
    fake_download(monkeypatch, [])
    # Missing target is pending, even if all existing rows match.
    pending = updates.update_due(identifier, at=moment("2024-01-04"))
    assert pending["results"][0]["status"] == "waiting"
    assert pending["results"][0]["written"] == 0
    fake_download(monkeypatch, [["2024-01-03", 10, 11, 9, 10, 12000, ""]])
    result = updates.update_due(identifier, force=True, at=moment("2024-01-04"))
    assert result["results"][0]["status"] == "failed"  # duplicate source date is rejected by preview
    fake_download(monkeypatch, [["2024-01-04", 10, 8, 12, 10, 100, ""]])
    assert (
        updates.update_due(identifier, force=True, at=moment("2024-01-04"))["results"][0]["status"]
        == "failed"
    )
    fake_download(monkeypatch, [["2024-01-06", 10, 11, 9, 10, 100, ""]])
    assert (
        updates.update_due(identifier, force=True, at=moment("2024-01-04"))["results"][0]["status"]
        == "failed"
    )
    with SessionLocal() as db:
        assert len(current_bars(db, identifier)) == 2


def test_conflicts_block_whole_batch_and_leave_trace(client, monkeypatch):
    identifier = setup_stock(client)

    def changed(provider, stock, start, end, directory):
        return (
            b"date,open,high,low,close,volume,amount\n2024-01-03,10,12,9,11,12000,\n2024-01-04,10,11,9,10.5,13000,\n",
            {"provider": provider, "row_count": 2},
        )

    monkeypatch.setattr(updates, "download_daily", changed)
    result = updates.update_due(identifier, at=moment("2024-01-04"))["results"][0]
    assert result["status"] == "failed" and result["conflicts"][0]["date"] == "2024-01-03"
    with SessionLocal() as db:
        assert len(current_bars(db, identifier)) == 2
        assert (
            db.scalar(select(func.count()).select_from(BarRevision).where(BarRevision.stock_id == identifier))
            == 2
        )
        run = db.scalar(select(MarketUpdateRun).where(MarketUpdateRun.stock_id == identifier))
        assert run.result["conflicts"][0]["fields"]["close"]["stored"] == 10
        assert (updates.DATA_DIR / run.result["artifacts"] / "result.json").exists()


def test_unknown_source_and_storage_mutex(client, monkeypatch):
    identifier = stock(client)
    commit(client, preview(client, identifier))
    assert client.get(f"/api/v1/market-updates/{identifier}").json()["configured"] is False
    with pytest.raises(DomainError, match="历史来源"):
        updates.update_due(identifier, at=moment("2024-01-04"))
    with storage_lock():
        with pytest.raises(DomainError, match="存储"):
            updates.update_due(at=moment("2024-01-04"))


def test_provider_mapping_units_and_identity():
    instrument = Stock(exchange="SZSE", code="002739", name="儒意电影")
    tx = {
        "code": 0,
        "data": {"sz002739": {"day": [["2026-09-30", "8.29", "8.22", "8.38", "8.16", "336633"]]}},
    }
    row = updates.parse_response("Tencent", instrument, tx, "2026-09-23", "2026-09-30")[0]
    assert row == ["2026-09-30", "8.29", "8.38", "8.16", "8.22", "33663300", ""]
    em = {
        "rc": 0,
        "data": {
            "code": "002739",
            "market": 0,
            "klines": ["2026-09-30,8.29,8.22,8.38,8.16,336633,277133200.80"],
        },
    }
    row = updates.parse_response("Eastmoney", instrument, em, "2026-09-23", "2026-09-30")[0]
    assert row[-2:] == ["33663300", "277133200.80"]
    em["data"]["code"] = "600036"
    with pytest.raises(DomainError, match="标识"):
        updates.parse_response("Eastmoney", instrument, em, "2026-09-23", "2026-09-30")
    with pytest.raises(DomainError, match="范围"):
        updates.parse_response("Tencent", instrument, tx, "2026-09-23", "2026-09-29")
    tx["data"]["sz002739"] = {"qfqday": []}
    with pytest.raises(DomainError, match="不复权"):
        updates.parse_response("Tencent", instrument, tx, "2026-09-23", "2026-09-30")


def test_manual_endpoint_owns_lock_and_publishes_status(client, monkeypatch):
    identifier = setup_stock(client)
    monkeypatch.setattr(updates, "market_time", lambda at=None: moment("2024-01-04"))
    fake_download(monkeypatch)
    response = client.post(f"/api/v1/market-updates/{identifier}/check", json={})
    assert response.status_code == 200, response.text
    assert response.json()["results"][0]["written"] == 1
    state = client.get(f"/api/v1/market-updates/{identifier}").json()
    assert state["provider"] == "Tencent" and state["runs"][0]["status"] == "succeeded"
    assert client.post(f"/api/v1/market-updates/{identifier}/check", json={}).json()["results"] == []


def test_future_session_and_interrupted_run_are_safe(client, monkeypatch):
    identifier = setup_stock(client)
    with SessionLocal() as db:
        updates.ensure_feeds(db)
        interrupted = MarketUpdateRun(
            stock_id=identifier, session_date="2024-01-04", started_at="2024-01-04T15:30:00+08:00"
        )
        db.add(interrupted)
        db.commit()
    fake_download(monkeypatch, [["2024-01-05", 10, 11, 9, 10, 13000, ""]])
    result = updates.update_due(identifier, force=True, at=moment("2024-01-04"))
    assert result["results"][0]["status"] == "failed"
    with SessionLocal() as db:
        assert db.get(MarketUpdateRun, interrupted.id).status == "interrupted"
        assert len(current_bars(db, identifier)) == 2
    fake_download(monkeypatch)
    assert (
        updates.update_due(identifier, force=True, at=moment("2024-01-04"))["results"][0]["status"]
        == "succeeded"
    )


def test_source_retry_budget_recovers_next_morning_without_changing_source(client, monkeypatch):
    identifier = setup_stock(client, "Eastmoney")

    def failed(*_):
        raise RuntimeError("temporary connection failure")

    monkeypatch.setattr(updates, "download_daily", failed)
    for attempt in range(6):
        updates.update_due(identifier, at=moment("2024-01-04") + timedelta(minutes=15 * attempt))
    calls = fake_download(monkeypatch)
    result = updates.update_due(identifier, at=moment("2024-01-05", 8))
    assert result["session_date"] == "2024-01-04"
    assert result["results"][0]["status"] == "succeeded"
    assert calls[0][0] == "Eastmoney"
