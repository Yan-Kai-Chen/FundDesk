"""One local service per workspace, shared by development and the Mac app."""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import threading
import signal
from contextlib import contextmanager
from urllib.parse import urlsplit
from urllib.request import build_opener, ProxyHandler

from app.version import VERSION


def workspace_id(root: Path) -> str:
    config = root / "config/app/desktop.json"
    if config.is_file():
        identity = json.loads(config.read_text())["workspace_id"]
        if not isinstance(identity, str) or not re.fullmatch(r"[a-f0-9]{32}", identity):
            raise ValueError("Invalid workspace identity")
        return identity
    return hashlib.sha256(str(root.resolve()).encode()).hexdigest()[:32]


def local_url(value) -> bool:
    try:
        p = urlsplit(value)
        return (p.scheme == "http" and p.hostname == "127.0.0.1" and p.port is not None
                and 1024 <= p.port <= 65535 and not p.username and not p.password
                and p.path in {"", "/"} and not p.query and not p.fragment)
    except (ValueError, TypeError, AttributeError):
        return False


def probe(url):
    if not local_url(url):
        return None
    try:
        with build_opener(ProxyHandler({})).open(url.rstrip("/") + "/api/v1/health", timeout=1) as r:
            return json.load(r)
    except Exception:
        return None


def read_state(root):
    try:
        value = json.loads((root / "data/runtime/server.json").read_text())
        if (not isinstance(value, dict) or not local_url(value.get("url"))
                or value.get("root") != str(root.resolve()) or type(value.get("pid")) is not int
                or value["pid"] <= 0 or not re.fullmatch(r"[a-f0-9]{32}", str(value.get("instance_id", "")))):
            return None
        return value
    except (OSError, ValueError):
        return None


def verified_state(root):
    state = read_state(root)
    live = probe(state["url"]) if state else None
    if (not live or live.get("app") != "funding-workbench"
            or live.get("instance_id") != state["instance_id"]
            or live.get("workspace_id") != workspace_id(root)):
        return None
    return dict(state, live_version=live.get("version"))


def write_private_json(path: Path, value):
    temporary = path.with_name(path.name + ".tmp-" + str(os.getpid()))
    try:
        with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as f:
            json.dump(value, f, ensure_ascii=False, indent=2)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def serve(root: Path, resources: Path, port: int, instance: str, owner="development", parent_pid=0):
    import uvicorn
    from app.main import create_app

    root, resources = root.resolve(), resources.resolve()
    runtime = root / "data/runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    state_file = runtime / "server.json"
    if not re.fullmatch(r"[a-f0-9]{32}", instance):
        raise ValueError("Invalid instance identity")
    with (runtime / "backend.lock").open("a") as guard:
        try:
            fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("This workspace already has a running backend; no second writer started")
        old = read_state(root)
        live = probe(old["url"]) if old else None
        if live and live.get("app") == "funding-workbench" and live.get("instance_id") == old["instance_id"]:
            raise RuntimeError("An existing workspace service is still alive; close it before starting another version")
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if port:
                sock.bind(("127.0.0.1", port))
            else:
                try:
                    hint = json.loads((runtime / "preferred-port.json").read_text())["port"]
                except (OSError, ValueError, KeyError, TypeError):
                    hint = 8731
                candidates = [hint] if type(hint) is int and 1024 <= hint <= 65535 else []
                for candidate in dict.fromkeys(candidates + list(range(8731, 8792)) + [0]):
                    try:
                        sock.bind(("127.0.0.1", candidate))
                        break
                    except OSError:
                        continue
                else:
                    raise RuntimeError("No loopback port available")
            selected_port = sock.getsockname()[1]
            if owner == "desktop":
                write_private_json(runtime / "preferred-port.json", {"port":selected_port})
            os.environ["FUNDING_INSTANCE_ID"] = instance
            os.environ["FUNDING_OWNER"] = owner
            app = create_app(root, resources=resources)
            config = uvicorn.Config(app, host="127.0.0.1", port=selected_port, workers=1,
                access_log=False, log_level="warning", loop="asyncio", http="h11", ws="none", lifespan="on")
            class ManagedServer(uvicorn.Server):
                @contextmanager
                def capture_signals(self):
                    # Uvicorn normally re-raises captured signals after shutdown,
                    # which would skip this owner's state-file cleanup. Restore
                    # the original handlers after a normal, graceful return.
                    previous={sig:signal.signal(sig,self.handle_exit) for sig in (signal.SIGINT,signal.SIGTERM)}
                    try:yield
                    finally:
                        for sig,handler in previous.items():signal.signal(sig,handler)
            server = ManagedServer(config)
            stopped = threading.Event()

            def watch_parent():
                while not stopped.wait(.5):
                    if os.getppid() != parent_pid:
                        server.should_exit = True
                        return

            state = {"pid":os.getpid(),"url":f"http://127.0.0.1:{selected_port}","instance_id":instance,
                     "root":str(root),"workspace_id":workspace_id(root),"owner":owner,"version":VERSION}
            write_private_json(state_file, state)
            if parent_pid:
                threading.Thread(target=watch_parent, daemon=True, name="desktop-parent").start()
            try:
                server.run(sockets=[sock])
            finally:
                stopped.set()
                current = read_state(root)
                if current and current["instance_id"] == instance:
                    state_file.unlink(missing_ok=True)
