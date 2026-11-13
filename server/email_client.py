# -*- coding: utf-8 -*-
"""邮件发送模块 —— 管理员回复工单时自动发邮件给用户。"""
import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import Header
from . import config

logger = logging.getLogger(__name__)


def is_email_configured() -> bool:
    """检查邮件是否已配置（EMAIL_USER 和 EMAIL_PASSWORD 都要有）。"""
    return bool(config.EMAIL_USER and config.EMAIL_PASSWORD)


def send_email(to_email: str, subject: str, body: str, is_html: bool = True) -> bool:
    """
    发送邮件。失败不抛异常，只记日志。
    
    Args:
        to_email: 收件人邮箱
        subject: 邮件主题
        body: 邮件正文（HTML 或纯文本）
        is_html: 是否 HTML 格式
    Returns:
        True=成功, False=失败
    """
    if not is_email_configured():
        logger.warning("邮件未配置（EMAIL_USER/EMAIL_PASSWORD 为空），跳过发送 to=%s", to_email)
        return False

    if not to_email or "@" not in to_email:
        logger.warning("收件邮箱无效: %s", to_email)
        return False

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = Header(subject, "utf-8")
        msg["From"] = f"{config.EMAIL_FROM_NAME} <{config.EMAIL_USER}>"
        msg["To"] = to_email

        content_type = "html" if is_html else "plain"
        msg.attach(MIMEText(body, content_type, "utf-8"))

        if config.EMAIL_USE_SSL:
            server = smtplib.SMTP_SSL(config.EMAIL_SMTP_HOST, config.EMAIL_SMTP_PORT, timeout=15)
        else:
            server = smtplib.SMTP(config.EMAIL_SMTP_HOST, config.EMAIL_SMTP_PORT, timeout=15)
            server.starttls()

        server.login(config.EMAIL_USER, config.EMAIL_PASSWORD)
        server.sendmail(config.EMAIL_USER, [to_email], msg.as_string())
        server.quit()
        logger.info("邮件发送成功 to=%s subject=%s", to_email, subject[:30])
        return True
    except Exception as e:
        logger.error("邮件发送失败 to=%s: %s", to_email, e)
        return False


def notify_feedback_reply(to_email: str, user_content: str, admin_reply: str, ai_analysis: str = "") -> bool:
    """
    管理员回复工单后，给用户发一封友好的通知邮件。
    
    Args:
        to_email: 用户留的邮箱
        user_content: 用户原始反馈内容
        admin_reply: 管理员回复内容
        ai_analysis: AI 自动分析结果（可选）
    """
    subject = "📬 你的简历编辑器反馈已收到回复"

    # HTML 正文
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
            <div style="background:#fff;border:1px solid #e5e7eb;padding:12px 16px;border-radius:8px;margin-bottom:16px;font-size:14px;line-height:1.7;">
                {user_content}
            </div>

            {ai_section}

            <div style="font-size:13px;color:#059669;font-weight:600;margin-bottom:6px;">✅ 客服回复：</div>
            <div style="background:#f0fdf4;border:1px solid #86efac;padding:12px 16px;border-radius:8px;font-size:14px;line-height:1.7;margin-bottom:16px;">
                {admin_reply}
            </div>

            <div style="font-size:12px;color:#9ca3af;border-top:1px solid #e5e7eb;padding-top:16px;margin-top:16px;line-height:1.6;">
                这是一封自动发送的通知邮件，请直接回复此邮件继续沟通。<br>
                如有问题，欢迎随时反馈给我们 👋
            </div>
        </div>
    </div>"""

    return send_email(to_email, subject, html, is_html=True)
