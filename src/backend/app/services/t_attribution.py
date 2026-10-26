"""Conditional screenshot reconstruction and user-selectable T attribution.

This analysis never posts ledger events. Pairing changes cost attribution only.
"""
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal

from app.domain.finance import (ZERO, Problem, dec, day, money, require,
                                purchase_preview, redemption_preview)
from app.schemas.asset_import import transaction_metadata

METHODS = {"RECENT_BUY_FIRST", "NEXT_BUY_FIRST", "MANUAL", "NONE"}
STEP = Decimal("0.01")


def take_value(total, take, remaining):
    return total if take == remaining else money(total * take / remaining)


def reference_redemption(lots, units, nav, rule, on):
    result = redemption_preview(lots, units, nav, rule, on)
    # Whole-order rounding for one tier; group by tier for mixed-tier estimates.
    # This is an explicitly conditional model, never a channel confirmation.
    groups = {}
    for a in result["allocations"]:
        groups[a["rate"]] = groups.get(a["rate"], ZERO)+a["units"]
    gross = money(units * nav)
    fee = sum((money(money(group_units*nav)*rate) for rate,group_units in groups.items()),ZERO)
    return dict(result, gross=gross, fee=fee, net=gross-fee)


def infer_units(lots, target, nav, rule, on):
    total = sum((l["units"] for l in lots if l["available_from"] <= on), ZERO)
    low, high = 1, int(total / STEP)
    require(high > 0, "REFERENCE_UNITS_MISSING", "卖出之前缺少足够的已还原份额")
    while low < high:
        mid = (low+high)//2
        if reference_redemption(lots, STEP*mid, nav, rule, on)["net"] >= target:
            high = mid
        else:
            low = mid+1
    result = reference_redemption(lots, STEP*low, nav, rule, on)
    require(result["net"] == target, "REFERENCE_NET_UNMATCHED", "列表卖出金额无法按当前规则还原，请补充确认份额")
    following = None
    if STEP*(low+1) <= total:
        other = reference_redemption(lots, STEP*(low+1), nav, rule, on)
        if other["net"] == target:
            following = other["units"]
    return dict(result, alternative_units=following)


def reconstruct(statement, history, rule):
    require(statement.get("history_complete"), "HISTORY_INCOMPLETE", "需要确认历史完整，才能进行做T收益还原")
    require(rule.get("verified"), "RULES_UNVERIFIED", "请先核对费用规则")
    require(bool(history), "NAV_MISSING", "缺少正式净值，无法还原")
    require(dec(rule.get("unit_step", "0.01")) == STEP, "REFERENCE_STEP_UNSUPPORTED", "当前截图还原使用0.01份步长")
    history = sorted(history, key=lambda r:r["effective_date"])
    quotes = {r["effective_date"]:r for r in history}
    latest = history[-1]
    rows = sorted((r for r in statement["rows"] if r["status"] != "CLOSED"), key=lambda r:r["displayed_at"])
    require(bool(rows) and latest["effective_date"] >= rows[-1]["displayed_at"][:10], "NAV_NOT_COVERED", "最近净值尚未覆盖交易日期")
    lots, buys, sells = [], [], []
    paid, received, realized = ZERO, ZERO, ZERO
    for r in rows:
        on = r["displayed_at"][:10]
        metadata = transaction_metadata(r)
        reference_date = metadata.get('reference_nav_date')
        require(on >= rule.get("effective_from", "0001-01-01"), "REFERENCE_RULE_DATE", "费用规则未覆盖交易日期")
        local_time = r["displayed_at"][11:]
        cutoff = rule.get("cutoff", "15:00:00")
        require(reference_date or local_time < cutoff or (rule.get("cutoff_inclusive") and local_time == cutoff),
                "REFERENCE_CUTOFF", "存在截止时间之后的申请，请先补充实际净值日，不能直接用当日净值还原")
        quote = quotes.get(reference_date or on)
        require(quote is not None, "NAV_MISSING", "缺少交易参考净值：" + (reference_date or on))
        nav = dec(quote["value"], positive=True)
        if r["side"] == "BUY":
            p = purchase_preview(r["value"], nav, rule)
            require(p["units"] > 0, "REFERENCE_UNITS_MISSING", "买入金额不足以还原有效份额")
            acquired = (day(reference_date or on)+timedelta(days=1)).isoformat()
            lot = {"id":r["displayed_at"]+"|"+r["id"],"units":p["units"],"reserved":ZERO,"cost":p["gross"],
                   "acquired":acquired,"available_from":acquired}
            lots.append(lot)
            buys.append({"id":r["id"],"at":r["displayed_at"],"units":p["units"],"cost":p["gross"],"nav":nav,"fee":p["fee"], **metadata})
            paid += p["gross"]
            continue
        sell_value = dec(r["value"], positive=True)
        require(r["unit"] == "CNY" or sell_value % STEP == 0,
                "PAIRING_PRECISION", "当前截图份额还原仅支持两位小数")
        p = (infer_units(lots, sell_value, nav, rule, on) if r["unit"] == "CNY" else
             reference_redemption(lots, sell_value, nav, rule, on))
        cost = sum((a["cost"] for a in p["allocations"]), ZERO)
        for a in p["allocations"]:
            lot = next(l for l in lots if l["id"] == a["lot_id"])
            lot["units"] -= a["units"]
            lot["cost"] -= a["cost"]
        received += p["net"]
        realized += p["net"]-cost
        sells.append({"id":r["id"],"at":r["displayed_at"],"units":p["units"],"net":p["net"],"fee":p["fee"],
            "nav":nav,"fifo_cost":cost,"fifo_pnl":p["net"]-cost,
            "pending":r["status"] == "PENDING_SETTLEMENT", "alternative_units":p.get("alternative_units")})
    units = sum((l["units"] for l in lots), ZERO)
    cost = sum((l["cost"] for l in lots), ZERO)
    value = money(units*dec(latest["value"]))
    total = value+received-paid
    require(total == realized+value-cost, "ATTRIBUTION_INCONSISTENT", "收益拆解未能与总账核对")
    return {"buys":buys,"sells":sells,"remaining_lots":[deepcopy(l) for l in lots if l['units']],"paid":paid,"received":received,"units":units,"holding_value":value,
            "fifo_remaining_cost":cost,"fifo_realized":realized,"fifo_unrealized":value-cost,"total_pnl":total,
            "nav_date":latest["effective_date"],"nav":dec(latest["value"]),"observation_id":latest["id"]}


def auto_pairs(ref, method):
    if method in {"NONE", "MANUAL"}:
        return []
    available = {b["id"]:b["units"] for b in ref["buys"]}
    pairs = []
    for sale in ref["sells"]:
        eligible = [b for b in ref["buys"] if (b["at"] < sale["at"] if method == "RECENT_BUY_FIRST" else b["at"] > sale["at"])]
        eligible.sort(key=lambda b:(b["at"],b["id"]), reverse=method == "RECENT_BUY_FIRST")
        left = sale["units"]
        for b in eligible:
            take = min(left, available[b["id"]])
            if take:
                pairs.append({"buy_id":b["id"],"sell_id":sale["id"],"units":take})
                available[b["id"]] -= take
                left -= take
            if not left:
                break
    return pairs


def attribute(ref, method="RECENT_BUY_FIRST", pairs=None):
    require(isinstance(method, str) and method in METHODS, "INVALID_PAIRING_METHOD", "请选择做T配对方式")
    selected = deepcopy(pairs) if method == "MANUAL" else auto_pairs(ref, method)
    require(isinstance(selected, list) and len(selected) <= 1000, "INVALID_T_PAIRING", "配对明细最多1000行")
    buys = {b["id"]:dict(b, remaining_units=b["units"],remaining_cost=b["cost"]) for b in ref["buys"]}
    sales = {s["id"]:dict(s,remaining_units=s["units"],remaining_net=s["net"],remaining_fee=s["fee"],paired_units=ZERO,paired_cost=ZERO,paired_net=ZERO,pairs=[]) for s in ref["sells"]}
    normalized, seen = [], set()
    for p in selected:
        require(isinstance(p, dict), "INVALID_T_PAIRING", "每行配对应包含买入、卖出和份额")
        require(isinstance(p.get("buy_id"), str) and isinstance(p.get("sell_id"), str)
                and p["buy_id"] in buys and p["sell_id"] in sales,
                "PAIRING_SOURCE_MISSING", "配对引用的买卖交易不存在，请刷新后重新选择")
        units = dec(p.get("units"), positive=True)
        require(units % STEP == 0, "PAIRING_PRECISION", "做T配对份额最多两位小数")
        pair_key = (p["buy_id"],p["sell_id"])
        require(pair_key not in seen, "PAIRING_DUPLICATE", "同一买卖组合请合并为一行")
        seen.add(pair_key)
        b,s = buys[p["buy_id"]],sales[p["sell_id"]]
        require(units <= b["remaining_units"] and units <= s["remaining_units"], "PAIRING_OVERALLOCATED", "同一份额不能重复配对，配对数量超过买入或卖出可用份额")
        cost = take_value(b["remaining_cost"],units,b["remaining_units"])
        net = take_value(s["remaining_net"],units,s["remaining_units"])
        fee = take_value(s["remaining_fee"],units,s["remaining_units"])
        b["remaining_units"] -= units;b["remaining_cost"] -= cost
        s["remaining_units"] -= units;s["remaining_net"] -= net;s["remaining_fee"] -= fee
        s["paired_units"] += units;s["paired_cost"] += cost;s["paired_net"] += net
        item = {"buy_id":b["id"],"sell_id":s["id"],"buy_at":b["at"],"sell_at":s["at"],"units":units,
                "buy_cost":cost,"sell_net":net,"sell_fee":fee,"pnl":net-cost,
                "direction":"BUY_THEN_SELL" if b["at"] < s["at"] else "SELL_THEN_BUY"}
        normalized.append({"buy_id":b["id"],"sell_id":s["id"],"units":units})
        s["pairs"].append(item)
    t_total = sum((s["paired_net"]-s["paired_cost"] for s in sales.values()),ZERO)
    for s in sales.values():
        s["t_pnl"] = s["paired_net"]-s["paired_cost"] if s["paired_units"] else None
    complete = bool(sales) and all(not s["remaining_units"] for s in sales.values())
    residual = ref["total_pnl"]-t_total
    return dict(ref, method=method, pairs=normalized, sell_details=list(sales.values()),
                t_pnl=t_total if normalized else None,residual_pnl=residual,
                residual_label="底仓浮盈亏" if complete else "其余仓位及未配对交易盈亏",
                all_sales_paired=complete,paired_sales_count=sum(not s["remaining_units"] for s in sales.values()),
                sell_count=len(sales),pending_sell_count=sum(s["pending"] for s in sales.values()))
