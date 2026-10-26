#!/usr/bin/env python3
"""Preview a Codex-normalized single-product data package; commit only with --commit."""
import argparse
from http.cookiejar import CookieJar
import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import build_opener, HTTPCookieProcessor, ProxyHandler, Request

ROOT=Path(__file__).resolve().parents[2]


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('manifest',type=Path)
    p.add_argument('--mode',choices=['real','demo'],required=True)
    p.add_argument('--commit',action='store_true',help='Save reviewed evidence; does not create holdings by itself')
    p.add_argument('--initialize-position',action='store_true',help='Also book explicitly provided current fund holdings')
    args=p.parse_args()
    if args.initialize_position and not args.commit:p.error('--initialize-position requires --commit')
    path=args.manifest.resolve();data=json.loads(path.read_text(encoding='utf-8'))
    state=json.loads((ROOT/'data/runtime/server.json').read_text())
    url=state['url'];op=build_opener(ProxyHandler({}),HTTPCookieProcessor(CookieJar()))
    csrf=json.load(op.open(url+'/api/v1/session',timeout=10))['csrf_token']
    def call(route,body=None):
        query={'mode':args.mode}
        payload=json.dumps(body,ensure_ascii=False).encode() if body is not None else None
        req=Request(url+'/api/v1'+route+'?'+urlencode(query),data=payload,
            headers={'content-type':'application/json','x-csrf-token':csrf})
        try:
            with op.open(req,timeout=60) as r:return json.load(r)
        except HTTPError as e:
            details=e.read().decode(errors='replace')
            raise ValueError(f'HTTP {e.code}: {details}') from None
    preview=call('/imports/batches/preview',data)
    report=ROOT/'output/reports/import-previews'/f'{preview["id"]}-{args.mode}.json'
    report.parent.mkdir(parents=True,exist_ok=True);report.write_text(json.dumps(preview,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    summary={k:preview[k] for k in ['id','status','asset_id','summary','can_commit_evidence','can_initialize_position','errors','position_errors','warnings']}
    summary['preview_report']=str(report)
    if args.commit:
        result=call('/imports/batches/'+preview['id']+'/commit',{'source_hash':preview['source_hash'],
            'preview_token':preview['preview_token'],'confirm':True,'initialize_position':args.initialize_position})
        summary['commit']=result
        report.write_text(json.dumps({'preview':preview,'result':result},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False,indent=2))
    return 0 if preview['can_commit_evidence'] else 1


if __name__=='__main__':
    try:raise SystemExit(main())
    except (OSError,ValueError,KeyError) as exc:
        print(str(exc));raise SystemExit(1)
