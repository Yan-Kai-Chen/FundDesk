"""Bounded disposable storage; financial facts and referenced evidence stay."""
from datetime import timedelta
import json
import re

from app.domain.finance import SHANGHAI, stamp
from app.persistence.store import pack

POLICY = {
    "version": 1, "intraday_days": 7, "raw_response_days": 3,
    "raw_response_cap_bytes": 20 * 1024 * 1024,
    "unlinked_plan_days": 7, "unlinked_plan_limit": 200,
    "maintenance_interval_seconds": 3600,
    "protected": "financial events, documents, official NAV, predictions, imports and referenced evidence",
}


def references(value):
    if isinstance(value, dict):
        return set().union(*(references(v) for v in value.values())) if value else set()
    if isinstance(value, list):
        return set().union(*(references(v) for v in value)) if value else set()
    if isinstance(value, str) and (value.startswith(("obs_", "plan_")) or re.fullmatch(r"[0-9a-f]{64}", value)):
        return {value}
    return set()


def automatic_quote(payload):
    return (payload.get('provenance', {}).get('provider_id') in {'THS_PUBLIC_ESTIMATE', 'EASTMONEY_PUBLIC_ESTIMATE', 'SGE_GOLD_REFERENCE'}
            and bool(re.fullmatch(r'[0-9a-f]{64}', payload.get('source_receipt_hash', ''))))


class StorageMixin:
    def _cache_files(self):
        folder = self.root / "data/cache/providers"
        if self.mode != "real" or not folder.is_dir() or folder.is_symlink() or not folder.resolve().is_relative_to(self.root.resolve()):
            return []
        return [p for p in folder.rglob('*') if p.is_file() and not p.is_symlink() and
                not any((folder / parent).is_symlink() for parent in p.relative_to(folder).parents if str(parent) != '.') and
                folder.resolve() in p.resolve().parents]

    def storage_status(self):
        with self.store.connect() as c:
            counts = {name: c.execute(f"SELECT count(*) FROM {name}").fetchone()[0]
                      for name in ("observations", "plans", "events", "predictions")}
            row = c.execute("SELECT value FROM meta WHERE key='storage:last_cleanup'").fetchone()
        paths = [self.store.path, self.store.path.with_name(self.store.path.name + '-wal')]
        return {"policy": POLICY, "database_bytes": sum(p.stat().st_size for p in paths if p.is_file()),
                "provider_cache_bytes": sum(p.stat().st_size for p in self._cache_files()),
                "counts": counts, "last_cleanup": json.loads(row[0]) if row else None}

    def cleanup_storage_due(self):
        with self.store.connect() as c:
            row = c.execute("SELECT value FROM meta WHERE key='storage:last_cleanup'").fetchone()
        if row and stamp(self.now()) - stamp(json.loads(row[0])["at"]) < timedelta(seconds=POLICY["maintenance_interval_seconds"]):
            return None
        return self.cleanup_storage()

    def cleanup_storage(self):
        now = stamp(self.now())
        pinned = set()
        removed = {"expired_plans": 0, "intraday_observations": 0, "raw_files": 0, "raw_bytes": 0}
        with self.store.transaction() as c:
            # Financial facts, settings, source imports and predictions are never
            # deleted. Their references pin the corresponding derived evidence.
            for table in ("events", "documents", "predictions", "resolutions", "imports"):
                for row in c.execute(f"SELECT payload FROM {table}"):
                    pinned.update(references(json.loads(row[0])))
            plans = {r['id']: (dict(r), json.loads(r['payload'])) for r in c.execute("SELECT * FROM plans")}
            pending = [id for id in pinned if id in plans]
            visited = set()
            while pending:
                id = pending.pop()
                if id in visited:
                    continue
                visited.add(id)
                refs = references(plans[id][1])
                pinned.update(refs)
                pending.extend(ref for ref in refs if ref in plans and ref not in visited)
            durable_pins = set(pinned)
            retained = set(visited)
            recent = 0
            cutoff = now - timedelta(days=POLICY['unlinked_plan_days'])
            for id, (row, value) in sorted(plans.items(), key=lambda pair: pair[1][0]['created_at'], reverse=True):
                if id in retained:
                    continue
                if stamp(value['expires_at']) > now:
                    retained.add(id)
                elif stamp(row['created_at']) >= cutoff and recent < POLICY['unlinked_plan_limit']:
                    retained.add(id)
                    recent += 1
            for id, (_, value) in plans.items():
                if id in retained:
                    pinned.update(references(value))
                else:
                    removed['expired_plans'] += c.execute("DELETE FROM plans WHERE id=?", (id,)).rowcount
            # Keep the last known automatic quote per fund even when offline for
            # weeks; older unreferenced minute estimates have a rolling lifetime.
            latest = set()
            for row in c.execute("SELECT id,fund_id,payload FROM observations WHERE quality IN ('PUBLIC_THIRD_PARTY_ESTIMATE','LOCAL_GOLD_REFERENCE') ORDER BY effective_date DESC,observed_at DESC,rowid DESC"):
                if automatic_quote(json.loads(row['payload'])) and row['fund_id'] not in latest:
                    pinned.add(row['id'])
                    durable_pins.add(row['id'])
                    latest.add(row['fund_id'])
            cutoff_date = (now.astimezone(SHANGHAI).date() - timedelta(days=POLICY['intraday_days'] - 1)).isoformat()
            candidates = c.execute("SELECT id,payload FROM observations WHERE quality IN ('PUBLIC_THIRD_PARTY_ESTIMATE','LOCAL_GOLD_REFERENCE') AND effective_date<?", (cutoff_date,)).fetchall()
            for row in candidates:
                if row['id'] not in pinned and automatic_quote(json.loads(row['payload'])):
                    removed['intraday_observations'] += c.execute("DELETE FROM observations WHERE id=?", (row['id'],)).rowcount
            # A pinned observation's normalized value AND raw receipt stay.
            for row in c.execute("SELECT id,payload FROM observations"):
                if row['id'] in durable_pins:
                    durable_pins.update(references(json.loads(row['payload'])))
        files = [p for p in self._cache_files() if re.fullmatch(r'[0-9a-f]{64}', p.stem) and p.suffix in {'.txt','.js','.json','.html'}]
        entries = sorted([(p, p.stat()) for p in files], key=lambda pair: pair[1].st_mtime)
        size = sum(info.st_size for _, info in entries)
        raw_cutoff = (now - timedelta(days=POLICY['raw_response_days'])).timestamp()
        for path, info in entries:
            if path.stem not in durable_pins and (info.st_mtime < raw_cutoff or size > POLICY['raw_response_cap_bytes']):
                path.unlink(missing_ok=True)
                size -= info.st_size
                removed['raw_files'] += 1
                removed['raw_bytes'] += info.st_size
        # Reclaim meaningful free space, rather than VACUUM on every refresh.
        compacted = False
        with self.store.connect() as c:
            c.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            pages = c.execute('PRAGMA page_count').fetchone()[0]
            free = c.execute('PRAGMA freelist_count').fetchone()[0]
            if free >= 256 and free / max(1, pages) >= .2:
                c.execute('VACUUM')
                compacted = True
        receipt = {"at": self.now(), "removed": removed, "compacted": compacted, "policy_version": POLICY['version']}
        with self.store.transaction() as c:
            c.execute("INSERT INTO meta VALUES('storage:last_cleanup',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (pack(receipt),))
        return {"cleanup": receipt, "storage": self.storage_status()}
