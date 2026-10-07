import { useState } from "react";
import {
  Activity,
  ChartCandlestick,
  BrainCircuit,
  ChartLine,
  CalendarDays,
  Database,
  Monitor,
  Plus,
  CircleHelp,
  ChevronDown,
} from "lucide-react";
import { api, type Stock } from "./api";
import { useLoad } from "./components";
import { Market, AddStock } from "./Market";
import { Training } from "./Training";
import { Forecast } from "./Forecast";
import { Maintenance } from "./Maintenance";
import { CalendarUpload } from "./CalendarUpload";

export default function App() {
  const [maintenanceOpen, setMaintenanceOpen] = useState(false);
  const [calendarOpen, setCalendarOpen] = useState(false);
  const [page, setPage] = useState("market");
  const [stockId, setStockId] = useState("");
  const [basis, setBasis] = useState("raw");
  const [adding, setAdding] = useState(false);
  const [version, setVersion] = useState(0);
  const refresh = () => setVersion((v) => v + 1);
  const { data: stocks, error } = useLoad(
    () => api<Stock[]>("/stocks"),
    [version],
    true,
  );
  const stock = stocks?.find((s) => s.id === stockId) || stocks?.[0];
  return (
    <div className="app">
      <aside className="sidebar" aria-label="研究工作台侧栏">
        <a
          className="brand"
          href="#"
          aria-label="EVENT2 股票研究工作台"
          onClick={(e) => e.preventDefault()}
        >
          <span>
            <Activity size={22} aria-hidden="true" />
          </span>
          <div>
            EVENT<span className="brand-two">2</span>
            <small>股票研究工作台</small>
          </div>
        </a>
        <div className="nav-label">研究空间</div>
        <nav className="sidebar-nav" aria-label="研究页面">
          {[
            ["market", "行情研究", ChartCandlestick],
            ["training", "模型训练", BrainCircuit],
            ["prediction", "预测分析", ChartLine],
          ].map(([id, label, Icon]) => {
            const Glyph = Icon as typeof Activity;
            return (
              <button
                key={String(id)}
                type="button"
                className={page === id ? "active" : ""}
                aria-label={String(label)}
                aria-current={page === id ? "page" : undefined}
                title={String(label)}
                onClick={() => setPage(String(id))}
              >
                <Glyph size={22} strokeWidth={1.8} aria-hidden="true" />
                <span className="nav-text">{String(label)}</span>
                {page === id && <span className="nav-dot" aria-hidden="true" />}
              </button>
            );
          })}
        </nav>
        <div className="sidebar-footer">
          <button
            className="calendar-button"
            aria-label="数据与备份"
            title="数据与备份"
            onClick={() => setMaintenanceOpen(true)}
          >
            <Database size={18} aria-hidden="true" />
            <span className="nav-text">数据与备份</span>
          </button>
          <button
            type="button"
            className="calendar-button"
            aria-label="导入交易日历"
            title="导入交易日历"
            aria-haspopup="dialog"
            onClick={() => setCalendarOpen(true)}
          >
            <CalendarDays size={18} strokeWidth={1.8} aria-hidden="true" />
            <span className="nav-text">导入交易日历</span>
          </button>
          <div className="sidebar-note">
            <div className="sidebar-mode">
              <Monitor size={14} aria-hidden="true" /> 本机研究模式
            </div>
            <p>手动导入 · 按需分析</p>
          </div>
          <div className="sidebar-bottom">
            <CircleHelp size={14} aria-hidden="true" />
            <span>个人研究辅助 · 不执行交易</span>
          </div>
        </div>
      </aside>
      <div className="workspace">
        <header className="topbar">
          <div className="stock-control">
            <span className="live-dot" />
            <select
              aria-label="研究股票"
              value={stock?.id || ""}
              onChange={(e) => {
                setStockId(e.target.value);
              }}
            >
              {stocks?.length ? (
                stocks.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name}　{s.code}
                  </option>
                ))
              ) : (
                <option value="">选择研究股票</option>
              )}
            </select>
            <ChevronDown size={14} />
            <button
              className="icon-btn"
              onClick={() => setAdding(true)}
              aria-label="添加股票"
            >
              <Plus size={17} />
            </button>
          </div>
          <div className="topbar-right">
            <select
              aria-label="价格口径"
              value={basis}
              onChange={(e) => setBasis(e.target.value)}
            >
              <option value="raw">不复权</option>
              <option value="qfq">前复权</option>
              <option value="hfq">后复权</option>
            </select>
            <span className="local-chip">LOCAL / M3</span>
            <div className="avatar">研</div>
          </div>
        </header>
        <main>
          {error && (
            <div className="error banner">
              无法连接后端：{error}。请先启动本地服务并完成数据库迁移。
            </div>
          )}
          {page === "market" ? (
            <Market
              key={stock?.id + basis}
              stock={stock}
              basis={basis}
              version={version}
              refresh={refresh}
              addStock={() => setAdding(true)}
            />
          ) : page === "training" ? (
            <Training
              key={stock?.id + basis}
              stock={stock}
              basis={basis}
              version={version}
              refresh={refresh}
            />
          ) : (
            <Forecast
              key={stock?.id + basis}
              stock={stock}
              basis={basis}
              version={version}
              refresh={refresh}
            />
          )}
        </main>
        <footer>
          EVENT2 RESEARCH　·　可追溯的数据，可核查的模型
          <span>市场时间：Asia/Shanghai</span>
        </footer>
      </div>
      {maintenanceOpen && (
        <Maintenance
          stock={stock}
          basis={basis}
          close={() => setMaintenanceOpen(false)}
        />
      )}
      {calendarOpen && <CalendarUpload close={() => setCalendarOpen(false)} />}{" "}
      {adding && <AddStock close={() => setAdding(false)} added={refresh} />}
    </div>
  );
}
