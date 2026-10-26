from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, copy_metadata

desktop = Path(SPECPATH)
backend = desktop.parent / "backend"
datas = collect_data_files("certifi")
# This local package is not on the build interpreter's import path yet.
# Explicit data paths keep the standalone bundle independent of PYTHONPATH.
research = backend / "app/research_methods"
for pattern in ["contracts/*.json", "config/*.json", "examples/*.gz", "provenance.json"]:
    datas += [(str(path), str(Path("app/research_methods") / path.parent.relative_to(research)))
              for path in research.glob(pattern)]
for package in ["fastapi", "starlette", "pydantic", "uvicorn", "httpx", "anyio"]:
    datas += copy_metadata(package)
a = Analysis([str(backend / "app/desktop_entry.py")], pathex=[str(backend)], datas=datas,
    hiddenimports=["uvicorn.loops.asyncio", "uvicorn.protocols.http.h11_impl", "uvicorn.lifespan.on"],
    excludes=["pytest", "coverage", "tkinter", "_tkinter", "unittest"], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="FundingBackend", console=True,
    strip=False, upx=False, target_arch="arm64", codesign_identity=None)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="backend")
