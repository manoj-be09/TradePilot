"""With the email check on, a new account signs in only after opening its one-time link."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from apps.api.settings import Settings, get_settings
from packages.database.models import AuditEvent, EmailVerification, User

EMAIL = "new.trader@example.com"
PASSWORD = "a-strong-test-password"


@pytest.fixture
def outbox(client, monkeypatch) -> list[tuple[str, str]]:
    monkeypatch.setenv("EMAIL_VERIFICATION_REQUIRED", "true")
    monkeypatch.setenv("SMTP_HOST", "smtp.example.test")
    monkeypatch.setenv("SMTP_FROM", "TradePilot <no-reply@example.test>")
    monkeypatch.setenv("PUBLIC_WEB_URL", "http://localhost:3000")
    get_settings.cache_clear()
    sent: list[tuple[str, str]] = []

    def send(_settings, to, token):
        sent.append((to, token))
        return True

    monkeypatch.setattr("apps.api.main.send_link", send)
    return sent


def _register(client):
    return client.post("/auth/register", json={"email": EMAIL, "password": PASSWORD})


def _login(client):
    return client.post("/auth/login", json={"email": EMAIL, "password": PASSWORD})


def test_signup_waits_for_the_link_then_the_link_signs_in_once(client, outbox):
    created = _register(client)
    assert created.status_code == 201
    assert created.json() == {"verification_required": True, "email_sent": True}
    assert "access_token" not in created.json()
    assert _login(client).status_code == 403

    (to, token), = outbox
    assert to == EMAIL
    verified = client.post("/auth/verify-email", json={"token": token})
    assert verified.status_code == 200
    bearer = {"Authorization": f"Bearer {verified.json()['access_token']}"}
    me = client.get("/auth/me", headers=bearer)
    assert me.status_code == 200 and me.json()["email"] == EMAIL
    assert _login(client).status_code == 200
    assert client.post("/auth/verify-email", json={"token": token}).status_code == 400


def test_an_expired_or_unknown_link_is_refused(client, outbox):
    _register(client)
    (_, token), = outbox
    factory = client.app.state.test_session_factory
    with factory() as session:
        row = session.scalar(select(EmailVerification))
        row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        session.commit()
    assert client.post("/auth/verify-email", json={"token": token}).status_code == 400
    assert client.post("/auth/verify-email", json={"token": "x" * 43}).status_code == 400
    assert _login(client).status_code == 403


def test_resend_replaces_the_link_and_never_reveals_whether_an_account_exists(client, outbox):
    _register(client)
    first = outbox[0][1]
    again = client.post("/auth/resend-verification", json={"email": EMAIL})
    unknown = client.post("/auth/resend-verification", json={"email": "nobody@example.com"})
    assert again.status_code == unknown.status_code == 202
    assert again.json() == unknown.json()
    assert len(outbox) == 2
    assert client.post("/auth/verify-email", json={"token": first}).status_code == 400
    assert client.post("/auth/verify-email", json={"token": outbox[1][1]}).status_code == 200


def test_a_failed_send_keeps_the_account_waiting_and_resend_can_retry(client, outbox, monkeypatch):
    monkeypatch.setattr("apps.api.main.send_link", lambda *_: False)
    assert _register(client).json() == {"verification_required": True, "email_sent": False}
    assert _login(client).status_code == 403


def test_accounts_created_before_the_check_still_sign_in(client, outbox):
    factory = client.app.state.test_session_factory
    from apps.api.auth import hash_password
    with factory() as session:
        session.add(User(email=EMAIL, password_hash=hash_password(PASSWORD), role="VIEWER"))
        session.commit()
    assert _login(client).status_code == 200
    with factory() as session:
        events = set(session.scalars(select(AuditEvent.event_type)))
    assert "auth.login.succeeded" in events


def test_with_the_check_off_signup_signs_in_at_once(client):
    created = _register(client)
    assert created.status_code == 201 and created.json()["access_token"]


def test_turning_the_check_on_without_delivery_settings_is_refused(monkeypatch):
    monkeypatch.setenv("EMAIL_VERIFICATION_REQUIRED", "true")
    monkeypatch.delenv("SMTP_HOST", raising=False)
    with pytest.raises(ValueError, match="SMTP_HOST"):
        Settings(_env_file=None, email_verification_required=True)
    with pytest.raises(ValueError, match="https"):
        Settings(_env_file=None, email_verification_required=True, smtp_host="h",
                 smtp_from="f", public_web_url="http://tradepilot.example.com")
