from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
import hashlib
import json
import sqlite3

from app.domain.finance import Problem, require


def pack(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      default=lambda x: format(x, "f") if isinstance(x, Decimal) else str(x))


def digest(value):
    return hashlib.sha256(pack(value).encode()).hexdigest()


def utcnow():
    return datetime.now(timezone.utc).isoformat()


MIGRATIONS = [
    ["CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT NOT NULL)",
     "CREATE TABLE documents(kind TEXT NOT NULL,id TEXT NOT NULL,version INTEGER NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(kind,id))",
     "CREATE TABLE events(seq INTEGER PRIMARY KEY AUTOINCREMENT,id TEXT UNIQUE NOT NULL,key TEXT UNIQUE NOT NULL,fingerprint TEXT NOT NULL,kind TEXT NOT NULL,payload TEXT NOT NULL,occurred_at TEXT NOT NULL,recorded_at TEXT NOT NULL)"],
    ["CREATE TABLE observations(id TEXT PRIMARY KEY,fund_id TEXT NOT NULL,value_kind TEXT NOT NULL,effective_date TEXT NOT NULL,as_of TEXT,published_at TEXT,observed_at TEXT NOT NULL,fetched_at TEXT NOT NULL,source TEXT NOT NULL,quality TEXT NOT NULL,payload TEXT NOT NULL,raw_hash TEXT NOT NULL,UNIQUE(fund_id,value_kind,effective_date,raw_hash))",
     "CREATE INDEX observation_lookup ON observations(fund_id,value_kind,effective_date)"],
    ["CREATE TABLE predictions(id TEXT PRIMARY KEY,payload TEXT NOT NULL,created_at TEXT NOT NULL)",
     "CREATE TABLE resolutions(prediction_id TEXT PRIMARY KEY REFERENCES predictions(id),payload TEXT NOT NULL)",
     "CREATE TABLE plans(id TEXT PRIMARY KEY,payload TEXT NOT NULL,created_at TEXT NOT NULL)",
     "CREATE TABLE imports(id TEXT PRIMARY KEY,payload TEXT NOT NULL,created_at TEXT NOT NULL)",
     "CREATE TABLE jobs(job_key TEXT PRIMARY KEY,payload TEXT NOT NULL,updated_at TEXT NOT NULL)"]
]


class Store:
    def __init__(self, path: Path, mode: str):
        self.path, self.mode = Path(path), mode
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as c:
            c.execute("PRAGMA journal_mode=WAL")
            version = c.execute("PRAGMA user_version").fetchone()[0]
            require(version <= len(MIGRATIONS), "SCHEMA_NEWER", "数据库版本高于此应用，请使用匹配的版本")
            for i in range(version, len(MIGRATIONS)):
                c.execute("BEGIN IMMEDIATE")
                try:
                    for statement in MIGRATIONS[i]:
                        c.execute(statement)
                    c.execute(f"PRAGMA user_version={i + 1}")
                    c.commit()
                except Exception:
                    c.rollback()
                    raise
            existing = c.execute("SELECT value FROM meta WHERE key='mode'").fetchone()
            require(not existing or existing[0] == mode, "MODE_MISMATCH", "禁止将演示数据库作为真实数据库打开")
            c.execute("INSERT OR IGNORE INTO meta VALUES('mode',?)", (mode,))
            c.commit()

    @contextmanager
    def connect(self):
        c = sqlite3.connect(self.path, timeout=10)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        c.execute("PRAGMA busy_timeout=10000")
        try:
            yield c
        finally:
            c.close()

    @contextmanager
    def transaction(self):
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            try:
                yield c
                c.commit()
            except Exception:
                c.rollback()
                raise

    @staticmethod
    def docs(c, kind):
        return [dict(json.loads(r["payload"]), _version=r["version"]) for r in
                c.execute("SELECT * FROM documents WHERE kind=? ORDER BY id", (kind,))]

    @staticmethod
    def doc(c, kind, id, default=None):
        r = c.execute("SELECT * FROM documents WHERE kind=? AND id=?", (kind, id)).fetchone()
        return dict(json.loads(r["payload"]), _version=r["version"]) if r else default

    @staticmethod
    def put(c, kind, id, payload, expected=None):
        old = Store.doc(c, kind, id)
        if old:
            require(expected == old["_version"], "VERSION_CONFLICT", "记录已被其他操作修改，请刷新后重试")
        else:
            require(expected in (None, 0), "VERSION_CONFLICT", "记录版本不匹配")
        version = old["_version"] + 1 if old else 1
        clean = {k: v for k, v in payload.items() if k != "_version"}
        c.execute("INSERT INTO documents VALUES(?,?,?,?) ON CONFLICT(kind,id) DO UPDATE SET version=excluded.version,payload=excluded.payload",
                  (kind, id, version, pack(clean)))
        return dict(clean, _version=version)

    @staticmethod
    def events(c):
        return [dict(r, payload=json.loads(r["payload"])) for r in c.execute("SELECT * FROM events ORDER BY seq")]

    @staticmethod
    def append(c, kind, payload, key, occurred_at):
        require(isinstance(key, str) and 8 <= len(key) <= 160, "IDEMPOTENCY_REQUIRED", "操作需要 8–160 字符的幂等键")
        fp = digest({"kind": kind, "payload": payload, "occurred_at": occurred_at})
        old = c.execute("SELECT * FROM events WHERE key=?", (key,)).fetchone()
        if old:
            require(old["fingerprint"] == fp, "IDEMPOTENCY_CONFLICT", "相同幂等键不能用于不同内容")
            return dict(old, duplicate=True)
        id = "ev_" + digest(key)[:24]
        c.execute("INSERT INTO events(id,key,fingerprint,kind,payload,occurred_at,recorded_at) VALUES(?,?,?,?,?,?,?)",
                  (id, key, fp, kind, pack(payload), occurred_at, utcnow()))
        return {"id": id, "seq": c.execute("SELECT last_insert_rowid()").fetchone()[0], "duplicate": False}
