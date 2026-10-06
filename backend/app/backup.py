import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess

from .config import DATA_DIR
from .db import engine, now


def verify(destination):
    root = Path(destination).resolve()
    manifest = json.loads((root / "manifest.json").read_text())
    for name, expected in manifest["files"].items():
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
    with open(DATA_DIR / "worker.lock", "a") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("请先停止工作进程，再进行备份")
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
        for name in ("uploads", "snapshots", "models"):
            shutil.copytree(DATA_DIR / name, root / "data" / name)
        checksums = {
            str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob("*")
            if path.is_file()
        }
        (root / "manifest.json").write_text(
            json.dumps({"created_at": now(), "database": engine.dialect.name, "files": checksums}, indent=2)
        )
    return verify(root)
