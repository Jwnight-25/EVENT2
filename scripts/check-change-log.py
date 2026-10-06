"""CI verifies the log for each commit in the pushed/reviewed range."""
import json
import os
import subprocess
import sys

event = json.load(open(os.environ["GITHUB_EVENT_PATH"]))
base = event.get("before") or event.get("pull_request", {}).get("base", {}).get("sha")
head = os.getenv("GITHUB_SHA", "HEAD")
range_spec = f"{base}..{head}" if base and set(base) != {"0"} else head
commits = subprocess.check_output(["git", "rev-list", range_spec]).decode().splitlines()
failed = []
for commit in commits:
    changed = subprocess.check_output(["git", "diff-tree", "--root", "--no-commit-id", "--name-only", "-r", "-z", commit]).decode().split("\0")
    code = any(path.startswith(("backend/", "frontend/", "scripts/", ".github/", ".githooks/")) or path in ("compose.yaml", "pyproject.toml", "alembic.ini", "启动.command") for path in changed)
    if code and "修改记录.md" not in changed:
        failed.append(commit[:12])
if failed:
    print("以下代码提交缺少修改记录：", ", ".join(failed))
    sys.exit(1)
print("修改记录检查通过")
