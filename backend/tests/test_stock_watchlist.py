from sqlalchemy import select, func
import pytest

from backend.app import daily_research as daily, market_updates, market_cycle as cycle, stock_watchlist
from backend.app.db import SessionLocal
from backend.app.entities import Stock, StockWatchEvent, BarCurrent, DailyBatch, DailyTask, Job, MarketFeed
from backend.app.storage import storage_lock
from .test_daily_research import prepared, stamp


def test_remove_restore_and_readd_keep_identity_and_market_records(client, monkeypatch):
    identifier, _ = prepared(client, monkeypatch)
    with SessionLocal() as db:
        prior = list(db.execute(select(BarCurrent.time, BarCurrent.revision_id)).all())
    response = client.delete(f"/api/v1/stocks/{identifier}")
    assert response.status_code == 200 and response.json()["data_preserved"]
    assert client.get("/api/v1/stocks").json() == []
    assert not client.get("/api/v1/stocks?include_inactive=true").json()[0]["is_active"]
    assert not client.delete(f"/api/v1/stocks/{identifier}").json()["changed"]
    body = {"exchange": "SZSE", "code": "000001", "name": "not a rename request"}
    restored = client.post("/api/v1/stocks", json=body)
    assert restored.status_code == 200 and restored.json()["id"] == identifier
    assert restored.json()["name"] == "Test only" and restored.json()["is_active"]
    assert client.post("/api/v1/stocks", json=body).status_code == 409
    assert client.post(f"/api/v1/stocks/{identifier}/restore").json()["id"] == identifier
    with SessionLocal() as db:
        assert list(db.execute(select(BarCurrent.time, BarCurrent.revision_id)).all()) == prior
        assert db.scalar(select(func.count()).select_from(Stock)) == 1
        assert [e.is_active for e in db.scalars(select(StockWatchEvent).order_by(StockWatchEvent.id))] == [
            False,
            True,
        ]


def test_removing_one_stock_keeps_other_selection_and_running_jobs(client, monkeypatch):
    identifier, _ = prepared(client, monkeypatch)
    clock = stamp("2024-01-04")
    monkeypatch.setattr(daily, "market_time", lambda at=None: at or clock)
    daily.daily_tick()
    with SessionLocal() as db:
        task = db.scalar(select(DailyTask))
        job = db.get(Job, task.training_job_id)
        job.status = "running"
        db.commit()
        job_id = job.id
    other = client.post(
        "/api/v1/stocks", json={"exchange": "SSE", "code": "600036", "name": "other test"}
    ).json()
    assert client.delete(f"/api/v1/stocks/{identifier}").status_code == 200
    assert [s["id"] for s in client.get("/api/v1/stocks").json()] == [other["id"]]
    with SessionLocal() as db:
        assert db.get(Job, job_id).status == "running" and not db.get(Job, job_id).cancel_requested
        assert db.get(DailyTask, task.id).status == "training"
        assert db.scalar(select(func.count()).select_from(DailyBatch)) == 1
    assert client.post(f"/api/v1/stocks/{identifier}/restore").status_code == 200
    assert len(client.get("/api/v1/stocks").json()) == 2


def test_removed_stocks_are_excluded_from_market_daily_and_news_scheduling(client, monkeypatch):
    identifier, _ = prepared(client, monkeypatch)
    client.delete(f"/api/v1/stocks/{identifier}")
    with SessionLocal() as db:
        db.get(MarketFeed, identifier).last_completed_session = "2024-01-02"
        db.commit()
    monkeypatch.setattr(market_updates, "_update_one", lambda *args: pytest.fail("removed stock fetched"))
    assert market_updates.update_due(at=stamp("2024-01-04"))["results"] == []
    other = client.post("/api/v1/stocks", json={"exchange": "SSE", "code": "600036", "name": "other"}).json()
    clock = stamp("2024-01-04")
    monkeypatch.setattr(cycle, "market_time", lambda: clock)
    monkeypatch.setattr(cycle, "update_due", lambda: {"results": []})
    monkeypatch.setattr(daily, "market_time", lambda at=None: at or clock)
    result = cycle.market_cycle()
    assert len(result["intelligence_jobs"]) == 1
    with SessionLocal() as db:
        assert db.get(Job, result["intelligence_jobs"][0]).payload["stock_id"] == other["id"]
        assert not list(db.scalars(select(DailyBatch).where(DailyBatch.stock_id == identifier)))
    assert client.post(f"/api/v1/intelligence/{identifier}/refresh").status_code == 409


def test_multiple_pauses_do_not_become_missed_preopen_sessions_after_restore(client, monkeypatch):
    identifier, _ = prepared(client, monkeypatch)
    clock = stamp("2024-01-04")
    monkeypatch.setattr(daily, "market_time", lambda at=None: at or clock)
    daily.daily_tick()
    for removed, restored, check in [
        ("2024-01-04T16:00:00+08:00", "2024-01-09T16:00:00+08:00", "2024-01-10"),
        ("2024-01-10T16:00:00+08:00", "2024-01-12T08:00:00+08:00", "2024-01-12"),
    ]:
        monkeypatch.setattr(stock_watchlist, "now", lambda: removed)
        client.delete(f"/api/v1/stocks/{identifier}")
        monkeypatch.setattr(stock_watchlist, "now", lambda: restored)
        client.post(f"/api/v1/stocks/{identifier}/restore")
        clock = stamp(check, 10)
        daily.daily_tick()
    with SessionLocal() as db:
        assert set(db.scalars(select(DailyBatch.session_date))) == {"2024-01-04", "2024-01-10", "2024-01-12"}


def test_remove_respects_origin_and_backup_lock_and_unknown_stock(client, monkeypatch):
    identifier, _ = prepared(client, monkeypatch)
    assert (
        client.delete(
            f"/api/v1/stocks/{identifier}", headers={"Origin": "https://not-local.example"}
        ).status_code
        == 403
    )
    with storage_lock(exclusive=True):
        assert client.delete(f"/api/v1/stocks/{identifier}").status_code == 409
    assert client.get("/api/v1/stocks").json()[0]["is_active"]
    assert client.delete("/api/v1/stocks/not-found").status_code == 404
    assert client.post("/api/v1/stocks/not-found/restore").status_code == 404


def test_waiting_data_is_paused_and_can_resume_before_deadline(client, monkeypatch):
    identifier, cutoff = prepared(client, monkeypatch)
    clock = stamp("2024-01-03", 17)
    monkeypatch.setattr(daily, "market_time", lambda at=None: at or clock)
    with SessionLocal() as db:
        db.get(MarketFeed, identifier).last_completed_session = "2024-01-02"
        db.commit()
    daily.daily_tick()
    client.delete(f"/api/v1/stocks/{identifier}")
    with SessionLocal() as db:
        assert db.scalar(select(DailyBatch)).status == "paused"
        assert not db.scalar(select(func.count()).select_from(DailyTask))
    status = client.get(f"/api/v1/market-updates/{identifier}").json()
    assert not status["enabled"] and status["next_check_at"] is None
    client.post(f"/api/v1/stocks/{identifier}/restore")
    assert daily.daily_tick()["batches"][0]["status"] == "waiting_data"
    with SessionLocal() as db:
        db.get(MarketFeed, identifier).last_completed_session = cutoff
        db.commit()
    assert daily.daily_tick()["batches"][0]["status"] == "training"
