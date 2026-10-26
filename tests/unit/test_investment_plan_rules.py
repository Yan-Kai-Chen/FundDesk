from copy import deepcopy
from decimal import Decimal as D

import pytest

from app.domain.buckets import BucketBook
from app.domain.investment_plan import investment_plan_reference, METHOD_VERSION
from app.domain.market_calendar import market_session

NOW = "2026-10-20T05:30:00Z"


def fund(change="-.03", now=NOW, t_units="900", t_cost="900", base_units="1100"):
    nav = D("1") + D(change)
    units = D(t_units) + D(base_units)
    book = BucketBook()
    if D(base_units):
        book.buy("base", base_units, base_units, "BASE", "2026-06-01", "2026-06-03")
    if D(t_units):
        book.buy("t", t_units, t_cost, "TACTICAL", "2026-06-01", "2026-06-03")
    return {"id": "synthetic", "units": units, "sellable_units": units,
            "investment_plan_profile": {"enabled": True, "holding_cap": "4000.00", "template": "DAILY_LADDER_V1", "_version": 3},
            "daily_t": {"market": market_session(now, "CN_HK"), "today": {
                "status": "READY", "change": D(change),
                "quote": {"id": "quote", "value": nav, "value_kind": "THIRD_PARTY_ESTIMATE", "source": "synthetic", "effective_date": "2026-10-20"},
                "baseline": {"id": "base-nav", "value": "1"}}},
            "position_buckets": book.summary("1", "2026-10-20"),
            "rules": {"verified": True, "effective_from": "2026-01-01", "lot_method": "FIFO", "unit_step": ".01",
                      "redemption_tiers": [{"min_days": 0, "max_days": 7, "rate": ".015"},
                                           {"min_days": 7, "max_days": 30, "rate": ".005"},
                                           {"min_days": 30, "max_days": None, "rate": "0"}]},
            "lots": [{"id": "physical", "units": units, "reserved": "0", "cost": units,
                      "acquired": "2026-06-01", "available_from": "2026-06-03"}]}


def order(side="BUY", requested="200", closed=False, gross="0", confirmed_units="0", at="2026-10-20T04:00:00Z", bucket="TACTICAL", fid="synthetic"):
    return {"fund_id": fid, "side": side, "bucket": bucket, "requested": requested, "closed": closed,
            "confirmed_gross": gross, "confirmed_units": confirmed_units, "submitted_at": at}


def reference(f=None, orders=None, now=NOW):
    return investment_plan_reference(f or fund(), orders or [], now)


@pytest.mark.parametrize("change,target", [("0", "0"), ("-.009999", "0"), ("-.01", "200"),
                                           ("-.019999", "200"), ("-.02", "400"), ("-.029999", "400"),
                                           ("-.03", "600"), ("-.20", "600")])
def test_buy_ladder_exact_boundaries(change, target):
    result = reference(fund(change))
    assert result["amount"] == D(target)
    assert result["plan"]["target_today"] == D(target)
    assert result["action"] == ("BUY" if D(target) else "HOLD")
    assert result["can_apply"] == bool(D(target))


@pytest.mark.parametrize("change,target", [(".01", "200"), (".02", "400"), (".03", "600")])
def test_sell_ladder_exact_boundaries(change, target):
    # A lower recorded T cost clears the realized-profit buffer at the 1% rung.
    f = fund(change, t_cost="800")
    r = reference(f)
    assert r["action"] == "SELL" and r["can_apply"]
    assert D(target) - D(".02") <= r["amount"] <= D(target)
    assert r["plan"]["target_today"] == D(target)
    assert r["sell_impact"]["base_units"] == 0


def test_disabled_profiles_and_response_contract():
    f = fund(); f["investment_plan_profile"]["enabled"] = False
    assert investment_plan_reference(f, [], NOW) is None
    r = reference()
    assert r["method_version"] == METHOD_VERSION and r["profile_version"] == 3 and r["level"] == "PLAN"
    assert r["observation_id"] == "quote" and r["baseline_id"] == "base-nav"
    assert r["plan"]["base_units"] == 1100 and r["plan"]["base_value"] == D("1067")
    assert r["daily_budget"] == D("600") and r["cash_status"] == "REFERENCE_ONLY"


def test_budget_has_no_cash_requirement_and_no_automatic_rebalancing():
    f = fund("-.03"); f["available_cash"] = D("0")
    assert reference(f)["amount"] == D("600")
    f = fund("-.03", t_units="4000")
    assert reference(f)["status"] == "HOLDING_CAP"
    assert reference(f)["action"] == "HOLD"


def test_buy_pending_including_historical_base_orders_uses_headroom_not_blanket_wait():
    f = fund("-.03", t_units="2900")  # marked confirmed holding = 3880.
    old = order(requested="110", at="2026-10-19T04:00:00Z", bucket="BASE")
    r = reference(f, [old])
    assert r["amount"] == D("10") and r["plan"]["holding_value"] == D("3880")
    assert r["plan"]["pending_buy"] == D("110") and r["plan"]["headroom"] == D("10")
    old["confirmed_gross"] = "10"
    assert reference(f, [old])["amount"] == D("20")  # only the remaining requested amount is pending.


def test_pending_sale_does_not_release_holding_budget_before_confirmation():
    f = fund("-.03", t_units="3100")
    pending_base_sale = order("SELL", "100", bucket="BASE")
    r = reference(f, [pending_base_sale])
    assert r["plan"]["committed_holding"] == D("4074") and r["status"] == "HOLDING_CAP"


def test_same_direction_partial_and_duplicate_refresh_use_cumulative_target():
    f = fund("-.025")
    pending = order(requested="200", gross="50", confirmed_units="51.28")
    original = deepcopy((f, pending))
    a = reference(f, [pending]); b = reference(f, [pending])
    assert a == b and a["amount"] == D("200")
    assert a["plan"]["used_buy"] == D("200")  # partial + remainder, never 250.
    assert (f, pending) == original


def test_cancel_requested_retains_commitment_final_cancellation_only_keeps_fill():
    f = fund("-.025")
    pending = order(requested="200", gross="50", confirmed_units="51.28")
    pending["cancel_requested"] = True
    assert reference(f, [pending])["amount"] == D("200")
    pending.update(closed=True, close_reason="ORDER_CANCELED")
    assert reference(f, [pending])["amount"] == D("350")
    pending.update(confirmed_gross="0", confirmed_units="0", close_reason="ORDER_REJECTED")
    assert reference(f, [pending])["amount"] == D("400")


def test_partial_sell_pending_amount_and_daily_cap():
    f = fund(".03")
    pending = order("SELL", "200", gross="51.50", confirmed_units="50")
    r = reference(f, [pending])
    assert r["plan"]["used_sell"] == D("206")
    assert r["amount"] <= D("394") and r["plan"]["remaining_daily"] == D("394")
    pending.update(closed=True)
    assert reference(f, [pending])["plan"]["used_sell"] == D("51.50")


@pytest.mark.parametrize("at,closed,gross,status", [("2026-10-20T04:00:00Z", False, "0", "OPPOSITE_ORDER"),
                                                    ("2026-10-19T04:00:00Z", False, "0", "OPPOSITE_ORDER"),
                                                    ("2026-10-20T04:00:00Z", True, "10", "OPPOSITE_ORDER"),
                                                    ("2026-10-20T04:00:00Z", True, "0", "REFERENCE"),
                                                    ("2026-10-19T04:00:00Z", True, "10", "REFERENCE")])
def test_opposite_direction_is_not_repeated_or_net_off(at, closed, gross, status):
    assert reference(orders=[order("SELL", "10", at=at, closed=closed, gross=gross)])["status"] == status


def test_beijing_actual_submission_date_and_other_funds_are_isolated():
    # 16:01 UTC on the 19th is already the 20th in Beijing.
    r = reference(orders=[order(requested="200", at="2026-10-19T16:01:00Z"),
                          order(requested="400", fid="other")])
    assert r["used_today"] == 200 and r["amount"] == 400
    assert reference(orders=[order(requested="600", at="2026-10-19T15:59:00Z")])["used_today"] == 0


def test_t_only_sale_caps_available_units_and_never_changes_base():
    f = fund(".03", t_units="10", t_cost="7")
    before = deepcopy(f)
    r = reference(f)
    assert r["action"] == "SELL" and r["units"] == 10 and r["amount"] == D("10.30")
    assert r["sell_impact"]["base_units"] == 0 and f == before
    assert reference(fund(".03", t_units="0"))["status"] == "NO_FEE_FREE_T_UNITS"


@pytest.mark.parametrize("acquired,ready", [("2026-10-14", False), ("2026-10-13", False),
                                             ("2026-09-21", False), ("2026-09-20", True)])
def test_fee_age_boundaries_6_7_29_30_days(acquired, ready):
    f = fund(".03"); f["lots"][0]["acquired"] = acquired
    r = reference(f)
    assert (r["action"] == "SELL") is ready
    assert r["fee"] == 0 if ready else r["status"] == "NO_FEE_FREE_T_UNITS"


def test_fifo_free_prefix_can_shorten_but_never_skip_charged_lot():
    f = fund(".03", t_cost="700")
    f["lots"] = [dict(f["lots"][0], id="a", units="50"),
                 dict(f["lots"][0], id="b", units="1950", acquired="2026-10-10")]
    r = reference(f)
    assert r["units"] == D("50") and r["fee"] == ZERO
    # A verified schedule charging the oldest prefix also cannot be bypassed.
    f["rules"]["redemption_tiers"][-1]["rate"] = ".005"
    assert reference(f)["status"] == "NO_FEE_FREE_T_UNITS"


def test_physical_free_prefix_reservations_and_unknown_availability_are_excluded():
    f = fund(".03", t_cost="500")
    f["lots"][0].update(units="20", reserved="10")
    assert reference(f)["units"] == 10
    f["lots"][0]["available_from"] = None
    assert reference(f)["status"] == "AVAILABILITY_UNKNOWN"


def test_per_lot_money_rounding_never_exceeds_ladder_or_daily_cap():
    f = fund(".5", t_cost="100")
    f["lots"] = [dict(f["lots"][0], id="a", units=".01"),
                 dict(f["lots"][0], id="b", units=".01"),
                 dict(f["lots"][0], id="c", units="1999.98")]
    r = reference(f)
    assert r["amount"] <= 600 and r["units"] < 400
    assert r["fee"] == 0 and not r["sell_impact"]["ledger_posted"]


def test_unknown_economic_cost_is_not_zero_and_profit_buffer_is_not_alpha():
    f = fund(".03", t_cost=None)
    assert reference(f)["status"] == "PNL_UNKNOWN"
    r = reference(fund(".01"))
    assert r["action"] == "HOLD" and r["status"] == "PROFIT_BUFFER" and r["amount"] == 0
    assert r["candidate_sell_impact"]["expected_realized_pnl"] < r["profit_buffer"]
    assert not r["can_apply"] and r["sell_impact"] is None
    loss = reference(fund(".03", t_cost="1000"))
    assert loss["status"] == "PROFIT_BUFFER" and loss["candidate_sell_impact"]["expected_realized_pnl"] < 0


def test_same_mark_sale_only_reclassifies_profit_without_adding_to_total():
    r = reference(fund(".03"))
    impact = r["sell_impact"]
    assert impact["expected_realized_pnl"] > 0
    assert impact["before"]["total_pnl"] == impact["after"]["total_pnl"]
    assert impact["sell_effect"] == 0 and not impact["ledger_posted"]


@pytest.mark.parametrize("now,label", [("2026-10-20T03:45:00Z", "午间"),
                                      ("2026-10-20T04:30:00Z", "午间"),
                                      ("2026-10-20T07:00:00Z", "今日已截止"),
                                      ("2026-10-20T08:30:00Z", "今日已截止")])
def test_lunch_and_cutoff_keep_quote_scenario_but_cannot_apply(now, label):
    r = reference(fund(now=now), now=now)
    assert r["amount"] == 600 and not r["can_apply"] and r["preview_only"]
    assert label in r["label"]


@pytest.mark.parametrize("status", ["STALE", "MISSING", "QUOTE_BASIS_MISMATCH", "CORPORATE_ACTION_REVIEW"])
def test_invalid_quote_does_not_generate_amount(status):
    f = fund(); f["daily_t"]["today"]["status"] = status
    r = reference(f)
    assert r["amount"] is None and not r["can_apply"] and r["status"] == status


def test_not_open_partial_holiday_initialization_and_product_rules():
    f = fund(); f["daily_t"]["market"].update(status="NOT_OPEN", label="没开盘")
    assert reference(f)["amount"] is None
    f = fund(); f["daily_t"]["market"]["partial_holiday"] = True
    r = reference(f)
    assert r["amount"] == 600 and r["preview_only"] and not r["can_apply"]
    f = fund(); f["initialization_pending"] = True
    assert reference(f)["status"] == "HOLDINGS_UNKNOWN"
    for change, field in (("-.03", "subscriptions_open"), (".03", "redemptions_open")):
        f = fund(change); f["rules"][field] = False
        assert reference(f)["status"] == "PRODUCT_CLOSED"
    f = fund(); f["rules"]["verified"] = False
    assert reference(f)["status"] == "RULES_UNVERIFIED"
    f = fund(); f["rules"]["effective_to"] = "2026-10-20"
    assert reference(f)["status"] == "RULES_UNVERIFIED"


def test_completed_day_budget_is_not_reissued_and_base_orders_do_not_consume_t_budget():
    assert reference(orders=[order(requested="600")])["status"] == "BUDGET_USED"
    r = reference(orders=[order(requested="600", bucket="BASE")])
    assert r["amount"] == 600 and r["used_today"] == 0
    assert r["plan"]["pending_buy"] == 600


def test_historical_same_direction_t_buy_is_headroom_only_and_custom_cap_scales_ladder():
    f = fund()
    old = order(requested="600", at="2026-10-19T04:00:00Z")
    r = reference(f, [old])
    assert r["amount"] == 600 and r["used_today"] == 0 and r["plan"]["pending_buy"] == 600
    f["investment_plan_profile"]["holding_cap"] = "3000"
    r = reference(f)
    assert r["amount"] == 450 and r["daily_budget"] == 450


def test_economic_t_reservations_limit_sale_without_borrowing_base():
    f = fund(".03", t_cost="500")
    f["position_buckets"]["positions"][-1]["reserved"] = D("890")
    f["position_buckets"]["groups"]["TACTICAL"].update(reserved=D("890"), available_units=D("10"))
    r = reference(f)
    assert r["units"] == 10 and r["sell_impact"]["base_units"] == 0


def test_current_quote_computed_decimal_ratio_keeps_working_precision():
    f = fund("-.03")
    f["daily_t"]["today"]["change"] = D(".97") / D("1.0013") - 1
    assert reference(f)["amount"] == 600


@pytest.mark.parametrize("state", ["NOT_OPEN", "MISSING", "STALE"])
def test_known_official_summary_remains_visible_without_current_quote(state):
    f = fund()
    f.update(valuation_nav=D("1"), market_value=D("2000"),
             nav={"effective_date": "2026-10-19", "value": "1"})
    if state == "NOT_OPEN":
        f["daily_t"]["market"].update(status=state, label="没开盘")
    else:
        f["daily_t"]["today"].update(status=state, quote=None)
    old_buy = order(requested="300", at="2026-10-19T04:00:00Z")
    r = reference(f, [old_buy])
    p = r["plan"]
    assert r["amount"] is None and not r["can_apply"]
    assert p["holding_value"] == 2000 and p["base_value"] == 1100 and p["t_value"] == 900
    assert p["pending_buy"] == 300 and p["committed_holding"] == 2300 and p["headroom"] == 1700
    assert p["valuation_basis"] == "OFFICIAL_NAV" and p["valuation_date"] == "2026-10-19"


def test_current_quote_overrides_official_summary_with_explicit_basis():
    f = fund()
    f.update(valuation_nav=D("1"), market_value="2000",
             nav={"effective_date": "2026-10-19", "value": "1"})
    p = reference(f)["plan"]
    assert p["holding_value"] == 1940 and p["base_value"] == 1067 and p["headroom"] == 2060
    assert p["valuation_basis"] == "CURRENT_QUOTE" and p["valuation_date"] == "2026-10-20"


def test_official_summary_can_derive_value_from_confirmed_units_and_adjusted_nav():
    f = fund()
    f.update(valuation_nav=D(".9"), nav={"effective_date": "2026-10-19"})
    f["position_buckets"]["groups"]["BASE"]["value"] = None
    f["daily_t"]["today"]["status"] = "MISSING"
    p = reference(f)["plan"]
    assert p["holding_value"] == 1800 and p["base_value"] == 990 and p["headroom"] == 2200
    assert p["valuation_basis"] == "OFFICIAL_NAV"


def test_unknown_earlier_availability_prevents_skipping_to_later_free_lot():
    f = fund(".03", t_cost="500")
    f["lots"] = [dict(f["lots"][0], id="unknown-first", units="10", available_from=None),
                 dict(f["lots"][0], id="later", units="1990", acquired="2026-06-02")]
    r = reference(f)
    assert r["status"] == "AVAILABILITY_UNKNOWN" and r["amount"] is None


def test_unknown_availability_after_known_prefix_only_keeps_that_prefix():
    f = fund(".03", t_cost="500")
    f["lots"] = [dict(f["lots"][0], id="known-first", units="10"),
                 dict(f["lots"][0], id="unknown-later", units="1990", acquired="2026-06-02", available_from=None)]
    r = reference(f)
    assert r["action"] == "SELL" and r["units"] == 10


def test_unknown_holding_date_cannot_be_assumed_to_sort_after_known_free_lot():
    f = fund(".03", t_cost="500")
    f["lots"] = [dict(f["lots"][0], id="known", units="1990"),
                 dict(f["lots"][0], id="unknown", units="10", acquired=None)]
    r = reference(f)
    assert r["status"] == "HISTORY_INCOMPLETE" and r["amount"] is None


ZERO = D("0")
