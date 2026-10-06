import sqlite3
from backend.app.backup import backup, verify
from .test_imports import stock, preview, commit


def test_backup_restores_database_and_artifacts(client, tmp_path):
    from backend.app.db import engine
    import pytest

    if engine.dialect.name != "sqlite":
        pytest.skip("SQLite restore test; PostgreSQL dump requires external pg_dump")
    identifier = stock(client)
    commit(client, preview(client, identifier))
    client.post("/api/v1/snapshots", json={"stock_id": identifier})
    destination = tmp_path / "backup"
    result = backup(destination)
    assert result["verified_files"] >= 3
    with sqlite3.connect(destination / "database.sqlite") as db:
        assert db.execute("select code from stocks").fetchone()[0] == "000001"
        assert db.execute("select count(*) from bar_revisions").fetchone()[0] == 2
    assert verify(destination)["verified_files"] == result["verified_files"]
