from importlib.metadata import PackageNotFoundError

from backend.app import model_catalog
from backend.app.research import nominate_families


def test_catalog_distinguishes_dependency_availability_from_training(client, monkeypatch):
    def installed(package):
        if package in ("numpy", "statsmodels", "scikit-learn"):
            return "test-only"
        raise PackageNotFoundError(package)

    monkeypatch.setattr(model_catalog, "version", installed)
    response = client.get("/api/v1/model-families")
    assert response.status_code == 200
    entries = {item["id"]: item for item in response.json()}
    assert len(entries) == 8  # seven model types and a comparison baseline
    assert entries["arima"]["available"] is True
    assert entries["lightgbm"]["available"] is False
    assert entries["garch"]["missing_dependencies"] == ["arch"]
    assert entries["patchtst"]["missing_dependencies"] == ["neuralforecast"]


def test_research_can_archive_more_than_three_types_without_expanding_holdout_candidates():
    # Scores are test fixtures, not an assertion about these algorithms' real performance.
    scores = {"naive": 0.1, "ridge": 0.6, "arima": 0.2, "garch": 0.7, "lightgbm": 0.3, "sarima": 0.4}
    winners = {name: {"score": score} for name, score in scores.items()}
    assert nominate_families(winners, "holdout") == ["arima", "lightgbm", "sarima"]
    assert nominate_families(winners, "research") == ["arima", "lightgbm", "sarima", "ridge", "garch"]
    assert nominate_families(winners, "cross_validation") == ["arima", "lightgbm", "sarima", "ridge", "garch"]
