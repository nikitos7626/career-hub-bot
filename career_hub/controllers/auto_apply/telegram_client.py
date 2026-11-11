"""
Telegram Channel & Recruiter Direct Messaging Adapter.
"""

import urllib.parse


def build_telegram_recruiter_link(username: str, cover_letter: str) -> dict:
    """Генерирует прямые ссылки (Deep Link и Web Link) для диалога с рекрутером в Telegram."""
    clean_user = (username or "").replace("@", "").strip()
    if not clean_user:
        return {"tg_url": "", "web_url": ""}
    encoded_text = urllib.parse.quote(str(cover_letter or "").strip())
    return {
        "tg_url": f"tg://resolve?domain={clean_user}&text={encoded_text}",
        "web_url": f"https://t.me/{clean_user}?text={encoded_text}",
        "username": clean_user,
    }


def apply_telegram_vacancy(raw_id: str, cover_letter: str, username: str = None) -> dict:
    """Формирует действие отклика на вакансию из Telegram-канала с глубокими ссылками в 1 клик."""
    links = build_telegram_recruiter_link(username, cover_letter) if username else {"tg_url": "", "web_url": ""}
    return {
        "ok": True,
        "status": "applied",
        "method": "telegram_direct",
        "detail": f"Сформирована прямая ссылка для диалога с рекрутером @{username}" if username else "Отмечено как откликнуто",
        "direct_link": links.get("tg_url", ""),
        "web_link": links.get("web_url", ""),
        "recruiter_username": username or "",
    }
