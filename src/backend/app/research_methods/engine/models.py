"""Three independent target generators; none authorizes a transaction."""
from .domain import ZERO, ONE, dec, exact_values, require

LABELS = {"TREND": "趋势跟随", "VALUE": "估值布局", "REBALANCE": "仓位再平衡"}


def supported(asset, method, dealing_date):
    if asset["market_scope"] != "CN_DOMESTIC":
        return False, "MARKET_MAPPING_UNSUPPORTED"
    if not (asset["eligible_from"] <= dealing_date and
            (asset["eligible_to"] is None or dealing_date < asset["eligible_to"])):
        return False, "OUTSIDE_ELIGIBILITY_WINDOW"
    kinds = {"TREND": ("PASSIVE_EQUITY", "PASSIVE_GOLD"),
             "VALUE": ("PASSIVE_EQUITY",),
             "REBALANCE": ("PASSIVE_EQUITY", "PASSIVE_GOLD", "ENHANCED_EQUITY",
                           "ACTIVE_EQUITY", "BOND")}
    return (True, None) if asset["kind"] in kinds[method] else (False, "PRODUCT_KIND_NOT_APPLICABLE")


def percentile(values, current):
    require(bool(values), "Percentile history is empty")
    n = dec(str(len(values)))
    less = sum(1 for value in values if value < current)
    equal = sum(1 for value in values if value == current)
    return (dec(str(less)) + dec(str(equal)) / dec("2")) / n


def value_feature(asset, data, config):
    """Current completed-month value is compared only to prior completed months."""
    sessions, anchor = data["calendar"]["sessions"], data["_anchor"]
    ends = {}
    for d in sessions:
        if d <= anchor:
            ends[d[:7]] = d
    ordered = sorted(ends)
    history_months = ordered[-(config["maximum_months"] + 1):-1]
    current_month = anchor[:7]
    rows = {r["date"]: r for r in asset["valuation_series"]}
    if anchor not in rows:
        return None, ["VALUATION_ANCHOR_MISSING"]
    require(ends[current_month] == anchor, "Valuation anchor is not month-end")
    expected = [ends[m] for m in history_months]
    if len(expected) < config["minimum_months"] or any(d not in rows for d in expected):
        return None, ["VALUATION_MONTH_HISTORY_INCOMPLETE"]
    current = rows[anchor]
    scores, components = [], {}
    for metric in ("pe", "pb", "dividend_yield"):
        values = [rows[d].get(metric) for d in expected]
        value = current.get(metric)
        if value is None or any(v is None for v in values):
            continue
        parsed = [dec(v, ZERO) for v in values]
        x = dec(value, ZERO)
        # Zero PE/PB cannot be interpreted as cheap. Zero dividend yield is valid.
        if metric != "dividend_yield" and (x <= ZERO or min(parsed) <= ZERO):
            continue
        if any(rows[d]["methodology_id"] != current["methodology_id"] for d in expected):
            continue
        p = percentile(parsed, x)
        score = ONE - p if metric == "dividend_yield" else p
        scores.append(score)
        components[metric] = score
    if len(scores) < config["minimum_metrics"]:
        return None, ["INSUFFICIENT_CONSISTENT_VALUATION_METRICS"]
    expensive = sum(scores, ZERO) / dec(str(len(scores)))
    bucket = sum(expensive >= dec(edge) for edge in config["percentile_edges"])
    return {"expensive_percentile": expensive, "components": components,
            "history_months": len(expected),
            "multiplier": dec(config["multipliers"][bucket])}, []


def generate(method, data, config):
    policy, equity = data["policy"], data["_equity"]
    assets = {a["id"]: a for a in data["instruments"]}
    holdings = {h["asset_id"]: h for h in data["portfolio"]["holdings"]}
    current = {key: holdings[key]["_value"] / equity for key in assets}
    raw = dict(current)
    details, eligible, locked = {}, [], set()
    for key in sorted(assets):
        ok, reason = supported(assets[key], method, data["_dealing_date"])
        details[key] = {"applicability": "APPLICABLE" if ok else "NOT_APPLICABLE",
                        "reasons": [] if ok else [reason]}
        if ok:
            eligible.append(key)
        else:
            locked.add(key)
    if not eligible:
        return {"status": "NOT_APPLICABLE", "reasons": ["NO_APPLICABLE_INSTRUMENTS"],
                "details": details, "raw_weights": None, "locked": locked}
    reasons = []
    if method == "TREND":
        n = config["trend"]["lookback_sessions"]
        sessions = data["calendar"]["sessions"]
        end = sessions.index(data["_anchor"])
        if end < n:
            reasons.append("TREND_CALENDAR_HISTORY_SHORT")
        else:
            dates = sessions[end - n:end + 1]
            cash, missing = exact_values(data["cash_benchmark"], dates)
            reasons.extend(["CASH_BENCHMARK:" + x for x in missing])
            candidates = []
            for key in eligible:
                values, missing = exact_values(assets[key], dates)
                if missing:
                    reasons.extend([key + ":" + x for x in missing])
                    continue
                total_return = values[-1] / values[0] - ONE
                excess = total_return - (cash[-1] / cash[0] - ONE) if cash else None
                details[key].update(total_return=total_return, excess_vs_cash=excess)
                if excess is not None and excess > dec(config["trend"]["minimum_excess_return"]):
                    candidates.append((total_return, key))
            if not reasons:
                winners = [key for _, key in sorted(candidates, key=lambda x: (-x[0], x[1]))
                           [:config["trend"]["top_k"]]]
                remaining = max(ZERO, dec(policy["max_invested_weight"]) -
                                sum((current[k] for k in locked), ZERO))
                for key in eligible:
                    raw[key] = remaining / dec(str(len(winners))) if key in winners else ZERO
                    details[key]["selected"] = key in winners
    elif method == "VALUE":
        for key in eligible:
            feature, missing = value_feature(assets[key], data, config["value"])
            if missing:
                reasons.extend([key + ":" + x for x in missing])
            else:
                details[key].update(feature)
                raw[key] = dec(policy["target_weights"][key]) * feature["multiplier"]
    else:
        for key in eligible:
            raw[key] = dec(policy["target_weights"][key])
    return {"status": "WAIT" if reasons else ("PARTIAL" if locked else "READY"),
            "reasons": reasons, "details": details,
            "raw_weights": None if reasons else raw, "locked": locked}
