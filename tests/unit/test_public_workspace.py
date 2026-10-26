"""Privacy boundaries of a fresh public clone, using temporary synthetic files."""
import importlib.util
import json
from pathlib import Path
import stat

import pytest

ROOT = Path(__file__).resolve().parents[2]


def load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def workspace(tmp_path):
    root = tmp_path
    config = root / 'config/app'
    config.mkdir(parents=True)
    (config / 'desktop.example.json').write_text(json.dumps({'product_name': 'FundDesk'}))
    return root


def test_new_clone_generates_private_independent_identity_and_preserves_existing(tmp_path):
    module = load('public_setup', 'tools/dev/setup_workspace.py')
    a = workspace(tmp_path / 'a')
    b = workspace(tmp_path / 'b')
    assert module.setup(a) and module.setup(b)
    pa, pb = (root / 'config/app/desktop.json' for root in (a, b))
    assert json.loads(pa.read_text())['workspace_id'] != json.loads(pb.read_text())['workspace_id']
    assert stat.S_IMODE(pa.stat().st_mode) == 0o600
    before = pa.read_bytes()
    assert module.setup(a) is False and pa.read_bytes() == before
    assert not list(a.rglob('*.sqlite3'))


def test_invalid_existing_identity_is_preserved(tmp_path):
    module = load('public_setup_invalid', 'tools/dev/setup_workspace.py')
    root = workspace(tmp_path)
    path = root / 'config/app/desktop.json'
    path.write_text('{"workspace_id":"invalid"}')
    before = path.read_bytes()
    with pytest.raises(ValueError):
        module.setup(root)
    assert path.read_bytes() == before


@pytest.mark.parametrize('rel', ['data/real/account.json', 'input/inbox/statement.csv',
                                'config/app/desktop.json', 'src/backend/.env',
                                'src/backend/backup.zip'])
def test_public_check_rejects_private_payload_paths(tmp_path, rel):
    audit = load('public_boundary', 'tools/release/check_public_tree.py')
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('{}')
    result = audit.audit(tmp_path)
    assert not result['passed'] and result['findings'][0]['path'] == rel


def test_public_check_rejects_symlinks_without_reading_target(tmp_path):
    audit = load('public_link_boundary', 'tools/release/check_public_tree.py')
    (tmp_path / 'README.md').symlink_to('/etc/hosts')
    assert audit.audit(tmp_path)['findings'] == [{'path': 'README.md', 'rule': 'symlink'}]
