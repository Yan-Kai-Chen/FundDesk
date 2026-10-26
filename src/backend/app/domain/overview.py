"""Read-only price impact for the units held now, distinct from daily cash-flow P&L."""
from app.domain.finance import ZERO, dec, money


LABEL = "当前持仓今日变动参考"
BASIS = "CURRENT_UNITS_PRICE_CHANGE"
DETAIL = "按当前持有份额与上一交易日正式净值计算，不是资金流调整后的实际日收益。"


def fund_day_valuation(fund, business_date):
    context = fund.get("daily_t") or {}
    today = context.get("today") or {}
    market = context.get("market") or {}
    units = dec(fund.get("units", ZERO))
    result = {"date": business_date, "label": LABEL, "basis": BASIS,
              "status": today.get("status", "MISSING"), "phase": today.get("phase", "WAIT"),
              "units": units, "quote_id": None, "baseline_id": None,
              "quote_date": None, "baseline_date": None, "value_kind": None,
              "is_estimate": None, "as_of": None, "current_nav": None,
              "reference_nav": None, "holding_value": None, "price_change": None,
              "base_change": None, "tactical_change": None,
              "detail": today.get("label") or "等待当天报价"}
    buckets = fund.get("position_buckets") or {}
    if fund.get("initialization_pending") or buckets.get("status") == "BLOCKED":
        return dict(result, status="HOLDINGS_REVIEW", detail="持仓或分红拆分口径待核对")
    if today.get("status") != "READY":
        return result
    quote, baseline = today.get("quote"), today.get("baseline")
    if (market.get("date") != business_date or not quote or not baseline or
            quote.get("effective_date") != business_date or
            baseline.get("value_kind") != "OFFICIAL_NAV" or
            baseline.get("effective_date", business_date) >= business_date or
            (market.get("previous_date") and baseline.get("effective_date") != market["previous_date"]) or
            quote.get("value_kind") not in {"OFFICIAL_NAV", "THIRD_PARTY_ESTIMATE", "LOCAL_ESTIMATE"}):
        return dict(result, status="BASIS_MISSING", detail="当天报价或上一交易日净值待更新")
    current, previous = dec(quote["value"], positive=True), dec(baseline["value"], positive=True)
    impact = money(units * (current - previous))
    base, tactical = (buckets.get("groups") or {}).get("BASE"), (buckets.get("groups") or {}).get("TACTICAL")
    if base is not None and tactical is not None and dec(base["units"]) + dec(tactical["units"]) == units:
        result["base_change"] = money(dec(base["units"]) * (current - previous))
        result["tactical_change"] = impact - result["base_change"]
    return dict(result, status="READY", quote_id=quote["id"], baseline_id=baseline["id"],
                quote_date=quote["effective_date"], baseline_date=baseline["effective_date"],
                value_kind=quote["value_kind"], is_estimate=quote["value_kind"] != "OFFICIAL_NAV",
                as_of=quote.get("as_of") or quote.get("observed_at"), current_nav=current,
                reference_nav=previous, holding_value=money(units * current), price_change=impact,
                detail=DETAIL)


def portfolio_day_valuation(funds, business_date):
    held = [f for f in funds if dec(f.get("units", ZERO)) > ZERO or f.get("initialization_pending")]
    ready = [f for f in held if (f.get("day_valuation") or {}).get("status") == "READY"]
    complete = bool(held) and len(held) == len(ready)
    subtotal = sum((f["day_valuation"]["price_change"] for f in ready), ZERO) if ready else None
    statuses = {(f.get("day_valuation") or {}).get("status") for f in held}
    status = ("EMPTY" if not held else "READY" if complete else "PARTIAL" if ready else
              "NOT_OPEN" if statuses == {"NOT_OPEN"} else "WAIT")
    ready_ids = {f["id"] for f in ready}
    return {"date": business_date, "label": LABEL, "basis": BASIS, "status": status,
            "holding_count": len(held), "covered_count": len(ready), "complete": complete,
            "total": subtotal if complete else None, "known_subtotal": subtotal,
            "missing_fund_ids": [f["id"] for f in held if f["id"] not in ready_ids], "detail": DETAIL}
