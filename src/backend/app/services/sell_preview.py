"""No writes or online fetches: estimate a sell from the current local book."""
from app.domain.buckets import bucket, part
from app.domain.finance import ZERO, SHANGHAI, dec, stamp, require, redemption_preview
from app.domain.sell_impact import sell_impact
from app.domain.sell_comparison import buy_price_comparison


class SellPreviewMixin:
    def preview_sell(self, fund_id, p):
        with self.store.connect() as c:
            state = self.snapshot(c)
            fund = next((f for f in state['funds'] if f['id'] == fund_id), None)
            require(fund is not None, 'FUND_NOT_FOUND', '基金不存在')
            require(not fund.get('initialization_pending'), 'HOLDINGS_UNKNOWN', '请先完成当前持仓记录')
            at = stamp(p.get('occurred_at') or self.now())
            require(at <= stamp(self.now()), 'FUTURE_EVENT', '申请时间不能晚于当前时间')
            on = at.astimezone(SHANGHAI).date().isoformat()
            intent = bucket(p.get('bucket', 'TACTICAL'))
            units = dec(p.get('requested'), positive=True)
            rule = fund['rules']
            require(rule.get('verified') and rule.get('effective_from', '9999') <= on and
                    (not rule.get('effective_to') or on < rule['effective_to']),
                    'RULES_UNVERIFIED', '申请日期的费用规则尚未核实')
            step = dec(rule.get('unit_step', '0.01'), positive=True)
            require(units % step == 0, 'UNIT_PRECISION', '卖出份额不符合产品最小步长')
            today = fund['daily_t']['today']
            quote = today.get('quote') if on == self.today() and today['status'] == 'READY' else None
            if quote is None:
                history = [r for r in fund['history'] if r['effective_date'] <= on]
                quote = history[-1] if history else None
            require(quote is not None, 'NAV_MISSING', '缺少申请日期可用的参考净值，暂不能估算收益')
            nav = dec(quote['value'], positive=True)
            # Use the same split adjustment as the current official valuation.
            if fund.get('nav') and quote['id'] == fund['nav']['id']:
                nav = fund['valuation_nav']
            lots = [lot for lot in fund['lots'] if lot.get('available_from') and lot['available_from'] <= on]
            allocations = p.get('allocations', [])
            require(isinstance(allocations, list), 'INVALID_ALLOCATIONS', '批次选择无效')
            if allocations:
                by_id = {lot['id']: lot for lot in lots}
                selected, seen, total = [], set(), ZERO
                for allocation in allocations:
                    require(isinstance(allocation, dict) and isinstance(allocation.get('lot_id'), str) and allocation['lot_id'] in by_id and
                            allocation['lot_id'] not in seen, 'INVALID_ALLOCATIONS', '批次不存在、重复或不属于此基金')
                    lot = by_id[allocation['lot_id']]
                    q = dec(allocation.get('units'), positive=True)
                    require(q % step == 0 and q <= lot['units'] - lot['reserved'] and
                            lot.get('available_from') and lot['available_from'] <= on,
                            'UNITS_RESERVED', '所选批次份额不足、已冻结或尚不可卖')
                    selected.append(dict(lot, units=q, reserved=ZERO, cost=part(lot['cost'], q, lot['units'])))
                    total += q
                    seen.add(lot['id'])
                require(total == units, 'ALLOCATION_MISMATCH', '所选批次合计须等于卖出份额')
                lots = selected
            redemption = redemption_preview(lots, units, nav, rule, on)
            impact = sell_impact(fund['position_buckets'], redemption, nav, on, at.isoformat(), intent,
                                 fund['position_buckets'].get('total_pnl'))
            basis = {k: quote.get(k) for k in ('id', 'value_kind', 'effective_date', 'as_of', 'observed_at', 'source')}
            impact.update(fund_id=fund_id, ledger_revision=state['revision'], rule_version=rule['_version'],
                          basis=basis, generated_at=self.now(),
                          notice='按当前持仓和参考净值估算；费用按申请日持有期计算，实际确认净值日、手续费及到账以渠道为准')
            impact['buy_comparison'] = buy_price_comparison(fund, state['orders'], redemption, nav, on,
                                                          at.isoformat(), p.get('reference_buy_order_id'))
            baseline = today.get('baseline') if quote == today.get('quote') else None
            impact['day_change'] = (nav / dec(baseline['value'], positive=True) - 1 if baseline else None)
            impact['day_baseline'] = {k: baseline.get(k) for k in ('id', 'effective_date', 'value')} if baseline else None
            return impact
