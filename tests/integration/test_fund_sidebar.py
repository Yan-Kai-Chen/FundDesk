from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from app.domain.finance import Problem
from app.main import create_app
from app.persistence.store import Store
from app.services.workbench import Workbench


def draft(catalog):
    return {"groups": deepcopy(catalog["groups"]), "funds": deepcopy(catalog["funds"]),
            "expected_version": catalog["_version"]}


def test_public_defaults_are_empty_and_read_only(wb):
    for code in ["012183", "021528", "019633", "012738", "017954", "008987"]:
        wb.save_fund({"fund_code": code, "share_class": "C", "name": "Synthetic unclassified fixture"})
    before = wb.snapshot()
    catalog = before["fund_sidebar"]
    assert catalog["groups"] == [] and catalog["_version"] == 0
    assert all(f["group_id"] is None and f["theme"] == "" for f in catalog["funds"])
    assert all(f["display_name"] == next(x["name"] for x in before["funds"] if x["id"] == f["fund_id"])
               for f in catalog["funds"])
    with wb.store.connect() as c:
        assert Store.docs(c, "fund_sidebar") == []


def test_custom_catalog_survives_reopen_backup_and_new_funds_without_ledger_changes(wb):
    before = wb.snapshot()
    p = draft(before["fund_sidebar"])
    p["groups"] = [{"id": "custom", "name": "我的观察"}, {"id": "long", "name": "长期"}]
    p["funds"][0].update(group_id="custom", display_name="我的基金", theme="自主标签")
    saved = wb.save_fund_sidebar(p)
    assert saved["_version"] == 1
    renamed = draft(saved)
    renamed["groups"] = [{"id": "long", "name": "底仓观察"}, {"id": "custom", "name": "我的科技"}]
    renamed["funds"][0]["group_id"] = "long"
    wb.save_fund_sidebar(renamed)
    reopened = Workbench(Store(wb.store.path, "real"), wb.root)
    reopened.now = wb.now
    after = reopened.snapshot()
    assert after["fund_sidebar"]["groups"][0]["name"] == "底仓观察"
    assert after["fund_sidebar"]["funds"][0]["group_id"] == "long"
    for field in ["funds", "orders", "lots", "audit", "portfolio", "revision"]:
        assert before[field] == after[field]
    backup = reopened.create_backup()
    backup_store = Store(reopened.backup_path(backup["id"]) / "workbench.sqlite3", "real")
    with backup_store.connect() as c:
        assert Store.doc(c, "fund_sidebar", "catalog")["funds"][0]["theme"] == "自主标签"
    new = reopened.save_fund({"fund_code": "008987", "share_class": "C", "name": "新加入的合成基金"})
    extended = reopened.snapshot()["fund_sidebar"]
    assert [g["id"] for g in extended["groups"]] == ["long", "custom"]
    assert next(f for f in extended["funds"] if f["fund_id"] == new["id"])["group_id"] is None
    # Deleting a category only unassigns its funds, and defaults never restore it.
    removed = draft(extended)
    removed["groups"] = []
    for f in removed["funds"]:
        f["group_id"] = None
    reopened.save_fund_sidebar(removed)
    assert reopened.snapshot()["fund_sidebar"]["groups"] == []
    assert len(reopened.snapshot()["funds"]) == 2


@pytest.mark.parametrize("change", ["blank", "duplicate", "unknown_fund", "unknown_group", "bad_version"])
def test_invalid_edits_are_atomic(wb, change):
    p = draft(wb.snapshot()["fund_sidebar"])
    if change == "blank":
        p["groups"] = [{"id": "a", "name": "   "}]
    elif change == "duplicate":
        p["funds"] *= 2
    elif change == "unknown_fund":
        p["funds"][0]["fund_id"] = "absent"
    elif change == "unknown_group":
        p["funds"][0]["group_id"] = "absent"
    else:
        p["expected_version"] = 1
    with pytest.raises(Problem):
        wb.save_fund_sidebar(p)
    with wb.store.connect() as c:
        assert Store.docs(c, "fund_sidebar") == []


def test_api_session_version_conflict_and_mode_isolation(tmp_path):
    with TestClient(create_app(tmp_path, testing=True)) as client:
        path = "/api/v1/fund-sidebar?mode=demo"
        assert client.put(path, json={}).status_code == 401
        csrf = client.get("/api/v1/session").json()["csrf_token"]
        catalog = client.get("/api/v1/state?mode=demo").json()["fund_sidebar"]
        p = draft(catalog)
        p["groups"] = [{"id": "mine", "name": "我的分类"}]
        p["funds"][0].update(group_id="mine", display_name="我的演示基金")
        assert client.put(path, json=p).status_code == 403
        client.headers["x-csrf-token"] = csrf
        assert client.put(path, json=p).json()["_version"] == 1
        stale = client.put(path, json=p)
        assert stale.status_code == 400 and stale.json()["code"] == "VERSION_CONFLICT"
        assert client.get("/api/v1/state?mode=real").json()["fund_sidebar"]["_version"] == 0
        assert client.put(path.replace("demo", "real"), json=p).status_code == 400
