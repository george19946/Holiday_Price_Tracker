"""Tests for alerts/rules.py (the fits-budget/cooldown decision) and
alerts/email.py (rendering and SMTP config), without ever sending a real
email -- send_alert_email itself is exercised against a fake SMTP server
class, never a real network connection.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from holiday_tracker.alerts.email import SmtpConfig, render_alert_email, send_alert_email
from holiday_tracker.alerts.rules import DEFAULT_COOLDOWN, decide, package_fingerprint, record_sent
from holiday_tracker.models import Money, StayQuote
from holiday_tracker.store import db


def _package(total=450, fits=True, destination="barcelona", stay=None, **overrides):
    from holiday_tracker.store.repo import PackageRecord

    defaults = dict(
        destination_city_id=destination,
        origin="LHR",
        depart_date=date(2027, 3, 4),
        return_date=date(2027, 3, 7),
        nights=3,
        flights_cost=Money.from_major(200, "GBP"),
        accommodation_cost=Money.from_major(150, "GBP"),
        spend_cost=Money.from_major(100, "GBP"),
        total_cost=Money.from_major(total, "GBP"),
        fits_budget=fits,
        stay=stay,
    )
    defaults.update(overrides)
    return PackageRecord(**defaults)


def _stay(**overrides) -> StayQuote:
    defaults = dict(
        city_id="barcelona",
        check_in=date(2027, 3, 4),
        check_out=date(2027, 3, 7),
        nightly_rate=Money.from_major(50, "GBP"),
        property_type="hotel",
        rating=8.0,
        distance_km=1.0,
        free_cancellation=True,
        observed_at=datetime(2026, 1, 1, tzinfo=UTC),
        source="fixtures",
    )
    defaults.update(overrides)
    return StayQuote(**defaults)


@pytest.fixture
def conn():
    connection = db.get_connection(":memory:")
    yield connection
    connection.close()


class TestPackageFingerprint:
    def test_same_package_same_fingerprint(self):
        a = _package(total=450.00)
        b = _package(total=450.49)  # rounds to the same whole-unit total
        assert package_fingerprint(a) == package_fingerprint(b)

    def test_different_total_different_fingerprint(self):
        a = _package(total=450)
        b = _package(total=460)
        assert package_fingerprint(a) != package_fingerprint(b)

    def test_different_destination_different_fingerprint(self):
        a = _package(destination="barcelona")
        b = _package(destination="valencia")
        assert package_fingerprint(a) != package_fingerprint(b)

    def test_different_dates_different_fingerprint(self):
        a = _package()
        b = _package(depart_date=date(2027, 4, 1), return_date=date(2027, 4, 4))
        assert package_fingerprint(a) != package_fingerprint(b)


class TestDecide:
    def test_no_package_does_not_alert(self, conn):
        decision = decide(conn, "watch-1", None)
        assert decision.should_send is False
        assert "no candidates" in decision.reason

    def test_over_budget_package_does_not_alert(self, conn):
        decision = decide(conn, "watch-1", _package(fits=False))
        assert decision.should_send is False
        assert "over budget" in decision.reason

    def test_first_time_fitting_package_alerts(self, conn):
        decision = decide(conn, "watch-1", _package(fits=True))
        assert decision.should_send is True
        assert decision.fingerprint is not None

    def test_same_fingerprint_within_cooldown_does_not_realert(self, conn):
        package = _package(fits=True)
        now = datetime(2026, 6, 1, 12, 0, 0)
        fingerprint = package_fingerprint(package)
        record_sent(conn, "watch-1", fingerprint, now=now)

        decision = decide(conn, "watch-1", package, now=now + timedelta(hours=1))
        assert decision.should_send is False
        assert "cooldown" in decision.reason

    def test_same_fingerprint_after_cooldown_realerts(self, conn):
        package = _package(fits=True)
        now = datetime(2026, 6, 1, 12, 0, 0)
        fingerprint = package_fingerprint(package)
        record_sent(conn, "watch-1", fingerprint, now=now)

        decision = decide(conn, "watch-1", package, now=now + DEFAULT_COOLDOWN + timedelta(seconds=1))
        assert decision.should_send is True

    def test_a_different_fitting_package_alerts_immediately_despite_cooldown(self, conn):
        first = _package(fits=True, total=450)
        now = datetime(2026, 6, 1, 12, 0, 0)
        record_sent(conn, "watch-1", package_fingerprint(first), now=now)

        different = _package(fits=True, total=300)  # a materially cheaper/different package
        decision = decide(conn, "watch-1", different, now=now + timedelta(minutes=5))
        assert decision.should_send is True

    def test_alerts_are_scoped_per_watch(self, conn):
        package = _package(fits=True)
        now = datetime(2026, 6, 1, 12, 0, 0)
        record_sent(conn, "watch-1", package_fingerprint(package), now=now)

        decision = decide(conn, "watch-2", package, now=now + timedelta(minutes=1))
        assert decision.should_send is True


class TestSmtpConfig:
    def test_from_env_returns_none_when_incomplete(self, monkeypatch):
        monkeypatch.delenv("SMTP_HOST", raising=False)
        monkeypatch.delenv("SMTP_USER", raising=False)
        monkeypatch.delenv("SMTP_PASS", raising=False)
        monkeypatch.delenv("ALERT_TO", raising=False)
        assert SmtpConfig.from_env() is None

    def test_from_env_builds_config_when_complete(self, monkeypatch):
        monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
        monkeypatch.setenv("SMTP_USER", "user@example.com")
        monkeypatch.setenv("SMTP_PASS", "secret")
        monkeypatch.setenv("ALERT_TO", "me@example.com")
        monkeypatch.delenv("SMTP_PORT", raising=False)
        monkeypatch.delenv("ALERT_FROM", raising=False)

        config = SmtpConfig.from_env()
        assert config is not None
        assert config.host == "smtp.example.com"
        assert config.port == 587
        assert config.sender == "user@example.com"  # falls back to SMTP_USER
        assert config.recipient == "me@example.com"

    def test_from_env_respects_port_and_sender_overrides(self, monkeypatch):
        monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
        monkeypatch.setenv("SMTP_USER", "user@example.com")
        monkeypatch.setenv("SMTP_PASS", "secret")
        monkeypatch.setenv("ALERT_TO", "me@example.com")
        monkeypatch.setenv("SMTP_PORT", "2525")
        monkeypatch.setenv("ALERT_FROM", "alerts@example.com")

        config = SmtpConfig.from_env()
        assert config.port == 2525
        assert config.sender == "alerts@example.com"


class TestRenderAlertEmail:
    def test_subject_mentions_destination_and_total(self):
        package = _package(destination="barcelona", total=450)
        subject, _, _ = render_alert_email("my-trip", package)
        assert "Barcelona" in subject
        assert "£450.00" in subject

    def test_plaintext_and_html_include_cost_breakdown(self):
        package = _package()
        _, plaintext, html = render_alert_email("my-trip", package)
        for body in (plaintext, html):
            assert "£200.00" in body  # flights
            assert "£150.00" in body  # accommodation
            assert "£100.00" in body  # spend
            assert "verify before booking" in body

    def test_stay_details_included_when_present(self):
        package = _package(stay=_stay(property_type="hotel", rating=8.5))
        _, plaintext, html = render_alert_email("my-trip", package)
        assert "hotel" in plaintext
        assert "8.5" in plaintext
        assert "hotel" in html

    def test_no_stay_details_when_absent(self):
        package = _package(stay=None)
        _, plaintext, _ = render_alert_email("my-trip", package)
        assert "Stay:" not in plaintext

    def test_watch_name_is_html_escaped(self):
        package = _package()
        _, _, html = render_alert_email("<script>alert(1)</script>", package)
        assert "<script>alert(1)</script>" not in html
        assert "&lt;script&gt;" in html


class _FakeSmtp:
    """A stand-in for smtplib.SMTP that records what would have been sent
    instead of opening a real network connection."""

    instances: list[_FakeSmtp] = []

    def __init__(self, host, port):
        self.host = host
        self.port = port
        self.started_tls = False
        self.logged_in = None
        self.sent_message = None
        _FakeSmtp.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def starttls(self):
        self.started_tls = True

    def login(self, username, password):
        self.logged_in = (username, password)

    def send_message(self, message):
        self.sent_message = message


class TestSendAlertEmail:
    def test_sends_via_smtp_with_tls_and_login(self, monkeypatch):
        _FakeSmtp.instances.clear()
        monkeypatch.setattr("holiday_tracker.alerts.email.smtplib.SMTP", _FakeSmtp)

        config = SmtpConfig(
            host="smtp.example.com", port=587, username="user", password="pw",
            sender="alerts@example.com", recipient="me@example.com",
        )
        send_alert_email(config, "my-trip", _package())

        assert len(_FakeSmtp.instances) == 1
        fake = _FakeSmtp.instances[0]
        assert fake.host == "smtp.example.com"
        assert fake.started_tls is True
        assert fake.logged_in == ("user", "pw")
        assert fake.sent_message["To"] == "me@example.com"
        assert fake.sent_message["From"] == "alerts@example.com"
