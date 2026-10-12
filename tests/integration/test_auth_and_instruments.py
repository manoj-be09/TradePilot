from datetime import date, datetime, timedelta, timezone

import httpx
import jwt
from sqlalchemy import select

from apps.api.auth import Role
from apps.api.settings import get_settings
from packages.database.models import User
from packages.market_data.upstox import UpstoxMarketDataProvider


def test_registration_is_viewer_only_and_instrument_reads_require_auth(client):
    response = client.post(
        "/auth/register", json={"email": "trader@example.com", "password": "a-strong-test-password"}
    )
    assert response.status_code == 201
    token = response.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    assert client.get("/auth/me", headers=headers).json()["role"] == "VIEWER"
    assert client.get("/instruments").status_code == 401
    assert client.get("/instruments", headers=headers).status_code == 200


def test_admin_can_register_dynamic_instruments_and_view_expiries(client):
    client.post(
        "/auth/register", json={"email": "admin@example.com", "password": "a-strong-test-password"}
    )
    with client.app.state.test_session_factory() as session:
        user = session.scalar(select(User).where(User.email == "admin@example.com"))
        user.role = Role.ADMIN.value
        session.commit()
    login = client.post(
        "/auth/login", json={"email": "admin@example.com", "password": "a-strong-test-password"}
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    payload = {
        "exchange": "NFO",
        "segment": "NFO-OPT",
        "instrument_token": "token-1",
        "trading_symbol": "provider-supplied-symbol",
        "underlying": "NIFTY",
        "instrument_type": "OPTION",
        "expiry": (date.today() + timedelta(days=10)).isoformat(),
        "strike": "25000",
        "option_type": "CE",
        "lot_size": 25,
        "tick_size": "0.05",
        "status": "ACTIVE",
        "effective_from": datetime.now(timezone.utc).isoformat(),
        "effective_to": None,
        "provider": "operator-import",
    }
    created = client.post("/instruments", json=payload, headers=headers)
    assert created.status_code == 201, created.text
    assert created.json()["instrument_token"] == "token-1"
    assert client.get("/instruments/expiries", headers=headers).json() == [payload["expiry"]]
    assert client.get(
        "/instruments/strikes?underlying=NIFTY&expiry=" + payload["expiry"], headers=headers
    ).json() == ["25000.00"]
    assert (
        client.get("/instruments?underlying=NIFTY", headers=headers).json()[0]["id"]
        == created.json()["id"]
    )
    assert client.post("/instruments", json=payload, headers=headers).status_code == 409


def test_shared_catalog_accepts_spot_and_futures_with_bounded_search(client):
    client.post(
        "/auth/register",
        json={"email": "catalog-admin@example.com", "password": "a-strong-test-password"},
    )
    with client.app.state.test_session_factory() as session:
        user = session.scalar(select(User).where(User.email == "catalog-admin@example.com"))
        user.role = Role.ADMIN.value
        session.commit()
    login = client.post(
        "/auth/login",
        json={"email": "catalog-admin@example.com", "password": "a-strong-test-password"},
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    base = {
        "exchange": "NSE", "segment": "NSE-CM", "underlying": "SAMPLE",
        "lot_size": 1, "tick_size": "0.05", "status": "ACTIVE",
        "effective_from": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
        "effective_to": None, "provider": "operator-import",
    }
    instruments = [
        {"instrument_type": "EQUITY", "instrument_token": "equity-1", "trading_symbol": "SAMPLE"},
        {"instrument_type": "ETF", "instrument_token": "etf-1", "trading_symbol": "SAMPLEETF"},
        {
            "instrument_type": "FUTURE", "instrument_token": "future-1",
            "trading_symbol": "SAMPLEFUT",
            "expiry": (date.today() + timedelta(days=10)).isoformat(),
            "exchange": "NFO", "segment": "NFO-FUT", "lot_size": 25,
        },
    ]
    for values in instruments:
        result = client.post("/instruments", json={**base, **values}, headers=headers)
        assert result.status_code == 201, result.text

    equities = client.get("/instruments?instrument_type=EQUITY&exchange=NSE", headers=headers)
    assert [row["instrument_token"] for row in equities.json()] == ["equity-1"]
    futures = client.get("/instruments?instrument_type=FUTURE&search=FUT", headers=headers)
    assert [row["instrument_token"] for row in futures.json()] == ["future-1"]
    assert futures.json()[0]["expiry"] == instruments[2]["expiry"]
    page = client.get("/instruments?limit=2&offset=2", headers=headers)
    assert len(page.json()) == 1
    assert client.get("/instruments?limit=101", headers=headers).status_code == 422
    assert client.get("/instruments?instrument_type=UNKNOWN", headers=headers).status_code == 422
    assert client.post(
        "/instruments", json={**base, **instruments[2], "expiry": None}, headers=headers
    ).status_code == 422
    assert client.post(
        "/instruments", json={**base, **instruments[0], "expiry": instruments[2]["expiry"]},
        headers=headers,
    ).status_code == 422


def test_login_rejects_bad_password_and_inactive_account(client):
    response = client.post(
        "/auth/register", json={"email": "user@example.com", "password": "a-strong-test-password"}
    )
    assert response.status_code == 201
    bad = client.post("/auth/login", json={"email": "user@example.com", "password": "wrong"})
    assert bad.status_code == 401
    token = response.json()["access_token"]
    with client.app.state.test_session_factory() as session:
        user = session.scalar(select(User).where(User.email == "user@example.com"))
        user.active = False
        session.commit()
    assert client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_market_data_status_is_explicitly_unconfigured(client):
    token = client.post(
        "/auth/register", json={"email": "data@example.com", "password": "a-strong-test-password"}
    ).json()["access_token"]
    result = client.get("/market-data/status", headers={"Authorization": f"Bearer {token}"})
    assert result.status_code == 200
    assert result.headers["cache-control"] == "no-store"
    assert set(result.json()) == {"provider", "configured", "healthy", "detail"}
    assert result.json()["configured"] is False
    assert result.json()["healthy"] is False


def test_market_data_reads_require_app_auth_and_fail_closed_without_upstox_token(client):
    assert client.get("/market-data/option-chain?expiry_date=2026-10-01").status_code == 401
    assert client.get("/market-data/instrument-search?query=RELIANCE").status_code == 401
    token = client.post(
        "/auth/register",
        json={"email": "market@example.com", "password": "a-strong-test-password"},
    ).json()["access_token"]
    result = client.get(
        "/market-data/option-chain?expiry_date=2026-10-01",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert result.status_code == 503
    search = client.get(
        "/market-data/instrument-search?query=RELIANCE",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert search.status_code == 503
    assert client.get(
        "/market-data/instrument-search?query=%20%20%20",
        headers={"Authorization": f"Bearer {token}"},
    ).status_code == 422


def test_authenticated_equity_discovery_returns_normalized_provider_fields(client, monkeypatch):
    token = client.post(
        "/auth/register",
        json={"email": "discovery@example.com", "password": "a-strong-test-password"},
    ).json()["access_token"]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["query"] == "RELIANCE"
        return httpx.Response(200, json={"status": "success", "data": [{
            "instrument_key": "NSE_EQ|INE002A01018", "trading_symbol": "RELIANCE",
            "exchange": "NSE", "segment": "NSE_EQ", "instrument_type": "EQ",
            "name": "Reliance Industries", "lot_size": 1, "tick_size": 0.05,
            "internal_upstream_field": "not-exposed",
        }]})

    provider = UpstoxMarketDataProvider("test-token", transport=httpx.MockTransport(handler))
    monkeypatch.setattr("apps.api.main.get_market_data_provider", lambda: provider)
    response = client.get(
        "/market-data/instrument-search?query=RELIANCE",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200, response.text
    assert response.json()[0]["instrument_key"] == "NSE_EQ|INE002A01018"
    assert "internal_upstream_field" not in response.json()[0]
    assert "test-token" not in response.text


def test_instrument_search_passes_selected_market_segment(client, monkeypatch):
    token = client.post(
        "/auth/register",
        json={"email": "segments@example.com", "password": "a-strong-test-password"},
    ).json()["access_token"]
    seen: list[tuple[str, str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((
            request.url.params["query"],
            request.url.params["exchanges"],
            request.url.params["segments"],
        ))
        return httpx.Response(200, json={"status": "success", "data": []})

    provider = UpstoxMarketDataProvider("test-token", transport=httpx.MockTransport(handler))
    monkeypatch.setattr("apps.api.main.get_market_data_provider", lambda: provider)
    headers = {"Authorization": f"Bearer {token}"}

    for segment in ("EQ", "INDEX", "FUT", "OPT"):
        response = client.get(
            f"/market-data/instrument-search?query=NIFTY&exchange=NSE&segment={segment}",
            headers=headers,
        )
        assert response.status_code == 200, response.text

    assert seen == [
        ("NIFTY", "NSE", "EQ"),
        ("NIFTY", "NSE", "INDEX"),
        ("NIFTY", "NSE", "FUT"),
        ("NIFTY", "NSE", "OPT"),
    ]


def test_admin_provisioning_contract_is_not_public(client):
    # Public registration must reject privileged or unknown fields outright.
    result = client.post(
        "/auth/register",
        json={
            "email": "role@example.com",
            "password": "a-strong-test-password",
            "role": Role.ADMIN.value,
        },
    )
    assert result.status_code == 422
    assert client.post(
        "/auth/login",
        json={"email": "role@example.com", "password": "a-strong-test-password"},
    ).status_code == 401


def test_account_creation_and_sign_in_work_without_exposing_secrets(client):
    credentials = {"email": " Mixed.Case@Example.com ", "password": "a-strong-test-password"}
    registration = client.post("/auth/register", json=credentials)
    assert registration.status_code == 201
    assert registration.headers["cache-control"] == "no-store"
    assert set(registration.json()) == {"access_token", "token_type", "expires_in"}
    assert credentials["password"] not in registration.text

    login = client.post(
        "/auth/login",
        json={"email": "mixed.case@example.com", "password": credentials["password"]},
    )
    assert login.status_code == 200
    assert login.headers["cache-control"] == "no-store"
    token = login.json()["access_token"]
    profile = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert profile.status_code == 200
    assert profile.headers["cache-control"] == "no-store"
    assert profile.json()["email"] == "mixed.case@example.com"
    assert set(profile.json()) == {"id", "email", "role"}


def test_duplicate_registration_and_invalid_auth_inputs_do_not_change_existing_account(client):
    credentials = {"email": "existing@example.com", "password": "a-strong-test-password"}
    assert client.post("/auth/register", json=credentials).status_code == 201
    assert client.post("/auth/register", json=credentials).status_code == 409
    assert client.post(
        "/auth/register", json={"email": "new@example.com", "password": "short"}
    ).status_code == 422
    assert client.post(
        "/auth/login", json={**credentials, "role": "ADMIN"}
    ).status_code == 422
    assert client.post("/auth/login", json=credentials).status_code == 200


def test_active_session_can_renew_without_extending_absolute_lifetime(client):
    original = client.post(
        "/auth/register",
        json={"email": "renew@example.com", "password": "a-strong-test-password"},
    ).json()["access_token"]
    renewed = client.post(
        "/auth/renew", headers={"Authorization": f"Bearer {original}"}
    )
    assert renewed.status_code == 200
    assert renewed.headers["cache-control"] == "no-store"
    token = renewed.json()["access_token"]
    assert client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 200
    secret = get_settings().jwt_secret.get_secret_value()
    first_claims = jwt.decode(original, secret, algorithms=["HS256"])
    renewed_claims = jwt.decode(token, secret, algorithms=["HS256"])
    assert renewed_claims["session_started_at"] == first_claims["session_started_at"]
    assert renewed_claims["exp"] <= (
        first_claims["session_started_at"] + get_settings().session_max_minutes * 60
    )


def test_absolute_session_limit_rejects_old_and_legacy_tokens(client):
    original = client.post(
        "/auth/register",
        json={"email": "old-session@example.com", "password": "a-strong-test-password"},
    ).json()["access_token"]
    secret = get_settings().jwt_secret.get_secret_value()
    claims = jwt.decode(original, secret, algorithms=["HS256"])
    now = datetime.now(timezone.utc)
    old_start = now - timedelta(minutes=get_settings().session_max_minutes + 1)
    old_token = jwt.encode(
        {**claims, "iat": now, "session_started_at": int(old_start.timestamp()),
         "exp": now + timedelta(minutes=5)},
        secret, algorithm="HS256",
    )
    old_headers = {"Authorization": f"Bearer {old_token}"}
    assert client.get("/auth/me", headers=old_headers).status_code == 401
    assert client.post("/auth/renew", headers=old_headers).status_code == 401

    legacy_claims = {
        key: value for key, value in claims.items()
        if key not in {"session_started_at", "sid"}
    }
    legacy_token = jwt.encode(legacy_claims, secret, algorithm="HS256")
    assert client.post(
        "/auth/renew", headers={"Authorization": f"Bearer {legacy_token}"}
    ).status_code == 200


def test_login_throttle_rejects_repeated_attempts_with_retry_after(client, monkeypatch):
    from apps.api.login_throttle import MemoryLoginThrottle

    limiter = MemoryLoginThrottle(
        ip_limit=2, identifier_limit=2, window_seconds=60, secret=b"test-secret"
    )
    monkeypatch.setattr("apps.api.main.get_login_throttle", lambda: limiter)
    credentials = {"email": "throttled@example.com", "password": "wrong-password"}
    assert client.post("/auth/login", json=credentials).status_code == 401
    assert client.post("/auth/login", json=credentials).status_code == 401
    blocked = client.post("/auth/login", json=credentials)
    assert blocked.status_code == 429
    assert blocked.headers["cache-control"] == "no-store"
    assert int(blocked.headers["retry-after"]) >= 1
    assert "throttled@example.com" not in blocked.text


def test_login_fails_closed_when_throttle_backend_is_unavailable(client, monkeypatch):
    from apps.api.login_throttle import LoginThrottleUnavailable

    class Unavailable:
        def consume(self, email, client_address):
            raise LoginThrottleUnavailable("private backend detail")

    monkeypatch.setattr("apps.api.main.get_login_throttle", lambda: Unavailable())
    response = client.post(
        "/auth/login", json={"email": "nobody@example.com", "password": "wrong-password"}
    )
    assert response.status_code == 503
    assert response.headers["cache-control"] == "no-store"
    assert "private backend detail" not in response.text


def test_logout_revokes_only_the_active_session_and_its_renewals(client):
    credentials = {"email": "sessions@example.com", "password": "a-strong-test-password"}
    first = client.post("/auth/register", json=credentials).json()["access_token"]
    second = client.post("/auth/login", json=credentials).json()["access_token"]
    secret = get_settings().jwt_secret.get_secret_value()
    first_sid = jwt.decode(first, secret, algorithms=["HS256"])["sid"]
    second_sid = jwt.decode(second, secret, algorithms=["HS256"])["sid"]
    assert first_sid != second_sid
    renewed = client.post(
        "/auth/renew", headers={"Authorization": f"Bearer {first}"}
    ).json()["access_token"]
    assert jwt.decode(renewed, secret, algorithms=["HS256"])["sid"] == first_sid

    response = client.post("/auth/logout", headers={"Authorization": f"Bearer {first}"})
    assert response.status_code == 204
    assert response.headers["cache-control"] == "no-store"
    for token in (first, renewed):
        headers = {"Authorization": f"Bearer {token}"}
        assert client.get("/auth/me", headers=headers).status_code == 401
        assert client.post("/auth/renew", headers=headers).status_code == 401
    assert client.get(
        "/auth/me", headers={"Authorization": f"Bearer {second}"}
    ).status_code == 200


def test_legacy_token_renews_into_a_revocable_session(client):
    original = client.post(
        "/auth/register",
        json={"email": "legacy-upgrade@example.com", "password": "a-strong-test-password"},
    ).json()["access_token"]
    secret = get_settings().jwt_secret.get_secret_value()
    claims = jwt.decode(original, secret, algorithms=["HS256"])
    legacy_claims = {
        key: value for key, value in claims.items()
        if key not in {"sid", "session_started_at"}
    }
    legacy = jwt.encode(legacy_claims, secret, algorithm="HS256")
    renewed = client.post(
        "/auth/renew", headers={"Authorization": f"Bearer {legacy}"}
    )
    assert renewed.status_code == 200, renewed.text
    new_token = renewed.json()["access_token"]
    assert jwt.decode(new_token, secret, algorithms=["HS256"])["sid"]
    assert client.post(
        "/auth/logout", headers={"Authorization": f"Bearer {new_token}"}
    ).status_code == 204
    assert client.get(
        "/auth/me", headers={"Authorization": f"Bearer {new_token}"}
    ).status_code == 401


def test_registration_throttle_blocks_account_creation_before_hashing(client, monkeypatch):
    from apps.api.login_throttle import MemoryLoginThrottle

    limiter = MemoryLoginThrottle(
        ip_limit=2, identifier_limit=2, window_seconds=60,
        secret=b"test-secret", action="registration",
    )
    monkeypatch.setattr("apps.api.main.get_registration_throttle", lambda: limiter)
    for email in ("first-limit@example.com", "second-limit@example.com"):
        assert client.post(
            "/auth/register", json={"email": email, "password": "a-strong-test-password"}
        ).status_code == 201
    blocked = client.post(
        "/auth/register",
        json={"email": "third-limit@example.com", "password": "a-strong-test-password"},
    )
    assert blocked.status_code == 429
    assert int(blocked.headers["retry-after"]) >= 1
    with client.app.state.test_session_factory() as session:
        assert session.scalar(
            select(User).where(User.email == "third-limit@example.com")
        ) is None


def test_registration_fails_closed_when_limiter_is_unavailable(client, monkeypatch):
    from apps.api.login_throttle import LoginThrottleUnavailable

    class Unavailable:
        def consume(self, email, client_address):
            raise LoginThrottleUnavailable("private backend detail")

    monkeypatch.setattr("apps.api.main.get_registration_throttle", lambda: Unavailable())
    response = client.post(
        "/auth/register",
        json={"email": "registration-down@example.com", "password": "a-strong-test-password"},
    )
    assert response.status_code == 503
    assert "private backend detail" not in response.text
