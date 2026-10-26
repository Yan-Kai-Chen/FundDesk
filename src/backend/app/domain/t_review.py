"""Exit references and an explicit, forward-only retained-proceeds scenario.

No wallet balance is inferred. The scenario's zero opening cash is a declared
boundary of a separate model, not a claim about a person's available money.
"""
from decimal import Decimal, localcontext

from app.domain.finance import ZERO, dec, money
from app.persistence.store import digest

POLICY = 'VIRTUAL_RETAINED_PROCEEDS'
POLICY_VERSION = 1


def review_quote(fund):
    view = fund.get('return_valuation') or {}
    quote = ((fund.get('daily_t') or {}).get('today') or {}).get('quote') or {}
    value = quote.get('value') if quote.get('id') == view.get('quote_id') and quote.get('id') else fund.get('valuation_nav')
    if value is None:
        value = (fund.get('nav') or {}).get('value')
    return dict(value=None if value is None else dec(value, positive=True), date=view.get('date') or (fund.get('nav') or {}).get('effective_date'),
                quote_id=view.get('quote_id'), label=view.get('label') or '最近正式', is_estimate=bool(view.get('is_estimate')))


def fund_journal(state, fid):
    orders = {o['id'] for o in state.get('orders', {}).values() if o['fund_id'] == fid}
    dividends = {d['id'] for d in state.get('dividends', {}).values() if d['fund_id'] == fid}
    return [e for e in state.get('audit', []) if e['payload'].get('fund_id') == fid or
            e['payload'].get('order_id') in orders or e['payload'].get('dividend_id') in dividends]


def prefix_fingerprint(state, fid, revision):
    return digest([{k: e[k] for k in ('id', 'seq', 'kind', 'payload', 'reversed')}
                   for e in fund_journal(state, fid) if e['seq'] <= revision])


def exit_reference(fund):
    """Per-exit current-price reference, never a whole-strategy alpha claim."""
    quote = review_quote(fund)
    rows = [s for s in (fund.get('position_buckets') or {}).get('sales', []) if s.get('intent') == 'TACTICAL']
    confirmed = [s for s in rows if not s.get('pending')]
    partial = [s for s in rows if s.get('pending')]
    if (fund.get('position_buckets') or {}).get('status') == 'BLOCKED':
        return {'status': 'PARTIAL', 'count': len(confirmed), 'pending_count': len(partial), 'difference': None,
                'detail': '分红或拆分资料需要核对，暂不比较原始卖出份额。', 'quote': quote, 'rows': []}
    if quote['value'] is None and confirmed:
        return {'status': 'WAIT', 'count': len(confirmed), 'pending_count': len(partial), 'difference': None,
                'detail': '等待可用净值。', 'quote': quote, 'rows': []}
    result = []
    for sale in confirmed:
        if sale.get('units') is None or sale.get('net') is None:
            partial.append(sale)
            continue
        net, units = dec(sale['net']), dec(sale['units'])
        held = money(units * quote['value'])
        result.append({'id': sale['id'], 'at': sale['at'], 'units': units, 'net': net,
                       'fee': None if sale.get('fee') is None else dec(sale['fee']),
                       'held_value': held, 'difference': net - held, 'reconstructed': bool(sale.get('reconstructed'))})
    return {'status': 'PARTIAL' if partial else 'READY', 'count': len(result), 'pending_count': len(partial),
            'difference': sum((r['difference'] for r in result), ZERO),
            'net_redemptions': sum((r['net'] for r in result), ZERO),
            'held_value': sum((r['held_value'] for r in result), ZERO), 'quote': quote, 'rows': result,
            'detail': '逐次退出参照合计，不等同整个基金做 T 增益。每次为已卖净额减去同批份额按当前净值继续持有的价值；含赎回费，也不表示回款仍在账户。'}


def scenario_review(fund, state, baseline):
    if not baseline:
        return {'status': 'NOT_STARTED', 'label': '尚未建立前向基准', 'difference': None, 'policy': POLICY}
    if baseline.get('start_pending'):
        return {'status': 'WAIT_BASELINE', 'label': '等待自动建立参照', 'difference': None, 'policy': POLICY,
                'started_at': None, 'requested_at': baseline.get('requested_at'), 'reasons': [{
                    'code': 'WAIT_EXISTING_ORDERS',
                    'message': '待已有在途完成后自动建立参照；期间新增订单也等待完成。比较从实际建立时开始，不追溯申请时点。'}]}
    fid, quote = fund['id'], review_quote(fund)
    result = {'status': 'READY_SCENARIO', 'label': '资金复用情景', 'policy': POLICY, 'policy_version': POLICY_VERSION,
              'started_at': baseline['started_at'], 'baseline_version': baseline.get('_version', 1),
              'initial_value': dec(baseline['holding_value']), 'initial_units': dec(baseline['units']),
              'initial_nav': dec(baseline['nav']), 'quote': quote, 'difference': None,
              'actual_value': None, 'hold_value': None, 'virtual_cash': None, 'receivable': None,
              'external_contributions': None, 'trade_count': 0, 'reasons': []}
    def stop(code, message):
        return dict(result, status='PARTIAL', label='情景待补', reasons=[{'code': code, 'message': message}])
    if prefix_fingerprint(state, fid, baseline['revision']) != baseline['prefix_fingerprint']:
        return stop('BASELINE_CORRECTED', '基准前的交易已校正，请以当前持仓重新建立基准；旧基准摘要保留。')
    if quote['value'] is None:
        return stop('WAIT_QUOTE', '等待可用净值。')
    if (fund.get('position_buckets') or {}).get('status') == 'BLOCKED':
        return stop('CORPORATE_ACTION', '分红或拆分需先补齐双方同口径处理，暂不输出增益。')
    journal = [e for e in fund_journal(state, fid) if e['seq'] > baseline['revision'] and not e.get('reversed')]
    # A replacement after the freeze is a new active event, but an old decision
    # must not be silently rewritten as a new strategy decision.
    if any(e['payload'].get('_replaces') for e in journal):
        return stop('POST_START_CORRECTION', '比较期间存在交易校正，请重新建立基准，避免改变原先冻结的决策。')
    committed = set(baseline.get('committed_order_ids', []))
    if any(e['payload'].get('order_id') in committed and e['kind'] in {'ORDER_CONFIRMED', 'ORDER_PAID', 'ORDER_SETTLED', 'ORDER_REFUNDED'} for e in journal):
        return stop('PRECOMMITTED_ORDER_CHANGED', '建立基准时已有在途订单现已变化；它不是新增做 T 决策，请待其完成后重新建立基准。')
    active = [o for o in state.get('orders', {}).values() if o['fund_id'] == fid and o['id'] not in committed]
    if any((not o.get('closed') and (o.get('paid', ZERO) or o.get('side') == 'SELL')) or o.get('refund_due', ZERO) for o in active):
        return stop('PENDING_FLOW', '比较期间有待确认或待退款交易；完成后自动继续计算，暂不把在途额当成收益。')
    orders = state.get('orders', {})
    units = dec(baseline['units']); held_units = units
    cash = ZERO; receivable = ZERO; external = ZERO
    funding = {}; trade_count = 0
    with localcontext() as ctx:
        ctx.prec = 40
        for e in journal:
            kind, p = e['kind'], e['payload']
            if kind in {'SPLIT', 'DIVIDEND_DECLARED', 'DIVIDEND_PAID', 'DIVIDEND_REINVESTED', 'OPENING_LOT', 'TRACKING_OPENING', 'ORDER_REFUNDED'}:
                return stop('UNSUPPORTED_FLOW', '期间有分红、拆分、补录起点或退款；需要同口径处理后重新建立基准。')
            oid = p.get('order_id'); order = orders.get(oid)
            if kind == 'ORDER_PAID' and order:
                value = dec(p['amount']); reused = min(cash, value); contribution = value - reused
                cash -= reused; external += contribution
                funding.setdefault(oid, []).append([value, contribution])
            elif kind == 'ORDER_CONFIRMED' and order:
                q, gross, net = dec(p['units']), dec(p['gross']), dec(p['net'])
                if order['side'] == 'SELL':
                    if order.get('bucket', 'BASE') != 'TACTICAL':
                        return stop('BASE_SALE', '期间有底仓卖出，不把它当成做 T 决策；请重新建立基准。')
                    units -= q; receivable += net; trade_count += 1
                else:
                    left, contribution = gross, ZERO
                    for lot in funding.get(oid, []):
                        take = min(left, lot[0])
                        if take:
                            share = lot[1] if take == lot[0] else lot[1] * take / lot[0]
                            lot[0] -= take; lot[1] -= share; contribution += share; left -= take
                        if not left:
                            break
                    if left:
                        return stop('PAYMENT_MISSING', '买入缺少比较起点后的付款记录，不能推定新增外部投入。')
                    units += q
                    # Same external contribution, same price and proportional
                    # actual acquisition fee/rounding; recycled funds never count twice.
                    held_units += q * contribution / gross
                    trade_count += 1
            elif kind == 'ORDER_SETTLED' and order:
                value = dec(p['amount']); cash += value; receivable -= value
                if receivable < ZERO:
                    return stop('SETTLEMENT_OUTSIDE_SCOPE', '本次到账对应起点前的卖出，不能视为本期做 T 产生的资金。')
        if units != dec(fund.get('units', ZERO)):
            return stop('HOLDINGS_MISMATCH', '比较份额未能与当前持仓核对，请检查期间更正或重新建立基准。')
        actual_value = money(units * quote['value'] + cash + receivable)
        hold_value = money(held_units * quote['value'])
        initial = dec(baseline['holding_value'])
        result.update(actual_value=actual_value, hold_value=hold_value, virtual_cash=money(cash), receivable=money(receivable),
                      external_contributions=money(external), trade_count=trade_count, actual_units=units,
                      hold_units=held_units.quantize(Decimal('0.000000000001')), difference=actual_value - hold_value,
                      actual_gain=actual_value - initial - external, hold_gain=hold_value - initial - external)
    if committed:
        result['reasons'] = [{'code': 'PRECOMMITTED_EXCLUDED', 'message': '建立时已有在途订单未纳入；其后续变化会暂停比较，不当成新增做 T。'}]
    return result


def t_review(fund, state, baseline=None):
    return {'schema_version': 1, 'fund_id': fund['id'], 'exit_reference': exit_reference(fund),
            'scenario': scenario_review(fund, state, baseline),
            'baseline': None if baseline is None else {k: v for k, v in baseline.items() if k not in {'prefix_fingerprint'}},
            'notice': '情景假设卖出净款结算后留在本基金，后续买入先复用，差额才是双方相同的新增投入。起点不含钱包现金，虚拟余额不是实际现金；不计利息，不是收益保证。'}
