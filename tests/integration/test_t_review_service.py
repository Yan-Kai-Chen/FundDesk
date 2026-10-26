from decimal import Decimal as D
import pytest

from app.domain.finance import Problem
from app.persistence.store import Store
from app.services.t_review import TReviewMixin
from conftest import opening


class ReviewWorkbench(TReviewMixin):
    pass


def enable(wb):
    # Isolate integration until the application composes the new mixin.
    for name in ('t_review_context','t_review','start_t_review','t_review_maintenance'):
        setattr(wb,name,getattr(TReviewMixin,name).__get__(wb))
    return wb


def start(wb,version=0):
    return wb.start_t_review(wb.fid,{'expected_revision':wb.snapshot()['revision'],'expected_version':version,
        'policy':'VIRTUAL_RETAINED_PROCEEDS','accept_scenario':True})


def test_start_writes_only_bounded_baseline_document_and_get_is_read_only(wb):
    enable(wb);opening(wb)
    wb.observe({'fund_id':wb.fid,'value_kind':'OFFICIAL_NAV','effective_date':'2026-10-20','value':'1'})
    def rows():
        with wb.store.connect() as c:
            return [tuple(r) for r in c.execute('SELECT * FROM events')]
    before=rows();result=start(wb)
    assert result['scenario']['difference']==0 and rows()==before
    with wb.store.connect() as c:
        before_docs=[tuple(r) for r in c.execute('SELECT * FROM documents')]
    assert wb.t_review(wb.fid)==result
    with wb.store.connect() as c:
        assert [tuple(r) for r in c.execute('SELECT * FROM documents')]==before_docs
    for version in range(1,8):
        start(wb,version)
    with wb.store.connect() as c:
        assert len(Store.doc(c,'t_review_baseline',wb.fid)['previous'])==5
    assert rows()==before


def test_precommitted_buy_confirmation_does_not_become_free_forward_alpha(wb):
    enable(wb);opening(wb)
    wb.observe({'fund_id':wb.fid,'value_kind':'OFFICIAL_NAV','effective_date':'2026-10-20','value':'1'})
    oid=wb.order({'fund_id':wb.fid,'side':'BUY','bucket':'TACTICAL','requested':'10','paid_immediately':True,
                  'occurred_at':wb.now(),'key':'precommitted-buy-one'})['order_id']
    waiting=start(wb)
    assert waiting['baseline']['start_pending'] is True
    assert waiting['scenario']['status']=='WAIT_BASELINE' and waiting['scenario']['difference'] is None
    wb.event('ORDER_CONFIRMED',{'order_id':oid,'units':'10','gross':'10','fee':'0','net':'10','nav':'1',
        'nav_date':'2026-10-20','confirm_date':'2026-10-20','available_from':'2026-10-21'},'precommit-confirm-one',wb.now())
    r=wb.t_review(wb.fid)
    assert r['scenario']['difference'] is None
    assert r['scenario']['status']=='WAIT_BASELINE'
    old_events=r['baseline']['requested_revision']
    done=wb.t_review_maintenance()
    assert len(done['started'])==1
    r=wb.t_review(wb.fid)
    assert r['baseline']['start_pending'] is False and D(r['baseline']['units'])==D('1010')
    assert r['baseline']['revision']>old_events and r['scenario']['difference']==0
    with wb.store.connect() as c:
        before=[tuple(row) for row in c.execute('SELECT * FROM documents')]
    assert wb.t_review_maintenance()=={'started':[],'waiting':[]}
    with wb.store.connect() as c:
        assert [tuple(row) for row in c.execute('SELECT * FROM documents')]==before


def test_stale_revision_and_unaccepted_scope_rejected(wb):
    enable(wb);opening(wb)
    wb.observe({'fund_id':wb.fid,'value_kind':'OFFICIAL_NAV','effective_date':'2026-10-20','value':'1'})
    with pytest.raises(Problem,match='明确接受'):
        wb.start_t_review(wb.fid,{'policy':'VIRTUAL_RETAINED_PROCEEDS','accept_scenario':False})
    with pytest.raises(Problem) as exc:
        wb.start_t_review(wb.fid,{'expected_revision':0,'expected_version':0,'policy':'VIRTUAL_RETAINED_PROCEEDS','accept_scenario':True})
    assert exc.value.code=='VERSION_CONFLICT'


def test_waiting_request_waits_for_new_orders_too_without_repeated_writes(wb):
    enable(wb);opening(wb)
    wb.observe({'fund_id':wb.fid,'value_kind':'OFFICIAL_NAV','effective_date':'2026-10-20','value':'1'})
    def submit(key):
        return wb.order({'fund_id':wb.fid,'side':'BUY','bucket':'TACTICAL','requested':'10','paid_immediately':True,
            'occurred_at':wb.now(),'key':key})['order_id']
    def finish(oid,key):
        wb.event('ORDER_CONFIRMED',{'order_id':oid,'units':'10','gross':'10','fee':'0','net':'10','nav':'1',
            'nav_date':'2026-10-20','confirm_date':'2026-10-20','available_from':'2026-10-21'},key,wb.now())
    first=submit('waiting-first-order')
    start(wb)
    second=submit('waiting-second-order')
    finish(first,'waiting-confirm-first')
    with wb.store.connect() as c:
        original=[tuple(r) for r in c.execute('SELECT * FROM documents')]
    for _ in range(3):
        result=wb.t_review_maintenance()
        assert not result['started'] and result['waiting'][0]['order_count']==1
    with wb.store.connect() as c:
        assert [tuple(r) for r in c.execute('SELECT * FROM documents')]==original
    finish(second,'waiting-confirm-second')
    result=wb.t_review_maintenance()
    assert len(result['started'])==1
    review=wb.t_review(wb.fid)
    assert D(review['baseline']['units'])==D('1020') and review['scenario']['difference']==0
    assert review['scenario']['external_contributions']==0 and review['scenario']['trade_count']==0


def test_waiting_request_waits_for_sale_settlement(wb):
    enable(wb);opening(wb)
    wb.observe({'fund_id':wb.fid,'value_kind':'OFFICIAL_NAV','effective_date':'2026-10-20','value':'1'})
    oid=wb.order({'fund_id':wb.fid,'side':'SELL','bucket':'TACTICAL','requested':'10',
        'occurred_at':wb.now(),'key':'waiting-sell-order'})['order_id']
    start(wb)
    wb.event('ORDER_CONFIRMED',{'order_id':oid,'units':'10','gross':'10','fee':'0','net':'10','nav':'1',
        'nav_date':'2026-10-20','confirm_date':'2026-10-20'},'waiting-sell-confirm',wb.now())
    assert wb.t_review_maintenance()['waiting'][0]['reason']=='WAIT_ORDERS'
    wb.event('ORDER_SETTLED',{'order_id':oid,'amount':'10'},'waiting-sell-settled',wb.now())
    assert len(wb.t_review_maintenance()['started'])==1
    r=wb.t_review(wb.fid)
    assert D(r['baseline']['units'])==D('990') and r['scenario']['difference']==0
    assert r['scenario']['virtual_cash']==0 and r['scenario']['receivable']==0
