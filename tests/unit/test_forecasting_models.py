from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal as D, localcontext
import json

import pytest

from app.domain.forecasting import ENGINE_VERSION, fit_forecast
from app.persistence.store import pack


def history(count=600, returns=None):
    rows, on, level = [], date(2023, 1, 2), D("100")
    with localcontext() as context:
        context.prec = 36
        for index in range(count):
            if index:
                move = returns(index) if returns else D(".002")
                level *= 1 + move
            while on.weekday() >= 5:
                on += timedelta(days=1)
            rows.append([on.isoformat(), format((level / 100).quantize(D(".000001")), "f"),
                         format(level.quantize(D(".0000000000000001")), "f")])
            on += timedelta(days=1)
    return rows


@pytest.mark.parametrize("horizon,minimum", [(1, 430), (5, 441)])
def test_segments_have_100_targets_purged_boundaries_and_known_training_prefix(horizon, minimum):
    rows = history(minimum)
    result = fit_forecast(rows, horizon)
    assert result["status"] == "READY" and result["engine_version"] == ENGINE_VERSION
    assert result["config"]["required_points_for_horizon"] == minimum
    segments = result["config"]["segments"]
    for label in ("validation", "calibration", "test"):
        segment = segments[label]
        assert segment["target_end_index_exclusive"] - segment["target_start_index"] == 100
        assert result[label]["start"] == rows[segment["target_start_index"]][0]
        assert result[label]["end"] == rows[segment["target_end_index_exclusive"] - 1][0]
    assert segments["calibration"]["target_start_index"] - segments["validation"]["target_end_index_exclusive"] == horizon
    assert segments["test"]["target_start_index"] - segments["calibration"]["target_end_index_exclusive"] == horizon
    first_origin = segments["validation"]["target_start_index"] - horizon
    assert result["history"]["train"]["end"] == rows[first_origin][0]
    assert result["history"]["train"]["return_count"] >= 126
    assert result["calibration"]["count"] == 100 // horizon
    assert result["test"]["nonoverlap_count"] == 100 // horizon
    assert result["test"]["overlapping_targets"] == (horizon > 1)
    for i, fold in enumerate(result["folds"], start=segments["test"]["target_start_index"]):
        assert fold["origin_date"] == rows[i - horizon][0]
        with localcontext() as context:
            context.prec = 36
            expected = (D(rows[i][2]) / D(rows[i - horizon][2]) - 1).quantize(D(".000000000001"))
        assert fold["actual_return"] == expected


@pytest.mark.parametrize("horizon", [1, 5])
def test_future_test_change_does_not_change_selection_calibration_or_prior_folds(horizon):
    rows = history()
    first = fit_forecast(rows, horizon)
    changed = deepcopy(rows)
    for i in range(len(changed) - 10, len(changed)):
        changed[i][2] = format(D(changed[i][2]) * D(".8"), "f")
    second = fit_forecast(changed, horizon)
    assert first["selected_model"] == second["selected_model"]
    assert first["validation"] == second["validation"] and first["calibration"] == second["calibration"]
    assert first["folds"][:90] == second["folds"][:90]
    assert first["test"] != second["test"]


def test_validation_selects_candidate_and_final_test_does_not_fallback():
    # Fixed positive validation/calibration, followed by a flat untouched test.
    result = fit_forecast(history(600, lambda i: D(".002") if i < 500 else D("0")), 1)
    assert result["selected_model"] == "AR1_RIDGE120"
    assert result["validation"]["improvement"] >= D(".02")
    assert result["test"]["baseline_mae"] == 0 and result["test"]["mae"] > 0
    assert result["test"]["improvement"] is None
    assert result["test"]["improved_over_baseline"] is False
    assert result["test"]["direction_accuracy"] is None and result["test"]["actual_flat_count"] == 100
    assert result["selected_model"] != "ZERO"


@pytest.mark.parametrize("horizon", [1, 5])
def test_constant_total_return_selects_zero_and_does_not_call_flat_up(horizon):
    result = fit_forecast(history(600, lambda _: D("0")), horizon)
    assert result["selected_model"] == "ZERO" and result["forecast_return"] == 0
    assert result["validation"]["selected_mae"] == result["test"]["mae"] == result["test"]["rmse"] == 0
    assert result["lower_return"] == result["upper_return"] == 0
    assert result["test"]["direction_accuracy"] is None and result["test"]["direction_count"] == 0
    assert result["test"]["forecast_flat_count"] == 100
    assert result["test"]["coverage"] == 1 and result["test"]["interval_width"] == 0
    assert "probability" not in pack(result)


def test_decimal_inputs_outputs_and_packable_precision():
    rows = history()
    for row in rows:
        row[1], row[2] = D(row[1]), D(row[2])
    before = deepcopy(rows)
    result = fit_forecast(rows, 5)
    assert rows == before
    assert isinstance(result["forecast_return"], D) and isinstance(result["test"]["rmse"], D)
    assert result["forecast_return"].as_tuple().exponent >= -12
    encoded = json.loads(pack(result))
    assert encoded["status"] == "READY" and encoded["horizon"] == 5
    assert len(encoded["folds"]) == 100


@pytest.mark.parametrize("horizon,count", [(1, 429), (5, 430), (5, 440)])
def test_insufficient_preserves_contract_and_explains_horizon_floor(horizon, count):
    result = fit_forecast(history(count), horizon)
    assert result["status"] == "INSUFFICIENT"
    assert result["forecast_return"] is None and result["folds"] == []
    assert result["calibration"]["half_width"] is None and result["history"]["count"] == count


@pytest.mark.parametrize("change", [
    lambda rows: rows.__setitem__(10, [rows[10][0], None, rows[10][2]]),
    lambda rows: rows.__setitem__(10, [rows[10][0], rows[10][1], float(rows[10][2])]),
    lambda rows: rows.__setitem__(10, [rows[10][0], rows[10][1], "NaN"]),
    lambda rows: rows.__setitem__(10, [rows[10][0], rows[10][1], "0"]),
    lambda rows: rows.__setitem__(10, rows[9]),
    lambda rows: rows.__setitem__(10, ["2026-99-99", rows[10][1], rows[10][2]]),
    lambda rows: rows.__setitem__(10, ["20230116", rows[10][1], rows[10][2]]),
])
def test_missing_invalid_decimal_duplicate_and_invalid_dates_wait(change):
    rows = history(); change(rows)
    result = fit_forecast(rows, 1)
    assert result["status"] == "INSUFFICIENT" and result["forecast_return"] is None


@pytest.mark.parametrize("horizon", [0, 2, True, "1"])
def test_unsupported_horizon_waits(horizon):
    assert fit_forecast(history(), horizon)["status"] == "INSUFFICIENT"


def test_only_last_1000_points_affect_fit_and_training_is_bounded():
    rows = history(1100)
    full, retained = fit_forecast(rows, 1), fit_forecast(rows[-1000:], 1)
    assert full["history"]["count"] == 1000 and full["history"]["source_count"] == 1100
    assert full["history"]["start"] == rows[-1000][0]
    assert full["history"]["train"]["return_count"] == 252
    assert full["folds"] == retained["folds"]
    assert full["selected_model"] == retained["selected_model"]
