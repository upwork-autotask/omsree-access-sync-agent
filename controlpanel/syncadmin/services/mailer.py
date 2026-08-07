"""SMTP email alerts, configured from AgentSettings (not Django settings.py)."""

from __future__ import annotations

import smtplib
from email.message import EmailMessage


def _send(settings, subject: str, body: str) -> None:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = settings.alert_from or settings.smtp_user
    msg["To"] = ", ".join(settings.recipient_list)
    msg.set_content(body)

    if settings.smtp_use_tls:
        server = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20)
        server.starttls()
    else:
        server = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20)
    try:
        if settings.smtp_user:
            server.login(settings.smtp_user, settings.smtp_password)
        server.send_message(msg)
    finally:
        server.quit()


def send_test(settings) -> tuple[bool, str]:
    if not settings.recipient_list:
        return False, "No alert recipients set."
    try:
        _send(settings, "OmSree Sync Agent — test email", "This is a test alert. SMTP works.")
        return True, f"Sent to {', '.join(settings.recipient_list)}."
    except Exception as exc:
        return False, str(exc)


def send_alert(settings, subject: str, body: str) -> None:
    """Best-effort + throttled: alerts must never crash a sync run, and must not spam.

    Skips if an alert was already sent within `alert_throttle_minutes` (anti-spam),
    so a failure recurring every few minutes emails at most once per window.
    """
    if not settings.alerts_enabled or not settings.recipient_list:
        return
    from django.utils import timezone
    now = timezone.now()
    throttle = getattr(settings, "alert_throttle_minutes", 0) or 0
    last = getattr(settings, "last_alert_at", None)
    if throttle and last and (now - last).total_seconds() < throttle * 60:
        return  # within the throttle window
    try:
        _send(settings, subject, body)
        try:
            settings.last_alert_at = now
            settings.save(update_fields=["last_alert_at"])
        except Exception:
            pass
    except Exception:
        pass
