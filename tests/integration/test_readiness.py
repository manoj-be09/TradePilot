from datetime import datetime, timedelta, timezone

from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from apps.api import main
from apps.api.login_throttle import LoginThrottleUnavailable
from packages.database.models import WorkerLease
from packages.paper_trading.worker_lease import WORKER_NAME


def test_ready_requires_database_and_limiter(client, monkeypatch):
    response = client.get("/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}
    assert response.headers["cache-control"] == "no-store"

    def unavailable(*args, **kwargs):
        raise OperationalError("SELECT 1", {}, Exception("db unavailable"))

    monkeypatch.setattr(Session, "execute", unavailable)
    response = client.get("/ready")
    assert response.status_code == 503
    assert response.json() == {"detail": "API dependencies unavailable"}
    assert response.headers["cache-control"] == "no-store"


def test_ready_fails_closed_when_limiter_is_down(client, monkeypatch):
    class DownThrottle:
        def check_health(self):
            raise LoginThrottleUnavailable("private backend detail")

    monkeypatch.setattr(main, "get_login_throttle", lambda: DownThrottle())
    response = client.get("/ready")
    assert response.status_code == 503
    assert "private backend detail" not in response.text


def test_paper_worker_readiness_tracks_heartbeat_and_health(client):
    assert client.get("/ready/paper-worker").status_code == 503
    factory = client.app.state.test_session_factory
    now = datetime.now(timezone.utc)
    with factory() as session:
        session.add(WorkerLease(
            name=WORKER_NAME, owner="probe-test", generation=1,
            lease_until=now, heartbeat_at=now, last_success_at=now,
            status="HEALTHY", detail="private worker detail",
        ))
        session.commit()
    response = client.get("/ready/paper-worker")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}
    assert response.headers["cache-control"] == "no-store"
    with factory() as session:
        lease = session.get(WorkerLease, WORKER_NAME)
        lease.status = "RUNNING"
        lease.lease_until = now + timedelta(seconds=60)
        session.commit()
    assert client.get("/ready/paper-worker").status_code == 200
    with factory() as session:
        lease = session.get(WorkerLease, WORKER_NAME)
        lease.lease_until = now - timedelta(seconds=1)
        session.commit()
    assert client.get("/ready/paper-worker").status_code == 503
    with factory() as session:
        lease = session.get(WorkerLease, WORKER_NAME)
        lease.status = "HEALTHY"
        lease.heartbeat_at = now - timedelta(minutes=10)
        session.commit()
    response = client.get("/ready/paper-worker")
    assert response.status_code == 503
    assert "private worker detail" not in response.text


def test_api_browser_security_headers_apply_to_success_and_errors(client):
    for path in ("/health", "/ready", "/auth/me"):
        response = client.get(path)
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["x-frame-options"] == "DENY"
        assert response.headers["referrer-policy"] == "no-referrer"
        assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
        assert "camera=()" in response.headers["permissions-policy"]
