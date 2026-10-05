# EVENT2 · A股研究辅助系统

个人本机股票研究工具，React＋FastAPI，PostgreSQL为目标数据库，SQLite为免安装本地模式。仅分析与展示，不执行交易。

## 基础结构

- `frontend/`：React、TypeScript、Vite。
- `backend/app/`：FastAPI与研究模块。
- `docs/requirements/`：三份需求基线。
- `修改记录.md`：每次提交前更新的改动、验证与限制。
- `compose.yaml`：可选PostgreSQL本机服务。

## 安装与启动

需要Python 3.12和Node.js 20.19+（推荐22）。在仓库根目录执行：

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
cd frontend
npm install
npm run dev
```

另开终端，在仓库根目录执行：

```sh
source .venv/bin/activate
python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

前端：http://127.0.0.1:5173；接口文档：http://127.0.0.1:8000/docs。

本地数据与密钥不会提交到GitHub。具体能力与验证随阶段记录在修改记录中。
