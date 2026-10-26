"""Read-only buy-price comparison, separate from the ledger's realized P&L."""
from app.domain.buckets import part
from app.domain.finance import ZERO, dec, money, require, stamp


def buy_price_comparison(fund, orders, redemption, nav, on, at, reference_id=None):
    """Compare a sale with a known remaining buy, without changing attribution.

    A price comparison is not a claim that the channel redeems this buy lot.
    Physical FIFO still supplies the whole sale's fees; a covered subset gets
    a stated proportional fee estimate. Nothing is matched or posted to books.
    """
    require(reference_id is None or isinstance(reference_id, str) and len(reference_id) <= 200,
            'INVALID_BUY_REFERENCE', '买入参照须为本基金的交易编号')
    positions = (fund.get('position_buckets') or {}).get('positions', [])
    buys = [o for o in orders if o['fund_id'] == fund['id'] and o['side'] == 'BUY'
            and stamp(o['submitted_at']) <= stamp(at)]
    if reference_id:
        require(any(o['id'] == reference_id for o in buys), 'BUY_REFERENCE_NOT_FOUND', '买入参照不属于此基金或申请时间尚未发生')
    candidates = []
    for order in sorted(buys, key=lambda o: (o['submitted_at'], o['id']), reverse=True):
        sources = {c.get('lot_id') or c.get('event_id') for c in order.get('confirmations', [])
                   if c.get('event_id') or c.get('lot_id')}
        lots = [p for p in positions if p.get('source_id') in sources and
                stamp(p['at']) <= stamp(at) and dec(p['units']) - dec(p.get('reserved', ZERO)) > ZERO]
        if not lots:
            continue
        units = sum((dec(p['units']) - dec(p.get('reserved', ZERO)) for p in lots), ZERO)
        cost = (None if any(p.get('cost') is None for p in lots) else
                sum((part(dec(p['cost']), dec(p['units']) - dec(p.get('reserved', ZERO)), dec(p['units'])) for p in lots), ZERO))
        available = sum((dec(p['units']) - dec(p.get('reserved', ZERO)) for p in lots
                         if p.get('available_from') and p['available_from'] <= on), ZERO)
        candidates.append({'order_id': order['id'], 'bucket': order.get('bucket', 'BASE'),
                           'submitted_at': order['submitted_at'], 'remaining_units': units,
                           'remaining_cost': cost, 'available_units': available,
                           'available_from': max((p.get('available_from') or '未知' for p in lots), default=None)})
    # Bound the response, without letting an explicitly selected older purchase
    # disappear just because newer purchases were recorded later.
    selected = next((c for c in candidates if c['order_id'] == reference_id), None) if reference_id else (
        next((c for c in candidates if c['bucket'] == 'TACTICAL'), None) or next(iter(candidates), None))
    options = candidates[:20]
    if selected and selected not in options:
        options.append(selected)
    result = {'status': 'NO_REMAINING_BUY', 'options': options, 'order_id': None,
              'selection': 'SELECTED' if reference_id else 'AUTO', 'ledger_posted': False,
              'included_in_total_pnl': False,
              'notice': '按参照买入的剩余成本比较价格；不是渠道卖出批次，不重复计入累计收益'}
    if selected is None:
        result['notice'] = ('所选买入已无可参照剩余份额，不自动替换其他买入' if reference_id else
                            '暂无可核对的剩余买入订单；历史迁入或未确认买入不推定为本轮做T')
        return result
    result.update(selected)
    units = selected['remaining_units']
    covered = min(redemption['units'], units)
    result.update(covered_units=covered, uncovered_units=redemption['units'] - covered,
                  covered_available_units=min(covered, selected['available_units']),
                  fee_share=part(redemption['fee'], covered, redemption['units']),
                  reference_value=money(units * nav), remaining_holding_pnl=None,
                  covered_cost=None, gross_price_edge=None, net_price_edge=None)
    if selected['remaining_cost'] is None:
        result.update(status='UNKNOWN_COST', notice='参照买入剩余成本未知，不虚构本轮价差')
        return result
    cost = part(selected['remaining_cost'], covered, units)
    # Compare against the actual sale preview's rounded proceeds. In particular,
    # a fully covered sale must reconcile to net proceeds minus reference cost;
    # recomputing units * NAV can differ by pennies from channel batch rounding.
    covered_gross = part(redemption['gross'], covered, redemption['units'])
    edge = covered_gross - cost
    result.update(status='READY', covered_cost=cost, covered_gross=covered_gross, gross_price_edge=edge,
                  net_price_edge=edge - result['fee_share'],
                  remaining_holding_pnl=result['reference_value'] - selected['remaining_cost'])
    return result
