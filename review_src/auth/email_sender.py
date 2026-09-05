from __future__ import annotations

import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

logger = logging.getLogger(__name__)

SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "").strip()
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "").strip()
FROM_NAME = os.getenv("FROM_NAME", "台股分析系統")
FROM_EMAIL = os.getenv("FROM_EMAIL", SMTP_USER).strip()


def smtp_configured() -> bool:
    return bool(SMTP_HOST and SMTP_PORT and SMTP_USER and SMTP_PASSWORD and FROM_EMAIL)


def _send_email(to_email: str, subject: str, html_body: str) -> bool:
    if not smtp_configured():
        logger.warning("[AUTH DEV EMAIL] to=%s subject=%s body=%s", to_email, subject, html_body)
        return True
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"{FROM_NAME} <{FROM_EMAIL}>"
        msg["To"] = to_email
        msg.attach(MIMEText(html_body, "html", "utf-8"))
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=12) as server:
            server.ehlo()
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(FROM_EMAIL, to_email, msg.as_string())
        return True
    except Exception:
        logger.exception("Failed to send auth email to %s", to_email)
        return False


def send_verification_email(to_email: str, code: str) -> bool:
    subject = f"【台股分析系統】Email 驗證碼：{code}"
    html = f"""
    <div style="font-family:Arial,'Noto Sans TC',sans-serif;max-width:520px;margin:0 auto;padding:28px;border:1px solid #ddd;border-radius:12px;">
      <h2 style="margin:0 0 16px;color:#111;">Email 驗證</h2>
      <p style="color:#444;line-height:1.6;">請輸入以下驗證碼完成註冊：</p>
      <div style="font-size:34px;letter-spacing:10px;font-weight:700;background:#f5f5f5;padding:18px;text-align:center;border-radius:10px;">{code}</div>
      <p style="color:#777;font-size:13px;line-height:1.6;">驗證碼 15 分鐘內有效。如果不是您本人操作，請忽略此信。</p>
    </div>
    """
    return _send_email(to_email, subject, html)


def send_password_reset_email(to_email: str, code: str) -> bool:
    subject = f"【台股分析系統】密碼重設驗證碼：{code}"
    html = f"""
    <div style="font-family:Arial,'Noto Sans TC',sans-serif;max-width:520px;margin:0 auto;padding:28px;border:1px solid #ddd;border-radius:12px;">
      <h2 style="margin:0 0 16px;color:#111;">密碼重設</h2>
      <p style="color:#444;line-height:1.6;">請輸入以下驗證碼重設密碼：</p>
      <div style="font-size:34px;letter-spacing:10px;font-weight:700;background:#f5f5f5;padding:18px;text-align:center;border-radius:10px;">{code}</div>
      <p style="color:#777;font-size:13px;line-height:1.6;">驗證碼 15 分鐘內有效。如果不是您本人操作，請忽略此信。</p>
    </div>
    """
    return _send_email(to_email, subject, html)


def email_security_warnings() -> list[str]:
    return [] if smtp_configured() else ["SMTP 尚未設定；正式上線前必須設定寄信服務"]
