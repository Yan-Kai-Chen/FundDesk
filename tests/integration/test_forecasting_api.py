from concurrent.futures import ThreadPoolExecutor
from threading import Event

from fastapi.testclient import TestClient
from app.main import create_app


def test_forecast_routes_are_authenticated_and_keep_accounts_separate(tmp_path, monkeypatch):
    app = create_app(tmp_path, testing=True)
    for mode, wb in app.state.workbenches.items():
        monkeypatch.setattr(wb, 'forecasting_dashboard', lambda mode=mode: {'mode': mode, 'latest': None})
        monkeypatch.setattr(wb, 'forecast_run_export', lambda id, mode=mode: {'mode': mode, 'id': id})
    with TestClient(app) as client:
        assert client.get('/api/v1/forecasting?mode=real').status_code == 401
        token = client.get('/api/v1/session').json()['csrf_token']
        assert client.post('/api/v1/forecasting/runs?mode=real', json={}).status_code == 403
        client.headers['X-CSRF-Token'] = token
        assert client.get('/api/v1/forecasting?mode=real').json()['mode'] == 'real'
        assert client.get('/api/v1/forecasting?mode=demo').json()['mode'] == 'demo'
        assert client.get('/api/v1/forecasting?mode=invalid').status_code == 400
        export = client.get('/api/v1/forecasting/runs/example/export?mode=real')
        assert export.json() == {'mode': 'real', 'id': 'example'}
        assert 'attachment' in export.headers['content-disposition']


def test_forecast_fitting_does_not_hold_main_request_lock(tmp_path, monkeypatch):
    app = create_app(tmp_path, testing=True)
    wb = app.state.workbenches['real']
    entered, release = Event(), Event()

    def slow_fit(p):
        entered.set()
        assert release.wait(3)
        return {'status': 'READY'}

    monkeypatch.setattr(wb, 'generate_forecasts', slow_fit)
    with TestClient(app) as client:
        client.headers['X-CSRF-Token'] = client.get('/api/v1/session').json()['csrf_token']
        with ThreadPoolExecutor(max_workers=2) as pool:
            fitting = pool.submit(client.post, '/api/v1/forecasting/runs?mode=real', json={})
            try:
                assert entered.wait(2)
                assert pool.submit(client.get, '/api/v1/state?mode=real').result(timeout=2).status_code == 200
            finally:
                release.set()
            assert fitting.result(timeout=2).json()['status'] == 'READY'
