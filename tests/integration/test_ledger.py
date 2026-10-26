from decimal import Decimal as D
from concurrent.futures import ThreadPoolExecutor
import pytest
from app.domain.finance import Problem
from app.persistence.store import Store
from conftest import event,deposit,opening,order,confirm,AT


def test_g08_paid_pending_and_confirmation_conserve_assets(wb):
    deposit(wb)
    oid=order(wb)
    s=wb.snapshot();assert s['portfolio']['cash']==D('10000') and s['portfolio']['available_cash']==D('9000') and s['portfolio']['pending_buy']==0
    event(wb,'ORDER_PAID',{'order_id':oid,'amount':'1000'})
    s=wb.snapshot();assert s['portfolio']['equity']==D('10000') and s['portfolio']['reserved_cash']==0 and s['portfolio']['pending_buy']==D('1000')
    confirm(wb,oid)
    wb.observe({'fund_id':wb.fid,'value_kind':'OFFICIAL_NAV','value':'1.25','effective_date':'2026-10-20'})
    s=wb.snapshot();assert s['portfolio']['equity']==D('9998.50') and s['portfolio']['pending_buy']==0 and s['lots'][0]['cost']==D('1000')


def test_n01_redemption_and_settlement_not_double_counted(wb):
    opening(wb,'400','400',id='old')
    opening(wb,'600','660',acquired='2026-10-19',id='new',available='2026-10-20')
    wb.observe({'fund_id':wb.fid,'value_kind':'OFFICIAL_NAV','value':'1.22','effective_date':'2026-10-20'})
    oid=order(wb,'SELL','500')
    assert wb.snapshot()['portfolio']['equity']==D('1220')
    confirm(wb,oid,'500','1.22','610','1.83','608.17')
    s=wb.snapshot();assert s['portfolio']['equity']==D('1218.17') and s['portfolio']['realized']==D('98.17')
    event(wb,'ORDER_SETTLED',{'order_id':oid,'amount':'608.17'})
    s=wb.snapshot();assert s['portfolio']['equity']==D('1218.17') and s['portfolio']['realized']==D('98.17') and s['portfolio']['redemption_due']==0


def test_partial_reject_refund_and_cancel_request_keeps_reserve(wb):
    deposit(wb)
    oid=order(wb)
    event(wb,'ORDER_CANCEL_REQUESTED',{'order_id':oid})
    assert wb.snapshot()['portfolio']['reserved_cash']==D('1000')
    event(wb,'ORDER_PAID',{'order_id':oid,'amount':'1000'})
    confirm(wb,oid,'399.40','1','400','.60','399.40',False)
    assert wb.snapshot()['portfolio']['pending_buy']==D('600')
    event(wb,'ORDER_REJECTED',{'order_id':oid})
    assert wb.snapshot()['portfolio']['refund_due']==D('600') and wb.snapshot()['portfolio']['available_cash']==D('9000')
    event(wb,'ORDER_REFUNDED',{'order_id':oid,'amount':'600'})
    assert wb.snapshot()['portfolio']['cash']==D('9600')


def test_duplicate_confirmation_external_id_only_once(wb):
    deposit(wb)
    oid=order(wb,paid_immediately=True)
    for _ in range(3):confirm(wb,oid,confirmation_external_id='CHANNEL-001')
    assert len(wb.snapshot()['lots'])==1 and wb.snapshot()['orders'][0]['confirmed_units']==D('798.80')


def test_duplicate_order_retry_and_different_real_orders(wb):
    deposit(wb)
    p={'fund_id':wb.fid,'side':'BUY','requested':'100','occurred_at':AT,'key':'same-order-key','external_order_id':'ONE','paid_immediately':True,'cash_source':'TRACKED'}
    first=wb.order(p);second=wb.order(p)
    assert first['order_id']==second['order_id'] and second['duplicate']
    wb.order(dict(p,key='second-order-key',external_order_id='TWO'))
    assert len(wb.snapshot()['orders'])==2 and wb.snapshot()['portfolio']['cash']==D('9800')


def test_failed_confirmation_rolls_back(wb):
    deposit(wb);oid=order(wb)
    before=wb.snapshot()['revision']
    with pytest.raises(Problem):confirm(wb,oid)
    assert wb.snapshot()['revision']==before and not wb.snapshot()['lots']


def test_negative_cash_and_oversell_roll_back(wb):
    before=wb.snapshot()['revision']
    with pytest.raises(Problem):order(wb)
    assert wb.snapshot()['revision']==before
    opening(wb)
    order(wb,'SELL','800')
    before=wb.snapshot()['revision']
    with pytest.raises(Problem):order(wb,'SELL','300')
    assert wb.snapshot()['revision']==before


def test_concurrent_submissions_cannot_double_spend(wb):
    deposit(wb,'1000')
    def submit(_):
        try:order(wb,requested='800');return True
        except Problem:return False
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(submit,[1,2]))
    assert sum(results)==1 and wb.snapshot()['portfolio']['available_cash']==D('200')


def test_future_and_naive_timestamps_rejected(wb):
    for at in ['2026-10-21T00:00:00Z','2026-10-20T10:00:00']:
        with pytest.raises(Problem):event(wb,'CASH_DEPOSIT',{'amount':'100'},at=at)
    assert wb.snapshot()['revision']==0


def test_history_unknown_not_zero_or_sellable(wb):
    opening(wb,cost=None,acquired=None,available=None)
    s=wb.snapshot()['funds'][0]
    assert s['cost'] is None and s['incomplete'] and s['sellable_units']==0 and s['unrealized'] is None


def test_historical_correction_replays_at_original_position(wb):
    original=deposit(wb,'1000');oid=order(wb,requested='100',paid_immediately=True)
    wb.correct({'event_id':original['id'],'reason':'核对期初现金','key':'correction-key','occurred_at':AT,'expected_revision':wb.snapshot()['revision'],
                'replacement':{'kind':'CASH_DEPOSIT','payload':{'amount':'1200'},'occurred_at':AT}})
    assert wb.snapshot()['portfolio']['cash']==D('1100')
    assert wb.snapshot()['orders'][0]['id']==oid
    assert any(e['reversed'] for e in wb.snapshot()['audit'])


def test_invalid_reversal_rolls_back_both_events(wb):
    original=deposit(wb,'1000');order(wb,requested='100',paid_immediately=True)
    before=wb.snapshot()['revision']
    with pytest.raises(Problem):wb.correct({'event_id':original['id'],'reason':'错误冲正','key':'reverse-only-key','occurred_at':AT,'expected_revision':before})
    assert wb.snapshot()['revision']==before


def test_split_preserves_cost_value_and_pending_reservations(wb):
    opening(wb)
    wb.observe({'fund_id':wb.fid,'value_kind':'OFFICIAL_NAV','value':'1.2','effective_date':'2026-10-19'})
    oid=order(wb,'SELL','100')
    before=wb.snapshot()['portfolio']['equity']
    event(wb,'SPLIT',{'fund_id':wb.fid,'factor':'2'})
    s=wb.snapshot()
    assert s['lots'][0]['units']==D('2000') and s['lots'][0]['reserved']==D('200') and s['lots'][0]['cost']==D('1000')
    assert s['portfolio']['equity']==before and s['orders'][0]['requested']==D('200')


def test_dividend_receivable_paid_and_reinvested_not_external_cash(wb):
    opening(wb)
    d=event(wb,'DIVIDEND_DECLARED',{'fund_id':wb.fid,'amount':'100'})
    event(wb,'DIVIDEND_PAID',{'dividend_id':d['id'],'amount':'40'})
    event(wb,'DIVIDEND_REINVESTED',{'dividend_id':d['id'],'amount':'60','units':'50','nav':'1.2','acquired':'2026-10-20','available_from':'2026-10-21'})
    s=wb.snapshot();assert s['portfolio']['cash']==D('40') and s['portfolio']['dividend_due']==0 and len(s['lots'])==2
    with wb.store.connect() as c:assert wb.state(c)['external_net']==0


def test_mode_isolation_and_migration_repeat(wb):
    path=wb.store.path
    assert Store(path,'real').mode=='real'
    with pytest.raises(Problem):Store(path,'demo')


def test_cannot_record_future_confirmation_or_opening_date(wb):
    deposit(wb);oid=order(wb,paid_immediately=True)
    with pytest.raises(Problem):confirm(wb,oid,confirm_date='2026-10-21')
    with pytest.raises(Problem):opening(wb,acquired='2026-10-21',available='2026-10-22')


def test_settlement_cannot_exceed_confirmed_receivable(wb):
    opening(wb);oid=order(wb,'SELL','100')
    confirm(wb,oid,'100','1','100','0','100')
    before=wb.snapshot()['revision']
    with pytest.raises(Problem):event(wb,'ORDER_SETTLED',{'order_id':oid,'amount':'101'})
    assert wb.snapshot()['revision']==before
