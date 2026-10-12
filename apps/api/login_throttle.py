"""Bound authentication attempts without storing addresses in throttle keys."""

import hashlib
import hmac
import math
import time
from collections.abc import Callable
from functools import lru_cache
from threading import Lock
from typing import Protocol

import redis
from redis.exceptions import RedisError

from apps.api.settings import get_settings

_INCREMENT_WINDOW = """
local count = redis.call('INCR', KEYS[1])
if count == 1 then
    redis.call('PEXPIRE', KEYS[1], ARGV[1])
end
return {count, redis.call('PTTL', KEYS[1])}
"""


class LoginThrottleUnavailable(RuntimeError):
    """The deployed authentication throttle is unavailable; fail closed."""


class LoginThrottle(Protocol):
    def consume(self, email: str, client_address: str) -> int | None: ...

    def check_health(self) -> None: ...


def _key(action: str, scope: str, value: str, secret: bytes) -> str:
    digest = hmac.new(secret, value.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"auth:{action}:{scope}:{digest}"


class MemoryLoginThrottle:
    """Development-only limiter; process-local state is unsuitable for deployment."""

    def __init__(
        self,
        *,
        ip_limit: int,
        identifier_limit: int,
        window_seconds: int,
        secret: bytes,
        action: str = "login",
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.ip_limit = ip_limit
        self.identifier_limit = identifier_limit
        self.window_seconds = window_seconds
        self.secret = secret
        self.action = action
        self.clock = clock
        self._lock = Lock()
        self._counts: dict[str, tuple[int, float]] = {}

    def check_health(self) -> None:
        """The development limiter has no external dependency."""

    def consume(self, email: str, client_address: str) -> int | None:
        keys = (
            (_key(self.action, "ip", client_address, self.secret), self.ip_limit),
            (_key(self.action, "id", email.strip().lower(), self.secret), self.identifier_limit),
        )
        with self._lock:
            now = self.clock()
            self._counts = {
                key: item for key, item in self._counts.items() if item[1] > now
            }
            retry_after = 0
            for key, limit in keys:
                count, expires_at = self._counts.get(key, (0, now + self.window_seconds))
                count += 1
                self._counts[key] = (count, expires_at)
                if count > limit:
                    retry_after = max(retry_after, math.ceil(expires_at - now))
            return retry_after or None


class RedisLoginThrottle:
    """Atomic per-key fixed windows shared by API replicas."""

    def __init__(
        self,
        *,
        redis_url: str,
        ip_limit: int,
        identifier_limit: int,
        window_seconds: int,
        secret: bytes,
        action: str = "login",
    ) -> None:
        try:
            self.client = redis.Redis.from_url(
                redis_url, decode_responses=True, socket_connect_timeout=0.5,
                socket_timeout=0.5,
            )
        except (RedisError, ValueError) as error:
            raise LoginThrottleUnavailable(
                "Authentication rate-limit service unavailable"
            ) from error
        self.ip_limit = ip_limit
        self.identifier_limit = identifier_limit
        self.window_seconds = window_seconds
        self.secret = secret
        self.action = action

    def check_health(self) -> None:
        try:
            if not self.client.ping():
                raise LoginThrottleUnavailable("Authentication rate-limit service unavailable")
        except (RedisError, OSError) as error:
            raise LoginThrottleUnavailable(
                "Authentication rate-limit service unavailable"
            ) from error

    def consume(self, email: str, client_address: str) -> int | None:
        keys = (
            (_key(self.action, "ip", client_address, self.secret), self.ip_limit),
            (_key(self.action, "id", email.strip().lower(), self.secret), self.identifier_limit),
        )
        retry_after = 0
        try:
            for key, limit in keys:
                result: object = self.client.eval(
                    _INCREMENT_WINDOW, 1, key, str(self.window_seconds * 1000)
                )
                if not isinstance(result, (list, tuple)) or len(result) != 2:
                    raise LoginThrottleUnavailable("Invalid authentication rate-limit response")
                count, ttl_ms = int(result[0]), int(result[1])
                if count < 1 or ttl_ms < 0:
                    raise LoginThrottleUnavailable("Invalid login rate-limit response")
                if count > limit:
                    retry_after = max(retry_after, max(1, math.ceil(ttl_ms / 1000)))
        except (RedisError, OSError, TypeError, ValueError) as error:
            raise LoginThrottleUnavailable("Login rate-limit service unavailable") from error
        return retry_after or None


@lru_cache
def get_login_throttle() -> LoginThrottle:
    settings = get_settings()
    secret = settings.jwt_secret.get_secret_value().encode("utf-8")
    if settings.environment.lower() == "development":
        return MemoryLoginThrottle(
            ip_limit=settings.login_ip_attempts,
            identifier_limit=settings.login_identifier_attempts,
            window_seconds=settings.login_window_seconds,
            secret=secret,
        )
    return RedisLoginThrottle(
        redis_url=settings.redis_url,
        ip_limit=settings.login_ip_attempts,
        identifier_limit=settings.login_identifier_attempts,
        window_seconds=settings.login_window_seconds,
        secret=secret,
    )


@lru_cache
def get_registration_throttle() -> LoginThrottle:
    settings = get_settings()
    secret = settings.jwt_secret.get_secret_value().encode("utf-8")
    if settings.environment.lower() == "development":
        return MemoryLoginThrottle(
            ip_limit=settings.registration_ip_attempts,
            identifier_limit=settings.registration_identifier_attempts,
            window_seconds=settings.registration_window_seconds,
            secret=secret, action="registration",
        )
    return RedisLoginThrottle(
        redis_url=settings.redis_url,
        ip_limit=settings.registration_ip_attempts,
        identifier_limit=settings.registration_identifier_attempts,
        window_seconds=settings.registration_window_seconds,
        secret=secret, action="registration",
    )
