from copy import deepcopy
from decimal import Decimal as D
import json
from pathlib import Path
import pytest
from pydantic import ValidationError
from app.domain.finance import Problem
from app.persistence.store import Store
from app.schemas.asset_import import AssetDataBatch
from conftest import deposit


def manifest(w,kind='FUND',ref='synthetic-batch-001'):
    source={'sha256':'a'*64,'filename':'synthetic-normalized-source.json'}
    asset={'kind':kind,'name':'Synthetic import product','channel':'支付宝','currency':'CNY'}
    if kind=='GOLD_GRAM': asset['product_code']='SYNTHETIC-GOLD'
    else: asset.update(fund_code='000099',share_class='C')
    refs=[{'sha256':source['sha256'],'row':1}]
    return {'schema_version':1,'client_reference':ref,'asset':asset,'sources':[source],
        'transactions':[{'displayed_at':'2026-09-01 14:00:00','side':'BUY','unit':'CNY','value':'100.00','status':'SHOWN','source_refs':refs}],
        'position':{'snapshot_date':'2026-10-19','units':'100.00','unit':'GRAM' if kind=='GOLD_GRAM' else 'FUND_UNIT',
                    'remaining_cost_cny':'100.00','reported_value_cny':'120.00','source_refs':refs,
                    **({} if kind=='GOLD_GRAM' else {'base_units':'80.00'})},
        'official_navs':[] if kind=='GOLD_GRAM' else [{'effective_date':'2026-10-19','value':'1.2000','source_refs':refs}]}


def commit(w,p,initialize=False):
    return w.commit_asset_batch(p['id'],{'source_hash':p['source_hash'],'preview_token':p['preview_token'],'confirm':True,'initialize_position':initialize})


def count(w,table):
    with w.store.connect() as c:return c.execute('SELECT COUNT(*) FROM '+table).fetchone()[0]


def test_preview_changes_no_financial_or_product_data_and_has_single_asset_contract(wb):
    m=manifest(wb);before=wb.snapshot();p=wb.preview_asset_batch(m)
    assert not p['errors'] and p['can_initialize_position']
    assert p['summary']['new_asset']
    after=wb.snapshot()
    assert before['funds']==after['funds'] and before['portfolio']==after['portfolio'] and before['revision']==after['revision']
    assert count(wb,'observations')==0 and count(wb,'events')==0
    for bad in [dict(m,assets=[m['asset']]),dict(m,asset=[m['asset']]),dict(m,unknown_field=1)]:
        with pytest.raises(ValidationError):AssetDataBatch.model_validate(bad)


def test_commit_evidence_only_never_creates_fills_or_holdings(wb):
    p=wb.preview_asset_batch(manifest(wb));result=commit(wb,p)
    assert result['backup_id'] and not result['position_initialized']
    assert wb.preview_restore(result['backup_id'])['revision']==0
    f=next(f for f in wb.snapshot()['funds'] if f['id']==p['asset_id'])
    assert f['units']==0 and f['initialization_pending'] and f['reported_holding']['units']=='100.00'
    assert count(wb,'events')==0 and f['statement']['summary']['count']==1
    assert not f['rules']['verified'] and f['daily_t']['profile']['level']=='OBSERVE'


def test_explicit_position_import_exact_buckets_scope_and_unknown_availability(wb):
    p=wb.preview_asset_batch(manifest(wb));r=commit(wb,p,True)
    s=wb.snapshot();f=next(f for f in s['funds'] if f['id']==p['asset_id'])
    assert r['position_initialized'] and f['units']==D('100') and f['cost']==D('100') and f['market_value']==D('120')
    assert f['sellable_units']==0 and all(l['acquired'] is None for l in f['lots'])
    assert not f['initialization_pending'] and f['migration_opening'] and not f['tracking']
    assert f['position_buckets']['groups']['BASE']['units']==D('80')
    assert f['position_buckets']['groups']['TACTICAL']['units']==D('20')
    assert f['position_buckets']['total_pnl']==D('20')
    assert not s['orders'] and s['portfolio']['cash']==0


def test_gram_gold_separate_identity_units_reported_profit_and_no_fund_totals(wb):
    before=wb.snapshot();p=wb.preview_asset_batch(manifest(wb,'GOLD_GRAM'))
    assert not p['errors'] and not p['can_initialize_position']
    with pytest.raises(Problem):commit(wb,p,True)
    commit(wb,p)
    g=wb.gold_products()[0]
    assert g['quantity_unit']=='GRAM' and g['holding']['unit']=='GRAM' and g['reported_holding_pnl_cny']==D('20')
    assert not g['included_in_fund_portfolio'] and g['statement']['rows'][0]['unit']=='CNY'
    assert wb.snapshot()['funds']==before['funds'] and wb.snapshot()['portfolio']==before['portfolio']
    assert count(wb,'events')==0


def test_gold_fund_is_still_fund_shares(wb):
    p=wb.preview_asset_batch(manifest(wb,'GOLD_FUND'));commit(wb,p,True)
    f=next(f for f in wb.snapshot()['funds'] if f['id']==p['asset_id'])
    assert f['instrument_type']=='GOLD_LINKED' and f['quantity_unit']=='FUND_UNIT' and not wb.gold_products()


def test_standard_rows_are_not_cleaned_by_app(wb):
    m=manifest(wb);m['transactions'].append(deepcopy(m['transactions'][0]))
    p=wb.preview_asset_batch(m)
    assert p['errors'][0]['code']=='NORMALIZED_ROW_DUPLICATE' and len(wb.snapshot()['funds'])==1
    with pytest.raises(Problem):commit(wb,p)
    m=manifest(wb);m['transactions'][0]['status']='CLOSED'
    with pytest.raises(ValidationError):AssetDataBatch.model_validate(m)
    m=manifest(wb);p=wb.preview_asset_batch(m);commit(wb,p)
    m['client_reference']='overlap-normalized-batch';q=wb.preview_asset_batch(m)
    assert q['errors'][0]['code']=='NORMALIZED_ROW_DUPLICATE'


def test_commit_repeat_and_repreview_are_idempotent_with_conflict_guard(wb):
    m=manifest(wb);p=wb.preview_asset_batch(m);one=commit(wb,p,True)
    assert commit(wb,p,True)['duplicate'] and wb.preview_asset_batch(m)['status']=='COMMITTED'
    assert count(wb,'events')==2
    with pytest.raises(Problem):commit(wb,p,False)
    m['position']['units']='101'
    with pytest.raises(Problem) as e:wb.preview_asset_batch(m)
    assert e.value.code=='IDEMPOTENCY_CONFLICT' and wb.snapshot()['revision']==one['revision']


def test_revised_preview_invalidates_old_token_and_changes_no_fund(wb):
    m=manifest(wb);p=wb.preview_asset_batch(m);m['position']['base_units']='70';q=wb.preview_asset_batch(m)
    assert p['id']==q['id'] and p['preview_token']!=q['preview_token']
    with pytest.raises(Problem):commit(wb,p,True)
    commit(wb,q,True)


def test_stale_ledger_nav_and_expired_preview_require_new_preview(wb):
    m=manifest(wb);p=wb.preview_asset_batch(m);deposit(wb)
    with pytest.raises(Problem) as e:commit(wb,p)
    assert e.value.code=='VERSION_CONFLICT'
    p=wb.preview_asset_batch(m);wb.now=lambda:'2026-10-22T05:30:00+00:00'
    with pytest.raises(Problem) as e:commit(wb,p)
    assert e.value.code=='IMPORT_EXPIRED'


def test_position_conflicts_block_opening_but_can_save_evidence(wb):
    m=manifest(wb);m['position']['base_units']=None
    p=wb.preview_asset_batch(m)
    assert not p['errors'] and p['position_errors'][0]['code']=='BUCKET_REQUIRED'
    with pytest.raises(Problem):commit(wb,p,True)
    commit(wb,p)
    assert count(wb,'events')==0


def test_missing_cost_remains_unknown_not_zero(wb):
    m=manifest(wb);m['position']['remaining_cost_cny']=None
    p=wb.preview_asset_batch(m);commit(wb,p,True)
    f=next(f for f in wb.snapshot()['funds'] if f['id']==p['asset_id'])
    assert f['cost'] is None and f['position_buckets']['total_pnl'] is None


def test_existing_opening_prevents_double_count_across_different_batch_ids(wb):
    m=manifest(wb);p=wb.preview_asset_batch(m);commit(wb,p,True)
    m['client_reference']='synthetic-second-opening';m['transactions']=[];q=wb.preview_asset_batch(m)
    assert not q['can_initialize_position'] and q['position_errors'][0]['code']=='HISTORY_OVERLAP'
    with pytest.raises(Problem):commit(wb,q,True)
    assert count(wb,'events')==2


def test_batch_failure_rolls_back_fund_statement_nav_and_position(wb,monkeypatch):
    p=wb.preview_asset_batch(manifest(wb));original=wb.event
    def fail(kind,payload,*a,**k):
        if payload.get('bucket')=='TACTICAL':raise Problem('SYNTHETIC_FAILURE','synthetic')
        return original(kind,payload,*a,**k)
    monkeypatch.setattr(wb,'event',fail)
    with pytest.raises(Problem):commit(wb,p,True)
    assert count(wb,'events')==0 and count(wb,'observations')==0
    assert len(wb.snapshot()['funds'])==1 and wb.asset_batch(p['id'])['status']=='PREVIEWED'


@pytest.mark.parametrize('change,code',[
    (lambda m:m['position'].update(unit='GRAM'),'UNIT_MISMATCH'),
    (lambda m:m['transactions'][0].update(unit='GRAM'),'UNIT_MISMATCH'),
    (lambda m:m['transactions'][0].update(displayed_at='2027-01-01 14:00:00'),'FUTURE_EVENT'),
    (lambda m:m['official_navs'][0].update(effective_date='2027-01-01'),'FUTURE_OBSERVATION'),
])
def test_domain_errors_are_previewed_without_any_partial_product(wb,change,code):
    m=manifest(wb);change(m);p=wb.preview_asset_batch(m)
    assert p['errors'][0]['code']==code and not p['can_commit_evidence'] and len(wb.snapshot()['funds'])==1
    with pytest.raises(Problem):commit(wb,p)


def test_source_refs_must_belong_to_package(wb):
    m=manifest(wb);m['transactions'][0]['source_refs'][0]['sha256']='b'*64
    p=wb.preview_asset_batch(m);assert p['errors'][0]['code']=='SOURCE_REFERENCE'
    m=manifest(wb);m['sources'].append(deepcopy(m['sources'][0]))
    with pytest.raises(Problem):wb.preview_asset_batch(m)


def test_distinct_share_classes_channels_and_explicit_fund_identity(wb):
    m=manifest(wb);p=wb.preview_asset_batch(m);commit(wb,p)
    for field,value in [('share_class','A'),('channel','另一渠道')]:
        n=deepcopy(m);n['client_reference']='other-'+field;n['asset'][field]=value;q=wb.preview_asset_batch(n)
        assert q['asset_id']!=p['asset_id']
    n=deepcopy(m);n['client_reference']='wrong-explicit-fund';n['asset']['fund_id']=wb.fid
    with pytest.raises(Problem) as e:wb.preview_asset_batch(n)
    assert e.value.code=='ASSET_MISMATCH'


def test_listing_pagination_and_profile_unchanged(wb):
    m=manifest(wb);p=wb.preview_asset_batch(m)
    rows=wb.asset_batches(1,0)
    assert rows['total']==1 and rows['items'][0]['id']==p['id'] and 'manifest' not in rows['items'][0]
    assert wb.asset_batches(1,1)['items']==[]
    with pytest.raises(Problem):wb.asset_batches(101)
    with pytest.raises(Problem):wb.asset_batch('../escape')


def test_nav_conflict_never_silently_revises_existing_nav(wb):
    m=manifest(wb);p=wb.preview_asset_batch(m);commit(wb,p)
    m['client_reference']='nav-conflict-batch';m['transactions']=[];m['official_navs'][0]['value']='1.3'
    p=wb.preview_asset_batch(m)
    assert p['errors'][0]['code']=='NAV_CONFLICT' and count(wb,'observations')==1


def test_reported_market_value_mismatch_blocks_only_initialization(wb):
    m=manifest(wb);m['position']['reported_value_cny']='999'
    p=wb.preview_asset_batch(m)
    assert not p['errors'] and p['position_errors'][0]['code']=='POSITION_VALUE_MISMATCH'


def test_api_catalog_schema_modes_preview_commit_and_gold(tmp_path):
    from fastapi.testclient import TestClient
    from app.main import create_app
    app=create_app(tmp_path,testing=True);w=app.state.workbenches['real'];w.now=lambda:'2026-10-20T05:30:00Z'
    with TestClient(app) as client:
        assert client.get('/api/v1/imports/catalog').status_code==401
        csrf=client.get('/api/v1/session').json()['csrf_token']
        m=manifest(w,'GOLD_GRAM')
        assert client.post('/api/v1/imports/batches/preview?mode=real',json=m).status_code==403
        client.headers['x-csrf-token']=csrf
        assert client.get('/api/v1/imports/batches').status_code==422
        assert client.get('/api/v1/imports/catalog').json()['one_asset_per_batch']
        p=client.post('/api/v1/imports/batches/preview?mode=real',json=m).json()
        assert p['can_commit_evidence']
        result=client.post('/api/v1/imports/batches/'+p['id']+'/commit?mode=real',json={'source_hash':p['source_hash'],'preview_token':p['preview_token'],'confirm':True})
        assert result.status_code==200 and len(client.get('/api/v1/gold-products?mode=real').json())==1
        assert client.get('/api/v1/gold-products?mode=demo').json()==[]
        assert client.get('/api/v1/imports/batches?mode=real').json()['total']==1
        assert client.get('/api/v1/imports/batches/'+p['id']+'?mode=demo').status_code==400
        assert client.get('/api/v1/imports/assets/'+p['asset_id']+'?mode=real').json()['holding']['unit']=='GRAM'
        assert client.get('/api/v1/imports/assets/'+p['asset_id']+'?mode=demo').status_code==400
        m['asset']['kind']='FUND';m['asset']['fund_code']=99
        assert client.post('/api/v1/imports/batches/preview?mode=real',json=m).status_code==422


def test_clean_incremental_rows_append_without_replacing_existing_records(wb):
    m=manifest(wb);p=wb.preview_asset_batch(m);commit(wb,p)
    m['client_reference']='clean-incremental-row';m['transactions'][0]['displayed_at']='2026-09-02 14:00:00'
    q=wb.preview_asset_batch(m);assert not q['errors'];commit(wb,q)
    assert len(wb.imported_asset(p['asset_id'])['transactions']['rows'])==2


def test_closed_float_money_wrong_currency_and_source_paths_are_rejected(wb):
    m=manifest(wb)
    cases=[lambda x:x['asset'].update(currency='USD'),lambda x:x['position'].update(units=1.1),
           lambda x:x['sources'][0].update(filename='../secret'),lambda x:x.update(assets=[x['asset']])]
    for change in cases:
        x=deepcopy(m);change(x)
        with pytest.raises(ValidationError):AssetDataBatch.model_validate(x)


def test_cli_reads_standard_json_previews_then_commits_in_isolated_api(tmp_path,monkeypatch,capsys):
    import importlib.util
    import io
    import sys
    from urllib.error import HTTPError
    from urllib.parse import urlsplit
    from fastapi.testclient import TestClient
    from app.main import create_app
    source=Path(__file__).resolve().parents[2]/'tools/data/import_asset.py'
    spec=importlib.util.spec_from_file_location('isolated_import_cli',source)
    cli=importlib.util.module_from_spec(spec);spec.loader.exec_module(cli);cli.ROOT=tmp_path
    app=create_app(tmp_path,testing=True);w=app.state.workbenches['real'];w.now=lambda:'2026-10-20T05:30:00Z'
    m=manifest(w);file=tmp_path/'input/package.json';file.parent.mkdir(exist_ok=True);file.write_text(json.dumps(m))
    runtime=tmp_path/'data/runtime';runtime.mkdir();(runtime/'server.json').write_text(json.dumps({'url':'http://testserver'}))
    with TestClient(app) as client:
        class Opener:
            def open(self,req,timeout=10):
                url=req if isinstance(req,str) else req.full_url
                parsed=urlsplit(url)
                response=client.request('GET' if isinstance(req,str) else req.get_method(),parsed.path+'?'+parsed.query,
                    content=None if isinstance(req,str) else req.data,headers={} if isinstance(req,str) else dict(req.header_items()))
                if response.status_code>=400:raise HTTPError(url,response.status_code,'error',{},io.BytesIO(response.content))
                return io.BytesIO(response.content)
        monkeypatch.setattr(cli,'build_opener',lambda *a:Opener())
        monkeypatch.setattr(sys,'argv',['import_asset.py',str(file),'--mode','real'])
        assert cli.main()==0 and not w.snapshot()['funds'];capsys.readouterr()
        monkeypatch.setattr(sys,'argv',['import_asset.py',str(file),'--mode','real','--commit','--initialize-position'])
        assert cli.main()==0
        output=json.loads(capsys.readouterr().out)
        assert Path(output['preview_report']).is_file() and w.snapshot()['funds'][0]['units']==D('100')
        assert cli.main()==0 and w.snapshot()['revision']==2
