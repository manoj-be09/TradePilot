from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Annotated
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pwdlib import PasswordHash
from sqlalchemy import select
from sqlalchemy.orm import Session

from apps.api.settings import get_settings
from packages.database.models import AuthSession, User
from packages.database.session import get_session


class Role(StrEnum):
    ADMIN = "ADMIN"
    TRADER = "TRADER"
    VIEWER = "VIEWER"


password_hash = PasswordHash.recommended()
bearer_scheme = HTTPBearer(auto_error=False)
SessionDep = Annotated[Session, Depends(get_session)]
_DUMMY_HASH = password_hash.hash("not-a-real-password-used-for-timing-equalization")


def hash_password(password: str) -> str:
    if len(password) < 12:
        raise ValueError("Password must contain at least 12 characters")
    return password_hash.hash(password)


def verify_password(password: str, encoded_hash: str) -> bool:
    try:
        return password_hash.verify(password, encoded_hash)
    except (ValueError, TypeError):
        return False


def authenticate(session: Session, email: str, password: str) -> User | None:
    normalized = email.strip().lower()
    user = session.scalar(select(User).where(User.email == normalized))
    checked_hash = user.password_hash if user else _DUMMY_HASH
    valid = verify_password(password, checked_hash)
    if not user or not user.active or not valid:
        return None
    return user


def create_access_token(
    user: User, *, session_started_at: datetime | None = None, session_id: UUID | None = None
) -> tuple[str, int]:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    started = session_started_at or now
    if started.tzinfo is None:
        raise ValueError("Session start must include a timezone")
    started = started.astimezone(timezone.utc)
    absolute_end = started + timedelta(minutes=settings.session_max_minutes)
    if started > now + timedelta(seconds=5) or absolute_end <= now:
        raise ValueError("Session lifetime has ended")
    expires_at = min(now + timedelta(minutes=settings.access_token_minutes), absolute_end)
    claims = {
        "sub": str(user.id),
        "role": user.role,
        "iat": now,
        "session_started_at": int(started.timestamp()),
        "exp": expires_at,
    }
    if session_id is not None:
        claims["sid"] = str(session_id)
    token = jwt.encode(claims, settings.jwt_secret.get_secret_value(), algorithm="HS256")
    return token, max(1, int((expires_at - now).total_seconds()))


def session_start_from_token(token: str) -> datetime:
    try:
        claims = jwt.decode(
            token, get_settings().jwt_secret.get_secret_value(), algorithms=["HS256"]
        )
        raw_start = claims.get("session_started_at", claims["iat"])
        if type(raw_start) is not int:
            raise ValueError("Invalid session timestamp")
        started = datetime.fromtimestamp(raw_start, timezone.utc)
    except (jwt.InvalidTokenError, KeyError, OverflowError, ValueError) as error:
        raise ValueError("Invalid or expired session") from error
    if datetime.now(timezone.utc) >= started + timedelta(
        minutes=get_settings().session_max_minutes
    ):
        raise ValueError("Session lifetime has ended")
    return started


def session_id_from_token(token: str) -> UUID | None:
    try:
        claims = jwt.decode(
            token, get_settings().jwt_secret.get_secret_value(), algorithms=["HS256"]
        )
        raw_id = claims.get("sid")
        return UUID(raw_id) if raw_id is not None else None
    except (jwt.InvalidTokenError, TypeError, ValueError) as error:
        raise ValueError("Invalid or expired session") from error


def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    session: SessionDep,
) -> User:
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if credentials is None:
        raise unauthorized
    try:
        claims = jwt.decode(
            credentials.credentials,
            get_settings().jwt_secret.get_secret_value(),
            algorithms=["HS256"],
        )
        user_id = UUID(claims["sub"])
        started = session_start_from_token(credentials.credentials)
        raw_session_id = claims.get("sid")
        session_id = UUID(raw_session_id) if raw_session_id is not None else None
    except (jwt.InvalidTokenError, KeyError, TypeError, ValueError):
        raise unauthorized from None
    if session_id is not None:
        auth_session = session.scalar(
            select(AuthSession).where(
                AuthSession.id == session_id, AuthSession.user_id == user_id
            )
        )
        if auth_session is None or auth_session.revoked_at is not None:
            raise unauthorized
        database_start = auth_session.started_at
        database_end = auth_session.absolute_expires_at
        if database_start.tzinfo is None:
            database_start = database_start.replace(tzinfo=timezone.utc)
        if database_end.tzinfo is None:
            database_end = database_end.replace(tzinfo=timezone.utc)
        if (
            int(database_start.timestamp()) != int(started.timestamp())
            or datetime.now(timezone.utc) >= database_end
        ):
            raise unauthorized
    user = session.scalar(select(User).where(User.id == user_id))
    if user is None or not user.active:
        raise unauthorized
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_roles(*roles: Role) -> Callable[..., User]:
    def dependency(user: CurrentUser) -> User:
        if Role(user.role) not in roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient role")
        return user

    return dependency
