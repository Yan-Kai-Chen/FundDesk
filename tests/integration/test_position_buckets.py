from decimal import Decimal as D
from copy import deepcopy
import random
import pytest
from fastapi.testclient import TestClient

from app.domain.buckets import BucketBook, reference_buckets
from app.domain.finance import Problem, money
from app.main import create_app
from app.persistence.store import Store
from conftest import event, opening, order, confirm, AT
from test_t_attribution import seed, analysis


def book():
    b=BucketBook()
    b.buy('base','100','120','BASE','2026-06-01','2026-06-02')
    b.buy('t','50','40','TACTICAL','2026-10-19','2026-10-20')
    return b


def test_t_overflow_deducts_base_cost_and_attributes_entire_profit_to_t():
    b=book();a=b.reserve('sell','70','TACTICAL','2026-10-20')
    assert [(x['bucket'],x['units']) for x in a]==[('TACTICAL',D('50')),('BASE',D('20'))]
    sale=b.sell('sell','70','69','1',AT);b.release('sell')
    assert sale['cost']==D('64') and sale['pnl']==D('5')
    assert sale['fallback_units']==D('20') and sale['fallback_principal']==D('24')
    s=b.summary('1','2026-10-20')
    assert s['groups']['BASE']['realized']==0 and s['groups']['TACTICAL']['realized']==D('5')
    assert s['groups']['BASE']['cost']==D('96') and s['total_pnl']==D('-11')
    # With no T inventory left, a later T sell consumes base principal and
    # can realize a T loss; its cash proceeds are not all counted as profit.
    b.reserve('second','10','TACTICAL','2026-10-20');second=b.sell('second','10','9.90','0.10',AT)
    assert second['pnl']==D('-2.10') and second['fallback_principal']==D('12')
    assert b.summary('1')['total_pnl']==D('-11.10')


def test_base_sells_do_not_use_t_and_reservations_prevent_reuse():
    b=book()
    with pytest.raises(Problem):b.reserve('too-big','101','BASE','2026-10-20')
    b.reserve('base-sell','90','BASE','2026-10-20')
    with pytest.raises(Problem):b.reserve('overlap','61','TACTICAL','2026-10-20')
    b.release('base-sell')
    assert all(l['reserved']==0 for l in b.lots.values())
    b.reserve('after-release','150','TACTICAL','2026-10-20')
    b.sell('after-release','150','160','0',AT)
    assert b.summary('1')['total_units']==0 and b.realized['TACTICAL']==0


def test_partial_confirm_cancel_releases_only_the_unconfirmed_units():
    b=book();b.reserve('s','70','TACTICAL','2026-10-20')
    first=b.sell('s','30','30','0',AT)
    assert first['pnl']==D('6')
    assert sum(l['reserved'] for l in b.lots.values())==D('40')
    b.release('s')
    assert sum(l['reserved'] for l in b.lots.values())==0
    assert b.summary('1')['groups']['TACTICAL']['units']==D('20')
    assert b.summary('1')['total_pnl']==D('-10')


@pytest.mark.parametrize('seed_',range(5))
def test_money_and_units_conserved_across_many_buys_and_sells(seed_):
    rng=random.Random(seed_);b=BucketBook();paid=received=D('0')
    for i in range(40):
        held=sum((l['units'] for l in b.lots.values()),D('0'))
        if not held or rng.random()<.6:
            q=D(rng.randint(1,60))/100;cost=money(q*D(rng.randint(70,140))/100)
            b.buy(str(i),q,cost,rng.choice(['BASE','TACTICAL']),'2026-09-01','2026-09-02');paid+=cost
        else:
            q=min(held,D(rng.randint(1,50))/100);net=money(q*D(rng.randint(75,140))/100)
            b.reserve(str(i),q,'TACTICAL','2026-10-20');b.sell(str(i),q,net,'0',AT);b.release(str(i));received+=net
        s=b.summary('0.9731','2026-10-20')
        assert s['total_pnl']==s['holding_value']+received-paid
        assert sum(g['units'] for g in s['groups'].values())==s['total_units']
        assert all(l['units']>=l['reserved']>=0 and (l['cost'] is None or l['cost']>=0) for l in b.lots.values())


def test_unknown_cost_stays_unknown_and_missing_nav_empty_positions_are_zero():
    b=BucketBook();assert b.summary()['total_pnl']==0
    b.buy('unknown','10',None,'TACTICAL','2026-01-01','2026-01-02')
    assert b.summary()['groups']['TACTICAL']['value'] is None
    b.reserve('s','10','TACTICAL','2026-10-20');b.sell('s','10','11','0',AT)
    assert b.summary('1')['groups']['TACTICAL']['realized'] is None
    assert b.summary('1')['total_pnl'] is None


def test_formal_channel_fifo_and_economic_t_books_reconcile(wb):
    opening(wb,'100','120',id='physical-base')
    event(wb,'OPENING_LOT',{'fund_id':wb.fid,'lot_id':'physical-t','units':'50','cost':'40',
        'acquired':'2026-10-19','available_from':'2026-10-20','bucket':'TACTICAL'})
    wb.observe({'fund_id':wb.fid,'value_kind':'OFFICIAL_NAV','effective_date':'2026-10-20','value':'1'})
    oid=order(wb,'SELL','70',bucket='TACTICAL')
    pending=wb.snapshot()['funds'][0]['position_buckets']
    assert pending['groups']['TACTICAL']['reserved']==D('50') and pending['groups']['BASE']['reserved']==D('20')
    confirm(wb,oid,'70','1','70','0','70')
    s=wb.snapshot();a=s['funds'][0]['position_buckets']
    assert a['groups']['TACTICAL']['realized']==D('6')
    assert a['sales'][0]['fallback_principal']==D('24') and a['sales'][0]['fee']==0
    assert s['portfolio']['realized']==D('-14')  # actual FIFO old lot
    assert a['total_pnl']==s['portfolio']['pnl']==D('-10')
    revision=s['revision']
    with pytest.raises(Problem):order(wb,'SELL','81',bucket='TACTICAL')
    assert wb.snapshot()['revision']==revision


def test_classification_migrates_accepted_pairs_without_posting_confirmations(wb):
    seed(wb);old=analysis(wb)
    a=wb.preview_bucket_analysis(wb.fid,{'method':'FROM_CURRENT_T'})
    assert a['groups']['TACTICAL']['pnl']==old['t_pnl']==D('10')
    assert a['groups']['BASE']['pnl']==old['residual_pnl']==D('-25')
    p={k:a[k] for k in ['assignments','statement_version','rule_version','source_basis']};p['expected_version']=0
    first=wb.save_bucket_classification(wb.fid,p)
    assert first['_version']==1 and wb.save_bucket_classification(wb.fid,p)['duplicate']
    snapshot=wb.snapshot()
    assert snapshot['revision']==0 and not snapshot['orders']
    assert snapshot['funds'][0]['statement']['bucket_analysis']['classification_saved']
    backup=wb.create_backup()
    p['assignments']=[dict(r,bucket='BASE') for r in p['assignments']];p['expected_version']=1
    wb.save_bucket_classification(wb.fid,p)
    wb.restore(backup['id'],backup['sha256'],True)
    assert wb.snapshot()['funds'][0]['statement']['bucket_analysis']['groups']['TACTICAL']['pnl']==D('10')
    with wb.store.connect() as c:r=wb.rules(c,wb.fid)
    wb.save_rules(wb.fid,dict(r,expected_version=r['_version'],source='revised evidence'))
    assert wb.snapshot()['funds'][0]['statement']['bucket_analysis']['code']=='BUCKET_BASIS_CHANGED'
    assert wb.preview_bucket_analysis(wb.fid,{'method':'FROM_RECENT_T'})['status']=='REFERENCE'


@pytest.mark.parametrize('problem',['missing','duplicate','unknown','invalid','overallocated'])
def test_bad_reference_classification_is_rejected(wb,problem):
    seed(wb);a=wb.preview_bucket_analysis(wb.fid,{'method':'FROM_CURRENT_T'});assignments=deepcopy(a['assignments'])
    if problem=='missing':assignments.pop()
    if problem=='duplicate':assignments.append(assignments[0])
    if problem=='unknown':assignments[0]['row_id']='unknown'
    if problem=='invalid':assignments[0]['bucket']='OTHER'
    if problem=='overallocated':assignments[0].update(bucket='TACTICAL',t_units='99999')
    with pytest.raises(Problem):wb.preview_bucket_analysis(wb.fid,{'assignments':assignments})
    assert wb.snapshot()['revision']==0


def test_new_routes_and_invalid_order_bucket_are_guarded(wb):
    seed(wb);app=create_app(wb.root,testing=True);app.state.workbenches['real'].now=wb.now
    with TestClient(app) as c:
        path=f'/api/v1/funds/{wb.fid}'
        assert c.post(path+'/bucket-analysis',json={}).status_code==401
        csrf=c.get('/api/v1/session').json()['csrf_token']
        assert c.put(path+'/bucket-classification',json={}).status_code==403
        c.headers['x-csrf-token']=csrf
        a=c.post(path+'/bucket-analysis',json={'method':'FROM_CURRENT_T'}).json()
        p={k:a[k] for k in ['assignments','statement_version','rule_version','source_basis']};p['expected_version']=0
        assert c.put(path+'/bucket-classification',json=p).status_code==200
        result=c.post('/api/v1/orders?mode=real',json={'fund_id':wb.fid,'side':'BUY','requested':'1',
            'bucket':'INVALID','key':'invalid-bucket-order','occurred_at':AT})
        assert result.status_code==400 and result.json()['code']=='INVALID_BUCKET'
