from datetime import timedelta
import hashlib
import json
import os

from app.domain.finance import stamp
from app.persistence.store import Store, pack
from app.services.storage import POLICY
from app.services.workbench import Workbench
from conftest import deposit, opening


def quote(wb, date, *, automatic=True, value='1.1', fid=None):
    receipt = hashlib.sha256((date + value).encode()).hexdigest()
    evidence = {'provider_id':'THS_PUBLIC_ESTIMATE','reference_date':date,'reference_nav':'1',
                'source_url':'https://example.invalid', 'raw_response_hash':receipt} if automatic else None
    result = wb.observe({'fund_id':fid or wb.fid,'value_kind':'THIRD_PARTY_ESTIMATE','effective_date':date,
                        'as_of':date+'T01:30:00Z','value':value,'source':'synthetic',
                        'quality':'PUBLIC_THIRD_PARTY_ESTIMATE'}, provider_evidence=evidence)
    return result['id'], receipt


def plan(c, id, created, expires, **data):
    value = dict(id=id, expires_at=expires, **data)
    c.execute('INSERT INTO plans VALUES(?,?,?)', (id, pack(value), created))


def raw(wb, sha, age, body=b'synthetic raw'):
    path = wb.root/'data/cache/providers/intraday/012183'/(sha+'.txt')
    path.parent.mkdir(parents=True, exist_ok=True);path.write_bytes(body)
    at = (stamp(wb.now()) - timedelta(days=age)).timestamp()
    os.utime(path,(at,at))
    return path


def test_cleanup_preserves_financial_facts_official_manual_and_referenced_old_estimates(wb):
    deposit(wb);opening(wb)
    official = wb.observe({'fund_id':wb.fid,'value_kind':'OFFICIAL_NAV','effective_date':'2026-10-19','value':'1.2'})['id']
    expired, _ = quote(wb,'2026-10-01')
    protected, receipt = quote(wb,'2026-10-02')
    manual, _ = quote(wb,'2026-10-03',automatic=False)
    recent, _ = quote(wb,'2026-10-14')  # seven calendar days including today
    latest, _ = quote(wb,'2026-10-20')
    with wb.store.transaction() as c:
        c.execute('INSERT INTO predictions VALUES(?,?,?)', ('prediction-keep',pack({'input_ids':[protected]}),wb.now()))
        before_events = [tuple(r) for r in c.execute('SELECT * FROM events')]
    file = raw(wb,receipt,10)
    before = wb.snapshot()
    result = wb.cleanup_storage()
    assert result['cleanup']['removed']['intraday_observations'] == 1
    with wb.store.connect() as c:
        ids = {r[0] for r in c.execute('SELECT id FROM observations')}
        assert [tuple(r) for r in c.execute('SELECT * FROM events')] == before_events
        assert c.execute('SELECT count(*) FROM predictions').fetchone()[0] == 1
    assert expired not in ids and {protected,manual,recent,latest,official} <= ids and file.exists()
    after = wb.snapshot()
    assert after['revision'] == before['revision'] and after['portfolio'] == before['portfolio']


def test_last_known_automatic_quote_survives_long_offline_period(wb):
    old, _ = quote(wb,'2026-09-01')
    last, receipt = quote(wb,'2026-09-02')
    quote(wb,'2026-09-03',automatic=False)
    file = raw(wb,receipt,30)
    wb.cleanup_storage()
    with wb.store.connect() as c:
        ids = {r[0] for r in c.execute('SELECT id FROM observations')}
    assert old not in ids and last in ids and file.exists()


def test_plan_retention_keeps_linked_chain_and_inputs_and_bounds_unlinked_expired(wb):
    id, receipt = quote(wb,'2026-09-01')
    quote(wb,'2026-10-20')
    with wb.store.transaction() as c:
        Store.append(c,'CASH_DEPOSIT',{'amount':'1','source_recommendation':'plan_linked'},'protected-event-key',wb.now())
        plan(c,'plan_linked','2026-08-01T00:00:00Z','2026-08-01T00:01:00Z',input_ids=[id],parent='plan_parent')
        plan(c,'plan_parent','2026-07-01T00:00:00Z','2026-07-01T00:01:00Z',parent='plan_linked')
        plan(c,'plan_old','2026-09-01T00:00:00Z','2026-09-01T00:01:00Z')
        plan(c,'plan_live',wb.now(),'2026-10-20T05:31:00Z')
        for i in range(205):
            created=(stamp(wb.now())-timedelta(days=1)+timedelta(seconds=i)).isoformat()
            plan(c,'plan_recent_'+str(i),created,'2026-10-20T04:00:00Z')
    file=raw(wb,receipt,30)
    result=wb.cleanup_storage()
    with wb.store.connect() as c:
        ids={r[0] for r in c.execute('SELECT id FROM plans')}
        assert c.execute('SELECT count(*) FROM observations WHERE id=?',(id,)).fetchone()[0] == 1
    assert {'plan_linked','plan_parent','plan_live'} <= ids and len(ids)==203
    assert 'plan_old' not in ids and file.exists() and result['cleanup']['removed']['expired_plans']==6


def test_raw_cache_expiry_cap_and_symlinks_do_not_touch_user_files(wb, monkeypatch):
    monkeypatch.setitem(POLICY,'raw_response_cap_bytes',20)
    old=raw(wb,'a'*64,4)
    recent1=raw(wb,'b'*64,2,b'12345678')
    recent2=raw(wb,'c'*64,1,b'12345678')
    pinned=raw(wb,'d'*64,10,b'12345678')
    with wb.store.transaction() as c:Store.put(c,'nav_sync',wb.fid,{'raw_sha256':'d'*64})
    outside=wb.root/'input/keep.txt';outside.parent.mkdir(parents=True);outside.write_text('user original')
    folder=old.parent
    (folder/('e'*64+'.txt')).symlink_to(outside)
    linked_folder=folder/'linked';linked_folder.symlink_to(outside.parent,target_is_directory=True)
    unknown=folder/'notes.txt';unknown.write_text('manual reference')
    result=wb.cleanup_storage()
    assert not old.exists() and not recent1.exists() and recent2.exists() and pinned.exists()
    assert outside.read_text()=='user original' and unknown.exists() and linked_folder.is_symlink()
    assert result['cleanup']['removed']['raw_files']==2


def test_demo_cleanup_never_removes_real_provider_files_or_real_records(wb):
    file=raw(wb,'a'*64,30)
    demo=Workbench(Store(wb.root/'data/demo/workbench.sqlite3','demo'),wb.root)
    before=wb.snapshot()
    demo.cleanup_storage()
    assert file.exists() and demo.storage_status()['provider_cache_bytes']==0
    assert wb.snapshot()==before


def test_automatic_cleanup_persists_hourly_schedule(wb):
    assert wb.cleanup_storage_due() is not None
    assert wb.cleanup_storage_due() is None
    wb.now=lambda:'2026-10-20T06:29:59Z'
    assert wb.cleanup_storage_due() is None
    wb.now=lambda:'2026-10-20T06:30:00Z'
    assert wb.cleanup_storage_due() is not None


def test_significant_database_free_space_is_actually_reclaimed(wb, monkeypatch):
    monkeypatch.setitem(POLICY,'unlinked_plan_limit',2)
    with wb.store.transaction() as c:
        for i in range(300):
            plan(c,'plan_large_'+str(i),'2026-10-19T00:00:00Z','2026-10-19T00:01:00Z',transient='x'*10000)
    before=wb.storage_status()['database_bytes']
    result=wb.cleanup_storage()
    assert result['cleanup']['compacted'] and result['storage']['database_bytes'] < before / 2
    assert result['storage']['counts']['plans']==2
