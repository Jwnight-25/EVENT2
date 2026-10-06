"""Installed model families, distinct from trials and saved model versions."""

from importlib.metadata import PackageNotFoundError, version


def model_families():
    specifications = [
        ("naive", "沿用收盘价基准", ["numpy"]),
        ("arima", "ARMA / ARIMA", ["statsmodels"]),
        ("sarima", "SARIMA", ["statsmodels"]),
        ("ridge", "Ridge基础模型", ["scikit-learn"]),
        ("lightgbm", "LightGBM", ["lightgbm"]),
        ("garch", "ARIMA＋GARCH", ["statsmodels", "arch"]),
        ("nhits", "N-HiTS", ["neuralforecast"]),
        ("patchtst", "PatchTST", ["neuralforecast"]),
    ]
    result = []
    for identifier, name, packages in specifications:
        dependencies, missing = {}, []
        for package in packages:
            try:
                dependencies[package] = version(package)
            except PackageNotFoundError:
                missing.append(package)
        result.append(
            {
                "id": identifier,
                "name": name,
                "available": not missing,
                "dependencies": dependencies,
                "missing_dependencies": missing,
                "message": "依赖已安装；仍需实际训练成功" if not missing else "训练依赖未安装",
            }
        )
    return result
