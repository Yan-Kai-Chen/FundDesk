"""Bounded daily safety snapshots; updates never rewrite the financial ledger."""
import json
import shutil

from app.persistence.store import Store, digest
from app.version import VERSION

USER_DOCUMENTS = ('fund','rule','strategy','bucket_classification','t_profile',
                  'investment_plan_profile','fund_sidebar','holding_reconciliation',
                  't_review_baseline','reported_holding','migration_opening',
                  'statement','gold_product','gold_statement','t_pairing','research_policy')


class MaintenanceMixin:
    def safety_fingerprint(self, c):
        return digest({'ledger_tip':c.execute('SELECT COALESCE(MAX(seq),0) FROM events').fetchone()[0],
            'documents':[tuple(r) for r in c.execute(
                'SELECT kind,id,version FROM documents WHERE kind IN ('+
                ','.join('?' for _ in USER_DOCUMENTS)+') ORDER BY kind,id', USER_DOCUMENTS)]})

    def automatic_backup_due(self):
        if self.mode != 'real':
            return {'created':False,'reason':'DEMO'}
        today=self.today()
        with self.store.connect() as c:
            prior=Store.doc(c,'maintenance','automatic-backup')
            fingerprint=self.safety_fingerprint(c)
            if prior and prior.get('date')==today:
                return {'created':False,'reason':'ALREADY_TODAY'}
            if prior and prior.get('fingerprint')==fingerprint:
                return {'created':False,'reason':'UNCHANGED'}
            has_data=bool(c.execute("SELECT 1 FROM documents WHERE kind IN ('fund','gold_product') LIMIT 1").fetchone())
        if not has_data:
            return {'created':False,'reason':'EMPTY'}
        backup=self.create_backup(automatic=True,reason='DAILY_CHANGED_RECORDS')
        with self.store.transaction() as c:
            Store.put(c,'maintenance','automatic-backup',{'date':today,'fingerprint':fingerprint,
                'backup_id':backup['id'],'created_at':backup['created_at']}, prior['_version'] if prior else 0)
        # Only these daily automatic copies are eligible for retention. Manual,
        # update and restore-safety snapshots are never deleted here.
        daily=[b for b in self.backups() if b.get('automatic') and b.get('reason')=='DAILY_CHANGED_RECORDS']
        for old in daily[7:]:
            shutil.rmtree(self.backup_path(old['id']))
        return {'created':True,'backup':backup}

    def maintenance_status(self):
        backups=self.backups()
        automatic=next((b for b in backups if b.get('automatic')),None)
        receipt=None
        path=self.root/'data/runtime/install_receipt.json'
        if path.is_file() and path.stat().st_size<=65536:
            try:receipt=json.loads(path.read_text())
            except (ValueError,OSError):pass
        manifest=self.root/'output/builds'/f'FundDesk-{VERSION}-manifest.json'
        signing=None
        if manifest.is_file() and manifest.stat().st_size<=65536:
            try:signing=json.loads(manifest.read_text()).get('signing')
            except (ValueError,OSError):pass
        return {'version':VERSION,'automatic_backup':automatic,'latest_backup':backups[0] if backups else None,
            'daily_policy':{'retained':7,'only_when_records_change':True,'manual_backups_preserved':True},
            'last_install':receipt,'signing':signing or 'Signing status not supplied by this build',
            'data_location':'原 Funding 工作区；更新应用不迁移或替换个人账本',
            'restore_notice':'设置页可先预览备份，再选择恢复；恢复前另外保存当前资料。'}
