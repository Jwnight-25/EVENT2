"""Descriptive evidence labels, independent of the user's price-match gate."""


def reliability(model, baseline=None):
    metrics = model.metrics
    test = metrics["test"]
    folds = [f["metrics"] for f in metrics.get("cross_validation", {}).get("folds", [])] or [test]
    nominal = metrics["nominal_coverage"]
    skill = metrics.get("assessment", {}).get("skill_test", {})
    base = baseline.metrics["test"] if baseline else None
    gain = (1 - test["mae"] / base["mae"]) if base and base["mae"] > 1e-8 else None
    significant = (
        gain is not None
        and gain > 0
        and skill.get("adjusted_pvalue") is not None
        and skill["adjusted_pvalue"] < 0.05
        and skill.get("gain_ci95", [0])[0] > 0
    )
    baseline_status = (
        "baseline"
        if model.family == "naive"
        else "unknown"
        if gain is None
        else "underperforming"
        if gain <= 0
        else "mixed_gain"
        if test["rmse"] >= base["rmse"]
        else "significant_gain"
        if significant
        else "unconfirmed_gain"
    )
    labels = {
        "baseline": "比较基准",
        "unknown": "缺少基准比较",
        "underperforming": "未优于基准",
        "mixed_gain": "MAE改善 · 大误差未改善",
        "significant_gain": "存在统计优势证据",
        "unconfirmed_gain": "误差改善 · 优势尚未证实",
    }
    relative_score = (
        test["interval_score"] / base["interval_score"] if base and base["interval_score"] > 1e-8 else None
    )
    min_coverage = min(f["coverage"] for f in folds)
    max_coverage = max(f["coverage"] for f in folds)
    stable = (
        min_coverage >= nominal - 0.05
        and max_coverage <= min(1, nominal + 0.05)
        and (relative_score is None or relative_score <= 1.1)
    )
    warnings = []
    if test.get("nonoverlap_sample_count", 0) < 20:
        warnings.append("不重叠窗口不足20个，长周期证据有限；窗口之间仍可能相关")
    if gain is not None and gain <= 0:
        warnings.append("价格匹配达标也可能没有优于保持最新收盘价的naive基准")
    if min_coverage < nominal - 0.05:
        warnings.append("至少一个验证区段的覆盖率低于目标超过5个百分点")
    if max_coverage > nominal + 0.05:
        warnings.append("部分区段覆盖过高，需同时关注区间宽度与评分")
    return {
        "price_match_passed": metrics.get("passes_thresholds", False),
        "baseline_status": baseline_status,
        "baseline_label": labels[baseline_status],
        "mae_improvement": gain,
        "interval_stable": stable,
        "interval_label": "区间相对稳定" if stable else "区间稳定性不足",
        "relative_interval_score": relative_score,
        "worst_fold_match": min(f.get("price_match", 0) for f in folds),
        "worst_fold_mae": max(f["mae"] for f in folds),
        "min_fold_coverage": min_coverage,
        "max_fold_width": max(f["mean_width"] for f in folds),
        "nonoverlap_windows": test.get("nonoverlap_sample_count"),
        "rules": "区间标识要求每折覆盖处于目标±5个百分点，且总体区间评分不高于基准1.1倍；仅为研究提示，不改变入选规则；基准优势同时参考MAE、RMSE及Holm校正p值",
        "warnings": warnings,
    }
