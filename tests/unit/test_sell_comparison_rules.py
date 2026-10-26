from copy import deepcopy
from decimal import Decimal as D

import pytest

from app.domain.buckets import BucketBook
from app.domain.finance import Problem, money
from app.domain.sell_comparison import buy_price_comparison

AT = '2026-10-23T06:35:00Z'
ON = '2026-10-23'


def setup(bucket='BASE', units='362.32', cost='300', available='2026-10-26'):
    b = BucketBook()
    b.buy('legacy', '1000', '1000', 'TACTICAL', '2026-06-01T04:00:00Z', '2026-06-03')
    b.buy('confirm-new', units, cost, bucket, '2026-10-22T16:09:00Z', available)
    order = {'id': 'new', 'fund_id': 'f', 'side': 'BUY', 'bucket': bucket,
             'submitted_at': '2026-10-22T06:00:00Z', 'confirmations': [{'event_id': 'confirm-new'}]}
    return {'id': 'f', 'position_buckets': b.summary()}, [order]


def compare(f=None, orders=None, units='600', fee='2.50', reference=None, nav='.88276', gross=None):
    if f is None:
        f, orders = setup()
    return buy_price_comparison(f, orders, {'units': D(units), 'fee': D(fee),
                                'gross': D(gross) if gross is not None else money(D(units)*D(nav))}, D(nav), ON, AT, reference)


def test_recent_base_buy_is_price_reference_not_t_profit_or_actual_lot():
    f, orders = setup()
    before = deepcopy((f, orders))
    r = compare(f, orders)
    assert r['status'] == 'READY' and r['bucket'] == 'BASE'
    assert r['covered_units'] == D('362.32') and r['uncovered_units'] == D('237.68')
    assert r['gross_price_edge'] == r['remaining_holding_pnl'] == D('19.84')
    assert r['fee_share'] == D('1.51') and r['net_price_edge'] == D('18.33')
    assert r['available_units'] == 0 and r['covered_available_units'] == 0
    assert not r['ledger_posted'] and not r['included_in_total_pnl']
    assert (f, orders) == before


def test_auto_prefers_confirmed_t_buy_explicit_selection_can_compare_base():
    f, orders = setup()
    b = BucketBook()
    b.buy('confirm-t', '100', '90', 'TACTICAL', '2026-10-21T04:00:00Z', '2026-10-22')
    f['position_buckets']['positions'] += b.summary()['positions']
    t = {'id': 't', 'fund_id': 'f', 'side': 'BUY', 'bucket': 'TACTICAL',
         'submitted_at': '2026-10-21T03:00:00Z', 'confirmations': [{'event_id': 'confirm-t'}]}
    orders.append(t)
    assert compare(f, orders)['order_id'] == 't'
    assert compare(f, orders, reference='new')['bucket'] == 'BASE'
    assert f['position_buckets']['positions'][-1]['bucket'] == 'TACTICAL'


def test_partial_position_and_reservation_use_remaining_proportional_cost():
    f, orders = setup(bucket='TACTICAL', units='100', cost='80', available='2026-10-23')
    p = f['position_buckets']['positions'][-1]
    # A previous sale left sixty units, then another live sale reserved twenty.
    p.update(units=D('60'), cost=D('48'), reserved=D('20'))
    r = compare(f, orders, units='20', fee='1', nav='1')
    assert r['remaining_units'] == 40 and r['remaining_cost'] == 32
    assert r['covered_cost'] == 16 and r['gross_price_edge'] == 4
    assert r['fee_share'] == 1 and r['net_price_edge'] == 3


def test_purchase_fee_is_already_in_cost_and_not_deducted_twice():
    f, orders = setup(units='100', cost='100.50')
    r = compare(f, orders, units='50', fee='1', nav='1.1')
    assert r['covered_cost'] == D('50.25') and r['gross_price_edge'] == D('4.75')
    assert r['net_price_edge'] == D('3.75')


def test_unknown_cost_is_missing_not_fictitious_gain():
    f, orders = setup(cost=None)
    r = compare(f, orders)
    assert r['status'] == 'UNKNOWN_COST' and r['net_price_edge'] is None
    assert r['covered_cost'] is None and r['remaining_holding_pnl'] is None


def test_full_coverage_uses_channel_batch_rounding_not_a_second_proceeds_estimate():
    f, orders = setup(units='.03', cost='.03')
    # Two .01-unit channel lots each round 0.015 to .02: actual gross is .04,
    # although the aggregate NAV valuation for .02 units is only .03.
    r = compare(f, orders, units='.02', fee='0', nav='1.5', gross='.04')
    assert r['covered_gross'] == D('.04') and r['covered_cost'] == D('.02')
    assert r['net_price_edge'] == D('.02') and r['uncovered_units'] == 0


def test_unconfirmed_fully_reserved_or_already_consumed_purchase_has_no_reference():
    f, orders = setup()
    orders[0]['confirmations'] = []
    assert compare(f, orders)['status'] == 'NO_REMAINING_BUY'
    f, orders = setup()
    p = f['position_buckets']['positions'][-1]
    p['reserved'] = p['units']
    assert compare(f, orders, reference='new')['status'] == 'NO_REMAINING_BUY'
    assert compare(f, orders, reference='new')['order_id'] is None


def test_other_funds_future_and_invalid_ids_cannot_be_selected():
    f, orders = setup()
    orders[0]['fund_id'] = 'other'
    assert compare(f, orders)['status'] == 'NO_REMAINING_BUY'
    with pytest.raises(Problem) as exc:
        compare(f, orders, reference='new')
    assert exc.value.code == 'BUY_REFERENCE_NOT_FOUND'
    for ref in ([], {}, 42):
        with pytest.raises(Problem) as exc:
            compare(f, orders, reference=ref)
        assert exc.value.code == 'INVALID_BUY_REFERENCE'
    f, orders = setup()
    orders[0]['submitted_at'] = '2026-10-24T04:00:00Z'
    assert compare(f, orders)['status'] == 'NO_REMAINING_BUY'
