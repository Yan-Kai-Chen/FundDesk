#!/usr/bin/env python3
"""Check a public source tree without printing matched sensitive content."""
from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path
import re
import subprocess

ROOT_FILES = {'.gitignore', '.gitattributes', 'README.md', 'SECURITY.md',
              'CONTRIBUTING.md', 'THIRD_PARTY_NOTICES.md', 'LICENSE'}
ROOT_DIRS = {'.github', 'src', 'tests', 'tools', 'config', 'input', 'data', 'docs'}
TEXT_SUFFIXES = {'.py', '.ts', '.tsx', '.css', '.swift', '.mjs', '.json', '.toml',
                 '.lock', '.yaml', '.yml', '.html', '.md', '.txt', '.csv', '.command', '.spec'}
SYNTHETIC_GZIP = {
    'src/backend/app/research_methods/examples/synthetic_snapshot.json.gz',
    'src/backend/app/research_methods/examples/synthetic_expected.json.gz',
}
PRIVATE_SUFFIXES = {'.db', '.sqlite', '.sqlite3', '.zip', '.tar', '.tgz', '.7z', '.pdf',
                    '.png', '.jpg', '.jpeg', '.heic', '.p12', '.pfx', '.key', '.pem', '.log'}
CONTENT_RULES = {
    'absolute_home_path': re.compile(r'/(?:Users|home)/[A-Za-z0-9_.-]+/'),
    'windows_home_path': re.compile(r'[A-Za-z]:\\Users\\[A-Za-z0-9_.-]+\\'),
    'private_key': re.compile(r'-----BEGIN (?:[A-Z0-9]+ )?PRIVATE KEY-----'),
    'github_token': re.compile(r'\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{50,})\b'),
    'aws_access_key': re.compile(r'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b'),
    'url_credentials': re.compile(r'https?://[^\s/\"\x27]+:[^\s/@\"\x27]+@'),
    'personal_email': re.compile(r'\b[A-Za-z0-9._%+-]+@(?!(?:example\.(?:com|org)|example\.invalid)\b)[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b'),
}


def files(root, tracked=False):
    if tracked:
        result = subprocess.run(['git', '-C', str(root), 'ls-files', '-z'],
                                check=True, capture_output=True)
        return [root / p.decode('utf-8') for p in result.stdout.split(b'\0') if p]
    return sorted(p for p in root.rglob('*') if '.git' not in p.relative_to(root).parts
                  and (p.is_file() or p.is_symlink()))


def audit(root, tracked=False):
    root = root.resolve()
    findings, total, size = [], 0, 0
    def fail(path, rule):
        findings.append({'path': path, 'rule': rule})
    for p in files(root, tracked):
        rel = p.relative_to(root).as_posix()
        parts = p.relative_to(root).parts
        total += 1
        if p.is_symlink():
            fail(rel, 'symlink'); continue
        if not p.is_file():
            fail(rel, 'missing_tracked_file'); continue
        size += p.stat().st_size
        if len(parts) == 1 and rel not in ROOT_FILES:
            fail(rel, 'unexpected_root_file')
        if len(parts) > 1 and parts[0] not in ROOT_DIRS:
            fail(rel, 'unexpected_root_directory')
        if any(x in {'__pycache__', 'node_modules', '.venv', 'dist', '.pytest_cache',
                     'memory', 'inbox', 'staging', 'backups', 'runtime', 'cache'} for x in parts):
            fail(rel, 'private_or_generated_directory')
        if parts[0] == 'data' and rel not in {'data/.gitignore', 'data/README.md'}:
            fail(rel, 'data_payload')
        if parts[0] == 'input' and len(parts) > 1 and parts[1] != 'templates' and rel not in {'input/.gitignore', 'input/README.md'}:
            fail(rel, 'non_template_input')
        if parts[0] == 'config' and rel not in {'config/.gitignore', 'config/app/desktop.example.json'}:
            fail(rel, 'local_configuration')
        if p.name.startswith('.env') and p.name != '.env.example':
            fail(rel, 'environment_file')
        if p.suffix.lower() in PRIVATE_SUFFIXES or '.sqlite' in p.name or p.name == '.DS_Store':
            fail(rel, 'private_or_binary_file')
        if p.stat().st_size > 2_000_000:
            fail(rel, 'unexpected_large_file'); continue
        data = p.read_bytes()
        if p.suffix == '.gz':
            if rel not in SYNTHETIC_GZIP:
                fail(rel, 'unapproved_compressed_file'); continue
            try:
                # Read at most 2 MiB, including when checking an untrusted archive.
                with gzip.open(p, 'rb') as stream:
                    data = stream.read(2_000_001)
                if len(data) > 2_000_000:
                    fail(rel, 'oversized_compressed_content'); continue
                if json.loads(data).get('mode') != 'SYNTHETIC':
                    fail(rel, 'non_synthetic_example')
            except (OSError, ValueError, EOFError):
                fail(rel, 'invalid_compressed_example'); continue
        try:
            text = data.decode('utf-8')
        except UnicodeError:
            fail(rel, 'unapproved_binary'); continue
        for rule, pattern in CONTENT_RULES.items():
            if pattern.search(text):
                fail(rel, rule)
    return {'passed': not findings, 'files': total, 'bytes': size, 'findings': findings,
            'scope': 'tracked_files' if tracked else 'complete_tree',
            'limitation': 'Pattern and structure checks complement manual review; they do not prove absence of every kind of personal information.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument('--tracked', action='store_true')
    args = parser.parse_args()
    result = audit(args.root, args.tracked)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
