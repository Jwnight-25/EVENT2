# EVENT2 · A股研究辅助系统

个人本机股票研究工具，React＋FastAPI，PostgreSQL为目标数据库，SQLite为免安装本地模式。仅分析与展示，不执行交易。第一版已提供上传、行情、真实模型训练与验证、次日OHLC和20/60交易日预测。

## 基础结构

- `frontend/`：React、TypeScript、Vite。
- `backend/app/`：FastAPI与研究模块。
- `docs/requirements/`：三份需求基线。
- `修改记录.md`：每次提交前更新的改动、验证与限制。
- `compose.yaml`：可选PostgreSQL本机服务。
- `docs/implementation.md`：已实现能力、实验方法、接口预留和限制。

## 安装与启动

需要Python 3.12、Node.js 22和pnpm 11。本机Codex环境可自动使用已有Node/pnpm运行时。

Mac可双击“启动.command”，或在仓库根目录运行bash scripts/start-local.sh。首次启动会安装依赖、迁移数据库，启动API、独立训练工作进程和前端；关闭终端会停止服务。

手动安装：

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements-lock.txt
cd frontend
pnpm install --frozen-lockfile
pnpm dev
```

另开终端，在仓库根目录执行：

```sh
source .venv/bin/activate
python -m backend.app.manage migrate
python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

前端：http://127.0.0.1:5173；接口文档：http://127.0.0.1:8000/docs。

另开终端运行独立训练进程，否则任务会等待：

```sh
source .venv/bin/activate
python -m backend.app.worker
```

## 使用流程

1. 添加股票，确认代码和交易所。
2. 2026及以后先导入交易日历（date,is_open，包含全部自然日）。
3. 上传CSV/XLSX，确认日/分钟周期、复权口径和单位；预检后确认导入。
4. 模型训练页启动实验；数据不足或依赖缺失会明确记录。
5. 预测页选择已达标模型，生成结果并回看历史。不达标时允许没有预测。

可选模型：LightGBM需macOS的OpenMP，深度模型依赖较大，可按backend/requirements-optional.txt分项安装；不是默认启动所必需。尚未接入真实行情和联网AI供应商；参考买卖区间及7个月模型仍需单独验证与开发。详见实现说明。

## 检查与修改记录

```sh
.venv/bin/python -m pytest -q
.venv/bin/ruff check backend
cd frontend
pnpm build
```

GitHub CI同时验证SQLite、PostgreSQL和前端构建。新克隆后执行git config core.hooksPath .githooks启用提交检查；每次代码提交先追加修改记录.md，CI也会检查。

本地数据与密钥不会提交到GitHub。具体能力与验证随阶段记录在修改记录中。
