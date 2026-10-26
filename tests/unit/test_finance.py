from decimal import Decimal as D
import pytest
from app.domain.finance import dec, Problem, purchase_preview, redemption_preview, solve_net_target, allocate_budget, trade_date


def test_g01_purchase():
    r=purchase_preview("1000.00","1.2500",{"verified":True,"purchase_rate":"0.0015","unit_step":"0.01"})
    assert (r['net'],r['fee'],r['units'])==(D('998.50'),D('1.50'),D('798.80'))


def test_g02_fifo_and_n01():
    rule={"verified":True,"lot_method":"FIFO","redemption_tiers":[{"min_days":0,"max_days":7,"rate":"0.015"},{"min_days":7,"max_days":None,"rate":"0"}]}
    lots=[{"id":"old","units":"1000","cost":"1000","acquired":"2026-01-01"},{"id":"new","units":"500","cost":"525","acquired":"2026-10-19"}]
    r=redemption_preview(lots,'1200','1.1',rule,'2026-10-20')
    assert (r['gross'],r['fee'],r['net'])==(D('1320'),D('3.30'),D('1316.70'))
    assert sum(a['cost'] for a in r['allocations'])==D('1210')
    assert r['net']-sum(a['cost'] for a in r['allocations'])==D('106.70')


def test_g03_discrete_net_target():
    rule={"verified":True,"lot_method":"FIFO","unit_step":"0.01","redemption_tiers":[{"min_days":0,"max_days":None,"rate":"0.001"}]}
    lots=[{"id":"a","units":"1000","cost":"1000","acquired":"2026-01-01"}]
    r=solve_net_target(lots,'1000','1.25',rule,'2026-10-20')
    assert r['units']==D('800.80') and r['net']==D('1000.00')
    assert redemption_preview(lots,'800.79','1.25',rule,'2026-10-20')['net']==D('999.99')


def test_g04_budget_does_not_use_receivables():
    assert allocate_budget(['1500','1500'],D('6000')-D('3000')-D('1000'))==[D('1500'),D('500')]


def test_g05_cutoff_tokyo_and_exclusive_boundary():
    rule={"verified":True,"open_dates":["2026-10-20","2026-10-21"],"cutoff":"15:00:00"}
    assert trade_date('2026-10-20T15:59:00+09:00',rule)=='2026-10-20'
    assert trade_date('2026-10-20T16:00:00+09:00',rule)=='2026-10-21'
    assert trade_date('2026-10-20T16:01:00+09:00',rule)=='2026-10-21'


@pytest.mark.parametrize('value',[float('nan'),1.1,'NaN','Infinity','-1','1000000000000000','0.0000000000001'])
def test_invalid_financial_numbers(value):
    with pytest.raises(Problem): dec(value)


def test_unknown_fee_and_unknown_holding_date():
    with pytest.raises(Problem): purchase_preview('100','1',{})
    with pytest.raises(Problem): redemption_preview([{'id':'a','units':'100','acquired':None}], '10','1',{'verified':True,'lot_method':'FIFO'},'2026-10-20')


def test_fee_boundary_day_seven():
    rule={"verified":True,"lot_method":"FIFO","redemption_tiers":[{"min_days":0,"max_days":7,"rate":"0.015"},{"min_days":7,"max_days":None,"rate":"0"}]}
    lot=[{"id":"a","units":"100","acquired":"2026-10-13"}]
    assert redemption_preview(lot,'100','1',rule,'2026-10-19')['fee']==D('1.50')
    assert redemption_preview(lot,'100','1',rule,'2026-10-20')['fee']==0
