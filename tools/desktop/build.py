#!/usr/bin/env python3
"""Build a self-contained arm64 Mac app; personal records stay outside it."""
from __future__ import annotations
import argparse
from datetime import datetime
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import platform
import re
import plistlib
import shutil
import subprocess
import sys
import tomllib
import uuid
import zipfile

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "src/desktop"
BUILD = ROOT / "data/runtime/desktop"
OUTPUT = ROOT / "output/builds"
APP_NAME = "FundDesk.app"
LEGACY_APP_NAME = "拾序.app"


def run(args, **kwargs):
    subprocess.run([str(v) for v in args],check=True,**kwargs)


def archive_bundle(app, destination):
    if list(app.rglob('*.sqlite3')):raise SystemExit('Unexpected database inside old app; keep it for manual review.')
    run(['/usr/bin/ditto','-c','-k','--sequesterRsrc','--keepParent',app,destination])
    # ditto stores UTF-8 names without consistently setting the ZIP UTF-8 flag.
    with zipfile.ZipFile(destination, metadata_encoding='utf-8') as z:
        assert z.testzip() is None
        assert z.read(app.name+'/Contents/MacOS/Shixu')==(app/'Contents/MacOS/Shixu').read_bytes()
    shutil.rmtree(app)  # Recoverable from the verified ZIP, without duplicate app registrations.


def notices(destination):
    names = {"fastapi","starlette","pydantic","pydantic-core","uvicorn","httpx","httpcore","anyio",
             "h11","idna","certifi","click","typing-extensions","typing-inspection","annotated-types","annotated-doc"}
    blocks = ["Third-party notices for FundDesk. Python and these libraries retain their respective licenses."]
    for d in sorted(metadata.distributions(),key=lambda d:d.metadata['Name'].lower()):
        if d.metadata['Name'].lower().replace('_','-') not in names:continue
        blocks.append(f"\n## {d.metadata['Name']} {d.version}\nLicense: {d.metadata.get('License-Expression') or d.metadata.get('License','See below')}")
        for f in d.files or []:
            if f.name.lower().startswith(('license','copying','notice')):
                q=Path(d.locate_file(f))
                if q.is_file() and q.stat().st_size<150_000:blocks.append(q.read_text(errors='replace'))
    for name in ['react','react-dom','lucide-react']:
        q=ROOT/'src/frontend/node_modules'/name/'LICENSE'
        if q.exists():blocks.append(f'\n## {name}\n'+q.read_text())
    q=Path(sys.base_prefix)/'LICENSE'
    if q.exists():blocks.append('\n## Python\n'+q.read_text())
    blocks.append('\nPackaged with PyInstaller, GPL with its bootloader exception: https://pyinstaller.org/en/stable/license.html')
    destination.write_text('\n'.join(blocks)+'\n')


def main():
    p=argparse.ArgumentParser();p.add_argument('--install',action='store_true');p.add_argument('--skip-frontend-build',action='store_true')
    p.add_argument('--signing-identity',help='Stable available Keychain identity SHA1; defaults to local ad-hoc')
    args=p.parse_args()
    if platform.system()!='Darwin' or platform.machine()!='arm64':raise SystemExit('Build on an Apple Silicon Mac.')
    config=json.loads((ROOT/'config/app/desktop.json').read_text())
    signing=args.signing_identity or config.get('signing_identity_sha1') or '-'
    if signing!='-':
        identities=subprocess.check_output(['/usr/bin/security','find-identity','-v','-p','codesigning'],text=True)
        if not re.fullmatch(r'[A-Fa-f0-9]{40}',signing) or signing.upper() not in identities.upper():
            raise SystemExit('Configured signing identity is unavailable; no silent fallback or account changes.')
    signing_description='local ad-hoc; no Developer ID or notarization' if signing=='-' else 'configured stable Keychain identity; not notarized'
    version=tomllib.loads((ROOT/'src/backend/pyproject.toml').read_text())['project']['version']
    assert json.loads((ROOT/'src/frontend/package.json').read_text())['version']==version
    import PyInstaller
    assert PyInstaller.__version__=='6.22.3'
    BUILD.mkdir(parents=True,exist_ok=True);OUTPUT.mkdir(parents=True,exist_ok=True)
    env=dict(os.environ,CLANG_MODULE_CACHE_PATH=str(ROOT/'data/cache/swift'))
    if not args.skip_frontend_build:
        pnpm=shutil.which('pnpm')
        if not pnpm:raise SystemExit('Add the documented Node and pnpm runtime to PATH for building.')
        run([pnpm,'build'],cwd=ROOT/'src/frontend',env=env)
    run([sys.executable,'-m','PyInstaller','--noconfirm','--clean','--distpath',BUILD/'dist',
         '--workpath',BUILD/'pyinstaller',SOURCE/'backend.spec'],cwd=ROOT,env=env)
    stage=BUILD/('stage-'+uuid.uuid4().hex)/APP_NAME
    macos=stage/'Contents/MacOS';resources=stage/'Contents/Resources'
    macos.mkdir(parents=True);resources.mkdir(parents=True)
    run(['xcrun','swiftc','-O','-swift-version','5','-target','arm64-apple-macos13.0',
         '-module-cache-path',ROOT/'data/cache/swift',SOURCE/'DesktopCore.swift',SOURCE/'Shixu.swift',
         '-framework','AppKit','-framework','WebKit','-o',macos/'Shixu'],env=env)
    run(['xcrun','swiftc','-O','-module-cache-path',ROOT/'data/cache/swift',SOURCE/'IconMaker.swift','-o',BUILD/'IconMaker'],env=env)
    iconset=BUILD/'AppIcon.iconset'
    run([BUILD/'IconMaker',iconset]);run(['/usr/bin/iconutil','-c','icns',iconset,'-o',resources/'AppIcon.icns'])
    shutil.copytree(BUILD/'dist/backend',resources/'backend',symlinks=True)
    for name in ['config/methods.json','contracts/input.schema.json',
                 'examples/synthetic_snapshot.json.gz','examples/synthetic_expected.json.gz','provenance.json']:
        assert (resources/'backend/_internal/app/research_methods'/name).is_file(), 'Research bundle asset missing: '+name
    payload=resources/'payload'
    shutil.copytree(ROOT/'src/frontend/dist',payload/'src/frontend/dist')
    (payload/'input/templates').mkdir(parents=True)
    for name in ['funds.csv','nav_history.csv','lots.csv','orders.csv','confirmations.csv','cash_events.csv','fund_import.json','gold_import.json']:
        shutil.copy2(ROOT/'input/templates'/name,payload/'input/templates'/name)
    notices(resources/'ThirdPartyNotices.txt')
    info={'CFBundleIdentifier':config['bundle_identifier'],'CFBundleName':config['product_name'],'CFBundleDisplayName':config['product_name'],
          'CFBundleExecutable':'Shixu','CFBundlePackageType':'APPL','CFBundleShortVersionString':version,
          'CFBundleVersion':'1','CFBundleIconFile':'AppIcon','LSMinimumSystemVersion':'13.0',
          'LSMultipleInstancesProhibited':True,'NSHighResolutionCapable':True,'NSPrincipalClass':'NSApplication',
          'NSAppTransportSecurity':{'NSAllowsLocalNetworking':True},
          'ShixuWorkspaceID':config['workspace_id'],'ShixuWorkspacePath':str(ROOT),'FundDeskSigningMode':signing_description}
    with (stage/'Contents/Info.plist').open('wb') as f:plistlib.dump(info,f,sort_keys=True)
    run(['/usr/bin/codesign','--force','--deep','--sign',signing,stage])
    run(['/usr/bin/codesign','--verify','--deep','--strict',stage])
    assert not list(stage.rglob('*.sqlite3')) and not list(stage.rglob('*.jpg'))
    target=OUTPUT/APP_NAME;archive=OUTPUT/'archive';archive.mkdir(exist_ok=True)
    stamp=datetime.now().strftime('%Y%m%d-%H%M%S')
    for name in [APP_NAME,LEGACY_APP_NAME]:
        previous=OUTPUT/name
        if previous.exists():
            old=plistlib.loads((previous/'Contents/Info.plist').read_bytes())
            if old.get('CFBundleIdentifier')!=config['bundle_identifier']:raise SystemExit('Unexpected bundle in builds; kept for review.')
            archive_bundle(previous,archive/f'{previous.stem}-before-{stamp}.zip')
    stage.rename(target)
    zip_=OUTPUT/f'FundDesk-{version}-macOS-arm64.zip'
    run(['/usr/bin/ditto','-c','-k','--sequesterRsrc','--keepParent',target,zip_])
    installed=None
    manifest={'version':version,'architecture':'arm64','build_host':platform.mac_ver()[0],
        'app':str(target),'installed_app':str(installed) if installed else None,'archive':str(zip_),
        'zip_sha256':hashlib.sha256(zip_.read_bytes()).hexdigest(),'zip_bytes':zip_.stat().st_size,
        'signing':signing_description,'contains_personal_database':False,
        'data_location':'existing workspace/data; no migration or bundled personal records','pyinstaller':PyInstaller.__version__}
    (OUTPUT/f'FundDesk-{version}-manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    if args.install:
        run([sys.executable,ROOT/'tools/desktop/install.py',target])
    print(json.dumps(manifest,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
