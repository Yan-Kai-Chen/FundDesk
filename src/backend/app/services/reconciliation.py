"""Small, explicitly saved platform snapshots; never a second financial ledger."""
from datetime import timedelta
from decimal import Decimal

from app.domain.buckets import BucketBook
from app.domain.finance import ZERO, SHANGHAI, dec, day, money, require, stamp
from app.persistence.store import Store, digest

KIND = "holding_reconciliation"
HISTORY_LIMIT = 5
CURRENT_WINDOW_SECONDS = 600


def _text(value, limit, label):
    require(isinstance(value, str) and 0 < len(value.strip()) <= limit,
            "INVALID_RECONCILIATION", f"请填写{label}，最多 {limit} 字符")
    return value.strip()


def _optional_decimal(value, *, signed=False, cents=False):
    if value in (None, ""):
        return None
    result = dec(value, nonnegative=not signed)
    require(not cents or result == money(result), "AMOUNT_PRECISION", "人民币金额最多两位小数")
    return format(result, "f")


class ReconciliationMixin:
    def _reconciliation_input(self, p):
        require(isinstance(p, dict), "INVALID_RECONCILIATION", "请提供平台持仓快照")
        require(p.get("units_basis") == "CONFIRMED_EXCLUDES_PENDING", "RECONCILIATION_BASIS_REQUIRED",
                "请使用平台已确认持有份额，不含待确认申购或已确认卖出")
        scope = p.get("scope", "CURRENT")
        require(scope in {"CURRENT", "HISTORICAL"}, "INVALID_RECONCILIATION", "请选择当前或历史截图")
        at = stamp(p.get("snapshot_at") or self.now())
        require(at <= stamp(self.now()), "FUTURE_EVENT", "截图持仓时点不能在未来")
        nav_date = p.get("nav_date") or None
        if nav_date:
            require(day(nav_date) <= at.astimezone(SHANGHAI).date(), "INVALID_DATE", "净值日期不能晚于截图时点")
        value_basis = p.get("valuation_basis", "UNSPECIFIED")
        require(value_basis in {"UNSPECIFIED", "OFFICIAL_NAV", "ESTIMATE"}, "INVALID_RECONCILIATION", "估值口径无效")
        pnl_basis = p.get("holding_pnl_basis", "UNSPECIFIED")
        require(pnl_basis in {"UNSPECIFIED", "REMAINING_COST"}, "INVALID_RECONCILIATION", "持有收益口径无效")
        return {"source": _text(p.get("source", "支付宝"), 120, "平台或资料来源"),
                "snapshot_at": at.isoformat(), "scope": scope, "units_basis": p["units_basis"],
                "confirmed_units": _optional_decimal(p.get("confirmed_units")),
                "market_value": _optional_decimal(p.get("market_value"), cents=True),
                "holding_pnl": _optional_decimal(p.get("holding_pnl"), signed=True, cents=True),
                "reported_fee": _optional_decimal(p.get("reported_fee"), cents=True),
                "nav_date": nav_date, "valuation_basis": value_basis, "holding_pnl_basis": pnl_basis,
                "note": str(p.get("note", ""))[:300]}

    def _reconciliation_context(self, c, fund_id, state=None):
        fund = self.fund(c, fund_id)
        state = self.state(c) if state is None else state
        lots = [l for l in state["lots"].values() if l["fund_id"] == fund_id]
        orders = [o for o in state["orders"].values() if o["fund_id"] == fund_id]
        book = state["bucket_books"].get(fund_id, BucketBook())
        history = self.observations(c, fund_id, "OFFICIAL_NAV")
        nav = history[-1] if history else None
        value = dec(nav["value"]) if nav else None
        if nav:
            for split in state["splits"]:
                if split["fund_id"] == fund_id and nav["id"] in split.get("affected_observation_ids", []):
                    value /= dec(split["factor"])
        units = sum((l["units"] for l in lots), ZERO)
        summary = book.summary(value, self.today())
        groups = summary.get("groups", {})
        floating = (sum((g["unrealized"] for g in groups.values()), ZERO)
                    if groups and summary.get("total_units") == units and
                    all(g["unrealized"] is not None for g in groups.values()) else None)
        # A fund with only screenshot history has not yet established a financial opening.
        initialized = not (Store.doc(c, "statement", fund_id) and not lots and not orders and
                           fund_id not in state["tracking"] and not Store.doc(c, "migration_opening", fund_id))
        pending = [o for o in orders if not o["closed"]]
        ledger_signature = digest({"lots": sorted(lots, key=lambda l: l["id"]),
            "orders": sorted(orders, key=lambda o: o["id"]),
            "book": book.summary(None, self.today()), "tracking": state["tracking"].get(fund_id),
            "splits": [s for s in state["splits"] if s["fund_id"] == fund_id],
            "initialized": initialized})
        order_ids = {o["id"] for o in orders}
        related = [e for e in state["audit"] if e["payload"].get("fund_id") == fund_id or
                   e["payload"].get("order_id") in order_ids or e["payload"].get("lot_id") in {l["id"] for l in lots}]
        related_ids = {e["id"] for e in related}
        related.extend(e for e in state["audit"] if e["payload"].get("event_id") in related_ids)
        change_times = [stamp(e[k]) for e in related for k in ["occurred_at", "recorded_at"]
                        if stamp(e[k]) <= stamp(self.now())]
        return {"fund_id": fund_id, "fund_name": fund["name"], "revision": state["revision"],
                "confirmed_units": units, "market_value": money(units * value) if value is not None else None,
                "holding_pnl": floating, "nav_date": nav["effective_date"] if nav else None,
                "nav_value": value, "nav_observation_id": nav["id"] if nav else None,
                "pending_order_count": len(pending), "order_ids": [o["id"] for o in reversed(orders[-20:])],
                "initialized": initialized, "ledger_signature": ledger_signature,
                "last_financial_change_at": max(change_times).isoformat() if change_times else None}

    @staticmethod
    def _reconciliation_signature(context, price_sensitive):
        return digest([context["ledger_signature"],
            [context["nav_date"], context["nav_value"]] if price_sensitive else None])

    def _reconciliation_proposal(self, c, fund_id, p):
        entered = self._reconciliation_input(p)
        require(entered["confirmed_units"] is not None, "UNITS_REQUIRED", "请填写平台已确认持有份额")
        context = self._reconciliation_context(c, fund_id)
        unchanged_since_capture = not context["last_financial_change_at"] or stamp(entered["snapshot_at"]) >= stamp(context["last_financial_change_at"])
        current = (entered["scope"] == "CURRENT" and context["initialized"] and unchanged_since_capture and
            timedelta(0) <= stamp(self.now()) - stamp(entered["snapshot_at"]) <= timedelta(seconds=CURRENT_WINDOW_SECONDS) and
            stamp(entered["snapshot_at"]).astimezone(SHANGHAI).date().isoformat() == self.today())
        comparable_price = (current and entered["valuation_basis"] == "OFFICIAL_NAV" and
                            entered["nav_date"] is not None and entered["nav_date"] == context["nav_date"])
        comparisons, unassessed = [], []

        def compare(field, label, tolerance):
            reported, local = entered[field], context[field]
            delta = dec(reported, nonnegative=False) - local
            comparisons.append({"field": field, "label": label, "reported": reported, "local": local,
                "difference": delta, "tolerance": tolerance, "status": "MATCHED" if abs(delta) <= tolerance else "DIFFERENCE"})

        if current:
            compare("confirmed_units", "已确认份额", Decimal("0.005"))
        else:
            reason = ("本基金尚未建立持仓起点，暂不与零份额比较" if not context["initialized"] else
                      "截图后本基金已有金融记录变化，不能拿当前持仓代替截图时持仓" if not unchanged_since_capture else
                      "历史或非当前时点只保存资料，不能拿当前持仓代替当时持仓")
            unassessed.append({"field": "confirmed_units", "reason": reason})
        for field, label in [("market_value", "持有金额"), ("holding_pnl", "持有收益")]:
            if entered[field] is None:
                continue
            if comparable_price and context[field] is not None and (field != "holding_pnl" or entered["holding_pnl_basis"] == "REMAINING_COST"):
                compare(field, label, Decimal("0.01"))
            else:
                unassessed.append({"field": field, "reason": "仅对比同一正式净值日；持有收益还需明确为市值减剩余成本，估算或平台其他收益口径不直接比较"})
        if entered["reported_fee"] is not None:
            unassessed.append({"field": "reported_fee", "reason": "费用须对应原订单或批次，此处只保留资料；请到原交易校正"})
        differences = sum(r["status"] == "DIFFERENCE" for r in comparisons)
        status = "DIFFERENCE" if differences else "MATCHED" if comparisons else "NOT_COMPARABLE"
        sensitive = any(r["field"] != "confirmed_units" for r in comparisons)
        baseline = {k: v for k, v in context.items() if k not in {"fund_name", "ledger_signature"}}
        result = {"fund_id": fund_id, "input": entered, "status": status, "difference_count": differences,
            "comparisons": comparisons, "unassessed": unassessed, "baseline": baseline,
            "baseline_signature": self._reconciliation_signature(context, sensitive), "price_sensitive": sensitive,
            "notice": "仅核对差异，不改交易或持仓；待确认申请不包含在已确认份额中。",
            "correction_action": {"kind": "EXISTING_ORDERS", "fund_id": fund_id, "order_ids": context["order_ids"]}}
        # Other funds or a cash entry may advance the global revision; only this
        # fund's frozen basis and explicitly entered snapshot affect this token.
        result["preview_token"] = digest({k: result[k] for k in
            ["fund_id", "input", "status", "comparisons", "unassessed", "baseline_signature"]})
        return result

    def preview_reconciliation(self, fund_id, p):
        with self.store.connect() as c:
            return self._reconciliation_proposal(c, fund_id, p)

    def save_reconciliation(self, fund_id, p):
        require(p.get("save_snapshot") is True, "SNAPSHOT_ACCEPTANCE_REQUIRED", "请明确保存本次平台持仓快照")
        with self.store.transaction() as c:
            proposal = self._reconciliation_proposal(c, fund_id, p)
            snapshot_id = "recon_" + digest([fund_id, proposal["input"]])[:24]
            old = Store.doc(c, KIND, snapshot_id)
            if old:
                return dict(self._reconciliation_saved_view(c, old), duplicate=True)
            require(p.get("preview_token") == proposal["preview_token"], "VERSION_CONFLICT", "截图输入、持仓或估值基准已变化，请重新核对预览")
            prior = [r for r in Store.docs(c, KIND) if r["fund_id"] == fund_id]
            saved = Store.put(c, KIND, snapshot_id, dict(proposal, id=snapshot_id, saved_at=self.now(),
                saved_sequence=max((r.get("saved_sequence", 0) for r in prior), default=0)+1))
            rows = sorted((r for r in Store.docs(c, KIND) if r["fund_id"] == fund_id),
                          key=lambda r: r["saved_sequence"], reverse=True)
            for stale in rows[HISTORY_LIMIT:]:
                c.execute("DELETE FROM documents WHERE kind=? AND id=?", (KIND, stale["id"]))
            return dict(self._reconciliation_saved_view(c, saved), duplicate=False)

    def _reconciliation_saved_view(self, c, saved, context=None):
        context = context or self._reconciliation_context(c, saved["fund_id"])
        stale = saved["status"] != "NOT_COMPARABLE" and saved["baseline_signature"] != self._reconciliation_signature(context, saved["price_sensitive"])
        return dict(saved, status="STALE" if stale else saved["status"], frozen_status=saved["status"],
                    stale=stale, stale_notice="本基金申请、持仓或对比净值已变化；以下为保存时的核对结果，请用新截图核对。" if stale else None)

    def reconciliation_summary(self, c, fund_id, state=None):
        rows = sorted((r for r in Store.docs(c, KIND) if r["fund_id"] == fund_id),
                      key=lambda r: r["saved_sequence"], reverse=True)
        latest = self._reconciliation_saved_view(c, rows[0], self._reconciliation_context(c, fund_id, state)) if rows else None
        return {"fund_id": fund_id, "status": latest["status"] if latest else "UNRECONCILED",
                "needs_attention": bool(latest and latest["status"] == "DIFFERENCE"),
                "history_count": len(rows), "latest": latest, "retention_per_fund": HISTORY_LIMIT}

    def reconciliation_catalog(self):
        with self.store.connect() as c:
            state = self.state(c)
            return {"funds": [dict(self.reconciliation_summary(c, f["id"], state), name=f["name"], fund_code=f.get("fund_code"))
                              for f in Store.docs(c, "fund")], "retention_per_fund": HISTORY_LIMIT}
