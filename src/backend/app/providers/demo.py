"""Synthetic, deterministic fixtures. Never called for a real account."""
from datetime import date, timedelta
from decimal import Decimal
import math

from app.persistence.store import Store


def seed(workbench):
    require_demo = workbench.mode == "demo"
    if not require_demo:
        return
    with workbench.store.transaction() as c:
        if Store.doc(c, "meta", "seed-v1"):
            return
        dates = []
        current = date(2026, 6, 1)
        while current <= date(2026, 12, 31):
            if current.weekday() < 5:
                dates.append(current.isoformat())
            current += timedelta(days=1)
        history_dates = [d for d in dates if d <= "2026-10-19"][-90:]
        definitions = [
            ("demo-equity", "宽基成长 · 演示 A", "A", "EQUITY_FUND", "1.2834", "8000", "9000", "TARGET_WEIGHT"),
            ("demo-gold", "黄金联接 · 演示 C", "C", "GOLD_LINKED", "1.5342", "6000", "8200", "ANCHORED_THRESHOLD"),
            ("demo-bond", "稳健债券 · 演示 A", "A", "BOND_FUND", "1.0689", "12000", "12000", "TARGET_WEIGHT"),
        ]
        for index, (id, name, category, kind, nav, units, cost, strategy) in enumerate(definitions):
            Store.put(c, "fund", id, {"id": id, "name": name, "fund_code": None, "share_class": category,
                "instrument_type": kind, "channel": "模拟渠道", "currency": "CNY", "quantity_unit": "FUND_UNIT", "watching": True})
            rule = {"fund_id": id, "verified": True, "source": "合成演示规则，不适用于任何真实基金", "verified_at": workbench.now(),
                "effective_from": "2020-01-01", "lot_method": "FIFO", "purchase_method": "EXTERNAL_RATIO", "purchase_rate": "0.0015",
                "unit_step": "0.01", "redemption_tiers": [{"min_days": 0, "max_days": 7, "rate": "0.015"},
                {"min_days": 7, "max_days": 30, "rate": "0.005"}, {"min_days": 30, "max_days": None, "rate": "0"}],
                "open_dates": dates, "cutoff": "15:00:00", "timezone": "Asia/Shanghai", "cutoff_inclusive": False,
                "subscriptions_open": True, "redemptions_open": True}
            Store.put(c, "rule", id, rule)
            Store.put(c, "rule_history", id + ":1", rule)
            for i, day_ in enumerate(history_dates):
                delta = len(history_dates) - 1 - i
                movement = (Decimal(str(math.sin(i * .43 + index) - math.sin((len(history_dates) - 1) * .43 + index)))
                            * (Decimal("0.014") if index < 2 else Decimal("0.001")))
                value = (Decimal(nav) - Decimal(delta) * Decimal("0.001" if index < 2 else "0.0001") + movement).quantize(Decimal("0.0001"))
                workbench.observe({"fund_id": id, "value_kind": "OFFICIAL_NAV", "value": str(value), "effective_date": day_,
                    "published_at": day_ + "T13:00:00+00:00", "observed_at": day_ + "T13:05:00+00:00",
                    "source": "DemoProvider · 合成日净值", "quality": "DEMO"}, c)
            if index < 2:
                estimate = (Decimal(nav) * Decimal("1.0048" if index == 0 else "0.9972")).quantize(Decimal("0.0001"))
                workbench.observe({"fund_id": id, "value_kind": "THIRD_PARTY_ESTIMATE", "value": str(estimate),
                    "effective_date": "2026-10-20", "as_of": workbench.now(), "source": "DemoProvider · 合成盘中估值", "quality": "DEMO"}, c)
            Store.append(c, "OPENING_LOT", {"fund_id": id, "units": units, "cost": cost, "acquired": "2026-06-01",
                "available_from": "2026-06-03", "snapshot_date": "2026-10-19"}, "demo:opening:" + id, "2026-10-19T01:00:00+00:00")
            params = {"target_weight": "0.28", "band": "0.03", "anchor_nav": "1.5000", "buy_trigger": "0.02", "sell_trigger": "0.02",
                "single_amount": "1000.00", "daily_limit": "3000.00", "cash_reserve": "3000.00", "min_value": "3000.00",
                "max_value": "25000.00", "min_order": "100.00", "max_fee_rate": "0.005", "cooldown_days": 1, "group_limit": "18000.00"}
            Store.put(c, "strategy", id, {"fund_id": id, "enabled": index < 2, "type": strategy, "params": params, "anchor_seq": 0, "updated_at": workbench.now()})
        Store.append(c, "CASH_DEPOSIT", {"amount": "18000.00", "note": "合成期初投资现金"}, "demo:cash:initial", "2026-10-19T01:00:00+00:00")
        Store.append(c, "ORDER_SUBMITTED", {"id": "demo-order-pending", "fund_id": "demo-bond", "side": "BUY", "requested": "600.00",
            "external_order_id": "DEMO-20261020-001"}, "demo:order:pending", "2026-10-20T04:00:00+00:00")
        Store.append(c, "ORDER_PAID", {"order_id": "demo-order-pending", "amount": "600.00"}, "demo:order:paid", "2026-10-20T04:01:00+00:00")
        Store.put(c, "meta", "seed-v1", {"notice": "All values are synthetic; fixed clock 2026-10-20 13:30 Asia/Shanghai"})
        workbench.state(c)
