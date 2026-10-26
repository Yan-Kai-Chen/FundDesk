from copy import deepcopy
from datetime import datetime
from decimal import Decimal as D
import json
import pytest
from pydantic import ValidationError

from app.domain.finance import Problem, SHANGHAI
from app.providers.gold_reference import build_reference
from app.persistence.store import Store
from app.schemas.asset_import import AssetDataBatch
from test_asset_imports import manifest, commit


def quotation(now, fixed=False):
    dates=[('2026-10-20',100)]+([('2026-10-21',102)] if fixed else [])
    fix=json.dumps({'wp':[[int(datetime.fromisoformat(d).replace(tzinfo=SHANGHAI).timestamp()*1000),v] for d,v in dates]}).encode()
    live=json.dumps({'heyue':'Au99.99','delaystr':'2026年10月21日 13:29:55',
                     'times':['13:29','15:30'],'data':['101','999']}).encode()
    return build_reference('008987',[{'effective_date':'2026-10-20','value':'2'}],fix,live,now)


def setup(w):
    w.now=lambda:'2026-10-21T13:30:00+08:00'
    f=w.save_fund({'fund_code':'008987','share_class':'C','name':'Synthetic gold linked C','instrument_type':'GOLD_LINKED'})
    w.observe({'fund_id':f['id'],'value_kind':'OFFICIAL_NAV','effective_date':'2026-10-20','value':'2','source':'synthetic'})
    return f['id']


def test_gold_routes_separately_preserves_financial_state_and_interval(wb,monkeypatch):
    fid=setup(wb);before=wb.snapshot()
    monkeypatch.setattr('app.services.market_data.fetch_estimate',lambda *a,**k:pytest.fail('equity source used for gold'))
    monkeypatch.setattr('app.services.market_data.fetch_gold_reference',lambda *a,**k:quotation(wb.now()))
    assert wb.refresh_intraday(fid)['inserted']==1
    after=wb.snapshot();f=next(f for f in after['funds'] if f['id']==fid)
    assert after['portfolio']==before['portfolio'] and after['audit']==before['audit'] and after['revision']==0
    assert f['automatic_market_supported'] and f['daily_t']['market']['scope']=='GOLD_CN'
    assert f['daily_t']['today']['change']==D('.0095') and f['daily_t']['profile']['level']=='OBSERVE'
    assert f['estimate']['quality']=='LOCAL_GOLD_REFERENCE' and f['estimate']['unit']=='CNY_PER_FUND_UNIT'
    with pytest.raises(Problem) as exc:wb.refresh_intraday(fid)
    assert exc.value.code=='RATE_LIMITED'
    assert wb.refresh_intraday(fid,manual=True)['inserted']==0
    with wb.store.connect() as c: assert Store.doc(c,'intraday_sync',fid)['automatic_interval_seconds']==600
    other=wb.save_fund({'fund_code':'008987','share_class':'A','name':'Synthetic unverified A'})
    with pytest.raises(Problem):wb.refresh_intraday(other['id'],manual=True)


def test_fixed_gold_keeps_first_receipt_does_not_expire_or_regress(wb,monkeypatch):
    fid=setup(wb);wb.now=lambda:'2026-10-21T14:30:00+08:00'
    fixed=quotation(wb.now(),True)
    monkeypatch.setattr('app.services.market_data.fetch_gold_reference',lambda *a,**k:fixed)
    wb.refresh_intraday(fid);q=next(f for f in wb.snapshot()['funds'] if f['id']==fid)['estimate']
    wb.now=lambda:'2026-10-21T16:30:00+08:00'
    assert wb.refresh_intraday(fid)['inserted']==0
    assert wb.refresh_intraday(fid)['status']=='CLOSE_CACHED'
    assert wb.refresh_intraday(fid,manual=True)['inserted']==0
    current=next(f for f in wb.snapshot()['funds'] if f['id']==fid)
    assert current['estimate']['id']==q['id'] and current['estimate']['observed_at']==q['observed_at']
    assert current['estimate']['fetched_at']!=q['fetched_at'] and current['estimate']['as_of'] is None
    assert current['daily_t']['today']['status']=='READY'
    monkeypatch.setattr('app.services.market_data.fetch_gold_reference',lambda *a,**k:quotation('2026-10-21T13:30:00+08:00'))
    with pytest.raises(Problem) as exc:wb.refresh_intraday(fid,manual=True)
    assert exc.value.code=='GOLD_FIXING_REGRESSION'
    assert next(f for f in wb.snapshot()['funds'] if f['id']==fid)['estimate']['id']==q['id']


def test_activities_keep_minute_precision_and_explicit_reference_assumption(wb):
    m=manifest(wb,'GOLD_FUND');m['position']=None;m['official_navs']=[]
    m['history_complete']=True;m['history_completeness_basis']='synthetic complete history'
    template=m['transactions'][0]
    first=dict(template,displayed_at='2026-10-16 14:59:00',value='100',time_precision='MINUTE',original_category='BUY')
    activity=dict(template,displayed_at='2026-10-18 08:12:00',value='0.20',time_precision='MINUTE',original_category='ACTIVITY',
                  reference_nav_date='2026-10-16',reference_nav_date_basis='Synthetic credit on Sunday; prior available NAV, not a channel confirmation')
    late=dict(activity,displayed_at='2026-10-19 16:53:00',reference_nav_date='2026-10-19',value='0.30')
    m['transactions']=[first,activity,late]
    p=wb.preview_asset_batch(m);assert not p['errors'];commit(wb,p);fid=p['asset_id']
    wb.save_rules(fid,{'verified':True,'source':'synthetic','effective_from':'2020-01-01','lot_method':'FIFO','unit_step':'0.01',
                      'purchase_rate':'0','redemption_tiers':[{'min_days':0,'max_days':None,'rate':'0'}]})
    for d in ['2026-10-16','2026-10-19']:wb.observe({'fund_id':fid,'value_kind':'OFFICIAL_NAV','effective_date':d,'value':'1','source':'synthetic'})
    a=wb.preview_t_analysis(fid,{'method':'NONE'})
    assert a['paid']==D('100.50') and a['units']==D('100.50')
    assert a['buys'][1]['original_category']=='ACTIVITY' and a['buys'][1]['reference_nav_date']=='2026-10-16'
    assert any('分钟' in x for x in a['assumptions']) and any('活动' in x for x in a['assumptions'])
    f=next(f for f in wb.snapshot()['funds'] if f['id']==fid)
    assert f['statement']['rows'][1]['time_precision']=='MINUTE'
    bad=deepcopy(m);bad['transactions'][1].pop('reference_nav_date');bad['transactions'][1].pop('reference_nav_date_basis')
    wb.save_statement({'fund_id':fid,'channel':'支付宝','sources':m['sources'],'rows':bad['transactions'],'history_complete':True,'expected_version':1})
    with pytest.raises(Problem) as exc:wb.preview_t_analysis(fid,{'method':'NONE'})
    assert exc.value.code=='NAV_MISSING'  # weekend activity has no implicit shift


@pytest.mark.parametrize('patch',[
    {'time_precision':'MINUTE','displayed_at':'2026-09-01 14:00:01'},
    {'original_category':'SELL'}, {'original_category':'ACTIVITY','side':'SELL'},
    {'reference_nav_date':'2026-09-01','reference_nav_date_basis':'invented'},
    {'original_category':'ACTIVITY','reference_nav_date':'2026-09-02','reference_nav_date_basis':'future'},
    {'original_category':'ACTIVITY','reference_nav_date':'2026-09-01','reference_nav_date_basis':''},
])
def test_metadata_cannot_silently_override_ordinary_cutoff_or_invent_precision(wb,patch):
    m=manifest(wb);m['transactions'][0].update(patch)
    with pytest.raises(ValidationError):AssetDataBatch.model_validate(m)


def test_gold_automatic_cache_rotates_but_manual_and_latest_are_kept(wb):
    fid=setup(wb);wb.now=lambda:'2026-10-21T14:30:00+08:00';d=quotation(wb.now(),True)
    evidence=dict(d['evidence'],provider_id=d['provider_id'],reference_date=d['reference_date'],reference_nav=d['reference_nav'],raw_response_hash='a'*64)
    for on in ['2026-09-01','2026-10-21']:
        wb.observe({'fund_id':fid,'value_kind':'LOCAL_ESTIMATE','effective_date':on,'value':'2.01','source':'synthetic',
                    'quality':'LOCAL_GOLD_REFERENCE'},provider_evidence=evidence)
    wb.observe({'fund_id':fid,'value_kind':'LOCAL_ESTIMATE','effective_date':'2026-09-02','value':'2','source':'manual'})
    result=wb.cleanup_storage();assert result['cleanup']['removed']['intraday_observations']==1
    with wb.store.connect() as c:
        rows=wb.observations(c,fid,'LOCAL_ESTIMATE',False)
    assert {r['effective_date'] for r in rows}=={'2026-09-02','2026-10-21'}
