from app.persistence.store import Store, pack
from app.services.portfolio_risk import PortfolioRiskMixin
from app.services.workbench import Workbench
from conftest import opening


def test_portfolio_risk_is_read_only_and_uses_one_snapshot_without_network(wb, monkeypatch):
    cls = Workbench if issubclass(Workbench, PortfolioRiskMixin) else type("RiskWorkbench", (PortfolioRiskMixin, Workbench), {})
    w = cls(wb.store, wb.root)
    w.now, w.fid = wb.now, wb.fid
    opening(w, units="100", cost="100")
    w.observe({"fund_id": w.fid, "value_kind": "OFFICIAL_NAV", "value": "1.2", "effective_date": "2026-10-19"})
    def capture():
        with w.store.connect() as c:
            return pack({"events": Store.events(c), "documents": [dict(row) for row in c.execute("SELECT * FROM documents")],
                         "observations": [dict(row) for row in c.execute("SELECT * FROM observations")]})
    before = capture()
    seen = []
    monkeypatch.setattr(w, "daily_t_references", lambda snapshot: seen.append(snapshot) or [])
    monkeypatch.setattr(w, "refresh_research_data", lambda: (_ for _ in ()).throw(AssertionError("read must not fetch")))
    snapshot = w.snapshot()
    for _ in range(3):
        view = w.portfolio_risk(snapshot)
        assert view["allocation"]["total_value"] == "120.00"
        assert view["daily_operations"]["action_count"] == 0
    assert all(item is snapshot for item in seen) and capture() == before
    assert w.portfolio_risk()["allocation"]["total_value"] == "120.00"
