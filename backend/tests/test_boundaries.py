import pytest
from backend.app.calendar import future_dates
from backend.app.db import SessionLocal
from backend.app.errors import DomainError
from backend.app.integrations import HttpAnalysisProvider
from .test_imports import stock


def test_unknown_calendar_is_blocked(client):
    with SessionLocal() as db:
        with pytest.raises(DomainError, match="交易日历未覆盖"):
            future_dates(db, "2025-12-31", 1)


def test_mutations_reject_remote_origins(client):
    response = client.post(
        "/api/v1/stocks",
        json={"exchange": "SSE", "code": "600000", "name": "测试"},
        headers={"Origin": "https://external.example"},
    )
    assert response.status_code == 403
    assert client.get("/api/v1/stocks").json() == []


def test_calendar_override_is_explicit(client):
    response = client.post(
        "/api/v1/calendar/import",
        data={"source": "test-calendar"},
        files={"file": ("calendar.csv", b"date,is_open\n2026-01-01,0\n2026-01-02,1\n")},
    )
    assert response.status_code == 200
    with SessionLocal() as db:
        assert future_dates(db, "2025-12-31", 1) == ["2026-01-02"]


def test_ai_provider_rejects_untrusted_protocol(monkeypatch):
    monkeypatch.setenv("AI_ANALYSIS_URL", "file:///tmp/private")
    with pytest.raises(ValueError, match="HTTPS"):
        HttpAnalysisProvider().analyze({})


def test_missing_data_and_validation(client):
    identifier = stock(client)
    response = client.post("/api/v1/training-runs", json={"stock_id": identifier})
    assert response.status_code == 409
    assert response.json()["code"] == "no_data"
    bad = client.post(
        "/api/v1/predictions", json={"stock_id": identifier, "horizon": "seven_months", "model_ids": ["x"]}
    )
    assert bad.status_code == 422
    assert bad.json()["code"] == "validation_error"
