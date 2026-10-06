import { useMemo, useState } from "react";
import { Play, FlaskConical } from "lucide-react";
import { api, post, horizons, money, pct, type Stock, type Model } from "./api";
import { Chart, Empty, JobPanel, axis, useLoad } from "./components";
import type { EChartsOption } from "echarts";

export function Training({
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
  const [diagnosticTarget, setDiagnosticTarget] = useState("");
  const [horizon, setHorizon] = useState("next_day");
  const [families, setFamilies] = useState(["arima", "ridge"]);
  const [trials, setTrials] = useState(8);
  const [budget, setBudget] = useState(600);
  const [modelId, setModelId] = useState("");
  const storageKey = "training-job-" + stock?.id + "-" + basis;
  const [job, setJob] = useState(localStorage.getItem(storageKey) || "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const { data: models, error: loadError } = useLoad(
    () =>
      stock
        ? api<Model[]>(
            "/models?" +
              new URLSearchParams({
                stock_id: stock.id,
                horizon,
                price_basis: basis,
              }),
          )
        : Promise.resolve([]),
    [stock?.id, basis, horizon, version],
  );
  const { data: runs } = useLoad(
    () =>
      stock
        ? api<any[]>("/training-runs?stock_id=" + stock.id)
        : Promise.resolve([]),
    [stock?.id, version],
  );
  const current = models?.find((m) => m.id === modelId) || models?.[0];
  const { data: allDiagnostics } = useLoad(
    () =>
      current
        ? api<any>("/models/" + current.id + "/diagnostics")
        : Promise.resolve(undefined),
    [current?.id],
  );
  const diagnosis =
    diagnosticTarget && allDiagnostics?.by_target?.[diagnosticTarget]
      ? allDiagnostics.by_target[diagnosticTarget]
      : allDiagnostics;
  const comparison = useMemo<EChartsOption>(
    () => ({
      tooltip: { trigger: "axis" },
      legend: { bottom: 0, textStyle: { color: "#7b8972", fontSize: 11 } },
      grid: { left: 55, right: 20, top: 20, bottom: 55 },
      xAxis: {
        ...axis,
        type: "category",
        data: models?.map((m) => m.family + " · " + m.id.slice(0, 4)),
      },
      yAxis: { ...axis, type: "value", name: "MAE" },
      series: [
        {
          name: "滚动验证",
          type: "bar",
          data: models?.map((m) => m.metrics.validation?.mae),
          itemStyle: { color: "#acc698", borderRadius: [3, 3, 0, 0] },
        },
        {
          name: "隔离测试",
          type: "bar",
          data: models?.map((m) => m.metrics.test?.mae),
          itemStyle: { color: "#426f53", borderRadius: [3, 3, 0, 0] },
        },
      ],
    }),
    [models],
  );
  const lineOption = (
    values: number[] | undefined,
    type = "line",
  ): EChartsOption => ({
    tooltip: { trigger: "axis" },
    grid: { left: 45, right: 15, top: 25, bottom: 35 },
    xAxis: {
      ...axis,
      type: "category",
      data: values?.map((_, i) =>
        type === "bar" ? i : diagnosis?.dates?.[i] || i,
      ),
    },
    yAxis: { ...axis, type: "value", scale: true },
    series: [
      {
        type: type as "line" | "bar",
        data: values || [],
        itemStyle: { color: "#7c9d6d" },
        symbol: "none",
      },
    ],
  });
  const histogram = useMemo<EChartsOption>(() => {
    const values: number[] = diagnosis?.residuals || [];
    const min = Math.min(...values),
      max = Math.max(...values);
    const width = (max - min) / 10 || 1;
    const bins = Array.from({ length: 10 }, () => 0);
    values.forEach((v) => bins[Math.min(9, Math.floor((v - min) / width))]++);
    return {
      tooltip: {},
      grid: { left: 40, right: 15, top: 25, bottom: 35 },
      xAxis: {
        ...axis,
        type: "category",
        data: bins.map((_, i) => money(min + width * i)),
      },
      yAxis: { ...axis, type: "value" },
      series: [{ type: "bar", data: bins, itemStyle: { color: "#a5bc90" } }],
    };
  }, [diagnosis]);
  async function start() {
    if (!stock) return;
    setBusy(true);
    setError("");
    try {
      const result = await post<{ job_id: string }>("/training-runs", {
        stock_id: stock.id,
        price_basis: basis,
        families,
        max_trials: trials,
        time_budget_seconds: budget,
        horizons: ["next_day", "one_month", "three_months"],
      });
      setJob(result.job_id);
      localStorage.setItem(storageKey, result.job_id);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <div className="page-heading">
        <div>
          <span className="eyebrow">MODEL LABORATORY</span>
          <h1>模型训练</h1>
          <p>滚动验证、区间校准与独立测试，筛选真正通过评估的模型。</p>
        </div>
        <span className="pill">每月训练 · 手动启动</span>
      </div>
      <section className="card">
        <div className="card-heading">
          <div>
            <h2>训练实验配置</h2>
            <p>
              {stock?.name || "请先添加股票"} · 使用完整日线快照 · M3资源预算
            </p>
          </div>
          <FlaskConical size={22} color="#81976b" />
        </div>
        <div className="config-grid">
          <label>
            每周期参数试验上限
            <input
              type="number"
              value={trials}
              min={1}
              max={30}
              onChange={(e) => setTrials(Number(e.target.value))}
            />
          </label>
          <label>
            总计算预算（秒）
            <input
              type="number"
              value={budget}
              min={10}
              max={3600}
              onChange={(e) => setBudget(Number(e.target.value))}
            />
          </label>
          <label>
            上次训练时间
            <input
              disabled
              value={runs?.[0]?.created_at?.slice(0, 10) || "尚未训练"}
            />
          </label>
        </div>
        <div className="check-group">
          {[
            ["arima", "ARMA / ARIMA"],
            ["sarima", "SARIMA"],
            ["ridge", "Ridge基础模型"],
            ["lightgbm", "LightGBM · 可选依赖"],
            ["garch", "ARIMA＋GARCH · 可选依赖"],
            ["nhits", "N-HiTS · 可选依赖"],
            ["patchtst", "PatchTST · 可选实验"],
          ].map(([value, label]) => (
            <label key={value}>
              <input
                type="checkbox"
                checked={families.includes(value)}
                onChange={(e) =>
                  setFamilies(
                    e.target.checked
                      ? [...families, value]
                      : families.filter((f) => f !== value),
                  )
                }
              />
              {label}
            </label>
          ))}
        </div>
        <p className="section-note">
          简单基准始终参与比较。深度模型需单独安装依赖；缺少依赖会记录失败原因。验证、校准和测试相互隔离，同一数据版本不反复调参。
        </p>
        <div className="row" style={{ marginTop: 18 }}>
          <button
            className="primary"
            disabled={
              !stock ||
              busy ||
              !families.length ||
              !Number.isFinite(trials) ||
              !Number.isFinite(budget)
            }
            onClick={start}
          >
            <Play size={15} />
            {busy ? "提交中…" : "开始训练三个周期"}
          </button>
          <span className="muted">数据不足的周期会跳过并说明原因</span>
        </div>
        {error && <p className="error">{error}</p>}
        {job && <JobPanel id={job} finished={refresh} />}
      </section>
      <section className="card">
        <div className="card-heading">
          <h2>模型效果对比</h2>
          <select
            value={horizon}
            onChange={(e) => {
              setHorizon(e.target.value);
              setModelId("");
            }}
          >
            {Object.entries(horizons).map(([v, label]) => (
              <option key={v} value={v}>
                {label}
              </option>
            ))}
          </select>
        </div>
        {loadError && <p className="error">{loadError}</p>}
        {models?.length ? (
          <>
            <Chart option={comparison} height={250} />
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>模型 / 版本</th>
                    <th>验证MAE</th>
                    <th>测试MAE</th>
                    <th>改善</th>
                    <th>方向表现</th>
                    <th>实际覆盖</th>
                    <th>平均区间宽度</th>
                    <th>入选状态</th>
                  </tr>
                </thead>
                <tbody>
                  {models.map((m) => (
                    <tr
                      key={m.id}
                      className={m.id === current?.id ? "selected-row" : ""}
                      onClick={() => setModelId(m.id)}
                      style={{ cursor: "pointer" }}
                    >
                      <td>
                        {m.family}
                        <small className="muted">　{m.id.slice(0, 6)}</small>
                      </td>
                      <td>{money(m.metrics.validation?.mae)}</td>
                      <td>{money(m.metrics.test?.mae)}</td>
                      <td>{pct(m.metrics.improvement)}</td>
                      <td>{pct(m.metrics.test?.direction_accuracy)}</td>
                      <td>{pct(m.metrics.test?.coverage)}</td>
                      <td>{money(m.metrics.test?.mean_width)}</td>
                      <td>
                        <span className={"pill" + (m.selected ? "" : " gray")}>
                          {m.selected ? "已入选" : "未入选"}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="section-note">
              指标为各周期终点收盘价。目标覆盖率与实际覆盖率分开报告；多步误差可能重叠。点击模型查看诊断。
            </p>
          </>
        ) : (
          <Empty title="尚无模型评估结果">
            上传历史日线后启动训练；没有达标模型也是有效研究结果。
          </Empty>
        )}
      </section>
      {current && diagnosis && (
        <section className="card">
          <div className="card-heading">
            <h2>诊断 · {current.family}</h2>
            <select
              aria-label="诊断目标"
              value={diagnosticTarget}
              onChange={(e) => setDiagnosticTarget(e.target.value)}
            >
              <option value="">周期终点收盘</option>
              {Object.keys(allDiagnostics?.by_target || {}).map((t) => (
                <option key={t} value={t}>
                  {(
                    {
                      open: "次日开盘",
                      high: "次日最高",
                      low: "次日最低",
                      close: "次日收盘",
                    } as Record<string, string>
                  )[t] || t.replace("step_", "第") + "交易日"}
                </option>
              ))}
            </select>
            <select
              value={current.id}
              onChange={(e) => setModelId(e.target.value)}
            >
              {models?.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.family} · {m.id.slice(0, 6)}
                </option>
              ))}
            </select>
          </div>
          <p className="note-box">
            {current.reason}。参数：{JSON.stringify(current.parameters)}
            。拟合截止：{current.metrics.fitted_through}；最终测试样本：
            {current.metrics.test.sample_count}；名义覆盖率：
            {pct(current.metrics.nominal_coverage)}。
          </p>
          <div className="chart-grid" style={{ marginTop: 18 }}>
            <div className="subcard">
              <h3>预测与实际 · 隔离测试</h3>
              <Chart
                height={230}
                option={{
                  ...lineOption(diagnosis.actual),
                  legend: { bottom: 0, data: ["实际", "预测"] },
                  series: [
                    {
                      name: "实际",
                      type: "line",
                      data: diagnosis.actual,
                      symbol: "none",
                      lineStyle: { color: "#385f43" },
                    },
                    {
                      name: "预测",
                      type: "line",
                      data: diagnosis.predicted,
                      symbol: "none",
                      lineStyle: { color: "#b0a076", type: "dashed" },
                    },
                  ],
                }}
              />
            </div>
            <div className="subcard">
              <h3>残差时序</h3>
              <Chart height={230} option={lineOption(diagnosis.residuals)} />
            </div>
            <div className="subcard">
              <h3>残差分布</h3>
              <Chart height={220} option={histogram} />
            </div>
            <div className="subcard">
              <h3>ACF / PACF</h3>
              <Chart
                height={220}
                option={{
                  ...lineOption(diagnosis.acf, "bar"),
                  legend: { bottom: 0 },
                  series: [
                    {
                      name: "ACF",
                      type: "bar",
                      data: diagnosis.acf,
                      itemStyle: { color: "#8caa76" },
                    },
                    {
                      name: "PACF",
                      type: "bar",
                      data: diagnosis.pacf,
                      itemStyle: { color: "#436b4f" },
                    },
                  ],
                }}
              />
            </div>
          </div>
          <p className="section-note">
            Ljung-Box p：{money(diagnosis.ljung_box_pvalue)} · ARCH p：
            {money(diagnosis.arch_pvalue)}。{diagnosis.notes}
          </p>
          <details>
            <summary>滚动验证窗口与频域诊断</summary>
            <pre>
              {JSON.stringify(
                {
                  windows: current.metrics.validation.windows,
                  mae_by_target: current.metrics.test.mae_by_target,
                  frequency_peaks: diagnosis.frequency_peaks,
                },
                null,
                2,
              )}
            </pre>
          </details>
        </section>
      )}
      {runs?.[0]?.report?.horizons && (
        <section className="card">
          <h2>最近实验报告</h2>
          <div className="trial-list">
            {Object.entries(runs[0].report.horizons).map(([h, r]) => (
              <p className="section-note" key={h}>
                {horizons[h]}：{(r as any).message} · {(r as any).status}
              </p>
            ))}
          </div>
          <p className="section-note">
            {runs[0].report.budget_exhausted ? "本次优化预算已用尽。" : ""}
            参数试验和失败原因可通过训练记录接口追溯。
          </p>
        </section>
      )}
    </>
  );
}
