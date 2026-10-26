"""Bounded, disposable paths through the production investment-plan engine.

The simulated NAV rows, orders and books live only inside this call.  Dates are
calendar mappings for the local dealing assumption, not a forecast of opening.
"""
from copy import deepcopy
from decimal import Decimal

from app.domain.buckets import BucketBook, BUCKETS, part
from app.domain.finance import ZERO, Problem, dec, money, purchase_preview, redemption_preview, stamp
from app.domain.investment_plan import investment_plan_reference, TEMPLATE
from app.domain.investment_risk import investment_risk, TEMPLATE_V2
from app.domain.market_calendar import market_session
from app.domain.order_automation import schedule
from app.domain.research_calendar import reference_sessions

METHOD_VERSION = 'plan-scenarios-2026-v1'
PATHS = (
    ('CONTINUED_DECLINE', '连续下跌', ('-1.2', '-1.2', '-1.2', '-1.2', '-.6', '-1.2', '-1.2', '-.6')),
    ('DECLINE_RECOVERY', '先跌后涨', ('-1.2', '-1.2', '-1.2', '0', '1.2', '1.2', '1.8', '2.4')),
    ('CONTINUED_RISE', '持续上涨', ('1.2', '1.2', '1.2', '1.2', '1.2', '.6', '1.2', '1.8')),
)
ASSUMPTIONS = [
    '从当前已确认持仓、原分仓、现有订单与已保存计划起步，价格从最近正式净值之后的假设参考期推进；不是今天实况或未来预测。',
    '三条固定路径按起点 h 的倍数生成；执行金额、动态 h、防御恢复、冷却、五期额度与成本占用直接调用当前生产计划引擎。',
    '仅假设每期满足条件的参考提交一次，并按该申请期路径净值成交；沿用现有本地规则 T+1 确认、保守 T+2 可卖。未核实日期不成交。',
    '买入费用与份额取整、实物 FIFO 赎回费用、经济 T 仓和净实现收益保护均沿用原计算；底仓不补足计划卖出。',
    '新买入资金只为情景外部投入，不代表实际现金；虚拟卖出回款列为应收，不假设已到账、复用或必然回补。',
    '已有在途订单保持原冻结和额度占用，不猜测其确认价、份额或到账；存在时只展示已确认持仓及新增情景范围，完整期末资产和损益留空。',
    '期末资产含情景持仓、待确认买入及虚拟赎回应收；情景期间盈亏扣除新增投入，与原累计收益分开展示。计划纪律未证明盈利。',
]
INCOMPLETE = {'RULES_UNVERIFIED', 'RISK_HISTORY_MISSING', 'RISK_CALENDAR_MISSING', 'RISK_COST_MISSING',
              'HISTORY_INCOMPLETE', 'AVAILABILITY_UNKNOWN', 'PNL_UNKNOWN', 'HOLDINGS_UNKNOWN',
              'AUTO_PRODUCT_UNSUPPORTED', 'AUTO_CALENDAR_MISSING', 'SCENARIO_DEALING_WAIT', 'CALENDAR_MISSING'}


def _book(summary):
    book = BucketBook()
    for source in summary['positions']:
        lot = deepcopy(source)
        for key in ('units', 'reserved', 'cost'):
            lot[key] = None if key == 'cost' and lot.get(key) is None else dec(lot.get(key, ZERO))
        book.lots[lot['id']] = lot
    book.realized = {key: dec(summary['groups'][key]['realized'], nonnegative=False) for key in BUCKETS}
    return book


def _quote(on, nav, *, close=False):
    at = on + ('T16:30:00+08:00' if close else 'T14:00:00+08:00')
    return {'id': 'scenario:' + on, 'value': nav, 'value_kind': 'OFFICIAL_NAV' if close else 'LOCAL_ESTIMATE',
            'effective_date': on, 'observed_at': at, 'as_of': at, 'published_at': at if close else None,
            'source': 'DISPOSABLE_SCENARIO', 'quality': 'HYPOTHETICAL_PATH'}


def _mark(fund, book, nav, on):
    fund['units'] = sum((dec(l['units']) for l in fund['lots']), ZERO)
    fund['sellable_units'] = sum((dec(l['units']) - dec(l.get('reserved', ZERO)) for l in fund['lots']
                                  if l.get('available_from') and l['available_from'] <= on), ZERO)
    fund['valuation_nav'] = nav
    fund['market_value'] = money(fund['units'] * nav)
    fund['position_buckets'] = book.summary(nav, on)


def _confirm(pending, fund, book, on):
    proceeds = ZERO
    for item in pending:
        order = item['order']
        if order['closed'] or item['schedule']['confirm_date'] > on:
            continue
        fill = item['fill']
        order.update(closed=True, confirmed_gross=fill['gross'], confirmed_units=fill['units'])
        at = item['schedule']['confirm_date'] + 'T09:00:00+08:00'
        if order['side'] == 'BUY':
            lot_id = order['id'] + ':lot'
            acquired, available = item['schedule']['confirm_date'], item['schedule']['available_from']
            fund['lots'].append({'id': lot_id, 'units': fill['units'], 'cost': fill['gross'],
                                 'reserved': ZERO, 'acquired': acquired, 'available_from': available})
            book.buy(lot_id, fill['units'], fill['gross'], 'TACTICAL', at, available)
        else:
            physical = {l['id']: l for l in fund['lots']}
            for allocation in fill['allocations']:
                lot, q = physical[allocation['lot_id']], allocation['units']
                removed = part(lot.get('cost'), q, dec(lot['units']))
                if removed is not None:
                    lot['cost'] = dec(lot['cost']) - removed
                lot['units'] = dec(lot['units']) - q
                lot['reserved'] = dec(lot.get('reserved', ZERO)) - q
            book.sell(order['id'], fill['units'], fill['net'], fill['fee'], at)
            book.release(order['id'])
            proceeds += fill['net']
    return proceeds


def _scenario(source, original_orders, base_nav, start_date, h, path, dates):
    code, label, multipliers = path
    fund, orders = deepcopy(source), deepcopy(original_orders)
    book = _book(fund['position_buckets'])
    initial = money(dec(fund['units']) * base_nav)
    nav, contributions, receivable, fees = base_nav, ZERO, ZERO, ZERO
    rows, pending = [], []
    existing = [o for o in orders if o['fund_id'] == fund['id'] and
                (not o.get('closed') or o.get('unsettled') or o.get('refund_due'))]
    # Only already disclosed history anchors the first step.  Synthetic closes
    # become visible on later steps within this isolated model, never globally.
    fund['history'] = [r for r in fund.get('history', []) if r['effective_date'] <= start_date]
    scope = fund['daily_t']['market']['scope']
    completed_dates = 0
    for index, multiplier in enumerate(multipliers):
        move = h * Decimal(multiplier)
        nav = (nav * (1 + move)).quantize(Decimal('.00000001'))
        on = dates[index] if index < len(dates) else None
        row = {'period': index + 1, 'date': on, 'change': move, 'nav': nav,
               'action': 'WAIT', 'amount': None, 'units': None, 'fee': None, 'expected_realized_pnl': None,
               't_units': None, 't_available_units': None, 't_value': None, 'holding_value': None, 'capital_used': None,
               'headroom': None, 'status': 'CALENDAR_MISSING', 'reason': '后续参考日历未核实，金额停止计算。',
               'simulated': False, 'trade_date': None, 'confirm_date': None, 'available_from': None}
        if on is None:
            rows.append(row)
            continue
        completed_dates += 1
        receivable += _confirm(pending, fund, book, on)
        at = on + 'T14:00:00+08:00'
        quote = _quote(on, nav)
        prior = fund['history'][-1] if fund['history'] else None
        _mark(fund, book, nav, on)
        fund['daily_t']['market'] = market_session(at, scope)
        fund['daily_t']['today'] = {'status': 'READY', 'quote': quote, 'baseline': prior, 'change': move}
        reference = investment_plan_reference(fund, orders, at)
        row.update(action=reference['action'], amount=reference['amount'], units=reference.get('units'),
                   fee=reference.get('fee'), status=reference['status'],
                   reason=reference.get('action_hint') if reference.get('preview_only') else reference['reason'],
                   h=(reference['plan'].get('risk') or {}).get('h', Decimal('.01')),
                   risk_state=(reference['plan'].get('risk') or {}).get('label'))
        if reference.get('sell_impact'):
            row['expected_realized_pnl'] = reference['sell_impact']['expected_realized_pnl']
        if reference['action'] in {'BUY', 'SELL'} and reference.get('can_apply'):
            order = {'id': 'scenario:' + code + ':' + str(index), 'fund_id': fund['id'],
                     'side': reference['action'], 'bucket': 'TACTICAL', 'submitted_at': at,
                     'requested': reference['amount'] if reference['action'] == 'BUY' else reference['units'],
                     'confirmed_gross': ZERO, 'confirmed_units': ZERO, 'closed': False}
            try:
                dealing = schedule(order, fund, fund['rules'])
                if dealing['trade_date'] != on:
                    raise Problem('SCENARIO_DEALING_WAIT', '此期不属于原规则可申请日期，未模拟成交。')
                if order['side'] == 'BUY':
                    fill = purchase_preview(reference['amount'], nav, fund['rules'])
                else:
                    candidate = redemption_preview(fund['lots'], reference['units'], nav, fund['rules'], on)
                    physical = {l['id']: l for l in fund['lots']}
                    selected = [dict(physical[a['lot_id']], units=a['units'], reserved=ZERO,
                                     cost=part(physical[a['lot_id']].get('cost'), a['units'], dec(physical[a['lot_id']]['units'])))
                                for a in candidate['allocations']]
                    # Match order_automation.order_progress: confirmation-day
                    # holding age, same application NAV, gross rounded once.
                    fill = redemption_preview(selected, reference['units'], nav, fund['rules'], dealing['confirm_date'])
                    fill['gross'] = money(reference['units'] * nav)
                    fill['net'] = fill['gross'] - fill['fee']
                if order['side'] == 'BUY':
                    contributions += fill['gross']
                else:
                    book.reserve(order['id'], fill['units'], 'TACTICAL', on)
                    physical = {l['id']: l for l in fund['lots']}
                    for a in fill['allocations']:
                        physical[a['lot_id']]['reserved'] = dec(physical[a['lot_id']].get('reserved', ZERO)) + a['units']
                orders.append(order)
                pending.append({'order': order, 'fill': fill, 'schedule': dealing})
                fees += fill['fee']
                row.update(simulated=True, fee=fill['fee'], units=fill['units'], **dealing)
                # Reflect the submitted amount/freeze in the displayed capital
                # summary using the same production engine a second time.
                _mark(fund, book, nav, on)
                reference_after = investment_plan_reference(fund, orders, at)
            except Problem as exc:
                row.update(action='WAIT', amount=None, units=None, fee=None,
                           status=exc.code, reason=exc.message)
                reference_after = reference
        else:
            reference_after = reference
        summary = fund['position_buckets']
        row.update(t_units=summary['groups']['TACTICAL']['units'],
                   t_available_units=summary['groups']['TACTICAL']['available_units'],
                   t_value=summary['groups']['TACTICAL']['value'],
                   holding_value=summary['holding_value'], capital_used=reference_after['plan']['committed_holding'],
                   headroom=reference_after['plan']['headroom'])
        rows.append(row)
        fund['history'].append(_quote(on, nav, close=True))
        fund['nav'] = fund['history'][-1]
    known_end = None
    period_pnl = None
    cumulative = None
    pending_buy = sum((item['fill']['gross'] for item in pending if not item['order']['closed'] and item['order']['side'] == 'BUY'), ZERO)
    if completed_dates == len(multipliers):
        known_end = money(fund['units'] * nav) + pending_buy + receivable
        period_pnl = known_end - initial - contributions
        cumulative = book.summary(nav, dates[-1])['total_pnl']
    incomplete = any(row['status'] in INCOMPLETE for row in rows)
    complete = not existing and completed_dates == len(multipliers) and not incomplete
    return {'id': code, 'label': label, 'rows': rows, 'status': 'READY' if complete else 'PARTIAL',
            'initial_value': initial, 'external_contributions': contributions, 'estimated_fees': fees,
            'pending_buy': pending_buy, 'virtual_receivable': receivable,
            'existing_in_flight_count': len(existing), 'ending_holding_value': money(fund['units'] * nav) if completed_dates == len(multipliers) else None,
            'known_scope_assets': known_end, 'known_scope_period_pnl': period_pnl,
            'ending_assets': known_end if complete else None, 'period_pnl': period_pnl if complete else None,
            'cumulative_pnl': cumulative if complete else None,
            'reason': '已有在途保持冻结；金额仅覆盖已确认持仓与本次情景。' if existing else
                      '日历或交易资料不足，相关期不模拟成交，完整期末金额留空。' if not complete else '固定假设路径，不是收益预测或真实订单。'}


def plan_scenarios(fund, orders, now):
    """Calculate exactly three eight-step paths without mutation or I/O."""
    profile = fund.get('investment_plan_profile') or {}
    result = {'fund_id': fund['id'], 'method_version': METHOD_VERSION, 'generated_at': now,
              'profile_version': profile.get('_version', 0), 'rule_version': (fund.get('rules') or {}).get('_version', 0),
              'status': 'WAIT', 'reason': '', 'h': None, 'initial_nav': None, 'initial_nav_date': None,
              'period_count': len(PATHS[0][2]), 'scenarios': [], 'assumptions': ASSUMPTIONS,
              'calendar_label': '参考期为假设路径；日期仅用于原规则申请、确认及费用计算。',
              'ledger_posted': False}
    def wait(code, reason):
        return dict(result, status=code, reason=reason)
    if not profile.get('enabled'):
        return wait('PLAN_DISABLED', '尚未启用投资计划；预演不会自动启用或填写总预算。')
    if profile.get('template') not in {TEMPLATE, TEMPLATE_V2}:
        return wait('PLAN_UNSUPPORTED', '当前计划模板尚未支持预演。')
    summary = fund.get('position_buckets') or {}
    groups = summary.get('groups') or {}
    if fund.get('initialization_pending') or summary.get('status') == 'BLOCKED' or set(groups) != BUCKETS or not isinstance(summary.get('positions'), list):
        return wait('HOLDINGS_UNKNOWN', '需先完成可核对的当前持仓与分仓。')
    if any(groups[k].get('cost') is None or groups[k].get('realized') is None for k in BUCKETS):
        return wait('COST_UNKNOWN', '两仓成本或已实现收益未知，暂不输出期末损益。')
    if not (fund.get('rules') or {}).get('verified'):
        return wait('RULES_UNVERIFIED', '需先核实本基金费用规则；预演不会填写默认费用。')
    units = dec(fund.get('units', ZERO))
    if (sum((dec(l['units']) for l in fund.get('lots', [])), ZERO) != units or
            sum((dec(l['units']) for l in summary['positions']), ZERO) != units):
        return wait('HOLDINGS_MISMATCH', '实物批次、分仓与当前总份额不一致，请先核对。')
    if ((fund.get('nav_sync') or {}).get('corporate_actions') or
            (fund.get('nav_sync') or {}).get('corporate_actions_complete') is False or
            (fund.get('daily_t') or {}).get('presets', {}).get('blocked_by') == 'CORPORATE_ACTION_REVIEW'):
        return wait('CORPORATE_ACTION_REVIEW', '分红、拆分或复权资料需核对，暂不预演。')
    visible = [r for r in fund.get('history', []) if r.get('value_kind') == 'OFFICIAL_NAV' and
               stamp(r['observed_at']) <= stamp(now) and (not r.get('as_of') or stamp(r['as_of']) <= stamp(now)) and
               (not r.get('published_at') or stamp(r['published_at']) <= stamp(now))]
    if not visible:
        return wait('NAV_MISSING', '缺少最近正式净值起点。')
    anchor = max(visible, key=lambda r: (r['effective_date'], r['observed_at'], r['id']))
    nav = dec(anchor['value'], positive=True)
    if fund.get('valuation_adjusted'):
        return wait('CORPORATE_ACTION_REVIEW', '净值有份额调整，需先核对情景起点。')
    result.update(initial_nav=nav, initial_nav_date=anchor['effective_date'], holding_cap=profile.get('holding_cap'), template=profile['template'])
    h = Decimal('.01')
    if profile['template'] == TEMPLATE_V2:
        # The first mapped period gives the exact production rule's strictly
        # earlier-history threshold, including continuity and disclosure checks.
        dates = [d for d in reference_sessions() if d > anchor['effective_date']][:len(PATHS[0][2])]
        if dates:
            initial = deepcopy(fund)
            initial['daily_t']['market'] = market_session(dates[0] + 'T14:00:00+08:00', initial['daily_t']['market']['scope'])
            initial['daily_t']['today'] = {'status': 'MISSING'}
            risk = investment_risk(initial, orders, dates[0] + 'T14:00:00+08:00', money(dec(profile['holding_cap'], positive=True)), ZERO, money(dec(fund['units']) * nav))
            if not risk['ready']:
                return wait(risk['status'], '；'.join(risk['reasons']))
            h = risk['h']
    result['h'] = h
    if h * Decimal('2.4') >= 1:
        return wait('PATH_RANGE_UNSUPPORTED', '当前波动尺度过大，固定路径不再适用。')
    dates = [d for d in reference_sessions() if d > anchor['effective_date']][:len(PATHS[0][2])]
    own_orders = [o for o in orders if o['fund_id'] == fund['id']]
    result['scenarios'] = [_scenario(fund, own_orders, nav, anchor['effective_date'], h, path, dates) for path in PATHS]
    result.update(status='READY' if all(s['status'] == 'READY' for s in result['scenarios']) else 'PARTIAL',
                  reason='仅按固定假设路径检查现有计划行为；不保存交易或预测。')
    return result
