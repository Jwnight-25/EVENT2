import { useEffect, useState } from "react";
import { api, pct, type Stock } from "./api";
import { useLoad } from "./components";

const fields = [
  ["open", "开盘"],
  ["high", "最高"],
  ["low", "最低"],
  ["close", "收盘"],
] as const;
type Prices = Record<(typeof fields)[number][0], number>;
type Observation = {
  batch_id: string;
  task_id: string | null;
  session_date: string;
  family: string | null;
  model_id: string | null;
  prediction_id: string | null;
  actual_revision_id: string | null;
  predicted: Prices | null;
  actual: Prices | null;
  errors: Prices | null;
  relative_errors: Prices | null;
  bias: number | null;
  mae: number | null;
  rmse: number | null;
  mape: number | null;
  status: string;
  reason: string;
};
type ModelSummary = {
  family: string;
  sample_count: number;
  mean_errors: Prices;
  bias: number;
  mae: number;
  rmse: number;
  mape: number;
};
type Statistics = {
  stock_id: string;
  definition: string;
  families: string[];
  rows: Observation[];
  total: number;
  offset: number;
  limit: number;
  summary: {
    available_count: number;
    unavailable_count: number;
    models: ModelSummary[];
  };
};
const states: Record<string, string> = {
  available: "可比较",
  preparing: "准备中",
  no_forecast: "缺少有效预测",
  awaiting_actual: "等待真实行情",
};
function signed(value: number | null | undefined, digits = 4) {
  if (value == null || !Number.isFinite(value)) return "—";
  const magnitude = Math.abs(value).toFixed(digits);
  return `${Number(magnitude) === 0 ? "" : value > 0 ? "+" : "−"}${magnitude}`;
}
function Delta({ value }: { value: number | null | undefined }) {
  return (
    <span
      className={
        value == null || Math.abs(value) < 0.00005
          ? ""
          : value > 0
            ? "deviation-high"
            : "deviation-low"
      }
    >
      {signed(value)}
    </span>
  );
}

export function DailyDeviations({
  stock,
  basis,
}: {
  stock?: Stock;
  basis: string;
}) {
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [family, setFamily] = useState("");
  const [scope, setScope] = useState("available");
  const [offset, setOffset] = useState(0);
  const [version, setVersion] = useState(0);
  const [detailId, setDetailId] = useState("");
  useEffect(() => {
    const timer = window.setInterval(() => setVersion((v) => v + 1), 60000);
    return () => window.clearInterval(timer);
  }, []);
  const query = new URLSearchParams({
    only_available: String(scope === "available"),
    limit: "20",
    offset: String(offset),
  });
  if (start) query.set("start", start);
  if (end) query.set("end", end);
  if (family) query.set("family", family);
  const requestKey = `${stock?.id}:${basis}:${query}`;
  const invalidRange = Boolean(start && end && start > end);
  const {
    data: response,
    error,
    loading,
  } = useLoad(
    () =>
      stock && basis === "raw" && !invalidRange
        ? api<Statistics>(
            `/daily-research/${stock.id}/deviations?${query}`,
          ).then((payload) => ({ key: requestKey, payload }))
        : Promise.resolve(undefined),
    [stock?.id, basis, start, end, family, scope, offset, version],
    true,
  );
  const previous =
    response && response.payload.stock_id === stock?.id
      ? response.payload
      : undefined;
  const data = response?.key === requestKey ? previous : undefined;
  const detail = data?.rows.find(
    (row) => (row.task_id || row.batch_id) === detailId,
  );
  const resetPage = () => {
    setOffset(0);
    setDetailId("");
  };
  return (
    <section id="daily-deviations" className="card daily-deviations">
      <div className="card-heading">
        <div>
          <span className="eyebrow">DAILY DEVIATION STATISTICS</span>
          <h2>每日模型偏差统计</h2>
          <p>
            预测值−真实值；正数表示高估，负数表示低估。单位：元，显示4位小数。
          </p>
        </div>
        <button
          className="secondary"
          disabled={loading || !stock || basis !== "raw"}
          onClick={() => setVersion((v) => v + 1)}
        >
          刷新偏差统计
        </button>
      </div>
      {basis !== "raw" ? (
        <p className="note-box">
          每日模型使用不复权价格，请切回“不复权”查看偏差统计。
        </p>
      ) : (
        <>
          <div className="deviation-filters">
            <label>
              开始日期
              <input
                type="date"
                aria-label="偏差开始日期"
                value={start}
                onChange={(e) => {
                  setStart(e.target.value);
                  resetPage();
                }}
              />
            </label>
            <label>
              结束日期
              <input
                type="date"
                aria-label="偏差结束日期"
                value={end}
                onChange={(e) => {
                  setEnd(e.target.value);
                  resetPage();
                }}
              />
            </label>
            <label>
              模型类型
              <select
                aria-label="偏差模型类型"
                value={family}
                onChange={(e) => {
                  setFamily(e.target.value);
                  resetPage();
                }}
              >
                <option value="">全部模型</option>
                {(previous?.families || (family ? [family] : [])).map(
                  (name) => (
                    <option value={name} key={name}>
                      {name}
                    </option>
                  ),
                )}
              </select>
            </label>
            <label>
              记录范围
              <select
                aria-label="偏差记录范围"
                value={scope}
                onChange={(e) => {
                  setScope(e.target.value);
                  resetPage();
                }}
              >
                <option value="available">已可比较</option>
                <option value="all">全部运行记录</option>
              </select>
            </label>
            <button
              className="text-btn"
              onClick={() => {
                setStart("");
                setEnd("");
                setFamily("");
                setScope("available");
                resetPage();
              }}
            >
              重置筛选
            </button>
          </div>
          {invalidRange && <p className="error">开始日期不能晚于结束日期。</p>}
          {error && <p className="error">{error}</p>}
          {loading && (
            <p className="section-note" role="status">
              正在读取偏差统计…
            </p>
          )}
          {data && (
            <>
              <p className="section-note">
                当前日期和模型范围：{data.summary.available_count} 条可比较，
                {data.summary.unavailable_count}{" "}
                条暂不可比较。汇总涵盖全部筛选日期，不受下方分页影响；缺失记录可切换“全部运行记录”查看。
              </p>
              <h3>按模型汇总</h3>
              <div className="table-wrap">
                <table aria-label="模型偏差汇总">
                  <thead>
                    <tr>
                      <th>模型</th>
                      <th>有效交易日</th>
                      {fields.map(([f, label]) => (
                        <th key={f}>{label}平均偏差</th>
                      ))}
                      <th>四价平均偏差 Bias</th>
                      <th>平均绝对偏差 MAE</th>
                      <th>RMSE</th>
                      <th>MAPE</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.summary.models.map((m) => (
                      <tr key={m.family}>
                        <td>
                          <strong>{m.family}</strong>
                        </td>
                        <td>{m.sample_count}</td>
                        {fields.map(([f]) => (
                          <td key={f}>
                            <Delta value={m.mean_errors[f]} />
                          </td>
                        ))}
                        <td>
                          <Delta value={m.bias} />
                        </td>
                        <td>{m.mae.toFixed(4)}</td>
                        <td>{m.rmse.toFixed(4)}</td>
                        <td>{pct(m.mape)}</td>
                      </tr>
                    ))}
                    {!data.summary.models.length && (
                      <tr>
                        <td colSpan={10}>
                          当前范围尚无可比较的预测与真实行情。
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
              <p className="section-note">
                Bias可能因正负偏差相抵而接近0；MAE表示绝对偏离程度。每个交易日按开、高、低、收四个目标计算，RMSE使用全部有效目标的平方误差汇总，不取每日RMSE的简单平均。
              </p>
              <h3>逐日逐模型明细</h3>
              <div className="table-wrap">
                <table aria-label="每日每个模型的真实偏差">
                  <thead>
                    <tr>
                      <th>交易日</th>
                      <th>模型 / 版本</th>
                      {fields.map(([f, label]) => (
                        <th key={f}>{label}偏差</th>
                      ))}
                      <th>四价 Bias</th>
                      <th>MAE</th>
                      <th>收盘偏差率</th>
                      <th>状态 / 明细</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.rows.map((row) => (
                      <tr
                        key={row.task_id || row.batch_id}
                        className={
                          (row.task_id || row.batch_id) === detailId
                            ? "selected-row"
                            : ""
                        }
                      >
                        <td>{row.session_date}</td>
                        <td>
                          <strong>{row.family || "未创建模型任务"}</strong>
                          <small className="muted">
                            {" "}
                            {row.model_id?.slice(0, 6)}
                          </small>
                        </td>
                        {fields.map(([f]) => (
                          <td key={f}>
                            <Delta value={row.errors?.[f]} />
                          </td>
                        ))}
                        <td>
                          <Delta value={row.bias} />
                        </td>
                        <td>{row.mae?.toFixed(4) ?? "—"}</td>
                        <td>
                          {row.relative_errors
                            ? `${signed(row.relative_errors.close * 100, 2)}%`
                            : "—"}
                        </td>
                        <td>
                          <button
                            className="text-btn"
                            onClick={() =>
                              setDetailId(row.task_id || row.batch_id)
                            }
                          >
                            {states[row.status] || row.status} · 查看
                          </button>
                        </td>
                      </tr>
                    ))}
                    {!data.rows.length && (
                      <tr>
                        <td colSpan={10}>
                          当前筛选无记录，可扩大日期范围或选择“全部运行记录”。
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
              <div className="row deviation-pagination">
                <button
                  className="secondary"
                  disabled={offset === 0 || loading}
                  onClick={() => {
                    setOffset(Math.max(0, offset - 20));
                    setDetailId("");
                  }}
                >
                  上一页偏差
                </button>
                <span>
                  {data.total
                    ? `${offset + 1}—${Math.min(offset + data.limit, data.total)}`
                    : "0"}{" "}
                  / {data.total} 条
                </span>
                <button
                  className="secondary"
                  disabled={offset + data.limit >= data.total || loading}
                  onClick={() => {
                    setOffset(offset + 20);
                    setDetailId("");
                  }}
                >
                  下一页偏差
                </button>
              </div>
              {detail && (
                <div className="deviation-detail">
                  <h3>
                    {detail.session_date} · {detail.family || "未创建模型任务"}{" "}
                    · 预测与真实值
                  </h3>
                  <p>{detail.reason}</p>
                  <div className="table-wrap">
                    <table aria-label="偏差对应的预测与真实价格">
                      <thead>
                        <tr>
                          <th>目标</th>
                          <th>已发布预测 / 元</th>
                          <th>真实值 / 元</th>
                          <th>偏差 / 元</th>
                          <th>偏差率</th>
                        </tr>
                      </thead>
                      <tbody>
                        {fields.map(([f, label]) => (
                          <tr key={f}>
                            <td>{label}</td>
                            <td>
                              {detail.predicted
                                ? detail.predicted[f].toFixed(4)
                                : "—"}
                            </td>
                            <td>
                              {detail.actual
                                ? detail.actual[f].toFixed(4)
                                : "—"}
                            </td>
                            <td>
                              <Delta value={detail.errors?.[f]} />
                            </td>
                            <td>
                              {detail.relative_errors
                                ? `${signed(detail.relative_errors[f] * 100, 2)}%`
                                : "—"}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  <p className="section-note">
                    按已发布预测（含当时校正）与当前同源真实行情比较。行情修订会更新本统计，历史积分以收盘评分记录为准；查询不触发训练或奖惩。
                  </p>
                </div>
              )}
            </>
          )}
        </>
      )}
    </section>
  );
}
