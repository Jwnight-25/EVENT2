# EVENT2 · A股研究辅助系统

个人本机股票研究工具，React＋FastAPI，PostgreSQL为目标数据库，SQLite为免安装本地模式。仅分析与展示，不执行交易。四个主页面为日K行情、模型训练、预测分析、信息情报。已提供每日自动盘前OHLC预测、收盘误差反馈，以及按需20/60交易日研究。

## 基础结构

- `frontend/`：React、TypeScript、Vite。
- `backend/app/`：FastAPI与研究模块。
- `docs/requirements/`：三份需求基线。
- `修改记录.md`：每次提交前更新的改动、验证与限制。
- `compose.yaml`：可选PostgreSQL本机服务。
- `docs/implementation.md`：已实现能力、实验方法、接口预留和限制。
- `docs/example-data.md`：本机招商银行样例的数据范围、格式、存储位置和核验结果。
- `docs/ruyi-example-data.md`：本机儒意电影（002739）日线、第二来源核对、历史缺口和完整备份记录。
- `docs/每日收盘更新.md`：固定数据来源、收盘时间、后台服务、重试与维护方式。
- `docs/自动盘前预测与情报.md`：自动日程、模型预算、真实收盘奖罚、有限偏差校正、新闻和研报来源。
- `docs/模型奖罚积分.md`：四价全对+5、最接近+2、最差−2、其余0的分精度排名、累计积分与真实首轮评分。
- `docs/参数数量与大小优化.md`：每次训练联合搜索模型结构和参数值、实际参数数目、系数幅度及预算内细化。

## 安装与启动

需要Python 3.12、Node.js 22和pnpm 11。本机Codex环境可自动使用已有Node/pnpm运行时。

Mac可双击“启动.command”，或在仓库根目录运行bash scripts/start-local.sh。首次启动会安装依赖、迁移数据库，启动API、工作进程和前端；如果已安装后台工作进程则复用。关闭启动终端会停止网页/API；已安装的两个LaunchAgent继续运行。

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

未安装后台服务时，另开终端运行独立工作进程，否则任务会等待：

```sh
source .venv/bin/activate
python -m backend.app.worker
```

## 使用流程

1. 添加股票，确认代码和交易所。
2. 2026及以后先导入交易日历（date,is_open，包含全部自然日）。
3. 上传CSV/XLSX，确认日/分钟周期、复权口径和单位；预检后确认导入。
4. 按[自动流程说明](docs/自动盘前预测与情报.md)安装后台服务。交易日15:30后补齐行情并评分，随即准备下一交易日模型与OHLC，夜间及休市日可继续准备、盘前检查复用同一批次，目标日09:15前冻结；下一批按已有真实反馈有限调参。预测页自动展示，无需点击生成。
5. 模型训练页仍可手动配置实验；中长期预测选择模型和周期按需生成。自动候选与手动验证入选分别标注，失败可没有结果。
6. 信息情报页浏览股票新闻、个股研报和行业研报；后台07:00—22:00每2小时更新，保留原文链接与发布时间。

可选模型：LightGBM需macOS的OpenMP，深度模型依赖较大，可按backend/requirements-optional.txt分项安装；不是默认启动所必需。已提供固定来源的每日收盘日线更新，默认北京时间15:30后由Mac后台检查；详见[每日收盘更新](docs/每日收盘更新.md)。AI供应商适配已实现，需在预测页“AI接入设置”填写服务、模型与密钥。解释通过按钮生成，交易建议通过对话框交流并保存偏好。参考买卖策略及7个月模型仍需单独验证与开发。详见[AI使用说明](docs/AI使用说明.md)和实现说明。

## 检查与修改记录

```sh
.venv/bin/python -m pytest -q
.venv/bin/ruff check backend
cd frontend
pnpm build
```

GitHub CI同时验证SQLite、PostgreSQL和前端构建。新克隆后执行git config core.hooksPath .githooks启用提交检查；每次代码提交先追加修改记录.md，CI也会检查。

本地数据与密钥不会提交到GitHub。具体能力与验证随阶段记录在修改记录中。


模型训练新增分阶段参数细化、拟合失败候选预检及独立的基准/区间证据标识；预测单独使用最新数据拟合并报告近期滚动复核，原指标和历史预测保持冻结。左侧“数据与备份”提供完整备份、校验、日历/数据状态和第二来源核对。多个本机入口可将.env中的DATA_DIR指向同一持久数据目录。方法与限制见[实现说明](docs/implementation.md)。
