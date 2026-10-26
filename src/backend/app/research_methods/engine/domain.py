"""Point-in-time preparation and Decimal primitives; prices are not filled."""
import copy
import hashlib
import json
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_DOWN, ROUND_HALF_UP

ZERO, ONE = Decimal("0"), Decimal("1")
CENT = Decimal("0.01")
CN = timezone(timedelta(hours=8))


class InputError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise InputError(message)


def dec(value, minimum=None, maximum=None):
    require(isinstance(value, str), "Financial numbers must be decimal strings")
    try:
        result = Decimal(value)
    except InvalidOperation:
        raise InputError("Invalid decimal: " + value)
    require(result.is_finite(), "Non-finite decimal")
    if minimum is not None:
        require(result >= minimum, "Decimal below minimum: " + value)
    if maximum is not None:
        require(result <= maximum, "Decimal above maximum: " + value)
    return result


def positive(value):
    result = dec(value, ZERO)
    require(result > ZERO, "Expected positive decimal")
    return result


def stamp(value):
    require(isinstance(value, str), "Timestamp must be a string")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise InputError("Invalid timestamp: " + value)
    require(result.tzinfo is not None, "Timezone required: " + value)
    return result


def day(value):
    try:
        return date.fromisoformat(value)
    except (ValueError, TypeError):
        raise InputError("Invalid date")


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def number(value):
    if value == ZERO:
        return "0"
    return format(value.normalize(), "f")


def money(value):
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def floor_money(value):
    return value.quantize(CENT, rounding=ROUND_DOWN)


def floor_units(value, step):
    return (value / step).to_integral_value(rounding=ROUND_DOWN) * step


def serial(value):
    if isinstance(value, Decimal):
        return number(value)
    if isinstance(value, dict):
        return {key: serial(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [serial(item) for item in value]
    return value


def available(record, as_of):
    published = stamp(record["published_at"])
    observed = stamp(record["observed_at"])
    require(observed >= published, "Observed timestamp precedes publication")
    return max(published, observed) <= as_of


def visible_rows(rows, as_of, cutoff, valuation=False):
    """Last available revision by observed_at; ambiguous equal clocks are rejected."""
    chosen = {}
    for row in rows:
        d = day(row["date"])
        require(stamp(row["published_at"]) >= datetime.combine(d, time(15), CN),
                "Completed-session evidence cannot be published before its close")
        if valuation:
            for metric in ("pe", "pb", "dividend_yield"):
                if row.get(metric) is not None:
                    dec(row[metric], ZERO, ONE if metric == "dividend_yield" else None)
        else:
            positive(row["value"])
        if not available(row, as_of) or d > cutoff:
            continue
        previous = chosen.get(row["date"])
        if previous and stamp(previous["observed_at"]) == stamp(row["observed_at"]):
            require(previous == row, "Conflicting revisions at the same observed_at")
        if not previous or stamp(row["observed_at"]) > stamp(previous["observed_at"]):
            chosen[row["date"]] = row
    return [chosen[key] for key in sorted(chosen)]


def exact_values(asset, sessions):
    rows = {row["date"]: row for row in asset["tr_series"]}
    missing = [d for d in sessions if d not in rows]
    if missing:
        return None, ["MISSING_TOTAL_RETURN_SESSION:" + d for d in missing[:3]]
    return [positive(rows[d]["value"]) for d in sessions], []


def month_anchor(sessions, decision):
    earlier = [d for d in sessions if d <= decision and d[:7] < decision[:7]]
    require(bool(earlier), "Calendar does not cover a completed prior month")
    return earlier[-1]


def _source(record, sources, real):
    require(record.get("source_ref") in sources, "Unknown source_ref")
    if real:
        require(sources[record["source_ref"]]["kind"] == "VERIFIED",
                "RESEARCH input cannot use a synthetic source")


def prepare(raw):
    require(isinstance(raw, dict), "Input must be an object")
    data = copy.deepcopy(raw)
    require(data.get("schema_version") == "1.0", "Unsupported schema_version")
    require(data.get("mode") in ("SYNTHETIC", "RESEARCH"), "Unsupported mode")
    require(data.get("base_currency") == "CNY", "Only CNY supported")
    require(isinstance(data.get("snapshot_id"), str) and data["snapshot_id"], "snapshot_id required")
    as_of = stamp(data["as_of"])
    decision = data["decision_session"]
    decision_day = day(decision)
    local = as_of.astimezone(CN)
    require(decision_day <= local.date(), "Future decision_session")
    if decision_day == local.date():
        require(local.time() >= time(15), "A completed daily NAV cannot precede CN close")
    sources = {}
    for source in data["sources"]:
        require(source["id"] not in sources, "Duplicate source id")
        require(source["kind"] in ("SYNTHETIC", "VERIFIED"), "Invalid source kind")
        sources[source["id"]] = source
    real = data["mode"] == "RESEARCH"
    cal = data["calendar"]
    _source(cal, sources, real)
    require(stamp(cal["known_at"]) <= as_of, "Calendar unavailable at as_of")
    require(cal["timezone"] == "Asia/Shanghai" and cal["cutoff"] == "15:00",
            "v01 supports only a verified CN 15:00 dealing calendar")
    sessions = cal["sessions"]
    require(bool(sessions) and sessions == sorted(set(sessions)), "Calendar must be sorted and unique")
    for d in sessions:
        day(d)
    require(cal["coverage_start"] <= sessions[0] and cal["coverage_end"] >= sessions[-1],
            "Calendar coverage inconsistent")
    require(decision in sessions, "decision_session missing from calendar")
    require(cal["coverage_end"] >= local.date().isoformat(), "Calendar coverage expired")
    passed = [d for d in sessions if d < local.date().isoformat()]
    expected = passed[-1] if passed else None
    if local.time() >= time(15) and local.date().isoformat() in sessions:
        expected = local.date().isoformat()
    # One session of NAV publication latency is allowed. A second missing session is stale.
    require(expected is not None, "Calendar does not cover prior sessions")
    require(sessions.index(expected) - sessions.index(decision) <= 1,
            "decision_session is stale by more than one market session")
    next_dates = [d for d in sessions
                  if datetime.combine(day(d), time(15), CN) > as_of]
    require(bool(next_dates), "Next dealing session unknown")
    data["_dealing_date"] = next_dates[0]
    data["_valid_until"] = datetime.combine(day(next_dates[0]), time(15), CN).isoformat()
    data["_anchor"] = month_anchor(sessions, decision)
    benchmark = data["cash_benchmark"]
    _source(benchmark, sources, real)
    require(benchmark["currency"] == "CNY" and
            benchmark["series_kind"] == "CNY_CASH_TOTAL_RETURN", "CNY cash total return required")
    for row in benchmark["tr_series"]:
        _source(row, sources, real)
    benchmark["tr_series"] = visible_rows(benchmark["tr_series"], as_of, decision_day)
    require(all(row["date"] in sessions for row in benchmark["tr_series"]),
            "Cash return observation outside declared calendar")
    policy = data["policy"]
    _source(policy, sources, real)
    require(stamp(policy["known_at"]) <= as_of, "Policy unavailable")
    require(policy["authority"] == ("USER_CONFIGURED" if real else "SYNTHETIC_EXAMPLE"),
            "Policy authority does not match input mode")
    for field in ("max_invested_weight", "max_asset_weight", "band", "large_gap",
                  "max_trade_fraction", "max_daily_turnover_fraction", "max_redemption_rate"):
        dec(policy[field], ZERO, ONE)
    require(dec(policy["large_gap"]) >= dec(policy["band"]), "large_gap must cover band")
    positive(policy["volatility_limit"])
    dec(policy["cash_reserve"], ZERO)
    positive(policy["minimum_trade"])
    for value in policy["max_group_weights"].values():
        dec(value, ZERO, ONE)
    portfolio = data["portfolio"]
    _source(portfolio, sources, real)
    require(stamp(portfolio["known_at"]) <= as_of, "Portfolio unavailable")
    settled = dec(portfolio["cash_settled"], ZERO)
    require(dec(portfolio["cash_reserved"], ZERO) <= settled, "Reserved cash exceeds settled cash")
    dec(portfolio["turnover_used_today"], ZERO)
    assets = data["instruments"]
    ids, exposures = set(), set()
    for asset in assets:
        require(asset["id"] not in ids, "Duplicate instrument id")
        require(asset["exposure_id"] not in exposures, "Duplicate exposure; choose one share class/vehicle")
        ids.add(asset["id"])
        exposures.add(asset["exposure_id"])
        _source(asset, sources, real)
        require(stamp(asset["identity_known_at"]) <= as_of, "Instrument identity unavailable")
        require(asset["currency"] == "CNY", "FX conversion must precede this draft")
        day(asset["eligible_from"])
        if asset["eligible_to"] is not None:
            require(day(asset["eligible_to"]) > day(asset["eligible_from"]), "Invalid eligibility interval")
        require(asset["risk_group"] in policy["max_group_weights"], "Risk group cap missing")
        require(asset["series_kind"] == "FUND_TOTAL_RETURN",
                "Index price or raw unit NAV cannot substitute for fund total return")
        require(asset["corporate_actions_verified"] is True, "Distribution/split basis unverified")
        for key in ("tr_series", "valuation_series"):
            for row in asset[key]:
                _source(row, sources, real)
        asset["tr_series"] = visible_rows(asset["tr_series"], as_of, decision_day)
        asset["valuation_series"] = visible_rows(asset["valuation_series"], as_of, decision_day, True)
        require(all(row["date"] in sessions for row in asset["tr_series"]),
                "NAV observation outside declared calendar")
        asset["valuation_series"] = [r for r in asset["valuation_series"]
                                     if r["date"] <= data["_anchor"]]
        quote = asset.get("intraday_reference")
        asset["intraday_reference"] = None
        if quote is not None:
            _source(quote, sources, real)
            positive(quote["value"])
            positive(quote["baseline_nav"])
            day(quote["date"])
            day(quote["baseline_nav_date"])
            quote_time, quote_seen = stamp(quote["as_of"]), stamp(quote["observed_at"])
            require(quote_seen >= quote_time, "Quote observation precedes quote time")
            if max(quote_time, quote_seen) <= as_of:
                asset["intraday_reference"] = quote
        fee = asset.get("fees")
        if fee is not None:
            _source(fee, sources, real)
            if stamp(fee["known_at"]) > as_of:
                asset["fees"] = None
                continue
            day(fee["effective_from"])
            if fee["effective_to"] is not None:
                require(day(fee["effective_to"]) > day(fee["effective_from"]), "Invalid fee interval")
            dec(fee["subscription_rate"], ZERO, ONE)
            positive(fee["unit_step"])
            positive(fee["minimum_subscription"])
            tiers = fee["redemption"]
            require(tiers and tiers[0]["min_days"] == 0, "Redemption table must start at day zero")
            require([x["min_days"] for x in tiers] == sorted(set(x["min_days"] for x in tiers)),
                    "Redemption tiers must be strictly ascending")
            for tier in tiers:
                require(type(tier["min_days"]) is int and tier["min_days"] >= 0,
                        "Invalid redemption holding period")
                dec(tier["rate"], ZERO, ONE)
            require(fee["redemption_order"] == "FIFO" and
                    fee["holding_days_basis"] == "CONFIRM_TO_DEALING_DATE",
                    "Unsupported fee or lot convention")
    require(set(policy["target_weights"]) == ids, "Target weights must name every instrument")
    require(sum((dec(v, ZERO, ONE) for v in policy["target_weights"].values()), ZERO) <= ONE,
            "Strategic weights exceed one")
    holdings = portfolio["holdings"]
    require({h["asset_id"] for h in holdings} == ids and len(holdings) == len(ids),
            "Exactly one holdings record per instrument is required, including zero positions")
    equity = settled
    for holding in holdings:
        require(type(holding["pending_application"]) is bool, "pending_application must be boolean")
        mark = holding["mark"]
        _source(mark, sources, real)
        require(mark["kind"] == "OFFICIAL_NAV" and mark["date"] == decision,
                "Holdings must use official NAV on decision_session")
        require(available(mark, as_of), "NAV mark unavailable")
        require(stamp(mark["published_at"]) >= datetime.combine(day(mark["date"]), time(15), CN),
                "Official NAV publication precedes its close")
        nav = positive(mark["value"])
        units, lot_ids = ZERO, set()
        for lot in holding["lots"]:
            require(lot["id"] not in lot_ids, "Duplicate lot id")
            lot_ids.add(lot["id"])
            units += dec(lot["units"], ZERO)
            if lot["confirmed_date"] is not None:
                require(day(lot["confirmed_date"]) <= local.date(), "Future lot confirmation")
            if lot["redeemable_from"] is not None:
                day(lot["redeemable_from"])
        require(dec(holding["protected_units"], ZERO) <= units, "Protected units exceed holdings")
        holding["_units"] = units
        holding["_value"] = units * nav
        equity += holding["_value"]
    require(equity > ZERO, "Positive portfolio equity required")
    data["_equity"] = equity
    data["_as_of"] = as_of
    return data
