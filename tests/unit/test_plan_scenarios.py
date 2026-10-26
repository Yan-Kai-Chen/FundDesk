from copy import deepcopy
from decimal import Decimal as D

import pytest

from app.domain.buckets import BucketBook
from app.domain.finance import ZERO
from app.domain.investment_plan import investment_plan_reference
from app.domain.market_calendar import market_session
from app.domain.plan_scenarios import plan_scenarios
from app.domain.research_calendar import reference_sessions

NOW = '2026-10-20T12:30:00Z'


def fund(template='DRAWDOWN_GUARD_V2', anchor='2026-10-20'):
    dates = [d for d in reference_sessions() if d <= anchor][-81:]
    history = [{'id': 'official:' + d, 'value': D('1'), 'effective_date': d, 'value_kind': 'OFFICIAL_NAV',
                'observed_at': d + 'T16:30:00+08:00', 'as_of': None, 'published_at': None} for d in dates]
    book = BucketBook()
    book.buy('base', '1100', '1100', 'BASE', '2026-06-01', '2026-06-03')
    book.buy('t', '900', '900', 'TACTICAL', '2026-06-01', '2026-06-03')
    return {'id': 'f', 'fund_code': '012738', 'share_class': 'C', 'units': D('2000'),
            'sellable_units': D('2000'), 'nav': history[-1], 'history': history, 'valuation_nav': D('1'),
            'market_value': D('2000'), 'position_buckets': book.summary('1', anchor),
            'investment_plan_profile': {'enabled': True, 'holding_cap': '4000', 'template': template, '_version': 2},
            'daily_t': {'market': market_session(NOW, 'CN'), 'today': {'status': 'MISSING'}, 'presets': {}},
            'rules': {'verified': True, 'effective_from': '2026-01-01', '_version': 3,
                      'purchase_rate': '0', 'lot_method': 'FIFO', 'unit_step': '.01',
                      'redemption_tiers': [{'min_days': 0, 'max_days': 7, 'rate': '.015'},
                                           {'min_days': 7, 'max_days': 30, 'rate': '.005'},
                                           {'min_days': 30, 'max_days': None, 'rate': '0'}]},
            'lots': [{'id': 'physical', 'units': D('2000'), 'cost': D('2000'), 'reserved': ZERO,
                      'acquired': '2026-06-01', 'available_from': '2026-06-03'}]}


def path(result, code='CONTINUED_DECLINE'):
    return next(s for s in result['scenarios'] if s['id'] == code)


def test_three_bounded_paths_do_not_mutate_source_and_repeat_identically():
    f, orders = fund(), []
    before = deepcopy((f, orders))
    a = plan_scenarios(f, orders, NOW)
    assert a == plan_scenarios(f, orders, NOW) and (f, orders) == before
    assert a['status'] == 'READY' and a['h'] == D('.01') and a['ledger_posted'] is False
    assert len(a['scenarios']) == 3 and all(len(s['rows']) == 8 for s in a['scenarios'])


def test_first_step_matches_production_engine_and_continuous_decline_pauses():
    f = fund()
    result = path(plan_scenarios(f, [], NOW))
    first = result['rows'][0]
    direct = deepcopy(f)
    direct['daily_t']['market'] = market_session('2026-10-21T14:00:00+08:00', 'CN')
    direct['daily_t']['today'] = {'status': 'READY', 'change': D('-.012'), 'baseline': f['nav'],
        'quote': {'id': 'probe', 'value': D('.988'), 'effective_date': '2026-10-21', 'value_kind': 'LOCAL_ESTIMATE'}}
    expected = investment_plan_reference(direct, [], '2026-10-21T14:00:00+08:00')
    assert first['action'] == expected['action'] == 'BUY' and first['amount'] == expected['amount'] == D('200')
    assert first['capital_used'] == D('2200')  # max(cost, value) plus new pending amount.
    assert result['rows'][1]['status'] == 'BUY_COOLDOWN'
    assert all(r['risk_state'] == '暂停加仓' and not r['simulated'] for r in result['rows'][3:])
    assert result['external_contributions'] == D('200') and result['period_pnl'] < 0


def test_buy_uses_application_period_price_and_production_t1_t2_dates():
    rows = path(plan_scenarios(fund(), [], NOW))['rows']
    assert rows[0]['trade_date'] == '2026-10-21'
    assert rows[0]['confirm_date'] == '2026-10-22'
    assert rows[0]['available_from'] == '2026-10-23'
    assert rows[0]['units'] == D('202.43')  # 200/.988, not the next period's .976144.
    assert rows[0]['t_units'] == 900 and rows[1]['t_units'] == D('1102.43')
    assert rows[1]['t_available_units'] == 900 and rows[2]['t_available_units'] == D('1102.43')


def test_purchase_fee_and_rounding_enter_actual_units_without_double_counting_pnl():
    f = fund(); f['rules']['purchase_rate'] = '.01'
    falling = path(plan_scenarios(f, [], NOW))
    assert falling['rows'][0]['fee'] == D('1.98')
    assert falling['rows'][0]['units'] == D('200.43')
    assert falling['estimated_fees'] == D('1.98') and falling['external_contributions'] == D('200')
    assert falling['pending_buy'] == 0
    assert falling['period_pnl'] == falling['ending_assets'] - D('2200')
    assert falling['cumulative_pnl'] == falling['period_pnl']


def test_fifo_fee_free_t_protection_survives_and_no_base_fallback():
    f = fund('DAILY_LADDER_V1')
    f['lots'][0]['acquired'] = '2026-10-13'
    rising = path(plan_scenarios(f, [], NOW), 'CONTINUED_RISE')
    assert all(not r['simulated'] and r['status'] in {'NO_FEE_FREE_T_UNITS', 'HOLD'} for r in rising['rows'])
    for lot in f['position_buckets']['positions']:
        lot['bucket'] = 'BASE'
    f['position_buckets']['groups']['BASE'].update(units=D('2000'), cost=D('2000'))
    f['position_buckets']['groups']['TACTICAL'].update(units=ZERO, cost=ZERO, available_units=ZERO)
    f['lots'][0]['acquired'] = '2026-06-01'
    rising = path(plan_scenarios(f, [], NOW), 'CONTINUED_RISE')
    assert all(not r['simulated'] for r in rising['rows'])


def test_profitable_sells_keep_net_realized_buffer_and_virtual_receivable():
    rising = path(plan_scenarios(fund('DAILY_LADDER_V1'), [], NOW), 'CONTINUED_RISE')
    sales = [r for r in rising['rows'] if r['simulated']]
    assert sales and all(r['action'] == 'SELL' and r['fee'] == 0 for r in sales)
    assert sales[0]['t_available_units'] == D('900') - sales[0]['units']
    assert all(r['expected_realized_pnl'] >= max(D('2'), r['amount'] * D('.005')) for r in sales)
    assert rising['virtual_receivable'] > 0 and rising['external_contributions'] == 0
    assert rising['ending_assets'] == rising['ending_holding_value'] + rising['virtual_receivable']
    assert rising['period_pnl'] == rising['ending_assets'] - rising['initial_value']


def test_v1_also_obeys_plan_cap_without_inferring_wallet_cash():
    f = fund('DAILY_LADDER_V1')
    f['investment_plan_profile']['holding_cap'] = '2100'
    falling = path(plan_scenarios(f, [], NOW))
    assert falling['rows'][0]['amount'] == D('105')  # V1 marked-value occupancy.
    assert all(r['capital_used'] <= D('2100') for r in falling['rows'])
    assert falling['external_contributions'] > 0


def test_existing_orders_remain_frozen_and_end_totals_are_not_falsely_complete():
    orders = [{'id': 'original', 'fund_id': 'f', 'side': 'BUY', 'bucket': 'BASE', 'requested': D('300'),
               'confirmed_gross': ZERO, 'confirmed_units': ZERO, 'submitted_at': '2026-10-20T06:00:00Z', 'closed': False}]
    result = path(plan_scenarios(fund(), orders, NOW))
    assert result['status'] == 'PARTIAL' and result['existing_in_flight_count'] == 1
    assert result['ending_assets'] is None and result['period_pnl'] is None
    assert result['known_scope_assets'] is not None
    assert result['rows'][0]['capital_used'] == D('2500')  # original300 + new200.
    assert orders[0]['closed'] is False


def test_prior_valid_buy_controls_rolling_limit_and_cross_day_cooldown():
    orders = [{'id': 'original', 'fund_id': 'f', 'side': 'BUY', 'bucket': 'TACTICAL', 'requested': D('100'),
               'confirmed_gross': D('100'), 'confirmed_units': D('100'), 'submitted_at': '2026-10-20T06:00:00Z', 'closed': True}]
    falling = path(plan_scenarios(fund(), orders, NOW))
    assert falling['rows'][0]['status'] == 'BUY_COOLDOWN'
    assert falling['rows'][1]['status'] == 'BUY_COOLDOWN'
    assert falling['rows'][2]['amount'] == D('100') and falling['rows'][2]['simulated']


def test_verified_fund_calendar_is_not_bypassed_with_synthetic_open_dates():
    f = fund('DAILY_LADDER_V1')
    f['rules'].update(calendar_verified=True, open_dates=['2026-10-20'])
    result = plan_scenarios(f, [], NOW)
    assert all(not r['simulated'] and r['amount'] in (None, ZERO) for s in result['scenarios'] for r in s['rows'])
    assert any(r['status'] == 'CALENDAR_MISSING' for r in path(result)['rows'])
    assert result['status'] == 'PARTIAL' and path(result)['ending_assets'] is None


def test_year_boundary_leaves_reference_numbers_but_no_invented_dates_or_totals():
    f = fund('DAILY_LADDER_V1', '2026-12-31')
    result = plan_scenarios(f, [], '2026-12-31T12:30:00Z')
    assert result['status'] == 'PARTIAL'
    for s in result['scenarios']:
        assert s['ending_assets'] is None and s['period_pnl'] is None
        assert all(r['date'] is None and r['amount'] is None for r in s['rows'])


@pytest.mark.parametrize('mutation,status', [
    (lambda f: f['investment_plan_profile'].update(enabled=False), 'PLAN_DISABLED'),
    (lambda f: f.update(history=[]), 'NAV_MISSING'),
    (lambda f: f['position_buckets']['groups']['TACTICAL'].update(cost=None), 'COST_UNKNOWN'),
    (lambda f: f.update(nav_sync={'corporate_actions_complete': False}), 'CORPORATE_ACTION_REVIEW'),
    (lambda f: f.update(history=f['history'][-10:]), 'RISK_HISTORY_MISSING'),
    (lambda f: f['rules'].update(verified=False), 'RULES_UNVERIFIED'),
])
def test_missing_facts_never_become_filled_results(mutation, status):
    f = fund(); mutation(f)
    result = plan_scenarios(f, [], NOW)
    assert result['status'] == status and result['scenarios'] == []
