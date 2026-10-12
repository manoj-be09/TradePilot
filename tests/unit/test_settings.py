import pytest
from pydantic import ValidationError

from apps.api.settings import Settings


def test_production_rejects_development_jwt_secret():
    with pytest.raises(ValidationError):
        Settings(environment="production", jwt_secret="short-development-secret")


def test_production_rejects_example_placeholder_secret():
    with pytest.raises(ValidationError):
        Settings(
            environment="production", jwt_secret="replace-with-a-random-secret-before-deployment"
        )


def test_production_accepts_long_configured_jwt_secret():
    settings = Settings(
        environment="production", jwt_secret="a-unique-secret-value-long-enough-for-deployment"
    )
    assert settings.jwt_secret.get_secret_value().startswith("a-unique")
