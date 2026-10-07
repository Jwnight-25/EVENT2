"""Refit fixed settings at the prediction cutoff, using causal rolling residuals."""

import numpy as np


def calibrated_width(errors, coverage):
    if len(errors) < 8:
        raise ValueError("区间校准有效样本不足8")
    quantile = min(1.0, np.ceil((len(errors) + 1) * coverage) / len(errors))
    return np.quantile(np.abs(errors), quantile, axis=0, method="higher")


def prepare_deployment(rows, features, model, config, job_id, deadline):
    from .research import STEPS, checkpoint, fit, evaluate, save_artifact
    from .db import uid

    horizon, family, parameters = model.horizon, model.family, model.parameters
    steps, coverage = STEPS[horizon], model.metrics["nominal_coverage"]
    count = config.get("evaluation_samples", 128)
    audit_count = max(32, steps + 8)
    # Purge between calibration and audit: the final calibration label ends
    # before the first audit origin. Audit answers never choose the width.
    audit_start = len(rows) - steps - audit_count
    cal_stop = audit_start - steps + 1  # exclusive origin boundary
    cal_start = max(100, cal_stop - count)
    if cal_stop - cal_start < 32:
        raise ValueError("最新数据不足以隔离滚动校准和近期复核窗口")
    seed = config.get("seed", 42)

    def rolling(start, stop, width=None):
        errors, reports = [], []
        # Approximately monthly refitting, always using labels known at origin.
        for origin in range(start, stop, 21):
            checkpoint(job_id, f"近期滚动复核 · {family}", deadline=deadline)
            end = min(stop, origin + 21)
            artifact = fit(rows, features, origin, horizon, family, parameters, seed)
            artifact["coverage"] = coverage
            metric, residuals, _ = evaluate(
                artifact,
                rows,
                features,
                origin,
                end - 1 + steps,
                job_id,
                deadline,
                width,
                max_samples=end - origin,
                minimum_samples=1,
            )
            errors.extend(residuals)
            reports.append(metric)
        return np.asarray(errors), reports

    errors, _ = rolling(cal_start, cal_stop)
    width = calibrated_width(errors, coverage)
    _, audits = rolling(audit_start, len(rows) - steps, width)
    weights = [m["sample_count"] for m in audits]
    audit = {
        key: float(np.average([m[key] for m in audits], weights=weights))
        for key in ("mae", "mape", "coverage", "mean_width", "interval_score")
    }
    audit.update({"sample_count": sum(weights), "nonoverlap_windows": int(np.ceil(audit_count / steps))})
    checkpoint(job_id, f"最新数据部署拟合 · {family}", deadline=deadline)
    # Only after measuring causal rolling errors do we fit on all available data.
    artifact = fit(rows, features, len(rows) - 1, horizon, family, parameters, seed)
    artifact.update({"coverage": coverage, "width": width})
    path, checksum = save_artifact(artifact, uid())
    detail = {
        "method": "fixed-settings-latest-refit-with-rolling-residuals",
        "fitted_through": rows[-1]["time"],
        "calibration_start": rows[cal_start + 1]["time"],
        "calibration_end": rows[cal_stop - 1 + steps]["time"],
        "calibration_samples": len(errors),
        "audit_start": rows[audit_start + 1]["time"],
        "audit_end": rows[-1]["time"],
        "recent_audit": audit,
        "refit_interval_bars": 21,
        "artifact_path": path,
        "artifact_checksum": checksum,
        "note": "近期复核来自参数固定、每21交易日重新拟合的历史预测；最新全数据产物没有新的样本外成绩，滚动残差区间为代理校准，不保证未来覆盖",
    }
    return artifact, detail
