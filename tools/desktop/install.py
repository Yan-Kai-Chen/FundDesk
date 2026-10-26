#!/usr/bin/env python3
"""Install a reviewed local build with a data snapshot and recoverable app copy.

No TCC changes, wallet actions, account login or filesystem migration. A running
app waiting for macOS authorization is kept in place instead of being killed as
an assumed startup failure.
"""
from datetime import datetime, timezone
import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shutil
import signal
import sqlite3
import subprocess
import time
from urllib.request import build_opener, ProxyHandler
from urllib.request import HTTPCookieProcessor
from http.cookiejar import CookieJar
import uuid
import zipfile

ROOT=Path(__file__).resolve().parents[2]


def bundle_info(path):
    return plistlib.loads((path/'Contents/Info.plist').read_bytes())


def validate_candidate(candidate, installed, root=ROOT):
    candidate,installed=candidate.resolve(),installed.resolve()
    if candidate==installed or candidate.suffix!='.app':
        raise ValueError('请选择与安装位置不同的完整.app构建')
    config=json.loads((root/'config/app/desktop.json').read_text())
    info=bundle_info(candidate)
    if info.get('CFBundleIdentifier')!=config['bundle_identifier'] or info.get('ShixuWorkspaceID')!=config['workspace_id']:
        raise ValueError('应用身份或工作区不匹配；原应用保持')
    if not (candidate/'Contents/Resources/backend/FundingBackend').is_file():
        raise ValueError('应用缺少内置后台')
    if list(candidate.rglob('*.sqlite3')):
        raise ValueError('构建中包含私人数据库，不能安装')
    if installed.exists():
        old=bundle_info(installed)
        if old.get('CFBundleIdentifier')!=config['bundle_identifier'] or old.get('ShixuWorkspaceID')!=config['workspace_id']:
            raise ValueError('安装位置属于另一应用，原文件保持')
    subprocess.run(['/usr/bin/codesign','--verify','--deep','--strict',str(candidate)],check=True)
    return info


def snapshot_database(root, mode):
    source=root/'data'/mode/'workbench.sqlite3'
    if not source.is_file():
        return None
    id='backup_'+uuid.uuid4().hex[:20]
    folder=root/'data/backups'/mode/id;folder.mkdir(parents=True)
    target=folder/'workbench.sqlite3'
    try:
        with sqlite3.connect(source.resolve().as_uri()+'?mode=ro',uri=True) as c, sqlite3.connect(target) as out:
            c.backup(out,pages=256,sleep=.01)
            schema=out.execute('PRAGMA user_version').fetchone()[0]
        meta={'id':id,'mode':mode,'created_at':datetime.now(timezone.utc).isoformat(),
            'schema_version':schema,'sha256':hashlib.sha256(target.read_bytes()).hexdigest(),
            'size':target.stat().st_size,'automatic':False,'reason':'BEFORE_APP_UPDATE',
            'notice':'更新前的一致资料快照；原始截图仍在input，应用替换不改写账本。'}
        (folder/'manifest.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2)+'\n')
        with zipfile.ZipFile(folder/'backup.zip','w',zipfile.ZIP_DEFLATED) as z:
            z.write(target,'workbench.sqlite3');z.write(folder/'manifest.json','manifest.json')
        return meta
    except Exception:
        shutil.rmtree(folder)
        raise


def ledger_signature(root):
    result={}
    for mode in ('real','demo'):
        path=root/'data'/mode/'workbench.sqlite3'
        if not path.is_file():continue
        with sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True) as c:
            result[mode]={'schema':c.execute('PRAGMA user_version').fetchone()[0],
                'tip':c.execute('SELECT COALESCE(MAX(seq),0) FROM events').fetchone()[0]}
    return result


def application_pids(installed):
    executable=str(installed/'Contents/MacOS/Shixu')
    rows=subprocess.check_output(['/bin/ps','-axo','pid=,command='],text=True).splitlines()
    return [int(parts[0]) for row in rows if len(parts:=row.strip().split(None,1))==2 and parts[1]==executable]


def stop_application(installed):
    pids=application_pids(installed)
    for pid in pids:os.kill(pid,signal.SIGTERM)
    for _ in range(60):
        alive=[]
        for pid in pids:
            try:os.kill(pid,0);alive.append(pid)
            except ProcessLookupError:pass
        if not alive:return
        time.sleep(.1)
    raise RuntimeError('原应用仍在退出，已保留原安装文件，请稍后重试')


def probe(root, version):
    try:
        runtime=json.loads((root/'data/runtime/server.json').read_text())
        config=json.loads((root/'config/app/desktop.json').read_text())
        # Never send even a read to a supplied remote/foreign workspace URL.
        from urllib.parse import urlparse
        u=urlparse(runtime['url'])
        if u.scheme!='http' or u.hostname!='127.0.0.1' or not u.port or u.username or u.password or u.path not in ('','/'):
            return False
        op=build_opener(ProxyHandler({}),HTTPCookieProcessor(CookieJar()))
        h=json.load(op.open(runtime['url']+'/api/v1/health',timeout=1))
        if not (h['version']==version and h['workspace_id']==config['workspace_id'] and
                h['instance_id']==runtime['instance_id'] and h['owner']=='desktop'):
            return False
        # Health alone does not prove a version can interpret the existing book.
        op.open(runtime['url']+'/api/v1/session',timeout=1).close()
        snapshot=json.load(op.open(runtime['url']+'/api/v1/state?mode=real',timeout=3))
        return snapshot.get('mode')=='real' and isinstance(snapshot.get('funds'),list)
    except (OSError,ValueError,KeyError):
        return False


def archive_app(installed, destination):
    subprocess.run(['/usr/bin/ditto','-c','-k','--sequesterRsrc','--keepParent',str(installed),str(destination)],check=True)
    with zipfile.ZipFile(destination,metadata_encoding='utf-8') as z:
        if z.testzip() is not None or z.read(installed.name+'/Contents/MacOS/Shixu')!=(installed/'Contents/MacOS/Shixu').read_bytes():
            raise RuntimeError('旧版归档校验未通过，原应用保持')


def install(candidate, installed=None, root=ROOT):
    candidate=candidate.resolve();installed=(installed or Path.home()/'Applications/FundDesk.app').resolve()
    info=validate_candidate(candidate,installed,root)
    version=info['CFBundleShortVersionString']
    runtime=root/'data/runtime';runtime.mkdir(parents=True,exist_ok=True)
    lock=runtime/'install.lock'
    fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    os.write(fd,str(os.getpid()).encode());os.close(fd)
    stage=installed.parent/('.FundDesk-update-'+uuid.uuid4().hex+'.app')
    previous=installed.parent/('.FundDesk-previous-'+uuid.uuid4().hex+'.app')
    receipt={'version':version,'status':'PREPARING','started_at':datetime.now(timezone.utc).isoformat(),
             'installed_app':str(installed),'previous_version':bundle_info(installed)['CFBundleShortVersionString'] if installed.exists() else None}
    def record():
        temporary=runtime/'install_receipt.tmp'
        temporary.write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n')
        os.replace(temporary,runtime/'install_receipt.json')
    try:
        installed.parent.mkdir(parents=True,exist_ok=True)
        subprocess.run(['/usr/bin/ditto',str(candidate),str(stage)],check=True)
        subprocess.run(['/usr/bin/codesign','--verify','--deep','--strict',str(stage)],check=True)
        # A development service is someone else's active workflow; leave it.
        state_path=runtime/'server.json'
        if state_path.is_file():
            state=json.loads(state_path.read_text())
            if state.get('owner')=='development':
                raise RuntimeError('请先通过项目入口停止开发服务；未替换任何运行文件')
        stop_application(installed)
        receipt['data_before']=ledger_signature(root)
        receipt['backups']=[b for mode in ('real','demo') if (b:=snapshot_database(root,mode))]
        archive=None
        if installed.exists():
            folder=root/'output/builds/archive';folder.mkdir(parents=True,exist_ok=True)
            archive=folder/('FundDesk-installed-before-v'+version+'-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'.zip')
            archive_app(installed,archive)
            receipt['previous_archive']=str(archive)
            receipt['previous_archive_sha256']=hashlib.sha256(archive.read_bytes()).hexdigest()
            os.replace(installed,previous)
        try:
            os.replace(stage,installed)
        except Exception:
            if previous.exists():os.replace(previous,installed)
            raise
        receipt['status']='STARTING';record()
        subprocess.run(['/usr/bin/open','-g',str(installed)],check=True)
        for _ in range(100):
            if probe(root,version):
                receipt['status']='READY';break
            time.sleep(.1)
        else:
            if application_pids(installed):
                # A permission dialog can keep a valid process alive. Do not
                # treat the time spent waiting for the user as a failed binary.
                receipt['status']='WAITING_FOR_STARTUP'
            elif previous.exists() and ledger_signature(root)==receipt['data_before']:
                shutil.rmtree(installed);os.replace(previous,installed)
                subprocess.run(['/usr/bin/open','-g',str(installed)],check=True)
                receipt['status']='ROLLED_BACK'
            else:receipt['status']='NEEDS_ATTENTION'
        receipt['finished_at']=datetime.now(timezone.utc).isoformat();record()
        if previous.exists() and archive and receipt['status'] in ('READY','WAITING_FOR_STARTUP'):
            shutil.rmtree(previous)  # exact, verified archive remains recoverable
        manifest=root/'output/builds'/f'FundDesk-{version}-manifest.json'
        if manifest.is_file():
            m=json.loads(manifest.read_text());m.update(installed_app=str(installed),previous_installed_archive=str(archive) if archive else None)
            manifest.write_text(json.dumps(m,ensure_ascii=False,indent=2)+'\n')
        return receipt
    except Exception as exc:
        receipt.update(status='FAILED',error=type(exc).__name__)
        if previous.exists():
            if not installed.exists():
                os.replace(previous,installed)
                receipt['status']='ROLLED_BACK'
            elif receipt.get('data_before') is not None:
                try:
                    if ledger_signature(root)==receipt['data_before']:
                        stop_application(installed)
                        shutil.rmtree(installed);os.replace(previous,installed)
                        receipt['status']='ROLLED_BACK'
                    else:receipt['status']='NEEDS_ATTENTION'
                except Exception as recovery:
                    receipt.update(status='NEEDS_ATTENTION',recovery_error=type(recovery).__name__)
        record()
        if installed.exists() and receipt.get('previous_version'):
            subprocess.run(['/usr/bin/open','-g',str(installed)],check=False)
        raise
    finally:
        if stage.exists():shutil.rmtree(stage)
        lock.unlink(missing_ok=True)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('candidate',type=Path)
    args=p.parse_args()
    receipt=install(args.candidate)
    print(json.dumps({'version':receipt['version'],'status':receipt['status'],
                      'backup_count':len(receipt.get('backups',[])),'previous_archive':receipt.get('previous_archive')},ensure_ascii=False))
    return 0 if receipt['status'] in ('READY','WAITING_FOR_STARTUP') else 1


if __name__=='__main__':raise SystemExit(main())
