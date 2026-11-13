from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage

from ..fields import Field
from .base import SendResult, Transport, TransportError, register


@register
class SmtpTransport(Transport):
    type = "smtp"
    label = "SMTP"
    description = "Deliver through any SMTP server (Gmail, Microsoft 365, Postfix, Mailgun, SES...)."
    fields = [
        Field("host", "Server", required=True, placeholder="smtp.example.com"),
        Field("port", "Port", type="number", default=587, min=1, max=65535),
        Field(
            "security",
            "Encryption",
            type="select",
            default="starttls",
            options=[("starttls", "STARTTLS (usually port 587)"), ("ssl", "SSL/TLS (usually port 465)"), ("none", "None")],
        ),
        Field("username", "Username", placeholder="Leave empty if the server needs no login"),
        Field("password", "Password", type="password", secret=True, help="Stored encrypted. Leave empty to keep the saved one."),
        Field("timeout", "Timeout (seconds)", type="number", default=30, min=1, max=300),
        Field(
            "verify_tls",
            "Verify TLS certificate",
            type="bool",
            default=True,
            help="Turn off only for servers with self-signed certificates.",
        ),
    ]

    def _tls_context(self) -> ssl.SSLContext:
        context = ssl.create_default_context()
        if not self.config.get("verify_tls", True):
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        return context

    def _connect(self) -> smtplib.SMTP:
        host = self.config["host"]
        port = int(self.config.get("port") or 587)
        timeout = int(self.config.get("timeout") or 30)
        security = self.config.get("security") or "starttls"
        try:
            if security == "ssl":
                server: smtplib.SMTP = smtplib.SMTP_SSL(host, port, timeout=timeout, context=self._tls_context())
            else:
                server = smtplib.SMTP(host, port, timeout=timeout)
            server.ehlo()
            if security == "starttls":
                server.starttls(context=self._tls_context())
                server.ehlo()
            username = self.config.get("username")
            if username:
                server.login(username, self.config.get("password") or "")
        except smtplib.SMTPAuthenticationError as exc:
            raise TransportError(f"Authentication failed: {exc.smtp_code} {_decode(exc.smtp_error)}") from exc
        except smtplib.SMTPNotSupportedError as exc:
            raise TransportError(f"The server does not support this option: {exc}") from exc
        except (OSError, smtplib.SMTPException) as exc:
            raise TransportError(f"Could not connect to {host}:{port}: {exc}") from exc
        return server

    def send(self, message: EmailMessage, *, envelope_from: str, recipients: list[str]) -> SendResult:
        server = self._connect()
        try:
            refused = server.send_message(message, from_addr=envelope_from, to_addrs=recipients)
        except smtplib.SMTPRecipientsRefused as exc:
            details = "; ".join(f"{rcpt}: {code} {_decode(msg)}" for rcpt, (code, msg) in exc.recipients.items())
            raise TransportError(f"All recipients were refused: {details}") from exc
        except smtplib.SMTPSenderRefused as exc:
            raise TransportError(f"Sender refused: {exc.smtp_code} {_decode(exc.smtp_error)}") from exc
        except smtplib.SMTPDataError as exc:
            raise TransportError(f"Message refused: {exc.smtp_code} {_decode(exc.smtp_error)}") from exc
        except (OSError, smtplib.SMTPException) as exc:
            raise TransportError(f"SMTP error: {exc}") from exc
        finally:
            try:
                server.quit()
            except Exception:  # noqa: BLE001 - best effort
                server.close()
        warnings = [f"Recipient refused: {rcpt} ({code} {_decode(msg)})" for rcpt, (code, msg) in (refused or {}).items()]
        accepted = len(recipients) - len(refused or {})
        return SendResult(detail=f"Accepted by {self.config['host']} for {accepted} recipient(s).", warnings=warnings)

    def test(self) -> str:
        server = self._connect()
        try:
            server.noop()
        finally:
            try:
                server.quit()
            except Exception:  # noqa: BLE001
                server.close()
        who = f" as {self.config['username']}" if self.config.get("username") else ""
        return f"Connected to {self.config['host']}:{self.config.get('port') or 587}{who}."


def _decode(value: bytes | str) -> str:
    return value.decode(errors="replace") if isinstance(value, bytes) else str(value)
