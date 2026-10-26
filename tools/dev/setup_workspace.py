#!/usr/bin/env python3
"""Create a private identity for this clone; preserve existing workspaces."""
import json
from pathlib import Path
import re
import uuid


def setup(root):
    root = Path(root).resolve()
    directory = root / 'config/app'
    path = directory / 'desktop.json'
    if path.is_symlink():
        raise ValueError('Workspace configuration must be a regular local file')
    if path.exists():
        existing = json.loads(path.read_text())
        if not re.fullmatch(r'[a-f0-9]{32}', str(existing.get('workspace_id', ''))):
            raise ValueError('Existing workspace identity is invalid; preserve it for review')
        return False
    template = json.loads((directory / 'desktop.example.json').read_text())
    template['workspace_id'] = uuid.uuid4().hex
    # Exclusive creation never overwrites another setup process or an existing identity.
    import os
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as f:
        json.dump(template, f, ensure_ascii=False, indent=2)
        f.write('\n')
    for name in ('data/runtime', 'data/cache', 'output/builds', 'output/reports', 'input/inbox'):
        (root / name).mkdir(parents=True, exist_ok=True)
    return True


if __name__ == '__main__':
    created = setup(Path(__file__).resolve().parents[2])
    print('Workspace initialized.' if created else 'Existing workspace identity preserved.')
