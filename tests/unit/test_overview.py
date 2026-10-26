from copy import deepcopy
from decimal import Decimal as D

import pytest

from app.domain.overview import fund_day_valuation, portfolio_day_valuation


DAY = "2026-10-23"


def fund(id="fund", units="1000", value="1.02", base="600", tactical="400"):
    return {"id": id, "units": units, "position_buckets": {"status": "CONFIRMED", "groups": {
        "BASE": {"units": base}, "TACTICAL": {"units": tactical}}},
        "daily_t": {"market": {"date": DAY, "previous_date": "2026-10-22"}, "today": {
            "status": "READY", "phase": "LIVE", "label": "今日估算涨跌",
            "quote": {"id": "quote", "effective_date": DAY, "value": value,
                      "value_kind": "THIRD_PARTY_ESTIMATE", "as_of": "2026-10-23T02:00:00Z"},
            "baseline": {"id": "prior", "effective_date": "2026-10-22", "value": "1", "value_kind": "OFFICIAL_NAV"}}}}


def project(f):
    return dict(f, day_valuation=fund_day_valuation(f, DAY))


def test_current_units_price_impact_is_read_only_and_never_cost_or_return_rate():
    f = fund(); original = deepcopy(f)
    f["cost"] = None
    result = fund_day_valuation(f, DAY)
    assert result["price_change"] == D("20")
    assert result["holding_value"] == D("1020")
    assert result["base_change"] == D("12") and result["tactical_change"] == D("8")
    assert result["basis"] == "CURRENT_UNITS_PRICE_CHANGE" and result["is_estimate"]
    assert result["quote_id"] == "quote" and result["baseline_id"] == "prior"
    assert {k: v for k, v in f.items() if k != "cost"} == original


def test_bucket_rounding_conserves_total_for_fractional_cents():
    r = fund_day_valuation(fund(units="1", value="1.01", base=".5", tactical=".5"), DAY)
    assert r["price_change"] == D(".01")
    assert r["base_change"] == D(".01") and r["tactical_change"] == D("0")
    assert r["base_change"] + r["tactical_change"] == r["price_change"]


@pytest.mark.parametrize("status", ["NOT_OPEN", "MISSING", "STALE", "CORPORATE_ACTION_REVIEW"])
def test_not_ready_is_unknown_instead_of_zero(status):
    f = fund(); f["daily_t"]["today"]["status"] = status
    r = fund_day_valuation(f, DAY)
    assert r["status"] == status and r["price_change"] is None and r["holding_value"] is None
    assert r["base_change"] is None and r["tactical_change"] is None


@pytest.mark.parametrize("field,value", [
    ("market_date", "2026-10-22"), ("quote_date", "2026-10-22"),
    ("baseline_date", DAY), ("baseline_date", "2026-10-21"), ("baseline_kind", "MANUAL_VALUE"),
])
def test_wrong_day_or_baseline_never_reuses_yesterday_as_today(field, value):
    f = fund(); context = f["daily_t"]
    if field == "market_date": context["market"]["date"] = value
    elif field == "quote_date": context["today"]["quote"]["effective_date"] = value
    elif field == "baseline_date": context["today"]["baseline"]["effective_date"] = value
    else: context["today"]["baseline"]["value_kind"] = value
    assert fund_day_valuation(f, DAY)["price_change"] is None


def test_official_metadata_is_distinct_and_unreconciled_holdings_wait():
    f = fund(); f["daily_t"]["today"]["quote"]["value_kind"] = "OFFICIAL_NAV"
    r = fund_day_valuation(f, DAY)
    assert r["is_estimate"] is False and r["value_kind"] == "OFFICIAL_NAV"
    f["position_buckets"]["status"] = "BLOCKED"
    assert fund_day_valuation(f, DAY)["status"] == "HOLDINGS_REVIEW"
    f["position_buckets"]["status"] = "CONFIRMED"; f["initialization_pending"] = True
    assert fund_day_valuation(f, DAY)["price_change"] is None


def test_complete_partial_preopen_and_empty_portfolio_amounts():
    a, b = project(fund("a")), project(fund("b", value=".99"))
    complete = portfolio_day_valuation([a, b], DAY)
    assert complete["total"] == D("10") and complete["covered_count"] == complete["holding_count"] == 2
    assert complete["complete"] and complete["status"] == "READY"
    b["day_valuation"]["status"] = "MISSING"
    partial = portfolio_day_valuation([a, b], DAY)
    assert partial["total"] is None and partial["known_subtotal"] == D("20")
    assert partial["missing_fund_ids"] == ["b"] and partial["status"] == "PARTIAL"
    a["day_valuation"]["status"] = b["day_valuation"]["status"] = "NOT_OPEN"
    pending = portfolio_day_valuation([a, b], DAY)
    assert pending["status"] == "NOT_OPEN" and pending["total"] is None and pending["known_subtotal"] is None
    empty = portfolio_day_valuation([project(fund("empty", units="0", base="0", tactical="0"))], DAY)
    assert empty["status"] == "EMPTY" and empty["total"] is None and empty["holding_count"] == 0


def test_unknown_initial_holdings_cannot_disappear_from_portfolio_coverage():
    a = project(fund("known"))
    unknown = fund("unknown", units="0", base="0", tactical="0")
    unknown["initialization_pending"] = True
    result = portfolio_day_valuation([a, project(unknown)], DAY)
    assert result["holding_count"] == 2 and result["covered_count"] == 1
    assert result["total"] is None and result["known_subtotal"] == D("20")
    assert result["missing_fund_ids"] == ["unknown"]
