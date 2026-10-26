import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import uuid

from fastapi.testclient import TestClient
import pytest

from app.main import create_app
from app.runtime import local_url, workspace_id, read_state, verified_state, probe, write_private_json

BACKEND=Path(__file__).resolve().parents[2]/'src/backend'


def wait_ready(root,process):
    until=time.monotonic()+10
    while time.monotonic()<until:
        value=verified_state(root)
        if value:return value
        assert process.poll() is None,'Runtime exited before health check'
        time.sleep(.05)
    raise AssertionError('Runtime did not become ready')


def start(root,resources,parent=0):
    return subprocess.Popen([sys.executable,'-m','app.desktop_entry','--root',str(root),'--resources',str(resources),
        '--instance',uuid.uuid4().hex,'--parent-pid',str(parent)],cwd=BACKEND,
        stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)


@pytest.mark.parametrize('url',['https://127.0.0.1:8731','http://localhost:8731','http://evil.example:8731',
    'http://' + 'user:pass' + '@127.0.0.1:8731','http://127.0.0.1:80','http://127.0.0.1:8731/?secret=x',
    'file:///etc/passwd','http://127.0.0.1:99999',None])
def test_service_discovery_never_probes_untrusted_urls(url):
    assert not local_url(url) and probe(url) is None


def test_workspace_identity_and_private_state_validation(tmp_path):
    assert workspace_id(tmp_path)==workspace_id(tmp_path)
    config=tmp_path/'config/app/desktop.json';config.parent.mkdir(parents=True)
    identity=uuid.uuid4().hex;config.write_text(json.dumps({'workspace_id':identity}))
    assert workspace_id(tmp_path)==identity
    state_file=tmp_path/'data/runtime/server.json';state_file.parent.mkdir(parents=True)
    write_private_json(state_file,{'root':str(tmp_path.resolve()),'pid':123,'url':'http://127.0.0.1:8731','instance_id':uuid.uuid4().hex})
    assert state_file.stat().st_mode & 0o777==0o600
    assert read_state(tmp_path)['pid']==123
    state_file.write_text('{invalid')
    assert read_state(tmp_path) is None
    config.write_text(json.dumps({'workspace_id':'not-a-valid-identity'}))
    with pytest.raises(ValueError):workspace_id(tmp_path)


def test_packaged_resources_are_separate_from_writable_data(tmp_path):
    root=tmp_path/'workspace';resources=tmp_path/'readonly-resources'
    (resources/'src/frontend/dist/assets').mkdir(parents=True)
    (resources/'src/frontend/dist/index.html').write_text('<html>packaged-ui</html>')
    (resources/'src/frontend/dist/assets/app.js').write_text('console.log("packaged");')
    (resources/'input/templates').mkdir(parents=True)
    (resources/'input/templates/funds.csv').write_text('fund_code,name\n')
    app=create_app(root,testing=True,resources=resources)
    with TestClient(app) as c:
        assert c.get('/').text=='<html>packaged-ui</html>'
        csrf=c.get('/api/v1/session').json()['csrf_token'];c.headers['x-csrf-token']=csrf
        assert c.get('/assets/app.js').status_code==200
        assert c.get('/api/v1/templates/funds').text=='fund_code,name\n'
        assert c.get('/api/v1/state?mode=real').json()['revision']==0
    assert (root/'data/real/workbench.sqlite3').is_file()
    assert not (resources/'data').exists()


def test_runtime_fallback_port_single_writer_and_graceful_cleanup(tmp_path):
    root=tmp_path/'workspace';resources=tmp_path/'resources';resources.mkdir()
    (root/'data/runtime').mkdir(parents=True)
    with socket.socket() as occupied:
        occupied.bind(('127.0.0.1',0));port=occupied.getsockname()[1]
        write_private_json(root/'data/runtime/preferred-port.json',{'port':port})
        p=start(root,resources)
        try:
            state=wait_ready(root,p)
            assert not state['url'].endswith(':'+str(port))
            assert state['pid']==p.pid and state['owner']=='desktop'
            duplicate=start(root,resources)
            assert duplicate.wait(timeout=5)!=0
            assert verified_state(root)['pid']==p.pid
        finally:
            p.terminate();p.wait(timeout=8)
    assert not (root/'data/runtime/server.json').exists()
    assert probe(state['url']) is None


def test_desktop_backend_exits_if_native_parent_disappears(tmp_path):
    root=tmp_path/'workspace';resources=tmp_path/'resources';resources.mkdir()
    helper='''import subprocess,sys,os,time,json
args=json.loads(sys.argv[1]);args += ["--parent-pid",str(os.getpid())]
p=subprocess.Popen(args,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
print(p.pid,flush=True)
time.sleep(30)
'''
    args=[sys.executable,'-m','app.desktop_entry','--root',str(root),'--resources',str(resources),'--instance',uuid.uuid4().hex]
    parent=subprocess.Popen([sys.executable,'-c',helper,json.dumps(args)],cwd=BACKEND,stdout=subprocess.PIPE,text=True)
    try:
        child_pid=int(parent.stdout.readline().strip())
        state=wait_ready(root,parent)
        assert state['pid']==child_pid
        parent.terminate();parent.wait(timeout=3)
        until=time.monotonic()+8
        while (root/'data/runtime/server.json').exists() and time.monotonic()<until:time.sleep(.05)
        assert not (root/'data/runtime/server.json').exists() and probe(state['url']) is None
    finally:
        if parent.poll() is None:parent.terminate();parent.wait(timeout=3)
