from decimal import Decimal as D

from app.domain.t_review import exit_reference, prefix_fingerprint, scenario_review


def fixture(units='100', nav='1', sales=None):
    return {'id':'fund-a','units':D(units),'valuation_nav':D(nav),'nav':{'value':nav,'effective_date':'2026-10-20'},
            'return_valuation':{'date':'2026-10-20','label':'今日正式'},
            'position_buckets':{'status':'CONFIRMED','sales':sales or []}}


def scenario(fund, events=(), orders=None):
    state={'revision':0,'orders':orders or {},'dividends':{},'audit':[]}
    baseline={'units':'100','nav':'1','holding_value':'100','started_at':'2026-10-19T01:00:00Z','revision':0,
              'committed_order_ids':[],'prefix_fingerprint':prefix_fingerprint(state,'fund-a',0)}
    state['audit']=list(events)
    return scenario_review(fund,state,baseline)


def event(seq,kind,payload):
    return {'seq':seq,'id':str(seq),'kind':kind,'payload':payload,'reversed':False}


def test_exit_reference_includes_fee_and_does_not_call_realized_profit_alpha():
    f=fixture(nav='1.2',sales=[{'id':'s1','intent':'TACTICAL','units':'10','net':'10.89','fee':'.11','at':'2026-10-19T01:00:00Z'},
        {'id':'s2','intent':'BASE','units':'20','net':'25','at':'2026-10-19T01:00:00Z'}])
    r=exit_reference(f)
    assert r['difference']==D('-1.11') and r['count']==1
    assert r['net_redemptions']==D('10.89') and r['held_value']==D('12')
    assert '逐次退出参照合计，不等同整个基金做 T 增益' in r['detail']


def test_no_sales_is_zero_not_unknown_or_fake_profit():
    r=exit_reference(fixture())
    assert r['difference']==0 and r['count']==0 and r['status']=='READY'


def test_unsettled_proceeds_are_receivable_not_reusable_cash():
    orders={'s':{'id':'s','fund_id':'fund-a','side':'SELL','bucket':'TACTICAL','closed':True}}
    events=[event(1,'ORDER_CONFIRMED',{'order_id':'s','units':'10','gross':'12','net':'11.9'})]
    r=scenario(fixture(units='90',nav='1.1'),events,orders)
    assert r['virtual_cash']==0 and r['receivable']==D('11.90')
    assert r['actual_value']==D('110.9') and r['hold_value']==D('110') and r['difference']==D('.90')


def test_recycled_proceeds_are_not_new_external_contributions_or_free_money():
    orders={'s':{'id':'s','fund_id':'fund-a','side':'SELL','bucket':'TACTICAL','closed':True},
            'b':{'id':'b','fund_id':'fund-a','side':'BUY','bucket':'TACTICAL','closed':True}}
    events=[event(1,'ORDER_CONFIRMED',{'order_id':'s','units':'20','gross':'24','net':'24'}),
            event(2,'ORDER_SETTLED',{'order_id':'s','amount':'24'}),event(3,'ORDER_PAID',{'order_id':'b','amount':'24'}),
            event(4,'ORDER_CONFIRMED',{'order_id':'b','units':'24','gross':'24','net':'24'})]
    r=scenario(fixture(units='104',nav='1.1'),events,orders)
    assert r['external_contributions']==0 and r['virtual_cash']==0 and r['receivable']==0
    assert r['hold_units']==D('100') and r['difference']==D('4.40')


def test_buy_before_settlement_is_new_money_and_later_cash_is_retained_once():
    orders={'s':{'id':'s','fund_id':'fund-a','side':'SELL','bucket':'TACTICAL','closed':True},
            'b':{'id':'b','fund_id':'fund-a','side':'BUY','closed':True}}
    events=[event(1,'ORDER_CONFIRMED',{'order_id':'s','units':'20','gross':'24','net':'24'}),
            event(2,'ORDER_PAID',{'order_id':'b','amount':'24'}),event(3,'ORDER_CONFIRMED',{'order_id':'b','units':'24','gross':'24','net':'24'}),
            event(4,'ORDER_SETTLED',{'order_id':'s','amount':'24'})]
    r=scenario(fixture(units='104',nav='1.1'),events,orders)
    assert r['external_contributions']==D('24') and r['virtual_cash']==D('24')
    assert r['hold_units']==D('124') and r['difference']==D('2')


def test_same_price_round_trip_only_loses_fee():
    orders={'s':{'id':'s','fund_id':'fund-a','side':'SELL','bucket':'TACTICAL','closed':True},
            'b':{'id':'b','fund_id':'fund-a','side':'BUY','closed':True}}
    events=[event(1,'ORDER_CONFIRMED',{'order_id':'s','units':'20','gross':'20','net':'19'}),
            event(2,'ORDER_SETTLED',{'order_id':'s','amount':'19'}),event(3,'ORDER_PAID',{'order_id':'b','amount':'20'}),
            event(4,'ORDER_CONFIRMED',{'order_id':'b','units':'20','gross':'20','net':'20'})]
    r=scenario(fixture(),events,orders)
    assert r['external_contributions']==D('1') and r['hold_units']==D('101') and r['difference']==D('-1')


def test_base_sale_and_pending_trade_do_not_output_precise_alpha():
    sale={'s':{'id':'s','fund_id':'fund-a','side':'SELL','bucket':'BASE','closed':True}}
    r=scenario(fixture(units='90'),[event(1,'ORDER_CONFIRMED',{'order_id':'s','units':'10','gross':'10','net':'10'})],sale)
    assert r['status']=='PARTIAL' and r['difference'] is None and r['reasons'][0]['code']=='BASE_SALE'
    r=scenario(fixture(),(),{'b':{'id':'b','fund_id':'fund-a','side':'BUY','closed':False,'paid':D('5')}})
    assert r['status']=='PARTIAL' and r['difference'] is None


def test_no_operation_same_units_revalues_both_sides_equally():
    r=scenario(fixture(nav='1.5'))
    assert r['difference']==0 and r['actual_gain']==D('50') and r['hold_gain']==D('50')


def test_current_estimate_quote_uses_same_mark_in_both_reference_sides():
    f=fixture(units='100',nav='1')
    f['return_valuation'].update(quote_id='estimate',is_estimate=True,label='收盘估算')
    f['daily_t']={'today':{'quote':{'id':'estimate','value':'1.1234'}}}
    r=scenario(f)
    assert r['actual_value']==D('112.34') and r['hold_value']==D('112.34') and r['difference']==0
    assert r['quote']['is_estimate'] is True


def test_old_event_reversal_invalidates_the_frozen_baseline():
    e=event(1,'OPENING_LOT',{'fund_id':'fund-a','units':'100'})
    state={'orders':{},'dividends':{},'audit':[e]}
    baseline={'units':'100','nav':'1','holding_value':'100','started_at':'2026-10-19T01:00:00Z','revision':1,
              'prefix_fingerprint':prefix_fingerprint(state,'fund-a',1)}
    state['audit'][0]['reversed']=True
    r=scenario_review(fixture(),state,baseline)
    assert r['status']=='PARTIAL' and r['difference'] is None and r['reasons'][0]['code']=='BASELINE_CORRECTED'
