"""Advance registered external trades using disclosed NAV, without placing trades."""
import json

from app.domain.finance import ZERO, Problem, amount, dec, money, require, stamp, SHANGHAI, purchase_preview, redemption_preview
from app.domain.buckets import bucket
from app.domain.order_automation import AUTO_BASIS, POLICY_SOURCES, POLICY_VERSION, schedule, submission_time
from app.persistence.store import Store, digest


def auto_key(order):
    generation = order.get("auto_generation", 0)
    return "auto-confirm:v1:" + order["id"] + (":" + str(generation) if generation else "")


class OrderAutomationMixin:
    def order_progress(self, c, order, state):
        confirmations = order["confirmations"]
        automatic = bool(confirmations and any(p.get("confirmation_basis") == AUTO_BASIS for p in confirmations))
        result = {"automatic": automatic, "basis": AUTO_BASIS if automatic else "CHANNEL_RECORD",
                  "status": "MANUAL", "label": "", "detail": ""}
        if order["closed"] or confirmations:
            if automatic:
                row = next(p for p in confirmations if p.get("confirmation_basis") == AUTO_BASIS)
                result.update(status="AUTO_BOOKED", label="已自动入仓" if order["side"] == "BUY" else "已自动结算",
                              detail="正式净值计算 · 可校正", nav_date=row["nav_date"], nav=row["nav"],
                              confirm_date=row["confirm_date"], available_from=row.get("available_from"),
                              assumption=row.get("auto_assumption"), calendar_basis=row.get("calendar_basis"),
                              sources=POLICY_SOURCES)
                # The active audit ID can differ from the logical lot ID after a correction.
                result["correction_event"] = next((e for e in reversed(state["audit"])
                    if e["kind"] == "ORDER_CONFIRMED" and not e["reversed"]
                    and e["payload"].get("order_id") == order["id"]), None)
                if order["side"] == "SELL":
                    result["detail"] = "收益已计入 · 回款单独记录" if order["unsettled"] else "回款已记录"
            return result, None
        if self.mode != "real" or order.get("cash_source", "TRACKED") != "EXTERNAL" or order.get("auto_confirm") is False:
            return result, None
        if order["cancel_requested"]:
            return dict(result, status="PAUSED", label="撤单处理中", detail="自动入账已暂停"), None
        # Reversing an auto event is an intentional override, not a reason to regenerate it.
        if c.execute("SELECT 1 FROM events WHERE key=?", (auto_key(order),)).fetchone():
            return dict(result, status="OVERRIDDEN", label="已人工调整", detail="自动入账已暂停"), None
        try:
            fund, rule = self.fund(c, order["fund_id"]), self.rules(c, order["fund_id"])
            plan = schedule(order, fund, rule)
            result.update(plan, basis=AUTO_BASIS, sources=POLICY_SOURCES)
            if order["side"] == "BUY" and order["paid"] != order["requested"]:
                return dict(result, status="WAIT_PAYMENT", label="待扣款记录", detail="记录足额扣款后自动处理"), None
            row = c.execute("SELECT id,payload,quality FROM observations WHERE fund_id=? AND value_kind='OFFICIAL_NAV' AND effective_date=? ORDER BY observed_at DESC,rowid DESC LIMIT 1",
                            (order["fund_id"], plan["trade_date"])).fetchone()
            nav = dict(json.loads(row["payload"]), id=row["id"]) if row and row["quality"] in {"DISTRIBUTOR_DISCLOSURE", "MANAGER_DISCLOSURE"} else None
            if self.today() < plan["confirm_date"]:
                return dict(result, status="WAIT_DATE", label="待自动入账",
                            detail=f"{plan['trade_date'][5:].replace('-', '/')}净值 · 预计{plan['confirm_date'][5:].replace('-', '/')}入账"), None
            if nav is None:
                return dict(result, status="WAIT_NAV", label="等待正式净值",
                            detail=f"等待{plan['trade_date'][5:].replace('-', '/')}净值，披露后自动处理"), None
            if order["side"] == "BUY":
                values = purchase_preview(order["requested"], nav["value"], rule)
            else:
                allocated = []
                for a in order["allocations"]:
                    lot = state["lots"][a["lot_id"]]
                    require(lot.get("acquired") and lot.get("available_from")
                            and lot["available_from"] <= plan["trade_date"],
                            "AUTO_LOT_DATES_MISSING", "批次持有期或可赎回日期不完整，等待校正")
                    allocated.append(dict(lot, units=a["units"], reserved=ZERO,
                        cost=None if lot["cost"] is None else money(lot["cost"] * a["units"] / lot["units"])))
                # The holding period excludes redemption confirmation day, includes buy confirmation day.
                values = redemption_preview(allocated, order["requested"], nav["value"], rule, plan["confirm_date"])
                values["gross"] = money(order["requested"] * dec(nav["value"]))
                values["net"] = values["gross"] - values["fee"]
            payload = {k: values[k] for k in ["units", "gross", "fee", "net"]}
            payload.update(order_id=order["id"], nav=nav["value"], nav_date=plan["trade_date"],
                           confirm_date=plan["confirm_date"], available_from=plan["available_from"] if order["side"] == "BUY" else None,
                           is_final=True, confirmation_basis=AUTO_BASIS, auto_policy_version=POLICY_VERSION,
                           nav_observation_id=nav["id"], rule_version=rule["_version"],
                           calendar_basis=plan["calendar_basis"], auto_assumption=plan["assumption"],
                           submission_precision=order.get("time_precision", "EXACT"))
            return dict(result, status="READY", label="即将自动入账", detail="正式净值已取得"), payload
        except Problem as exc:
            return dict(result, status="WAIT_RULES", label="等待规则资料", detail=str(exc), error_code=exc.code), None

    def auto_post_orders(self, c=None):
        if c is None:
            with self.store.transaction() as connection:
                return self.auto_post_orders(connection)
        if self.mode != "real":
            return {"processed": 0, "orders": []}
        state = self.state(c)
        results = []
        for order in list(state["orders"].values()):
            if order["closed"] or order["confirmations"]:
                continue
            progress, payload = self.order_progress(c, order, state)
            if payload:
                c.execute("SAVEPOINT auto_order")
                try:
                    self.event("ORDER_CONFIRMED", payload, auto_key(order), self.now(), c=c)
                    c.execute("RELEASE auto_order")
                    state = self.state(c)
                    progress["status"] = "AUTO_BOOKED"
                except Problem as exc:
                    c.execute("ROLLBACK TO auto_order")
                    c.execute("RELEASE auto_order")
                    progress.update(status="WAIT_RULES", detail=str(exc), error_code=exc.code)
            results.append({"order_id": order["id"], **{k: progress[k] for k in ["status", "detail"]}})
        return {"processed": sum(r["status"] == "AUTO_BOOKED" for r in results), "orders": results, "revision": state["revision"]}

    def edit_order(self, order_id, p):
        """Replace registration and its automatic result atomically, keeping the order ID."""
        with self.store.transaction() as c:
            fingerprint = digest({"order_id": order_id, "request": p})
            saved = Store.doc(c, "order_edit", p["key"])
            if saved:
                require(saved["fingerprint"] == fingerprint, "IDEMPOTENCY_CONFLICT", "同一修改请求不能用于不同内容")
                return {"order_id": order_id, "duplicate": True, "revision": self.state(c)["revision"]}
            state = self.state(c)
            require(order_id in state["orders"], "ORDER_NOT_FOUND", "交易不存在")
            old = state["orders"][order_id]
            require(p.get("expected_revision") == state["revision"] or
                    bool(p.get("expected_order_updated_at") and p["expected_order_updated_at"] == old["updated_at"]),
                    "VERSION_CONFLICT", "交易信息已被修改，请关闭后重新打开核对")
            require(not old.get("close_reason") and not old["cancel_requested"], "ORDER_CLOSED", "撤单或拒绝记录请从原记录更正")
            original = next(e for e in reversed(state["audit"]) if e["kind"] == "ORDER_SUBMITTED"
                            and e["payload"].get("id") == order_id and not e["reversed"])
            fund_id, side = p.get("fund_id", old["fund_id"]), p.get("side", old["side"])
            self.fund(c, fund_id)
            require(side in {"BUY", "SELL"}, "INVALID_SIDE", "请选择买入或卖出")
            requested = amount(p.get("requested", old["requested"]), positive=True) if side == "BUY" else dec(p.get("requested", old["requested"]), positive=True)
            precision = p.get("time_precision", old.get("time_precision", "EXACT"))
            require(precision in {"EXACT", "MINUTE", "DAY", "BEFORE_CUTOFF"}, "INVALID_TIME", "下单时间精度无效")
            submitted = submission_time(p.get("submitted_at", old["submitted_at"]), precision)
            require(stamp(submitted) <= stamp(self.now()), "FUTURE_EVENT", "实际下单时间不能在未来")
            intent = bucket(p.get("bucket", old.get("bucket", "BASE")))
            paid = amount(p.get("paid_amount", old["paid"])) if side == "BUY" else ZERO
            require(side != "BUY" or paid <= requested, "PAYMENT_EXCESS", "已扣款不能超过买入金额")
            automatic = [e for e in state["audit"] if e["kind"] == "ORDER_CONFIRMED"
                         and e["payload"].get("order_id") == order_id and not e["reversed"]
                         and e["payload"].get("confirmation_basis") == AUTO_BASIS]
            changed = (fund_id, side, requested, stamp(submitted), paid, precision) != (old["fund_id"], old["side"], old["requested"], stamp(old["submitted_at"]), old["paid"], old.get("time_precision", "EXACT"))
            can_recalculate = bool(changed and automatic and len(automatic) == len(old["confirmations"]) and not old["settled"])
            if old["confirmations"] and not can_recalculate:
                require((fund_id, side, requested) == (old["fund_id"], old["side"], old["requested"]),
                        "ACTUAL_CONFIRMATION_EXISTS", "已有平台确认或回款；可修改时间、归属和订单号，金额及份额请校正平台结果")
            def replace(event, kind, payload, at, suffix):
                Store.append(c, "REVERSAL", {"event_id": event["id"], "reason": "用户修改交易信息"}, p["key"] + ":reverse:" + suffix, self.now())
                if kind:
                    return Store.append(c, kind, dict(payload, _replaces=event["id"]), p["key"] + ":" + suffix, at)

            if can_recalculate:
                for index, e in enumerate(automatic):
                    replace(e, None, None, None, "auto-" + str(index))
                try:
                    base = self.state(c)
                except Problem:
                    raise Problem("DEPENDENT_EVENT", "此笔自动入仓已被后续卖出使用，请先校正关联卖出，避免改坏持仓")
            else:
                base = state
            clean = dict(original["payload"], fund_id=fund_id, side=side, requested=requested,
                         bucket=intent, auto_confirm=p.get("auto_confirm", old.get("auto_confirm", True)),
                         auto_generation=old.get("auto_generation", 0) + 1,
                         external_order_id=p.get("external_order_id", old.get("external_order_id", "")), time_precision=precision)
            if precision == "BEFORE_CUTOFF":
                clean["submission_basis"] = "USER_STATED_BEFORE_1500"
            else:
                clean.pop("submission_basis", None)
            require(isinstance(clean["auto_confirm"], bool), "INVALID_STATE", "自动入账选项须为布尔值")
            if side == "SELL" and (not old["confirmations"] or can_recalculate):
                rule = self.rules(c, fund_id)
                require(rule.get("verified") and rule.get("lot_method") == "FIFO", "RULES_UNVERIFIED", "需要已核实的FIFO规则")
                own = {a["lot_id"]: a["units"] for a in base["orders"][order_id]["allocations"]} if old["side"] == "SELL" else {}
                left, allocations = requested, []
                on = stamp(submitted).astimezone(SHANGHAI).date().isoformat()
                for lot in sorted(base["lots"].values(), key=lambda l: (l.get("acquired") or "9999", l["id"])):
                    if lot["fund_id"] != fund_id or not lot.get("available_from") or lot["available_from"] > on:
                        continue
                    take = min(left, lot["units"] - lot["reserved"] + own.get(lot["id"], ZERO))
                    if take > 0:
                        allocations.append({"lot_id": lot["id"], "units": take})
                        left -= take
                    if not left:
                        break
                require(not left, "UNITS_RESERVED", "修改后的卖出份额超过当时可用持仓")
                clean["allocations"] = allocations
            elif side == "BUY":
                clean["allocations"] = []
            replace(original, "ORDER_SUBMITTED", clean, submitted, "submitted")
            payments = [e for e in base["audit"] if e["kind"] == "ORDER_PAID" and not e["reversed"]
                        and e["payload"].get("order_id") == order_id]
            def payment_payload(value):
                payload = {"order_id": order_id, "amount": value}
                if precision == "BEFORE_CUTOFF":
                    payload.update(time_precision="DAY", submission_basis="USER_STATED_BEFORE_1500")
                return payload
            if paid != old["paid"] or side != old["side"]:
                for index, e in enumerate(payments):
                    replace(e, "ORDER_PAID" if index == 0 and paid else None,
                            payment_payload(paid), submitted, "paid-" + str(index))
                if paid and not payments:
                    Store.append(c, "ORDER_PAID", payment_payload(paid), p["key"] + ":paid-new", submitted)
            else:
                for index, e in enumerate(payments):
                    linked_time = stamp(e["occurred_at"]) == stamp(old["submitted_at"])
                    if stamp(e["occurred_at"]) < stamp(submitted) or (linked_time and changed):
                        replace(e, "ORDER_PAID", payment_payload(e["payload"]["amount"]), submitted, "paid-" + str(index))
            edited = self.state(c)
            new = edited["orders"][order_id]
            _, payload = self.order_progress(c, new, edited)
            if payload:
                if can_recalculate:
                    # Preserve the logical lot ID when recalculating an already-booked purchase.
                    payload["_replaces"] = automatic[0]["id"]
                Store.append(c, "ORDER_CONFIRMED", payload, auto_key(new), self.now())
                self.state(c)
            # One compact receipt per deliberate edit, never one per refresh.
            Store.put(c, "order_edit", p["key"], {"fingerprint": fingerprint, "order_id": order_id})
            return {"order_id": order_id, "duplicate": False, "revision": self.state(c)["revision"]}
