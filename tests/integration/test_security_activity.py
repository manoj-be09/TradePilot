def test_security_activity_is_private_bounded_and_sanitized(client):
    first = client.post("/auth/register", json={
        "email": "activity-first@example.com", "password": "a-strong-test-password",
    })
    second = client.post("/auth/register", json={
        "email": "activity-second@example.com", "password": "a-strong-test-password",
    })
    assert first.status_code == 201 and second.status_code == 201
    assert client.get("/auth/activity").status_code == 401
    first_headers = {"Authorization": f"Bearer {first.json()['access_token']}"}
    second_headers = {"Authorization": f"Bearer {second.json()['access_token']}"}
    client.post("/auth/login", json={
        "email": "activity-first@example.com", "password": "a-strong-test-password",
    })
    activity = client.get("/auth/activity?limit=1", headers=first_headers)
    assert activity.status_code == 200
    assert activity.headers["cache-control"] == "no-store"
    assert len(activity.json()) == 1
    assert set(activity.json()[0]) == {"id", "event_type", "created_at"}
    assert activity.json()[0]["event_type"] == "auth.login.succeeded"
    assert "activity-second@example.com" not in activity.text
    assert "activity-first@example.com" not in activity.text
    other = client.get("/auth/activity", headers=second_headers)
    assert [item["event_type"] for item in other.json()] == ["auth.registration.succeeded"]
    assert client.get("/auth/activity?limit=51", headers=first_headers).status_code == 422
