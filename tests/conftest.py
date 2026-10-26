from pathlib import Path
import sys
import uuid
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src/backend"))
from app.persistence.store import Store
from app.services.workbench import Workbench

AT = "2026-10-20T04:00:00+00:00"


@pytest.fixture
def wb(tmp_path):
    w = Workbench(Store(tmp_path / "data/real/workbench.sqlite3", "real"), tmp_path)
    w.now = lambda: "2026-10-20T05:30:00+00:00"
    f = w.save_fund({"fund_code": "000001", "name": "独立合成测试基金", "share_class": "A"})
    w.fid = f["id"]
    w.save_rules(w.fid, {"verified": True, "source": "Synthetic test fixture only", "effective_from": "2020-01-01",
        "lot_method": "FIFO", "purchase_rate": "0.0015", "unit_step": "0.01", "open_dates": ["2026-10-19","2026-10-20","2026-10-21","2026-10-22"],
        "redemption_tiers": [{"min_days":0,"max_days":7,"rate":"0.015"},{"min_days":7,"max_days":None,"rate":"0"}]})
    return w


def event(w, kind, payload, key=None, at=AT):
    return w.event(kind, payload, key or str(uuid.uuid4()), at)


def deposit(w, value="10000.00"):
    return event(w,"CASH_DEPOSIT",{"amount":value})


def opening(w, units="1000", cost="1000", acquired="2026-06-01", id="lot-a", available="2026-06-03"):
    return event(w,"OPENING_LOT",{"fund_id":w.fid,"lot_id":id,"units":units,"cost":cost,"acquired":acquired,"available_from":available})


def order(w, side="BUY", requested="1000", **extra):
    return w.order({"fund_id":w.fid,"side":side,"requested":requested,"occurred_at":AT,"key":str(uuid.uuid4()),"cash_source":"TRACKED",**extra})["order_id"]


def confirm(w, oid, units="798.80", nav="1.25", gross="1000.00", fee="1.50", net="998.50", final=True, **extra):
    return event(w,"ORDER_CONFIRMED",{"order_id":oid,"units":units,"nav":nav,"gross":gross,"fee":fee,"net":net,
        "nav_date":"2026-10-20","confirm_date":"2026-10-20","available_from":"2026-10-21","is_final":final,**extra})
