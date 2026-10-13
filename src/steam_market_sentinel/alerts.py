"""Windows/Qt desktop alerts live in the GUI; email and OneBot run in worker threads."""
from __future__ import annotations

from email.message import EmailMessage
import smtplib
import ssl
from urllib.parse import urlsplit

import requests

from .config import Settings
from .models import Alert, cents_text, listing_url

SERVICE = "SteamMarketSentinel"


class SecretStore:
    """Uses Windows Credential Manager / macOS Keychain / Secret Service via keyring.

    Never falls back to plaintext files if the host has no secure keyring.
    """
    @staticmethod
    def get(key: str) -> str:
        try:
            import keyring
            return keyring.get_password(SERVICE, key) or ""
        except Exception:
            return ""

    @staticmethod
    def set(key: str, value: str) -> None:
        import keyring
        if value:
            keyring.set_password(SERVICE, key, value)
        else:
            try:
                keyring.delete_password(SERVICE, key)
            except keyring.errors.PasswordDeleteError:
                pass


def render_alert(alert: Alert) -> tuple[str, str]:
    amount = cents_text(alert.price_cents, alert.currency)
    previous = cents_text(alert.comparison_cents, alert.currency)
    if alert.rule == "target":
        subject = f"[Steam 目标价] {alert.title}: {amount}"
        body = f"最新最低挂单价 {amount}，已经达到你的目标价 {previous}。"
    elif alert.rule == "drop":
        subject = f"[Steam 价格下降] {alert.title}: {amount}"
        body = f"最新最低挂单价 {amount}，比上一次采样 {previous} 更低。"
    else:
        subject = f"[Steam 刷新史低] {alert.title}: {amount}"
        body = f"最新最低挂单价 {amount}，低于先前史低 {previous}。"
    body += ("\n\n注意：是周期轮询时观察到的挂单价，可能随时变化，"
             "并非 Steam 全平台历史最低价。\n" + listing_url(alert.appid, alert.hash_name))
    return subject, body


def _smtp_send(settings: Settings, subject: str, body: str) -> None:
    sender = settings.email_sender.strip()
    receiver = settings.email_receiver.strip() or sender
    password = SecretStore.get("qq_smtp_authorization")
    if not sender.lower().endswith("@qq.com") or not receiver or not password:
        raise ValueError("请填写 QQ 邮箱、收件邮箱，并保存 QQ 邮箱 SMTP 授权码")
    mail = EmailMessage()
    mail["From"] = sender
    mail["To"] = receiver
    mail["Subject"] = subject
    mail.set_content(body)

    context = ssl.create_default_context()

    with smtplib.SMTP("smtp.qq.com", 587, timeout=15) as server:
        server.ehlo()

        if not server.has_extn("starttls"):
            raise smtplib.SMTPNotSupportedError(
                "SMTP 服务器未提供 STARTTLS"
            )
        server.starttls(context=context)
        server.ehlo()
        
        server.login(sender, password)
        server.send_message(mail)


def _onebot_send(settings: Settings, subject: str, body: str) -> None:
    endpoint = settings.onebot_url.rstrip("/")
    parsed = urlsplit(endpoint)
    if parsed.scheme not in {"https", "http"} or not parsed.netloc:
        raise ValueError("OneBot 地址必须是 http(s) URL")
    if parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("远程 OneBot 必须使用 HTTPS，避免明文传输访问令牌")
    user_id = settings.onebot_user_id.strip()
    if not user_id.isdigit():
        raise ValueError("OneBot 私聊收件 QQ 号必须是数字")
    token = SecretStore.get("onebot_access_token")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    res = requests.post(endpoint + "/send_private_msg", json={
        "user_id": int(user_id), "message": subject + "\n" + body,
    }, headers=headers, timeout=12)
    res.raise_for_status()
    data = res.json()
    if data.get("status") not in ("ok", None) or data.get("retcode", 0) != 0:
        raise ValueError("OneBot 推送被拒绝: " + str(data.get("message") or data.get("wording")))


def send_remote_notifications(settings: Settings, alert: Alert) -> list[str]:
    """Best effort delivery, errors returned to UI; never log secret values."""
    subject, body = render_alert(alert)
    errors = []
    for enabled, name, handler in (
        (settings.email_enabled, "QQ 邮箱", _smtp_send),
        (settings.onebot_enabled, "OneBot", _onebot_send),
    ):
        if enabled:
            try:
                handler(settings, subject, body)
            except Exception as exc:
                errors.append(f"{name} 发送失败: {type(exc).__name__}: {exc}")
    return errors


def send_test(settings: Settings, channel: str) -> None:
    if channel == "email":
        _smtp_send(settings, "Steam Market Sentinel 测试邮件", "通知测试成功。")
    elif channel == "onebot":
        _onebot_send(settings, "Steam Market Sentinel 测试", "通知测试成功。")
    else:
        raise ValueError("未知通知渠道")
