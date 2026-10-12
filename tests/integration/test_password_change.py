from sqlalchemy import select

from packages.database.models import AuditEvent, AuthSession, User


def test_password_change_revokes_every_session(client):
    email = "password-change@example.com"
    old = "old-strong-password"
    new = "new-strong-password"
    first = client.post("/auth/register", json={"email": email, "password": old})
    second = client.post("/auth/login", json={"email": email, "password": old})
    first_headers = {"Authorization": f"Bearer {first.json()['access_token']}"}
    second_headers = {"Authorization": f"Bearer {second.json()['access_token']}"}
    assert client.post("/auth/change-password", json={
        "current_password": "incorrect", "new_password": new,
    }, headers=first_headers).status_code == 401
    assert client.get("/auth/me", headers=second_headers).status_code == 200
    changed = client.post("/auth/change-password", json={
        "current_password": old, "new_password": new,
    }, headers=first_headers)
    assert changed.status_code == 204
    assert changed.headers["cache-control"] == "no-store"
    assert client.get("/auth/me", headers=first_headers).status_code == 401
    assert client.get("/auth/me", headers=second_headers).status_code == 401
    assert client.post("/auth/login", json={"email": email, "password": old}).status_code == 401
    fresh = client.post("/auth/login", json={"email": email, "password": new})
    assert fresh.status_code == 200
    with client.app.state.test_session_factory() as session:
        user = session.scalar(select(User).where(User.email == email))
        sessions = session.scalars(select(AuthSession).where(AuthSession.user_id == user.id)).all()
        assert sum(item.revoked_at is None for item in sessions) == 1
        events = session.scalars(
            select(AuditEvent).where(AuditEvent.actor_user_id == user.id)
        ).all()
        assert any(event.event_type == "auth.password_changed" for event in events)


def test_password_change_rejects_same_or_short_password(client):
    email = "password-validation@example.com"
    old = "old-strong-password"
    registered = client.post("/auth/register", json={"email": email, "password": old})
    token = registered.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    assert client.post("/auth/change-password", headers=headers, json={
        "current_password": old, "new_password": old,
    }).status_code == 409
    assert client.post("/auth/change-password", headers=headers, json={
        "current_password": old, "new_password": "short",
    }).status_code == 422
    assert client.get("/auth/me", headers=headers).status_code == 200
