"""Replayable ledger. Every mutation must replay successfully inside its transaction."""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal

from app.domain.finance import ZERO, amount, dec, money, require, day, stamp, SHANGHAI
from app.domain.buckets import project_buckets


def project(events):
    reversed_ids = set()
    by_id = {e["id"]: e for e in events}
    for e in events:
        if e["kind"] == "REVERSAL":
            target = e["payload"].get("event_id")
            require(target in by_id and by_id[target]["seq"] < e["seq"] and by_id[target]["kind"] != "REVERSAL",
                    "INVALID_REVERSAL", "只能冲正先前的业务事件")
            require(target not in reversed_ids, "ALREADY_REVERSED", "该事件已经冲正")
            require(bool(e["payload"].get("reason", "").strip()), "REASON_REQUIRED", "更正必须填写原因")
            reversed_ids.add(target)

    replacements = {e["payload"]["_replaces"]: e for e in events if e["payload"].get("_replaces")}
    timeline = []
    for original in events:
        if original["kind"] == "REVERSAL" or original["payload"].get("_replaces"):
            continue
        current = original
        while current["id"] in reversed_ids:
            current = replacements.get(current["id"])
            if current is None:
                break
        if current:
            timeline.append(dict(current, id=original["id"], seq=original["seq"]))

    state = {"cash": ZERO, "external_net": ZERO, "opening_basis": ZERO, "lots": {}, "orders": {},
             "dividends": {}, "realized": ZERO, "realized_complete": True,
             "revision": max((e["seq"] for e in events), default=0), "splits": [], "audit": [], "tracking": {}}
    lots, orders = state["lots"], state["orders"]

    def reserve():
        for l in lots.values():
            l["reserved"] = ZERO
        for order in orders.values():
            if order["side"] == "SELL" and not order["closed"]:
                for a in order["allocations"]:
                    require(a["lot_id"] in lots, "DEPENDENT_EVENT", "更正影响了仍被订单使用的批次")
                    lots[a["lot_id"]]["reserved"] += a["units"]

    def newlot(e, p, units, cost, suffix=""):
        id = p.get("lot_id") or e["id"] + suffix
        require(id not in lots, "DUPLICATE_LOT", "批次编号重复")
        acquired, available = p.get("acquired"), p.get("available_from")
        if acquired:
            day(acquired)
            require(acquired <= stamp(e["occurred_at"]).astimezone(SHANGHAI).date().isoformat(), "INVALID_DATE", "已确认批次的起算日不能晚于本次实际确认时间")
        if available:
            day(available)
            require(not acquired or available >= acquired, "INVALID_DATE", "可赎回日期不能早于批次起算日")
        lots[id] = {"id": id, "fund_id": p["fund_id"], "units": units, "original_units": units,
                    "cost": cost, "acquired": acquired, "available_from": available,
                    "reserved": ZERO, "source_event": e["id"], "completeness": "COMPLETE" if cost is not None and acquired and available and p.get("completeness") != "INCOMPLETE" else "INCOMPLETE"}
        if p.get("confirmation_basis"):
            lots[id]["basis"] = p["confirmation_basis"]

    state["audit"] = [{"id": e["id"], "seq": e["seq"], "kind": e["kind"], "payload": deepcopy(e["payload"]),
                       "occurred_at": e["occurred_at"], "recorded_at": e["recorded_at"], "reversed": e["id"] in reversed_ids} for e in events]
    for e in timeline:
        k, p = e["kind"], deepcopy(e["payload"])
        stamp(e["occurred_at"])
        if k in {"CASH_DEPOSIT", "CASH_WITHDRAWAL"}:
            sign = 1 if k == "CASH_DEPOSIT" else -1
            value = amount(p["amount"], positive=True) * sign
            state["cash"] += value
            state["external_net"] += value
        elif k == 'TRACKING_OPENING':
            fid = p['fund_id']
            require(fid not in state['tracking'] and not any(l['fund_id']==fid for l in lots.values()) and
                    not any(o['fund_id']==fid for o in orders.values()), 'HISTORY_OVERLAP', '跟踪起点与既有持仓重叠')
            require(p.get('basis')=='USER_ACCEPTED_RECONSTRUCTION', 'INVALID_BASIS', '跟踪起点需保留资料还原标记')
            physical_cost = ZERO
            for l in p['physical_lots']:
                q, cost = dec(l['units'],positive=True), amount(l['cost'])
                newlot(e,dict(l,fund_id=fid,lot_id=l['id'],completeness='INCOMPLETE'),q,cost)
                lots[l['id']]['basis'] = p['basis']
                physical_cost += cost
            require(sum((l['units'] for l in lots.values() if l['fund_id']==fid),ZERO)==dec(p['units']), 'OPENING_MISMATCH', '跟踪起点份额不一致')
            realized = dec(p['fifo_realized'],nonnegative=False)
            require(physical_cost-realized==dec(p['net_invested'],nonnegative=False), 'OPENING_MISMATCH', '跟踪起点成本与历史收益不一致')
            require(state['opening_basis'] is not None, 'OPENING_MISMATCH', '已有未知成本起点，不能合并初始化')
            state['opening_basis'] += physical_cost-realized
            state['realized'] += realized
            state['tracking'][fid] = dict(p,event_id=e['id'])
        elif k == "OPENING_LOT":
            units = dec(p["units"], positive=True)
            cost = None if p.get("cost") in (None, "") else amount(p["cost"])
            newlot(e, p, units, cost)
            if cost is None or state["opening_basis"] is None:
                state["opening_basis"] = None
            else:
                state["opening_basis"] += cost
        elif k == "ORDER_SUBMITTED":
            id = p["id"]
            require(id not in orders, "DUPLICATE_ORDER", "订单编号重复")
            require(p["side"] in {"BUY", "SELL"}, "INVALID_SIDE", "订单方向须为 BUY 或 SELL")
            require(isinstance(p.get('cash_source', 'TRACKED'), str) and p.get('cash_source', 'TRACKED') in {'TRACKED', 'EXTERNAL'},
                    'INVALID_CASH_SOURCE', '交易资金范围无效')
            external = p.get("external_order_id", "").strip()
            require(not external or all(o.get("external_order_id") != external for o in orders.values()),
                    "DUPLICATE_EXTERNAL_ORDER", "渠道订单号已存在，请选择原订单回填")
            order = dict(p, paid=ZERO, confirmed_gross=ZERO, confirmed_units=ZERO, confirmed_fee=ZERO,
                         confirmed_net=ZERO, settled=ZERO, refunded=ZERO, closed=False,
                         cancel_requested=False, allocations=[], confirmations=[], submitted_at=e["occurred_at"],
                         recorded_at=by_id[e["id"]]["recorded_at"], updated_at=e["recorded_at"])
            order["requested"] = amount(p["requested"], positive=True) if p["side"] == "BUY" else dec(p["requested"], positive=True)
            if p["side"] == "SELL":
                allocations = p.get("allocations", [])
                require(bool(allocations), "ALLOCATION_REQUIRED", "卖出申请需明确占用的实际批次")
                total = ZERO
                seen = set()
                business_date = stamp(e["occurred_at"]).astimezone(SHANGHAI).date().isoformat()
                for a in allocations:
                    require(a["lot_id"] in lots and a["lot_id"] not in seen, "INVALID_LOT", "批次不存在或重复")
                    seen.add(a["lot_id"])
                    lot = lots[a["lot_id"]]
                    q = dec(a["units"], positive=True)
                    require(lot["fund_id"] == p["fund_id"], "INVALID_LOT", "卖出批次与基金不一致")
                    require(not lot.get("available_from") or lot["available_from"] <= business_date,
                            "BEFORE_AVAILABLE_DATE", "所选批次尚未到可赎回日期")
                    require(q <= lot["units"] - lot["reserved"], "UNITS_RESERVED", "份额不足或已被其他订单冻结")
                    order["allocations"].append({"lot_id": a["lot_id"], "units": q})
                    total += q
                require(total == order["requested"], "ALLOCATION_MISMATCH", "批次分配之和必须等于实际申请份额")
            orders[id] = order
        elif k.startswith("ORDER_"):
            require(p.get("order_id") in orders, "ORDER_NOT_FOUND", "关联订单不存在或被历史更正影响")
            order = orders[p["order_id"]]
            side = order["side"]
            if p.get("time_precision") == "DAY":
                ordered = stamp(e["occurred_at"]).astimezone(SHANGHAI).date() >= stamp(order["submitted_at"]).astimezone(SHANGHAI).date()
            else:
                ordered = stamp(e["occurred_at"]) >= stamp(order["submitted_at"])
            require(ordered, "INVALID_TIME", "订单结果的实际发生时间不能早于申请时间")
            if k == "ORDER_PAID":
                require(side == "BUY" and not order["closed"], "INVALID_TRANSITION", "当前订单不能记录申购扣款")
                value = amount(p["amount"], positive=True)
                require(order["paid"] + value <= order["requested"], "PAYMENT_EXCESS", "实际扣款超过已记录申请额，请先核对申请")
                order["paid"] += value
                if order.get('cash_source', 'TRACKED') == 'EXTERNAL':
                    state['external_net'] += value
                else:
                    state["cash"] -= value
            elif k == "ORDER_CONFIRMED":
                require(not order["closed"], "INVALID_TRANSITION", "订单已结束，不能追加确认")
                q, nav = dec(p["units"], positive=True), dec(p["nav"], positive=True)
                gross, fee, net = amount(p["gross"], positive=True), amount(p["fee"]), amount(p["net"])
                require(gross - fee == net, "CONFIRMATION_MISMATCH", "确认总额减费用必须等于确认净额")
                basis = net if side == "BUY" else gross
                require(abs(money(q * nav) - basis) <= Decimal("0.05"), "CONFIRMATION_MISMATCH", "份额×实际净值与确认金额差异超过 0.05 元，请核对")
                day(p["nav_date"])
                confirm_date = p.get("confirm_date") or stamp(e["occurred_at"]).astimezone(SHANGHAI).date().isoformat()
                require(confirm_date <= stamp(e["occurred_at"]).astimezone(SHANGHAI).date().isoformat(), "INVALID_DATE", "确认日期不能晚于本次实际发生时间")
                require(isinstance(p.get("is_final", True), bool), "INVALID_STATE", "最终确认标记必须是布尔值")
                require(p["nav_date"] <= confirm_date, "NAV_DATE_MISMATCH", "净值日期不能晚于确认日期")
                if side == "BUY":
                    require(order["confirmed_gross"] + gross <= order["paid"], "PAYMENT_MISSING", "已确认金额超过记录的实际扣款，请先记录扣款")
                    newlot(e, dict(p, fund_id=order["fund_id"], acquired=confirm_date), q, gross)
                else:
                    require(order["confirmed_units"] + q <= order["requested"], "CONFIRMATION_EXCESS", "确认份额超过申请剩余份额")
                    left, removed = q, ZERO
                    complete = True
                    used = []
                    for a in order["allocations"]:
                        take = min(left, a["units"])
                        if take <= 0:
                            continue
                        lot = lots[a["lot_id"]]
                        require(take <= lot["units"], "UNITS_RESERVED", "确认份额超过批次剩余份额")
                        if lot["cost"] is None:
                            complete = False
                        else:
                            cost = lot["cost"] if take == lot["units"] else money(lot["cost"] * take / lot["units"])
                            lot["cost"] -= cost
                            removed += cost
                        lot["units"] -= take
                        a["units"] -= take
                        left -= take
                        used.append({"lot_id": a["lot_id"], "units": take})
                    require(left == 0, "ALLOCATION_MISMATCH", "确认份额没有完整的申请批次分配")
                    if complete:
                        state["realized"] += net - removed
                    else:
                        state["realized_complete"] = False
                    p["removed_cost"] = removed if complete else None
                    p["allocations"] = used
                order["confirmed_gross"] += gross
                order["confirmed_fee"] += fee
                order["confirmed_net"] += net
                order["confirmed_units"] += q
                order["confirmations"].append(dict(p, event_id=e["id"]))
                if p.get("is_final", True):
                    order["closed"] = True
            elif k == "ORDER_CANCEL_REQUESTED":
                require(not order["closed"], "INVALID_TRANSITION", "订单已结束")
                order["cancel_requested"] = True
            elif k in {"ORDER_CANCELED", "ORDER_REJECTED"}:
                require(not order["closed"], "INVALID_TRANSITION", "订单已结束")
                order["closed"] = True
                order["close_reason"] = k
            elif k == "ORDER_SETTLED":
                require(side == "SELL", "INVALID_TRANSITION", "只有赎回订单可记录到账")
                value = amount(p["amount"], positive=True)
                require(value <= order["confirmed_net"] - order["settled"], "SETTLEMENT_EXCESS", "到账金额超过已确认未到账净额")
                order["settled"] += value
                if order.get('cash_source', 'TRACKED') == 'EXTERNAL':
                    state['external_net'] -= value
                else:
                    state["cash"] += value
            elif k == "ORDER_REFUNDED":
                require(side == "BUY" and order["closed"], "INVALID_TRANSITION", "订单尚未结束或不属于待退款申购")
                value = amount(p["amount"], positive=True)
                require(value <= order["paid"] - order["confirmed_gross"] - order["refunded"], "REFUND_EXCESS", "退款金额超过实际待退款额")
                order["refunded"] += value
                if order.get('cash_source', 'TRACKED') == 'EXTERNAL':
                    state['external_net'] -= value
                else:
                    state["cash"] += value
            else:
                require(False, "INVALID_EVENT", "不支持的订单事件")
        elif k == "DIVIDEND_DECLARED":
            value = amount(p["amount"], positive=True)
            state["dividends"][e["id"]] = dict(p, id=e["id"], amount=value, remaining=value)
        elif k in {"DIVIDEND_PAID", "DIVIDEND_REINVESTED"}:
            require(p.get("dividend_id") in state["dividends"], "DIVIDEND_NOT_FOUND", "分红权益不存在")
            div = state["dividends"][p["dividend_id"]]
            value = amount(p["amount"], positive=True)
            require(value <= div["remaining"], "DIVIDEND_EXCESS", "超过剩余分红权益")
            div["remaining"] -= value
            if k == "DIVIDEND_PAID":
                state["cash"] += value
            else:
                q, nav = dec(p["units"], positive=True), dec(p["nav"], positive=True)
                require(abs(money(q * nav) - value) <= Decimal("0.05"), "CONFIRMATION_MISMATCH", "再投资份额与金额不符")
                newlot(e, dict(p, fund_id=div["fund_id"]), q, value)
        elif k == "SPLIT":
            factor = dec(p["factor"], positive=True)
            for lot in lots.values():
                if lot["fund_id"] == p["fund_id"]:
                    lot["units"] *= factor
                    lot["original_units"] *= factor
            for order in orders.values():
                if order["fund_id"] == p["fund_id"] and side_is_sell(order) and not order["closed"]:
                    remaining = order["requested"] - order["confirmed_units"]
                    order["requested"] = order["confirmed_units"] + remaining * factor
                    for allocation in order["allocations"]:
                        allocation["units"] *= factor
            state["splits"].append(dict(p, seq=e["seq"]))
        else:
            require(False, "INVALID_EVENT", "不支持的账本事件：" + k)
        reserve()
        reserved_cash = sum((max(ZERO, o["requested"] - o["paid"]) for o in orders.values()
                             if o["side"] == "BUY" and not o["closed"] and o.get('cash_source', 'TRACKED') == 'TRACKED'), ZERO)
        require(state["cash"] >= reserved_cash, "INSUFFICIENT_SETTLED_CASH", "可用现金不足；在途款不计入可用现金，请先核对入金/到账记录")
        for lot in lots.values():
            require(lot["units"] >= lot["reserved"] >= 0, "UNITS_RESERVED", "批次份额不足或被重复占用")
            require(lot["cost"] is None or lot["cost"] >= 0, "INVALID_COST", "批次成本不能为负")

    state["reserved_cash"] = sum((max(ZERO, o["requested"] - o["paid"]) for o in orders.values()
                                  if o["side"] == "BUY" and not o["closed"] and o.get('cash_source', 'TRACKED') == 'TRACKED'), ZERO)
    state["available_cash"] = state["cash"] - state["reserved_cash"]
    state["pending_buy"] = sum((o["paid"] - o["confirmed_gross"] - o["refunded"] for o in orders.values()
                                if o["side"] == "BUY" and not o["closed"]), ZERO)
    state["refund_due"] = sum((o["paid"] - o["confirmed_gross"] - o["refunded"] for o in orders.values()
                               if o["side"] == "BUY" and o["closed"]), ZERO)
    state["redemption_due"] = sum((o["confirmed_net"] - o["settled"] for o in orders.values() if o["side"] == "SELL"), ZERO)
    state["dividend_due"] = sum((d["remaining"] for d in state["dividends"].values()), ZERO)
    for o in orders.values():
        o["unpaid_remaining"] = max(ZERO, o["requested"] - o["paid"]) if o["side"] == "BUY" else ZERO
        o["unconfirmed_paid"] = o["paid"] - o["confirmed_gross"] - o["refunded"] if o["side"] == "BUY" else ZERO
        o["refund_due"] = o["unconfirmed_paid"] if o["side"] == "BUY" and o["closed"] else ZERO
        o["unsettled"] = o["confirmed_net"] - o["settled"] if o["side"] == "SELL" else ZERO
        o["remaining_requested"] = o["requested"] - (o["confirmed_gross"] if o["side"] == "BUY" else o["confirmed_units"])
        o["status"] = ("PARTIALLY_CONFIRMED" if o["confirmed_units"] and not o["closed"] else
                       "CONFIRMED" if o["confirmed_units"] and o["closed"] else
                       o.get("close_reason", "").replace("ORDER_", "") if o["closed"] else
                       "CANCEL_REQUESTED" if o["cancel_requested"] else o.get("submission_status") or "SUBMITTED")
    state['bucket_books'] = project_buckets(timeline)
    return state


def side_is_sell(order):
    return order["side"] == "SELL"
