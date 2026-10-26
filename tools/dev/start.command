#!/bin/zsh
project_root="$(cd "$(dirname "$0")/../.." && pwd)"
/usr/bin/python3 "$project_root/tools/dev/manage.py" start
if [[ $? -ne 0 ]]; then
  read -k 1 "?启动失败，按任意键关闭窗口。"
fi
