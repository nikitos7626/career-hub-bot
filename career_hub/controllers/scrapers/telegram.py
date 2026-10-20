"""
Telegram Public Channels Scraper for IT & Junior Vacancies.
"""

import re
import html
import time
import logging
import requests
from datetime import datetime, timezone
from career_hub.controllers.scrapers.base import HTTP_HEADERS

logger = logging.getLogger("career_hub.scrapers.telegram")


def extract_telegram_contacts(text: str, ext_links: list = None, channel_name: str = "") -> dict:
    """
    Извлекает юзернейм рекрутера и email для прямого отклика из текста поста или внешних ссылок Telegram.
    """
    recruiter_username = ""
    recruiter_email = ""

    clean_ch = channel_name.replace('@', '').strip().lower() if channel_name else ""
    system_words = {"joinchat", "c", "s", "addstickers", "share", "iv", "proxy", "socks", "boost", "channel", clean_ch}

    # 1. Поиск Email
    email_m = re.search(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b', text)
    if email_m:
        cand_email = email_m.group(0).strip().lower()
        if not any(cand_email.endswith(bad) for bad in [".png", ".jpg", ".jpeg", ".svg", ".webp"]):
            recruiter_email = cand_email

    # 2. Поиск Telegram юзернеймов из ссылок
    all_links = list(ext_links or [])
    all_links.extend(re.findall(r'https?://t\.me/([a-zA-Z0-9_]{4,})', text))
    for l in all_links:
        m = re.search(r't\.me/([a-zA-Z0-9_]{4,})', l, re.IGNORECASE)
        if m:
            uname = m.group(1).strip()
            uname_low = uname.lower()
            if uname_low not in system_words and not uname_low.endswith("bot"):
                recruiter_username = uname
                break

    # 3. Поиск @username в контекстных строках (отклик, контакты, резюме, пишите, HR, лс)
    if not recruiter_username:
        for line in text.split('\n'):
            line_low = line.lower()
            if any(k in line_low for k in ["контакт", "отклик", "резюме", "писать", "пишите", "hr", "лс", "связь", "вопрос", "cv", "apply"]):
                usernames = re.findall(r'(?<!\w)@([a-zA-Z0-9_]{4,32})\b', line)
                for uname in usernames:
                    uname_low = uname.lower()
                    if uname_low not in system_words and not uname_low.endswith("bot"):
                        recruiter_username = uname
                        break
                if recruiter_username:
                    break

    # 4. Общий fallback по @username в тексте
    if not recruiter_username:
        usernames = re.findall(r'(?<!\w)@([a-zA-Z0-9_]{4,32})\b', text)
        for uname in usernames:
            uname_low = uname.lower()
            if uname_low not in system_words and not uname_low.endswith("bot"):
                recruiter_username = uname
                break

    return {
        "username": recruiter_username,
        "email": recruiter_email
    }



def fetch_tg_channel_vacancies(channel_name: str, max_messages: int = 25) -> list:
    """
    Публичный парсер веб-ленты Telegram-каналов без API-ключей и сессий.
    Парсит последние посты, извлекает контакты/ссылки на отклик и текст вакансии.
    Игнорирует посты старше 7 дней, чтобы исключить закрытые архивные наборы.
    """
    clean_ch = channel_name.replace('@', '').strip()
    url = f"https://t.me/s/{clean_ch}"
    results = []
    resp = None
    for attempt in range(2):
        try:
            resp = requests.get(url, headers=HTTP_HEADERS, timeout=14)
            if resp.status_code == 200:
                break
            time.sleep(1)
        except Exception as e:
            if attempt == 0:
                time.sleep(1.5)
                continue
            logger.warning(f"Не удалось получить посты из TG канала @{channel_name} (попытка {attempt+1}): {e}")
            return results

    if not resp or resp.status_code != 200:
        return results

    try:
        chunks = resp.text.split('class="tgme_widget_message ')
        now_utc = datetime.now(timezone.utc)
        for c in chunks[1:]:
            post_m = re.search(r'data-post="([^"]+)"', c)
            text_m = re.search(r'<div class="[^"]*tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', c, re.DOTALL)
            if not post_m or not text_m:
                continue

            time_m = re.search(r'<time[^>]*datetime="([^"]+)"', c)
            if time_m:
                try:
                    post_dt = datetime.fromisoformat(time_m.group(1))
                    age_days = (now_utc - post_dt).total_seconds() / 86400
                    if age_days > 7.0:
                        continue
                except Exception:
                    pass

            pid = post_m.group(1)
            msg_id = pid.split('/')[-1] if '/' in pid else pid
            raw_html = text_m.group(1)

            ext_links = re.findall(r'href="([^"]+)"', raw_html)
            clean_ext_links = [
                l for l in ext_links
                if not l.startswith('?') and f"t.me/{clean_ch}" not in l and not l.startswith('tg://')
            ]
            apply_link = clean_ext_links[0] if clean_ext_links else f"https://t.me/{pid}"

            clean_text = re.sub(r'<br\s*/?>', '\n', raw_html)
            clean_text = re.sub(r'<[^>]+>', '', clean_text)
            clean_text = html.unescape(clean_text).strip()
            if len(clean_text) < 50:
                continue

            clean_lower = clean_text.lower()
            is_job = any(k in clean_lower for k in [
                "ваканси", "ищем", "требуется", "developer", "разработчик",
                "стажер", "стажёр", "junior", "intern", "в поиске", "локация:",
                "формат работы:", "требования:", "стек:", "грейд / опыт"
            ])
            if not is_job:
                continue

            if any(k in clean_lower for k in ["опрос", "что делаете с таким", "вебинар", "бесплатный интенсив", "почему ты получаешь отказ"]):
                continue

            if any(k in clean_lower[:160] for k in ["подборка", "дайджест", "digest", "вакансии недели", "вакансии дня"]):
                continue

            lines = [l.strip() for l in clean_text.split('\n') if l.strip()]
            title = ""
            for l in lines:
                clean_l = re.sub(r'^[💻🔥📌⚡🎯💡👉💼]\s*', '', l).strip()
                if not clean_l.startswith('#') and len(clean_l) > 3:
                    title = clean_l
                    break
            if not title:
                title = lines[0] if lines else "IT Вакансия"
            title = title[:120].strip()

            comp_m = re.search(r'(?:Компания\s*/\s*проект|Компания|Company|Работодатель):\s*([^\n\r,•|]+)', clean_text, re.I)
            company = comp_m.group(1).strip() if comp_m else f"@{clean_ch}"

            sal_m = re.search(r'(?:Вилка|Зарплата|ЗП|Salary|Payment):\s*([^\n\r•]+)', clean_text, re.I)
            salary = sal_m.group(1).strip() if sal_m else ""

            contacts = extract_telegram_contacts(clean_text, ext_links=clean_ext_links, channel_name=clean_ch)

            results.append({
                "id": f"tg_{clean_ch}_{msg_id}",
                "raw_id": f"{clean_ch}_{msg_id}",
                "title": title,
                "company": company,
                "salary": salary,
                "link": apply_link,
                "description": clean_text,
                "source": "telegram",
                "source_name": f"TG: @{clean_ch}",
                "source_emoji": "📢",
                "recruiter_username": contacts.get("username", ""),
                "recruiter_email": contacts.get("email", ""),
                "contacts": contacts
            })
    except Exception as e:
        logger.error(f"Ошибка сбора постов из TG канала @{channel_name}: {e}")
    return results
