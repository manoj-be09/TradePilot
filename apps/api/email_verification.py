"""Sign-up email verification: one-time link tokens, stored only as hashes, sent by SMTP."""

import hashlib
import logging
import secrets
import smtplib
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from urllib.parse import quote

from sqlalchemy import select
from sqlalchemy.orm import Session

from apps.api.settings import Settings
from packages.database.models import EmailVerification, User

logger = logging.getLogger(__name__)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def is_verified(session: Session, user: User) -> bool:
    """A user with no verification row signed up before the check existed."""
    row = session.get(EmailVerification, user.id)
    return row is None or row.verified_at is not None


def issue_token(session: Session, user: User, settings: Settings) -> str:
    """A fresh link token for this user; any earlier link stops working. Caller commits."""
    token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(hours=settings.email_verification_hours)
    row = session.get(EmailVerification, user.id)
    if row is None:
        session.add(EmailVerification(user_id=user.id, token_hash=_hash(token),
                                      expires_at=expires))
    else:
        row.token_hash, row.expires_at, row.sent_at = _hash(token), expires, None
    return token


def redeem(session: Session, token: str) -> User | None:
    """The user a valid, unexpired, unused token belongs to, now marked verified."""
    row = session.scalar(select(EmailVerification).where(
        EmailVerification.token_hash == _hash(token)).with_for_update())
    if row is None or row.verified_at is not None:
        return None
    expires = row.expires_at if row.expires_at.tzinfo else row.expires_at.replace(
        tzinfo=timezone.utc)
    if expires <= datetime.now(timezone.utc):
        return None
    row.verified_at = datetime.now(timezone.utc)
    return session.get(User, row.user_id)


def send_link(settings: Settings, to: str, token: str) -> bool:
    """Send the verification link; False (logged without details) when delivery fails."""
    if not (settings.smtp_host and settings.smtp_from and settings.public_web_url):
        return False
    link = f"{settings.public_web_url.rstrip('/')}/?verify={quote(token)}"
    message = EmailMessage()
    message["Subject"] = "Confirm your TradePilot email"
    message["From"] = settings.smtp_from
    message["To"] = to
    hours = settings.email_verification_hours
    message.set_content("\n".join([
        "Confirm this email address to finish creating your TradePilot account:",
        "",
        link,
        "",
        f"The link works once and expires in {hours} hours. "
        "If you did not sign up, ignore this email.",
    ]))
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as smtp:
            if settings.smtp_starttls:
                smtp.starttls()
            if settings.smtp_username and settings.smtp_password:
                smtp.login(settings.smtp_username, settings.smtp_password.get_secret_value())
            smtp.send_message(message)
    except (OSError, smtplib.SMTPException) as error:
        logger.warning("Verification email failed: %s", type(error).__name__)
        return False
    return True
