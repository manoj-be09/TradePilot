"""Provision an administrator interactively on a trusted operator machine."""

from getpass import getpass

from pydantic import EmailStr, TypeAdapter, ValidationError
from sqlalchemy import select

from apps.api.auth import Role, hash_password
from packages.database.models import User
from packages.database.session import build_session_factory


def main() -> None:
    try:
        email = str(TypeAdapter(EmailStr).validate_python(input("Admin email: "))).strip().lower()
    except ValidationError as error:
        raise SystemExit(f"Invalid email: {error}") from error
    password = getpass("Admin password (minimum 12 characters): ")
    try:
        encoded = hash_password(password)
    except ValueError as error:
        raise SystemExit(str(error)) from error
    session = build_session_factory()()
    try:
        if session.scalar(select(User.id).where(User.email == email)):
            raise SystemExit("An account with that email already exists")
        session.add(User(email=email, password_hash=encoded, role=Role.ADMIN.value))
        session.commit()
        print("Administrator account created.")
    finally:
        session.close()


if __name__ == "__main__":
    main()
