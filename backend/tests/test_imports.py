import json
from backend.app.db import SessionLocal
from backend.app.entities import Snapshot
from backend.app.market import load_snapshot, aggregate


def stock(client, code="000001"):
    response = client.post("/api/v1/stocks", json={"exchange": "SZSE", "code": code, "name": "测试股票"})
    assert response.status_code == 201
    return response.json()["id"]


def preview(client, identifier, price=10, basis="raw"):
    blob = f"date,open,high,low,close,volume\n2024-01-02,{price},{price+1},{price-1},{price},100\n2024-01-03,{price},{price+1},{price-1},{price},120\n"
    response = client.post("/api/v1/imports/preview", data={"stock_id": identifier, "price_basis": basis, "volume_unit": "lots"}, files={"file": ("sample.csv", blob.encode(), "text/csv")})
    assert response.status_code == 200, response.text
    return response.json()


def commit(client, batch, policy="skip"):
    response = client.post(f"/api/v1/imports/{batch['id']}/commit", json={"policy": policy})
    assert response.status_code == 200, response.text
    return response.json()


def test_revision_snapshot_and_stock_isolation(client):
    identifier = stock(client)
    commit(client, preview(client, identifier))
    snap = client.post("/api/v1/snapshots", json={"stock_id": identifier}).json()
    conflict = preview(client, identifier, 20)
    assert conflict["report"]["conflicts"] == 2
    commit(client, conflict, "replace")
    current = client.get("/api/v1/market/bars", params={"stock_id": identifier}).json()["bars"]
    assert current[0]["close"] == 20
    assert current[0]["volume"] == 10000
    with SessionLocal() as db:
        old = load_snapshot(db.get(Snapshot, snap["id"]))
        assert old[0]["close"] == 10
    second = stock(client, "000002")
    assert client.get("/api/v1/market/bars", params={"stock_id": second}).json()["bars"] == []
    assert client.get("/api/v1/market/bars", params={"stock_id": identifier, "interval": "1m"}).json()["bars"] == []


def test_invalid_rows_and_duplicate_commit(client):
    identifier = stock(client)
    batch = preview(client, identifier)
    commit(client, batch)
    assert commit(client, batch)["report"]["written"] == 2
    identical = preview(client, identifier)
    assert identical["report"]["duplicates"] == 2
    bad = client.post("/api/v1/imports/preview", data={"stock_id": identifier}, files={"file": ("bad.csv", b"date,open,high,low,close,volume\n2024-01-02,10,8,11,10,100\n", "text/csv")}).json()
    assert bad["report"]["error_count"] == 1
    assert client.post(f"/api/v1/imports/{bad['id']}/commit", json={"policy": "replace"}).status_code == 409


def test_aggregation_does_not_cross_lunch():
    def row(time, close):
        return {"time": time, "open": close, "high": close, "low": close, "close": close, "volume": 1, "amount": None}
    rows = [row("2024-01-02T11:29:00+08:00", 10), row("2024-01-02T13:00:00+08:00", 20)]
    result = aggregate(rows, "1h")
    assert len(result) == 2
    assert [r["close"] for r in result] == [10, 20]


def test_calendar_requires_complete_natural_dates(client):
    response = client.post("/api/v1/calendar/import", data={"source": "test"}, files={"file": ("calendar.csv", b"date,is_open\n2026-01-01,0\n2026-01-03,0\n")})
    assert response.status_code == 422


def test_mapping_errors_are_visible(client):
    identifier = stock(client)
    response = client.post("/api/v1/imports/preview", data={"stock_id": identifier, "mapping": json.dumps({"a": 1})}, files={"file": ("x.csv", b"a\n1")})
    assert response.status_code == 422
