"""Reference previews only. Never assume the reference NAV is the dealing NAV."""
from .domain import (ZERO, ONE, dec, day, stamp, floor_units, floor_money, money)


def fee_ready(asset, data):
    fee = asset.get("fees")
    if fee is None:
        return "FEES_MISSING"
    if stamp(fee["known_at"]) > data["_as_of"]:
        return "FEES_NOT_YET_KNOWN"
    dealing = data["_dealing_date"]
    if fee["effective_from"] > dealing or (fee["effective_to"] is not None and dealing >= fee["effective_to"]):
        return "FEE_VERSION_NOT_EFFECTIVE"
    return None


def redemption_prefix(holding, fee, requested_units, date_string, fee_limit):
    """FIFO prefix; never skip an ineligible or expensive earlier lot."""
    lots = holding["lots"]
    if any(lot["confirmed_date"] is None or lot["redeemable_from"] is None for lot in lots):
        return ZERO, ZERO, [], "LOT_DATES_UNKNOWN"
    lots = sorted(lots, key=lambda row: (row["confirmed_date"], row["id"]))
    remaining, units, allocations = requested_units, ZERO, []
    stopped = None
    for lot in lots:
        if remaining <= ZERO:
            break
        if dec(lot["units"]) == ZERO:
            continue
        if date_string < lot["redeemable_from"]:
            stopped = "LOT_NOT_REDEEMABLE"
            break
        days = (day(date_string) - day(lot["confirmed_date"])).days
        if days < 0:
            stopped = "LOT_CONFIRMATION_IN_FUTURE"
            break
        rate = next(dec(tier["rate"]) for tier in reversed(fee["redemption"])
                    if days >= tier["min_days"])
        if rate > fee_limit:
            stopped = "REDEMPTION_FEE_LIMIT"
            break
        take = min(remaining, dec(lot["units"]))
        allocations.append({"lot_id": lot["id"], "units": take,
                            "holding_calendar_days": days, "rate": rate})
        units += take
        remaining -= take
    return units, remaining, allocations, stopped


def reference_plans(weights, details, data):
    p, portfolio, equity = data["policy"], data["portfolio"], data["_equity"]
    assets = {a["id"]: a for a in data["instruments"]}
    holdings = {h["asset_id"]: h for h in portfolio["holdings"]}
    band, large = dec(p["band"]), dec(p["large_gap"])
    cash = max(ZERO, dec(portfolio["cash_settled"]) - dec(portfolio["cash_reserved"]) -
               dec(p["cash_reserve"]))
    daily = max(ZERO, equity * dec(p["max_daily_turnover_fraction"]) -
                dec(portfolio["turnover_used_today"]))
    initial_cash, initial_daily = cash, daily
    items = []
    for key in sorted(weights):
        holding, target = holdings[key], weights[key]
        current = holding["_value"] / equity
        lower, upper = max(ZERO, target - band), min(ONE, target + band)
        displacement = lower - current if current < lower else (upper - current if current > upper else ZERO)
        if displacement == ZERO:
            grade = "HOLD"
        else:
            direction = "INCREASE" if displacement > ZERO else "DECREASE"
            grade = direction + ("_LARGE" if abs(target - current) >= large else "_SMALL")
        item = {"asset_id": key, "current_weight": current, "target_weight": target,
                "target_band": [lower, upper], "signal_action": grade,
                "execution_status": "HOLD" if displacement == ZERO else "REFERENCE_ONLY",
                "reasons": [], "gross_reference": ZERO, "fee_reference": ZERO,
                "units_reference": ZERO, "net_reference": ZERO,
                "reference_nav": dec(holding["mark"]["value"]),
                "reference_nav_date": data["decision_session"],
                "dealing_date_if_applied": data["_dealing_date"],
                "price_basis": "LAST_OFFICIAL_NAV_REFERENCE_NOT_FILL",
                "_displacement": displacement}
        if details[key]["applicability"] != "APPLICABLE":
            item.update(signal_action=None, execution_status="NOT_APPLICABLE",
                        reasons=details[key]["reasons"])
        items.append(item)
    # Sells consume turnover but never replenish spendable cash in this analysis.
    ordered = sorted(items, key=lambda item: (0 if item["_displacement"] < ZERO else 1,
                                             item["asset_id"]))
    for item in ordered:
        if item["execution_status"] in ("HOLD", "NOT_APPLICABLE"):
            continue
        key = item["asset_id"]
        asset, holding = assets[key], holdings[key]
        error = fee_ready(asset, data)
        if holding["pending_application"]:
            error = "PENDING_APPLICATION"
        if error:
            item.update(execution_status="WAIT", reasons=[error])
            continue
        fee = asset["fees"]
        selling = item["_displacement"] < ZERO
        if not fee["redemptions_open" if selling else "subscriptions_open"]:
            item.update(execution_status="BLOCKED", reasons=["DIRECTION_CLOSED"])
            continue
        desired_value = abs(item["_displacement"]) * equity
        cap = min(equity * dec(p["max_trade_fraction"]), daily)
        nav, step = item["reference_nav"], dec(fee["unit_step"])
        if selling:
            available_units = max(ZERO, holding["_units"] - dec(holding["protected_units"]))
            requested = floor_units(min(available_units, min(desired_value, cap) / nav), step)
            units, _, allocations, stopped = redemption_prefix(
                holding, fee, requested, data["_dealing_date"], dec(p["max_redemption_rate"]))
            units = floor_units(units, step)
            # Rerun after step truncation so allocations and transaction-level rounding agree.
            units, _, allocations, _ = redemption_prefix(
                holding, fee, units, data["_dealing_date"], dec(p["max_redemption_rate"]))
            for allocation in allocations:
                allocation["gross_reference"] = money(allocation["units"] * nav)
                allocation["fee_reference"] = money(allocation["gross_reference"] * allocation["rate"])
            gross = sum((a["gross_reference"] for a in allocations), ZERO)
            cost = sum((a["fee_reference"] for a in allocations), ZERO)
            # Per-lot cent rounding can exceed a strict cap: trim whole steps if necessary.
            if gross > floor_money(min(desired_value, cap)):
                low, high = 0, int(units / step)
                while low < high:
                    middle = (low + high + 1) // 2
                    _, _, candidate, _ = redemption_prefix(
                        holding, fee, step * middle, data["_dealing_date"], dec(p["max_redemption_rate"]))
                    amount = sum((money(a["units"] * nav) for a in candidate), ZERO)
                    if amount <= floor_money(min(desired_value, cap)):
                        low = middle
                    else:
                        high = middle - 1
                units, _, allocations, _ = redemption_prefix(
                    holding, fee, step * low, data["_dealing_date"], dec(p["max_redemption_rate"]))
                for allocation in allocations:
                    allocation["gross_reference"] = money(allocation["units"] * nav)
                    allocation["fee_reference"] = money(allocation["gross_reference"] * allocation["rate"])
                gross = sum((a["gross_reference"] for a in allocations), ZERO)
                cost = sum((a["fee_reference"] for a in allocations), ZERO)
            if gross < dec(p["minimum_trade"]):
                item.update(execution_status="WAIT",
                            reasons=[stopped or "SELLABLE_AMOUNT_BELOW_MINIMUM"])
                continue
            item.update(gross_reference=gross, fee_reference=cost, units_reference=units,
                        net_reference=gross - cost, fifo_allocations=allocations,
                        reasons=([stopped] if stopped else []))
            daily -= gross
        else:
            rate = dec(fee["subscription_rate"])
            gross = floor_money(min(desired_value * (ONE + rate), cap, cash))
            minimum = max(dec(p["minimum_trade"]), dec(fee["minimum_subscription"]))
            if gross < minimum:
                item.update(execution_status="WAIT", reasons=["CASH_OR_BUDGET_BELOW_MINIMUM"])
                continue
            cost = money(gross * rate / (ONE + rate))
            net = gross - cost
            units = floor_units(net / nav, step)
            if units <= ZERO:
                item.update(execution_status="WAIT", reasons=["UNIT_STEP_BELOW_MINIMUM"])
                continue
            item.update(gross_reference=gross, fee_reference=cost, units_reference=units,
                        net_reference=net, rounding_difference_reference=net - units * nav)
            cash -= gross
            daily -= gross
    for item in items:
        item.pop("_displacement")
    return {"items": items, "budget": {
        "initial_spendable_cash": initial_cash, "remaining_spendable_cash": cash,
        "initial_turnover_budget": initial_daily, "remaining_turnover_budget": daily,
        "sale_proceeds_reused": False,
        "allocation_order": "SELLS_THEN_BUYS_EACH_BY_ASSET_ID",
        "independent_counterfactual_budget": True
    }}
