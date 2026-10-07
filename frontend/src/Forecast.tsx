import { EvidenceLabels } from "./EvidenceLabels";
import { useMemo, useState } from "react";
import { Telescope, ArrowUpRight } from "lucide-react";
import type { EChartsOption, SeriesOption } from "echarts";
import {
  api,
  post,
  horizons,
  money,
  pct,
  type Stock,
  type Model,
  type Prediction,
} from "./api";
import {
  Chart,
  Empty,
  JobPanel,
  axis,
  useLoad,
  type ChartZoom,
} from "./components";
import { TimeScrollbar } from "./TimeScrollbar";
import { forecastPlot } from "./forecastPlot";
import { recentWindow, visibleIndices, type WindowRange } from "./chartUtils";
import { summarizePath, type ForecastPoint } from "./forecastSummary";

export function Forecast({
  stock,
  basis,
  version,
  refresh,
}: {
  stock?: Stock;
  basis: string;
  version: number;
  refresh: () => void;
}) {
  const [horizon, setHorizon] = useState("next_day");
  const [chosen, setChosen] = useState<string[] | null>(null);
  const [reference, setReference] = useState("");
  const [experimental, setExperimental] = useState(false);
  const [resultModelId, setResultModelId] = useState("");
  const [aiJob, setAIJob] = useState("");
  const [observationStart, setObservationStart] = useState("");
  const storageKey = "prediction-job-" + stock?.id + "-" + basis;
  const [job, setJob] = useState(localStorage.getItem(storageKey) || "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [predictionId, setPredictionId] = useState("");
  const [plotWindow, setPlotWindow] = useState<{
    id: string;
    range: WindowRange;
  } | null>(null);
  const {
    data: models,
    error: modelError,
    loading: modelsLoading,
  } = useLoad(
    () =>
      stock
        ? api<Model[]>(
            "/models?" +
              new URLSearchParams({
                stock_id: stock.id,
                price_basis: basis,
                horizon,
                selected_only: String(!experimental),
              }),
          )
        : Promise.resolve([]),
    [stock?.id, basis, horizon, experimental, version],
  );
  const { data: history } = useLoad(
    () =>
      stock
        ? api<Prediction[]>("/predictions?stock_id=" + stock.id)
        : Promise.resolve([]),
    [stock?.id, version],
  );
  const { data: integrations } = useLoad(
    () => api<Record<string, string>>("/integrations"),
    [],
  );
  const latest =
    predictionId ||
    history?.find(
      (p) =>
        p.horizon === horizon &&
        p.config.price_basis === basis &&
        p.result.models?.length,
    )?.id;
  const { data: prediction, error: historyError } = useLoad(
    () =>
      latest
        ? api<Prediction>("/predictions/" + latest)
        : Promise.resolve(undefined),
    [latest, version],
  );
  const selected = chosen?.filter((id) => models?.some((m) => m.id === id));
  const defaults =
    models
      ?.filter((m) => m.run_id === models[0]?.run_id && m.family !== "naive")
      .sort((a, b) =>
        a.metrics.evaluation_mode === "cross_validation" &&
        b.metrics.evaluation_mode === "cross_validation"
          ? b.metrics.test.price_match - a.metrics.test.price_match
          : a.metrics.validation.mae - b.metrics.validation.mae,
      )
      .slice(0, 3)
      .map((m) => m.id) || [];
  const activeIds = selected ?? defaults;
  const result = prediction?.result;
  const plot = useMemo(() => forecastPlot(result), [result]);
  const plotRange =
    plotWindow && plotWindow.id === prediction?.id
      ? plotWindow.range
      : recentWindow(plot.dates.length, 30 + plot.futureCount);
  function changePlotWindow(range: WindowRange) {
    setPlotWindow({ id: prediction?.id || "", range });
  }
  function onPlotZoom(ranges: ChartZoom[]) {
    const zoom = ranges.find((r) => r.id === "forecast-time-inside");
    if (zoom) changePlotWindow({ start: zoom.start, end: zoom.end });
  }
  const [plotFirst, plotLast] = visibleIndices(plot.dates.length, plotRange);
  const chart = useMemo<EChartsOption>(() => {
    const series: SeriesOption[] = [...plot.series];
    const futureDates = new Set(plot.dates.slice(result?.history?.length || 0));
    const actual = (prediction?.actual_bars || []).filter((r) =>
      futureDates.has(r.time),
    );
    if (actual.length) {
      const byDate = Object.fromEntries(actual.map((r) => [r.time, r.close]));
      series.push({
        id: "forecast-actual",
        name: "后续实际",
        type: "line",
        data: plot.dates.map((d) => byDate[d] ?? null),
        symbol: "circle",
        symbolSize: 5,
        lineStyle: { color: "#c46b58", width: 2 },
        itemStyle: { color: "#c46b58" },
      });
    }
    return {
      animation: false,
      tooltip: {
        trigger: "axis",
        confine: true,
        formatter: (params: any) => {
          const date = (Array.isArray(params) ? params[0] : params)?.axisValue;
          const historical = result?.history?.find((r: any) => r.time === date);
          const lines = [date];
          if (historical) lines.push(`历史收盘：¥${money(historical.close)}`);
          for (const m of result?.models || []) {
            const point = m.points.find((p: any) => p.date === date);
            if (point)
              lines.push(
                `${m.family}：¥${money(point.estimate)}<br/>区间：¥${money(point.lower)} — ¥${money(point.upper)}`,
              );
          }
          const observed = actual.find((r) => r.time === date);
          if (observed) lines.push(`后续实际：¥${money(observed.close)}`);
          return lines.join("<br/>");
        },
      },
      legend: {
        bottom: 0,
        data: [
          "历史收盘",
          ...(result?.models || []).map((m: any) => m.family),
          ...(actual.length ? ["后续实际"] : []),
        ],
        textStyle: { fontSize: 11, color: "#7c8a73" },
      },
      grid: { left: 55, right: 55, top: 35, bottom: 55 },
      xAxis: {
        ...axis,
        type: "category",
        data: plot.dates,
        boundaryGap: true,
        axisLabel: { ...axis.axisLabel, hideOverlap: true },
      },
      yAxis: { ...axis, type: "value", scale: true },
      dataZoom: [
        {
          id: "forecast-time-inside",
          type: "inside",
          start: plotRange.start,
          end: plotRange.end,
          zoomOnMouseWheel: true,
          moveOnMouseMove: true,
          moveOnMouseWheel: "shift",
          throttle: 50,
        },
      ],
      series,
    };
  }, [plot, prediction, plotRange.start, plotRange.end]);
  async function start() {
    if (!stock) return;
    setBusy(true);
    setError("");
    try {
      const response = await post<{ job_id: string; prediction_id: string }>(
        "/predictions",
        {
          stock_id: stock.id,
          price_basis: basis,
          horizon,
          model_ids: activeIds,
          with_ai: false,
          allow_unvalidated: experimental,
          reference_price: reference ? Number(reference) : null,
          observation_start: observationStart || null,
        },
      );
      setJob(response.job_id);
      setPredictionId(response.prediction_id);
      setResultModelId("");
      localStorage.setItem(storageKey, response.job_id);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const first =
    result?.models?.find((m: any) => m.model_id === resultModelId) ||
    result?.models?.[0];
  const endpoint = first?.points?.at(-1);
  const baseClose = result?.history?.at(-1)?.close || result?.reference_price;
  const pathSummary = summarizePath(first?.points || [], baseClose || 0);
  return (
    <>
      <div className="page-heading">
        <div>
          <span className="eyebrow">FORECAST & PERSPECTIVE</span>
          <h1>预测分析</h1>
          <p>先选择模型与周期，生成数值预测，再按需联网解释。</p>
        </div>
        <span className="pill">收盘后 · 手动分析</span>
      </div>
      <section className="card">
        <h2>1 · 选择模型与预测周期</h2>
        <div className="prediction-settings">
          <label>
            模型范围
            <select
              value={experimental ? "research" : "qualified"}
              onChange={(e) => {
                setExperimental(e.target.value === "research");
                setChosen(null);
              }}
            >
              <option value="qualified">已通过验证的模型</option>
              <option value="research">全部模型 · 研究试算</option>
            </select>
          </label>
          <label>
            预测周期
            <select
              value={horizon}
              onChange={(e) => {
                setHorizon(e.target.value);
                setChosen(null);
                setPredictionId("");
              }}
            >
              {Object.entries(horizons).map(([v, label]) => (
                <option value={v} key={v}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <label>
            参考价格（可选）
            <input
              type="number"
              min="0.01"
              step="0.01"
              value={reference}
              onChange={(e) => setReference(e.target.value)}
              placeholder="默认最新完整日线收盘价"
            />
          </label>
          <label>
            观察起始日（可选）
            <input
              type="date"
              value={observationStart}
              onChange={(e) => setObservationStart(e.target.value)}
            />
          </label>
          <button
            className="primary"
            disabled={
              !stock ||
              busy ||
              !activeIds.length ||
              (reference !== "" && !(Number(reference) > 0))
            }
            onClick={start}
          >
            <Telescope size={16} />
            {busy ? "提交中…" : "2 · 生成模型预测"}
          </button>
        </div>
        <div className="check-group">
          {models?.map((m) => (
            <label key={m.id}>
              <input
                type="checkbox"
                checked={activeIds.includes(m.id)}
                disabled={!activeIds.includes(m.id) && activeIds.length >= 3}
                onChange={(e) => {
                  const next = e.target.checked
                    ? [...activeIds, m.id]
                    : activeIds.filter((id) => id !== m.id);
                  setChosen(next);
                }}
              />
              {m.family} · {m.id.slice(0, 6)} ·{" "}
              {m.selected
                ? m.metrics.evaluation_mode === "cross_validation"
                  ? "交叉验证通过"
                  : "已入选"
                : m.metrics.evaluation_mode === "cross_validation" &&
                    m.metrics.passes_thresholds
                  ? "验证通过 · 非当前入选"
                  : "未验证 / 研究"}{" "}
              · MAE {money(m.metrics.test?.mae)}
            </label>
          ))}
        </div>
        <p className="section-note">
          默认选择最近实验最多三个非基准模型，交叉验证模式按价格匹配度优先，也可手动调整。每次预测都会保存模型版本、输入数据和数值结果。下一交易日预测OHLC；20
          / 60交易日预测每日收盘路径和区间。
        </p>
        {experimental && (
          <p className="note-box">
            研究试算允许选择未入选模型，结果明确标注为未经验证；更长持有期及可执行买卖点尚无策略验证。
          </p>
        )}
        {modelsLoading && <p className="section-note">正在读取模型…</p>}
        {modelError && <p className="error">{modelError}</p>}
        {!modelsLoading && !modelError && !models?.length && (
          <p className="note-box">
            {experimental
              ? "当前周期暂无已训练模型，请先运行对应周期的训练。"
              : "当前周期没有达标模型。可查看训练报告，或切换“全部模型 · 研究试算”来检查现有模型的预测结果。"}
          </p>
        )}
        {error && <p className="error">{error}</p>}
        {job && <JobPanel id={job} finished={refresh} />}
      </section>
      {historyError && <p className="error">{historyError}</p>}
      {result?.models?.length ? (
        <>
          {result.model_failures?.length > 0 && (
            <div className="notice">
              <strong>部分模型未完成本次最新拟合</strong>
              <p>
                {result.model_failures
                  .map((m: any) => `${m.family}：${m.error}`)
                  .join("；")}
                。仅展示成功模型的预测，不用旧产物替代失败的新拟合。
              </p>
            </div>
          )}
          <div className="forecast-grid">
            <section className="card">
              <div className="card-heading">
                <div>
                  <h2>价格走势与边际预测区间</h2>
                  {result.experimental && (
                    <span className="pill gray">研究试算 · 未经验证</span>
                  )}
                  <p>
                    基于 {result.data_cutoff} 数据 · {horizons[result.horizon]}{" "}
                    · 不是实时行情
                  </p>
                </div>
              </div>
              <Chart
                key={prediction?.id}
                option={chart}
                height={390}
                onZoom={onPlotZoom}
                preserveSeries
              />
              <TimeScrollbar
                dates={plot.dates}
                range={plotRange}
                onChange={changePlotWindow}
                label="预测时间滑条"
              />
              <div className="row forecast-chart-controls">
                <button
                  className="text-btn"
                  onClick={() =>
                    changePlotWindow(
                      recentWindow(plot.dates.length, 30 + plot.futureCount),
                    )
                  }
                >
                  定位预测区段
                </button>
                <button
                  className="text-btn"
                  onClick={() => changePlotWindow({ start: 0, end: 100 })}
                >
                  全部时间
                </button>
                <span className="muted">
                  可见 {plotLast - plotFirst + 1} 个时间点 · 预测{" "}
                  {plot.futureCount} 个交易日
                </span>
              </div>
              {plot.futureCount === 1 && (
                <p className="section-note">
                  下一交易日只有一个预测点：彩色圆点为收盘估计，竖线为价格区间，虚线从最新实际收盘连接至该预测点。
                </p>
              )}
              <p className="section-note">
                每个日期的区间分别校准，不代表全路径保证覆盖。20/60交易日为实验跨度。
                {result.warnings?.join("；")}。
              </p>
            </section>
            <aside className="forecast-summary">
              <h3>查看模型 · {first.family}</h3>
              <select
                aria-label="结果模型"
                value={first.model_id}
                onChange={(e) => setResultModelId(e.target.value)}
              >
                {result.models.map((m: any) => (
                  <option key={m.model_id} value={m.model_id}>
                    {m.family} · {m.model_id.slice(0, 6)}
                  </option>
                ))}
              </select>
              <EvidenceLabels evidence={first.reliability} />
              <small className="muted">终点收盘价估计 / 区间</small>
              <strong>¥ {money(endpoint?.estimate)}</strong>
              <p>
                {money(endpoint?.lower)} — {money(endpoint?.upper)}
              </p>
              <ul>
                {first.evaluation_mode === "cross_validation" && (
                  <li>
                    15%交叉验证匹配度：{pct(first.validation_price_match)}
                  </li>
                )}
                <li>目标覆盖率：{pct(first.nominal_coverage)}</li>
                <li>原版本历史覆盖：{pct(first.historical_coverage)}</li>
                {first.deployment && (
                  <>
                    <li>
                      近期滚动复核覆盖：
                      {pct(first.deployment.recent_audit.coverage)}
                    </li>
                    <li>
                      近期复核MAE：{money(first.deployment.recent_audit.mae)} 元
                    </li>
                  </>
                )}
                <li>
                  参数拟合截止：{first.fitted_through || "历史结果未记录"}
                </li>
                <li>参考价格：¥ {money(result.reference_price)}</li>
                <li>到区间下沿：{pct(first.target_upside?.lower)}</li>
                <li>到点估计：{pct(first.target_upside?.median)}</li>
                <li>到区间上沿：{pct(first.target_upside?.upper)}</li>
              </ul>
              <p className="section-note">
                {first.evaluation_mode === "cross_validation" &&
                  "该模型通过已有历史数据内部交叉验证；匹配度不是方向准确率或盈利概率。"}
                {first.deployment?.note}。
                10%仅为观察阈值。区间上沿不是未来最高价，也不代表继续持有条件。
              </p>
            </aside>
          </div>
          {first.deployment && (
            <section className="card">
              <h2>本次预测的最新拟合与近期复核</h2>
              <p>
                最新参数拟合截止：{first.fitted_through}；原评估版本拟合截止：
                {first.evaluation_fitted_through}。
              </p>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>过程</th>
                      <th>日期区段</th>
                      <th>样本 / 指标</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr>
                      <td>滚动残差校准</td>
                      <td>
                        {first.deployment.calibration_start} —{" "}
                        {first.deployment.calibration_end}
                      </td>
                      <td>{first.deployment.calibration_samples}个预测起点</td>
                    </tr>
                    <tr>
                      <td>近期复核</td>
                      <td>
                        {first.deployment.audit_start} —{" "}
                        {first.deployment.audit_end}
                      </td>
                      <td>
                        {first.deployment.recent_audit.sample_count}个起点；约
                        {first.deployment.recent_audit.nonoverlap_windows}
                        个不重叠窗口
                      </td>
                    </tr>
                    <tr>
                      <td>复核覆盖 / 平均宽度</td>
                      <td>{pct(first.deployment.recent_audit.coverage)}</td>
                      <td>
                        ¥{money(first.deployment.recent_audit.mean_width)}
                      </td>
                    </tr>
                    <tr>
                      <td>复核MAE / 区间评分</td>
                      <td>¥{money(first.deployment.recent_audit.mae)}</td>
                      <td>
                        {money(first.deployment.recent_audit.interval_score)}
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
              <p className="section-note">
                {first.deployment.note}
                。历史匹配度和基准优势属于原评估版本，最新拟合版本还没有未来真实结果，不将旧成绩视作其验收成绩。
              </p>
            </section>
          )}
          {pathSummary && (
            <section className="card">
              <div className="card-heading">
                <div>
                  <h2>价格预测文字与逐日数据 · {first.family}</h2>
                  <p>
                    模型版本 {first.model_id.slice(0, 8)} ·{" "}
                    {first.points.length}个交易日 · 金额单位：元
                  </p>
                </div>
                <select
                  aria-label="文字与表格模型"
                  value={first.model_id}
                  onChange={(e) => setResultModelId(e.target.value)}
                >
                  {result.models.map((m: any) => (
                    <option key={m.model_id} value={m.model_id}>
                      {m.family} · {m.model_id.slice(0, 6)}
                    </option>
                  ))}
                </select>
              </div>
              <p className="forecast-narrative">
                点估计走势为<strong>{pathSummary.trend}</strong>
                ：由最新实际收盘价 ¥{money(baseClose)}，变化至 {endpoint.date}{" "}
                的 ¥{money(endpoint.estimate)}（{pct(pathSummary.change)}
                ）。期末收盘边际预测区间为
                <strong>
                  {" "}
                  ¥{money(endpoint.lower)} — ¥{money(endpoint.upper)}
                </strong>
                。
              </p>
              {first.points.length > 1 && (
                <>
                  <p className="forecast-narrative">
                    预测路径中的收盘点估计最低为 ¥
                    {money(pathSummary.trough.estimate)}（
                    {pathSummary.trough.date}），最高为 ¥
                    {money(pathSummary.peak.estimate)}（{pathSummary.peak.date}
                    ）。各日区间下沿最小值为 ¥{money(pathSummary.envelopeLow)}
                    ，上沿最大值为 ¥{money(pathSummary.envelopeHigh)}。
                  </p>
                  <div className="table-wrap">
                    <table>
                      <thead>
                        <tr>
                          <th>走势阶段</th>
                          <th>日期范围</th>
                          <th>阶段末点估计</th>
                          <th>阶段变化</th>
                          <th>描述</th>
                        </tr>
                      </thead>
                      <tbody>
                        {pathSummary.stages.map((stage, i) => (
                          <tr key={stage.end}>
                            <td>第{i + 1}阶段</td>
                            <td>
                              {stage.start} — {stage.end}
                            </td>
                            <td>{money(stage.estimate)}</td>
                            <td>{pct(stage.change)}</td>
                            <td>{stage.trend}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </>
              )}
              <p className="section-note">
                走势文字由已保存的模型数字计算，按区段变化超过±1%区分偏上行、偏下行，其余为小幅波动；不依赖AI。收盘点估计的高低值不是期间实际最高/最低价，区间包络也不是全路径覆盖保证。
              </p>
              <div className="table-wrap forecast-data">
                <table>
                  <thead>
                    <tr>
                      <th>交易日</th>
                      <th>收盘点估计</th>
                      <th>区间下沿</th>
                      <th>区间上沿</th>
                      <th>区间宽度</th>
                      <th>较最新收盘</th>
                    </tr>
                  </thead>
                  <tbody>
                    {first.points.map((p: ForecastPoint) => (
                      <tr key={p.date}>
                        <td>{p.date}</td>
                        <td>{money(p.estimate)}</td>
                        <td>{money(p.lower)}</td>
                        <td>{money(p.upper)}</td>
                        <td>{money(p.upper - p.lower)}</td>
                        <td>{pct(p.estimate / baseClose - 1)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          )}
          {first.candle && (
            <section className="card">
              <h2>下一交易日预测K线</h2>
              <div className="two-cols">
                <Chart
                  height={240}
                  option={{
                    tooltip: { trigger: "axis" },
                    grid: { left: 55, right: 30, top: 30, bottom: 35 },
                    xAxis: {
                      ...axis,
                      type: "category",
                      data: [first.points[0].date],
                    },
                    yAxis: { ...axis, type: "value", scale: true },
                    series: [
                      {
                        type: "candlestick",
                        barMaxWidth: 45,
                        data: [
                          [
                            first.candle.open,
                            first.candle.close,
                            first.candle.low,
                            first.candle.high,
                          ],
                        ],
                        itemStyle: {
                          color: "#cf7565",
                          color0: "#619274",
                          borderColor: "#cf7565",
                          borderColor0: "#619274",
                        },
                      },
                    ],
                  }}
                />
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>字段</th>
                        <th>估计</th>
                        <th>边际区间</th>
                      </tr>
                    </thead>
                    <tbody>
                      {[
                        ["open", "开盘"],
                        ["high", "最高"],
                        ["low", "最低"],
                        ["close", "收盘"],
                      ].map(([key, label]) => (
                        <tr key={key}>
                          <td>{label}</td>
                          <td>{money(first.candle[key])}</td>
                          <td>
                            {money(first.ohlc_intervals[key].lower)} —{" "}
                            {money(first.ohlc_intervals[key].upper)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
              <p className="section-note">
                建模方式：{first.ohlc_method || "历史版本未记录"}
                。各字段区间不构成整根K线的联合覆盖保证；原入选指标仅为收盘价格匹配。
              </p>
            </section>
          )}
          <section className="card">
            <div className="card-heading">
              <h2>模型之间的分歧</h2>
              <ArrowUpRight size={18} />
            </div>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>模型</th>
                    <th>期末估计</th>
                    <th>期末区间</th>
                    <th>历史覆盖率</th>
                    <th>到点估计空间</th>
                  </tr>
                </thead>
                <tbody>
                  {result.models.map((m: any) => (
                    <tr key={m.model_id}>
                      <td>{m.family}</td>
                      <td>{money(m.points.at(-1).estimate)}</td>
                      <td>
                        {money(m.points.at(-1).lower)} —{" "}
                        {money(m.points.at(-1).upper)}
                      </td>
                      <td>{pct(m.historical_coverage)}</td>
                      <td>{pct(m.target_upside.median)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
          <div className="notice">
            <strong>持有辅助 · 参考买卖区间尚未验证</strong>
            <p>
              {result.strategy?.message}。{result.holding?.message}
              。未设置固定止损，不执行交易。
            </p>
          </div>
          <section className="card" style={{ marginTop: 20 }}>
            <div className="card-heading">
              <h2>3 · 按需联网AI解释</h2>
              <button
                className="secondary"
                disabled={integrations?.ai !== "configured" || busy}
                onClick={async () => {
                  setBusy(true);
                  setError("");
                  try {
                    const r = await post<{ job_id: string }>(
                      "/predictions/" + prediction!.id + "/ai-analysis",
                      {},
                    );
                    setAIJob(r.job_id);
                  } catch (e) {
                    setError((e as Error).message);
                  } finally {
                    setBusy(false);
                  }
                }}
              >
                {prediction?.ai_analyses?.length
                  ? "重新联网解释"
                  : "解释这次预测"}
              </button>
            </div>
            <p className="section-note">
              {integrations?.ai === "configured"
                ? "服务接口已配置；点击后才检索公告、财报与事件，并提供引用来源。"
                : "当前未连接联网AI服务。接入支持联网检索的服务后才能解释这次预测；数值预测可以独立完成。"}{" "}
              AI接收这次保存的模型结果并解释，不替代或改写预测价格。
            </p>
            {aiJob && <JobPanel id={aiJob} finished={refresh} />}
            {prediction?.ai_analyses?.length ? (
              prediction.ai_analyses.map((a) => (
                <div key={a.id}>
                  <p style={{ whiteSpace: "pre-wrap", fontSize: 13 }}>
                    {a.content.text || a.content.message}
                  </p>
                  <div className="sources">
                    {a.content.sources?.map((s: any) => (
                      <div key={s.url}>
                        <a href={s.url} target="_blank" rel="noreferrer">
                          {s.title}
                        </a>
                        <small className="muted">
                          发布时间：{s.published_at || "未知"} · 检索：
                          {s.retrieved_at}
                        </small>
                      </div>
                    ))}
                  </div>
                </div>
              ))
            ) : (
              <p className="muted">
                未进行联网分析。接口预留，模型数值结果独立保存。
              </p>
            )}
          </section>
        </>
      ) : (
        <section className="card">
          <Empty title="等待模型预测">
            选择模型及周期后生成数值结果，完成后可以单独请求联网解释。
          </Empty>
        </section>
      )}
      <section className="card">
        <h2>历史预测</h2>
        {history?.length ? (
          history
            .filter((p) => p.config.price_basis === basis)
            .map((p) => (
              <button
                className="history-item"
                key={p.id}
                onClick={() => {
                  setPredictionId(p.id);
                  setHorizon(p.horizon);
                  setChosen(null);
                  setResultModelId("");
                }}
              >
                <span>
                  {horizons[p.horizon]} ·{" "}
                  {p.created_at.slice(0, 16).replace("T", " ")}
                </span>
                <span>
                  {p.result.models?.length ? "查看结果" : "尚无完成结果"} →
                </span>
              </button>
            ))
        ) : (
          <p className="section-note">
            暂无历史记录。预测生成后会保留模型版本和数据截止时间。
          </p>
        )}
      </section>
    </>
  );
}
