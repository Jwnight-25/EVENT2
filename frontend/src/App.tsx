import { useState } from "react";
import {
  Activity,
  ChartCandlestick,
  FlaskConical,
  Telescope,
  Plus,
  CircleHelp,
  ChevronDown,
} from "lucide-react";
import { api, type Stock } from "./api";
import { useLoad } from "./components";
import { Market, AddStock } from "./Market";
import { Training } from "./Training";
import { Forecast } from "./Forecast";
import { CalendarUpload } from "./CalendarUpload";

export default function App() {
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
      <aside className="sidebar">
        <a className="brand" href="#" onClick={(e) => e.preventDefault()}>
          <span>
            <Activity size={24} />
          </span>
          <div>
            EVENT<span className="brand-two">2</span>
            <small>股票研究工作台</small>
          </div>
        </a>
        <div className="nav-label">研究空间</div>
        <nav>
          {[
            ["market", "行情研究", ChartCandlestick],
            ["training", "模型训练", FlaskConical],
            ["prediction", "预测分析", Telescope],
          ].map(([id, label, Icon]) => {
            const Glyph = Icon as typeof Activity;
            return (
              <button
                key={String(id)}
                className={page === id ? "active" : ""}
                onClick={() => setPage(String(id))}
              >
                <Glyph size={19} />
                {String(label)}
                {page === id && <span className="nav-dot" />}
              </button>
            );
          })}
        </nav>
        <div className="sidebar-note">
          <span className="live-dot" /> 本机研究模式
          <p>
            手动导入 · 按需分析
            <br />
            数据留在你的电脑
          </p>
        </div>
        <button
          className="calendar-button"
          onClick={() => setCalendarOpen(true)}
        >
          导入交易日历
        </button>
        <div className="sidebar-bottom">
          <CircleHelp size={16} />
          <span>个人研究辅助 · 不执行交易</span>
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
          EVENT2 RESEARCH　·　可追溯的数据，独立验证的模型
          <span>市场时间：Asia/Shanghai</span>
        </footer>
      </div>
      {calendarOpen && <CalendarUpload close={() => setCalendarOpen(false)} />}{" "}
      {adding && <AddStock close={() => setAdding(false)} added={refresh} />}
    </div>
  );
}
