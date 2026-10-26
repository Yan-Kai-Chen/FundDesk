from copy import deepcopy
from decimal import Decimal as D

from app.domain.buckets import BucketBook
from app.domain.holding_composition import holding_composition


def summary(base_units='1884.57', t_units='17.20', base_cost='1637.37', t_cost='15.27', nav='.8951'):
    book=BucketBook()
    if D(base_units): book.buy('base',base_units,base_cost,'BASE','2026-09-01T02:00:00Z')
    if D(t_units): book.buy('t',t_units,t_cost,'TACTICAL','2026-09-01T02:00:00Z')
    book.realized['TACTICAL']=D('29.94')
    return book.summary(nav,'2026-10-23')


def test_true_small_t_allocation_and_profit_composition_are_distinct_readonly():
    source=summary();before=deepcopy(source)
    view=holding_composition(source);base,t=view['groups'].values()
    assert view['total']['value']==source['holding_value']
    assert base['weight']+t['weight']==1 and D('0')<t['weight']<D('.01')
    assert view['total']['pnl']==base['pnl']+t['pnl']==source['total_pnl']
    assert base['pnl_weight']+t['pnl_weight']==1
    assert t['pnl_weight']>t['weight']
    assert t['pnl']==t['unrealized']+D('29.94')
    assert t['holding_return']==(t['unrealized']/t['cost']).quantize(D('.000000000001'))
    assert source==before


def test_opposing_profits_use_absolute_weights_not_percent_of_small_or_zero_net():
    source=summary(base_units='100',t_units='100',base_cost='120',t_cost='80',nav='1')
    source['groups']['TACTICAL']['realized']=D('0')
    source['groups']['TACTICAL']['pnl']=D('20');source['total_pnl']=D('0')
    view=holding_composition(source);base,t=view['groups'].values()
    assert base['pnl']==D('-20') and t['pnl']==D('20')
    assert base['pnl_direction']=='LOSS' and t['pnl_direction']=='GAIN'
    assert base['pnl_weight']==t['pnl_weight']==D('.5') and view['total']['pnl']==0
    assert view['pnl_magnitude']==D('40')


def test_all_loss_and_zero_pnl_never_get_negative_or_undefined_chart_geometry():
    source=summary(base_units='100',t_units='100',base_cost='120',t_cost='110',nav='1')
    source['groups']['TACTICAL'].update(realized=D('0'),pnl=D('-10'))
    view=holding_composition(source)
    assert all(g['pnl_direction']=='LOSS' and 0<=g['pnl_weight']<=1 for g in view['groups'].values())
    for g in source['groups'].values():g.update(pnl=D('0'),unrealized=D('0'))
    view=holding_composition(source)
    assert view['pnl_status']=='FLAT' and all(g['pnl_weight'] is None for g in view['groups'].values())


def test_zero_current_holdings_can_retain_realized_profit_without_fake_allocation():
    view=holding_composition(summary(base_units='0',t_units='0'))
    assert view['status']=='EMPTY' and view['total']['value']==0
    assert view['total']['pnl']==D('29.94') and view['pnl_status']=='READY'
    assert all(g['weight'] is None for g in view['groups'].values())
    assert view['groups']['TACTICAL']['pnl_weight']==1


def test_unknown_cost_keeps_known_allocation_but_never_invents_profit_or_return():
    source=summary(base_cost=None)
    view=holding_composition(source)
    assert view['status']=='READY' and view['groups']['BASE']['weight'] is not None
    assert view['total']['pnl'] is None and view['total']['holding_return'] is None
    assert view['groups']['BASE']['holding_return'] is None
    assert view['pnl_status']=='UNKNOWN'


def test_missing_or_blocked_book_is_unknown_not_an_empty_zero_account():
    for source in (None,{'status':'BLOCKED'}):
        view=holding_composition(source)
        assert view['status']=='UNKNOWN' and view['total']['value'] is None
        assert all(g['weight'] is None for g in view['groups'].values())
