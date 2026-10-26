from fastapi.testclient import TestClient
import pytest
from app.main import create_app


@pytest.fixture
def client(tmp_path):
    app=create_app(tmp_path,testing=True)
    app.state.workbenches['real'].now=lambda:'2026-10-20T05:30:00+00:00'
    with TestClient(app) as client:
        yield client


def session(client):
    value=client.get('/api/v1/session').json()
    client.headers['x-csrf-token']=value['csrf_token']


def test_api_t_intensity_presets_version_and_mode_isolation(client):
    session(client)
    fid = 'demo-equity'
    initial = client.get('/api/v1/state?mode=demo').json()
    f = next(f for f in initial['funds'] if f['id'] == fid)
    assert [p['level'] for p in f['daily_t']['presets']['options']] == ['OBSERVE', 'CAUTIOUS', 'BALANCED', 'ACTIVE']
    path = f'/api/v1/funds/{fid}/t-profile?mode=demo'
    saved = client.put(path, json={'level': 'ACTIVE', 'expected_version': 0})
    assert saved.status_code == 200 and saved.json()['_version'] == 1
    assert client.put(path, json={'level': 'CAUTIOUS', 'expected_version': 0}).status_code == 400
    assert client.put(path, json={'level': 'ACTIVE', 'expected_version': 1, 'trigger': '0.001'}).status_code == 400
    assert client.put(path.replace('mode=demo', 'mode=real'), json={'level': 'ACTIVE', 'expected_version': 0}).status_code == 400
    plan = client.post('/api/v1/recommendations/evaluate?mode=demo').json()
    assert next(i for i in plan['daily_t_items'] if i['fund_id'] == fid)['profile_version'] == 1
    assert client.get('/api/v1/state?mode=demo').json()['revision'] == initial['revision']


def test_api_automatic_collection_and_manual_refresh_keep_estimates_separate(client, monkeypatch):
    from decimal import Decimal
    from app.providers.public_market import parse_ths_estimate
    session(client)
    f = client.post('/api/v1/funds?mode=real', json={'fund_code':'012183','name':'合成行情 API 测试','share_class':'C'}).json()
    monkeypatch.setattr('app.services.market_data.fetch_official_nav', lambda *a, **k: {
        'source':'synthetic NAV','source_url':'https://example.invalid','raw_sha256':'a'*64,
        'records':[{'effective_date':'2026-10-19','value':Decimal('1.2'),'corporate_action':''}]})
    monkeypatch.setattr('app.services.market_data.fetch_estimate', lambda code, clock, **k: dict(parse_ths_estimate(
        b"vm_fd_012183='2026-10-19;0930-1130,1300-1500|2026-10-20~1.2~1329,1.23,1.2,0'", code, clock()), fallback_checked=False))
    result = client.app.state.collect_market()
    assert result[0]['intraday']['status']=='READY'
    s = client.get('/api/v1/state?mode=real').json()
    fund = s['funds'][0]
    assert fund['nav']['value_kind']=='OFFICIAL_NAV' and fund['estimate']['value_kind']=='THIRD_PARTY_ESTIMATE'
    assert fund['daily_t']['today']['change']=='0.025' and s['revision']==0
    r = client.post('/api/v1/funds/'+f['id']+'/market/refresh?mode=real', json={})
    assert r.status_code==200 and r.json()['intraday']['status']=='READY'
    for _ in range(2):
        again=client.post('/api/v1/funds/'+f['id']+'/market/refresh?mode=real',json={})
        assert again.status_code==200 and again.json()['nav']['status']==again.json()['intraday']['status']=='READY'
    assert client.post('/api/v1/funds/demo-equity/market/refresh?mode=demo',json={}).status_code==400


def test_market_network_does_not_hold_the_ui_request_lock(client, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from app.providers.public_market import parse_ths_estimate
    from app.domain.finance import Problem
    session(client)
    f=client.post('/api/v1/funds?mode=real',json={'fund_code':'012183','name':'合成并发测试','share_class':'C'}).json()
    client.post('/api/v1/observations?mode=real',json={'fund_id':f['id'],'value_kind':'OFFICIAL_NAV','effective_date':'2026-10-19','value':'1.2'})
    def unavailable(*a, **k):
        raise Problem('SOURCE_UNAVAILABLE', 'synthetic')
    monkeypatch.setattr('app.services.market_data.fetch_official_nav', unavailable)
    entered, release = Event(), Event()
    def slow(code, clock, **k):
        entered.set()
        assert release.wait(3)
        return dict(parse_ths_estimate(b"vm_fd_012183='2026-10-19;0930-1130,1300-1500|2026-10-20~1.2~1329,1.23,1.2,0'",code,clock()),fallback_checked=False)
    monkeypatch.setattr('app.services.market_data.fetch_estimate',slow)
    with ThreadPoolExecutor(max_workers=2) as pool:
        request=pool.submit(client.post,'/api/v1/funds/'+f['id']+'/market/refresh?mode=real',json={})
        try:
            assert entered.wait(2)
            state=pool.submit(client.get,'/api/v1/state?mode=real').result(timeout=2)
            assert state.status_code==200 and not release.is_set()
        finally:
            release.set()
        assert request.result(timeout=2).status_code==200


def test_health_session_and_csrf(client):
    providers=client.get('/api/v1/health').json()['providers']
    assert providers['real_network']=='AUTO_VERIFIED_FUNDS'
    assert providers['verified_codes']==['006810','008987','012183','012738','017954','019633','021528']
    assert client.get('/api/v1/state').status_code==401
    assert client.post('/api/v1/events',json={}).status_code==401
    client.get('/api/v1/session')
    assert client.post('/api/v1/events',json={}).status_code==403
    session(client)
    assert client.get('/api/v1/state').status_code==200


def test_storage_status_and_cleanup_keep_ledger_unchanged(client):
    session(client)
    before=client.get('/api/v1/state?mode=demo').json()
    status=client.get('/api/v1/storage?mode=demo')
    assert status.status_code==200 and status.json()['policy']['intraday_days']==7
    result=client.post('/api/v1/storage/cleanup?mode=demo',json={})
    assert result.status_code==200 and result.json()['storage']['provider_cache_bytes']==0
    after=client.get('/api/v1/state?mode=demo').json()
    assert after['revision']==before['revision'] and after['portfolio']==before['portfolio']


def test_host_origin_and_cross_site_guards(client):
    assert client.get('/api/v1/health',headers={'host':'evil.example'}).status_code==400
    assert client.get('/api/v1/session',headers={'origin':'https://evil.example'}).status_code==403
    assert client.get('/api/v1/session',headers={'sec-fetch-site':'cross-site'}).status_code==403


def test_modes_isolated_empty_real_and_demo_seeded(client):
    session(client)
    assert len(client.get('/api/v1/state?mode=demo').json()['funds'])==3
    assert client.get('/api/v1/state?mode=real').json()['funds']==[]
    assert client.get('/api/v1/state?mode=invalid').status_code==400


def test_api_fund_cash_order_flow_and_rejection(client):
    session(client)
    r=client.post('/api/v1/funds?mode=real',json={'fund_code':'000001','name':'合成 API 测试','share_class':'A'})
    assert r.status_code==200 and r.json()['fund_code']=='000001'
    fid=r.json()['id']
    payload={'kind':'CASH_DEPOSIT','payload':{'amount':'1000'},'key':'api-cash-initial','occurred_at':'2026-10-20T12:00:00+08:00','expected_revision':0}
    assert client.post('/api/v1/events?mode=real',json=payload).status_code==200
    assert client.post('/api/v1/events?mode=real',json=payload).json()['duplicate']
    o={'fund_id':fid,'side':'BUY','requested':'300','key':'api-order-request','occurred_at':'2026-10-20T12:01:00+08:00','paid_immediately':True,'cash_source':'TRACKED'}
    assert client.post('/api/v1/orders?mode=real',json=o).status_code==200
    p=client.get('/api/v1/state?mode=real').json()['portfolio']
    assert p['cash']=='700' and p['pending_buy']=='300' and p['equity']=='1000'
    assert client.post('/api/v1/events?mode=real',json=dict(payload,key='bad-float-amount',payload={'amount':10.2},expected_revision=None)).status_code==400


def test_security_headers_and_export(client):
    session(client)
    r=client.get('/api/v1/export?mode=demo')
    assert r.status_code==200 and 'attachment' in r.headers['content-disposition']
    assert r.headers['x-content-type-options']=='nosniff' and r.headers['cache-control']=='no-store'
    assert "frame-ancestors 'none'" in r.headers['content-security-policy']


def test_api_prediction_research_and_backup_restore(client):
    session(client)
    evaluate=client.get('/api/v1/predictions/evaluate/demo-equity').json()
    assert evaluate['fold_count']==70 and evaluate['status']=='DEMO_EVALUATION'
    p=client.post('/api/v1/predictions',json={'fund_id':'demo-equity','target_date':'2026-10-21','key':'api-predict-test'}).json()
    assert p['sample_count']==90
    backup=client.post('/api/v1/backups').json()
    r=client.post('/api/v1/restore/preview',json={'id':backup['id']})
    assert r.status_code==200 and r.json()['counts']['predictions']==1
    assert client.post('/api/v1/restore/commit',json={'id':backup['id'],'sha256':backup['sha256'],'confirm':True}).status_code==200


def test_api_unknown_fields_and_template_path(client):
    session(client)
    assert client.post('/api/v1/funds?mode=real',json={}).status_code==400
    assert client.get('/api/v1/templates/not-a-template').status_code==400
