#!/usr/bin/env python3
"""Exercise the frozen backend with an isolated fake ledger and minimal PATH."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
from urllib.request import build_opener, HTTPCookieProcessor, ProxyHandler, Request
from http.cookiejar import CookieJar
import uuid
from datetime import datetime,timezone

ROOT=Path(__file__).resolve().parents[2]


def main():
    p=argparse.ArgumentParser();p.add_argument('--app',type=Path,default=ROOT/'output/builds/FundDesk.app');args=p.parse_args()
    resources=args.app/'Contents/Resources';executable=resources/'backend/FundingBackend'
    checks={};state=None;process=None
    with tempfile.TemporaryDirectory(prefix='frozen-smoke-',dir=ROOT/'data/runtime') as folder:
        root=Path(folder);log=(root/'process.log').open('ab');cookie=CookieJar();op=build_opener(ProxyHandler({}),HTTPCookieProcessor(cookie));csrf=''
        def start():
            nonlocal process,state,csrf
            instance=uuid.uuid4().hex
            process=subprocess.Popen([str(executable),'--root',str(root),'--resources',str(resources/'payload'),
                '--instance',instance,'--parent-pid',str(os.getpid())],cwd=root,stdin=subprocess.DEVNULL,
                stdout=log,stderr=log,env={'PATH':'/usr/bin:/bin','LANG':'en_US.UTF-8'})
            until=time.monotonic()+15
            while time.monotonic()<until:
                f=root/'data/runtime/server.json'
                if f.exists():
                    state=json.loads(f.read_text())
                    try:
                        health=json.load(op.open(state['url']+'/api/v1/health',timeout=1))
                        if health['instance_id']==instance:break
                    except OSError:pass
                if process.poll() is not None:raise RuntimeError((root/'process.log').read_text())
                time.sleep(.05)
            else:raise RuntimeError('Frozen service did not start')
            csrf=json.load(op.open(state['url']+'/api/v1/session'))['csrf_token']
        def api(path,body=None,method=None):
            request=Request(state['url']+'/api/v1'+path,data=json.dumps(body).encode() if body is not None else None,
                headers={'content-type':'application/json','x-csrf-token':csrf},method=method)
            return json.load(op.open(request))
        try:
            start();checks['bundled_python_minimal_path']=api('/health')['owner']=='desktop'
            checks['frozen_auto_market_capability']=any(p['id']=='PublicEstimateProvider' and p['enabled'] for p in api('/providers'))
            checks['frozen_gold_reference_capability']=any(p['id']=='GoldReferenceProvider' and p['enabled'] and p['supported_value_kinds']==['LOCAL_ESTIMATE'] for p in api('/providers'))
            checks['frozen_auto_market_health']=api('/health')['providers']['real_network']=='AUTO_VERIFIED_FUNDS'
            page=op.open(state['url']+'/').read();checks['bundled_frontend']=b'<div id="root">' in page
            checks['packaged_template']=b'fund_code' in op.open(state['url']+'/api/v1/templates/funds').read()
            before=api('/state?mode=real');checks['empty_isolated_real']=not before['funds'] and before['revision']==0
            api('/events?mode=real',{'kind':'CASH_DEPOSIT','payload':{'amount':'45.00'},'key':'frozen-synthetic-deposit',
                'occurred_at':datetime.now(timezone.utc).isoformat(),'expected_revision':0})
            fund=api('/funds?mode=real',{'fund_code':'009999','name':'Synthetic package test','share_class':'C'})
            context=api('/state?mode=real')['funds'][0]['daily_t']
            checks['frozen_t_presets_default_observe']=context['profile']['level']=='OBSERVE' and len(context['presets']['options'])==4
            profile=api('/funds/'+fund['id']+'/t-profile?mode=real',{'level':'BALANCED','expected_version':0},'PUT')
            checks['frozen_t_profile_saved']=profile['level']=='BALANCED' and profile['_version']==1
            outside=api('/orders?mode=real',{'fund_id':fund['id'],'side':'BUY','requested':'200',
                'key':'frozen-outside-cash-buy','paid_immediately':True,'occurred_at':datetime.now(timezone.utc).isoformat()})
            external_state=api('/state?mode=real')
            checks['frozen_external_buy_without_cash_budget']=external_state['portfolio']['cash']=='45.00' and external_state['portfolio']['pending_buy']=='200' and external_state['portfolio']['reserved_cash']=='0' and external_state['orders'][0]['cash_source']=='EXTERNAL'
            for kind,payload in [('ORDER_CANCELED',{}),('ORDER_REFUNDED',{'amount':'200'})]:
                api('/events?mode=real',{'kind':kind,'payload':dict(payload,order_id=outside['order_id']),
                    'key':'frozen-outside-'+kind,'occurred_at':datetime.now(timezone.utc).isoformat()})
            external_state=api('/state?mode=real')
            checks['frozen_external_refund_preserves_scope']=external_state['portfolio']['cash']=='45.00' and external_state['portfolio']['refund_due']=='0' and external_state['portfolio']['pnl']=='0.00'
            date=api('/state?mode=real')['business_date']
            for name,units,cost,intent in [('synthetic-base','10','10','BASE'),('synthetic-t','5','4','TACTICAL')]:
                api('/events?mode=real',{'kind':'OPENING_LOT','payload':{'fund_id':fund['id'],'lot_id':name,
                    'units':units,'cost':cost,'bucket':intent,'acquired':'2026-01-01','available_from':'2026-01-03'},
                    'key':'opening-'+name,'occurred_at':datetime.now(timezone.utc).isoformat()})
            api('/observations?mode=real',{'fund_id':fund['id'],'value_kind':'OFFICIAL_NAV','effective_date':date,'value':'1','source':'synthetic'})
            api('/funds/'+fund['id']+'/rules?mode=real',{'verified':True,'source':'Synthetic sell projection test','effective_from':'2020-01-01',
                'lot_method':'FIFO','purchase_rate':'0','unit_step':'0.01',
                'redemption_tiers':[{'min_days':0,'max_days':None,'rate':'0'}]},'PUT')
            before_sell_preview=api('/state?mode=real')
            impact=api('/funds/'+fund['id']+'/sell-preview?mode=real',{'requested':'7','bucket':'TACTICAL'})
            checks['frozen_t_current_holding_pnl']=impact['before']['groups']['TACTICAL']['unrealized']=='1.00'
            checks['frozen_t_sell_profit_projection']=impact['status']=='READY' and impact['expected_realized_pnl']=='1.00' and impact['fallback_principal']=='2.00' and impact['after']['total_pnl']==impact['before']['total_pnl']=='1.00'
            after_sell_preview=api('/state?mode=real')
            checks['frozen_sell_preview_no_writes']=before_sell_preview['revision']==after_sell_preview['revision'] and before_sell_preview['lots']==after_sell_preview['lots'] and not impact['ledger_posted']
            order=api('/orders?mode=real',{'fund_id':fund['id'],'side':'SELL','bucket':'TACTICAL','requested':'7',
                'allocations':[{'lot_id':'synthetic-base','units':'7'}],'key':'frozen-synthetic-sell','occurred_at':datetime.now(timezone.utc).isoformat()})
            api('/events?mode=real',{'kind':'ORDER_CONFIRMED','payload':{'order_id':order['order_id'],'units':'7',
                'nav':'1','gross':'7','fee':'0','net':'7','nav_date':date,'confirm_date':date,'is_final':True},
                'key':'frozen-synthetic-confirm','occurred_at':datetime.now(timezone.utc).isoformat()})
            positions=api('/state?mode=real')['funds'][0]['position_buckets']
            checks['frozen_t_overflow_attribution']=positions['groups']['TACTICAL']['realized']=='1.00' and positions['sales'][0]['fallback_principal']=='2.00'
            checks['frozen_total_pnl_reconciles']=positions['total_pnl']=='1.00'
            backup=api('/backups?mode=real',{});preview=api('/restore/preview?mode=real',{'id':backup['id']})
            checks['frozen_backup_integrity']=preview['cash']=='45.00'
            api('/restore/commit?mode=real',{'id':backup['id'],'sha256':backup['sha256'],'confirm':True})
            checks['frozen_restore']=api('/state?mode=real')['portfolio']['cash']=='45.00'
            process.terminate();process.wait(timeout=8);checks['graceful_state_cleanup']=not (root/'data/runtime/server.json').exists()
            start();restored=api('/state?mode=real');checks['restart_preserves_records']=restored['portfolio']['cash']=='45.00' and restored['funds'][0]['position_buckets']['groups']['TACTICAL']['realized']=='1.00'
            checks['frozen_t_profile_restart']=restored['funds'][0]['daily_t']['profile']['level']=='BALANCED'
            checks['no_data_in_bundle']=not list(args.app.rglob('*.sqlite3')) and not list(args.app.rglob('*.jpg'))
            f2=api('/funds?mode=real',{'fund_code':'008888','name':'Anonymous tracking test','share_class':'C'})
            fid=f2['id'];prefix='/funds/'+fid
            api(prefix+'/rules?mode=real',{'verified':True,'source':'Synthetic package test','effective_from':'2020-01-01',
                'lot_method':'FIFO','purchase_rate':'0','unit_step':'0.01',
                'redemption_tiers':[{'min_days':0,'max_days':None,'rate':'0'}]},'PUT')
            api('/statements?mode=real',{'fund_id':fid,'channel':'手工记录','history_complete':True,
                'sources':[{'sha256':'c'*64,'filename':'synthetic.jpg'}],
                'rows':[{'displayed_at':d+' 14:00:00','side':side,'unit':unit,'value':v,'status':'SHOWN',
                         'source_refs':[{'sha256':'c'*64,'row':i+1}]}
                        for i,(d,side,unit,v) in enumerate([('2026-08-01','BUY','CNY','120'),
                            ('2026-09-01','BUY','CNY','80'),('2026-09-05','SELL','FUND_UNIT','100')])]})
            for d,v in [('2026-08-01','1.2'),('2026-09-01','.8'),('2026-09-05','.9'),('2026-09-10','.95')]:
                api('/observations?mode=real',{'fund_id':fid,'value_kind':'OFFICIAL_NAV','effective_date':d,'value':v,'source':'synthetic'})
            a=api(prefix+'/bucket-analysis?mode=real',{'method':'FROM_CURRENT_T'})
            api(prefix+'/bucket-classification?mode=real',dict(a,expected_version=0),'PUT')
            p=api(prefix+'/tracking/preview?mode=real',{'base_value':'70'})
            api(prefix+'/tracking/start?mode=real',dict(p,accept_reconstruction=True))
            tracked=next(f for f in api('/state?mode=real')['funds'] if f['id']==fid)
            checks['frozen_current_holding_split']=tracked['position_buckets']['groups']['BASE']['value']=='70.00' and tracked['position_buckets']['groups']['TACTICAL']['value']=='25.00'
            checks['frozen_tracking_basis_preserved']=tracked['tracking']['basis']=='USER_ACCEPTED_RECONSTRUCTION' and not tracked['initialization_pending']
            checks['frozen_historical_pnl_preserved']=tracked['position_buckets']['total_pnl']=='-15.00' and tracked['position_buckets']['groups']['TACTICAL']['realized']=='10.00'
            b2=api('/backups?mode=real',{})
            api('/restore/commit?mode=real',{'id':b2['id'],'sha256':b2['sha256'],'confirm':True})
            process.terminate();process.wait(timeout=8);start()
            tracked=next(f for f in api('/state?mode=real')['funds'] if f['id']==fid)
            checks['frozen_tracking_restore_restart']=tracked['position_buckets']['groups']['BASE']['units']=='73.68' and tracked['strategy']['type']=='MANUAL_BUCKETS'
            storage=api('/storage?mode=real')
            checks['frozen_storage_policy']=storage['policy']['intraday_days']==7 and storage['policy']['unlinked_plan_limit']==200
            before_cleanup=api('/state?mode=real')
            api('/storage/cleanup?mode=real',{})
            after_cleanup=api('/state?mode=real')
            checks['frozen_cleanup_preserves_financial_state']=before_cleanup['portfolio']==after_cleanup['portfolio'] and before_cleanup['revision']==after_cleanup['revision']
            catalog=api('/imports/catalog')
            checks['frozen_import_single_asset_contract']=catalog['one_asset_per_batch'] and catalog['input_format']=='CODEX_NORMALIZED_JSON'
            checks['frozen_import_templates']=bool(json.loads(op.open(state['url']+'/api/v1/imports/templates/fund').read())['asset'])
            refs=[{'sha256':'d'*64,'row':1}]
            package={'schema_version':1,'client_reference':'frozen-normalized-fund',
                'asset':{'kind':'GOLD_FUND','currency':'CNY','fund_code':'007777','share_class':'C','name':'Synthetic package import','channel':'synthetic'},
                'sources':[{'sha256':'d'*64,'filename':'synthetic.json'}],
                'transactions':[{'displayed_at':'2026-09-01 14:00:00','time_precision':'MINUTE','original_category':'ACTIVITY',
                    'reference_nav_date':'2026-09-01','reference_nav_date_basis':'Synthetic explicit credit reconstruction assumption',
                    'side':'BUY','unit':'CNY','value':'100','source_refs':refs}],
                'official_navs':[{'effective_date':'2026-10-19','value':'1.2','source_refs':refs}],
                'position':{'snapshot_date':'2026-10-19','units':'100','unit':'FUND_UNIT','base_units':'80',
                    'remaining_cost_cny':'100','reported_value_cny':'120','source_refs':refs}}
            before=api('/state?mode=real')
            draft=api('/imports/batches/preview?mode=real',package)
            checks['frozen_import_preview_no_ledger_change']=api('/state?mode=real')['revision']==before['revision'] and draft['can_initialize_position']
            commit_data={'source_hash':draft['source_hash'],'preview_token':draft['preview_token'],'confirm':True,'initialize_position':True}
            committed=api('/imports/batches/'+draft['id']+'/commit?mode=real',commit_data)
            loaded=api('/imports/assets/'+draft['asset_id']+'?mode=real')
            row=loaded['transactions']['rows'][0]
            checks['frozen_activity_annotations_preserved']=row['time_precision']=='MINUTE' and row['original_category']=='ACTIVITY' and row['reference_nav_date']=='2026-09-01'
            checks['frozen_standard_fund_import']=committed['position_initialized'] and loaded['opening']['basis']=='USER_REVIEWED_CURRENT_SNAPSHOT'
            imported=next(f for f in api('/state?mode=real')['funds'] if f['id']==draft['asset_id'])
            checks['frozen_import_buckets']=imported['position_buckets']['groups']['BASE']['units']=='80' and imported['position_buckets']['groups']['TACTICAL']['units']=='20'
            checks['frozen_import_retry']=api('/imports/batches/'+draft['id']+'/commit?mode=real',commit_data)['duplicate']
            package['client_reference']='frozen-normalized-gram-gold'
            package['asset']={'kind':'GOLD_GRAM','currency':'CNY','name':'Synthetic gram gold','channel':'synthetic','product_code':'GOLD-TEST'}
            package['official_navs']=[];package['position']['unit']='GRAM';package['position'].pop('base_units')
            package['transactions'][0].pop('reference_nav_date')
            package['transactions'][0].pop('reference_nav_date_basis')
            draft=api('/imports/batches/preview?mode=real',package)
            assert not draft['errors'], draft['errors']
            before=api('/state?mode=real')
            api('/imports/batches/'+draft['id']+'/commit?mode=real',{'source_hash':draft['source_hash'],'preview_token':draft['preview_token'],'confirm':True})
            gold=api('/gold-products?mode=real')[0]
            checks['frozen_gram_gold_separate']=gold['quantity_unit']=='GRAM' and gold['reported_holding_pnl_cny']=='20' and api('/state?mode=real')['portfolio']==before['portfolio']
            process.terminate();process.wait(timeout=8);start()
            checks['frozen_import_restart']=api('/gold-products?mode=real')[0]['holding']['units']=='100' and api('/imports/batches?mode=real')['total']==2
        finally:
            if process and process.poll() is None:process.terminate();process.wait(timeout=8)
            log.close()
    assert all(checks.values()),checks
    report={'version':'0.18.0','checks':checks,'all_passed':True,'real_user_ledger_modified':False,
            'exe_sha256':hashlib.sha256(executable.read_bytes()).hexdigest()}
    (ROOT/'output/reports/v0.18.0-desktop-backend-smoke.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))


if __name__=='__main__':main()
