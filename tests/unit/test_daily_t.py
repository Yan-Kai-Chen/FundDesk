from datetime import date, timedelta
from decimal import Decimal as D
import pytest

from app.domain.market_calendar import market_session, previous_reference_date
from app.domain.daily_t import profile_options, today_quote, daily_context, reference
from app.domain.buckets import BucketBook


@pytest.mark.parametrize("at,scope,status", [
    ("2026-10-21T09:29:59+08:00", "CN_HK", "NOT_OPEN"),
    ("2026-10-21T09:30:00+08:00", "CN_HK", "OPEN"),
    ("2026-10-21T11:45:00+08:00", "CN_HK", "OPEN"),
    ("2026-10-21T12:00:00+08:00", "CN_HK", "BREAK"),
    ("2026-10-21T13:00:00+08:00", "CN_HK", "OPEN"),
    ("2026-10-21T15:05:00+08:00", "CN_HK", "OPEN"),
    ("2026-10-21T16:10:00+08:00", "CN_HK", "CLOSED"),
    ("2026-10-21T11:30:00+08:00", "CN", "BREAK"),
    ("2026-10-21T15:00:00+08:00", "CN", "CLOSED"),
    ("2026-10-10T10:00:00+08:00", "CN", "NOT_OPEN"),  # Saturday make-up workday
    ("2026-10-01T10:00:00+08:00", "CN_HK", "NOT_OPEN"),
    ("2026-12-24T12:10:00+08:00", "CN_HK", "BREAK"),  # HK half-day; CN resumes at 13
    ("2027-01-04T10:00:00+08:00", "CN_HK", "UNKNOWN"),
    ("2026-10-21T10:00:00+08:00", "UNKNOWN", "UNKNOWN"),
])
def test_reference_market_boundaries(at, scope, status):
    assert market_session(at, scope)["status"] == status


def test_timezone_holiday_union_and_cutoff():
    s = market_session("2026-10-21T00:35:00+09:00", "CN_HK")
    assert s["date"] == "2026-10-20" and s["status"] == "CLOSED"
    s = market_session("2026-10-19T10:00:00+08:00", "CN_HK")
    assert s["partial_holiday"] and s["open_venues"] == ["CN"]
    assert previous_reference_date(date(2026, 10, 12), "CN") == "2026-10-09"
    assert not market_session("2026-10-21T15:00:00+08:00", "CN_HK")["before_cutoff"]


def record(id, date_, value="1", kind="OFFICIAL_NAV", as_of=None):
    return {"id": id, "effective_date": date_, "value": value, "value_kind": kind,
            "observed_at": as_of or "2026-10-20T03:00:00Z", "as_of": as_of, "source": "synthetic"}


def history(count=61):
    start = date(2026, 10, 19) - timedelta(days=count - 1)
    return [record(str(i), (start + timedelta(days=i)).isoformat(), "1") for i in range(count)]


def test_presets_empirical_rank_ceiling_and_no_current_day_lookahead():
    records = [record("0", "2026-09-01", "1"), record("1", "2026-09-02", "1.01501")]
    records += [record(str(i), f"2026-09-{i+1:02}", "1.01501") for i in range(2, 21)]
    p = profile_options(records, "2026-10-20", "1114.77")
    assert p["ready"] and p["sample_count"] == 20
    cautious = p["options"][1]
    assert cautious["trigger"] == D("0.005") and cautious["single_budget"] == D("22.30")
    assert p["options"][2]["trigger"] == D("0.010")  # two times allowed fee rate
    assert p["options"][3]["single_budget"] == D("89.18")
    assert p["options"][3]["daily_budget"] == D("178.36")
    assert profile_options(records + [record("today", "2026-10-20", "10")], "2026-10-20", "1114.77") == p
    moving = [record(str(i), (date(2026, 8, 1) + timedelta(days=i)).isoformat(), str((D("1.01501") ** i).quantize(D(".00000001")))) for i in range(61)]
    assert profile_options(moving, "2026-10-20", "1000")["options"][1]["trigger"] == D("0.016")


def test_short_history_corporate_action_and_missing_value():
    assert not profile_options(history(20), "2026-10-20", "100")["ready"]
    p = profile_options(history(90), "2026-10-20", None, True)
    assert p["sample_count"] == 60 and p["blocked_by"] == "CORPORATE_ACTION_REVIEW"
    assert all(o["trigger"] is None and o["single_budget"] is None for o in p["options"])


@pytest.mark.parametrize("date_,as_of,status", [
    ("2026-10-19", "2026-10-20T05:29:00Z", "MISSING"),
    ("2026-10-20", "2026-10-20T05:14:59Z", "STALE"),
    ("2026-10-20", "2026-10-20T05:15:00Z", "READY"),
    ("2026-10-20", "2026-10-20T05:30:01Z", "MISSING"),
    ("2026-10-20", "2026-10-19T05:29:00Z", "STALE"),
])
def test_today_quote_never_uses_yesterday_future_or_stale(date_, as_of, status):
    now = "2026-10-20T05:30:00Z"
    s = market_session(now, "CN_HK")
    quote = today_quote(history(), [record("quote", date_, "1.03", "THIRD_PARTY_ESTIMATE", as_of)], now, s)
    assert quote["status"] == status
    assert (quote["change"] == D(".03")) if status == "READY" else quote["change"] is None


def test_official_today_percent_and_prior_baseline_required():
    now = "2026-10-20T12:30:00Z"
    s = market_session(now, "CN_HK")
    q = today_quote(history() + [record("official", "2026-10-20", "1.025")], [], now, s)
    assert q["change"] == D(".025") and q["label"] == "收盘正式涨跌"
    assert q['display_title'] == '收盘涨跌' and q['phase'] == 'OFFICIAL'
    assert today_quote(history()[:-1], [], now, s)["status"] == "BASELINE_MISSING"
    before = market_session("2026-10-20T00:00:00Z", "CN_HK")
    assert today_quote(history(), [], now, before)["label"] == "没开盘"


def fund_and_context(level="BALANCED", value="1.03", now="2026-10-20T05:30:00Z"):
    f = {"id": "synthetic", "history": history(), "market_value": D("1000"), "sellable_units": D("1000"),
         "rules": {"verified": True, "effective_from": "2026-01-01", "lot_method": "FIFO", "unit_step": ".01",
                   "redemption_tiers": [{"min_days": 0, "max_days": 7, "rate": ".015"},
                                        {"min_days": 7, "max_days": 30, "rate": ".005"},
                                        {"min_days": 30, "max_days": None, "rate": "0"}]},
         "lots": [{"id": "lot", "units": "1000", "cost": "1000", "reserved": "0", "acquired": "2026-06-01", "available_from": "2026-06-03"}]}
    book = BucketBook()
    book.buy('economic', '1000', '1000', 'TACTICAL', '2026-06-01', '2026-06-03')
    f['position_buckets'] = book.summary('1', '2026-10-20')
    f["daily_t"] = daily_context(f, {"level": level, "_version": 1},
                                [record("quote", "2026-10-20", value, "THIRD_PARTY_ESTIMATE", "2026-10-20T05:29:00Z")], now, "CN_HK")
    return f


def test_reference_buy_sell_hold_and_no_ledger_mutation():
    f = fund_and_context()
    r = reference(f, [], "2026-10-20T05:30:00Z")
    assert r["action"] == "SELL" and r["amount"] <= D("50") and r["units"] == D("48.54")
    assert r["fee"] == 0 and f["lots"][0]["units"] == "1000"
    impact = r['sell_impact']
    assert impact['expected_realized_pnl'] == D('1.46')
    assert impact['before']['total_pnl'] == impact['after']['total_pnl'] == D('30')
    assert not impact['ledger_posted'] and f['position_buckets']['groups']['TACTICAL']['units'] == D('1000')
    buy = reference(fund_and_context(value=".97"), [], "2026-10-20T05:30:00Z")
    assert buy["action"] == "BUY" and buy["amount"] == D("50") and buy["cash_status"] == "REFERENCE_ONLY"
    assert reference(fund_and_context(value="1.001"), [], "2026-10-20T05:30:00Z")["action"] == "HOLD"
    assert reference(fund_and_context(level="OBSERVE"), [], "2026-10-20T05:30:00Z")["status"] == "OBSERVE"


def test_fee_cap_uses_rate_even_if_small_fee_rounds_to_zero_and_caps_shares():
    f = fund_and_context()
    f["lots"][0]["acquired"] = "2026-10-19"
    f["sellable_units"] = D(".98")
    assert reference(f, [], "2026-10-20T05:30:00Z")["status"] == "FEE_LIMIT"
    f["lots"][0]["acquired"] = "2026-10-10"
    f["daily_t"]["profile"]["level"] = "CAUTIOUS"
    assert reference(f, [], "2026-10-20T05:30:00Z")["status"] == "FEE_LIMIT"
    f["daily_t"]["profile"]["level"] = "BALANCED"
    result = reference(f, [], "2026-10-20T05:30:00Z")
    assert result["units"] == D(".98")


def test_daily_confirmed_turnover_and_pending_orders_per_fund():
    f = fund_and_context(value=".97")
    orders = [{"fund_id": f["id"], "side": "SELL", "bucket": "TACTICAL", "closed": True,
               "submitted_at": "2026-10-20T04:00:00Z", "confirmed_gross": "80"}]
    r = reference(f, orders, "2026-10-20T05:30:00Z")
    assert r["amount"] == D("20") and r["used_today"] == D("80")
    orders[0]["confirmed_gross"] = "100"
    assert reference(f, orders, "2026-10-20T05:30:00Z")["status"] == "BUDGET_USED"
    orders[0]["closed"] = False
    assert reference(f, orders, "2026-10-20T05:30:00Z")["status"] == "ORDER_PENDING"
    orders[0]["fund_id"] = "other"
    assert reference(f, orders, "2026-10-20T05:30:00Z")["amount"] == D("50")


def test_per_lot_rounding_cannot_exceed_single_or_daily_money_cap():
    f = fund_and_context(value="1.5")
    f["lots"] = [dict(f["lots"][0], id="a", units=".01"), dict(f["lots"][0], id="b", units=".01"),
                 dict(f["lots"][0], id="c", units="999.98")]
    r = reference(f, [], "2026-10-20T05:30:00Z")
    assert r["units"] == D("33.32") and r["amount"] <= D("50")


@pytest.mark.parametrize("mutate,status", [
    (lambda f: f["rules"].update(verified=False), "RULES_UNVERIFIED"),
    (lambda f: f["rules"].update(redemptions_open=False), "PRODUCT_CLOSED"),
    (lambda f: f.update(initialization_pending=True), "HOLDINGS_UNKNOWN"),
    (lambda f: f.update(sellable_units=D("0")), "UNITS_RESERVED"),
    (lambda f: f["daily_t"]["presets"].update(ready=False, blocked_by="CORPORATE_ACTION_REVIEW"), "CORPORATE_ACTION_REVIEW"),
    (lambda f: f["daily_t"]["market"].update(before_cutoff=False), "CUTOFF"),
    (lambda f: f["daily_t"]["market"].update(partial_holiday=True), "PARTIAL_HOLIDAY"),
    (lambda f: f.pop('position_buckets'), 'BUCKET_PREVIEW_UNAVAILABLE'),
    (lambda f: f['position_buckets']['positions'][0].update(cost=None), 'PNL_UNKNOWN'),
])
def test_reference_guardrails(mutate, status):
    f = fund_and_context()
    mutate(f)
    r = reference(f, [], "2026-10-20T05:30:00Z")
    assert r["status"] == status and r["amount"] is None
