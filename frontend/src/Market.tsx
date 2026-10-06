import { useState, useMemo } from "react";
import { Upload, Plus, ArrowUpRight, Download } from "lucide-react";
import { api, post, money, type Stock, type Bar } from "./api";
import { Chart, Empty, Modal, axis, useLoad } from "./components";

export function AddStock({
  close,
  added,
}: {
  close: () => void;
  added: () => void;
}) {
  const [exchange, setExchange] = useState("SSE");
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  return (
    <Modal title="添加研究股票" close={close}>
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          setBusy(true);
          try {
            await post("/stocks", { exchange, code, name });
            added();
            close();
          } catch (e) {
            setError((e as Error).message);
          } finally {
            setBusy(false);
          }
        }}
      >
        <label>
          交易所
          <select
            value={exchange}
            onChange={(e) => setExchange(e.target.value)}
          >
            <option value="SSE">上海证券交易所</option>
            <option value="SZSE">深圳证券交易所</option>
            <option value="BSE">北京证券交易所</option>
          </select>
        </label>
        <label>
          股票代码
          <input
            required
            pattern="[0-9]{6}"
            placeholder="六位代码"
            value={code}
            onChange={(e) => setCode(e.target.value)}
          />
        </label>
        <label>
          股票名称
          <input
            required
            maxLength={80}
            placeholder="输入名称"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </label>
        {error && <p className="error">{error}</p>}
        <button className="primary" disabled={busy}>
          添加股票
        </button>
      </form>
    </Modal>
  );
}

function UploadDialog({
  stock,
  basis,
  close,
  imported,
}: {
  stock: Stock;
  basis: string;
  close: () => void;
  imported: () => void;
}) {
  const [file, setFile] = useState<File>();
  const [interval, setInterval] = useState("1d");
  const [unit, setUnit] = useState("shares");
  const [mapping, setMapping] = useState("{}");
  const [preview, setPreview] = useState<any>();
  const [policy, setPolicy] = useState("skip");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function preflight() {
    if (!file) return;
    setBusy(true);
    setError("");
    try {
      const form = new FormData();
      form.append("file", file);
      form.append("stock_id", stock.id);
      form.append("interval", interval);
      form.append("price_basis", basis);
      form.append("volume_unit", unit);
      form.append("mapping", mapping);
      setPreview(await api("/imports/preview", { method: "POST", body: form }));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal title={"导入行情 · " + stock.name} close={close}>
      <p className="muted">
        CSV / XLSX · 最大20MB · 当前价格口径：{basis}。成交额须为元。
      </p>
      <a className="text-btn" download href="/daily-template.csv">
        <Download size={14} /> 下载日线字段模板
      </a>
      {!preview ? (
        <>
          <label className="dropzone">
            <Upload size={26} />
            <span>{file ? file.name : "选择行情文件"}</span>
            <input
              type="file"
              accept=".csv,.xlsx"
              onChange={(e) => setFile(e.target.files?.[0])}
            />
          </label>
          <div className="two-cols">
            <label>
              数据周期
              <select
                value={interval}
                onChange={(e) => setInterval(e.target.value)}
              >
                <option value="1d">日线</option>
                <option value="1m">一分钟线</option>
              </select>
            </label>
            <label>
              成交量单位
              <select value={unit} onChange={(e) => setUnit(e.target.value)}>
                <option value="shares">股</option>
                <option value="lots">手（100股）</option>
              </select>
            </label>
          </div>
          <details>
            <summary>自定义字段映射</summary>
            <p className="muted">
              支持常用中文列名；特殊列名使用JSON映射，例如{" "}
              {'{"交易日期":"date"}'}。
            </p>
            <textarea
              value={mapping}
              onChange={(e) => setMapping(e.target.value)}
            />
          </details>
          <button
            className="primary"
            disabled={!file || busy}
            onClick={preflight}
          >
            {busy ? "预检中…" : "检查数据"}
          </button>
        </>
      ) : (
        <>
          <div className="summary-grid">
            <div>
              <small>总行数</small>
              <strong>{preview.report.total}</strong>
            </div>
            <div>
              <small>新记录</small>
              <strong>{preview.report.new}</strong>
            </div>
            <div>
              <small>相同 / 冲突</small>
              <strong>
                {preview.report.duplicates} / {preview.report.conflicts}
              </strong>
            </div>
          </div>
          <p>
            {preview.report.start} → {preview.report.end}
          </p>
          {preview.report.errors.map((e: any) => (
            <p className="error" key={e.row}>
              第{e.row}行：{e.message}
            </p>
          ))}
          {preview.report.error_count > 100 && <p>仅显示前100条错误</p>}
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>日期</th>
                  <th>开</th>
                  <th>高</th>
                  <th>低</th>
                  <th>收</th>
                </tr>
              </thead>
              <tbody>
                {preview.report.sample.map((r: Bar) => (
                  <tr key={r.time}>
                    <td>{r.time}</td>
                    <td>{r.open}</td>
                    <td>{r.high}</td>
                    <td>{r.low}</td>
                    <td>{r.close}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <label>
            冲突处理
            <select value={policy} onChange={(e) => setPolicy(e.target.value)}>
              <option value="skip">跳过已存在时间（默认）</option>
              <option value="replace">创建新修订，保留旧记录</option>
            </select>
          </label>
          <div className="row">
            <button
              className="secondary"
              onClick={() => {
                setPreview(undefined);
                setError("");
              }}
            >
              重新选择
            </button>
            <button
              className="primary"
              disabled={
                busy || preview.report.error_count > 0 || !preview.report.valid
              }
              onClick={async () => {
                setBusy(true);
                try {
                  await post("/imports/" + preview.id + "/commit", { policy });
                  imported();
                  close();
                } catch (e) {
                  setError((e as Error).message);
                } finally {
                  setBusy(false);
                }
              }}
            >
              确认导入
            </button>
          </div>
        </>
      )}
      {error && <p className="error">{error}</p>}
    </Modal>
  );
}

export function Market({
  stock,
  basis,
  refresh,
  version,
  addStock,
}: {
  stock?: Stock;
  basis: string;
  refresh: () => void;
  version: number;
  addStock: () => void;
}) {
  const [interval, setInterval] = useState("1d");
  const [mode, setMode] = useState("k");
  const [upload, setUpload] = useState(false);
  const { data, error, loading } = useLoad(
    () =>
      stock
        ? api<{ bars: Bar[]; total: number; aggregation: string | null }>(
            "/market/bars?" +
              new URLSearchParams({
                stock_id: stock.id,
                interval,
                price_basis: basis,
                limit: "1000",
              }),
          )
        : Promise.resolve({ bars: [], total: 0, aggregation: null }),
    [stock?.id, interval, basis, version],
  );
  const rows = data?.bars || [];
  const last = rows.at(-1);
  const previous = rows.at(-2);
  const change =
    last && previous ? (last.close / previous.close - 1) * 100 : null;
  const chart = useMemo(
    () => ({
      backgroundColor: "transparent",
      tooltip: { trigger: "axis" as const },
      axisPointer: { link: [{ xAxisIndex: "all" as const }] },
      grid: [
        { left: 56, right: 25, top: 25, height: "62%" },
        { left: 56, right: 25, top: "77%", height: "11%" },
      ],
      xAxis: [
        {
          ...axis,
          type: "category" as const,
          data: rows.map((r) => r.time),
          boundaryGap: true,
        },
        {
          ...axis,
          type: "category" as const,
          data: rows.map((r) => r.time),
          gridIndex: 1,
          axisLabel: { show: false },
        },
      ],
      yAxis: [
        { ...axis, scale: true, type: "value" as const },
        { ...axis, type: "value" as const, gridIndex: 1, splitNumber: 2 },
      ],
      dataZoom: [
        {
          type: "inside" as const,
          xAxisIndex: [0, 1],
          start: Math.max(0, 100 - (120 / Math.max(1, rows.length)) * 100),
        },
        {
          type: "slider" as const,
          xAxisIndex: [0, 1],
          bottom: 0,
          height: 18,
          borderColor: "#e6eae2",
        },
      ],
      series: [
        mode === "k"
          ? {
              name: "行情",
              type: "candlestick" as const,
              data: rows.map((r) => [r.open, r.close, r.low, r.high]),
              itemStyle: {
                color: "#e06e64",
                color0: "#4a9982",
                borderColor: "#e06e64",
                borderColor0: "#4a9982",
              },
            }
          : {
              name: "收盘价",
              type: "line" as const,
              data: rows.map((r) => r.close),
              symbol: "none",
              lineStyle: { color: "#28765e", width: 2 },
            },
        {
          name: "成交量（股）",
          type: "bar" as const,
          xAxisIndex: 1,
          yAxisIndex: 1,
          data: rows.map((r) => r.volume),
          itemStyle: { color: "#cedcd3" },
        },
      ],
    }),
    [data, mode],
  );
  return (
    <>
      <div className="page-heading">
        <div>
          <span className="eyebrow">MARKET OVERVIEW</span>
          <h1>行情研究</h1>
          <p>从真实历史数据出发，观察价格与成交变化。</p>
        </div>
        <button
          className="primary"
          onClick={() => (stock ? setUpload(true) : addStock())}
        >
          <Upload size={17} /> 导入数据
        </button>
      </div>
      <div className="metric-grid">
        <div className="metric">
          <small>最近收盘价</small>
          <strong>¥ {money(last?.close)}</strong>
          <span className={change != null && change >= 0 ? "up" : "down"}>
            {change == null
              ? "暂无涨跌数据"
              : (change >= 0 ? "+" : "") + change.toFixed(2) + "%"}{" "}
            <ArrowUpRight size={14} />
          </span>
        </div>
        <div className="metric">
          <small>最高 / 最低</small>
          <strong>
            {money(last?.high)} <i>/</i> {money(last?.low)}
          </strong>
          <span>所选周期最后一根K线</span>
        </div>
        <div className="metric">
          <small>行情截止</small>
          <strong className="date-value">
            {last?.time.slice(0, 16) || "暂无数据"}
          </strong>
          <span>历史上传数据 · 非实时行情</span>
        </div>
        <div className="metric">
          <small>可见记录</small>
          <strong>
            {data?.total || 0}
            <i> 条</i>
          </strong>
          <span>市场时间 Asia/Shanghai</span>
        </div>
      </div>
      <section className="card">
        <div className="card-heading">
          <div>
            <h2>
              {stock ? stock.name + " · " + stock.code : "开始你的股票研究"}
            </h2>
            <p>
              {basis === "raw"
                ? "不复权"
                : basis === "qfq"
                  ? "前复权"
                  : "后复权"}{" "}
              · {data?.aggregation || "保留原始价格口径"}
            </p>
          </div>
          <div className="segmented">
            <button
              className={mode === "k" ? "active" : ""}
              onClick={() => setMode("k")}
            >
              K线
            </button>
            <button
              className={mode === "line" ? "active" : ""}
              onClick={() => setMode("line")}
            >
              走势
            </button>
          </div>
        </div>
        <div className="periods">
          {[
            ["1m", "1分"],
            ["5m", "5分"],
            ["30m", "半小时"],
            ["1h", "1小时"],
            ["1d", "日"],
            ["1mo", "月"],
          ].map(([value, label]) => (
            <button
              className={interval === value ? "active" : ""}
              key={value}
              onClick={() => setInterval(value)}
            >
              {label}
            </button>
          ))}
          <span>红涨 · 绿跌</span>
        </div>
        {error ? (
          <p className="error">{error}</p>
        ) : loading ? (
          <Empty title="正在读取行情" />
        ) : rows.length ? (
          <Chart option={chart} height={430} />
        ) : (
          <Empty title={stock ? "该周期暂无数据" : "还没有研究股票"}>
            {stock ? (
              "上传日线可查看日、月周期；分钟周期需要真实分钟数据。"
            ) : (
              <button className="secondary" onClick={addStock}>
                <Plus size={15} /> 添加第一只股票
              </button>
            )}
          </Empty>
        )}
      </section>
      <div className="notice">
        <strong>数据即研究基础</strong>
        <p>
          导入前检查交易日期、复权口径和成交量单位。每次训练和预测会保存数据快照，历史结果可以追溯。
        </p>
      </div>
      {upload && stock && (
        <UploadDialog
          key={stock.id + basis}
          stock={stock}
          basis={basis}
          close={() => setUpload(false)}
          imported={refresh}
        />
      )}
    </>
  );
}
