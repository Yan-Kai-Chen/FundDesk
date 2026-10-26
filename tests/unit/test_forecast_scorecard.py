"""Persistent forecast scorecard semantics, without refitting or network use."""
from datetime import date, timedelta
from decimal import Decimal as D

import pytest

from app.domain.forecast_scorecard import build_forecast_scorecard, RESULT_BASIS

ENGINE = "frozen-test-v1"
NOW = "2026-10-24T12:00:00Z"
FUNDS = [{"id": "f", "fund_code": "012183", "share_class": "C", "name": "Test fund"}]


def prediction(id="p1", predicted=".01", **kw):
    return {"id": id, "prediction_id": id, "fund_id": "f", "fund_code": "012183", "engine_version": ENGINE,
            "horizon": 1, "status": "READY", "task": "TOTAL_RETURN", "target_existed_at_generation": False,
            "base_date": "2026-10-22", "target_date": "2026-10-23", "generated_at": "2026-10-22T12:00:00Z",
            "dataset_observed_at": "2026-10-22T11:00:00Z", "base_observed_at": "2026-10-22T10:00:00Z",
            "forecast_return": predicted, "lower_return": "-.01", "upper_return": ".03", "selected_model": "SYNTHETIC", **kw}


def resolution(id="p1", actual=".02", **kw):
    return {"id": id, "prediction_id": id, "fund_id": "f", "fund_code": "012183", "engine_version": ENGINE,
            "horizon": 1, "target_date": "2026-10-23", "resolved_at": "2026-10-23T12:00:00Z",
            "actual_first_observed_at": "2026-10-23T11:00:00Z", "research_observed_at": "2026-10-23T11:30:00Z",
            "actual_return": actual, "actual_nav": "1.02", "actual_observation_id": "formal-obs-1", "basis": RESULT_BASIS, **kw}


def score(predictions=(), resolutions=(), funds=FUNDS):
    return build_forecast_scorecard(funds, list(predictions), list(resolutions), now=NOW,
                                   engine_version=ENGINE, supported_codes={"012183"})


def group(result, horizon=1, engine=ENGINE):
    return next(g for g in result["groups"] if g["horizon"] == horizon and g["engine_version"] == engine)


def test_empty_is_unknown_not_perfect_zero_error_and_seven_does_not_get_fake_model():
    result = score(funds=FUNDS + [{"id": "seven", "fund_code": "006810", "share_class": "C", "name": "HK Bank"}])
    one = group(result)
    assert result["status"] == "WAIT_RESULTS" and result["summary"]["resolved"] == 0
    assert one["mae_pp"] is None and one["direction_accuracy"] is None and one["verdict"] == "NO_RESULT"
    assert result["not_in_experiment"][0]["status"] == "NOT_IN_EXPERIMENT"


def test_same_formal_total_return_samples_compare_original_prediction_and_zero_baseline():
    result = score([prediction()], [resolution()])
    one = group(result)
    assert one["mae_pp"] == D(1) and one["baseline_mae_pp"] == D(2)
    assert one["improvement_pp"] == D(1) and one["relative_improvement"] == D(".5")
    assert one["verdict"] == "IMPROVED" and one["direction_accuracy"] == 1
    row = result["recent_resolved"][0]
    assert row["forecast_return"] == D(".01") and row["actual_return"] == D(".02")
    assert row["baseline_return"] == 0 and row["interval_covered"] is True
    assert group(result, 5)["mae_pp"] is None


@pytest.mark.parametrize("predicted,actual,verdict", [("0", ".02", "TIED"), ("-.02", ".02", "WORSE"), ("0", "0", "TIED")])
def test_zero_baseline_ties_negative_predictions_and_flat_actual_are_explicit(predicted, actual, verdict):
    result = score([prediction(predicted=predicted)], [resolution(actual=actual)])
    one = group(result)
    assert one["verdict"] == verdict
    if predicted == "0" or actual == "0":
        assert one["direction_count"] == 0 and one["direction_accuracy"] is None
    if actual == "0":
        assert one["relative_improvement"] is None and one["actual_flat_count"] == 1


def test_pending_forecast_does_not_enter_mae_or_direction_denominator():
    p2 = prediction("p2", base_date="2026-10-23", target_date="2026-10-26", generated_at="2026-10-23T13:00:00Z")
    result = score([prediction(), p2], [resolution()])
    assert group(result)["count"] == 2 and group(result)["pending"] == 1
    assert group(result)["resolved"] == 1 and group(result)["mae_pp"] == 1


@pytest.mark.parametrize("field,value", [("task", "HISTORICAL_REPLAY"), ("target_existed_at_generation", True),
    ("generated_at", "2026-10-24T01:00:00Z"), ("dataset_observed_at", "2026-10-23T01:00:00Z"),
    ("base_observed_at", None), ("forecast_return", None), ("forecast_return", 0.1), ("forecast_return", "NaN"),
    ("horizon", 2), ("horizon", True), ("fund_code", "006810")])
def test_reconstruction_seen_targets_and_missing_provenance_never_become_forward_samples(field, value):
    result = score([prediction(**{field: value})], [resolution()])
    assert result["summary"]["prediction_count"] == 0 and group(result)["mae_pp"] is None
    assert result["excluded"]["not_forward_eligible"] == 1


@pytest.mark.parametrize("field,value", [("basis", "INTRADAY_ESTIMATE"), ("actual_first_observed_at", "2026-10-21T12:00:00Z"),
    ("actual_observation_id", None), ("actual_return", None), ("actual_return", 0.1),
    ("resolved_at", "2026-10-25T12:00:00Z"), ("research_observed_at", "2026-10-24T13:00:00Z"),
    ("fund_code", "006810"), ("horizon", 5)])
def test_invalid_results_remain_unknown_and_do_not_join_count(field, value):
    result = score([prediction()], [resolution(**{field: value})])
    assert group(result)["pending"] == 1 and group(result)["unverified_result_count"] == 1
    assert group(result)["mae_pp"] is None and not result["recent_resolved"]


def test_duplicate_delivery_and_later_origin_revisions_keep_earliest_frozen_issue():
    original = prediction(predicted="-.02")
    # This local-clock string looks earlier lexically but is later in real time.
    revised = prediction("p2", ".02", generated_at="2026-10-22T21:00:00+08:00")
    result = score([revised, original, original], [resolution("p2"), resolution()])
    assert group(result)["resolved"] == 1 and group(result)["mae_pp"] == 4
    assert result["recent_resolved"][0]["prediction_id"] == "p1"
    assert result["excluded"] == {"duplicate_copy": 1, "later_revision": 1}


def test_first_resolution_is_not_replaced_by_later_revision_or_today_recomputed_errors():
    first = resolution(actual=".02", absolute_error_pp="999", baseline_absolute_error_pp="999")
    changed = resolution(actual=".01", resolved_at="2026-10-23T13:00:00Z")
    result = score([prediction()], [changed, first, first])
    assert group(result)["mae_pp"] == 1 and result["recent_resolved"][0]["actual_return"] == D(".02")


def test_versions_and_horizons_stay_separate_history_does_not_inflate_forward_results():
    test = {"count": 100, "mae": ".04", "baseline_mae": ".03", "start": "2026-01-01", "end": "2026-10-22"}
    p = prediction(test=test)
    second = prediction("v2", ".02", engine_version="new-engine")
    result = score([p, second], [resolution(), resolution("v2", engine_version="new-engine")])
    assert result["summary"]["resolved"] == 2
    assert group(result)["resolved"] == 1 and group(result, engine="new-engine")["resolved"] == 1
    assert result["historical_tests"][0]["scope"] == "RETROSPECTIVE_RECONSTRUCTED_FINAL_TEST"
    assert result["historical_tests"][0]["count"] == 100 and result["historical_tests"][0]["mae_pp"] == 4


def test_recent_limit_is_twenty_but_accumulated_mae_uses_all_samples():
    ps, rs = [], []
    for i in range(25):
        base = date(2026, 9, 1) + timedelta(days=i)
        target = base + timedelta(days=1)
        id = f"p{i:02}"
        ps.append(prediction(id, ".01", base_date=base.isoformat(), target_date=target.isoformat(),
                            generated_at=base.isoformat() + "T12:00:00Z", dataset_observed_at=base.isoformat() + "T11:00:00Z",
                            base_observed_at=base.isoformat() + "T10:00:00Z"))
        rs.append(resolution(id, ".02", target_date=target.isoformat(), resolved_at=target.isoformat() + "T12:00:00Z",
                             actual_first_observed_at=target.isoformat() + "T11:00:00Z", research_observed_at=target.isoformat() + "T11:30:00Z"))
    result = score(ps, rs)
    assert result["summary"]["resolved"] == 25 and group(result)["mae_pp"] == 1
    assert len(result["recent_resolved"]) == 20 and len(group(result)["recent_resolved"]) == 20
    assert result["recent_resolved"][0]["prediction_id"] == "p24"
