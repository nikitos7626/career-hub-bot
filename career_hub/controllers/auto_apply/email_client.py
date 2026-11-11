"""
Direct Email Auto-Mailer (SMTP) Adapter with PDF resume attachment and CRLF protection.
"""

import html
import os
import re
import smtplib
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from career_hub.controllers.auto_apply.base import _get_bot_ref, logger
from career_hub.models.config import load_config, safe_int
from career_hub.models.storage import DATA_DIR


def check_smtp_connection(smtp_host: str, smtp_port: int, smtp_user: str, smtp_password: str, use_ssl: bool = True) -> dict:
    """Проверяет подключение к SMTP-серверу кандидата (Яндекс, Mail.ru, Gmail, корпоративный SMTP)."""
    host = (smtp_host or "").strip()
    user = (smtp_user or "").strip()
    pwd = (smtp_password or "").strip()
    port = safe_int(smtp_port, 465)

    if not host or not user or not pwd:
        return {"ok": False, "detail": "Параметры SMTP (хост, логин, пароль приложения) не заполнены"}

    smtp_mod = _get_bot_ref("smtplib", smtplib)
    try:
        if use_ssl or port == 465:
            server = smtp_mod.SMTP_SSL(host, port, timeout=10)
        else:
            server = smtp_mod.SMTP(host, port, timeout=10)
            server.starttls()
        server.login(user, pwd)
        server.quit()
        return {"ok": True, "detail": f"SMTP-подключение к {host}:{port} успешно установлено"}
    except Exception as e:
        logger.error(f"Ошибка проверки SMTP ({host}:{port}): {e}")
        return {"ok": False, "detail": str(e)}


def send_email_application(to_email: str, vacancy_title: str, cover_letter: str, smtp_config: dict = None, resume_path: str = None) -> dict:
    """Отправляет профессиональный отклик на email рекрутера с прикреплением резюме (PDF)."""
    clean_email = (to_email or "").strip().lower()
    if not clean_email or "@" not in clean_email:
        return {"ok": False, "status": "error", "detail": "Некорректный email адрес получателя"}

    cfg = smtp_config or load_config()
    host = (cfg.get("smtp_host") or "").strip()
    port = safe_int(cfg.get("smtp_port", 465), 465)
    user = (cfg.get("smtp_user") or "").strip()
    pwd = (cfg.get("smtp_password") or "").strip()
    use_ssl = bool(cfg.get("smtp_ssl", True))
    from_name = (cfg.get("smtp_from_name") or cfg.get("candidate_name") or "Candidate").strip()
    contact_email = (cfg.get("contact_email") or user).strip()

    if not host or not user or not pwd:
        return {"ok": False, "status": "no_credentials", "detail": "SMTP параметры не настроены в Mini App"}

    clean_title = re.sub(r'[\r\n\t]+', ' ', vacancy_title).strip()[:150]
    clean_from_name = re.split(r'[\r\n]', from_name)[0].strip()
    clean_user = re.split(r'[\r\n]', user)[0].strip()
    clean_contact = re.split(r'[\r\n]', contact_email)[0].strip()
    clean_to = re.split(r'[\r\n]', clean_email)[0].strip()

    msg = MIMEMultipart("mixed")
    msg["From"] = f"{clean_from_name} <{clean_user}>"
    msg["To"] = clean_to
    msg["Reply-To"] = clean_contact
    msg["Subject"] = f"Отклик на позицию {clean_title} — {clean_from_name}"

    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText(cover_letter, "plain", "utf-8"))

    port_url = (cfg.get("portfolio_url") or "").strip()
    port_display = port_url.replace("https://", "").replace("http://", "").rstrip("/")
    res_tunnel = (cfg.get("resume_quick_tunnel_url") or "").strip()

    footer_links = [f"Контакты: {html.escape(clean_contact)}"]
    if res_tunnel:
        footer_links.append(f'Портфолио/Резюме: <a href="{html.escape(res_tunnel)}" style="color: #4f46e5;">онлайн-резюме</a>')
    if port_url:
        footer_links.append(f'Проект (деплой): <a href="{html.escape(port_url)}" style="color: #4f46e5;">{html.escape(port_display)}</a>')
    footer_links_html = " | ".join(footer_links)

    html_content = f"""<html>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; line-height: 1.6; color: #1e293b;">
    <div style="max-width: 600px; margin: 0 auto; padding: 20px; border: 1px solid #e2e8f0; border-radius: 12px; background-color: #ffffff;">
        <h3 style="color: #4f46e5; margin-top: 0;">Здравствуйте!</h3>
        <div style="white-space: pre-line; font-size: 15px; color: #334155;">{html.escape(cover_letter)}</div>
        <hr style="border: none; border-top: 1px solid #e2e8f0; margin: 24px 0;" />
        <p style="font-size: 13px; color: #64748b; margin-bottom: 4px;">С уважением,<br><strong>{html.escape(clean_from_name)}</strong></p>
        <p style="font-size: 12px; color: #94a3b8; margin: 0;">{footer_links_html}</p>
    </div>
</body>
</html>"""
    alt.attach(MIMEText(html_content, "html", "utf-8"))
    msg.attach(alt)

    pdf_file = resume_path or cfg.get("resume_pdf_path")
    if not pdf_file:
        candidate_pdf = os.path.join(DATA_DIR, "resume.pdf")
        if os.path.exists(candidate_pdf):
            pdf_file = candidate_pdf

    if pdf_file and os.path.exists(pdf_file):
        resolved_pdf = os.path.realpath(pdf_file)
        if resolved_pdf.lower().endswith(".pdf") and ".." not in str(pdf_file):
            try:
                with open(resolved_pdf, "rb") as f:
                    part = MIMEApplication(f.read(), Name=os.path.basename(resolved_pdf))
                part["Content-Disposition"] = f'attachment; filename="{os.path.basename(resolved_pdf)}"'
                msg.attach(part)
            except Exception as e:
                logger.warning(f"Не удалось прикрепить PDF резюме: {e}")
        else:
            logger.warning(f"Отклонен небезопасный файл резюме: {pdf_file}")

    smtp_mod = _get_bot_ref("smtplib", smtplib)
    try:
        if use_ssl or port == 465:
            server = smtp_mod.SMTP_SSL(host, port, timeout=15)
        else:
            server = smtp_mod.SMTP(host, port, timeout=15)
            server.starttls()
        server.login(user, pwd)
        server.sendmail(user, [clean_email], msg.as_string())
        server.quit()
        return {"ok": True, "status": "applied", "method": "email_smtp", "detail": f"Отклик успешно отправлен на {clean_email}"}
    except Exception as e:
        logger.error(f"Исключение при отправке email на {clean_email}: {e}")
        return {"ok": False, "status": "error", "detail": str(e)}
