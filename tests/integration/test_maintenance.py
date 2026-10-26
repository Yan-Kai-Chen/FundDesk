"""Daily copies follow user records and Beijing days, never market-refresh churn."""
import json
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.main import create_app
from app.persistence.store import Store, pack
from app.services import backup as backup_service
from app.services.workbench import Workbench
from conftest import deposit


def fingerprint(w):
    with w.store.connect() as c:
        return w.safety_fingerprint(c)


def user_doc(w, kind, id="synthetic", **payload):
    with w.store.transaction() as c:
        old = Store.doc(c, kind, id)
        return Store.put(c, kind, id, payload, old["_version"] if old else 0)


def financial_rows(w):
    with w.store.connect() as c:
        return pack({"events": Store.events(c), "documents": Store.docs(c, "fund"),
                     "rules": Store.docs(c, "rule"),
                     "observations": [dict(r) for r in c.execute("SELECT * FROM observations ORDER BY id")]})


def test_daily_backup_once_per_beijing_day_and_only_changed_records(wb, monkeypatch):
    monkeypatch.setattr(backup_service, "utcnow", lambda: wb.now())
    wb.now = lambda: "2026-10-20T15:59:00+00:00"  # 23:59 Beijing, 00:59 Tokyo next day
    before = financial_rows(wb)
    first = wb.automatic_backup_due()
    assert first["created"] and first["backup"]["reason"] == "DAILY_CHANGED_RECORDS"
    assert wb.automatic_backup_due() == {"created": False, "reason": "ALREADY_TODAY"}
    user_doc(wb, "fund_sidebar", "catalog", groups=[], funds=[])
    assert wb.automatic_backup_due()["reason"] == "ALREADY_TODAY"
    wb.now = lambda: "2026-10-20T16:01:00+00:00"  # Now October 21 in Beijing
    second = wb.automatic_backup_due()
    assert second["created"] and second["backup"]["id"] != first["backup"]["id"]
    with wb.store.connect() as c:
        receipt = Store.doc(c, "maintenance", "automatic-backup")
        assert receipt["date"] == "2026-10-21" and receipt["_version"] == 2
    wb.now = lambda: "2026-10-21T16:01:00+00:00"
    assert wb.automatic_backup_due() == {"created": False, "reason": "UNCHANGED"}
    assert len(wb.backups()) == 2
    # The copies and receipt never change financial facts.
    assert financial_rows(wb) == before


def test_market_refresh_logs_and_observations_do_not_trigger_daily_copies(wb, monkeypatch):
    monkeypatch.setattr(backup_service, "utcnow", lambda: wb.now())
    assert wb.automatic_backup_due()["created"]
    original = fingerprint(wb)
    wb.now = lambda: "2026-10-21T05:30:00Z"
    for kind in ["nav_sync", "intraday_sync", "research_sync", "forecast_maintenance", "research_run"]:
        user_doc(wb, kind, status="READY", attempted_at=wb.now())
    wb.observe({"fund_id": wb.fid, "value_kind": "OFFICIAL_NAV", "value": "1.23",
                "effective_date": "2026-10-20", "source": "Synthetic quotation"})
    wb.observe({"fund_id": wb.fid, "value_kind": "THIRD_PARTY_ESTIMATE", "value": "1.24",
                "effective_date": "2026-10-21", "source": "Synthetic quotation"})
    assert fingerprint(wb) == original
    assert wb.automatic_backup_due() == {"created": False, "reason": "UNCHANGED"}
    assert len(wb.backups()) == 1


def test_event_and_actual_original_transaction_documents_trigger_next_day_copy(wb, monkeypatch):
    monkeypatch.setattr(backup_service, "utcnow", lambda: wb.now())
    assert wb.automatic_backup_due()["created"]
    for i, kind in enumerate(["statement", "gold_statement", "reported_holding", "t_pairing", "research_policy"], start=1):
        wb.now = lambda i=i: f"2026-10-{20+i:02d}T05:30:00Z"
        prior = fingerprint(wb)
        user_doc(wb, kind, rows=[{"synthetic": i}])
        assert fingerprint(wb) != prior
        assert wb.automatic_backup_due()["created"]
    wb.now = lambda: "2026-10-26T05:30:00Z"
    prior = fingerprint(wb)
    deposit(wb, "12")
    assert fingerprint(wb) != prior and wb.automatic_backup_due()["created"]


def test_retains_seven_newest_daily_copies_but_preserves_manual_and_update_copies(wb, monkeypatch):
    monkeypatch.setattr(backup_service, "utcnow", lambda: wb.now())
    preserved = [wb.create_backup(), wb.create_backup(automatic=True, reason="BEFORE_UPDATE"),
                 wb.create_backup(automatic=True, reason="BEFORE_RESTORE")]
    dates, ids = [], []
    start = datetime(2026, 10, 20, 5, 30, tzinfo=timezone.utc)
    for i in range(11):
        at = (start+timedelta(days=i)).isoformat()
        wb.now = lambda at=at: at
        user_doc(wb, "fund_sidebar", "catalog", groups=[], funds=[], synthetic_change=i)
        result = wb.automatic_backup_due()
        assert result["created"]
        dates.append(at);ids.append(result["backup"]["id"])
    remaining = wb.backups()
    daily = [b for b in remaining if b.get("automatic") and b.get("reason") == "DAILY_CHANGED_RECORDS"]
    assert [b["id"] for b in daily] == list(reversed(ids[-7:]))
    assert {b["id"] for b in preserved} <= {b["id"] for b in remaining}
    assert all(not wb.backup_path(id).exists() for id in ids[:-7])
    assert all(wb.preview_restore(b["id"])["valid"] for b in remaining)


def test_backup_sort_uses_creation_time_instead_of_random_directory_names(wb, monkeypatch):
    times = ["2026-10-22T13:00:00+00:00", "2026-10-20T13:00:00+00:00", "2026-10-23T13:00:00+00:00"]
    results = []
    for at in times:
        monkeypatch.setattr(backup_service, "utcnow", lambda at=at: at)
        results.append(wb.create_backup())
    assert [b["id"] for b in wb.backups()] == [results[i]["id"] for i in [2, 0, 1]]
    status = wb.maintenance_status()
    assert status["latest_backup"]["id"] == results[2]["id"]


def test_maintenance_get_is_read_only_and_reports_latest_daily_install_and_signing(wb, monkeypatch):
    monkeypatch.setattr(backup_service, "utcnow", lambda: wb.now())
    automatic = wb.automatic_backup_due()["backup"]
    wb.now = lambda: "2026-10-20T06:30:00Z"
    manual = wb.create_backup()
    before = financial_rows(wb)
    with wb.store.connect() as c:
        docs_before = pack([dict(r) for r in c.execute("SELECT * FROM documents ORDER BY kind,id")])
    receipt = wb.root/"data/runtime/install_receipt.json"
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text(json.dumps({"version": "synthetic", "validated": True}))
    for _ in range(3):
        status = wb.maintenance_status()
        assert status["automatic_backup"]["id"] == automatic["id"]
        assert status["latest_backup"]["id"] == manual["id"]
        assert status["daily_policy"] == {"retained": 7, "only_when_records_change": True, "manual_backups_preserved": True}
        assert status["last_install"]["validated"] is True
    assert financial_rows(wb) == before
    with wb.store.connect() as c:
        assert pack([dict(r) for r in c.execute("SELECT * FROM documents ORDER BY kind,id")]) == docs_before


def test_demo_and_empty_real_do_not_make_automatic_copies(tmp_path):
    for mode in ["demo", "real"]:
        w = Workbench(Store(tmp_path/"data"/mode/"workbench.sqlite3", mode), tmp_path)
        assert w.automatic_backup_due() == {"created": False, "reason": "DEMO" if mode == "demo" else "EMPTY"}
        assert w.backups() == []


def test_gold_gram_only_user_records_are_not_treated_as_empty(tmp_path, monkeypatch):
    w = Workbench(Store(tmp_path/"data/real/workbench.sqlite3", "real"), tmp_path)
    w.now = lambda: "2026-10-20T05:30:00Z"
    monkeypatch.setattr(backup_service, "utcnow", lambda: w.now())
    user_doc(w, "gold_product", "gold_synthetic", name="合成按克黄金", quantity_unit="GRAM")
    user_doc(w, "gold_statement", "gold_synthetic", rows=[{"unit": "GRAM", "value": "1"}])
    assert w.automatic_backup_due()["created"]
    assert w.maintenance_status()["latest_backup"]["mode"] == "real"


def test_get_maintenance_route_requires_session_and_does_not_append_events(tmp_path):
    with TestClient(create_app(tmp_path, testing=True)) as client:
        assert client.get("/api/v1/maintenance?mode=real").status_code == 401
        client.get("/api/v1/session")
        wb = client.app.state.workbenches["real"]
        wb.save_fund({"fund_code": "000001", "name": "合成维护测试基金"})
        before = financial_rows(wb)
        for _ in range(3):
            response = client.get("/api/v1/maintenance?mode=real")
            assert response.status_code == 200 and response.json()["daily_policy"]["retained"] == 7
        assert wb.backups() == [] and financial_rows(wb) == before
