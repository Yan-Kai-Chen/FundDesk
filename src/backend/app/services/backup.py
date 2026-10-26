from __future__ import annotations

from pathlib import Path
import hashlib
import json
import os
import re
import shutil
import sqlite3
import uuid
import zipfile

from app.domain.finance import require
from app.domain.ledger import project
from app.persistence.store import Store, pack, utcnow, MIGRATIONS


class BackupMixin:
    def backups(self):
        folder = self.root / "data/backups" / self.mode
        folder.mkdir(parents=True, exist_ok=True)
        return sorted((json.loads(p.read_text()) for p in folder.glob("*/manifest.json")),
                      key=lambda r:r['created_at'], reverse=True)

    def backup_path(self, id):
        require(bool(re.fullmatch(r"backup_[0-9a-f]{20}", id)), "INVALID_BACKUP", "备份编号无效")
        return self.root / "data/backups" / self.mode / id

    def create_backup(self, *, automatic=False, reason='MANUAL'):
        id = "backup_" + uuid.uuid4().hex[:20]
        folder = self.backup_path(id)
        folder.mkdir(parents=True)
        db = folder / "workbench.sqlite3"
        with self.store.connect() as source:
            target = sqlite3.connect(db)
            try:
                source.backup(target)
            finally:
                target.close()
        meta = {"id": id, "mode": self.mode, "created_at": utcnow(), "schema_version": len(MIGRATIONS),
                "sha256": hashlib.sha256(db.read_bytes()).hexdigest(), "size": db.stat().st_size,
                "automatic": automatic, "reason": reason,
                "notice": "本机账本、行情、规则、建议、预测及截图转录资料；不含原始图片及外部密钥。原图请另存，请作为个人数据保管。"}
        (folder / "manifest.json").write_text(pack(meta), encoding="utf-8")
        with zipfile.ZipFile(folder / "backup.zip", "w", zipfile.ZIP_DEFLATED) as archive:
            archive.write(db, "workbench.sqlite3")
            archive.write(folder / "manifest.json", "manifest.json")
        return meta

    def preview_restore(self, id):
        folder = self.backup_path(id)
        require((folder / "manifest.json").is_file(), "BACKUP_NOT_FOUND", "找不到此模式的备份")
        manifest = json.loads((folder / "manifest.json").read_text())
        db = folder / "workbench.sqlite3"
        require(manifest["mode"] == self.mode and hashlib.sha256(db.read_bytes()).hexdigest() == manifest["sha256"],
                "BACKUP_CHECKSUM", "备份模式或校验和不一致")
        with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as c:
            c.row_factory = sqlite3.Row
            require(c.execute("PRAGMA integrity_check").fetchone()[0] == "ok", "BACKUP_INTEGRITY", "备份数据库完整性校验失败")
            version = c.execute("PRAGMA user_version").fetchone()[0]
            require(version == len(MIGRATIONS), "BACKUP_SCHEMA", "此版本恢复仅接受相同数据库结构版本")
            require(c.execute("SELECT value FROM meta WHERE key='mode'").fetchone()[0] == self.mode, "MODE_MISMATCH", "禁止跨真实/演示模式恢复")
            ledger = project(Store.events(c))
            counts = {table: c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in ["events", "observations", "predictions", "plans"]}
        return {"manifest": manifest, "counts": counts, "revision": ledger["revision"], "cash": ledger["cash"], "valid": True}

    def restore(self, id, checksum, confirm):
        require(confirm is True, "RESTORE_CONFIRMATION", "请先预览并明确确认恢复此备份")
        preview = self.preview_restore(id)
        require(checksum == preview["manifest"]["sha256"], "BACKUP_CHECKSUM", "恢复内容与预览不一致")
        safety = self.create_backup()
        path = self.store.path
        temporary = path.with_suffix(".restore.tmp")
        shutil.copy2(self.backup_path(id) / "workbench.sqlite3", temporary)
        with self.store.connect() as c:
            c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        for suffix in ["-wal", "-shm"]:
            path.with_name(path.name + suffix).unlink(missing_ok=True)
        os.replace(temporary, path)
        self.store = Store(path, self.mode)
        self.snapshot()
        return {"restored": id, "safety_backup": safety["id"], "revision": preview["revision"]}
