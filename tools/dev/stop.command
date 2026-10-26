#!/bin/zsh
project_root="$(cd "$(dirname "$0")/../.." && pwd)"
/usr/bin/python3 "$project_root/tools/dev/manage.py" stop
