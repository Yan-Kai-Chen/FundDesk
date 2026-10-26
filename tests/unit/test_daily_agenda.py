from copy import deepcopy
from decimal import Decimal as D

import pytest

from app.domain.daily_agenda import daily_agenda
from app.domain.market_calendar import market_session


def case(now="2026-10-23T06:00:00Z", side="SELL"):
    quote = {"id": "quote", "value_kind": "THIRD_PARTY_ESTIMATE", "effective_date": "2026-10-23",
             "value": "1.10", "source": "Synthetic fixture", "as_of": now, "observed_at": now}
    fund = {"id": "f", "fund_code": "synthetic", "name": "合成基金", "sidebar": {"display_name": "用户名称"},
            "investment_plan_profile": {"enabled": True, "_version": 2, "template": "DAILY_LADDER_V1"},
            "rules": {"verified": True, "purchase_rate": ".0015", "unit_step": ".01"},
            "daily_t": {"market": market_session(now, "CN_HK"),
                        "today": {"status": "READY", "quote": quote, "baseline": {"id": "prior"}}}}
    ref = {"fund_id": "f", "level": "PLAN", "profile_version": 2, "plan": {"template": "DAILY_LADDER_V1"},
           "observation_id": "quote", "baseline_id": "prior", "action": side, "status": "REFERENCE",
           "label": "参考卖出" if side == "SELL" else "参考买入", "reason": "已扣减有效申请并核对费用",
           "can_apply": True, "amount": D("123.45"), "units": D("112.23") if side == "SELL" else None,
           "fee": D(".50") if side == "SELL" else None, "net": D("122.95") if side == "SELL" else None,
           "sell_impact": {"status": "READY", "expected_realized_pnl": D("9.27")} if side == "SELL" else None}
    state = {"business_date": "2026-10-23", "now": now, "revision": 42, "funds": [fund], "orders": []}
    return state, [ref]


def test_reuses_reference_values_and_existing_form_target_without_mutation():
    snapshot, refs = case()
    before = deepcopy((snapshot, refs))
    view = daily_agenda(snapshot, refs)
    action = view["actions"][0]
    assert action["fund_name"] == "用户名称"
    assert action["amount"] == refs[0]["amount"] and action["units"] == refs[0]["units"]
    assert action["fee"] == D(".50") and action["expected_realized_pnl"] == D("9.27")
    assert action["expires_at"] == "2026-10-23T06:15:00+00:00"
    assert action["prefill"] == {"fund_id": "f", "side": "SELL", "bucket": "TACTICAL", "requested": "112.23"}
    assert view["summary"]["actionable_count"] == 1 and view["revision"] == 42
    assert (snapshot, refs) == before


def test_buy_shows_existing_fee_calculator_without_inventing_profit_or_amount():
    snapshot, refs = case(side="BUY")
    row = daily_agenda(snapshot, refs)["actions"][0]
    assert row["amount"] == D("123.45") and row["prefill"]["requested"] == "123.45"
    assert row["fee"] == D(".18") and row["expected_realized_pnl"] is None


@pytest.mark.parametrize("now,status", [
    ("2026-10-23T03:30:00Z", "BREAK"),  # Source lunch while HK is open.
    ("2026-10-23T04:15:00Z", "BREAK"),
    ("2026-10-23T07:00:00Z", "CUTOFF"),
    ("2026-10-23T07:30:00Z", "CUTOFF"),  # HK still open after fund cutoff.
    ("2026-10-23T09:00:00Z", "CUTOFF"),
    ("2026-10-23T01:00:00Z", "NOT_OPEN"),
])
def test_paused_closed_and_after_cutoff_never_prepare_an_operation(now, status):
    snapshot, refs = case(now)
    view = daily_agenda(snapshot, refs)
    assert view["actions"] == []
    assert view["funds"][0]["status"] == status and view["funds"][0]["prefill"] is None
    assert view["funds"][0]["amount"] is None  # A scenario is not an executable instruction.


@pytest.mark.parametrize("change", ["quote", "baseline", "profile", "disabled", "expired", "future", "template", "wrong_date", "preview"])
def test_stale_reference_or_preview_cannot_create_an_action(change):
    snapshot, refs = case()
    fund, ref = snapshot["funds"][0], refs[0]
    if change == "quote": ref["observation_id"] = "old"
    elif change == "baseline": ref["baseline_id"] = "old"
    elif change == "profile": ref["profile_version"] = 1
    elif change == "disabled": fund["investment_plan_profile"]["enabled"] = False
    elif change == "expired": ref["expires_at"] = snapshot["now"]
    elif change == "future": ref["generated_at"] = "2026-10-23T06:00:01Z"
    elif change == "template": ref["plan"]["template"] = "DRAWDOWN_GUARD_V2"
    elif change == "wrong_date": fund["daily_t"]["market"]["date"] = "2026-10-22"
    elif change == "preview": ref["preview_only"] = True
    assert daily_agenda(snapshot, refs)["actions"] == []


def test_normal_waiting_is_separate_from_missing_order_information_and_closed_excluded():
    snapshot, refs = case()
    for status in ("WAIT_DATE", "WAIT_NAV", "READY", "PAUSED", "WAIT_PAYMENT", "WAIT_RULES", "MANUAL"):
        snapshot["orders"].append({"id": status, "fund_id": "f", "side": "BUY", "requested": "200", "closed": False,
                                   "automation": {"status": status, "label": status, "detail": "合成进度"}})
    snapshot["orders"].append({"id": "closed", "fund_id": "f", "closed": True, "automation": {"status": "WAIT_RULES"}})
    view = daily_agenda(snapshot, refs)
    assert {r["status"] for r in view["waiting"]} == {"WAIT_DATE", "WAIT_NAV", "READY", "PAUSED"}
    assert {r["status"] for r in view["needs_input"]} == {"WAIT_PAYMENT", "WAIT_RULES", "MANUAL"}
    assert view["summary"]["needs_input_count"] == 3 and view["summary"]["waiting_count"] == 4
    assert all(row["id"] != "order:closed" for row in view["needs_input"])


def test_each_fund_has_a_reason_even_when_not_actionable_or_reference_missing():
    snapshot, refs = case()
    refs[0].update(action="HOLD", status="PROFIT_BUFFER", label="暂不卖出", reason="净收益未达到缓冲", amount="0", can_apply=False)
    snapshot["funds"].append({"id": "other", "name": "其他基金"})
    view = daily_agenda(snapshot, refs)
    assert not view["actions"] and view["funds"][0]["reason"] == "净收益未达到缓冲"
    assert view["funds"][1]["reason"] and view["summary"]["holding_count"] == 2


def test_sale_without_known_net_profit_and_zero_or_nonfinite_size_cannot_prepare_form():
    snapshot, refs = case()
    refs[0]["sell_impact"] = None
    assert daily_agenda(snapshot, refs)["funds"][0]["status"] == "PNL_UNKNOWN"
    for size in ("0", "-1", "NaN", "Infinity", None):
        refs[0]["units"] = size
        assert not daily_agenda(snapshot, refs)["actions"]


def test_ordinary_intensity_reference_remains_supported_without_plan_specific_can_apply():
    snapshot, refs = case(side="BUY")
    fund, ref = snapshot["funds"][0], refs[0]
    fund["investment_plan_profile"]["enabled"] = False
    fund["daily_t"]["profile"] = {"level": "BALANCED", "_version": 4}
    ref.update(level="BALANCED", profile_version=4)
    ref.pop("can_apply")
    assert daily_agenda(snapshot, refs)["actions"][0]["prefill"]["bucket"] == "TACTICAL"


@pytest.mark.parametrize("now,expiry", [
    ("2026-10-23T03:29:00Z", "2026-10-23T11:30:00+08:00"),
    ("2026-10-23T06:59:00Z", "2026-10-23T15:00:00+08:00"),
])
def test_local_action_expiry_stops_at_source_lunch_or_fund_cutoff(now, expiry):
    snapshot, refs = case(now)
    assert daily_agenda(snapshot, refs)["actions"][0]["expires_at"] == expiry


def test_already_stale_quote_cannot_escape_context_freshness_or_missing_plan_permission():
    snapshot, refs = case()
    snapshot["funds"][0]["daily_t"]["today"]["quote"]["as_of"] = "2026-10-23T05:44:59Z"
    assert not daily_agenda(snapshot, refs)["actions"]
    snapshot, refs = case()
    refs[0].pop("can_apply")
    assert not daily_agenda(snapshot, refs)["actions"]


def test_partial_market_holiday_is_explained_instead_of_showing_an_open_action():
    snapshot, refs = case()
    snapshot["funds"][0]["daily_t"]["market"]["partial_holiday"] = True
    view = daily_agenda(snapshot, refs)
    assert not view["actions"]
    assert view["funds"][0]["status"] == "PARTIAL_HOLIDAY" and view["funds"][0]["label"] == "部分市场休市"


def test_snapshot_basis_preserves_quote_and_nonledger_versions_for_cross_get_matching():
    snapshot, refs = case()
    fund = snapshot["funds"][0]
    fund["units"] = D("1234.56")
    fund["rules"]["_version"] = 5
    fund["daily_t"]["profile"] = {"_version": 3}
    view = daily_agenda(snapshot, refs)
    assert view["snapshot_basis"] == [{"fund_id": "f", "units": D("1234.56"), "rule_version": 5,
        "t_profile_version": 3, "investment_plan_version": 2, "quote_id": "quote", "baseline_id": "prior", "market_date": "2026-10-23"}]
    assert snapshot["revision"] == view["revision"] == 42
    fund["daily_t"]["today"]["quote"]["id"] = "fresh-quote-without-ledger-change"
    assert daily_agenda(snapshot, refs)["snapshot_basis"][0]["quote_id"] != view["snapshot_basis"][0]["quote_id"]
