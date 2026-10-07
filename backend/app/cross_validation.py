"""Forward-only three-fold validation over the final 15% of a frozen history."""

import numpy as np


def fold_boundaries(count, steps):
    validation_start = int(count * 0.85)
    edges = np.linspace(validation_start, count, 4, dtype=int)
    # Match the 70% parameter-search boundary exactly, including integer rounding.
    calibration_size = validation_start - int(count * 0.7)
    folds = []
    for start, stop in zip(edges[:-1], edges[1:]):
        # Forecast labels stay wholly within this fold. Calibration labels end
        # before it, and fitting labels end before calibration begins.
        fit_end = int(start) - calibration_size - 1
        if stop - start < steps + 7 or calibration_size < steps + 8 or fit_end - steps < 100:
            raise ValueError("最后15%数据不足以划分三折并隔离当前预测周期的标签")
        folds.append({"fit_end": fit_end, "start": int(start), "end": int(stop) - 1})
    return folds


def merge_folds(metrics, series):
    weights = np.array([m["sample_count"] for m in metrics], dtype=float)
    total = int(weights.sum())
    result = dict(metrics[-1])
    for key in (
        "mae",
        "direction_accuracy",
        "coverage",
        "mean_width",
        "pinball_loss",
        "interval_score",
        "bias",
        "mape",
    ):
        result[key] = float(np.average([m[key] for m in metrics], weights=weights))
    result["rmse"] = float(np.sqrt(np.average([m["rmse"] ** 2 for m in metrics], weights=weights)))
    result["mase"] = (
        float(np.average([m["mase"] for m in metrics], weights=weights))
        if all(m["mase"] is not None for m in metrics)
        else None
    )
    for key in ("coverage_by_target", "width_by_target"):
        result[key] = np.average([m[key] for m in metrics], axis=0, weights=weights).tolist()
    result["mae_by_target"] = {
        key: float(np.average([m["mae_by_target"][key] for m in metrics], weights=weights))
        for key in metrics[0]["mae_by_target"]
    }
    result.update(
        {
            "sample_count": total,
            "price_match": max(0.0, 1 - result["mape"]),
            "evaluation_start": metrics[0]["evaluation_start"],
            "sampling": "purged_forward_folds",
            "nonoverlap_sample_count": sum(m["nonoverlap_sample_count"] for m in metrics),
        }
    )
    list_keys = ("origin_indices", "dates", "predicted", "actual", "residuals")
    combined = {key: [item for s in series for item in s[key]] for key in list_keys}
    combined["by_target"] = {
        field: {key: [item for s in series for item in s["by_target"][field][key]] for key in list_keys}
        for field in series[0]["by_target"]
    }
    return result, combined


def validate_family(rows, features, horizon, family, settings, config, job_id, deadline):
    from .research import STEPS, fit, evaluate, checkpoint

    boundaries = fold_boundaries(len(rows), STEPS[horizon])
    metrics, series, folds, calibration_counts = [], [], [], []
    artifact = None
    for index, boundary in enumerate(boundaries):
        fit_end, start, end = boundary["fit_end"], boundary["start"], boundary["end"]
        checkpoint(job_id, f"15%交叉验证 第{index + 1}/3折 · {family} · {horizon}", deadline=deadline)
        artifact = fit(rows, features, fit_end, horizon, family, settings["parameters"], config["seed"])
        artifact["coverage"] = config["coverage"]
        _, errors, _ = evaluate(
            artifact,
            rows,
            features,
            fit_end + 1,
            start - 1,
            job_id,
            deadline,
            max_samples=config["evaluation_samples"],
        )
        quantile = min(1.0, np.ceil((len(errors) + 1) * config["coverage"]) / len(errors))
        width = np.quantile(np.abs(errors), quantile, axis=0, method="higher")
        artifact["width"] = width
        # Every eligible origin is evaluated; evaluation_samples only limits calibration.
        metric, _, values = evaluate(
            artifact,
            rows,
            features,
            start - 1,
            end,
            job_id,
            deadline,
            width,
            max_samples=end - start + 1,
        )
        metrics.append(metric)
        series.append(values)
        calibration_counts.append(len(errors))
        folds.append(
            {
                "fold": index + 1,
                "fitted_through": rows[fit_end]["time"],
                "calibration_start": rows[fit_end + 1]["time"],
                "calibration_end": rows[start - 1]["time"],
                "validation_start": rows[start]["time"],
                "validation_end": rows[end]["time"],
                "metrics": metric,
                "fit_diagnostics": artifact.get("fit_diagnostics"),
            }
        )
    pooled, combined = merge_folds(metrics, series)
    details = {
        "method": "three_fold_forward_validation",
        "validation_fraction": 0.15,
        "validation_bars": len(rows) - int(len(rows) * 0.85),
        "folds": folds,
        "match_threshold": config["match_threshold"],
        "match_definition": "max(0, 1 - MAPE)，以周期终点实际收盘价作百分比误差分母",
        "note": "历史数据内部验证，可重复训练；不作为新的独立测试或盈利概率。",
    }
    return artifact, pooled, combined, details, sum(calibration_counts)
