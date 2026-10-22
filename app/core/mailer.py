"""Minimal SMTP sender. Configure SMTP_HOST / SMTP_PORT / SMTP_USER / SMTP_PASSWORD / MAIL_FROM.
Port 465 = implicit SSL, anything else = STARTTLS. Call from a BackgroundTask so a slow
SMTP server never blocks a request."""
import smtplib
import ssl
from email.message import EmailMessage

from .config import settings


def send_email(to: str, subject: str, body: str) -> None:
    if not settings.smtp_host or not settings.mail_from:
        raise RuntimeError("SMTP is not configured (SMTP_HOST / MAIL_FROM)")
    msg = EmailMessage()
    msg["From"] = settings.mail_from
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    ctx = ssl.create_default_context()
    if settings.smtp_port == 465:
        with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, context=ctx, timeout=15) as smtp:
            if settings.smtp_user:
                smtp.login(settings.smtp_user, settings.smtp_password)
            smtp.send_message(msg)
    else:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as smtp:
            smtp.starttls(context=ctx)
            if settings.smtp_user:
                smtp.login(settings.smtp_user, settings.smtp_password)
            smtp.send_message(msg)