from copy import deepcopy
from decimal import Decimal as D
import gzip
import json
from pathlib import Path

from app.domain.method_observations import descriptive_stats, observe_methods, method_targets, portfolio_risk
from app.domain.research_calendar import reference_sessions
from app.research_methods.engine.api import evaluate

ROOT = Path(__file__).resolve().parents[2] / "src/backend/app/research_methods"
NOW = "2026-10-21T06:00:00+00:00"


def observation_fixture():
    dates = [d for d in reference_sessions() if d <= "2026-10-20"]
    funds, datasets = [], {}
    for id, code, rate in [("drug", "012738", "0.0005"), ("gold", "008987", "0.001"), ("semi", "019633", "0.0008")]:
        rows = [[d, "1", str(D("100") * (1 + D(rate)) ** index)] for index, d in enumerate(dates)]
        funds.append({"id": id, "fund_code": code, "share_class": "C", "units": "1000", "market_value": "1000",
                      "nav": {"value": "1", "effective_date": "2026-10-20"},
                      "position_buckets": {"groups": {"BASE": {"units": "100"}}}})
        datasets[code] = {"rows": rows, "observed_at": NOW}
    datasets["cash"] = {"rows": [[d, str(D("100") * D("1.00001") ** i)] for i, d in enumerate(dates)], "observed_at": NOW}
    return {"funds": funds, "now": NOW, "business_date": "2026-10-21"}, datasets, {"level": "OBSERVE", "target_weights": {}, "cash_budget": None}


def test_frozen_engine_and_bundled_contracts_match_original_golden_result():
    raw = json.loads(gzip.decompress((ROOT / "examples/synthetic_snapshot.json.gz").read_bytes()))
    expected = json.loads(gzip.decompress((ROOT / "examples/synthetic_expected.json.gz").read_bytes()))
    config = json.loads((ROOT / "config/methods.json").read_text())
    assert evaluate(raw, config) == expected


def test_short_statistics_and_real_covariance_are_not_weighted_individual_volatilities():
    assert descriptive_stats([])["return_20"] is None
    assert descriptive_stats([D("1"), D("2")])["drawdown_252"] is None
    sessions = reference_sessions()[:61]
    dates = sessions
    series = {"a": {}, "b": {}}
    a, b = D("100"), D("100")
    for i, d in enumerate(dates):
        if i:
            a *= 1 + (D(".01") if i % 2 else D("-.01"))
            b *= 1 + (D("-.01") if i % 2 else D(".01"))
        series["a"][d], series["b"][d] = a, b
    funds = [{"id": id, "units": "1", "market_value": "100", "nav": {"effective_date": dates[-1]}} for id in ["a", "b"]]
    result = portfolio_risk(funds, series, sessions, sessions, D("200"))
    assert result["status"] == "READY" and result["annualized_volatility"] < D("1e-20")


def test_budget_free_observation_uses_completed_month_and_whole_candidate_pool():
    snapshot, datasets, policy = observation_fixture()
    result = observe_methods(snapshot, datasets, policy, ["TREND", "VALUE", "REBALANCE"])
    assert result["trend_anchor"] == "2026-09-30"
    assert result["methods"]["TREND"]["status"] == "OBSERVATION_READY"
    selected = [r["fund_id"] for r in result["methods"]["TREND"]["items"] if r["selected"]]
    assert set(selected) == {"gold", "semi"}
    assert result["methods"]["VALUE"]["items"][1]["status"] == "NOT_APPLICABLE"
    assert result["methods"]["REBALANCE"]["status"] == "WAIT"
    assert result["equity_reference"] == D("3000")  # Registered-fund scope; no wallet assertion.
    datasets["019633"]["rows"] = [r for r in datasets["019633"]["rows"] if r[0] != "2026-09-29"]
    missing = observe_methods(snapshot, datasets, policy, ["TREND"])
    assert missing["methods"]["TREND"]["status"] == "WAIT"
    assert all(r.get("selected") is None for r in missing["methods"]["TREND"]["items"])


def test_future_evidence_and_month_turn_do_not_enter_current_signal():
    snapshot, datasets, policy = observation_fixture()
    datasets["008987"]["observed_at"] = "2026-10-21T08:00:00+00:00"
    result = observe_methods(snapshot, datasets, policy, ["TREND"])
    assert next(r for r in result["funds"] if r["fund_id"] == "gold")["history_count"] == 0
    snapshot["now"], snapshot["business_date"] = "2026-11-01T02:00:00+00:00", "2026-11-01"
    # The last disclosed decision remains October: do not use October as a prior month.
    assert observe_methods(snapshot, datasets, policy, ["TREND"])["trend_anchor"] == "2026-09-30"


def test_target_comparison_reuses_generators_and_protects_base_without_amounts():
    snapshot, datasets, policy = observation_fixture()
    config = json.loads((ROOT / "config/methods.json").read_text())
    level = {"volatility_limit": ".16", "band": ".04"}
    assert method_targets(snapshot, datasets, policy, ["TREND"], config, level) is None
    policy.update(level="BALANCED", target_weights={"drug": ".3", "gold": ".3", "semi": ".4"})
    result = method_targets(snapshot, datasets, policy, ["TREND", "REBALANCE"], config, level)
    assert result["execution_enabled"] is False
    for method in result["methods"].values():
        assert method["analysis_status"] == "READY"
        assert all(row["gross_reference"] is None for row in method["items"])
        assert all(row["target_weight"] >= D("100") / D("3000") for row in method["items"])
    # Classes/markets are evaluated by the frozen applicability matrix, not editable sidebar tags.
    snapshot["funds"][0]["fund_code"] = "012183"
    result = method_targets(snapshot, datasets, policy, ["TREND"], config, level)
    assert result["methods"]["TREND"]["features"]["drug"]["applicability"] == "NOT_APPLICABLE"


def test_duplicate_exposure_is_not_ranked_twice_and_wrong_share_class_gets_no_c_history():
    snapshot, datasets, policy = observation_fixture()
    snapshot["funds"][1]["fund_code"] = "012738"
    result = observe_methods(snapshot, datasets, policy, ["TREND"])
    assert result["methods"]["TREND"]["status"] == "WAIT"
    assert not any(row.get("selected") for row in result["methods"]["TREND"]["items"])
    snapshot["funds"][0]["share_class"] = "A"
    result = observe_methods(snapshot, datasets, policy, ["TREND"])
    assert result["funds"][0]["history_count"] == 0
