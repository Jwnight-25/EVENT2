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
import { Chart, Empty, JobPanel, axis, useLoad } from "./components";

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
  const [withAI, setWithAI] = useState(false);
  const [observationStart, setObservationStart] = useState("");
  const storageKey = "prediction-job-" + stock?.id + "-" + basis;
  const [job, setJob] = useState(localStorage.getItem(storageKey) || "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [predictionId, setPredictionId] = useState("");
  const { data: models } = useLoad(
    () =>
      stock
        ? api<Model[]>(
            "/models?" +
              new URLSearchParams({
                stock_id: stock.id,
                price_basis: basis,
                horizon,
                selected_only: "true",
              }),
          )
        : Promise.resolve([]),
    [stock?.id, basis, horizon, version],
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
    history?.find((p) => p.horizon === horizon && p.result.models?.length)?.id;
  const { data: prediction, error: historyError } = useLoad(
    () =>
      latest
        ? api<Prediction>("/predictions/" + latest)
        : Promise.resolve(undefined),
    [latest, version],
  );
  const selected = chosen?.filter((id) => models?.some((m) => m.id === id));
  const activeIds = selected ?? (models?.slice(0, 3).map((m) => m.id) || []);
  const result = prediction?.result;
  const colors = ["#477956", "#a38b59", "#7e83a1"];
  const chart = useMemo<EChartsOption>(() => {
    const historical = result?.history || [];
    const forecasts = result?.models || [];
    const dates = [
      ...historical.map((r: any) => r.time),
      ...(forecasts[0]?.points || []).map((p: any) => p.date),
    ];
    const series: SeriesOption[] = [
      {
        name: "历史收盘",
        type: "line",
        data: [
          ...historical.map((r: any) => r.close),
          ...Array(forecasts[0]?.points.length || 0).fill(null),
        ],
        symbol: "none",
        lineStyle: { color: "#304e3b", width: 2 },
      },
    ];
    forecasts.forEach((m: any, i: number) => {
      const prefix = Array(historical.length).fill(null);
      series.push({
        name: m.family,
        type: "line",
        data: [
          ...prefix.slice(1),
          historical.at(-1)?.close,
          ...m.points.map((p: any) => p.estimate),
        ],
        symbol: "none",
        lineStyle: { color: colors[i], type: "dashed", width: 2 },
      });
      series.push({
        name: m.family + "区间下沿",
        type: "line",
        stack: "band-" + i,
        data: [...prefix, ...m.points.map((p: any) => p.lower)],
        symbol: "none",
        lineStyle: { opacity: 0 },
        areaStyle: { opacity: 0 },
        emphasis: { disabled: true },
      });
      series.push({
        name: m.family + "区间宽度",
        type: "line",
        stack: "band-" + i,
        data: [...prefix, ...m.points.map((p: any) => p.upper - p.lower)],
        symbol: "none",
        lineStyle: { opacity: 0 },
        areaStyle: { color: colors[i], opacity: 0.15 },
        emphasis: { disabled: true },
      });
    });
    const actual = prediction?.actual_bars || [];
    if (actual.length > 1) {
      const byDate = Object.fromEntries(actual.map((r) => [r.time, r.close]));
      series.push({
        name: "后续实际",
        type: "line",
        data: dates.map((d) => byDate[d] ?? null),
        symbol: "none",
        lineStyle: { color: "#c46b58", width: 2 },
      });
    }
    return {
      tooltip: { trigger: "axis" },
      legend: {
        bottom: 0,
        data: ["历史收盘", ...forecasts.map((m: any) => m.family), "后续实际"],
        textStyle: { fontSize: 11, color: "#7c8a73" },
      },
      grid: { left: 55, right: 20, top: 25, bottom: 70 },
      xAxis: { ...axis, type: "category", data: dates },
      yAxis: { ...axis, type: "value", scale: true },
      dataZoom: [{ type: "inside", start: 20 }],
      series,
    };
  }, [prediction]);
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
          with_ai: withAI,
          reference_price: reference ? Number(reference) : null,
          observation_start: observationStart || null,
        },
      );
      setJob(response.job_id);
      setPredictionId(response.prediction_id);
      localStorage.setItem(storageKey, response.job_id);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const first = result?.models?.[0];
  const endpoint = first?.points?.at(-1);
  return (
    <>
      <div className="page-heading">
        <div>
          <span className="eyebrow">FORECAST & PERSPECTIVE</span>
          <h1>预测分析</h1>
          <p>查看价格区间、模型分歧与剩余空间，保留每一次判断。</p>
        </div>
        <span className="pill">收盘后 · 手动分析</span>
      </div>
      <section className="card">
        <div className="prediction-settings">
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
            {busy ? "提交中…" : "生成预测"}
          </button>
        </div>
        <div className="check-group">
          {models?.map((m) => (
            <label key={m.id}>
              <input
                type="checkbox"
                checked={activeIds.includes(m.id)}
                onChange={(e) => {
                  const next = e.target.checked
                    ? [...activeIds, m.id]
                    : activeIds.filter((id) => id !== m.id);
                  setChosen(next);
                }}
              />
              {m.family} · {m.id.slice(0, 6)}
            </label>
          ))}
          <label>
            <input
              type="checkbox"
              checked={withAI}
              disabled={integrations?.ai !== "configured"}
              onChange={(e) => setWithAI(e.target.checked)}
            />
            联网AI解释{integrations?.ai !== "configured" ? "（未配置）" : ""}
          </label>
        </div>
        {!models?.length && (
          <p className="note-box">
            当前周期没有达标模型。先训练并查看报告；系统不会强行提供预测或参考买卖区间。
          </p>
        )}
        {error && <p className="error">{error}</p>}
        {job && <JobPanel id={job} finished={refresh} />}
      </section>
      {historyError && <p className="error">{historyError}</p>}
      {result?.models?.length ? (
        <>
          <div className="forecast-grid">
            <section className="card">
              <div className="card-heading">
                <div>
                  <h2>价格走势与边际预测区间</h2>
                  <p>
                    基于 {result.data_cutoff} 数据 · {horizons[result.horizon]}{" "}
                    · 不是实时行情
                  </p>
                </div>
              </div>
              <Chart option={chart} height={390} />
              <p className="section-note">
                每个日期的区间分别校准，不代表全路径保证覆盖。20/60交易日为实验跨度。
                {result.warnings?.join("；")}。
              </p>
            </section>
            <aside className="forecast-summary">
              <h3>首个模型 · {first.family}</h3>
              <small className="muted">终点收盘价估计 / 区间</small>
              <strong>¥ {money(endpoint?.estimate)}</strong>
              <p>
                {money(endpoint?.lower)} — {money(endpoint?.upper)}
              </p>
              <ul>
                <li>目标覆盖率：{pct(first.nominal_coverage)}</li>
                <li>历史实际覆盖：{pct(first.historical_coverage)}</li>
                <li>参考价格：¥ {money(result.reference_price)}</li>
                <li>到区间下沿：{pct(first.target_upside?.lower)}</li>
                <li>到中位值：{pct(first.target_upside?.median)}</li>
                <li>到区间上沿：{pct(first.target_upside?.upper)}</li>
              </ul>
              <p className="section-note">
                10%仅为观察阈值。区间上沿不是未来最高价，也不代表继续持有条件。
              </p>
            </aside>
          </div>
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
                各字段区间不构成整根K线的联合覆盖保证。
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
                    <th>到中位值空间</th>
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
              <h2>联网信息与情景解释</h2>
              <button
                className="secondary"
                disabled={integrations?.ai !== "configured"}
                onClick={async () => {
                  try {
                    const r = await post<{ job_id: string }>(
                      "/predictions/" + prediction!.id + "/ai-analysis",
                      {},
                    );
                    setJob(r.job_id);
                  } catch (e) {
                    setError((e as Error).message);
                  }
                }}
              >
                重新分析
              </button>
            </div>
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
          <Empty title="等待一次经过验证的预测">
            选择通过训练验收的模型后生成分析；新结果不会覆盖历史预测。
          </Empty>
        </section>
      )}
      <section className="card">
        <h2>历史预测</h2>
        {history?.length ? (
          history.map((p) => (
            <button
              className="history-item"
              key={p.id}
              onClick={() => setPredictionId(p.id)}
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
