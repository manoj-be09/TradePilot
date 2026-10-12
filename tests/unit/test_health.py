from fastapi.testclient import TestClient

from apps.api.main import app


def test_health_reports_live_execution_locked(monkeypatch) -> None:
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "true")
    from apps.api.settings import get_settings

    get_settings.cache_clear()
    response = TestClient(app).get("/health")
    get_settings.cache_clear()
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["live_trading_enabled"] is False
