"""Accepted reconstruction starts an operational book without invented confirmations."""
from decimal import Decimal as D
import pytest
from fastapi.testclient import TestClient

from app.domain.finance import Problem, money
from app.main import create_app
from app.services.initialization import split_opening
from conftest import order, confirm, event, opening, AT
from test_t_attribution import seed


def setup(w):
    seed(w)
    a=w.preview_bucket_analysis(w.fid,{'method':'FROM_CURRENT_T'})
    w.save_bucket_classification(w.fid,dict(a,expected_version=0))
    return w.preview_tracking(w.fid,{'base_value':'70.00'})


def start(w,p):
    return w.initialize_tracking(w.fid,dict(p,accept_reconstruction=True))


def test_preview_and_explicit_acceptance(wb):
    p=setup(wb)
    assert wb.snapshot()['revision']==0 and not wb.snapshot()['lots']
    with pytest.raises(Problem):wb.initialize_tracking(wb.fid,p)
    start(wb,p);s=wb.snapshot();f=s['funds'][0]
    assert s['revision']==1 and not s['orders']
    assert f['tracking']['basis']=='USER_ACCEPTED_RECONSTRUCTION' and not f['initialization_pending']
    assert s['portfolio']['equity']==D('95') and s['portfolio']['pnl']==D('-15')
    assert s['portfolio']['cash']==0  # historical proceeds were not invented as available cash
    assert f['position_buckets']['total_pnl']==D('-15')
    assert f['position_buckets']['groups']['BASE']['units']==D('73.68')
    assert f['position_buckets']['groups']['BASE']['value']==D('70')
    assert f['position_buckets']['groups']['TACTICAL']['value']==D('25')
    assert f['position_buckets']['groups']['TACTICAL']['realized']==D('10')
    assert f['incomplete'] and all(l['basis']=='USER_ACCEPTED_RECONSTRUCTION' for l in f['lots'])
    assert s['audit'][0]['kind']=='TRACKING_OPENING'
    assert f['statement']['rows'][0]['ledger_posted'] is False


@pytest.mark.parametrize('target',['0','95','70'])
def test_split_conserves_units_cost_and_value(target):
    rows=split_opening('100','120','0.95',target,AT)
    assert sum(r['units'] for r in rows)==100 and sum(r['cost'] for r in rows)==120
    assert money(rows[0]['units']*D('.95'))==D(target)


def test_overlarge_or_bad_base_amount_rejected(wb):
    setup(wb)
    for value in ['95.01','-1','2.001',0.5]:
        with pytest.raises(Problem):wb.preview_tracking(wb.fid,{'base_value':value})
    assert wb.snapshot()['revision']==0


def test_stale_or_tampered_proposal_rolls_back(wb):
    p=setup(wb);bad=dict(p,base_value='20')
    with pytest.raises(Problem):start(wb,bad)
    assert wb.snapshot()['revision']==0
    wb.observe({'fund_id':wb.fid,'value_kind':'OFFICIAL_NAV','effective_date':'2026-09-11','value':'0.96'})
    with pytest.raises(Problem):start(wb,p)
    assert wb.snapshot()['revision']==0


def test_repeat_commit_idempotent_and_historical_editor_locked(wb):
    p=setup(wb);start(wb,p)
    assert start(wb,p)['duplicate'] and wb.snapshot()['revision']==1
    with pytest.raises(Problem):start(wb,dict(p,proposal_token='x'))
    with pytest.raises(Problem):wb.save_bucket_classification(wb.fid,{'assignments':[]})
    with pytest.raises(Problem):wb.save_t_pairings(wb.fid,{'method':'NONE'})


def test_existing_book_cannot_be_overlapped(wb):
    setup(wb);opening(wb,'5','5')
    with pytest.raises(Problem):wb.preview_tracking(wb.fid,{'base_value':'1'})


def test_follow_on_t_sell_consumes_new_split_and_reconciles(wb):
    p=setup(wb);start(wb,p)
    oid=order(wb,'SELL','30',bucket='TACTICAL')
    f=wb.snapshot()['funds'][0]
    assert f['position_buckets']['groups']['TACTICAL']['reserved']==D('26.32')
    assert f['position_buckets']['groups']['BASE']['reserved']==D('3.68')
    confirm(wb,oid,'30','1','30','0','30')
    s=wb.snapshot();f=s['funds'][0]
    assert f['units']==70 and f['position_buckets']['total_units']==70
    assert f['position_buckets']['sales'][-1]['fallback_units']==D('3.68')
    assert f['position_buckets']['total_pnl']==s['portfolio']['pnl']==D('-13.50')
    assert f['position_buckets']['groups']['BASE']['realized']==0
    assert s['portfolio']['cash']==0 and s['portfolio']['redemption_due']==30


def test_manual_tracking_plan_does_not_invent_triggers(wb):
    start(wb,setup(wb));p=wb.recommendations()['items'][0]
    assert p['action']=='HOLD' and p['tracking_mode'] and p['amount']==0 and not p['blocked_by']
    assert p['base_value']==70 and p['t_value']==25


def test_new_nav_moves_values_without_repeating_initial_split(wb):
    start(wb,setup(wb))
    wb.observe({'fund_id':wb.fid,'value_kind':'OFFICIAL_NAV','effective_date':'2026-09-11','value':'1.05'})
    f=wb.snapshot()['funds'][0];a=f['position_buckets']
    assert a['groups']['BASE']['units']==D('73.68') and a['groups']['BASE']['value']==D('77.36')
    assert a['groups']['TACTICAL']['value']==D('27.64')
    assert a['total_pnl']==wb.snapshot()['portfolio']['pnl']==D('-5')
    assert f['statement']['tracking_started']


def test_tracking_backup_restore_keeps_basis_and_split(wb):
    start(wb,setup(wb));b=wb.create_backup();before=wb.snapshot()
    event(wb,'CASH_DEPOSIT',{'amount':'10'})
    wb.restore(b['id'],b['sha256'],True)
    after=wb.snapshot()
    assert after['revision']==before['revision']==1
    assert after['funds'][0]['tracking']==before['funds'][0]['tracking']
    assert after['portfolio']['equity']==95 and after['portfolio']['cash']==0


def test_tracking_endpoint_requires_local_session(tmp_path):
    app=create_app(tmp_path,testing=True);w=app.state.workbenches['real']
    w.now=lambda:'2026-10-20T05:30:00+00:00'
    w.fid=w.save_fund({'fund_code':'000002','name':'Synthetic','share_class':'C'})['id']
    w.save_rules(w.fid,{'verified':True,'source':'synthetic','effective_from':'2020-01-01','lot_method':'FIFO',
        'purchase_rate':'0','unit_step':'0.01','redemption_tiers':[{'min_days':0,'max_days':None,'rate':'0'}]})
    p=setup(w)
    with TestClient(app) as client:
        path=f'/api/v1/funds/{w.fid}/tracking/start?mode=real'
        assert client.post(path,json={'base_value':'70'}).status_code==401
        session=client.get('/api/v1/session').json()
        preview=client.post(f'/api/v1/funds/{w.fid}/tracking/preview?mode=real',json={'base_value':'70'},headers={'x-csrf-token':session['csrf_token']}).json()
        result=client.post(path,json=dict(preview,accept_reconstruction=True),headers={'x-csrf-token':session['csrf_token']})
        assert result.status_code==200,result.text
        assert client.get('/api/v1/state?mode=real').json()['revision']==1
