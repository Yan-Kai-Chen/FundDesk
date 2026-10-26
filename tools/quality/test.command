#!/bin/zsh
set -euo pipefail
project_root="$(cd "$(dirname "$0")/../.." && pwd)"
mkdir -p "$project_root/output/reports"
cd "$project_root/src/backend"
"$project_root/src/backend/.venv/bin/python" -m pytest -c pyproject.toml "$project_root/tests" --basetemp="$project_root/data/runtime/pytest" --junitxml="$project_root/output/reports/pytest.xml"
cd "$project_root/src/frontend"
pnpm test
pnpm build
