from redis.exceptions import ConnectionError

from apps.api.login_throttle import (
    LoginThrottleUnavailable,
    MemoryLoginThrottle,
    RedisLoginThrottle,
)


def test_memory_throttle_bounds_email_across_ips_and_expires():
    now = [100.0]
    limiter = MemoryLoginThrottle(
        ip_limit=3, identifier_limit=2, window_seconds=60,
        secret=b"test-secret", clock=lambda: now[0],
    )
    assert limiter.consume("Person@Example.com", "127.0.0.1") is None
    assert limiter.consume("person@example.com", "127.0.0.2") is None
    assert limiter.consume(" PERSON@example.com ", "127.0.0.3") == 60
    assert all("person@example.com" not in key for key in limiter._counts)
    now[0] += 61
    assert limiter.consume("person@example.com", "127.0.0.3") is None


def test_memory_throttle_bounds_ip_across_emails():
    limiter = MemoryLoginThrottle(
        ip_limit=2, identifier_limit=10, window_seconds=60,
        secret=b"test-secret",
    )
    assert limiter.consume("first@example.com", "127.0.0.1") is None
    assert limiter.consume("second@example.com", "127.0.0.1") is None
    assert limiter.consume("third@example.com", "127.0.0.1") is not None


def test_redis_throttle_uses_atomic_script_and_fails_closed():
    limiter = RedisLoginThrottle(
        redis_url="redis://localhost:6379/0", ip_limit=2,
        identifier_limit=2, window_seconds=60, secret=b"test-secret",
    )

    class FakeRedis:
        def __init__(self):
            self.counts = {}
            self.keys = []

        def eval(self, script, keys_count, key, window_ms):
            assert "INCR" in script and "PEXPIRE" in script
            assert keys_count == 1 and window_ms == "60000"
            self.keys.append(key)
            self.counts[key] = self.counts.get(key, 0) + 1
            return [self.counts[key], 60000]

    fake = FakeRedis()
    limiter.client = fake
    assert limiter.consume("person@example.com", "127.0.0.1") is None
    assert limiter.consume("person@example.com", "127.0.0.1") is None
    assert limiter.consume("person@example.com", "127.0.0.1") == 60
    assert all("person@example.com" not in key for key in fake.keys)

    class BrokenRedis:
        def eval(self, *args):
            raise ConnectionError("service down")

    limiter.client = BrokenRedis()
    try:
        limiter.consume("person@example.com", "127.0.0.1")
    except LoginThrottleUnavailable:
        pass
    else:
        raise AssertionError("A Redis failure must block login")


def test_registration_and_login_counters_are_separate():
    now = [100.0]
    login = MemoryLoginThrottle(
        ip_limit=1, identifier_limit=1, window_seconds=60,
        secret=b"test-secret", action="login", clock=lambda: now[0],
    )
    registration = MemoryLoginThrottle(
        ip_limit=1, identifier_limit=1, window_seconds=60,
        secret=b"test-secret", action="registration", clock=lambda: now[0],
    )
    assert login.consume("same@example.com", "127.0.0.1") is None
    assert registration.consume("same@example.com", "127.0.0.1") is None
    assert login.consume("same@example.com", "127.0.0.1") == 60
    assert registration.consume("same@example.com", "127.0.0.1") == 60
    assert set(login._counts).isdisjoint(registration._counts)
