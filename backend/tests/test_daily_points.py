import pytest

from backend.app.daily_points import rank_predictions, price_cents

ACTUAL = {"open": 10.0, "high": 11.0, "low": 9.0, "close": 10.5}


def candidate(index, offset):
    return {
        "task_id": str(index),
        "model_id": str(index),
        "family": str(index),
        "outcome_id": str(index),
        "candle": {f: v + offset for f, v in ACTUAL.items()},
    }


@pytest.mark.parametrize(
    "offsets,expected",
    [
        ([0.01, 0.02, 0.03], [2, 0, -2]),
        ([0, 0.01, 0.02], [5, 0, -2]),
        ([0, 0, 0.03], [5, 5, -2]),
        ([0.01, 0.01, 0.03, 0.03], [2, 2, -2, -2]),
        ([0.02, 0.02, 0.02], [0, 0, 0]),
        ([0, 0], [5, 5]),
        ([0.01], [0]),
        ([0], [5]),
        ([], []),
    ],
)
def test_exact_best_worst_ties_single_and_empty_cohorts(offsets, expected):
    result = rank_predictions([candidate(i, v) for i, v in enumerate(offsets)], ACTUAL)
    assert [e["points"] for e in result["entries"]] == expected
    for entry in result["entries"]:
        assert entry["ranking_mae"] == entry["sum_absolute_error_cents"] / 400


def test_full_correct_requires_all_four_prices_at_cent_precision():
    c = candidate(0, 0.004)
    assert rank_predictions([c], ACTUAL)["entries"][0]["points"] == 5
    c["candle"]["high"] = 11.01
    assert rank_predictions([c], ACTUAL)["entries"][0]["points"] == 0
    assert price_cents("10.005") == 1001
    assert price_cents("10.004999") == 1000


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1, 0])
def test_bad_prices_are_not_rewarded(value):
    with pytest.raises(ValueError):
        price_cents(value)
