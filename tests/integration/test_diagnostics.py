from pathlib import Path
import importlib.util
from app.persistence.store import Store


def test_diagnostics_detects_corrupt_database_without_exporting_values(tmp_path):
    source=Path(__file__).resolve().parents[2]/'tools/quality/diagnose.py'
    spec=importlib.util.spec_from_file_location('funding_diagnostics',source)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    path=tmp_path/'data/real/workbench.sqlite3'
    Store(path,'real')
    assert module.diagnose(tmp_path)['healthy']
    path.write_bytes(b'broken synthetic database')
    result=module.diagnose(tmp_path)
    assert not result['healthy'] and result['databases']['real']['status']=='error'
    assert not result['includes_personal_ledger_values']
