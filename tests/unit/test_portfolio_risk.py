from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal as D

import pytest

from app.domain.market_calendar import market_session
from app.domain.portfolio_risk import portfolio_risk


def case(now="2026-10-23T06:00:00Z"):
    days, d = [], date(2026, 7, 1)
    while d <= date(2026, 10, 22):
        if d.weekday() < 5:
            days.append(d.isoformat())
        d += timedelta(days=1)
    funds = []
    for index, fid in enumerate(("a", "b")):
        value, history = D(1), []
        for n, day in enumerate(days):
            value *= 1 + D((n % 7) - 3) / 1000
            history.append({"id": f"{fid}:{day}", "effective_date": day, "value": str(value),
                            "value_kind": "OFFICIAL_NAV", "quality": "MANAGER_DISCLOSURE", "observed_at": "2026-10-22T12:00:00Z"})
        quote = {"id": f"quote-{fid}", "value_kind": "THIRD_PARTY_ESTIMATE", "effective_date": "2026-10-23",
                 "value": "1.1", "as_of": now, "observed_at": now}
        funds.append({"id": fid, "fund_code": fid, "share_class": "C", "name": f"基金{fid}", "units": "100",
            "history": history, "nav": history[-1], "nav_sync": {"corporate_actions_complete": True, "corporate_actions": []},
            "return_valuation": {"holding_value": "300" if index == 0 else "100", "status": "ESTIMATE", "quote_id": quote["id"],
                "date": "2026-10-23", "label": "实时估算", "is_estimate": True},
            "rules": {"verified": True, "purchase_rate": "0", "unit_step": ".01", "_version": 1},
            "investment_plan_profile": {"enabled": True, "template": "DAILY_LADDER_V1", "_version": 1},
            "daily_t": {"market": market_session(now, "CN"), "today": {"status": "READY", "quote": quote, "baseline": {"id": f"baseline-{fid}"}}}})
    snapshot = {"business_date": "2026-10-23", "now": now, "revision": 7, "funds": funds, "orders": [],
                "fund_sidebar": {"_version": 5, "groups": [{"id": "bank", "name": "我的银行"}, {"id": "tech", "name": "自定科技"}],
                    "funds": [{"fund_id": "a", "group_id": "tech", "display_name": "自选甲", "theme": "PCB"},
                              {"fund_id": "b", "group_id": "bank", "display_name": "自选乙", "theme": "黄金"}]}}
    references = []
    for fund, side in zip(funds, ("BUY", "SELL")):
        references.append({"fund_id": fund["id"], "level": "PLAN", "profile_version": 1,
            "plan": {"template": "DAILY_LADDER_V1"}, "observation_id": fund["daily_t"]["today"]["quote"]["id"],
            "baseline_id": f"baseline-{fund['id']}", "action": side, "status": "REFERENCE", "can_apply": True,
            "amount": "200.00" if side == "BUY" else "110.00", "units": None if side == "BUY" else "100.00",
            "fee": "0.00" if side == "BUY" else "1.00", "net": "109.00" if side == "SELL" else None,
            "sell_impact": {"status": "READY", "expected_realized_pnl": "8.00"} if side == "SELL" else None})
    return snapshot, references


def test_user_categories_values_and_concentration_use_app_holdings_only_without_mutation():
    snapshot, refs = case()
    before = deepcopy((snapshot, refs))
    value = portfolio_risk(snapshot, refs)
    allocation = value["allocation"]
    assert allocation["total_value"] == "400" and allocation["known_subtotal"] == "400"
    assert [group["name"] for group in allocation["groups"]] == ["我的银行", "自定科技"]
    assert allocation["largest_group"] == {"group_id": "tech", "name": "自定科技", "weight": "0.75000000"}
    assert allocation["category_hhi"] == "0.62500000"
    assert [fund["weight"] for fund in allocation["funds"]] == ["0.75000000", "0.25000000"]
    assert "PCB" not in str(allocation) and "黄金" not in str(allocation)  # Theme labels are not asset look-through.
    assert "个人总资产" in value["notice"] and "不是基金真实资产穿透" in value["notice"]
    assert (snapshot, refs) == before


def test_unknown_valuation_is_never_zero_or_a_fabricated_complete_weight():
    snapshot, refs = case()
    snapshot["funds"][1]["return_valuation"]["holding_value"] = None
    allocation = portfolio_risk(snapshot, refs)["allocation"]
    assert allocation["status"] == "PARTIAL" and allocation["total_value"] is None
    assert allocation["known_subtotal"] == "300" and allocation["missing_fund_ids"] == ["b"]
    assert all(group["weight"] is None for group in allocation["groups"])
    assert all(fund["weight"] is None for fund in allocation["funds"])
    assert allocation["largest_group"] is None and allocation["category_hhi"] is None
    assert allocation["groups"][0]["value"] is None


def test_uninitialized_and_unknown_units_remain_missing_even_with_a_visible_value():
    snapshot, refs = case()
    snapshot["funds"][1].update(units="0", initialization_pending=True)
    allocation = portfolio_risk(snapshot, refs)["allocation"]
    assert allocation["holding_count"] == 2 and allocation["missing_fund_ids"] == ["b"]
    snapshot["funds"][1].update(units=None, initialization_pending=False)
    assert portfolio_risk(snapshot, refs)["allocation"]["total_value"] is None


def test_unclassified_empty_and_zero_holdings_have_no_misleading_proportions():
    snapshot, refs = case()
    snapshot["fund_sidebar"]["funds"][1]["group_id"] = "deleted"
    assert portfolio_risk(snapshot, refs)["allocation"]["groups"][-1]["name"] == "未分类"
    for fund in snapshot["funds"]:
        fund["units"] = "0"
    allocation = portfolio_risk(snapshot, refs)["allocation"]
    assert allocation["holding_count"] == 0 and allocation["total_value"] == "0"
    assert allocation["groups"] == [] and allocation["category_hhi"] is None


def test_all_eligible_references_aggregate_existing_amounts_without_cash_requirements():
    snapshot, refs = case()
    operations = portfolio_risk(snapshot, refs)["daily_operations"]
    assert operations["action_count"] == 2 and operations["buy_count"] == operations["sell_count"] == 1
    assert operations["buy_amount"] == "200.00" and operations["sell_gross"] == "110.00"
    assert operations["sell_net"] == "109.00" and operations["net_subscription"] == "91.00"
    assert operations["estimated_fees"] == "1.00" and operations["expires_at"]
    assert operations["actions"][0]["fund_name"] == "自选甲"
    assert "cash" not in operations


@pytest.mark.parametrize("now", ["2026-10-23T03:40:00Z", "2026-10-23T07:00:00Z", "2026-10-23T01:00:00Z"])
def test_lunch_cutoff_and_before_open_references_do_not_enter_operations(now):
    snapshot, refs = case(now)
    operations = portfolio_risk(snapshot, refs)["daily_operations"]
    assert operations["action_count"] == 0 and operations["buy_amount"] == operations["sell_gross"] == "0"
    assert operations["expires_at"] is None


def test_expired_and_changed_reference_are_excluded_individually():
    snapshot, refs = case()
    refs[0]["expires_at"] = snapshot["now"]
    refs[1]["observation_id"] = "old"
    assert portfolio_risk(snapshot, refs)["daily_operations"]["action_count"] == 0


def test_correlation_uses_maximum_60_matching_completed_formal_intervals():
    snapshot, refs = case()
    result = portfolio_risk(snapshot, refs)["correlations"]
    pair = result["pairs"][0]
    assert pair["status"] == "READY" and pair["sample_count"] == 60
    assert pair["correlation"] == "1.00000000" and pair["label"] == "高度同向"
    assert pair["end"] == "2026-10-22" and pair["names"] == ["自选甲", "自选乙"]
    assert pair["basis"] == ["OFFICIAL_NAV_NO_ACTIONS"] * 2
    assert result["history"]["a"]["count"] == 60


def test_correlation_never_pairs_different_holiday_intervals_or_forward_fills():
    snapshot, refs = case()
    snapshot["funds"][1]["history"] = snapshot["funds"][1]["history"][::2]
    snapshot["funds"][1]["nav"] = snapshot["funds"][1]["history"][-1]
    pair = portfolio_risk(snapshot, refs)["correlations"]["pairs"][0]
    assert pair["status"] == "INSUFFICIENT" and pair["sample_count"] == 0 and pair["correlation"] is None


def test_too_short_or_constant_history_has_no_coefficient():
    snapshot, refs = case()
    snapshot["funds"][0]["history"] = snapshot["funds"][0]["history"][-20:]
    pair = portfolio_risk(snapshot, refs)["correlations"]["pairs"][0]
    assert pair["sample_count"] == 19 and pair["correlation"] is None
    snapshot, refs = case()
    for row in snapshot["funds"][0]["history"]:
        row["value"] = "1"
    pair = portfolio_risk(snapshot, refs)["correlations"]["pairs"][0]
    assert pair["status"] == "CONSTANT_SERIES" and pair["correlation"] is None


@pytest.mark.parametrize("change,status", [("incomplete", "ACTIONS_UNVERIFIED"), ("action", "TOTAL_RETURN_REQUIRED"),
                                           ("quality", "HISTORY_INVALID"), ("future", "HISTORY_MISSING")])
def test_unreliable_formal_history_is_explicitly_degraded(change, status):
    snapshot, refs = case()
    fund = snapshot["funds"][0]
    if change == "incomplete": fund["nav_sync"]["corporate_actions_complete"] = False
    elif change == "action": fund["history"][-10]["corporate_action"] = "分红"
    elif change == "quality": fund["history"][-10]["quality"] = "MANUAL"
    elif change == "future":
        # Anchor itself remains visible but every prior history observation is future.
        fund["nav"] = deepcopy(fund["nav"])
        for row in fund["history"]: row["observed_at"] = "2026-10-24T06:00:00Z"
    result = portfolio_risk(snapshot, refs)["correlations"]
    assert result["history"]["a"]["status"] == status
    assert result["pairs"][0]["status"] == "HISTORY_REQUIRED" and result["pairs"][0]["correlation"] is None


def test_verified_total_returns_allow_dividends_without_price_jump_bias():
    snapshot, refs = case()
    data = {}
    for fund in snapshot["funds"]:
        fund["nav_sync"]["corporate_actions_complete"] = False
        data[fund["fund_code"]] = {"fund_code": fund["fund_code"], "series_kind": "FUND_TOTAL_RETURN",
            "corporate_actions_verified": True, "observed_at": snapshot["now"],
            "rows": [[row["effective_date"], row["value"], row["value"]] for row in fund["history"]]}
    result = portfolio_risk(snapshot, refs, data)["correlations"]
    assert result["pairs"][0]["status"] == "READY" and result["pairs"][0]["correlation"] == "1.00000000"
    assert result["pairs"][0]["basis"] == ["VERIFIED_TOTAL_RETURN"] * 2
    data["a"]["corporate_actions_verified"] = False
    assert portfolio_risk(snapshot, refs, data)["correlations"]["pairs"][0]["correlation"] is None


def test_public_nav_action_metadata_is_not_lost_when_observations_have_no_action_field():
    snapshot, refs = case()
    snapshot["funds"][0]["nav_sync"]["corporate_actions"] = [{"effective_date": "2026-10-15", "corporate_action": "分红"}]
    result = portfolio_risk(snapshot, refs)["correlations"]
    assert result["history"]["a"]["status"] == "TOTAL_RETURN_REQUIRED"
    assert result["pairs"][0]["correlation"] is None


def test_snapshot_basis_includes_quote_category_and_valuation_identities():
    snapshot, refs = case()
    value = portfolio_risk(snapshot, refs)
    assert value["sidebar_version"] == 5 and value["revision"] == snapshot["revision"]
    basis = value["snapshot_basis"][0]
    assert basis["fund_id"] == "a" and basis["units"] == "100"
    assert basis["nav_id"] == snapshot["funds"][0]["nav"]["id"]
    assert basis["valuation_quote_id"] == "quote-a" and basis["holding_value"] == "300"


def test_all_money_and_ratios_are_strings_or_null_even_when_reference_is_decimal():
    snapshot, refs = case()
    refs[0]["amount"] = D("200.00")
    value = portfolio_risk(snapshot, refs)
    def visit(item):
        if isinstance(item, dict):
            for child in item.values(): visit(child)
        elif isinstance(item, list):
            for child in item: visit(child)
        else:
            assert not isinstance(item, (float, D))
    visit(value)
