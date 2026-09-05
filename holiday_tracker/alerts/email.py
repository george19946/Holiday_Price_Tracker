"""Sends the fits-budget alert email via SMTP.

Credentials come from environment variables (SMTP_HOST, SMTP_PORT,
SMTP_USER, SMTP_PASS, ALERT_FROM, ALERT_TO) so the same code runs
identically locally and in the scheduled GitHub Action (phase 7), whose
secrets populate these same names. SmtpConfig.from_env() returns None if
the configuration is incomplete -- callers should treat that as "alerting
isn't configured", not an error, since not every user wants email alerts.
"""

from __future__ import annotations

import html as html_module
import os
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage

from holiday_tracker.report import city_label
from holiday_tracker.store.repo import PackageRecord


@dataclass(frozen=True)
class SmtpConfig:
    host: str
    port: int
    username: str
    password: str
    sender: str
    recipient: str

    @classmethod
    def from_env(cls) -> SmtpConfig | None:
        host = os.environ.get("SMTP_HOST")
        username = os.environ.get("SMTP_USER")
        password = os.environ.get("SMTP_PASS")
        recipient = os.environ.get("ALERT_TO")
        if not (host and username and password and recipient):
            return None
        return cls(
            host=host,
            port=int(os.environ.get("SMTP_PORT", "587")),
            username=username,
            password=password,
            sender=os.environ.get("ALERT_FROM", username),
            recipient=recipient,
        )


def _stay_lines(package: PackageRecord) -> tuple[str, str]:
    """(plaintext_line, html_line) describing the package's stay, or a
    pair of empty strings if there isn't one."""
    if package.stay is None:
        return "", ""
    stay = package.stay
    plain = (
        f"Stay: {stay.property_type}, rating {stay.rating}, {stay.distance_km} km from centre "
        f"(observed {stay.observed_at:%Y-%m-%d}, source: {stay.source})"
    )
    html_line = html_module.escape(plain)
    return plain, html_line


def render_alert_email(watch_name: str, package: PackageRecord) -> tuple[str, str, str]:
    """Returns (subject, plaintext_body, html_body)."""
    destination = city_label(package.destination_city_id)
    safe_watch_name = html_module.escape(watch_name)
    subject = f"Holiday Price Tracker: {destination} now fits your budget ({package.total_cost})"
    stay_plain, stay_html = _stay_lines(package)

    plaintext = (
        f'Your watch "{watch_name}" now has a package within budget.\n\n'
        f"{destination}: {package.depart_date} -> {package.return_date} "
        f"({package.nights} nights)\n"
        f"Flights: {package.flights_cost}\n"
        f"Accommodation: {package.accommodation_cost}\n"
        f"Estimated spend: {package.spend_cost}\n"
        f"Total: {package.total_cost}\n\n"
        f"{stay_plain + chr(10) if stay_plain else ''}"
        "Prices are cached/indicative (Travelpayouts data), not live-bookable "
        "-- verify before booking.\n"
    )

    html = f"""\
<p>Your watch <strong>{safe_watch_name}</strong> now has a package within budget.</p>
<table cellpadding="4" style="border-collapse:collapse">
  <tr><td><strong>Destination</strong></td><td>{html_module.escape(destination)}</td></tr>
  <tr><td><strong>Dates</strong></td><td>{package.depart_date} &rarr; {package.return_date}
      ({package.nights} nights)</td></tr>
  <tr><td>Flights</td><td>{package.flights_cost}</td></tr>
  <tr><td>Accommodation</td><td>{package.accommodation_cost}</td></tr>
  <tr><td>Estimated spend</td><td>{package.spend_cost}</td></tr>
  <tr><td><strong>Total</strong></td><td><strong>{package.total_cost}</strong></td></tr>
</table>
{f"<p>{stay_html}</p>" if stay_html else ""}
<p style="color:#777;font-size:0.85em">Prices are cached/indicative (Travelpayouts data),
not live-bookable -- verify before booking.</p>
"""
    return subject, plaintext, html


def send_alert_email(config: SmtpConfig, watch_name: str, package: PackageRecord) -> None:
    subject, plaintext, html = render_alert_email(watch_name, package)
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = config.sender
    message["To"] = config.recipient
    message.set_content(plaintext)
    message.add_alternative(html, subtype="html")

    with smtplib.SMTP(config.host, config.port) as smtp:
        smtp.starttls()
        smtp.login(config.username, config.password)
        smtp.send_message(message)
