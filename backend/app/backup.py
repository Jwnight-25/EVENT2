import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess

from .config import DATA_DIR
from .db import engine, now, SessionLocal
from .entities import Job
from .storage import storage_lock
from sqlalchemy import select, func
from .errors import DomainError


def verify(destination):
    root = Path(destination).resolve()
    manifest = json.loads((root / "manifest.json").read_text())
    if (
        not isinstance(manifest, dict)
        or manifest.get("database") not in ("sqlite", "postgresql")
        or not isinstance(manifest.get("created_at"), str)
        or not isinstance(manifest.get("files"), dict)
        or ("database.sqlite" if manifest["database"] == "sqlite" else "database.dump")
        not in manifest["files"]
    ):
        raise ValueError("备份清单不完整或格式错误")
    for name, expected in manifest["files"].items():
        if not isinstance(name, str) or not isinstance(expected, str):
            raise ValueError("备份清单文件校验值错误")
        path = (root / name).resolve()
        if (
            not path.is_relative_to(root)
            or not path.is_file()
            or hashlib.sha256(path.read_bytes()).hexdigest() != expected
        ):
            raise ValueError(f"备份校验失败：{name}")
    return {
        "verified_files": len(manifest["files"]),
        "created_at": manifest["created_at"],
        "database": manifest["database"],
    }


def backup(destination):
    root = Path(destination).resolve()
    if root.exists():
        raise ValueError("备份目录已存在，请使用新的目录，避免覆盖")
    if root.is_relative_to(DATA_DIR):
        raise ValueError("备份目录不能位于数据目录内部")
    with storage_lock(exclusive=True):
        with SessionLocal() as db:
            if db.scalar(select(func.count()).select_from(Job).where(Job.status.in_(["queued", "running"]))):
                raise DomainError("请等待训练、预测及AI任务完成，再备份", "jobs_active", 409)
        root.mkdir(parents=True)
        if engine.dialect.name == "sqlite":
            source = sqlite3.connect(engine.url.database)
            target = sqlite3.connect(root / "database.sqlite")
            try:
                source.backup(target)
            finally:
                source.close()
                target.close()
        elif engine.dialect.name == "postgresql":
            if not shutil.which("pg_dump"):
                raise ValueError("PostgreSQL备份需要安装与服务器兼容的pg_dump")
            url = engine.url
            env = {
                **os.environ,
                "PGHOST": url.host or "127.0.0.1",
                "PGPORT": str(url.port or 5432),
                "PGUSER": url.username or "",
                "PGPASSWORD": url.password or "",
                "PGDATABASE": url.database or "",
            }
            result = subprocess.run(
                ["pg_dump", "--format=custom", "--no-owner", "--file", str(root / "database.dump")],
                env=env,
                capture_output=True,
            )
            if result.returncode:
                raise ValueError("pg_dump失败，请检查数据库连接及工具版本；错误正文未保存以避免泄露配置")
        else:
            raise ValueError("不支持该数据库备份")
        for name in ("uploads", "snapshots", "models", "audits"):
            if (DATA_DIR / name).exists():
                shutil.copytree(DATA_DIR / name, root / "data" / name)
        checksums = {
            str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob("*")
            if path.is_file()
        }
        (root / "manifest.json").write_text(
            json.dumps(
                {
                    "created_at": now(),
                    "database": engine.dialect.name,
                    "source_data_dir": str(DATA_DIR),
                    "format_version": 2,
                    "files": checksums,
                },
                indent=2,
            )
        )
    return verify(root)


def restore_new(source, destination):
    """Restore SQLite into a NEW directory; never overwrite a running database."""
    checked = verify(source)
    if checked["database"] != "sqlite":
        raise ValueError("PostgreSQL请在新数据库中使用pg_restore恢复")
    root = Path(destination).resolve()
    if root.exists() or root == DATA_DIR or root.is_relative_to(DATA_DIR):
        raise ValueError("恢复目标必须是数据目录外、尚不存在的新目录")
    root.mkdir(parents=True)
    try:
        shutil.copy2(Path(source) / "database.sqlite", root / "research.db")
        for name in ("uploads", "snapshots", "models", "audits"):
            original = Path(source) / "data" / name
            if original.exists():
                shutil.copytree(original, root / name)
            else:
                (root / name).mkdir()
        with sqlite3.connect(root / "research.db") as db:
            if (
                db.execute("pragma quick_check").fetchone()[0] != "ok"
                or db.execute("pragma foreign_key_check").fetchall()
            ):
                raise ValueError("恢复数据库完整性检查失败")
            # A crashed queued task must not be replayed against historical input.
            db.execute(
                "update jobs set status='cancelled', error='备份恢复后取消未完成任务' where status in ('queued','running')"
            )
        (root / "restore.json").write_text(
            json.dumps({"source": str(Path(source).resolve()), **checked}, ensure_ascii=False, indent=2)
        )
    except Exception:
        # Preserve any partial output for inspection; never delete another directory.
        raise
    return {"destination": str(root), **checked}
