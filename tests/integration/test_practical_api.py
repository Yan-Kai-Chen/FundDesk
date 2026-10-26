"""The practical panels share authentication and never silently alter holdings."""
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from conftest import opening


@pytest.fixture
def practical(tmp_path):
    app = create_app(tmp_path, testing=True)
    w = app.state.workbenches['real']
    w.now = lambda: '2026-10-20T05:30:00+00:00'
    w.fid = w.save_fund({'fund_code':'012183','name':'合成实用功能测试','share_class':'C'})['id']
    opening(w, units='100', cost='100')
    w.observe({'fund_id':w.fid,'value_kind':'OFFICIAL_NAV','effective_date':'2026-10-20','value':'1.20'})
    with TestClient(app) as client:
        yield client, w


def authenticate(client):
    client.headers['x-csrf-token'] = client.get('/api/v1/session').json()['csrf_token']


def test_new_routes_require_session_and_writes_require_csrf(practical):
    c, w = practical
    paths = ['/daily-agenda','/reconciliations','/maintenance',f'/funds/{w.fid}/t-review']
    for path in paths:
        assert c.get('/api/v1'+path+'?mode=real').status_code == 401
    writes = [f'/funds/{w.fid}/reconciliation/preview',f'/funds/{w.fid}/reconciliation',f'/funds/{w.fid}/t-review/baseline']
    c.get('/api/v1/session')
    for path in writes:
        assert c.post('/api/v1'+path+'?mode=real',json={}).status_code == 403
    authenticate(c)
    for path in paths:
        assert c.get('/api/v1'+path+'?mode=invalid').status_code == 400


def test_snapshot_agenda_reconciliation_and_review_stay_consistent(practical):
    c, w = practical
    authenticate(c)
    def state():
        return c.get('/api/v1/state?mode=real').json()
    before = state()
    for _ in range(2):
        agenda = c.get('/api/v1/daily-agenda?mode=real').json()
        assert agenda['revision'] == before['revision']
        assert agenda['date'] == before['business_date']
        assert agenda['snapshot_basis'][0]['fund_id'] == w.fid
        assert agenda['snapshot_basis'][0]['units'] == '100'
        assert c.get('/api/v1/maintenance?mode=real').status_code == 200
    preview = c.post(f'/api/v1/funds/{w.fid}/reconciliation/preview?mode=real',json={
        'source':'合成平台截图','snapshot_at':w.now(),'scope':'CURRENT',
        'units_basis':'CONFIRMED_EXCLUDES_PENDING','confirmed_units':'100'}).json()
    assert preview['status'] == 'MATCHED'
    saved = c.post(f'/api/v1/funds/{w.fid}/reconciliation?mode=real',json={
        **preview['input'],'preview_token':preview['preview_token'],'save_snapshot':True})
    assert saved.status_code == 200
    review = c.post(f'/api/v1/funds/{w.fid}/t-review/baseline?mode=real',json={
        'expected_revision':before['revision'],'expected_version':0,
        'policy':'VIRTUAL_RETAINED_PROCEEDS','accept_scenario':True})
    assert review.status_code == 200 and review.json()['scenario']['difference'] == '0.00'
    after = state()
    assert after['revision'] == before['revision'] and after['audit'] == before['audit']
    assert after['funds'][0]['units'] == before['funds'][0]['units']
    assert after['funds'][0]['reconciliation']['status'] == 'MATCHED'
    assert after['funds'][0]['t_review']['scenario']['status'] == 'READY_SCENARIO'
    assert c.get('/api/v1/reconciliations?mode=demo').status_code == 200
    assert c.get(f'/api/v1/funds/{w.fid}/t-review?mode=demo').status_code == 400
