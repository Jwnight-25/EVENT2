#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

# Prefer the user's runtime, with a Codex-bundled fallback on this Mac.
task_runtime="$HOME/.cache/codex-runtimes/codex-primary-runtime/dependencies"
if ! command -v node >/dev/null 2>&1 && [ -x "$task_runtime/node/bin/node" ]; then
  export PATH="$task_runtime/node/bin:$PATH"
fi
if ! command -v node >/dev/null 2>&1; then
  echo '请先安装Node.js 22，再重新启动。'
  exit 1
fi
python_cmd="${PYTHON_BIN:-python3}"
"$python_cmd" -c 'import sys; assert (3,12) <= sys.version_info[:2] < (3,13), "请使用Python 3.12（可设置PYTHON_BIN）"'
if [ ! -x .venv/bin/python ]; then
  "$python_cmd" -m venv .venv
fi
if ! .venv/bin/python -c 'import fastapi, sqlalchemy, statsmodels, exchange_calendars' >/dev/null 2>&1; then
  .venv/bin/pip install -r backend/requirements-lock.txt
fi
if command -v pnpm >/dev/null 2>&1; then
  package_manager="$(command -v pnpm)"
elif [ -x "$task_runtime/bin/fallback/pnpm" ]; then
  package_manager="$task_runtime/bin/fallback/pnpm"
else
  echo '请安装pnpm 11（npm install -g pnpm@11），再重新启动。'
  exit 1
fi
if [ ! -d frontend/node_modules ]; then
  (cd frontend && "$package_manager" install --frozen-lockfile)
fi
.venv/bin/python -m backend.app.manage migrate
mkdir -p data/logs
pids=()
cleanup() { for pid in "${pids[@]}"; do kill "$pid" 2>/dev/null || true; done; }
trap cleanup EXIT INT TERM
.venv/bin/python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000 >data/logs/api.log 2>&1 &
pids+=("$!")
.venv/bin/python -m backend.app.worker >data/logs/worker.log 2>&1 &
pids+=("$!")
(cd frontend && "$package_manager" dev --strictPort) >data/logs/frontend.log 2>&1 &
pids+=("$!")
sleep 2
for pid in "${pids[@]}"; do
  if ! kill -0 "$pid" 2>/dev/null; then
    echo '启动失败，请查看data/logs；也请检查8000/5173端口是否已使用。'
    exit 1
  fi
done
echo 'EVENT2已启动：http://127.0.0.1:5173'
echo '请在浏览器打开上述地址。关闭此窗口或按Ctrl+C停止服务。'
wait
