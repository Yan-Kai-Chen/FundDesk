"""Bounded, read-only risk observations for the funds registered in this app.

Categories are the user's navigation preferences, not a look-through holdings
model. Correlation uses completed, matching return intervals; a missing value
never becomes a zero holding or a fabricated daily return.
"""
from datetime import date
from decimal import Decimal, InvalidOperation, localcontext
from itertools import combinations

from app.domain.daily_agenda import daily_agenda
from app.domain.finance import Problem, SHANGHAI, ZERO, stamp


METHOD_VERSION = "registered-portfolio-risk-v1"
MIN_SAMPLES, MAX_SAMPLES = 20, 60
FORMAL_QUALITIES = {"DISTRIBUTOR_DISCLOSURE", "MANAGER_DISCLOSURE"}


def _number(value, *, positive=False):
    if not isinstance(value, (str, int, Decimal)) or isinstance(value, bool):
        return None
    try:
        value = Decimal(value)
        return value if value.is_finite() and (value > 0 if positive else value >= 0) else None
    except (InvalidOperation, ValueError):
        return None


def _text(value):
    return None if value is None else format(Decimal(value), "f")


def _ratio(value):
    return None if value is None else format(value.quantize(Decimal("0.00000001")), "f")


def _visible_at(value, now):
    try:
        return bool(value) and stamp(value) <= stamp(now)
    except (Problem, ValueError, TypeError):
        return False


def _valid_day(value):
    try:
        return isinstance(value, str) and date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def _series(fund, data, now):
    """Use at most 61 verified levels, then construct interval identities.

    No forward filling: two returns ending on the same day may nevertheless
    cover different market holidays, so both start and end must match.
    """
    nav = fund.get("nav") or {}
    if nav.get("quality") not in FORMAL_QUALITIES or not _visible_at(nav.get("observed_at"), now):
        return {}, {"status": "FORMAL_REQUIRED", "label": "等待正式历史", "basis": None}
    if not _valid_day(nav.get("effective_date")):
        return {}, {"status": "HISTORY_INVALID", "label": "历史日期待核对", "basis": None}
    limit = min(nav["effective_date"], stamp(now).astimezone(SHANGHAI).date().isoformat())
    levels, basis, reason = [], None, None
    if (data and data.get("fund_code") == fund.get("fund_code") and fund.get("share_class") == "C" and data.get("series_kind") == "FUND_TOTAL_RETURN" and
            data.get("corporate_actions_verified") is True and _visible_at(data.get("observed_at"), now)):
        raw_rows = data.get("rows", [])
        if not isinstance(raw_rows, list) or any(not isinstance(row, list) or len(row) < 3 or not _valid_day(row[0]) for row in raw_rows[-(MAX_SAMPLES + 1):]):
            return {}, {"status": "HISTORY_INVALID", "label": "总回报历史待核对", "basis": None}
        rows = [row for row in raw_rows if isinstance(row, list) and len(row) >= 3 and _valid_day(row[0]) and row[0] <= limit]
        rows = rows[-(MAX_SAMPLES + 1):]
        for row in rows:
            value = _number(row[2], positive=True)
            if not _valid_day(row[0]) or value is None or levels and row[0] <= levels[-1][0]:
                return {}, {"status": "HISTORY_INVALID", "label": "总回报历史待核对", "basis": None}
            levels.append((row[0], value))
        basis = "VERIFIED_TOTAL_RETURN"
    else:
        sync = fund.get("nav_sync") or {}
        rows = [row for row in fund.get("history", []) if row.get("value_kind") == "OFFICIAL_NAV" and
                row.get("effective_date", "") <= limit and _visible_at(row.get("observed_at"), now)]
        rows = rows[-(MAX_SAMPLES + 1):]
        if sync.get("corporate_actions_complete") is not True:
            reason = {"status": "ACTIONS_UNVERIFIED", "label": "分红与拆分资料待核对", "basis": None}
        elif any(row.get("corporate_action") or row.get("corporate_actions") or row.get("adjustment_factor") for row in rows):
            reason = {"status": "TOTAL_RETURN_REQUIRED", "label": "有分红或拆分，等待总回报历史", "basis": None}
        elif any(not isinstance(action, dict) or not _valid_day(action.get("effective_date") or action.get("date")) or
                 (rows[0]["effective_date"] if rows else limit) <= (action.get("effective_date") or action.get("date")) <= limit
                 for action in sync.get("corporate_actions", [])):
            reason = {"status": "TOTAL_RETURN_REQUIRED", "label": "有分红或拆分，等待总回报历史", "basis": None}
        if reason:
            return {}, reason
        for row in rows:
            value = _number(row.get("value"), positive=True)
            if (row.get("quality") not in FORMAL_QUALITIES or not _valid_day(row.get("effective_date")) or value is None or
                    levels and row["effective_date"] <= levels[-1][0]):
                return {}, {"status": "HISTORY_INVALID", "label": "正式净值历史待核对", "basis": None}
            levels.append((row["effective_date"], value))
        basis = "OFFICIAL_NAV_NO_ACTIONS"
    if not levels:
        return {}, {"status": "HISTORY_MISSING", "label": "等待历史资料", "basis": basis}
    if (date.fromisoformat(limit) - date.fromisoformat(levels[-1][0])).days > 10:
        return {}, {"status": "HISTORY_STALE", "label": "历史较旧，等待更新", "basis": basis, "end": levels[-1][0]}
    returns = {(before[0], after[0]): after[1] / before[1] - 1 for before, after in zip(levels, levels[1:])}
    return returns, {"status": "READY" if len(returns) >= MIN_SAMPLES else "INSUFFICIENT",
                     "label": "总回报历史" if basis == "VERIFIED_TOTAL_RETURN" else "正式净值历史（窗口无企业行动）",
                     "basis": basis, "start": levels[0][0], "end": levels[-1][0], "count": len(returns)}


def _correlation(first, second):
    intervals = sorted(first.keys() & second.keys())[-MAX_SAMPLES:]
    result = {"status": "INSUFFICIENT", "correlation": None, "sample_count": len(intervals),
              "start": intervals[0][0] if intervals else None, "end": intervals[-1][1] if intervals else None,
              "label": f"共同区间不足 {MIN_SAMPLES} 期"}
    if len(intervals) < MIN_SAMPLES:
        return result
    with localcontext() as context:
        context.prec = 38
        x, y = [first[key] for key in intervals], [second[key] for key in intervals]
        mx, my = sum(x) / len(x), sum(y) / len(y)
        xx, yy = sum((v-mx)**2 for v in x), sum((v-my)**2 for v in y)
        if xx == ZERO or yy == ZERO:
            return dict(result, status="CONSTANT_SERIES", label="波动不足，相关性未定义")
        corr = sum((a-mx)*(b-my) for a, b in zip(x, y)) / (xx * yy).sqrt()
        corr = min(Decimal(1), max(Decimal(-1), corr))
        return dict(result, status="READY", correlation=_ratio(corr),
                    label="高度同向" if corr >= Decimal("0.8") else "高度反向" if corr <= Decimal("-0.8") else "走势相关")


def portfolio_risk(snapshot, references, datasets=None):
    """All amounts and ratios leave this pure function as decimal strings."""
    datasets = datasets or {}
    catalog = snapshot.get("fund_sidebar") or {}
    names = {group["id"]: group["name"] for group in catalog.get("groups", [])}
    sidebars = {row["fund_id"]: row for row in catalog.get("funds", [])}
    held, totals, all_funds = [], {}, []
    for fund in snapshot.get("funds", []):
        sidebar = sidebars.get(fund["id"], {})
        group_id = sidebar.get("group_id") if sidebar.get("group_id") in names else "unclassified"
        copy = dict(fund, sidebar=sidebar)
        all_funds.append(copy)
        units = _number(fund.get("units"))
        if units == ZERO and not fund.get("initialization_pending"):
            continue
        valuation = fund.get("return_valuation") or {}
        value = _number(valuation.get("holding_value"))
        if fund.get("initialization_pending") or units is None or valuation.get("status") == "HOLDINGS_REVIEW":
            value = None
        item = {"fund_id": fund["id"], "fund_code": fund.get("fund_code"),
                "name": sidebar.get("display_name") or fund.get("name", "基金"), "group_id": group_id,
                "value": _text(value), "weight": None, "valuation_date": valuation.get("date"),
                "valuation_label": valuation.get("label"), "is_estimate": bool(valuation.get("is_estimate")),
                "unknown_reason": "持仓待核对" if fund.get("initialization_pending") or units is None else "持仓估值待补" if value is None else None}
        held.append(item)
        group = totals.setdefault(group_id, {"group_id": group_id, "name": names.get(group_id, "未分类"),
                       "fund_ids": [], "known_subtotal": ZERO, "unknown_fund_ids": [], "value": None, "weight": None})
        group["fund_ids"].append(fund["id"])
        if value is None:
            group["unknown_fund_ids"].append(fund["id"])
        else:
            group["known_subtotal"] += value
    known = sum((group["known_subtotal"] for group in totals.values()), ZERO)
    missing = [row["fund_id"] for row in held if row["value"] is None]
    complete = not missing
    for group in totals.values():
        value = group["known_subtotal"] if not group["unknown_fund_ids"] else None
        group["value"] = _text(value)
        group["weight"] = _ratio(value/known) if complete and known > ZERO else None
        group["known_subtotal"] = _text(group["known_subtotal"])
    for row in held:
        row["weight"] = _ratio(Decimal(row["value"])/known) if complete and known > ZERO else None
    order = {group["id"]: index for index, group in enumerate(catalog.get("groups", []))}
    groups = sorted(totals.values(), key=lambda group: order.get(group["group_id"], len(order)))
    largest = max(groups, key=lambda group: Decimal(group["weight"])) if complete and known > ZERO else None
    hhi = sum((Decimal(group["value"])**2 / known**2 for group in groups), ZERO) if largest else None
    agenda = daily_agenda(dict(snapshot, funds=all_funds), references)
    actions = agenda["actions"]
    buys = [row for row in actions if row["action"] == "BUY"]
    sells = [row for row in actions if row["action"] == "SELL"]
    def total(rows, key):
        values = [_number(row.get(key)) for row in rows]
        return _text(sum(values, ZERO)) if all(value is not None for value in values) else None
    buy_total, sell_total = total(buys, "amount"), total(sells, "amount")
    sell_net, fees = total(sells, "net"), total(actions, "fee")
    pair_rows, history = [], {}
    held_ids = {row["fund_id"] for row in held}
    held_funds = [fund for fund in all_funds if fund["id"] in held_ids]
    series = {}
    for fund in held_funds:
        series[fund["id"]], history[fund["id"]] = _series(fund, datasets.get(fund.get("fund_code")), snapshot["now"])
    for first, second in combinations(held_funds, 2):
        ids = [first["id"], second["id"]]
        row = _correlation(series[ids[0]], series[ids[1]])
        if any(history[fid]["status"] not in {"READY", "INSUFFICIENT"} for fid in ids):
            row.update(status="HISTORY_REQUIRED", label="历史资料待核对", correlation=None)
        row.update(fund_ids=ids, names=[(fund.get("sidebar") or {}).get("display_name") or fund.get("name", "基金") for fund in (first, second)],
                   basis=[history[fid]["basis"] for fid in ids])
        pair_rows.append(row)
    pair_rows.sort(key=lambda row: (row["correlation"] is None, -(Decimal(row["correlation"]) if row["correlation"] is not None else ZERO)))
    basis = []
    by_fund = {fund["id"]: fund for fund in all_funds}
    for row in agenda["snapshot_basis"]:
        fund = by_fund[row["fund_id"]]
        valuation = fund.get("return_valuation") or {}
        basis.append(dict(row, units=_text(_number(row.get("units"))), nav_id=(fund.get("nav") or {}).get("id"), valuation_quote_id=valuation.get("quote_id"),
                          holding_value=_text(_number(valuation.get("holding_value")))))
    return {"method_version": METHOD_VERSION, "scope": "REGISTERED_CONFIRMED_HOLDINGS_ONLY", "date": snapshot.get("business_date"),
            "generated_at": snapshot["now"], "revision": snapshot.get("revision"), "sidebar_version": catalog.get("_version", 0),
            "snapshot_basis": basis,
            "allocation": {"status": "READY" if complete else "PARTIAL", "total_value": _text(known) if complete else None,
                "known_subtotal": _text(known), "missing_fund_ids": missing, "holding_count": len(held),
                "estimate_count": sum(row["is_estimate"] for row in held), "groups": groups, "funds": held,
                "largest_group": {"group_id": largest["group_id"], "name": largest["name"], "weight": largest["weight"]} if largest else None,
                "category_hhi": _ratio(hhi)},
            "daily_operations": {"action_count": len(actions), "buy_count": len(buys), "sell_count": len(sells),
                "buy_amount": buy_total, "sell_gross": sell_total, "sell_net": sell_net, "estimated_fees": fees,
                "net_subscription": _text(Decimal(buy_total)-Decimal(sell_net)) if buy_total is not None and sell_net is not None else None,
                "expires_at": min((row["expires_at"] for row in actions), key=stamp) if actions else None,
                "actions": [{key: (_text(row.get(key)) if key in {"amount", "units", "fee", "net"} else row.get(key))
                             for key in ("fund_id", "fund_name", "action", "amount", "units", "fee", "net", "expires_at")} for row in actions]},
            "correlations": {"minimum_samples": MIN_SAMPLES, "maximum_samples": MAX_SAMPLES,
                "ready_count": sum(row["status"] == "READY" for row in pair_rows), "pairs": pair_rows, "history": history,
                "notice": "按相同起止日期的最近最多60期正式收益计算；至少20期。相关性仅描述历史走势，不代表实际重仓重叠或未来走势。"},
            "notice": "仅含App已确认基金持仓，不含个人总资产、未确认买入及现金。分类由你编辑，属于观察分组，不是基金真实资产穿透。"}
