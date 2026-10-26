#!/usr/bin/env python3
"""Export structural diagnostics without account balances, fund names or credentials."""
import argparse
import json
from pathlib import Path
import platform
import sqlite3
from contextlib import closing


def diagnose(root):
    report={'version':'0.18.0','platform':platform.system(),'machine':platform.machine(),'databases':{},'includes_personal_ledger_values':False}
    for mode in ['demo','real']:
        path=root/'data'/mode/'workbench.sqlite3'
        result={'exists':path.is_file()}
        if path.is_file():
            try:
                # mode=rw permits WAL bookkeeping for an existing DB; query_only prohibits logical writes.
                with closing(sqlite3.connect(f'file:{path}?mode=rw',uri=True,timeout=3)) as c:
                    c.execute('PRAGMA query_only=ON')
                    result.update(integrity=c.execute('PRAGMA quick_check').fetchone()[0],schema_version=c.execute('PRAGMA user_version').fetchone()[0])
                result['status']='ok' if result['integrity']=='ok' else 'error'
            except sqlite3.OperationalError as exc:
                result.update(status='unavailable',error_type=type(exc).__name__,message='本次读取暂不可用，可能有锁或文件状态变化；请稍后重试，尚未判定数据库损坏。')
            except sqlite3.DatabaseError as exc:
                result.update(status='error',error_type=type(exc).__name__,message='数据库完整性检查失败；保留原件，先在临时库验证备份后再恢复。')
        else:
            result.update(status='not_initialized')
        report['databases'][mode]=result
    report['healthy']=all(r['status'] not in {'error','unavailable'} for r in report['databases'].values())
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[2])
    args=parser.parse_args()
    report=diagnose(args.root.resolve())
    path=args.root/'output/reports/v0.18.0-diagnostics.json'
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return 0 if report['healthy'] else 1


if __name__=='__main__':raise SystemExit(main())
