"""Scorecard reads only permanent documents in its own isolated workspace."""
from decimal import Decimal as D

import pytest

from app.domain.forecast_scorecard import RESULT_BASIS
from app.domain.forecasting import ENGINE_VERSION
from app.persistence.store import Store
from app.services.forecast_scorecard import ForecastScorecardMixin
from app.services.workbench import Workbench


@pytest.fixture
def sw(tmp_path):
    cls = Workbench if issubclass(Workbench, ForecastScorecardMixin) else type("ScorecardWorkbench", (ForecastScorecardMixin, Workbench), {})
    w = cls(Store(tmp_path / "data/real/workbench.sqlite3", "real"), tmp_path)
    w.now = lambda: "2026-10-24T12:00:00Z"
    w.fid = w.save_fund({"fund_code": "012183", "name": "Synthetic scorecard fund", "share_class": "C"})["id"]
    return w


def save_documents(w):
    p = {"id": "p", "prediction_id": "p", "fund_id": w.fid, "fund_code": "012183", "engine_version": ENGINE_VERSION,
         "horizon": 1, "status": "READY", "task": "TOTAL_RETURN", "target_existed_at_generation": False,
         "base_date": "2026-10-22", "target_date": "2026-10-23", "generated_at": "2026-10-22T12:00:00Z",
         "dataset_observed_at": "2026-10-22T11:00:00Z", "base_observed_at": "2026-10-22T10:00:00Z",
         "forecast_return": D(".01"), "selected_model": "SYNTHETIC"}
    r = {"id": "p", "prediction_id": "p", "fund_id": w.fid, "fund_code": "012183", "engine_version": ENGINE_VERSION,
         "horizon": 1, "target_date": "2026-10-23", "resolved_at": "2026-10-23T12:00:00Z",
         "actual_first_observed_at": "2026-10-23T11:00:00Z", "research_observed_at": "2026-10-23T11:30:00Z",
         "actual_return": D(".02"), "actual_nav": "1.02", "actual_observation_id": "synthetic-formal", "basis": RESULT_BASIS}
    with w.store.transaction() as c:
        Store.put(c, "forecast_prediction", "p", p, 0)
        Store.put(c, "forecast_resolution", "p", r, 0)


def database_rows(w):
    with w.store.connect() as c:
        return {t: [tuple(r) for r in c.execute(f"SELECT * FROM {t} ORDER BY rowid")] for t in
                ("documents", "events", "observations", "predictions", "resolutions")}


def test_projection_is_readonly_serializes_decimal_and_never_fits_resolves_or_fetches(sw, monkeypatch):
    save_documents(sw)
    def forbidden(*args, **kw):
        raise AssertionError("Scorecard must only read its frozen documents")
    for name in ("generate_forecasts", "refresh_research_data", "_resolve_forecasts", "snapshot"):
        monkeypatch.setattr(sw, name, forbidden)
    before = database_rows(sw)
    for _ in range(3):
        result = sw.forecast_scorecard()
        assert result["mode"] == "real" and result["summary"]["resolved"] == 1
        one = next(g for g in result["groups"] if g["horizon"] == 1)
        assert one["mae_pp"] == "1.00" and one["baseline_mae_pp"] == "2.00"
        assert isinstance(one["recent_resolved"][0]["forecast_return"], str)
    assert database_rows(sw) == before


def test_empty_demo_never_borrows_real_frozen_predictions(sw):
    save_documents(sw)
    demo = type(sw)(Store(sw.root / "data/demo/workbench.sqlite3", "demo"), sw.root)
    demo.now = sw.now
    result = demo.forecast_scorecard()
    assert result["mode"] == "demo" and result["summary"]["resolved"] == 0 and not result["groups"]


def test_scorecard_frozen_result_survives_new_estimates_and_formal_revisions(sw):
    save_documents(sw)
    before = sw.forecast_scorecard()
    sw.observe({"fund_id": sw.fid, "value_kind": "THIRD_PARTY_ESTIMATE", "effective_date": "2026-10-23",
                "value": "1.5", "source": "Synthetic estimate"})
    sw.observe({"fund_id": sw.fid, "value_kind": "OFFICIAL_NAV", "effective_date": "2026-10-23",
                "value": "1.8", "source": "Synthetic later revision", "quality": "MANAGER_DISCLOSURE"})
    after = sw.forecast_scorecard()
    assert after == before


def test_scorecard_api_requires_session_isolates_modes_and_get_does_not_write(sw):
    from app.main import create_app
    from fastapi.testclient import TestClient
    save_documents(sw)
    app = create_app(sw.root, testing=True)
    app.state.workbenches['real'].now = sw.now
    with TestClient(app) as client:
        assert client.get('/api/v1/forecasting/scorecard?mode=real').status_code == 401
        client.get('/api/v1/session')
        before = database_rows(sw)
        for _ in range(2):
            response = client.get('/api/v1/forecasting/scorecard?mode=real')
            assert response.status_code == 200, response.text
            assert response.json()['summary']['resolved'] == 1
            assert response.json()['groups'][0]['mae_pp'] == '1.00'
        assert client.get('/api/v1/forecasting/scorecard?mode=demo').json()['summary']['resolved'] == 0
        assert client.get('/api/v1/forecasting/scorecard?mode=invalid').status_code == 400
        assert database_rows(sw) == before
