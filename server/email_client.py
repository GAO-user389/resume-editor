# -*- coding: utf-8 -*-
"""邮件发送模块 —— 支持 SendGrid > Resend > SMTP 自动降级。

优先级：SendGrid HTTP API（无需域名验证，可发任意邮箱）
     → Resend HTTP API（需验证域名才能发非自有邮箱）
     → SMTP（Render Free 版出网屏蔽，备用）
"""
import json
import logging
import urllib.request
import urllib.error
from . import config

logger = logging.getLogger(__name__)


def _send_sendgrid(to_email: str, subject: str, html_body: str, plain_body: str = "") -> bool:
    """SendGrid HTTP API 发邮件（免费版无需域名验证，可发任意邮箱）。"""
    import httpx

    payload = {
        "personalizations": [{"to": [{"email": to_email}]}],
        "from": {"email": config.EMAIL_USER, "name": config.EMAIL_FROM_NAME},
        "subject": subject,
        "content": [{"type": "text/html", "value": html_body}],
    }
    if plain_body:
        payload["content"].insert(0, {"type": "text/plain", "value": plain_body})

    try:
        resp = httpx.post(
            "https://api.sendgrid.com/v3/mail/send",
            json=payload,
            headers={"Authorization": f"Bearer {config.SENDGRID_API_KEY}"},
            timeout=15,
        )
        if resp.status_code in (200, 202):
            logger.info("SendGrid 发送成功 to=%s", to_email)
            return True
        else:
            logger.error("SendGrid HTTP %d to=%s: %s", resp.status_code, to_email, resp.text[:300])
            return False
    except Exception as e:
        logger.error("SendGrid 发送异常 to=%s: %s", to_email, e)
        return False


def _send_resend(to_email: str, subject: str, html_body: str, plain_body: str = "") -> bool:
    """用 Resend HTTP API 发邮件（Render 上首选，HTTP 出站正常）。"""
    import httpx

    payload = {
        "from": f"{config.EMAIL_FROM_NAME} <{config.EMAIL_USER}>",
        "to": [to_email],
        "subject": subject,
        "html": html_body,
    }
    if plain_body:
        payload["text"] = plain_body

    try:
        resp = httpx.post(
            "https://api.resend.com/emails",
            json=payload,
            headers={"Authorization": f"Bearer {config.RESEND_API_KEY}"},
            timeout=15,
        )
        if resp.status_code in (200, 201):
            result = resp.json()
            logger.info("Resend 发送成功 to=%s id=%s", to_email, result.get("id", ""))
            return True
        else:
            logger.error("Resend HTTP %d to=%s: %s", resp.status_code, to_email, resp.text[:300])
            return False
    except Exception as e:
        logger.error("Resend 发送异常 to=%s: %s", to_email, e)
        return False


def _send_smtp(to_email: str, subject: str, html_body: str, plain_body: str = "") -> bool:
    """SMTP 发邮件（备用，Render Free 版出网被屏蔽）。"""
    import smtplib
    import ssl
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart
    from email.header import Header

    if not (config.EMAIL_USER and config.EMAIL_PASSWORD):
        return False

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = Header(subject, "utf-8")
        msg["From"] = f"{config.EMAIL_FROM_NAME} <{config.EMAIL_USER}>"
        msg["To"] = to_email

        if plain_body:
            msg.attach(MIMEText(plain_body, "plain", "utf-8"))
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        if config.EMAIL_USE_SSL:
            server = smtplib.SMTP_SSL(config.EMAIL_SMTP_HOST, config.EMAIL_SMTP_PORT, timeout=15)
        else:
            server = smtplib.SMTP(config.EMAIL_SMTP_HOST, config.EMAIL_SMTP_PORT, timeout=15)
            server.starttls()

        server.login(config.EMAIL_USER, config.EMAIL_PASSWORD)
        server.sendmail(config.EMAIL_USER, [to_email], msg.as_string())
        server.quit()
        logger.info("SMTP 发送成功 to=%s", to_email)
        return True
    except Exception as e:
        logger.error("SMTP 发送失败 to=%s: %s", to_email, e)
        return False


def send_email(to_email: str, subject: str, html_body: str, plain_body: str = "") -> bool:
    """
    发送邮件。优先级：SendGrid > Resend > SMTP 自动降级。
    
    Args:
        to_email: 收件人邮箱
        subject: 邮件主题
        html_body: HTML 正文
        plain_body: 纯文本备用
    Returns:
        True=成功, False=失败
    """
    if not to_email or "@" not in to_email:
        logger.warning("收件邮箱无效: %s", to_email)
        return False

    # 1. SendGrid（免费版无需域名验证，可发任意邮箱）
    if config.SENDGRID_API_KEY:
        return _send_sendgrid(to_email, subject, html_body, plain_body)

    # 2. Resend（需验证域名才能发非自有邮箱）
    if config.RESEND_API_KEY:
        return _send_resend(to_email, subject, html_body, plain_body)

    # 3. 降级 SMTP（Render Free 不可用）
    return _send_smtp(to_email, subject, html_body, plain_body)


def notify_feedback_reply(to_email: str, user_content: str, admin_reply: str, ai_analysis: str = "") -> bool:
    """管理员回复工单后，给用户发一封友好的通知邮件。"""
    subject = "📬 你的简历编辑器反馈已收到回复"

    ai_section = ""
    if ai_analysis:
        ai_section = f"""
        <div style="background:#eff6ff;border-left:4px solid #3b82f6;padding:12px 16px;border-radius:4px;margin:12px 0;">
            <div style="font-size:13px;color:#6b7280;font-weight:600;margin-bottom:6px;">🤖 AI 自动诊断</div>
            <div style="font-size:14px;color:#1f2937;line-height:1.7;white-space:pre-wrap;">{ai_analysis}</div>
        </div>"""

    html = f"""
    <div style="max-width:520px;margin:0 auto;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;color:#1f2937;">
        <div style="background:linear-gradient(135deg,#667eea,#764ba2);padding:24px;border-radius:12px 12px 0 0;color:white;">
            <div style="font-size:18px;font-weight:700;">📬 客服回复通知</div>
            <div style="font-size:13px;opacity:.85;margin-top:4px;">来自「简历编辑器」客服中心</div>
        </div>
        <div style="background:#fafafa;border:1px solid #e5e7eb;border-top:none;border-radius:0 0 12px 12px;padding:24px;">
            <div style="font-size:13px;color:#6b7280;font-weight:600;margin-bottom:6px;">你之前的反馈：</div>
            <div style="background:#fff;border:1px solid #e5e7eb;padding:12px 16px;border-radius:8px;margin-bottom:16px;font-size:14px;line-height:1.7;">{user_content}</div>
            {ai_section}
            <div style="font-size:13px;color:#059669;font-weight:600;margin-bottom:6px;">✅ 客服回复：</div>
            <div style="background:#f0fdf4;border:1px solid #86efac;padding:12px 16px;border-radius:8px;font-size:14px;line-height:1.7;margin-bottom:16px;">{admin_reply}</div>
            <div style="font-size:12px;color:#9ca3af;border-top:1px solid #e5e7eb;padding-top:16px;margin-top:16px;line-height:1.6;">
                这是一封自动发送的通知邮件，如需继续沟通请直接回复此邮件 👋
            </div>
        </div>
    </div>"""

    plain = f"""📬 客服回复通知\n\n你之前的反馈：{user_content}\n\n客服回复：{admin_reply}"""
    return send_email(to_email, subject, html, plain)
