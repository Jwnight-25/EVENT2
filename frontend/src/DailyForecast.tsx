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
  paused: "已移出关注",
};
const fields = [
  ["open", "开盘"],
  ["high", "最高"],
  ["low", "最低"],
  ["close", "收盘"],
];
const points = (value: number) => (value > 0 ? `+${value}` : `${value}`);
const settlements: Record<string, string> = {
  scored: "已评分",
  no_forecast: "无法评分：缺少盘前预测",
  preparing: "准备预测中",
  settlement_pending: "等待后台评分",
  awaiting_actual: "等待同源收盘行情",
};
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
            {data?.schedule || "收盘后提前准备 · 09:15前保存 · 收盘后核对"}
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
      {basis === "raw" && data?.batches.length > 0 && (
        <>
          <h3>近期每日运行记录</h3>
          <p className="section-note">{data.runtime_note}</p>
          <div className="table-wrap">
            <table className="daily-coverage" aria-label="每日预测与评分完整性">
              <thead>
                <tr>
                  <th>交易日</th>
                  <th>真实行情</th>
                  <th>有效盘前预测</th>
                  <th>评分状态</th>
                  <th>运行说明</th>
                </tr>
              </thead>
              <tbody>
                {data.batches.slice(0, 8).map((b: any) => (
                  <tr
                    key={b.id}
                    className={b.id === batch?.id ? "selected-row" : ""}
                  >
                    <td>
                      <button
                        className="text-btn"
                        onClick={() => setChosen(b.id)}
                      >
                        {b.session_date}
                      </button>
                    </td>
                    <td>
                      {b.coverage?.actual_available ? "已入库" : "待收盘或补齐"}
                    </td>
                    <td>{b.coverage?.prediction_count ?? 0} 个模型</td>
                    <td>{settlements[b.coverage?.settlement] || "等待检查"}</td>
                    <td>
                      {b.config.gap_record
                        ? "未创建盘前批次，点击日期查看"
                        : b.error || labels[b.status] || b.status}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
      {basis !== "raw" ? (
        <p className="note-box">自动流程使用不复权日线，请切回“不复权”查看。</p>
      ) : !batch ? (
        <p className="note-box">
          系统将在固定来源上一交易日收盘数据齐备后，自动准备下一交易日预测，无需点击生成。本机需保持唤醒，错过截止的日期不会补造盘前预测。
        </p>
      ) : (
        <>
          <p className="note-box">
            <strong>
              {batch.session_date} · {labels[batch.status] || batch.status}
            </strong>
            　
            {batch.config.gap_record
              ? "漏跑记录（未生成预测）"
              : `数据截至 ${batch.config.data_cutoff || "等待补齐"}`}
            　{batch.error || ""}
          </p>
          {batch.coverage?.settlement === "no_forecast" && (
            <p className="note-box">
              本日缺少有效盘前预测，无法比较模型误差，不加分也不扣分。这条记录仅用于说明缺失，未生成历史预测或奖惩。
            </p>
          )}
          {batch.coverage?.actual && (
            <div className="table-wrap">
              <table aria-label="所选交易日真实行情">
                <thead>
                  <tr>
                    <th>同源真实行情 / 元</th>
                    {fields.map(([f, label]) => (
                      <th key={f}>{label}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    <td>{batch.session_date}</td>
                    {fields.map(([f]) => (
                      <td key={f}>{money(batch.coverage.actual[f])}</td>
                    ))}
                  </tr>
                </tbody>
              </table>
            </div>
          )}
          {batch.tasks.length > 0 && (
            <>
              <h3>模型积分榜</h3>
              <p className="section-note">
                开高低收统一四舍五入到分，按四项平均绝对误差排名。最接近+2，四项全对+5（不叠加），最差−2，其余0。并列同分；全部误差相同且非全对时均0分。包括naive比较基准。
              </p>
              <div className="table-wrap">
                <table aria-label="模型奖罚积分">
                  <thead>
                    <tr>
                      <th>模型</th>
                      <th>本日积分</th>
                      <th>排名MAE / 元</th>
                      <th>累计积分</th>
                      <th>已评分交易日</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(batch.tasks || []).map((t: any) => {
                      const total = data.leaderboard?.find(
                        (v: any) => v.family === t.family,
                      );
                      return (
                        <tr key={t.id}>
                          <td>
                            <strong>{t.family}</strong>
                          </td>
                          <td>
                            {t.score ? (
                              <strong>{points(t.score.points)}</strong>
                            ) : t.status === "missed" ||
                              t.status === "failed" ? (
                              "不参与"
                            ) : (
                              "待评分"
                            )}
                          </td>
                          <td>
                            {t.score ? t.score.ranking_mae.toFixed(4) : "—"}
                          </td>
                          <td>{total ? points(total.points) : "—"}</td>
                          <td>{total?.sessions ?? 0}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              <p className="section-note">
                累计截至 {data.points_through || "尚无已评分日期"}
                ，按同股模型类型跨版本汇总，行情修订只计最新一轮，不重复加分。
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
                      <th>原值OHLC MAE</th>
                      <th>本日积分</th>
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
                            <td key={f}>
                              {model ? money(model.candle[f]) : "—"}
                            </td>
                          ))}
                          <td>
                            {model
                              ? `${money(model.points[0].lower)}—${money(model.points[0].upper)}`
                              : "—"}
                          </td>
                          <td>
                            {model ? pct(model.validation_price_match) : "—"}
                          </td>
                          <td>
                            {t.outcome
                              ? money(t.outcome.actual.close)
                              : "待收盘核对"}
                          </td>
                          <td>{t.outcome ? t.outcome.mae.toFixed(4) : "—"}</td>
                          <td>{t.score ? points(t.score.points) : "—"}</td>
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
                                    {t.outcome
                                      ? money(t.outcome.actual[f])
                                      : "—"}
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
                      Bias {money(t.outcome.bias)} · RMSE{" "}
                      {money(t.outcome.rmse)} · MAPE {pct(t.outcome.mape)} ·
                      本日四目标覆盖 {pct(t.outcome.coverage)} · 方向
                      {t.outcome.direction_correct ? "一致" : "不一致"} ·
                      原始MAE {money(t.outcome.raw_mae)}
                    </p>
                  )}
                  {t.score && (
                    <p>
                      本日{points(t.score.points)}分 · {t.score.reason} · 排名
                      {t.score.rank} · 分精度MAE{" "}
                      {t.score.ranking_mae.toFixed(4)}元
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
                  {batch.config.unavailable.map((m: any) => m.name).join("、")}
                  。
                </p>
              )}
            </>
          )}
        </>
      )}
      <p className="section-note">
        每个可用模型类型保留当日新版本；沿用收盘价模型是比较基准。奖罚按真实四价误差排名，积分不代表交易收益。至少5个真实盘前样本才启用有限偏差校正；至少5个积分样本才按平均积分调整搜索预算。单日覆盖率仅有4个目标，不能表示长期可信度。
      </p>
    </section>
  );
}
