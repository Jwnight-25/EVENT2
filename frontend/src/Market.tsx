import { useState, useMemo, useEffect } from "react";
import {
  Upload,
  Plus,
  ArrowUpRight,
  Download,
  ZoomIn,
  ZoomOut,
  RotateCcw,
} from "lucide-react";
import { api, post, money, type Stock, type Bar } from "./api";
import {
  Chart,
  Empty,
  Modal,
  axis,
  useLoad,
  type ChartZoom,
} from "./components";
import type { EChartsOption } from "echarts";
import { TimeScrollbar } from "./TimeScrollbar";
import {
  FALL,
  RISE,
  volumeColor,
  priceBounds,
  rangeForDates,
  recentWindow,
  visibleIndices,
  zoomWindow,
  type WindowRange,
} from "./chartUtils";

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
  const [window, setWindow] = useState<WindowRange | null>(null);
  const [dateStart, setDateStart] = useState("");
  const [dateEnd, setDateEnd] = useState("");
  const [priceMin, setPriceMin] = useState("");
  const [priceMax, setPriceMax] = useState("");
  const [fixedPrices, setFixedPrices] = useState<{
    min: number;
    max: number;
  } | null>(null);
  const [controlError, setControlError] = useState("");
  const { data, error, loading } = useLoad(
    () =>
      stock
        ? api<{ bars: Bar[]; total: number; aggregation: string | null }>(
            "/market/bars?" +
              new URLSearchParams({
                stock_id: stock.id,
                interval,
                price_basis: basis,
                limit: "10000",
              }),
          )
        : Promise.resolve({ bars: [], total: 0, aggregation: null }),
    [stock?.id, interval, basis, version],
  );
  const rows = data?.bars || [];
  const range = window || recentWindow(rows.length);
  const [firstIndex, lastIndex] = visibleIndices(rows.length, range);
  const prices = fixedPrices || priceBounds(rows, range);
  useEffect(() => {
    const [first, last] = visibleIndices(
      data?.bars.length || 0,
      window || recentWindow(data?.bars.length || 0),
    );
    setDateStart(data?.bars[first]?.time.slice(0, 10) || "");
    setDateEnd(data?.bars[last]?.time.slice(0, 10) || "");
  }, [data]);
  function changeWindow(next: WindowRange) {
    setWindow(next);
    const [first, last] = visibleIndices(rows.length, next);
    setDateStart(rows[first]?.time.slice(0, 10) || "");
    setDateEnd(rows[last]?.time.slice(0, 10) || "");
    setControlError("");
  }
  function resetPrice() {
    setFixedPrices(null);
    setPriceMin("");
    setPriceMax("");
    setControlError("");
  }
  function onZoom(ranges: ChartZoom[]) {
    const horizontal = ranges.find((r) => r.id === "market-time-inside");
    if (horizontal)
      changeWindow({ start: horizontal.start, end: horizontal.end });
  }
  const last = rows.at(-1);
  const previous = rows.at(-2);
  const change =
    last && previous ? (last.close / previous.close - 1) * 100 : null;
  const chart = useMemo<EChartsOption>(
    () => ({
      backgroundColor: "transparent",
      animation: false,
      tooltip: {
        trigger: "axis",
        confine: true,
        axisPointer: { type: "cross" },
        formatter: (params: any) => {
          const item = Array.isArray(params) ? params[0] : params;
          const r = rows[item?.dataIndex];
          if (!r) return "";
          return `${r.time}<br/>开 ¥${money(r.open)}　收 ¥${money(r.close)}<br/>高 ¥${money(r.high)}　低 ¥${money(r.low)}<br/>成交量 ${r.volume.toLocaleString("zh-CN")} 股`;
        },
      },
      axisPointer: { link: [{ xAxisIndex: "all" as const }] },
      grid: [
        { left: 76, right: 18, top: 30, height: "58%" },
        { left: 76, right: 18, top: "78%", height: "19%" },
      ],
      xAxis: [
        {
          ...axis,
          type: "category" as const,
          data: rows.map((r) => r.time),
          boundaryGap: true,
          axisLabel: { color: "#78837c", fontSize: 10, hideOverlap: true },
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
        {
          ...axis,
          scale: true,
          type: "value",
          min: prices.min,
          max: prices.max,
          name: "价格 / 元",
          splitNumber: 5,
          axisLabel: {
            color: "#78837c",
            fontSize: 10,
            formatter: (v: number) => v.toFixed(2),
          },
        },
        {
          ...axis,
          type: "value",
          gridIndex: 1,
          splitNumber: 2,
          name: "成交量",
          axisLabel: {
            color: "#78837c",
            fontSize: 10,
            formatter: (v: number) =>
              v >= 1e8
                ? (v / 1e8).toFixed(1) + "亿"
                : v >= 1e4
                  ? (v / 1e4).toFixed(0) + "万"
                  : String(v),
          },
        },
      ],
      dataZoom: [
        {
          type: "inside" as const,
          id: "market-time-inside",
          xAxisIndex: [0, 1],
          start: range.start,
          end: range.end,
          zoomOnMouseWheel: true,
          moveOnMouseMove: true,
          moveOnMouseWheel: "shift",
          throttle: 50,
          preventDefaultMouseMove: true,
        },
      ],
      series: [
        mode === "k"
          ? {
              name: "行情",
              id: "market-price-series",
              type: "candlestick" as const,
              data: rows.map((r) => [r.open, r.close, r.low, r.high]),
              itemStyle: {
                color: RISE,
                color0: FALL,
                borderColor: RISE,
                borderColor0: FALL,
              },
            }
          : {
              name: "收盘价",
              id: "market-price-series",
              type: "line" as const,
              data: rows.map((r) => r.close),
              symbol: "none",
              lineStyle: { color: "#28765e", width: 2 },
            },
        {
          name: "成交量（股）",
          id: "market-volume-series",
          type: "bar" as const,
          xAxisIndex: 1,
          yAxisIndex: 1,
          data: rows.map((r, i) => ({
            value: r.volume,
            itemStyle: { color: volumeColor(r, rows[i - 1]) },
          })),
        },
      ],
    }),
    [data, mode, range.start, range.end, prices.min, prices.max],
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
              onClick={() => {
                setInterval(value);
                setWindow(null);
                resetPrice();
              }}
            >
              {label}
            </button>
          ))}
          <span>K线红阳 · 绿阴</span>
        </div>
        {rows.length > 0 && (
          <>
            <div className="market-toolbar">
              <label>
                开始日期
                <input
                  type="date"
                  value={dateStart}
                  min={rows[0].time.slice(0, 10)}
                  max={rows.at(-1)!.time.slice(0, 10)}
                  onChange={(e) => setDateStart(e.target.value)}
                  onInput={(e) => setDateStart(e.currentTarget.value)}
                />
              </label>
              <label>
                结束日期
                <input
                  type="date"
                  value={dateEnd}
                  min={rows[0].time.slice(0, 10)}
                  max={rows.at(-1)!.time.slice(0, 10)}
                  onChange={(e) => setDateEnd(e.target.value)}
                  onInput={(e) => setDateEnd(e.currentTarget.value)}
                />
              </label>
              <button
                className="secondary"
                onClick={() => {
                  const next = rangeForDates(rows, dateStart, dateEnd);
                  if (next) changeWindow(next);
                  else
                    setControlError(
                      "请选择包含行情记录的有效日期范围，开始日期不能晚于结束日期。",
                    );
                }}
              >
                应用时间范围
              </button>
            </div>
            <div className="row market-shortcuts">
              {[30, 90, 365].map((days) => (
                <button
                  className="secondary"
                  key={days}
                  onClick={() => {
                    const date = new Date(
                      rows.at(-1)!.time.slice(0, 10) + "T00:00:00Z",
                    );
                    date.setUTCDate(date.getUTCDate() - days + 1);
                    const next = rangeForDates(
                      rows,
                      date.toISOString().slice(0, 10),
                      rows.at(-1)!.time.slice(0, 10),
                    );
                    if (next) changeWindow(next);
                  }}
                >
                  近{days}天
                </button>
              ))}
              <button
                className="secondary"
                onClick={() => changeWindow({ start: 0, end: 100 })}
              >
                全部历史
              </button>
              <button
                className="secondary"
                aria-label="放大K线时间范围"
                title="放大"
                onClick={() =>
                  changeWindow(zoomWindow(range, 0.7, rows.length))
                }
              >
                <ZoomIn size={16} />
              </button>
              <button
                className="secondary"
                aria-label="缩小K线时间范围"
                title="缩小"
                onClick={() =>
                  changeWindow(zoomWindow(range, 1.4, rows.length))
                }
              >
                <ZoomOut size={16} />
              </button>
              <button
                className="secondary"
                onClick={() => {
                  changeWindow(recentWindow(rows.length));
                  resetPrice();
                }}
              >
                <RotateCcw size={14} />
                重置视图
              </button>
            </div>
            <details className="price-controls">
              <summary>价格坐标范围 · 自动随可见K线适配，也可手动调整</summary>
              <div className="market-toolbar">
                <label>
                  价格下限（元）
                  <input
                    type="number"
                    min={0}
                    step="0.01"
                    placeholder={money(prices.min)}
                    value={priceMin}
                    onChange={(e) => setPriceMin(e.target.value)}
                  />
                </label>
                <label>
                  价格上限（元）
                  <input
                    type="number"
                    min={0}
                    step="0.01"
                    placeholder={money(prices.max)}
                    value={priceMax}
                    onChange={(e) => setPriceMax(e.target.value)}
                  />
                </label>
                <button
                  className="secondary"
                  onClick={() => {
                    const min = Number(priceMin),
                      max = Number(priceMax);
                    if (
                      !priceMin ||
                      !priceMax ||
                      !Number.isFinite(min) ||
                      !Number.isFinite(max) ||
                      min < 0 ||
                      max <= min
                    ) {
                      setControlError(
                        "价格上限必须大于下限，且下限不能小于0。",
                      );
                      return;
                    }
                    setFixedPrices({ min, max });
                    setControlError("");
                  }}
                >
                  应用价格范围
                </button>
                <button className="secondary" onClick={resetPrice}>
                  恢复价格自动缩放
                </button>
              </div>
            </details>
            <p className="section-note" aria-live="polite">
              可见时间：{rows[firstIndex]?.time} — {rows[lastIndex]?.time} ·{" "}
              {lastIndex - firstIndex + 1}根K线；价格视窗：¥
              {money(prices.min)} — {money(prices.max)}
              {fixedPrices ? "（手动）" : "（自动基准）"}。
            </p>
            {controlError && <p className="error">{controlError}</p>}
          </>
        )}
        {error ? (
          <p className="error">{error}</p>
        ) : loading ? (
          <Empty title="正在读取行情" />
        ) : rows.length ? (
          <Chart option={chart} height={510} onZoom={onZoom} preserveSeries />
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
        {rows.length > 0 && (
          <>
            <TimeScrollbar
              dates={rows.map((r) => r.time)}
              range={range}
              onChange={changeWindow}
              label="K线时间滑条"
              inset={76}
            />
            <p className="section-note">
              拖动横向时间滑条查看前后行情，或按住图内左右拖动；滚轮与放大缩小按钮调整时间跨度。价格轴自动适配，也可填写上下限。成交量按收盘对比上一周期收盘：红涨、绿跌、灰平，首根对比开盘。
            </p>
          </>
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
