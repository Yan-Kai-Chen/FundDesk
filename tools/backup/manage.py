#!/usr/bin/env python3
"""Backup CLI through the running application's serialized API."""
import argparse
import http.cookiejar
import json
from pathlib import Path
import urllib.request
import urllib.error
import sys

root=Path(__file__).resolve().parents[2]
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('action',choices=['list','create','preview','restore'])
parser.add_argument('--mode',choices=['demo','real'],default='demo')
parser.add_argument('--id')
parser.add_argument('--confirm',action='store_true')
args=parser.parse_args()
try:
    url=json.loads((root/'data/runtime/server.json').read_text())['url']
    opener=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    session=json.load(opener.open(url+'/api/v1/session',timeout=5))
    def call(path,body=None):
        request=urllib.request.Request(url+'/api/v1'+path+'?mode='+args.mode,
            data=json.dumps(body).encode() if body is not None else None,
            headers={'Content-Type':'application/json','X-CSRF-Token':session['csrf_token']})
        return json.load(opener.open(request,timeout=15))
    if args.action=='list':result=call('/backups')
    elif args.action=='create':result=call('/backups',{})
    else:
        if not args.id:parser.error('preview/restore 需要 --id')
        result=call('/restore/preview',{'id':args.id})
        if args.action=='restore':
            if not args.confirm:parser.error('请先 preview，再以 --confirm 明确确认恢复')
            result=call('/restore/commit',{'id':args.id,'sha256':result['manifest']['sha256'],'confirm':True})
    print(json.dumps(result,ensure_ascii=False,indent=2))
except (OSError,ValueError,urllib.error.URLError) as exc:
    print('操作未完成；请先启动本地应用并核对备份编号。'+str(exc),file=sys.stderr)
    sys.exit(1)
