"""Installer checks use only synthetic apps/SQLite under pytest's temp root."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import plistlib
import shutil
import sqlite3
import subprocess
import zipfile

import pytest


@pytest.fixture
def installer():
    path = Path(__file__).resolve().parents[2] / "tools/desktop/install.py"
    spec = importlib.util.spec_from_file_location("funddesk_install_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def database(root, mode="real"):
    path = root / "data" / mode / "workbench.sqlite3"
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA user_version=23")
        connection.execute("CREATE TABLE events (seq INTEGER PRIMARY KEY, payload TEXT)")
        connection.execute("INSERT INTO events VALUES (1, 'synthetic first event')")
    return path


def bundle(path, version, workspace="test-workspace", identity="local.synthetic.funddesk"):
    executable = path / "Contents/MacOS/Shixu"
    backend = path / "Contents/Resources/backend/FundingBackend"
    executable.parent.mkdir(parents=True)
    backend.parent.mkdir(parents=True)
    executable.write_bytes(("synthetic app " + version).encode())
    backend.write_bytes(b"synthetic backend")
    (path / "Contents/Info.plist").write_bytes(plistlib.dumps({
        "CFBundleIdentifier": identity, "ShixuWorkspaceID": workspace, "CFBundleShortVersionString": version}))
    return path


@pytest.fixture
def sandbox(tmp_path, installer, monkeypatch):
    root = tmp_path / "workspace"
    config = root / "config/app/desktop.json"
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps({"bundle_identifier": "local.synthetic.funddesk", "workspace_id": "test-workspace"}))
    candidate = bundle(root / "output/builds/FundDesk.app", "2.0")
    installed = bundle(root / "Applications/FundDesk.app", "1.0")
    database(root)
    opened, stopped, commands = [], [], []

    def mac_run(command, **kwargs):
        command = [str(value) for value in command]
        commands.append(command)
        if command[0] == "/usr/bin/ditto":
            if "-c" in command:
                source, destination = Path(command[-2]), Path(command[-1])
                with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
                    for file in source.rglob("*"):
                        if file.is_file(): archive.write(file, str(Path(source.name) / file.relative_to(source)))
            else:
                shutil.copytree(command[-2], command[-1])
        elif command[0] == "/usr/bin/open":
            opened.append(installer.bundle_info(Path(command[-1]))["CFBundleShortVersionString"])
        elif command[0] != "/usr/bin/codesign":
            raise AssertionError("Unexpected macOS command in isolated test: " + command[0])
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(installer.subprocess, "run", mac_run)
    monkeypatch.setattr(installer, "stop_application", lambda path: stopped.append(Path(path)))
    monkeypatch.setattr(installer, "application_pids", lambda path: [])
    monkeypatch.setattr(installer, "probe", lambda root, version: True)
    monkeypatch.setattr(installer.time, "sleep", lambda seconds: None)
    return {"root": root, "candidate": candidate, "installed": installed, "opened": opened,
            "stopped": stopped, "commands": commands, "mac_run": mac_run}


def receipt(sandbox):
    return json.loads((sandbox["root"] / "data/runtime/install_receipt.json").read_text())


def test_database_snapshot_is_consistent_with_committed_wal_and_excludes_pending_writes(tmp_path, installer):
    root = tmp_path / "isolated"
    source = database(root)
    with sqlite3.connect(source) as writer:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("INSERT INTO events VALUES (2, 'committed WAL event')")
        writer.commit()
        writer.execute("INSERT INTO events VALUES (3, 'not committed')")
        metadata = installer.snapshot_database(root, "real")
        assert writer.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 3
        target = root / "data/backups/real" / metadata["id"] / "workbench.sqlite3"
        with sqlite3.connect(target) as copy:
            assert copy.execute("SELECT seq FROM events ORDER BY seq").fetchall() == [(1,), (2,)]
            assert copy.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert copy.execute("PRAGMA user_version").fetchone()[0] == 23
        assert metadata["sha256"] == hashlib.sha256(target.read_bytes()).hexdigest()
        assert metadata["reason"] == "BEFORE_APP_UPDATE" and metadata["schema_version"] == 23
        with zipfile.ZipFile(target.parent / "backup.zip") as archive:
            assert archive.testzip() is None
            assert archive.read("workbench.sqlite3") == target.read_bytes()
            assert json.loads(archive.read("manifest.json")) == metadata
        writer.rollback()
    assert installer.snapshot_database(root, "demo") is None


@pytest.mark.parametrize("location", ["candidate-workspace", "candidate-identity", "installed-workspace", "private-db"])
def test_validation_rejects_foreign_identity_workspace_or_private_database_without_stopping_app(sandbox, installer, location):
    path = sandbox["candidate"] if location.startswith("candidate") else sandbox["installed"]
    if location == "private-db":
        secret = sandbox["candidate"] / "Contents/Resources/private/workbench.sqlite3"
        secret.parent.mkdir(parents=True)
        with sqlite3.connect(secret) as connection: connection.execute("CREATE TABLE private (value TEXT)")
    else:
        plist = path / "Contents/Info.plist"
        info = plistlib.loads(plist.read_bytes())
        info["CFBundleIdentifier" if location == "candidate-identity" else "ShixuWorkspaceID"] = "foreign-workspace-or-app"
        plist.write_bytes(plistlib.dumps(info))
    before = (sandbox["installed"] / "Contents/MacOS/Shixu").read_bytes()
    with pytest.raises(ValueError): installer.install(sandbox["candidate"], sandbox["installed"], sandbox["root"])
    assert (sandbox["installed"] / "Contents/MacOS/Shixu").read_bytes() == before
    assert not sandbox["opened"] and not sandbox["stopped"] and not sandbox["commands"]


def test_success_replaces_app_with_verified_archive_and_keeps_ledger_unchanged(sandbox, installer):
    before = installer.ledger_signature(sandbox["root"])
    result = installer.install(sandbox["candidate"], sandbox["installed"], sandbox["root"])
    assert result["status"] == "READY" and installer.bundle_info(sandbox["installed"])["CFBundleShortVersionString"] == "2.0"
    assert sandbox["opened"] == ["2.0"] and sandbox["stopped"] == [sandbox["installed"]]
    assert installer.ledger_signature(sandbox["root"]) == before == result["data_before"]
    assert len(result["backups"]) == 1
    archive = Path(result["previous_archive"])
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == result["previous_archive_sha256"]
    with zipfile.ZipFile(archive) as previous:
        assert previous.read("FundDesk.app/Contents/MacOS/Shixu") == b"synthetic app 1.0"
    assert receipt(sandbox)["status"] == "READY"
    assert not list(sandbox["installed"].parent.glob(".FundDesk-*"))
    assert not (sandbox["root"] / "data/runtime/install.lock").exists()


def test_running_app_waiting_for_permission_is_kept_without_rollback(sandbox, installer, monkeypatch):
    monkeypatch.setattr(installer, "probe", lambda root, version: False)
    monkeypatch.setattr(installer, "application_pids", lambda path: [43210])
    result = installer.install(sandbox["candidate"], sandbox["installed"], sandbox["root"])
    assert result["status"] == "WAITING_FOR_STARTUP"
    assert installer.bundle_info(sandbox["installed"])["CFBundleShortVersionString"] == "2.0"
    assert sandbox["opened"] == ["2.0"] and len(sandbox["stopped"]) == 1
    assert Path(result["previous_archive"]).is_file() and not list(sandbox["installed"].parent.glob(".FundDesk-previous-*"))


@pytest.mark.parametrize("change", [None, "ledger", "schema"])
def test_probe_failure_rolls_back_only_when_original_ledger_and_schema_are_unchanged(sandbox, installer, monkeypatch, change):
    def failed_probe(root, version):
        if change:
            with sqlite3.connect(root / "data/real/workbench.sqlite3") as connection:
                if change == "ledger": connection.execute("INSERT OR IGNORE INTO events VALUES (2, 'new event')")
                else: connection.execute("PRAGMA user_version=24")
        return False
    monkeypatch.setattr(installer, "probe", failed_probe)
    result = installer.install(sandbox["candidate"], sandbox["installed"], sandbox["root"])
    assert result["status"] == ("NEEDS_ATTENTION" if change else "ROLLED_BACK")
    expected = "2.0" if change else "1.0"
    assert installer.bundle_info(sandbox["installed"])["CFBundleShortVersionString"] == expected
    assert sandbox["opened"] == (["2.0"] if change else ["2.0", "1.0"])
    assert bool(list(sandbox["installed"].parent.glob(".FundDesk-previous-*"))) == bool(change)


@pytest.mark.parametrize("changed", [False, True])
def test_open_exception_rolls_back_only_if_no_new_financial_events(sandbox, installer, monkeypatch, changed):
    mac_run = sandbox["mac_run"]
    failed = False
    def open_fails_once(command, **kwargs):
        nonlocal failed
        if command[0] == "/usr/bin/open" and not failed:
            failed = True
            if changed:
                with sqlite3.connect(sandbox["root"] / "data/real/workbench.sqlite3") as connection:
                    connection.execute("INSERT INTO events VALUES (2, 'new event before launch error')")
            raise subprocess.CalledProcessError(1, command)
        return mac_run(command, **kwargs)
    monkeypatch.setattr(installer.subprocess, "run", open_fails_once)
    with pytest.raises(subprocess.CalledProcessError):
        installer.install(sandbox["candidate"], sandbox["installed"], sandbox["root"])
    assert receipt(sandbox)["status"] == ("NEEDS_ATTENTION" if changed else "ROLLED_BACK")
    expected = "2.0" if changed else "1.0"
    assert installer.bundle_info(sandbox["installed"])["CFBundleShortVersionString"] == expected
    assert sandbox["opened"] == [expected]
    assert bool(list(sandbox["installed"].parent.glob(".FundDesk-previous-*"))) == changed
    assert not (sandbox["root"] / "data/runtime/install.lock").exists()


def test_active_development_service_is_not_stopped_or_replaced(sandbox, installer):
    runtime = sandbox["root"] / "data/runtime"
    runtime.mkdir(parents=True)
    (runtime / "server.json").write_text(json.dumps({"owner": "development"}))
    with pytest.raises(RuntimeError, match="开发服务"):
        installer.install(sandbox["candidate"], sandbox["installed"], sandbox["root"])
    assert installer.bundle_info(sandbox["installed"])["CFBundleShortVersionString"] == "1.0"
    assert not sandbox["stopped"] and not list(sandbox["installed"].parent.glob(".FundDesk-*"))


def test_pid_selection_matches_only_the_exact_installed_executable(tmp_path, installer, monkeypatch):
    installed = tmp_path / "FundDesk.app"
    executable = str(installed / "Contents/MacOS/Shixu")
    monkeypatch.setattr(installer.subprocess, "check_output", lambda *args, **kwargs:
        f" 123 {executable}\n 124 {executable} --other\n 125 /other/FundDesk.app/Contents/MacOS/Shixu\n 126 /bin/sh\n")
    assert installer.application_pids(installed) == [123]


@pytest.mark.parametrize("url", ["https://127.0.0.1:8731", "http://other-host:8731", "http://127.0.0.1:8731/foreign", "http://" + "user:pass" + "@127.0.0.1:8731"])
def test_probe_never_contacts_a_remote_or_foreign_url(tmp_path, installer, monkeypatch, url):
    runtime = tmp_path / "data/runtime/server.json"
    runtime.parent.mkdir(parents=True)
    runtime.write_text(json.dumps({"url": url, "instance_id": "synthetic-instance"}))
    config = tmp_path / "config/app/desktop.json"
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps({"workspace_id": "synthetic-workspace"}))
    def no_network(*args, **kwargs): raise AssertionError("foreign URL must not be contacted")
    monkeypatch.setattr(installer, "build_opener", no_network)
    assert installer.probe(tmp_path, "2.0") is False


def test_probe_requires_matching_workspace_instance_owner_and_readable_book(tmp_path, installer, monkeypatch):
    runtime = tmp_path / "data/runtime/server.json"
    runtime.parent.mkdir(parents=True)
    runtime.write_text(json.dumps({"url": "http://127.0.0.1:8731", "instance_id": "synthetic-instance"}))
    config = tmp_path / "config/app/desktop.json"
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps({"workspace_id": "synthetic-workspace"}))
    health = {"version": "2.0", "workspace_id": "synthetic-workspace", "instance_id": "synthetic-instance", "owner": "desktop"}
    snapshot = {"mode": "real", "funds": []}
    calls = []
    class Opener:
        def open(self, url, **kwargs):
            calls.append(url)
            value = health if url.endswith("/health") else snapshot if "/state?" in url else {}
            return io.BytesIO(json.dumps(value).encode())
    monkeypatch.setattr(installer, "build_opener", lambda *args: Opener())
    assert installer.probe(tmp_path, "2.0") is True
    for key in ("version", "workspace_id", "instance_id", "owner"):
        old = health[key];health[key] = "foreign"
        calls.clear()
        assert installer.probe(tmp_path, "2.0") is False and len(calls) == 1
        health[key] = old
    snapshot["mode"] = "demo"
    assert installer.probe(tmp_path, "2.0") is False
