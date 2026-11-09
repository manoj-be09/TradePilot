"""The verification email carries the one-time link; a failed delivery returns False."""

import smtplib

from apps.api.email_verification import send_link
from apps.api.settings import Settings


class _SMTP:
    sent: list = []

    def __init__(self, host, port, timeout):
        self.calls = [("connect", host, port, timeout)]

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def starttls(self):
        self.calls.append(("starttls",))

    def login(self, user, password):
        self.calls.append(("login", user, password))

    def send_message(self, message):
        _SMTP.sent.append((self.calls, message))


def _settings(**changes):
    values = dict(smtp_host="smtp.example.test", smtp_from="no-reply@example.test",
                  public_web_url="https://app.example.test/", smtp_username="u",
                  smtp_password="p") | changes
    return Settings(_env_file=None, **values)


def test_the_link_points_at_the_web_app_over_starttls(monkeypatch):
    monkeypatch.setattr(smtplib, "SMTP", _SMTP)
    _SMTP.sent.clear()
    assert send_link(_settings(), "a@example.com", "tok+en/1") is True
    (calls, message), = _SMTP.sent
    assert [call[0] for call in calls] == ["connect", "starttls", "login"]
    assert message["To"] == "a@example.com"
    assert "https://app.example.test/?verify=tok%2Ben/1" in message.get_content()


def test_a_delivery_failure_returns_false(monkeypatch):
    def broken(*_args, **_kwargs):
        raise smtplib.SMTPConnectError(421, "down")

    monkeypatch.setattr(smtplib, "SMTP", broken)
    assert send_link(_settings(), "a@example.com", "token") is False
    assert send_link(_settings(smtp_host=None), "a@example.com", "token") is False
