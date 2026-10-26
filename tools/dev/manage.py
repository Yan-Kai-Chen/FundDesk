#!/usr/bin/env python3
"""Own only this workspace's single local server. Standard-library launcher."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
import uuid
import webbrowser

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src/backend"))
from app.runtime import verified_state, probe
from app.version import VERSION
RUNTIME = ROOT / "data/runtime"
RUNTIME.mkdir(parents=True, exist_ok=True)
STATE = RUNTIME / "server.json"


def health(url):
    return probe(url)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["start", "stop", "status"])
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    with (RUNTIME / "controller.lock").open("w") as guard:
        fcntl.flock(guard, fcntl.LOCK_EX)
        old = verified_state(ROOT)
        owned = old is not None
        if args.action == "status":
            print(json.dumps({"running": owned, "url": old["url"] if owned else None,
                              "owner":old.get("owner") if owned else None}, ensure_ascii=False))
            return 0
        if args.action == "stop":
            if not owned:
                print("当前没有可验证的本项目运行实例；没有终止其他进程。")
                return 0
            if old.get("owner") == "desktop":
                print("当前服务由FundDesk Mac 应用管理，请关闭应用窗口或按 Command-Q 退出。", file=sys.stderr)
                return 1
            command = subprocess.check_output(["/bin/ps", "-p", str(old["pid"]), "-o", "command="], text=True)
            if str(ROOT / "src/backend/.venv/bin/python") not in command or "app.run" not in command:
                print("进程身份与本项目不匹配，停止操作已取消。", file=sys.stderr)
                return 1
            os.kill(old["pid"], signal.SIGTERM)
            for _ in range(50):
                if not health(old["url"]):
                    print("FundDesk已停止，记录保存在本机。")
                    return 0
                time.sleep(.1)
            print("关闭尚未完成，请稍后再检查。", file=sys.stderr)
            return 1
        if owned:
            if old.get("live_version") != VERSION:
                print("已有其他版本服务运行，请先退出旧应用或停止旧开发服务。", file=sys.stderr)
                return 1
            print("Local: " + old["url"])
            if not args.no_browser:
                webbrowser.open(old["url"])
            return 0
        python = ROOT / "src/backend/.venv/bin/python"
        if not python.exists() or not (ROOT / "src/frontend/dist/index.html").exists():
            print("运行环境或界面产物缺失。请先按 docs/development/RUNBOOK.md 完成安装/构建。", file=sys.stderr)
            return 1
        port = None
        for candidate in range(8731, 8792):
            try:
                with socket.socket() as s:
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    s.bind(("127.0.0.1", candidate))
                    port = candidate
                    break
            except OSError:
                continue
        if port is None:
            print("8731–8791 端口均被占用，请检查本机服务。", file=sys.stderr)
            return 1
        instance = uuid.uuid4().hex
        env = dict(os.environ, FUNDING_INSTANCE_ID=instance)
        with (RUNTIME / "server.log").open("ab") as log:
            process = subprocess.Popen([str(python), "-m", "app.run", "--port", str(port)], cwd=ROOT / "src/backend",
                                       stdin=subprocess.DEVNULL, stdout=log, stderr=log, env=env, start_new_session=True)
        url = "http://127.0.0.1:" + str(port)
        for _ in range(100):
            result = health(url)
            if result and result.get("instance_id") == instance:
                print("Local: " + url)
                if not args.no_browser:
                    webbrowser.open(url)
                return 0
            if process.poll() is not None:
                break
            time.sleep(.15)
        if process.poll() is None:
            process.terminate()
        print("启动未完成，请查看 data/runtime/server.log。", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
