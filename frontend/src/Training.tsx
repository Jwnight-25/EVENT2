import { useMemo, useState } from "react";
import { Play, FlaskConical } from "lucide-react";
import {
  api,
  post,
  horizons,
  money,
  score,
  pct,
  probability,
  type Stock,
  type Model,
  type ModelFamily,
} from "./api";
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
  const [trials, setTrials] = useState(6);
  const [budget, setBudget] = useState(1800);
  const [mode, setMode] = useState("research");
  const [profile, setProfile] = useState("standard");
  const [alphaMin, setAlphaMin] = useState(0.01);
  const [alphaMax, setAlphaMax] = useState(1000);
  const [orderLimit, setOrderLimit] = useState(2);
  const [samples, setSamples] = useState(128);
  const [trainHorizons, setTrainHorizons] = useState([
    "next_day",
    "one_month",
    "three_months",
  ]);
  const [runId, setRunId] = useState("");
  const [modelId, setModelId] = useState("");
  const storageKey = "training-job-" + stock?.id + "-" + basis;
  const [job, setJob] = useState(localStorage.getItem(storageKey) || "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const { data: catalog } = useLoad(
    () => api<ModelFamily[]>("/model-families"),
    [],
  );
  const { data: runs } = useLoad(
    () =>
      stock
        ? api<any[]>("/training-runs?stock_id=" + stock.id)
        : Promise.resolve([]),
    [stock?.id, version],
  );
  const compatibleRuns =
    runs?.filter((r) => r.config.price_basis === basis) || [];
  const viewedRun =
    compatibleRuns.find((r) => r.id === runId) || compatibleRuns[0];
  const { data: runDetail } = useLoad(
    () =>
      viewedRun
        ? api<any>("/training-runs/" + viewedRun.id)
        : Promise.resolve(undefined),
    [viewedRun?.id, version],
  );
  const researchResult = viewedRun?.config.evaluation_mode === "research";
  const { data: models, error: loadError } = useLoad(
    () =>
      stock && viewedRun
        ? api<Model[]>(
            "/models?" +
              new URLSearchParams({
                stock_id: stock.id,
                horizon,
                price_basis: basis,
                run_id: viewedRun.id,
              }),
          )
        : Promise.resolve([]),
    [stock?.id, basis, horizon, viewedRun?.id, version],
  );
  const current = models?.find((m) => m.id === modelId) || models?.[0];
  const baseline = models?.find((m) => m.family === "naive");
  const policy = viewedRun?.report.policy;
  const validationGain =
    current && baseline
      ? (current.metrics.validation_improvement ??
        (baseline.metrics.validation.mae > 1e-8
          ? 1 - current.metrics.validation.mae / baseline.metrics.validation.mae
          : 0))
      : undefined;
  const windowRatios =
    current?.metrics.validation.windows?.map((w: any, i: number) => {
      const base = baseline?.metrics.validation.windows?.[i]?.mae;
      return typeof base === "number" && base > 1e-8
        ? w.mae / base
        : w.mae <= 1e-8
          ? 1
          : Infinity;
    }) || [];
  const acceptanceRows = current
    ? [
        [
          "验证MAE改善",
          pct(validationGain),
          "至少 " + pct(policy?.min_improvement ?? 0.02),
          "validation_improvement",
        ],
        [
          "最终评估MAE改善",
          pct(current.metrics.improvement),
          "至少 " + pct(policy?.min_improvement ?? 0.02),
          "test_improvement",
        ],
        [
          "最差验证窗口误差 / 基准",
          score(Math.max(...windowRatios)),
          "不超过 1.10",
          "stable_windows",
        ],
        [
          "实际覆盖率",
          pct(current.metrics.test.coverage),
          "至少 " + pct(policy?.min_coverage ?? 0.75),
          "coverage",
        ],
        [
          "区间宽度 / 基准",
          baseline && baseline.metrics.test.mean_width > 1e-10
            ? score(
                current.metrics.test.mean_width /
                  baseline.metrics.test.mean_width,
              )
            : "—",
          "不超过 1.25",
          "interval_width",
        ],
      ]
    : [];
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
          name: researchResult ? "研究评估" : "历史测试",
          type: "bar",
          data: models?.map((m) => m.metrics.test?.mae),
          itemStyle: { color: "#426f53", borderRadius: [3, 3, 0, 0] },
        },
      ],
    }),
    [models, researchResult],
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
        trials_per_family: trials,
        time_budget_seconds: budget,
        horizons: trainHorizons,
        evaluation_mode: mode,
        search_profile: profile,
        ridge_alpha_min: alphaMin,
        ridge_alpha_max: alphaMax,
        arima_max_order: orderLimit,
        evaluation_samples: samples,
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
          <p>搜索真实参数，比较样本外误差，并查看每个模型的证据和限制。</p>
        </div>
        <span className="pill">建议每月复核 · 可手动重训</span>
      </div>
      <section className="card">
        <div className="card-heading">
          <div>
            <h2>训练实验配置</h2>
            <p>
              {stock?.name || "请先添加股票"} · 使用完整日线快照 · 本机串行计算
            </p>
          </div>
          <FlaskConical size={22} color="#81976b" />
        </div>
        <div className="config-grid">
          <label>
            每模型、每周期试验上限
            <input
              type="number"
              value={trials}
              min={1}
              max={40}
              onChange={(e) => setTrials(Number(e.target.value))}
            />
          </label>
          <label>
            整次训练计算时限（秒）
            <input
              type="number"
              value={budget}
              min={10}
              max={21600}
              onChange={(e) => setBudget(Number(e.target.value))}
            />
          </label>
          <label>
            评估模式
            <select value={mode} onChange={(e) => setMode(e.target.value)}>
              <option value="research">研究调参 · 可重复训练</option>
              <option value="holdout">独立验收 · 同数据仅一次</option>
            </select>
          </label>
        </div>
        <div className="check-group">
          {Object.entries(horizons).map(([value, label]) => (
            <label key={value}>
              <input
                type="checkbox"
                checked={trainHorizons.includes(value)}
                onChange={(e) =>
                  setTrainHorizons(
                    e.target.checked
                      ? [...trainHorizons, value]
                      : trainHorizons.filter((h) => h !== value),
                  )
                }
              />
              {label}
            </label>
          ))}
        </div>
        <details style={{ marginTop: 16 }}>
          <summary>参数大小与评估设置</summary>
          <div className="config-grid" style={{ marginTop: 16 }}>
            <label>
              搜索范围
              <select
                value={profile}
                onChange={(e) => setProfile(e.target.value)}
              >
                <option value="standard">标准网格</option>
                <option value="expanded">扩展网格</option>
              </select>
            </label>
            <label>
              Ridge α 下限
              <input
                type="number"
                min="0.000001"
                max="1000000"
                step="any"
                value={alphaMin}
                onChange={(e) => setAlphaMin(Number(e.target.value))}
              />
            </label>
            <label>
              Ridge α 上限
              <input
                type="number"
                min="0.000001"
                max="1000000"
                step="any"
                value={alphaMax}
                onChange={(e) => setAlphaMax(Number(e.target.value))}
              />
            </label>
            <label>
              ARIMA p / q 最高阶数
              <input
                type="number"
                min={1}
                max={3}
                value={orderLimit}
                onChange={(e) => setOrderLimit(Number(e.target.value))}
              />
            </label>
            <label>
              校准 / 评估点数上限
              <input
                type="number"
                min={32}
                max={512}
                value={samples}
                onChange={(e) => setSamples(Number(e.target.value))}
              />
            </label>
          </div>
          <p className="section-note">
            标准 Ridge
            网格在上下限之间按对数间隔取6个值，扩展取12个值；ARIMA搜索不同p、d、q，扩展SARIMA增加20日季节周期。仅用滚动验证误差选择参数。试验上限和时限共同限制搜索。
          </p>
        </details>
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
                disabled={
                  catalog?.find((f) => f.id === value)?.available === false
                }
                onChange={(e) =>
                  setFamilies(
                    e.target.checked
                      ? [...families, value]
                      : families.filter((f) => f !== value),
                  )
                }
              />
              {label}
              {catalog?.find((f) => f.id === value)?.available === false &&
                " · 依赖未安装"}
            </label>
          ))}
        </div>
        <p className="note-box">
          模型类型是算法选项，参数试验是同类算法的不同设置，保存版本是训练成功的产物。当前有
          {catalog?.filter((f) => f.available && f.id !== "naive").length ??
            "…"}
          类模型的依赖已安装。每类保留滚动验证最佳参数；研究模式保存所有成功类型，独立验收先固定最多三个候选。缺少依赖的选项暂不可训练。
        </p>
        <p className="section-note">
          基准始终参与比较。时限涵盖所选周期的调参、校准和评估；延长时间需同时增加试验次数才会探索更多参数。研究模式可反复修改设置，其结果不作为新的独立测试，也不会替换已入选模型。缺少可选依赖会记录失败。
        </p>
        <div className="row" style={{ marginTop: 18 }}>
          <button
            className="primary"
            disabled={
              !stock ||
              busy ||
              !families.length ||
              !trainHorizons.length ||
              trials < 1 ||
              trials > 40 ||
              budget < 10 ||
              budget > 21600 ||
              alphaMin <= 0 ||
              alphaMax < alphaMin ||
              !Number.isFinite(trials) ||
              !Number.isFinite(budget)
            }
            onClick={start}
          >
            <Play size={15} />
            {busy
              ? "提交中…"
              : mode === "research"
                ? "开始研究训练"
                : "开始独立验收"}
          </button>
          <span className="muted">数据不足的周期会跳过并说明原因</span>
        </div>
        {error && <p className="error">{error}</p>}
        {job && <JobPanel id={job} finished={refresh} />}
      </section>
      <section className="card">
        <label>
          查看实验
          <select
            value={viewedRun?.id || ""}
            onChange={(e) => {
              setRunId(e.target.value);
              setModelId("");
            }}
          >
            {compatibleRuns.map((r) => (
              <option key={r.id} value={r.id}>
                {r.created_at.slice(0, 16).replace("T", " ")} ·{" "}
                {r.config.evaluation_mode === "research" ? "研究" : "独立验收"}{" "}
                · {r.id.slice(0, 6)}
              </option>
            ))}
          </select>
        </label>
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
                    <th>{researchResult ? "研究MAE" : "测试MAE"}</th>
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
                      <td>{score(m.metrics.validation?.mae)}</td>
                      <td>{score(m.metrics.test?.mae)}</td>
                      <td>
                        {typeof m.metrics.improvement === "number"
                          ? (m.metrics.improvement * 100).toFixed(3) + "%"
                          : "—"}
                      </td>
                      <td>{pct(m.metrics.test?.direction_accuracy)}</td>
                      <td>{pct(m.metrics.test?.coverage)}</td>
                      <td>{money(m.metrics.test?.mean_width)}</td>
                      <td>
                        <span className={"pill" + (m.selected ? "" : " gray")}>
                          {m.selected
                            ? "已入选"
                            : m.family === "naive"
                              ? "比较基准"
                              : m.metrics.evaluation_mode === "research"
                                ? m.metrics.passes_thresholds
                                  ? "研究达标 · 待独立验收"
                                  : "研究未达标"
                                : "未入选"}
                        </span>
                        <small
                          className="muted"
                          style={{ display: "block", maxWidth: 240 }}
                        >
                          {m.reason}
                        </small>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="section-note">
              指标为各周期终点收盘价；入选要求验证和测试MAE改善至少{" "}
              {pct(viewedRun?.config.min_improvement)}
              ，各验证窗口劣化不超过10%，覆盖率不低于目标减15个百分点，区间宽度不超过基准1.25倍。研究模型不自动入选。方向表现包括涨、跌、持平；naive始终预测持平，其命中率不能用于比较涨跌判断能力。点击模型查看诊断。
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
            。拟合截止：{current.metrics.fitted_through}；
            {researchResult ? "研究评估" : "历史测试"}样本：
            {current.metrics.test.sample_count}；名义覆盖率：
            {pct(current.metrics.nominal_coverage)}。
          </p>
          {current.family !== "naive" && (
            <>
              <h3 style={{ margin: "18px 0 10px" }}>验收条件逐项核对</h3>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>验收条件</th>
                      <th>实际数值</th>
                      <th>规则门槛</th>
                      <th>结果</th>
                    </tr>
                  </thead>
                  <tbody>
                    {acceptanceRows.map(([name, value, threshold, key]) => (
                      <tr key={key}>
                        <td>{name}</td>
                        <td>{value}</td>
                        <td>{threshold}</td>
                        <td
                          className={
                            current.metrics.acceptance_checks?.[key]
                              ? "acceptance-pass"
                              : "acceptance-fail"
                          }
                        >
                          {current.metrics.acceptance_checks?.[key]
                            ? "通过"
                            : "未通过"}
                        </td>
                      </tr>
                    ))}
                    <tr>
                      <td>评估数据独立性</td>
                      <td>
                        {researchResult ? "重复历史研究" : "本版本一次性验收"}
                      </td>
                      <td>独立验收才可入选</td>
                      <td
                        className={
                          researchResult ? "acceptance-fail" : "acceptance-pass"
                        }
                      >
                        {researchResult ? "待新数据验证" : "符合当前流程"}
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
              <p className="section-note">
                五项误差和区间门槛全部通过，且属于独立验收，才标为“已入选”。研究达标表示通过工程门槛，仍需新的未使用数据；统计优势及可靠性另看下方检验，不能由入选标签保证。
              </p>
            </>
          )}
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>实际评估指标</th>
                  <th>数值</th>
                  <th>含义</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td>RMSE / MASE</td>
                  <td>
                    {score(current.metrics.test.rmse)} /{" "}
                    {score(current.metrics.test.mase)}
                  </td>
                  <td>RMSE强调大误差；MASE相对拟合期日变化缩放</td>
                </tr>
                <tr>
                  <td>平均偏差 / 最大绝对误差</td>
                  <td>
                    {score(current.metrics.assessment?.bias)} /{" "}
                    {score(current.metrics.assessment?.max_absolute_error)}
                  </td>
                  <td>实际减预测，单位元</td>
                </tr>
                <tr>
                  <td>相对基准RMSE</td>
                  <td>{score(current.metrics.assessment?.relative_rmse)}</td>
                  <td>小于1表示误差小于naive</td>
                </tr>
                <tr>
                  <td>区间评分 / Pinball</td>
                  <td>
                    {score(current.metrics.test.interval_score)} /{" "}
                    {score(current.metrics.test.pinball_loss)}
                  </td>
                  <td>兼顾区间宽度和漏覆盖；越低越好</td>
                </tr>
                <tr>
                  <td>优势检验 p / Holm校正p</td>
                  <td>
                    {probability(
                      current.metrics.assessment?.skill_test?.pvalue,
                    )}{" "}
                    /{" "}
                    {probability(
                      current.metrics.assessment?.skill_test?.adjusted_pvalue,
                    )}
                  </td>
                  <td>原假设：模型平均绝对误差不低于naive</td>
                </tr>
                <tr>
                  <td>平均误差优势95%区间</td>
                  <td>
                    {current.metrics.assessment?.skill_test?.gain_ci95
                      ?.map(score)
                      .join(" — ") || "未计算"}
                  </td>
                  <td>正值表示误差更低；不是价格预测区间</td>
                </tr>
                <tr>
                  <td>评估点 / 不重叠窗口</td>
                  <td>
                    {current.metrics.test.sample_count} /{" "}
                    {current.metrics.test.nonoverlap_sample_count ??
                      "旧报告未记录"}
                  </td>
                  <td>不重叠数量也不等于统计独立样本数量</td>
                </tr>
              </tbody>
            </table>
          </div>
          <p className="note-box">
            {researchResult
              ? "本实验重复使用历史评估数据，仅供研究。"
              : "独立测试也不能保证未来表现。"}
            {current.metrics.assessment?.skill_test?.note}{" "}
            统计检验与是否通过误差门槛分开报告；目前没有足够证据保证价格预测可靠。
          </p>
          <div className="chart-grid" style={{ marginTop: 18 }}>
            <div className="subcard">
              <h3>预测与实际 · {researchResult ? "研究评估" : "历史测试"}</h3>
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
            Ljung-Box p：{probability(diagnosis.ljung_box_pvalue)} · ARCH p：
            {probability(diagnosis.arch_pvalue)}
            。检测误差自相关与波动聚集；p&gt;0.05不能证明模型可靠。旧报告为非连续抽样，长周期误差可能重叠，诊断仅作探索参考。
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
      {viewedRun?.report?.horizons && (
        <section className="card">
          <h2>当前实验报告与实际参数</h2>
          <div className="trial-list">
            {Object.entries(viewedRun.report.horizons).map(([h, r]) => (
              <p className="section-note" key={h}>
                {horizons[h]}：{(r as any).message} · {(r as any).status}
              </p>
            ))}
          </div>
          <p className="section-note">
            {viewedRun.report.budget_exhausted ? "本次优化预算已用尽。" : ""}
            设置时限：{viewedRun.config.time_budget_seconds}秒；实际耗时：
            {viewedRun.report.elapsed_seconds ?? "旧报告未记录"}；
            {viewedRun.report.evaluation_note}
          </p>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>周期</th>
                  <th>模型</th>
                  <th>实际参数</th>
                  <th>状态</th>
                  <th>验证MAE / 失败原因</th>
                </tr>
              </thead>
              <tbody>
                {runDetail?.trials?.map((t: any) => (
                  <tr key={t.id}>
                    <td>{horizons[t.horizon]}</td>
                    <td>{t.family}</td>
                    <td>{JSON.stringify(t.parameters)}</td>
                    <td>{t.status === "succeeded" ? "完成" : "失败"}</td>
                    <td>{t.report.error || score(t.report.validation_mae)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </>
  );
}
