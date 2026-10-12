"""Install a per-user macOS checker; market time is evaluated in Python, not launchd."""

import argparse
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys

LABEL = "com.event2.market-update"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--backup-dir", required=True, type=Path)
    parser.add_argument("--python", default=sys.executable, type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    project = args.project.resolve()
    # Preserve the venv executable's symlink path; resolving it would lose its environment.
    executable = args.python.absolute()
    if not (project / "backend/app/market_updates.py").is_file() or not executable.is_file():
        parser.error("项目目录或Python环境不完整，请先同步代码并安装依赖")
    data_dir, backup_dir = args.data_dir.resolve(), args.backup_dir.resolve()
    logs = data_dir / "logs"
    config = {
        "Label": LABEL,
        "ProgramArguments": [str(executable), "-m", "backend.app.manage", "market-cycle"],
        "WorkingDirectory": str(project),
        "EnvironmentVariables": {
            "DATA_DIR": str(data_dir),
            "BACKUP_DIR": str(backup_dir),
            "PYTHONUNBUFFERED": "1",
        },
        "RunAtLoad": True,
        "StartInterval": 300,
        "ProcessType": "Background",
        "ThrottleInterval": 60,
        "StandardOutPath": str(logs / "market-updater.log"),
        "StandardErrorPath": str(logs / "market-updater-error.log"),
    }
    if args.dry_run:
        print(plistlib.dumps(config).decode())
        return
    if sys.platform != "darwin":
        parser.error("此安装器仅支持macOS；其他系统可每5分钟运行manage market-cycle并常驻worker")
    subprocess.run([str(executable), "-c", "import httpx, sqlalchemy, exchange_calendars"], check=True)
    logs.mkdir(parents=True, exist_ok=True)
    agent_dir = Path.home() / "Library/LaunchAgents"
    agent_dir.mkdir(parents=True, exist_ok=True)
    domain = f"gui/{os.getuid()}"
    worker = {
        **config,
        "Label": "com.event2.worker",
        "ProgramArguments": [str(executable), "-m", "backend.app.worker"],
        "KeepAlive": True,
        "ThrottleInterval": 30,
        "StandardOutPath": str(logs / "worker-background.log"),
        "StandardErrorPath": str(logs / "worker-background-error.log"),
    }
    worker.pop("StartInterval")
    for item in (config, worker):
        label = item["Label"]
        target = agent_dir / f"{label}.plist"
        if target.exists():
            existing = plistlib.loads(target.read_bytes())
            if existing.get("Label") != label:
                raise SystemExit("已有配置标识不符，已保留原文件")
            subprocess.run(["launchctl", "bootout", domain, str(target)], capture_output=True)
        temporary = target.with_suffix(".plist.tmp")
        temporary.write_bytes(plistlib.dumps(item))
        temporary.chmod(0o644)
        temporary.replace(target)
        subprocess.run(["launchctl", "bootstrap", domain, str(target)], check=True)
        subprocess.run(["launchctl", "print", f"{domain}/{label}"], check=True, capture_output=True)
    print(
        json.dumps(
            {
                "installed": [str(agent_dir / f"{label}.plist") for label in (LABEL, "com.event2.worker")],
                "check_interval_seconds": 300,
                "market_close_check": "15:30 Asia/Shanghai",
                "python": str(executable),
                "project": str(project),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
