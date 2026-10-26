from copy import deepcopy
from decimal import Decimal as D

import pytest

from app.domain.buckets import BucketBook
from app.domain.investment_plan import investment_plan_reference
from app.domain.investment_risk import investment_risk, _threshold, _advance, _metrics
from app.domain.market_calendar import market_session
from app.domain.research_calendar import reference_sessions

NOW = "2026-10-23T05:30:00Z"
TODAY = "2026-10-23"


def case(change="-.012", tail=None, count=90, t_cost="1000", base_cost="4000", t_units="1000", base_units="3000"):
    dates = [d for d in reference_sessions() if d < TODAY][-count:]
    values = [D("1")] * count
    if tail:
        start = count - len(tail)
        for i, value in enumerate(tail, start):
            values[i] = values[i - 1] * (1 + D(value))
    rows = [{"id": "nav-" + on, "effective_date": on, "value": value.quantize(D(".000000000001")),
             "value_kind": "OFFICIAL_NAV", "quality": "MANUAL", "observed_at": on + "T13:00:00Z"}
            for on, value in zip(dates, values)]
    last = rows[-1]["value"]
    book = BucketBook()
    book.buy("base", base_units, base_cost, "BASE", "2026-01-05", "2026-01-06")
    book.buy("t", t_units, t_cost, "TACTICAL", "2026-01-05", "2026-01-06")
    total = D(base_units) + D(t_units)
    fund = {"id": "f", "units": total, "market_value": total * last, "valuation_nav": last,
            "nav": rows[-1], "history": rows, "sellable_units": total,
            "investment_plan_profile": {"enabled": True, "holding_cap": "6000", "template": "DRAWDOWN_GUARD_V2", "_version": 2},
            "position_buckets": book.summary(last, TODAY),
            "daily_t": {"market": market_session(NOW, "CN"), "today": {"status": "READY", "change": D(change),
                        "quote": {"id": "estimate", "effective_date": TODAY, "value": (last * (1 + D(change))).quantize(D(".000000000001")), "value_kind": "THIRD_PARTY_ESTIMATE"},
                        "baseline": rows[-1]}, "presets": {}},
            "rules": {"verified": True, "effective_from": "2026-01-01", "unit_step": ".01", "lot_method": "FIFO",
                      "redemption_tiers": [{"min_days": 0, "max_days": None, "rate": "0"}]},
            "lots": [{"id": "physical", "units": total, "reserved": "0", "cost": D(base_cost) + D(t_cost), "acquired": "2026-01-05", "available_from": "2026-01-06"}]}
    return fund


def order(requested="300", at="2026-10-21T04:00:00Z", closed=False, gross="0", bucket="TACTICAL", side="BUY"):
    return {"fund_id": "f", "side": side, "bucket": bucket, "requested": requested,
            "submitted_at": at, "closed": closed, "confirmed_gross": gross, "confirmed_units": "0"}


def result(f=None, orders=None):
    return investment_plan_reference(f or case(), orders or [], NOW)


def test_v2_flat_history_initializes_normal_and_day_one_is_300():
    item = result()
    risk = item["plan"]["risk"]
    assert risk["ready"] and risk["state"] == "NORMAL" and risk["h"] == D(".01")
    assert item["amount"] == 300 and item["can_apply"] and item["method_version"] == "investment-plan-2026-v2"
    assert risk["streak"] == 1 and risk["provisional"]
    assert risk["ma20"] == risk["ma60"] == 1  # Intraday estimate cannot create an MA cross.


def test_threshold_uses_last_sixty_returns_not_sixty_largest_and_nearest_rank():
    values = [D(1)]
    for change in [D(".1")] * 60 + [D(".01501")] * 60:
        values.append(values[-1] * (1 + change))
    assert _threshold(values) == D(".016")
    assert _threshold([D(1)] * 20) is None
    assert _threshold([D(1)] * 21) == D(".01")


@pytest.mark.parametrize("tail,change,state", [([], "-.012", "NORMAL"), (["-.012"], "-.012", "DEFENSIVE"),
                                               (["-.012"] * 2, "-.012", "DEFENSIVE"),
                                               (["-.012"] * 3, "-.012", "PAUSED"),
                                               ([], "-.06", "PAUSED")])
def test_continuous_decline_and_shock_do_not_accelerate_buying(tail, change, state):
    item = result(case(change, tail=tail))
    assert item["plan"]["risk"]["state"] == state
    assert item["amount"] == 0 if state == "PAUSED" else item["amount"] <= 300
    if state == "PAUSED":
        assert "暂停" in item["plan"]["risk"]["reasons"][0] or "4个" in item["plan"]["risk"]["reasons"][0]


def test_defensive_buy_is_half_normal_ladder_target():
    r = result(case("-.022", tail=["-.012"]))
    assert r["plan"]["raw_target_today"] == 600
    assert r["plan"]["target_today"] == 300 and r["amount"] == 300


def test_drawdown_does_not_create_new_principal_budget():
    f = case("-.012", base_cost="4159.33", t_cost="1039.84", base_units="3000", t_units="1000")
    item = result(f)
    risk = item["plan"]["risk"]
    assert risk["cost_basis"] == D("5199.17")
    assert risk["capital_headroom"] == D("800.83")
    f["daily_t"]["today"]["quote"]["value"] = D(".8")
    f["daily_t"]["today"]["change"] = D("-.2")
    risk2 = result(f)["plan"]["risk"]
    assert risk2["capital_used"] == risk["capital_used"] and risk2["capital_headroom"] == risk["capital_headroom"]


def test_all_pending_buys_use_capital_while_pending_sale_never_releases_it():
    item = result(orders=[order("700", bucket="BASE"), order("200", side="SELL", bucket="BASE")])
    assert item["plan"]["risk"]["capital_used"] == 5700
    assert item["plan"]["headroom"] == 300
    assert item["amount"] == 300


def test_partial_cancellation_and_rejection_count_only_valid_buy_commitments():
    pending = order("600", at="2026-10-23T04:00:00Z", gross="100")
    assert result(orders=[pending])["plan"]["risk"]["rolling_buy_used"] == 600
    pending.update(closed=True)
    assert result(orders=[pending])["plan"]["risk"]["rolling_buy_used"] == 100
    pending["confirmed_gross"] = "0"
    assert result(orders=[pending])["plan"]["risk"]["rolling_buy_used"] == 0


def test_normal_cooldown_is_one_full_reference_day_defensive_is_two():
    previous_day = order(at="2026-10-22T04:00:00Z", closed=True, gross="300")
    item = result(orders=[previous_day])
    assert item["status"] == "BUY_COOLDOWN" and item["plan"]["risk"]["cooldown_until"] == "2026-10-26"
    two_days_before = order(at="2026-10-21T04:00:00Z", closed=True, gross="300")
    assert result(orders=[two_days_before])["action"] == "BUY"
    defensive = result(case(tail=["-.012"]), [two_days_before])
    assert defensive["status"] == "BUY_COOLDOWN" and defensive["plan"]["risk"]["cooldown_until"] == "2026-10-26"


def test_same_day_tier_increase_is_difference_despite_cooldown():
    current = order("300", at="2026-10-23T04:00:00Z")
    item = result(case("-.022"), [current])
    assert item["plan"]["raw_target_today"] == 600
    # The two-percent 5-day decline alone enters defense; no duplicate 300.
    assert item["status"] == "BUDGET_USED" and not item["plan"]["risk"]["cooldown_blocked"]
    f = case("-.022", tail=[".015"])
    item = result(f, [current])
    assert item["plan"]["risk"]["state"] == "NORMAL" and item["amount"] == 300


def test_rolling_buy_limit_is_gross_and_sales_do_not_restore_it():
    buys = [order("900", at="2026-10-19T04:00:00Z", closed=True, gross="900")]
    sold = order("900", at="2026-10-20T04:00:00Z", closed=True, gross="900", side="SELL")
    item = result(orders=buys + [sold])
    assert item["status"] == "ROLLING_BUY_LIMIT" and item["plan"]["risk"]["rolling_buy_remaining"] == 0
    assert result(case(tail=["-.012"]), buys)["plan"]["risk"]["rolling_buy_limit"] == 450


def test_paused_market_risk_does_not_block_a_profitable_t_sale_or_touch_base():
    f = case(".03", tail=["-.012"] * 4, t_cost="500")
    before = deepcopy(f)
    item = result(f)
    assert item["plan"]["risk"]["formal_state"] == "PAUSED"
    assert item["action"] == "SELL" and item["sell_impact"]["base_units"] == 0
    assert f == before


def test_loss_t_sale_retains_profit_buffer_even_when_risk_paused():
    item = result(case(".03", tail=["-.012"] * 4, t_cost="2000"))
    assert item["status"] == "PROFIT_BUFFER" and item["candidate_sell_impact"]["expected_realized_pnl"] < 0


def test_intraday_rebound_never_relaxes_a_formally_paused_state():
    risk = result(case(".10", tail=["-.012"] * 4))["plan"]["risk"]
    assert risk["state"] == risk["formal_state"] == "PAUSED"
    metrics = _metrics([D(1)] * 60 + [D("1.01"), D("1.02")])
    state, _ = _advance("PAUSED", metrics, D(".01"), D("1.02"))
    assert state == "DEFENSIVE"
    state, _ = _advance(state, metrics, D(".01"), D("1.02"))
    assert state == "NORMAL"


def test_today_official_close_is_not_appended_twice_or_used_for_its_own_h():
    f = case("-.012")
    today = {"id": "official-today", "effective_date": TODAY, "value": "0.988", "value_kind": "OFFICIAL_NAV", "observed_at": "2026-10-23T04:00:00Z"}
    f["history"].append(today)
    f["daily_t"]["today"]["quote"] = today
    risk = result(f)["plan"]["risk"]
    assert risk["streak"] == 1 and risk["h"] == D(".01") and not risk["provisional"]


def test_no_quote_still_displays_formal_risk_and_principal_without_action():
    f = case(tail=["-.012"] * 4)
    f["daily_t"]["today"].update(status="NOT_OPEN", quote=None, baseline=None)
    f["daily_t"]["market"].update(status="NOT_OPEN", label="没开盘")
    item = result(f)
    assert item["amount"] is None and item["plan"]["risk"]["state"] == "PAUSED"
    assert item["plan"]["risk"]["capital_used"] == 5000


def test_short_history_missing_session_corporate_and_unknown_calendar_wait():
    for f, expected in [(case(count=20), "RISK_HISTORY_MISSING"), (case(count=21), None)]:
        item = result(f)
        assert item["status"] == expected if expected else item["plan"]["risk"]["ready"]
    f = case(); f["history"].pop(-4)
    assert result(f)["status"] == "RISK_HISTORY_MISSING"
    f = case(); f["nav_sync"] = {"corporate_actions_complete": False}
    assert result(f)["status"] == "RISK_CORPORATE_REVIEW"
    f = case(); f["daily_t"]["market"]["date"] = "2027-01-04"
    assert result(f)["status"] == "RISK_CALENDAR_MISSING"


def test_future_observations_cannot_change_risk_and_history_is_bounded():
    f = case(count=220)
    before = result(f)["plan"]["risk"]
    future = dict(f["history"][-1], id="future", value=".1", observed_at="2026-10-23T06:00:00Z")
    f["history"].append(future)
    after = result(f)["plan"]["risk"]
    assert before == after and len(after["input_ids"]) == 181


def test_future_disclosure_and_asof_are_not_training_inputs():
    f = case()
    original = result(f)["plan"]["risk"]
    for field in ("published_at", "as_of"):
        f["history"].append(dict(f["history"][-1], id="future-" + field, value=".1", **{field: "2026-10-23T06:00:00Z"}))
        assert result(f)["plan"]["risk"] == original


def test_input_digest_changes_for_correction_without_changing_history_identity():
    f = case()
    old = result(f)["plan"]["risk"]["input_basis"]["history_digest"]
    f["history"][-10]["value"] = D("1.001")
    assert result(f)["plan"]["risk"]["input_basis"]["history_digest"] != old
