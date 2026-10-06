"""Supplementary out-of-sample evidence; never changes historical acceptance."""

import numpy as np
from scipy.stats import norm
from statsmodels.regression.linear_model import OLS
from statsmodels.stats.multitest import multipletests


CHECK_LABELS = {
    "validation_improvement": "验证误差改善不足设定门槛",
    "test_improvement": "测试误差改善不足设定门槛",
    "stable_windows": "部分验证窗口比基准差超过10%",
    "coverage": "实际覆盖率低于门槛",
    "interval_width": "区间比基准宽超过25%",
}


def compare_errors(series, baseline, steps):
    result = {
        "pvalue": None,
        "adjusted_pvalue": None,
        "gain_ci95": None,
        "method": "配对绝对误差差异；单侧HAC均值检验；候选间Holm校正",
        "null": "模型平均绝对误差不低于naive",
        "note": "近似检验依赖平稳性；不是未来盈利或成功概率。",
    }
    if series.get("dates") != baseline.get("dates"):
        return {**result, "status": "unaligned", "note": "模型与基准评估日期不一致，不能配对。"}
    errors = np.asarray(series["residuals"], dtype=float)
    base_errors = np.asarray(baseline["residuals"], dtype=float)
    origins = np.asarray(series.get("origin_indices", []), dtype=int)
    gains = np.abs(base_errors) - np.abs(errors)
    result.update({"sample_count": len(gains), "mean_absolute_error_gain": float(gains.mean())})
    # Legacy evenly spread samples are not a consecutive trading-day sequence.
    # Do not silently use a daily HAC lag on irregularly spaced observations.
    if len(origins) != len(gains) or not np.all(np.diff(origins) == 1):
        return {
            **result,
            "status": "irregular_sampling",
            "note": "旧报告为非连续抽样，未计算日频优势检验p值；请查看新研究实验。",
        }
    nonoverlap = len(origins[::steps])
    result["nonoverlap_sample_count"] = nonoverlap
    if len(gains) < 30 or nonoverlap < 20:
        return {
            **result,
            "status": "insufficient_samples",
            "note": "至少需要30个评估点及20个不重叠窗口；当前不报告显著性。",
        }
    if np.std(gains) < 1e-12:
        return {**result, "status": "degenerate", "note": "误差差异方差过小，无法可靠估计显著性。"}
    lag = min(len(gains) // 4, max(steps - 1, int(4 * (len(gains) / 100) ** (2 / 9))))
    fitted = OLS(gains, np.ones((len(gains), 1))).fit(
        cov_type="HAC", cov_kwds={"maxlags": lag, "use_correction": True}, use_t=False
    )
    se = float(fitted.bse[0])
    if not np.isfinite(se) or se <= 1e-12:
        return {**result, "status": "degenerate"}
    mean = float(gains.mean())
    return {
        **result,
        "status": "approximate",
        "hac_lags": lag,
        "pvalue": float(norm.sf(mean / se)),
        "gain_ci95": [mean - 1.96 * se, mean + 1.96 * se],
    }


def assess_models(models, rows, steps):
    """Return values derived from each frozen report and its matching naive."""
    baseline = next((m for m in models if m.family == "naive"), None)
    date_indices = {r["time"]: i for i, r in enumerate(rows)}

    def series(model):
        diagnostic = model.diagnostics
        return {
            **diagnostic,
            "origin_indices": diagnostic.get("origin_indices")
            or [date_indices.get(day, -1) - steps for day in diagnostic.get("dates", [])],
        }

    evidence = {}
    for model in models:
        metric = model.metrics
        residuals = np.asarray(model.diagnostics.get("residuals", []), dtype=float)
        item = {
            "bias": float(residuals.mean()) if len(residuals) else None,
            "max_absolute_error": float(np.max(np.abs(residuals))) if len(residuals) else None,
            "evaluation_mode": metric.get("evaluation_mode", "holdout"),
            "interpretation": "尚不能证明可靠；达标表示通过预设误差和区间门槛。",
        }
        if baseline and model.family != "naive":
            item["baseline_test_mae"] = baseline.metrics["test"]["mae"]
            base_rmse = baseline.metrics["test"]["rmse"]
            item["relative_rmse"] = metric["test"]["rmse"] / base_rmse if base_rmse > 1e-12 else None
            item["skill_test"] = compare_errors(series(model), series(baseline), steps)
        evidence[model.id] = item
    tested = [
        item["skill_test"]
        for item in evidence.values()
        if item.get("skill_test", {}).get("pvalue") is not None
    ]
    # Missing tests are conservatively p=1, preserving the declared comparison family.
    family_size = sum(m.family != "naive" for m in models)
    if tested:
        adjusted = multipletests(
            [t["pvalue"] for t in tested] + [1.0] * (family_size - len(tested)), alpha=0.05, method="holm"
        )[1]
        for item, value in zip(tested, adjusted):
            item["adjusted_pvalue"] = float(value)
    return evidence
