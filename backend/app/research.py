"""Causal multi-horizon experiments. No random split, no fabricated predictions."""

import hashlib
import time
import warnings

import joblib
import numpy as np
import pandas as pd
from scipy.stats import skew, kurtosis
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sqlalchemy import select
from statsmodels.tsa.arima.model import ARIMA
from statsmodels.tsa.stattools import acf, pacf
from statsmodels.stats.diagnostic import acorr_ljungbox, het_arch

from .config import DATA_DIR
from .db import SessionLocal, now, uid
from .entities import Job, TrainingRun, Snapshot, Model, Trial, Prediction
from .errors import DomainError
from .market import load_snapshot
from .calendar import future_dates

STEPS = {"next_day": 1, "one_month": 20, "three_months": 60}
VERSION = "direct-multistep-v3"


def relative_improvement(error, baseline):
    # A zero-error baseline cannot be improved; avoid declaring a 100% gain.
    return 0.0 if baseline <= 1e-8 else 1 - error / baseline


def provenance():
    from importlib.metadata import version
    from pathlib import Path

    source = Path(__file__).parent
    digest = hashlib.sha256()
    for file in sorted(source.glob("*.py")):
        digest.update(file.name.encode())
        digest.update(file.read_bytes())
    return {
        "source_checksum": digest.hexdigest(),
        "dependencies": {
            name: version(name) for name in ("numpy", "pandas", "statsmodels", "scikit-learn", "sqlalchemy")
        },
    }


class Cancelled(Exception):
    pass


class BudgetExceeded(Exception):
    pass


def checkpoint(job_id, stage, progress=None, deadline=None):
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        if job.cancel_requested:
            raise Cancelled()
        job.stage, job.progress, job.heartbeat_at = stage, progress, now()
        db.commit()
    if deadline and time.monotonic() > deadline:
        raise BudgetExceeded()


def feature_matrix(rows):
    frame = pd.DataFrame(rows)
    close = frame.close.astype(float)
    log_return = np.log(close).diff()
    features = {f"return_lag_{i}": log_return.shift(i) for i in range(10)}
    features.update({"range": np.log(frame.high / frame.low), "body": np.log(frame.close / frame.open)})
    for window in (5, 20, 60):
        features[f"momentum_{window}"] = np.log(close / close.shift(window))
        features[f"volatility_{window}"] = log_return.rolling(window).std()
        mean = frame.volume.rolling(window).mean()
        features[f"volume_ratio_{window}"] = frame.volume / mean.replace(0, np.nan) - 1
    # Every feature at origin t uses data through t only. No two-sided smoothing.
    return pd.DataFrame(features).replace([np.inf, -np.inf], np.nan).fillna(0).to_numpy(dtype=float)


def target(rows, origin, horizon):
    close = rows[origin]["close"]
    future = rows[origin + 1 : origin + STEPS[horizon] + 1]
    if horizon == "next_day":
        r = future[0]
        return np.array(
            [
                np.log(r["open"] / close),
                np.log(r["close"] / close),
                np.log(r["high"] / max(r["open"], r["close"])),
                np.log(min(r["open"], r["close"]) / r["low"]),
            ]
        )
    return np.log(np.array([r["close"] for r in future]) / close)


def training_indices(end, steps):
    # end is inclusive last known observation; labels must end no later than end.
    return np.arange(60, end - steps + 1)


def encode_prices(vector, last_close, horizon):
    if not np.isfinite(vector).all():
        raise ValueError("模型输出非有限值")
    vector = np.clip(vector, -8, 8)
    if horizon == "next_day":
        opened, closed = last_close * np.exp(vector[:2])
        high = max(opened, closed) * np.exp(max(0, vector[2]))
        low = min(opened, closed) * np.exp(-max(0, vector[3]))
        return np.array([opened, high, low, closed])
    return last_close * np.exp(vector)


def interval_prices(vector, width, close, horizon):
    if horizon != "next_day":
        return encode_prices(vector - width, close, horizon), encode_prices(vector + width, close, horizon)
    lo, hi = vector - width, vector + width
    open_lo, close_lo = close * np.exp(np.clip(lo[:2], -8, 8))
    open_hi, close_hi = close * np.exp(np.clip(hi[:2], -8, 8))
    return (
        np.array(
            [
                open_lo,
                max(open_lo, close_lo) * np.exp(max(0, lo[2])),
                min(open_lo, close_lo) * np.exp(-max(0, hi[3])),
                close_lo,
            ]
        ),
        np.array(
            [
                open_hi,
                max(open_hi, close_hi) * np.exp(max(0, hi[2])),
                min(open_hi, close_hi) * np.exp(-max(0, lo[3])),
                close_hi,
            ]
        ),
    )


def candidates(family, config=None):
    config = config or {}
    expanded = config.get("search_profile") == "expanded"
    orders = [[0, 1, 0], [1, 1, 0], [0, 1, 1], [1, 1, 1], [1, 0, 1]]
    limit = config.get("arima_max_order", 2)
    orders += [
        [p, d, q]
        for p in range(limit + 1)
        for q in range(limit + 1)
        for d in (0, 1)
        if [p, d, q] not in orders and p + q > 0
    ]
    grid = {
        "naive": [{}],
        "ridge": [
            {"alpha": float(a)}
            for a in np.unique(
                np.geomspace(
                    config.get("ridge_alpha_min", 0.01),
                    config.get("ridge_alpha_max", 1000),
                    12 if expanded else 6,
                )
            )
        ],
        "arima": [{"order": p} for p in orders],
        "sarima": [
            {"order": [1, 1, 0], "seasonal_order": [p, 0, q, period]}
            for period in ((5, 20) if expanded else (5,))
            for p, q in ((1, 0), (0, 1), (1, 1))
        ],
        "lightgbm": [
            {"num_leaves": n, "n_estimators": count, "learning_rate": rate}
            for count, rate in ((80, 0.04), (160, 0.02))
            for n in (7, 15)
        ],
        "garch": [{"order": [1, 1, 0], "p": p, "q": q} for p, q in ((1, 1), (1, 2), (2, 1))],
        "nhits": [{"input_size": size, "max_steps": count} for size in (60, 120) for count in (100, 200)],
        "patchtst": [{"input_size": size, "max_steps": count} for size in (60, 120) for count in (100, 200)],
    }
    return grid[family]


def fit(rows, features, end, horizon, family, parameters, seed):
    steps = STEPS[horizon]
    indices = training_indices(end, steps)
    if len(indices) < 40:
        raise ValueError("训练窗口有效样本不足40")
    targets = np.array([target(rows, int(i), horizon) for i in indices])
    artifact = {
        "family": family,
        "horizon": horizon,
        "parameters": parameters,
        "fit_end": end,
        "shape": np.mean(targets[:, 2:], axis=0).tolist() if horizon == "next_day" else None,
        "version": VERSION,
    }
    if family == "naive":
        artifact["estimator"] = None
    elif family == "ridge":
        estimator = make_pipeline(StandardScaler(), Ridge(alpha=parameters["alpha"]))
        estimator.fit(features[indices], targets)
        artifact["estimator"] = estimator
    elif family == "lightgbm":
        from lightgbm import LGBMRegressor

        # Quantile objectives per target; intervals are additionally calibrated.
        estimators = []
        for column in range(targets.shape[1]):
            quantiles = []
            for quantile in (0.05, 0.5, 0.95):
                estimator = LGBMRegressor(
                    objective="quantile",
                    alpha=quantile,
                    verbosity=-1,
                    random_state=seed,
                    n_jobs=1,
                    **parameters,
                )
                estimator.fit(features[indices], targets[:, column])
                quantiles.append(estimator)
            estimators.append(quantiles)
        artifact["estimator"] = estimators
    elif family in ("arima", "sarima", "garch"):
        log_close = np.log([r["close"] for r in rows[: end + 1]])
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            specification = ARIMA(
                log_close,
                order=tuple(parameters["order"]),
                seasonal_order=tuple(parameters.get("seasonal_order", [0, 0, 0, 0])),
            )
            estimator = specification.fit(method_kwargs={"maxiter": 80})
            retried = not estimator.mle_retvals.get("converged", True)
            if retried:
                estimator = specification.fit(start_params=estimator.params, method_kwargs={"maxiter": 400})
        if not estimator.mle_retvals.get("converged", True):
            raise ValueError("统计模型未收敛")
        artifact["estimator"] = estimator
        artifact["fit_diagnostics"] = {
            "converged": True,
            "optimizer_retry": retried,
            "max_iterations": 400 if retried else 80,
        }
        if family == "garch":
            from arch import arch_model

            volatility = arch_model(
                np.diff(log_close) * 100,
                mean="Zero",
                vol="GARCH",
                p=parameters["p"],
                q=parameters["q"],
                dist="t",
            ).fit(disp="off")
            artifact["volatility"] = volatility
            artifact["variance_reference"] = float(volatility.conditional_volatility[-1] ** 2)
    else:
        from neuralforecast import NeuralForecast
        from neuralforecast.models import NHITS, PatchTST
        from neuralforecast.losses.pytorch import MAE

        model_cls = NHITS if family == "nhits" else PatchTST
        settings = {
            "h": steps,
            "input_size": parameters["input_size"],
            "max_steps": parameters["max_steps"],
            "random_seed": seed,
            "loss": MAE(),
            "accelerator": "cpu",
            "devices": 1,
            "enable_progress_bar": False,
            "logger": False,
        }
        if family == "nhits":
            settings["mlp_units"] = [[32, 32]] * 3
        else:
            settings.update({"hidden_size": 32, "n_heads": 4, "encoder_layers": 2})
        estimator = NeuralForecast(models=[model_cls(**settings)], freq="D")
        history = pd.DataFrame(
            {
                "unique_id": "stock",
                "ds": pd.date_range("2000-01-01", periods=end + 1),
                "y": np.log([r["close"] for r in rows[: end + 1]]),
            }
        )
        estimator.fit(df=history)
        artifact["estimator"] = estimator
    return artifact


def forecast(artifact, rows, features, origin):
    family, horizon = artifact["family"], artifact["horizon"]
    steps = STEPS[horizon]
    if family == "naive":
        return np.r_[0.0, 0.0, artifact["shape"]] if horizon == "next_day" else np.zeros(steps)
    if family == "ridge":
        return artifact["estimator"].predict(features[origin : origin + 1])[0]
    if family == "lightgbm":
        values = [
            sorted(float(estimator.predict(features[origin : origin + 1])[0]) for estimator in quantiles)
            for quantiles in artifact["estimator"]
        ]
        return np.array([v[1] for v in values])
    if family in ("arima", "sarima", "garch"):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            state = artifact["estimator"].apply(np.log([r["close"] for r in rows[: origin + 1]]), refit=False)
            path = np.asarray(state.forecast(steps)) - np.log(rows[origin]["close"])
    else:
        history = pd.DataFrame(
            {
                "unique_id": "stock",
                "ds": pd.date_range("2000-01-01", periods=origin + 1),
                "y": np.log([r["close"] for r in rows[: origin + 1]]),
            }
        )
        result = artifact["estimator"].predict(df=history)
        column = [c for c in result.columns if c not in ("unique_id", "ds")][0]
        path = result[column].to_numpy() - np.log(rows[origin]["close"])
    return np.r_[path[0] * 0.5, path[0], artifact["shape"]] if horizon == "next_day" else path


def volatility_scale(artifact, rows, origin):
    size = 4 if artifact["horizon"] == "next_day" else STEPS[artifact["horizon"]]
    if artifact["family"] != "garch":
        return np.ones(size)
    from arch import arch_model

    returns = np.diff(np.log([r["close"] for r in rows[: origin + 1]])) * 100
    settings = artifact["parameters"]
    fixed = arch_model(returns, mean="Zero", vol="GARCH", p=settings["p"], q=settings["q"], dist="t").fix(
        artifact["volatility"].params
    )
    variance = fixed.forecast(horizon=STEPS[artifact["horizon"]], reindex=False).variance.iloc[-1].to_numpy()
    ratio = np.sqrt(np.maximum(variance, 1e-10) / max(artifact["variance_reference"], 1e-10))
    return np.repeat(ratio[0], 4) if artifact["horizon"] == "next_day" else ratio


def evaluate(artifact, rows, features, start, end, job_id, deadline, width=None, max_samples=32):
    horizon = artifact["horizon"]
    # Frozen coefficients within this evaluation window. Actual observations update
    # only causal input/state, never fitted parameters.
    origins = list(range(start, end - STEPS[horizon] + 1))
    origins = origins[-max_samples:]
    estimates, actuals, residuals, covered, widths, dates = [], [], [], [], [], []
    full_predicted, full_actual = [], []
    target_errors = []
    pinballs = []
    interval_scores = []
    for origin in origins:
        checkpoint(job_id, f"评估 {artifact['family']} · {horizon}", deadline=deadline)
        pred = forecast(artifact, rows, features, origin)
        real = target(rows, origin, horizon)
        scale = volatility_scale(artifact, rows, origin)
        target_errors.append((real - pred) / scale)
        close = rows[origin]["close"]
        pred_price, real_price = encode_prices(pred, close, horizon), encode_prices(real, close, horizon)
        full_predicted.append(pred_price.tolist())
        full_actual.append(real_price.tolist())
        estimates.append(float(pred_price[-1]))
        actuals.append(float(real_price[-1]))
        residuals.append(float(real_price[-1] - pred_price[-1]))
        dates.append(rows[origin + STEPS[horizon]]["time"])
        if width is not None:
            low, high = interval_prices(pred, width * scale, close, horizon)
            covered.append(((real_price >= low) & (real_price <= high)).astype(float).tolist())
            widths.append((high - low).tolist())
            tail = (1 - artifact["coverage"]) / 2
            alpha = 1 - artifact["coverage"]
            interval_scores.append(
                (
                    high
                    - low
                    + 2 / alpha * np.maximum(low - real_price, 0)
                    + 2 / alpha * np.maximum(real_price - high, 0)
                ).tolist()
            )
            errors_low, errors_high = real_price - low, real_price - high
            pinballs.append(
                (
                    (
                        np.maximum(tail * errors_low, (tail - 1) * errors_low)
                        + np.maximum((1 - tail) * errors_high, -tail * errors_high)
                    )
                    / 2
                ).tolist()
            )
    if len(estimates) < 8:
        raise ValueError("评估窗口有效样本不足8；请增加历史数据")
    estimates, actuals = np.array(estimates), np.array(actuals)
    base_prices = np.array([rows[i]["close"] for i in origins])
    metric = {
        "mae": float(np.mean(np.abs(actuals - estimates))),
        "mape": float(np.mean(np.abs(actuals - estimates) / actuals)),
        "rmse": float(np.sqrt(np.mean((actuals - estimates) ** 2))),
        "direction_accuracy": float(
            np.mean(np.sign(estimates - base_prices) == np.sign(actuals - base_prices))
        ),
        "sample_count": len(estimates),
        "evaluation_start": dates[0],
        "evaluation_end": dates[-1],
        "target": "close_at_horizon",
        "overlapping_labels": STEPS[horizon] > 1,
        "sampling": "consecutive_trading_origins",
        "nonoverlap_sample_count": len(origins[:: STEPS[horizon]]),
        "bias": float(np.mean(actuals - estimates)),
        "mase": None,
    }
    metric["price_match"] = max(0.0, 1 - metric["mape"])
    scale = np.mean(np.abs(np.diff([r["close"] for r in rows[: artifact["fit_end"] + 1]])))
    if scale > 1e-12:
        metric["mase"] = metric["mae"] / float(scale)
    if covered:
        metric.update(
            {
                "coverage": float(np.mean(np.array(covered)[:, -1])),
                "mean_width": float(np.mean(np.array(widths)[:, -1])),
                "pinball_loss": float(np.mean(np.array(pinballs)[:, -1])),
                "interval_score": float(np.mean(np.array(interval_scores)[:, -1])),
                "coverage_by_target": np.mean(covered, axis=0).tolist(),
                "width_by_target": np.mean(widths, axis=0).tolist(),
            }
        )
    fields = (
        ["open", "high", "low", "close"]
        if horizon == "next_day"
        else [f"step_{i + 1}" for i in range(STEPS[horizon])]
    )
    predicted_matrix, actual_matrix = np.array(full_predicted), np.array(full_actual)
    metric["mae_by_target"] = {
        field: float(np.mean(np.abs(actual_matrix[:, i] - predicted_matrix[:, i])))
        for i, field in enumerate(fields)
    }
    series = {
        "origin_indices": origins,
        "dates": dates,
        "predicted": estimates.tolist(),
        "actual": actuals.tolist(),
        "residuals": residuals,
    }
    series["by_target"] = {
        field: {
            "origin_indices": origins,
            "dates": [rows[origin + (1 if horizon == "next_day" else i + 1)]["time"] for origin in origins],
            "predicted": predicted_matrix[:, i].tolist(),
            "actual": actual_matrix[:, i].tolist(),
            "residuals": (actual_matrix[:, i] - predicted_matrix[:, i]).tolist(),
        }
        for i, field in enumerate(fields)
    }
    return (
        metric,
        np.array(target_errors),
        series,
    )


def diagnose(series, rows):
    residuals = np.array(series["residuals"])
    nlags = min(10, len(residuals) // 2 - 1)
    result = {
        **series,
        "acf": [],
        "pacf": [],
        "ljung_box_pvalue": None,
        "arch_pvalue": None,
        "notes": "以下是样本外预测误差诊断；p>0.05仅表示未拒绝原假设，不证明可靠。长周期误差重叠，Ljung-Box/ARCH仅作探索性诊断。",
        "skew": float(skew(residuals)) if np.std(residuals) > 0 else 0.0,
        "kurtosis": float(kurtosis(residuals)) if np.std(residuals) > 0 else 0.0,
    }
    origins = series.get("origin_indices", [])
    contiguous = not origins or np.all(np.diff(origins) == 1)
    if not contiguous:
        result["notes"] += "各折间有多步标签隔离缺口，不对拼接残差计算日频ACF/PACF及Ljung-Box/ARCH检验。"
    if contiguous and np.std(residuals) > 1e-12 and nlags > 0:
        result["acf"] = acf(residuals, nlags=nlags, fft=False).tolist()
        try:
            result["pacf"] = pacf(residuals, nlags=nlags, method="ywm").tolist()
            result["ljung_box_pvalue"] = float(
                acorr_ljungbox(residuals, lags=[nlags], return_df=True).lb_pvalue.iloc[0]
            )
            result["arch_pvalue"] = float(het_arch(residuals, nlags=min(3, nlags))[1])
        except (ValueError, np.linalg.LinAlgError):
            pass
    log_returns = np.diff(np.log([r["close"] for r in rows]))
    # Descriptive only: computed on fitting history, not appended test data.
    spectrum = np.abs(np.fft.rfft(log_returns - log_returns.mean())) ** 2
    frequencies = np.fft.rfftfreq(len(log_returns))
    indices = np.argsort(spectrum[1:])[-5:] + 1
    result["frequency_peaks"] = [
        {"period_trading_days": float(1 / frequencies[i]), "power": float(spectrum[i])} for i in indices[::-1]
    ]
    if "by_target" in series:
        result["by_target"] = {key: diagnose(value, rows) for key, value in series["by_target"].items()}
    return result


def save_artifact(artifact, model_id):
    path = DATA_DIR / "models" / f"{model_id}.joblib"
    joblib.dump(artifact, path)
    return str(path.relative_to(DATA_DIR)), hashlib.sha256(path.read_bytes()).hexdigest()


def load_artifact(model):
    path = (DATA_DIR / model.artifact_path).resolve()
    if (
        not path.is_relative_to(DATA_DIR / "models")
        or hashlib.sha256(path.read_bytes()).hexdigest() != model.artifact_checksum
    ):
        raise DomainError("模型文件校验失败", "artifact_corrupt", 409)
    return joblib.load(path)


def nominate_families(family_best, mode):
    ordered = sorted([f for f in family_best if f != "naive"], key=lambda f: family_best[f]["score"])
    return ordered if mode in ("research", "cross_validation") else ordered[:3]


def train_job(job_id):
    from .assessment import assess_models, CHECK_LABELS
    from .cross_validation import fold_boundaries, validate_family

    with SessionLocal() as db:
        job = db.get(Job, job_id)
        run = db.get(TrainingRun, job.result["run_id"])
        snapshot = db.get(Snapshot, run.snapshot_id)
        rows = load_snapshot(snapshot)
        config = run.config
        run_id, stock_id, basis, snapshot_id = run.id, run.stock_id, snapshot.price_basis, snapshot.id
    started = time.monotonic()
    deadline = started + config["time_budget_seconds"]
    mode = config.get("evaluation_mode", "holdout")
    samples = config.get("evaluation_samples", 128)
    features = feature_matrix(rows)
    n = len(rows)
    train_end, val_end, cal_end, test_end = int(n * 0.5) - 1, int(n * 0.7) - 1, int(n * 0.85) - 1, n - 1
    report = {
        "version": VERSION,
        "evaluation_mode": mode,
        "evaluation_note": "最后15%历史数据进行三折时序验证；匹配度达标可入选，可重复训练，不要求额外独立数据"
        if mode == "cross_validation"
        else "可反复调参，评估结果仅供研究，不再视作独立测试"
        if mode == "research"
        else "本数据版本该周期只允许一次最终测试；通过门槛不等于可靠性证明",
        "provenance": provenance(),
        "policy": {
            "min_improvement": config["min_improvement"],
            "min_coverage": config["coverage"] - 0.15,
            "max_width_ratio": 1.25,
            "max_selected": 3,
            "match_threshold": config.get("match_threshold", 0.95),
            "acceptance_basis": "price_match" if mode == "cross_validation" else "error_and_interval_checks",
        },
        "split": {
            "train_end": rows[train_end]["time"],
            "validation_end": rows[val_end]["time"],
            "calibration_end": rows[cal_end]["time"],
            "test_end": rows[test_end]["time"],
            "final_validation_start": rows[cal_end + 1]["time"],
            "final_validation_fraction": 0.15,
        },
        "horizons": {},
        "limits": [
            "20/60交易日为实验跨度，不等同精确自然月",
            "区间为边际经验校准，存在时序依赖",
            "首版冻结拟合参数，不在验收后用测试数据重拟合",
        ],
    }
    with SessionLocal() as db:
        db.get(TrainingRun, run_id).report = report
        db.commit()
    staged_models = []
    staged_trials = []
    completed_horizons = []
    for horizon in dict.fromkeys(config["horizons"]):
        steps = STEPS[horizon]
        middle = (train_end + val_end) // 2
        if mode == "cross_validation":
            try:
                fold_boundaries(n, steps)
            except ValueError as exc:
                report["horizons"][horizon] = {"status": "insufficient_data", "message": str(exc)}
                continue
        if (
            min(middle - train_end, val_end - middle, cal_end - val_end, test_end - cal_end) < steps + 8
            or train_end - steps < 100
        ):
            report["horizons"][horizon] = {
                "status": "insufficient_data",
                "message": f"当前{n}条日线不足以隔离验证、校准及{steps}步测试窗口；建议增加多年历史",
            }
            continue
        family_best = {}
        trial_count = 0
        exhausted = False
        families = ["naive", *dict.fromkeys(f for f in config["families"] if f != "naive")]
        # Round-robin search ensures a small budget still compares model families.
        grids = {f: candidates(f, config) for f in families}
        proposals = [
            (family, parameters)
            for index in range(max(len(grids[f]) for f in families))
            for family in families
            for parameters in grids[family][index : index + 1]
        ]
        family_counts = {f: 0 for f in families}
        for family, parameters in proposals:
            if family != "naive":
                cap = config.get("trials_per_family")
                if (cap and family_counts[family] >= cap) or (
                    not cap and trial_count >= config["max_trials"]
                ):
                    continue
            checkpoint(job_id, f"调参 {horizon} · {family}", deadline=None)
            try:
                checkpoint(job_id, f"调参 {horizon} · {family}", deadline=deadline)
                windows = []
                for fit_end, evaluate_end in ((train_end, middle), (middle, val_end)):
                    artifact = fit(rows, features, fit_end, horizon, family, parameters, config["seed"])
                    metric, _, _ = evaluate(
                        artifact, rows, features, fit_end + 1, evaluate_end, job_id, deadline
                    )
                    windows.append(metric)
                score = float(np.mean([m["mae"] for m in windows]))
                trial_report = {"validation_windows": windows, "validation_mae": score}
                if family not in family_best or score < family_best[family]["score"]:
                    family_best[family] = {"parameters": parameters, "score": score, "windows": windows}
                status = "succeeded"
            except BudgetExceeded:
                report["budget_exhausted"] = True
                exhausted = True
                break
            except Cancelled:
                raise
            except Exception as exc:
                status, trial_report = "failed", {"error": f"{type(exc).__name__}: {str(exc)[:300]}"}
            with SessionLocal() as db:
                db.add(
                    Trial(
                        run_id=run_id,
                        horizon=horizon,
                        family=family,
                        parameters=parameters,
                        status=status,
                        report=trial_report,
                    )
                )
                db.commit()
            if family != "naive":
                trial_count += 1
                family_counts[family] += 1
        if exhausted:
            report["horizons"][horizon] = {
                "status": "budget_exhausted",
                "message": "预算用尽，本周期未完成交叉验证"
                if mode == "cross_validation"
                else "预算用尽，本周期未进行最终验收",
            }
            break
        if "naive" not in family_best:
            report["horizons"][horizon] = {"status": "failed", "message": "基准评估失败"}
            continue
        base_score = family_best["naive"]["score"]
        nominees = nominate_families(family_best, mode)
        # Nominees are fixed BEFORE looking at the final test.
        results = {}
        local_models = []
        for family in ["naive", *nominees]:
            settings = family_best[family]
            try:
                checkpoint(job_id, f"校准及验收 {horizon} · {family}", deadline=deadline)
                report["test_started_horizons"] = list(
                    set(report.get("test_started_horizons", []) + [horizon])
                )
                with SessionLocal() as db:
                    db.get(TrainingRun, run_id).report = report
                    db.commit()
                cv_details = None
                if mode == "cross_validation":
                    artifact, test_metric, series, cv_details, calibration_count = validate_family(
                        rows, features, horizon, family, settings, config, job_id, deadline
                    )
                else:
                    artifact = fit(
                        rows, features, val_end, horizon, family, settings["parameters"], config["seed"]
                    )
                    artifact["coverage"] = config["coverage"]
                    _, cal_errors, _ = evaluate(
                        artifact, rows, features, val_end + 1, cal_end, job_id, deadline, max_samples=samples
                    )
                    quantile = min(1.0, np.ceil((len(cal_errors) + 1) * config["coverage"]) / len(cal_errors))
                    width = np.quantile(np.abs(cal_errors), quantile, axis=0, method="higher")
                    artifact["width"] = width
                    calibration_count = len(cal_errors)
                    test_metric, _, series = evaluate(
                        artifact,
                        rows,
                        features,
                        cal_end + 1,
                        test_end,
                        job_id,
                        deadline,
                        width,
                        max_samples=samples,
                    )
                metrics = {
                    "evaluation_mode": mode,
                    "validation": {"mae": settings["score"], "windows": settings["windows"]},
                    "test": test_metric,
                    "nominal_coverage": config["coverage"],
                    "calibration_samples": calibration_count,
                    "fitted_through": rows[artifact["fit_end"]]["time"],
                    "steps": steps,
                }
                if cv_details:
                    metrics["cross_validation"] = cv_details
                model_id = uid()
                path, checksum = save_artifact(artifact, model_id)
                model = Model(
                    id=model_id,
                    run_id=run_id,
                    stock_id=stock_id,
                    snapshot_id=snapshot_id,
                    family=family,
                    horizon=horizon,
                    price_basis=basis,
                    selected=False,
                    parameters=settings["parameters"],
                    metrics=metrics,
                    diagnostics=diagnose(series, rows[: artifact["fit_end"] + 1]),
                    artifact_path=path,
                    artifact_checksum=checksum,
                    reason="基准仅用于比较" if family == "naive" else "等待验收",
                )
                local_models.append(model)
                results[family] = model
            except (BudgetExceeded, Cancelled):
                raise
            except Exception as exc:
                staged_trials.append(
                    Trial(
                        run_id=run_id,
                        horizon=horizon,
                        family=family,
                        parameters=settings["parameters"],
                        status="failed",
                        report={"phase": "acceptance", "error": str(exc)[:300]},
                    )
                )
        if "naive" not in results:
            report["horizons"][horizon] = {"status": "failed", "message": "基准验收失败"}
            continue
        base = results["naive"].metrics["test"]
        for family, model in results.items():
            if family == "naive":
                continue
            metric = model.metrics["test"]
            improvement = relative_improvement(metric["mae"], base["mae"])
            val_improvement = relative_improvement(family_best[family]["score"], base_score)
            stable = all(
                window["mae"] <= baseline["mae"] * 1.1
                for window, baseline in zip(family_best[family]["windows"], family_best["naive"]["windows"])
            )
            checks = {
                "validation_improvement": val_improvement >= config["min_improvement"],
                "test_improvement": improvement >= config["min_improvement"],
                "stable_windows": stable,
                "coverage": metric["coverage"] >= config["coverage"] - 0.15,
                "interval_width": metric["mean_width"] <= max(base["mean_width"] * 1.25, 1e-10),
            }
            model.metrics = {
                **model.metrics,
                "improvement": improvement,
                "validation_improvement": val_improvement,
                "acceptance_checks": checks,
                "passes_thresholds": all(checks.values()),
            }
            if mode == "cross_validation":
                matches = metric["price_match"] >= config["match_threshold"]
                model.metrics = {
                    **model.metrics,
                    "reference_checks": checks,
                    "acceptance_checks": {"price_match": matches},
                    "passes_thresholds": matches,
                }
            model.selected = mode == "holdout" and all(checks.values())
            model.reason = (
                "通过预设验收规则；不代表已证明可靠"
                if model.selected
                else ("研究模型；" if mode == "research" else "")
                + (
                    "达到误差门槛，仍需新的独立数据验收"
                    if all(checks.values())
                    else "未通过："
                    + "、".join(CHECK_LABELS[key] for key, passed in checks.items() if not passed)
                )
            )
        if mode == "cross_validation":
            qualified = sorted(
                [m for m in local_models if m.family != "naive" and m.metrics["passes_thresholds"]],
                key=lambda m: (-m.metrics["test"]["price_match"], m.metrics["test"]["mae"], m.family),
            )
            for model in local_models:
                if model.family == "naive":
                    continue
                model.selected = model in qualified[:3]
                model.reason = (
                    "通过15%历史数据时序交叉验证；匹配度达标，已入选预测"
                    if model.selected
                    else "交叉验证通过；本周期保留匹配度最高的三个模型"
                    if model.metrics["passes_thresholds"]
                    else "未通过：15%交叉验证价格匹配度低于设定门槛"
                )
        evidence = assess_models(local_models, rows, steps)
        for model in local_models:
            model.metrics = {**model.metrics, "assessment": evidence[model.id]}
        staged_models.extend(local_models)
        completed_horizons.append(horizon)
        report["horizons"][horizon] = {
            "status": "completed",
            "selected": [m.id for m in local_models if m.selected],
            "nominees": nominees,
            "trials": trial_count,
            "trials_by_family": family_counts,
            "message": "15%时序交叉验证完成"
            if mode == "cross_validation"
            else "研究实验完成；模型仅供研究选择"
            if mode == "research"
            else "没有模型达标"
            if not any(m.selected for m in local_models)
            else "模型验收完成",
        }
    checkpoint(job_id, "保存实验、模型与验收结果", 95)
    report["elapsed_seconds"] = round(time.monotonic() - started, 2)
    with SessionLocal() as db:
        for horizon in completed_horizons if mode in ("holdout", "cross_validation") else []:
            for old in db.scalars(
                select(Model).where(
                    Model.stock_id == stock_id,
                    Model.horizon == horizon,
                    Model.price_basis == basis,
                    Model.selected.is_(True),
                )
            ):
                old.selected = False
        db.add_all(staged_trials)
        db.add_all(staged_models)
        db.get(TrainingRun, run_id).report = report
        db.commit()


def prediction_job(job_id):
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        prediction = db.get(Prediction, job.result["prediction_id"])
        snapshot = db.get(Snapshot, prediction.snapshot_id)
        rows = load_snapshot(snapshot)
        features = feature_matrix(rows)
        dates = future_dates(db, snapshot.cutoff, STEPS[prediction.horizon])
        config = prediction.config
        models = [db.get(Model, m) for m in config["model_ids"]]
        identifier, horizon, cutoff = prediction.id, prediction.horizon, snapshot.cutoff
    result_models = []
    reference = config.get("reference_price") or rows[-1]["close"]
    for model in models:
        checkpoint(job_id, f"预测 {model.family}")
        artifact = load_artifact(model)
        vector = forecast(artifact, rows, features, len(rows) - 1)
        price = encode_prices(vector, rows[-1]["close"], horizon)
        low, high = interval_prices(
            vector,
            artifact["width"] * volatility_scale(artifact, rows, len(rows) - 1),
            rows[-1]["close"],
            horizon,
        )
        if horizon == "next_day":
            candle = {field: float(value) for field, value in zip(["open", "high", "low", "close"], price)}
            points = [
                {
                    "date": dates[0],
                    "estimate": candle["close"],
                    "lower": float(low[-1]),
                    "upper": float(high[-1]),
                }
            ]
            ohlc_intervals = {
                field: {"lower": float(lower_value), "upper": float(upper_value)}
                for field, lower_value, upper_value in zip(["open", "high", "low", "close"], low, high)
            }
        else:
            candle = None
            ohlc_intervals = None
            points = [
                {"date": day, "estimate": float(p), "lower": float(lower_value), "upper": float(upper_value)}
                for day, p, lower_value, upper_value in zip(dates, price, low, high)
            ]
        result_models.append(
            {
                "model_id": model.id,
                "family": model.family,
                "selected_at_prediction": model.selected,
                "evaluation_mode": model.metrics.get("evaluation_mode", "holdout"),
                "fitted_through": model.metrics["fitted_through"],
                "experimental": not model.selected or model.metrics.get("evaluation_mode") == "research",
                "points": points,
                "candle": candle,
                "ohlc_intervals": ohlc_intervals,
                "nominal_coverage": artifact["coverage"],
                "historical_coverage": model.metrics["test"]["coverage"],
                "validation_price_match": model.metrics["test"].get("price_match"),
                "interval_type": "marginal_empirical",
                "target_upside": {
                    "lower": float(low[-1] / reference - 1),
                    "median": float(price[-1] / reference - 1),
                    "upper": float(high[-1] / reference - 1),
                },
                "state_method": "fixed-parameters-current-input",
                "artifact_checksum": model.artifact_checksum,
            }
        )
    # Freeze calendar dates and input history into this result; later revisions cannot alter it.
    result = {
        "data_cutoff": cutoff,
        "price_basis": snapshot.price_basis,
        "generated_at": now(),
        "horizon": horizon,
        "steps": STEPS[horizon],
        "validation_horizon": STEPS[horizon],
        "reference_price": reference,
        "reference_source": "manual" if config.get("reference_price") else "snapshot_close",
        "models": result_models,
        "experimental": any(m["experimental"] for m in result_models),
        "history": rows[-120:],
        "strategy": {
            "status": "not_validated",
            "message": "参考买卖区间尚未通过策略验证，仅展示价格预测区间",
        },
        "holding": {
            "observation_start": config.get("observation_start"),
            "max_months": 7,
            "message": "最长7个月采用逐月复核；当前模型不支持直接7个月预测",
        },
        "warnings": [
            "价格区间不代表期间最高价或可执行买卖点",
            "边际区间不是全路径覆盖保证",
            "模型使用冻结参数和最新因果输入",
        ],
    }
    checkpoint(job_id, "保存预测结果", 95)
    with SessionLocal() as db:
        prediction = db.get(Prediction, identifier)
        prediction.result = result
        db.commit()
    if config["with_ai"]:
        from .integrations import analyze_prediction

        analyze_prediction(identifier)
