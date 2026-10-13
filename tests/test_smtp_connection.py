
"""QQ SMTP 587 STARTTLS network diagnostics.

Run:
    python -m pytest tests/test_smtp_connection.py -v -s -m network
"""

import smtplib
import socket
import ssl

import pytest


SMTP_HOST = "smtp.qq.com"
SMTP_PORT = 587
TIMEOUT = 15

pytestmark = pytest.mark.network


def test_smtp_tcp_connection():
    """Verify that the SMTP server accepts TCP connections."""
    with socket.create_connection(
        (SMTP_HOST, SMTP_PORT),
        timeout=TIMEOUT,
    ) as sock:
        assert sock is not None


def test_smtp_starttls():
    """Verify SMTP STARTTLS and certificate validation."""
    context = ssl.create_default_context()

    with smtplib.SMTP(
        SMTP_HOST,
        SMTP_PORT,
        timeout=TIMEOUT,
    ) as smtp:
        code, _ = smtp.ehlo()
        assert code == 250

        assert smtp.has_extn("starttls"), (
            "SMTP server does not support STARTTLS"
        )

        code, _ = smtp.starttls(context=context)
        assert code == 220

        assert isinstance(smtp.sock, ssl.SSLSocket)

        code, _ = smtp.ehlo()
        assert code == 250

        code, _ = smtp.noop()
        assert code == 250