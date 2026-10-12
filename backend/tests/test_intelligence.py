import pytest
from sqlalchemy import select, func
from backend.app import intelligence as intel
from backend.app.db import SessionLocal
from backend.app.entities import IntelligenceItem, IntelligenceSync, Job
from backend.app.errors import DomainError
from .test_imports import stock


def test_jsonp_is_only_decoded_never_executed_and_links_are_safe():
    assert intel.decode_jsonp('callback({"result":1});') == {"result": 1}
    assert intel.decode_jsonp('{"result":2}') == {"result": 2}
    with pytest.raises(DomainError):
        intel.decode_jsonp('alert(1);callback({"result":1})')
    assert intel.safe_url("javascript:alert(1)") is None
    assert intel.safe_url("https://user:secret@example.com/a") is None
    assert intel.safe_url("https://example.com/a\n") is None
    assert intel.safe_url("https://finance.eastmoney.com/a/2026101212345.html")
    assert intel.clean("<em>公司</em> &amp; <b>行业</b>") == "公司 & 行业"
    assert intel.publication("2024-01-04 08:00:00") == "2024-01-04T00:00:00+00:00"
    assert intel.publication("unknown") is None


def test_refresh_coalesces_and_keeps_stock_isolation(client):
    identifier = stock(client)
    second = stock(client, "000002")
    a = client.post(f"/api/v1/intelligence/{identifier}/refresh").json()
    b = client.post(f"/api/v1/intelligence/{identifier}/refresh").json()
    c = client.post(f"/api/v1/intelligence/{second}/refresh").json()
    assert a["job_id"] == b["job_id"] and a["job_id"] != c["job_id"]


def test_collection_deduplicates_filters_unsafe_future_and_keeps_evidence(client, monkeypatch):
    identifier = stock(client)
    calls = []

    def fetch(kind, stock, directory, industry=None):
        calls.append((kind, industry))
        if kind == "industry_report":
            assert industry == "123"
        rows = [
            {
                "title": "<em>Example</em>",
                "url": f"https://example.com/{kind}",
                "summary": "source snippet",
                "publisher": "Test publisher",
                "published_at": None,
                "metadata": {"industry_code": "123", "association": "test only"},
            },
            {"title": "unsafe", "url": "javascript:evil", "metadata": {}},
            {
                "title": "future",
                "url": "https://example.com/future",
                "published_at": "2099-01-01",
                "metadata": {},
            },
        ]
        return rows, {"provider": "TestOnlyMock", "kind": kind}

    monkeypatch.setattr(intel, "fetch_source", fetch)
    with SessionLocal() as db:
        job = Job(kind="intelligence", payload={"stock_id": identifier})
        db.add(job)
        db.commit()
    intel.intelligence_job(job.id)
    intel.intelligence_job(job.id)
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(IntelligenceItem)) == 3
        items = list(db.scalars(select(IntelligenceItem)))
        assert all(r.title == "Example" and r.published_at is None for r in items)
        assert db.get(Job, job.id).result["successful_sources"] == 3
        assert db.scalar(select(IntelligenceSync)).result["skipped"] == 2
    assert calls[2] == ("industry_report", "123")


def test_source_failure_never_removes_prior_news(client, monkeypatch):
    identifier = stock(client)
    with SessionLocal() as db:
        db.add(
            IntelligenceItem(
                stock_id=identifier,
                fingerprint="f" * 64,
                kind="news",
                title="Existing",
                url="https://example.com/existing",
                publisher="Test",
                summary="",
                metadata_json={},
            )
        )
        job = Job(kind="intelligence", payload={"stock_id": identifier})
        db.add(job)
        db.commit()

    def failed(*_):
        raise RuntimeError("untrusted provider text")

    monkeypatch.setattr(intel, "fetch_source", failed)
    with pytest.raises(DomainError, match="均未完成"):
        intel.intelligence_job(job.id)
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(IntelligenceItem)) == 1
        records = list(db.scalars(select(IntelligenceSync)))
        assert all(r.status == "failed" and "untrusted" not in r.error for r in records)


def test_cursor_is_stable_and_cannot_cross_stocks(client):
    identifier = stock(client)
    second = stock(client, "000002")
    with SessionLocal() as db:
        for i in range(7):
            db.add(
                IntelligenceItem(
                    id=f"{i:036d}",
                    stock_id=identifier,
                    fingerprint=f"{i:064d}",
                    kind="news",
                    title=f"item {i}",
                    url=f"https://example.com/{i}",
                    publisher="Test",
                    summary="",
                    published_at="2024-01-04T00:00:00+00:00",
                    retrieved_at="2024-01-04T01:00:00+00:00",
                    metadata_json={},
                )
            )
        db.commit()
    first = client.get(f"/api/v1/intelligence/{identifier}?limit=3").json()
    next_page = client.get(f"/api/v1/intelligence/{identifier}?limit=3&before={first['next_cursor']}").json()
    assert {r["id"] for r in first["items"]}.isdisjoint(r["id"] for r in next_page["items"])
    assert len(next_page["items"]) == 3
    assert client.get(f"/api/v1/intelligence/{second}?before={first['next_cursor']}").status_code == 404
