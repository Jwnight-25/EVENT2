import { useEffect, useState } from "react";
import { api, money, pct, type Stock } from "./api";
import { Chart, useLoad, axis } from "./components";
import type { EChartsOption } from "echarts";

const labels: Record<string, string> = {
  waiting_data: "等待上一交易日行情",
  training: "训练中",
  predicting: "预测中",
  ready: "已保存",
  partial: "部分完成",
  failed: "未完成",
  missed: "错过盘前窗口",
};
const fields = [
  ["open", "开盘"],
  ["high", "最高"],
  ["low", "最低"],
  ["close", "收盘"],
];
export function DailyForecast({
  stock,
  basis,
  inspect,
}: {
  stock?: Stock;
  basis: string;
  inspect: (id: string) => void;
}) {
  const [version, setVersion] = useState(0);
  const [chosen, setChosen] = useState("");
  useEffect(() => {
    const timer = window.setInterval(() => setVersion((v) => v + 1), 15000);
    return () => window.clearInterval(timer);
  }, []);
  const { data: response, error } = useLoad(
    () =>
      stock
        ? api<any>(`/daily-research/${stock.id}`)
        : Promise.resolve(undefined),
    [stock?.id, version],
    true,
  );
  const data = response?.stock_id === stock?.id ? response : undefined;
  const batch =
    data?.batches.find((b: any) => b.id === chosen) || data?.batches[0];
  const available = (batch?.tasks || []).filter(
    (t: any) => t.prediction?.models?.length,
  );
  const chart: EChartsOption = {
    animation: false,
    tooltip: { trigger: "axis" },
    grid: { left: 55, right: 25, top: 25, bottom: 35 },
    xAxis: {
      ...axis,
      type: "category",
      data: available.map((t: any) => t.family),
    },
    yAxis: { ...axis, type: "value", scale: true },
    series: [
      {
        name: "预测OHLC",
        type: "candlestick",
        data: available.map((t: any) => {
          const c = t.prediction.models[0].candle;
          return [c.open, c.close, c.low, c.high];
        }),
        itemStyle: {
          color: "#cf695e",
          color0: "#39866d",
          borderColor: "#cf695e",
          borderColor0: "#39866d",
        },
      },
    ],
  };
  return (
    <section className="card daily-forecast">
      <div className="card-heading">
        <div>
          <span className="eyebrow">DAILY MODEL OBSERVATORY</span>
          <h2>每日盘前预测与收盘复盘</h2>
          <p>
            {data?.schedule || "07:00自动训练 · 09:15前保存 · 收盘后核对"}
            （北京时间）
          </p>
        </div>
        {data?.batches.length > 0 && (
          <label>
            预测交易日
            <select
              aria-label="盘前预测交易日"
              value={batch?.id || ""}
              onChange={(e) => setChosen(e.target.value)}
            >
              {data.batches.map((b: any) => (
                <option key={b.id} value={b.id}>
                  {b.session_date} · {labels[b.status] || b.status}
                </option>
              ))}
            </select>
          </label>
        )}
      </div>
      {error && <p className="error">{error}</p>}
      {basis !== "raw" ? (
        <p className="note-box">自动流程使用不复权日线，请切回“不复权”查看。</p>
      ) : !batch ? (
        <p className="note-box">
          首次盘前任务尚未到达执行窗口。系统会自动训练已安装的模型类型，无需点击生成。错过开盘的任务不会补造为盘前预测。
        </p>
      ) : (
        <>
          <p className="note-box">
            <strong>
              {batch.session_date} · {labels[batch.status] || batch.status}
            </strong>
            　数据截至 {batch.config.data_cutoff || "等待补齐"}　
            {batch.error || ""}
          </p>
          {available.length > 0 && <Chart option={chart} height={280} />}
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>模型 / 版本</th>
                  {fields.map(([f, label]) => (
                    <th key={f}>{label}预测 / 元</th>
                  ))}
                  <th>收盘区间</th>
                  <th>历史验证匹配</th>
                  <th>真实收盘</th>
                  <th>OHLC MAE</th>
                  <th>奖励</th>
                </tr>
              </thead>
              <tbody>
                {(batch.tasks || []).map((t: any) => {
                  const model = t.prediction?.models?.[0];
                  return (
                    <tr key={t.id}>
                      <td>
                        <strong>{t.family}</strong>
                        <small className="muted">
                          {" "}
                          · {t.model_id?.slice(0, 6) || labels[t.status]}
                        </small>
                      </td>
                      {fields.map(([f]) => (
                        <td key={f}>{model ? money(model.candle[f]) : "—"}</td>
                      ))}
                      <td>
                        {model
                          ? `${money(model.points[0].lower)}—${money(model.points[0].upper)}`
                          : "—"}
                      </td>
                      <td>{model ? pct(model.validation_price_match) : "—"}</td>
                      <td>
                        {t.outcome
                          ? money(t.outcome.actual.close)
                          : "待收盘核对"}
                      </td>
                      <td>{t.outcome ? money(t.outcome.mae) : "—"}</td>
                      <td>{t.outcome ? t.outcome.reward.toFixed(3) : "—"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          {(batch.tasks || []).map((t: any) => (
            <details key={t.id} className="daily-model-detail">
              <summary>
                {t.family} · {labels[t.status] || t.status} · 反馈样本{" "}
                {t.feedback.sample_count || 0} 个
              </summary>
              {t.error && <p className="error">{t.error}</p>}
              {t.prediction?.models?.[0] && (
                <>
                  <p className="section-note">
                    已于{" "}
                    {new Date(t.prediction.published_at).toLocaleString(
                      "zh-CN",
                      { timeZone: "Asia/Shanghai", hour12: false },
                    )}
                    （北京时间）保存。 自动研究候选；历史验证匹配{" "}
                    {pct(t.prediction.models[0].validation_price_match)}
                    ，真实盘前成绩单独累计。
                  </p>
                  <button
                    className="text-btn"
                    onClick={() => inspect(t.prediction_id)}
                  >
                    查看完整图表与AI解释
                  </button>
                  <div className="table-wrap">
                    <table>
                      <thead>
                        <tr>
                          <th>目标</th>
                          <th>原始预测</th>
                          <th>偏差校正后</th>
                          <th>预测区间</th>
                          <th>实际值</th>
                          <th>预测−实际</th>
                        </tr>
                      </thead>
                      <tbody>
                        {fields.map(([f, label]) => {
                          const m = t.prediction.models[0];
                          return (
                            <tr key={f}>
                              <td>{label}</td>
                              <td>{money(m.uncorrected_candle[f])}</td>
                              <td>{money(m.candle[f])}</td>
                              <td>
                                {money(m.ohlc_intervals[f].lower)}—
                                {money(m.ohlc_intervals[f].upper)}
                              </td>
                              <td>
                                {t.outcome ? money(t.outcome.actual[f]) : "—"}
                              </td>
                              <td>
                                {t.outcome
                                  ? money(t.outcome.signed_errors[f])
                                  : "—"}
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                </>
              )}
              {t.outcome && (
                <p>
                  Bias {money(t.outcome.bias)} · RMSE {money(t.outcome.rmse)} ·
                  MAPE {pct(t.outcome.mape)} · 本日四目标覆盖{" "}
                  {pct(t.outcome.coverage)} · 方向
                  {t.outcome.direction_correct ? "一致" : "不一致"} · 原始MAE{" "}
                  {money(t.outcome.raw_mae)}
                </p>
              )}
              <p className="section-note">
                {t.feedback.note}。本轮每类参数试验{" "}
                {t.feedback.search_trials || 4} 次。
              </p>
            </details>
          ))}
          {batch.config.unavailable?.length > 0 && (
            <p className="section-note">
              缺少依赖，未参加：
              {batch.config.unavailable.map((m: any) => m.name).join("、")}。
            </p>
          )}
        </>
      )}
      <p className="section-note">
        每个可用模型类型保留当日新版本；沿用收盘价模型是比较基准。奖励以相对基准的真实误差计算，范围−1到1。至少5个真实盘前样本才启用有限偏差校正；没有事后补算的盘前成绩。单日覆盖率仅有4个目标，不能表示长期可信度。
      </p>
    </section>
  );
}
