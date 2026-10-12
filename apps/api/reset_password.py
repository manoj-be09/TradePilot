"""Reset a local account password from a trusted operator terminal."""

from getpass import getpass

from pydantic import EmailStr, TypeAdapter, ValidationError
from sqlalchemy import select

from apps.api.auth import hash_password
from apps.api.settings import get_settings
from packages.database.models import AuditEvent, User
from packages.database.session import build_session_factory


def main() -> None:
    try:
        email = str(TypeAdapter(EmailStr).validate_python(input("Account email: "))).strip().lower()
    except ValidationError as error:
        raise SystemExit(f"Invalid email: {error}") from error

    password = getpass("New password (minimum 12 characters): ")
    confirmation = getpass("Confirm new password: ")
    if password != confirmation:
        raise SystemExit("Passwords did not match")
    try:
        password_hash = hash_password(password)
    except ValueError as error:
        raise SystemExit(str(error)) from error

    database_url = get_settings().database_url
    if not database_url.startswith("sqlite:"):
        raise SystemExit("This helper only resets accounts in a local SQLite database")
    session = build_session_factory(database_url)()
    try:
        user = session.scalar(select(User).where(User.email == email))
        if user is None:
            raise SystemExit("No account with that email was found in the configured database")
        if not user.active:
            raise SystemExit("That account is inactive; ask an administrator to restore access")

        user.password_hash = password_hash
        session.add(
            AuditEvent(
                actor_user_id=user.id,
                event_type="auth.password.reset",
                details={"method": "local_cli"},
            )
        )
        session.commit()
        print("Password updated. Sign in with the new password.")
    finally:
        session.close()


if __name__ == "__main__":
    main()
