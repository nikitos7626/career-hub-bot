"""
Multi-Source Career AI Telegram Bot (Habr Career, HeadHunter, GetMatch, Remotive)
Интеллектуальный карьерный ассистент с мульти-поточным мониторингом и интерактивной генерацией откликов.
"""

import os
import sys
import time
import json
import logging
import threading
import urllib.parse
from urllib.parse import urlparse, parse_qs
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timezone
import html
import re
import xml.etree.ElementTree as ET
import requests
import hmac
import hashlib
import secrets

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

DATA_DIR = os.environ.get("DATA_DIR", "/app/data")
if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR, exist_ok=True)

STATE_FILE = os.path.join(DATA_DIR, "seen_vacancies.json")
CACHE_FILE = os.path.join(DATA_DIR, "vacancies_cache.json")
STATUSES_FILE = os.path.join(DATA_DIR, "vacancy_statuses.json")
MINIAPP_HTML_PATH = os.path.join(DATA_DIR, "miniapp", "index.html")
CONFIG_FILE = os.path.join(DATA_DIR, "bot_config.json")
SESSIONS_FILE = os.path.join(DATA_DIR, "auth_sessions.json")
HH_NEGOTIATIONS_FILE = os.path.join(DATA_DIR, "hh_negotiations_tracker.json")

sessions_lock = threading.Lock()
pending_logins_lock = threading.Lock()
pending_logins = {}     # session_id -> {"code": "...", "created_at": ..., "approved": False, "token": ...}
one_time_tokens = {}    # token -> {"user_id": ..., "expires_at": ...}

# --- IN-MEMORY CONFIG CACHE ---
_config_cache = None
_config_cache_mtime = 0.0
_config_cache_lock = threading.Lock()

DEFAULT_CONFIG = {
    "bot_token": os.environ.get("BOT_TOKEN", ""),
    "chat_id": int(os.environ.get("CHAT_ID", "0")) if os.environ.get("CHAT_ID") else 0,
    "allowed_telegram_id": int(os.environ.get("ALLOWED_TELEGRAM_ID", "0")) if os.environ.get("ALLOWED_TELEGRAM_ID") else 0,
    "groq_api_key": os.environ.get("GROQ_API_KEY", ""),
    "groq_model": os.environ.get("GROQ_MODEL", "qwen/qwen3.8-27b"),
    "min_match_score": 6,
    "notify_min_score": 7,
    "auto_apply_enabled": True,
    "auto_apply_min_score": 7,
    "hh_access_token": "",
    "hh_resume_id": "",
    "hh_web_cookie": "",
    "hh_resume_hash": "b81ca639ff1137b70d0039ed1f57755649766f",
    "resume_quick_tunnel_url": "https://south-tomato-therapist-minimum.trycloudflare.com/",
    "daily_digest_enabled": True,
    "last_digest_time": 0,
    "check_interval_seconds": 600,
    "max_ai_analyses_per_cycle": 25,
    "ai_delay_seconds": 2.5,
    "enable_habr": True,
    "enable_hh": True,
    "enable_getmatch": False,
    "enable_vseti": True,
    "enable_remotive": True,
    "enable_trudvsem": False,
    "vseti_pages": 6,

    "blacklisted_companies": ["aston", "астон", "deepra", "дипра"],
    "habr_queries": [
        "https://career.habr.com/vacancies/rss?s[]=1&s[]=2&q=junior+java",
        "https://career.habr.com/vacancies/rss?s[]=1&s[]=2&q=стажер+java",
        "https://career.habr.com/vacancies/rss?s[]=1&s[]=2&q=java",
        "https://career.habr.com/vacancies/rss?s[]=1&s[]=2&q=spring",
        "https://career.habr.com/vacancies/rss?s[]=1&s[]=2&q=стажер+разработчик",
        "https://career.habr.com/vacancies/rss?s[]=1&s[]=2&q=junior+backend",
        "https://career.habr.com/vacancies/rss?s[]=1&s[]=2&q=junior+fullstack",
        "https://career.habr.com/vacancies/rss?s[]=1&s[]=2&q=junior+frontend",
        "https://career.habr.com/vacancies/rss?s[]=1&s[]=2&q=стажер+frontend",
        "https://career.habr.com/vacancies/rss?s[]=1&s[]=2&q=junior+python",
        "https://career.habr.com/vacancies/rss?s[]=1&s[]=2&q=стажер+python",
        "https://career.habr.com/vacancies/rss?s[]=1&s[]=2&q=python+backend",
        "https://career.habr.com/vacancies/rss?q=Spring+AI",
        "https://career.habr.com/vacancies/rss?q=LangChain4j",
        "https://career.habr.com/vacancies/rss?q=Java+AI",
        "https://career.habr.com/vacancies/rss?q=AI+engineer",
        "https://career.habr.com/vacancies/rss?q=LLM",
        "https://career.habr.com/vacancies/rss?q=AI+разработчик",
        "https://career.habr.com/vacancies/rss?q=remnawave",
        "https://career.habr.com/vacancies/rss?q=xray"
    ],
    "hh_queries": [
        {"query": "junior java", "experience": "noExperience"},
        {"query": "стажер java", "experience": "noExperience"},
        {"query": "стажёр java", "experience": "noExperience"},
        {"query": "trainee java", "experience": "noExperience"},
        {"query": "intern java", "experience": "noExperience"},
        {"query": "младший java разработчик", "experience": "noExperience"},
        {"query": "java разработчик", "schedule": "remote", "experience": "noExperience"},
        {"query": "java", "schedule": "remote", "experience": "noExperience"},
        {"query": "spring", "schedule": "remote", "experience": "noExperience"},
        {"query": "стажер backend", "experience": "noExperience"},
        {"query": "junior backend", "experience": "noExperience"},
        {"query": "стажер разработчик", "experience": "noExperience"},
        {"query": "junior developer", "experience": "noExperience"},
        {"query": "junior fullstack", "experience": "noExperience"},
        {"query": "стажер fullstack", "experience": "noExperience"},
        {"query": "junior frontend", "experience": "noExperience"},
        {"query": "стажер frontend", "experience": "noExperience"},
        {"query": "junior javascript", "experience": "noExperience"},
        {"query": "junior python", "experience": "noExperience"},
        {"query": "стажер python", "experience": "noExperience"},
        {"query": "стажёр python", "experience": "noExperience"},
        {"query": "python backend", "experience": "noExperience"},
        {"query": "junior python backend", "experience": "noExperience"},
        {"query": "fastapi", "experience": "noExperience"},
        {"query": "Spring AI"},
        {"query": "LangChain4j"},
        {"query": "Java AI"},
        {"query": "AI engineer"},
        {"query": "LLM developer"},
        {"query": "инженер ИИ"},
        {"query": "разработчик ИИ"},
        {"query": "AI разработчик"},
        {"query": "junior AI"},
        {"query": "стажер AI"},
        {"query": "xray developer"},
        {"query": "remnawave"},
        {"query": "разработчик vpn"}
    ]
}

CANDIDATE_PROFILE = """
Профиль кандидата (Никита Фролов, 21 год):
Образование: Неоконченное высшее (технический ВУЗ) + СПО (колледж, Информационные системы и программирование) + Школа 21 от Сбера.
База и алгоритмы: 2.5 года вел практические занятия и код-ревью работ (алгоритмическая культура чистого кода, выявление багов).

ЦЕЛЕВОЙ ПРОФИЛЬ (СТРОГО РАЗРАБОТКА ПО):
1. Junior Java Backend Developer:
   - Стек: Java 21, Spring Boot 3 (Spring Web, Spring Security, Spring Data JPA/JDBC), PostgreSQL, Docker, Clean Architecture, REST API, многопоточность.
2. AI / LLM Integration & AI Engineer (РАБОТА С ИИ И НЕЙРОСЕТЯМИ):
   - Разработка и интеграция AI-решений, агентов и LLM пайплайнов (Spring AI, LangChain, OpenAI/Groq API, Function Calling, RAG, pgvector, prompt engineering).
3. Junior Python Backend Developer:
   - Стек: Python 3, FastAPI, Flask, REST API, PostgreSQL, Docker, ООП, скрипты автоматизации и интеграции.
4. Junior / Trainee Fullstack JS / Frontend:
   - React 18, Node.js, Express, Sequelize, REST API, Nginx, Linux VPS. Деплой https://park.shadowkek.space
5. Разработчик VPN / Прокси сервисов (Xray, Remnawave, VLESS, Sing-box, сетевые протоколы).
6. Junior / Intern Backend Developer (стажер или младший бэкенд-разработчик).

КАТЕГОРИЧЕСКИ НЕ ИНТЕРЕСУЕТ (СТРОЖАЙШИЙ ОТСЕВ):
- ПРЕПОДАВАНИЕ / УЧИТЕЛЬ / МЕНТОР / НАСТАВНИК / РЕПЕТИТОР: любые вакансии в сфере обучения строго запрещены! Никиту интересует ТОЛЬКО инженерная разработка ПО, НЕ преподавание!
- ТЕСТИРОВАНИЕ И QA / AQA: любые вакансии тестировщиков, автотестеров и QA строго запрещены!
- ТЕХНИЧЕСКАЯ ПОДДЕРЖКА / САППОРТ / L1-L3 / СОПРОВОЖДЕНИЕ: запрещены!
- РАЗМЕТКА ДАННЫХ И AI-ТРЕНЕРСТВО: запрещены!
- ВАКАНСИИ С ТРЕБОВАНИЕМ ОПЫТА: Исключать вакансии уровня Middle+ / Senior (требование от 2-3+ лет реального коммерческого опыта, руководство, архитектура). Шаблоны вида 'опыт 1-3 года' или 'от 1 года' на позициях Junior / Стажер допустимы, если стек подходит.
- ЛЮБЫЕ обучающие платные курсы, онлайн-школы, академии, инкубаторы.
- Ручной эникей / выездной мастер / ремонт ПК / картриджи / телефонный спам-коллцентр.
- Позиции Middle+, Senior, Lead, Тимлид, Архитектор, Руководитель.
- 1С, PHP, Ruby, Мобильная разработка.
"""

HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
}


def load_config():
    global _config_cache, _config_cache_mtime
    with _config_cache_lock:
        try:
            current_mtime = os.path.getmtime(CONFIG_FILE) if os.path.exists(CONFIG_FILE) else 0.0
        except OSError:
            current_mtime = 0.0

        # Return cached copy if file hasn't changed
        if _config_cache is not None and current_mtime == _config_cache_mtime:
            return _config_cache

        if not os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(DEFAULT_CONFIG, f, indent=4, ensure_ascii=False)
            _config_cache = dict(DEFAULT_CONFIG)
            _config_cache_mtime = os.path.getmtime(CONFIG_FILE)
            return _config_cache
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            updated = False
            for k, v in DEFAULT_CONFIG.items():
                if k not in cfg:
                    cfg[k] = v
                    updated = True

            # Fixed: do NOT auto-reset habr_queries — trust whatever is in the file
            if "habr_queries" not in cfg:
                cfg["habr_queries"] = DEFAULT_CONFIG["habr_queries"]
                updated = True
            if "hh_queries" not in cfg:
                cfg["hh_queries"] = DEFAULT_CONFIG["hh_queries"]
                updated = True

            if updated:
                with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                    json.dump(cfg, f, indent=4, ensure_ascii=False)
                current_mtime = os.path.getmtime(CONFIG_FILE)

            _config_cache = cfg
            _config_cache_mtime = current_mtime
            return cfg
        except Exception as e:
            logger.error(f"Ошибка чтения {CONFIG_FILE}: {e}")
            if _config_cache is not None:
                return _config_cache
            return DEFAULT_CONFIG


def save_config(config):
    global _config_cache, _config_cache_mtime
    with _config_cache_lock:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=4, ensure_ascii=False)
        # Invalidate cache so next load_config() re-reads from disk
        _config_cache = None
        _config_cache_mtime = 0.0




SEEN_VACANCIES_TTL_DAYS = 7


def load_seen_vacancies():
    """Load seen vacancy IDs. Returns dict {id: iso_timestamp} with auto-purge of entries older than SEEN_VACANCIES_TTL_DAYS.
    Always preserves IDs that exist in vacancy_statuses.json (applied, archived, inbox).
    Backward-compatible: if file contains an old plain list, converts it automatically."""
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        now = datetime.now(timezone.utc)
        cutoff = now.timestamp() - (SEEN_VACANCIES_TTL_DAYS * 86400)
        statuses = load_statuses() if os.path.exists(STATUSES_FILE) else {}

        # Backward compat: old format was a plain list of IDs
        if isinstance(data, list):
            logger.info(f"Конвертация seen_vacancies из list -> dict с TTL (найдено {len(data)} записей)")
            ts_str = now.isoformat()
            seen = {v_id: ts_str for v_id in data}
            return seen

        # New format: dict {id -> iso_str}
        purged = 0
        seen = {}
        for v_id, ts_str in data.items():
            try:
                ts = datetime.fromisoformat(ts_str).timestamp()
                if ts >= cutoff or v_id in statuses:
                    seen[v_id] = ts_str
                else:
                    purged += 1
            except Exception:
                seen[v_id] = ts_str  # keep if timestamp is unparseable
        if purged:
            logger.info(f"Авто-очистка seen_vacancies: удалено {purged} записей старше {SEEN_VACANCIES_TTL_DAYS} дней")
        return seen
    except Exception as e:
        logger.error(f"Ошибка загрузки истории вакансий: {e}")
        return {}


def seen_add(seen: dict, v_id: str):
    """Mark a vacancy ID as seen (now). Returns the updated dict."""
    seen[v_id] = datetime.now(timezone.utc).isoformat()
    return seen


def save_seen_vacancies(seen):
    """Save seen vacancies dict. Trims to newest 5000 entries if oversized."""
    if isinstance(seen, set):
        # Safety: handle callers that still pass a set
        seen = {v_id: datetime.now(timezone.utc).isoformat() for v_id in seen}
    if len(seen) > 5000:
        # Keep the 5000 most-recently seen (sorted by timestamp desc)
        sorted_items = sorted(seen.items(), key=lambda x: x[1], reverse=True)[:5000]
        seen = dict(sorted_items)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(seen, f, indent=2, ensure_ascii=False)




def load_cache():
    if not os.path.exists(CACHE_FILE):
        return {}
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_to_cache(vacancy):
    cache = load_cache()
    cache[vacancy["id"]] = vacancy
    if len(cache) > 500:
        keys_to_del = list(cache.keys())[:-500]
        for k in keys_to_del:
            del cache[k]
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2, ensure_ascii=False)


def get_from_cache(v_id):
    cache = load_cache()
    return cache.get(v_id)


def delete_from_cache(v_id):
    cache = load_cache()
    if v_id in cache:
        del cache[v_id]
        try:
            with open(CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(cache, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Ошибка удаления из кэша: {e}")



def load_statuses():
    if not os.path.exists(STATUSES_FILE):
        return {}
    try:
        with open(STATUSES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Ошибка чтения статусов: {e}")
        return {}


def save_statuses(statuses):
    try:
        with open(STATUSES_FILE, "w", encoding="utf-8") as f:
            json.dump(statuses, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error(f"Ошибка сохранения статусов: {e}")


def update_vacancy_status(v_id, status, **kwargs):
    statuses = load_statuses()
    now_iso = datetime.now(timezone.utc).isoformat()
    cur = statuses.get(v_id, {})
    cur["status"] = status
    cur["updated_at"] = now_iso
    if status == "applied" and not cur.get("applied_at"):
        cur["applied_at"] = now_iso
    for k, val in kwargs.items():
        if val is not None:
            cur[k] = val
    statuses[v_id] = cur
    save_statuses(statuses)
    logger.info(f"Обновлен статус вакансии {v_id} -> {status}")
    return cur


# --- АВТОРИЗАЦИЯ И СЕССИИ (Telegram WebApp & Web Browser) ---

def load_sessions():
    if not os.path.exists(SESSIONS_FILE):
        return {}
    try:
        with open(SESSIONS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Ошибка чтения сессий: {e}")
        return {}


def save_sessions(sessions):
    try:
        with open(SESSIONS_FILE, "w", encoding="utf-8") as f:
            json.dump(sessions, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error(f"Ошибка сохранения сессий: {e}")


def create_user_session(user_id, device="Browser", days=30):
    with sessions_lock:
        sessions = load_sessions()
        token = secrets.token_urlsafe(32)
        expires_at = int(time.time()) + (days * 86400)
        sessions[token] = {
            "user_id": int(user_id),
            "created_at": int(time.time()),
            "expires_at": expires_at,
            "device": device
        }
        # Удаляем просроченные сессии
        now = int(time.time())
        sessions = {t: data for t, data in sessions.items() if data.get("expires_at", 0) > now}
        save_sessions(sessions)
        return token, expires_at


def verify_session_token(token):
    if not token:
        return None
    with sessions_lock:
        sessions = load_sessions()
        session = sessions.get(token)
        if not session:
            return None
        if session.get("expires_at", 0) < time.time():
            sessions.pop(token, None)
            save_sessions(sessions)
            return None
        return session


def revoke_session_token(token):
    if not token:
        return
    with sessions_lock:
        sessions = load_sessions()
        if token in sessions:
            del sessions[token]
            save_sessions(sessions)


def create_one_time_token(user_id):
    token = secrets.token_urlsafe(24)
    one_time_tokens[token] = {
        "user_id": int(user_id),
        "expires_at": time.time() + 600  # 10 минут
    }
    return token


def consume_one_time_token(token):
    if not token or token not in one_time_tokens:
        return None
    data = one_time_tokens.pop(token)
    if data.get("expires_at", 0) < time.time():
        return None
    return data.get("user_id")


def validate_telegram_init_data(init_data: str, bot_token: str) -> dict | None:
    if not init_data:
        return None
    try:
        parsed = dict(urllib.parse.parse_qsl(init_data, keep_blank_values=True))
        auth_hash = parsed.pop("hash", None)
        if not auth_hash:
            return None
        data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
        secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
        calc_hash = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()
        if hmac.compare_digest(calc_hash, auth_hash):
            user_data = parsed.get("user")
            if user_data:
                return json.loads(user_data)
        return None
    except Exception as e:
        logger.error(f"Ошибка проверки initData Telegram: {e}")
        return None


def send_message(token, chat_id, text, reply_markup=None):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code == 200:
            return resp.json()

        # Fallback: if HTML parsing failed, retry once as plain text without HTML tags
        if resp.status_code == 400 and "can't parse entities" in resp.text.lower():
            logger.warning("Telegram parse error, retrying without HTML parse_mode...")
            plain_text = re.sub(r'<[^>]+>', '', text)
            payload["text"] = plain_text
            payload.pop("parse_mode", None)
            retry_resp = requests.post(url, json=payload, timeout=10)
            if retry_resp.status_code == 200:
                return retry_resp.json()
            logger.error(f"Telegram API fallback ошибка ({retry_resp.status_code}): {retry_resp.text}")
            return None

        logger.error(f"Telegram API ошибка ({resp.status_code}): {resp.text}")
    except Exception as e:
        logger.error(f"Ошибка отправки в Telegram: {e}")
    return None


def edit_message_text(token, chat_id, message_id, text, reply_markup=None):
    url = f"https://api.telegram.org/bot{token}/editMessageText"
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code == 200:
            return resp.json()

        # Fallback if HTML parsing fails
        if resp.status_code == 400 and "can't parse entities" in resp.text.lower():
            logger.warning("Telegram edit parse error, retrying without HTML parse_mode...")
            plain_text = re.sub(r'<[^>]+>', '', text)
            payload["text"] = plain_text
            payload.pop("parse_mode", None)
            retry_resp = requests.post(url, json=payload, timeout=10)
            if retry_resp.status_code == 200:
                return retry_resp.json()

        logger.error(f"Telegram API editMessageText ошибка ({resp.status_code}): {resp.text}")
    except Exception as e:
        logger.error(f"Ошибка editMessageText: {e}")
    return None


def edit_message_reply_markup(token, chat_id, message_id, reply_markup):
    url = f"https://api.telegram.org/bot{token}/editMessageReplyMarkup"
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "reply_markup": reply_markup
    }
    try:
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code == 200:
            return resp.json()
        logger.error(f"Telegram API editMessageReplyMarkup ошибка ({resp.status_code}): {resp.text}")
    except Exception as e:
        logger.error(f"Ошибка editMessageReplyMarkup: {e}")
    return None


def answer_callback_query(token, callback_query_id, text=None, show_alert=False):
    url = f"https://api.telegram.org/bot{token}/answerCallbackQuery"
    payload = {"callback_query_id": callback_query_id, "show_alert": show_alert}
    if text:
        payload["text"] = text
    try:
        requests.post(url, json=payload, timeout=5)
    except Exception:
        pass


def clean_html(raw_html):
    if not raw_html:
        return ""
    text = re.sub(r'<[^>]+>', ' ', raw_html)
    text = html.unescape(text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


KNOWN_TECH = [
    ("Java 21", r'\bjava\s*21\b'),
    ("Java 17", r'\bjava\s*17\b'),
    ("Java", r'\bjava\b'),
    ("Kotlin", r'\bkotlin\b'),
    ("Spring Boot", r'\b(spring\s*boot|springboot|javaspringframework)\b'),
    ("Spring", r'\bspring(\s*framework)?\b'),
    ("PostgreSQL", r'\b(postgresql|postgres)\b'),
    ("MySQL", r'\bmysql\b'),
    ("SQL", r'\bsql\b'),
    ("Hibernate", r'\bhibernate\b'),
    ("JPA", r'\bjpa\b'),
    ("Docker", r'\bdocker\b'),
    ("Kubernetes", r'\b(kubernetes|k8s)\b'),
    ("Kafka", r'\b(apache\s*kafka|apachekafka|kafka)\b'),
    ("RabbitMQ", r'\brabbitmq\b'),
    ("Redis", r'\bredis\b'),
    ("REST API", r'\b(rest\s*api|restful|rest)\b'),
    ("Git", r'\bgit\b'),
    ("Maven", r'\bmaven\b'),
    ("Gradle", r'\bgradle\b'),
    ("Linux", r'\blinux\b'),
    ("CI/CD", r'\bci[/-]?cd\b'),
    ("Microservices", r'\b(microservices|микросервис[а-я]*)\b'),
    ("Clean Architecture", r'\b(clean\s*architecture|чистая\s*архитектура)\b'),
    ("JUnit / Mockito", r'\b(junit|mockito)\b'),
    ("Xray / VLESS", r'\b(xray(-core)?|vless|reality)\b'),
    ("Spring AI", r'\bspring\s*ai\b'),
    ("LangChain4j", r'\blangchain4j\b'),
    ("LLM / AI Agents", r'\b(llm|ai\s*agent[s]?|rag|genai|function\s*calling|pgvector)\b'),
    ("Sing-box", r'\bsing-box\b'),
    ("Remnawave", r'\bremnawave\b'),
    ("3X-UI", r'\b3x-ui\b'),
]

TAG_REMAP = {
    "javaspringframework": "Spring Boot",
    "springboot": "Spring Boot",
    "apachekafka": "Kafka",
    "postgresql": "PostgreSQL",
    "docker": "Docker",
    "kubernetes": "Kubernetes",
    "rabbitmq": "RabbitMQ",
    "redis": "Redis",
    "kotlin": "Kotlin",
    "java": "Java",
    "git": "Git",
    "hibernate": "Hibernate",
    "linux": "Linux",
    "gradle": "Gradle",
    "maven": "Maven",
    "remnawave": "Remnawave",
    "xray": "Xray / VLESS",
    "singbox": "Sing-box",
    "sing-box": "Sing-box",
    "restapi": "REST API",
    "rest": "REST API",
}


def extract_tech_stack(text, title=""):
    combined = f"{title} {text}".lower()
    stack = []
    seen = set()

    # 1. Извлекаем теги Хабра (#Java, #SpringBoot, etc.)
    habr_tags = re.findall(r'#([A-Za-z0-9_а-яА-Я]+)', text)
    for tag in habr_tags:
        t_low = tag.lower()
        if t_low in ["junior", "middle", "senior", "lead", "стажер", "intern"]:
            continue
        cleaned_name = TAG_REMAP.get(t_low, tag)
        if cleaned_name.lower() not in seen:
            seen.add(cleaned_name.lower())
            stack.append(cleaned_name)

    # 2. Сканируем по известным ключевым словам
    for name, pattern in KNOWN_TECH:
        if name.lower() in seen:
            continue
        if re.search(pattern, combined, re.IGNORECASE):
            if name == "Spring" and "Spring Boot" in stack:
                continue
            if name == "Java" and any(j in stack for j in ["Java 21", "Java 17"]):
                continue
            seen.add(name.lower())
            stack.append(name)

    return stack


def format_structured_description(desc, source=""):
    if not desc:
        return "Описание не указано"

    text = desc.strip()

    # Специфика Хабра: "Компания «X» ищет хорошего специалиста... Требуемые навыки: #..."
    if "Требуемые навыки:" in text:
        parts = text.split("Требуемые навыки:")
        info_part = parts[0].strip()
        skills_part = parts[1].strip() if len(parts) > 1 else ""

        info_part = re.sub(r'^Компания\s+[«"].*?[»"]\s+ищет\s+хорошего\s+специалиста\s+на\s+вакансию\s+[«"].*?[»"]\.?\s*', '', info_part)

        formatted_lines = []
        if info_part:
            conditions = [c.strip() for c in info_part.split('.') if c.strip()]
            if conditions:
                formatted_lines.append("<div class='mb-2 text-slate-300 font-medium'>📍 " + " • ".join(conditions) + "</div>")

        tags = [t.strip().lstrip('#') for t in skills_part.split(',') if t.strip()]
        tags = [t for t in tags if t.lower() not in ['junior', 'middle', 'senior', 'стажер', 'intern']]
        cleaned_tags = []
        seen = set()
        for t in tags:
            clean = TAG_REMAP.get(t.lower(), t)
            if clean.lower() not in seen:
                seen.add(clean.lower())
                cleaned_tags.append(clean)

        if cleaned_tags:
            formatted_lines.append("<div class='text-xs text-indigo-300 font-semibold mb-1'>🛠 Ключевые требования:</div><div class='text-slate-300 leading-relaxed'>" + " • ".join(cleaned_tags) + "</div>")

        if formatted_lines:
            return "".join(formatted_lines)

    # Общий случай (HH, GetMatch): разбиваем сплошной текст на секции с буллетами
    text = re.sub(r'(Требования|Обязанности|Условия|Мы предлагаем|Задачи|Стек|Стек технологий|Стек проекта):', r'\n\n###\1###\n', text)

    paragraphs = text.split('\n\n')
    out_paras = []
    for p in paragraphs:
        p = p.strip()
        if not p:
            continue
        m = re.match(r'^###([^#]+)###\s*(.*)$', p, re.DOTALL)
        if m:
            sec_name = m.group(1).strip()
            sec_body = m.group(2).strip()
            out_paras.append(f"<div class='mt-2.5 mb-1 text-xs font-bold text-indigo-300 uppercase tracking-wider'>📋 {sec_name}</div>")
            if sec_body:
                items = [it.strip(' .;•-') for it in sec_body.split(';') if it.strip(' .;•-')]
                if len(items) > 1:
                    bullets = "".join(f"<li class='ml-4 text-xs text-slate-300 leading-relaxed'>• {it}</li>" for it in items)
                    out_paras.append(f"<ul class='space-y-1 mb-2'>{bullets}</ul>")
                else:
                    out_paras.append(f"<div class='text-xs text-slate-300 leading-relaxed mb-2'>{sec_body}</div>")
        else:
            items = [it.strip(' .;•-') for it in p.split(';') if it.strip(' .;•-')]
            if len(items) >= 3:
                bullets = "".join(f"<li class='ml-4 text-xs text-slate-300 leading-relaxed'>• {it}</li>" for it in items)
                out_paras.append(f"<ul class='space-y-1 mb-2'>{bullets}</ul>")
            else:
                out_paras.append(f"<p class='text-xs text-slate-300 leading-relaxed mb-2'>{p}</p>")

    return "".join(out_paras)


# --- ИСТОЧНИК 1: ХАБР КАРЬЕРА (RSS & Парсинг) ---

def fetch_habr_vacancies(query_url):
    try:
        resp = requests.get(query_url, headers=HTTP_HEADERS, timeout=3)
        if resp.status_code != 200:
            logger.warning(f"Ошибка загрузки RSS {query_url}: статус {resp.status_code}")
            return []

        root = ET.fromstring(resp.content)
        items = root.findall("./channel/item")
        vacancies = []

        for item in items:
            guid = item.find("guid").text if item.find("guid") is not None else ""
            title = item.find("title").text if item.find("title") is not None else ""
            link = item.find("link").text if item.find("link") is not None else ""
            company = item.find("author").text if item.find("author") is not None else "Не указана"
            pub_date = item.find("pubDate").text if item.find("pubDate") is not None else ""
            description = item.find("description").text if item.find("description") is not None else ""

            vid = f"habr_{guid}" if not guid.startswith("habr_") else guid
            vacancies.append({
                "id": vid,
                "raw_id": guid,
                "source": "habr",
                "source_name": "Хабр Карьера",
                "source_emoji": "🔵",
                "title": title,
                "link": link,
                "company": company,
                "pub_date": pub_date,
                "salary": "",
                "description": clean_html(description)
            })

        return vacancies
    except Exception as e:
        logger.error(f"Ошибка парсинга RSS {query_url}: {e}")
        return []


def fetch_habr_vacancy_by_id(v_id):
    raw_id = v_id.replace("habr_", "")
    url = f"https://career.habr.com/vacancies/{raw_id}"
    try:
        resp = requests.get(url, headers=HTTP_HEADERS, timeout=12)
        if resp.status_code == 200:
            html_text = resp.text
            title_m = re.search(r'<title>([^<]+)</title>', html_text)
            raw_title = title_m.group(1) if title_m else f"Вакансия #{raw_id}"
            title = raw_title.replace("— Хабр Карьера", "").replace("Вакансия", "").strip()
            company = "Не указана"
            if "в компании" in title:
                parts = title.split("в компании")
                title = parts[0].strip()
                company = parts[1].strip()

            desc_m = re.search(r'<meta name="description" content="([^"]+)"', html_text)
            desc = desc_m.group(1) if desc_m else ""

            return {
                "id": f"habr_{raw_id}",
                "raw_id": raw_id,
                "source": "habr",
                "source_name": "Хабр Карьера",
                "source_emoji": "🔵",
                "title": title,
                "company": company,
                "salary": "",
                "description": clean_html(desc),
                "link": url
            }
    except Exception as e:
        logger.error(f"Не удалось загрузить вакансию Хабра {raw_id}: {e}")
    return None


# --- ИСТОЧНИК 2: HEADHUNTER (hh.ru Web State Search & Full Vacancy) ---

def fetch_hh_vacancies(query, experience="noExperience", max_pages=2, schedule=None):
    encoded_q = urllib.parse.quote(query)
    results = []
    seen_page_vids = set()
    for page in range(max_pages):
        exp_part = f"&experience={experience}" if experience else ""
        sched_part = f"&schedule={schedule}" if schedule else ""
        url = f"https://hh.ru/search/vacancy?text={encoded_q}{exp_part}{sched_part}&order_by=publication_time&page={page}"
        try:
            resp = requests.get(url, headers=HTTP_HEADERS, timeout=12)
            if resp.status_code == 200:
                templates = re.findall(r'<template[^>]*>(.*?)</template>', resp.text, re.DOTALL)
                if templates:
                    data = json.loads(html.unescape(templates[0]))
                    vacancies = data.get("vacancySearchResult", {}).get("vacancies", [])
                    if not vacancies:
                        break
                    for v in vacancies:
                        vid = str(v.get("vacancyId", ""))
                        if not vid or vid in seen_page_vids:
                            continue
                        seen_page_vids.add(vid)
                        name = v.get("name", "")
                        company = v.get("company", {}).get("name", "?? ???????")
                        url_link = v.get("links", {}).get("desktop") or f"https://hh.ru/vacancy/{vid}"

                        comp = v.get("compensation", {})
                        salary_str = ""
                        if comp and not comp.get("noCompensation"):
                            cur = comp.get("currencyCode", "RUR")
                            if comp.get("from") and comp.get("to"):
                                salary_str = f"?? {comp['from']} ?? {comp['to']} {cur}"
                            elif comp.get("from"):
                                salary_str = f"?? {comp['from']} {cur}"
                            elif comp.get("to"):
                                salary_str = f"?? {comp['to']} {cur}"

                        exp = v.get("workExperience", experience)
                        tags_list = [t.get("text") for t in v.get("tags", []) if isinstance(t, dict) and t.get("text")]
                        tags_str = ", ".join(tags_list)

                        results.append({
                            "id": f"hh_{vid}",
                            "raw_id": vid,
                            "source": "hh",
                            "source_name": "HeadHunter",
                            "source_emoji": "??",
                            "title": name,
                            "company": company,
                            "salary": salary_str,
                            "link": url_link,
                            "description": f"????: {exp}. ????: {tags_str}"
                        })
            time.sleep(0.3)
        except Exception as e:
            logger.error(f"?????? ?????? HeadHunter ('{query}', {experience}, page={page}): {e}")
            break
    return results

def fetch_hh_vacancy_full(raw_id):
    url = f"https://hh.ru/vacancy/{raw_id}"
    try:
        resp = requests.get(url, headers=HTTP_HEADERS, timeout=12)
        if resp.status_code == 200:
            templates = re.findall(r'<template[^>]*>(.*?)</template>', resp.text, re.DOTALL)
            if templates:
                data = json.loads(html.unescape(templates[0]))
                full = data.get("vacancyView", {}).get("vacancyFull", {}).get("vacancy", {})
                if full:
                    clean_desc = clean_html(full.get("description", ""))
                    comp = full.get("compensation", {})
                    salary_str = ""
                    if comp and not comp.get("noCompensation"):
                        cur = comp.get("currencyCode", "RUR")
                        if comp.get("from") and comp.get("to"):
                            salary_str = f"от {comp['from']} до {comp['to']} {cur}"
                        elif comp.get("from"):
                            salary_str = f"от {comp['from']} {cur}"
                        elif comp.get("to"):
                            salary_str = f"до {comp['to']} {cur}"

                    return {
                        "id": f"hh_{raw_id}",
                        "raw_id": raw_id,
                        "source": "hh",
                        "source_name": "HeadHunter",
                        "source_emoji": "🔴",
                        "title": full.get("name", ""),
                        "company": full.get("company", {}).get("name", "Не указана"),
                        "description": clean_desc,
                        "salary": salary_str,
                        "link": url
                    }
    except Exception as e:
        logger.error(f"Не удалось загрузить детальное описание hh.ru #{raw_id}: {e}")
    return None


# --- ИСТОЧНИК 3: GETMATCH (JSON API) ---

def fetch_getmatch_vacancies():
    results = []
    seen_ids = set()
    # Запрашиваем специализации Java / Scala и Kotlin
    for sp in ["java_scala", "kotlin"]:
        url = f"https://getmatch.ru/api/offers?page=1&sp={sp}"
        try:
            resp = requests.get(url, headers=HTTP_HEADERS, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                for off in data.get("offers", []):
                    oid = str(off.get("id"))
                    if oid in seen_ids:
                        continue
                    seen_ids.add(oid)

                    title = off.get("position", "")
                    comp_obj = off.get("company")
                    comp_name = "IT-Компания"
                    if isinstance(comp_obj, dict) and comp_obj.get("name"):
                        comp_name = comp_obj.get("name")
                    elif off.get("company_name"):
                        comp_name = off.get("company_name")

                    url_path = off.get("url", "")
                    link = f"https://getmatch.ru{url_path}" if url_path else f"https://getmatch.ru/vacancies/{oid}"
                    desc = clean_html(off.get("offer_description", ""))

                    t_low = title.lower()
                    d_low = desc.lower()
                    if not any(k in t_low or k in d_low for k in ["junior", "джуниор", "джун", "стажер", "стажёр", "trainee", "intern"]):
                        continue

                    salary_str = ""
                    s_from = off.get("salary_display_from")
                    s_to = off.get("salary_display_to")
                    cur = off.get("salary_currency") or "RUB"
                    if s_from and s_to:
                        salary_str = f"от {s_from} до {s_to} {cur}"
                    elif s_from:
                        salary_str = f"от {s_from} {cur}"
                    elif s_to:
                        salary_str = f"до {s_to} {cur}"

                    loc_items = off.get("location_items", [])
                    loc_labels = [it.get("label", "") for it in loc_items if it.get("label")]
                    is_gm_remote = any(it.get("format") == "remote" for it in loc_items)
                    loc_str = ", ".join(loc_labels)
                    if is_gm_remote:
                        loc_str = f"Удалённо ({loc_str})" if loc_str else "Удалённо"

                    results.append({
                        "id": f"gm_{oid}",
                        "raw_id": oid,
                        "source": "getmatch",
                        "source_name": "GetMatch",
                        "source_emoji": "🟣",
                        "title": title,
                        "company": comp_name,
                        "salary": salary_str,
                        "location": loc_str,
                        "link": link,
                        "description": desc
                    })
        except Exception as e:
            logger.error(f"Ошибка запроса GetMatch API ({sp}): {e}")
    return results


# --- ИСТОЧНИК 4: REMOTIVE (Global Remote JSON API) ---


DEFAULT_SUPERJOB_KEY = "v1.r00000001769221ce7a8d0a2dd47c132c9fed70d06afa65121b02277f364d08fd_api3web.5be7a889f65ecefb876179d577ff16aff5a9db59"

def fetch_superjob_vacancies(query_item, count=40, secret_key=None):
    key = secret_key or DEFAULT_SUPERJOB_KEY
    params = {
        "order_by[date]": "desc",
        "count": count
    }
    if isinstance(query_item, dict):
        params.update(query_item)
    else:
        params["keyword"] = str(query_item)

    headers = {
        "X-Api-App-Id": key,
        "User-Agent": "SuperJob-Career-Bot/1.0"
    }
    try:
        resp = requests.get("https://api.superjob.ru/2.0/vacancies/", headers=headers, params=params, timeout=12)
        if resp.status_code == 200:
            data = resp.json()
            results = []
            for obj in data.get("objects", []):
                vid = str(obj.get("id", ""))
                if not vid:
                    continue
                title = obj.get("profession", "")
                company = obj.get("firm_name", "Не указана")
                link = obj.get("link", f"https://www.superjob.ru/vakansii/{vid}.html")
                cand_text = obj.get("candidat", "") or ""
                work_text = obj.get("work", "") or ""
                desc = f"{cand_text}\n\n{work_text}".strip()

                p_from = obj.get("payment_from")
                p_to = obj.get("payment_to")
                cur = obj.get("currency", "rub")
                salary_str = ""
                if p_from and p_to:
                    salary_str = f"от {p_from} до {p_to} {cur}"
                elif p_from:
                    salary_str = f"от {p_from} {cur}"
                elif p_to:
                    salary_str = f"до {p_to} {cur}"

                town = obj.get("town", {}).get("title", "")
                is_rem = obj.get("work_type", {}).get("id") == 2 or obj.get("place_of_work", {}).get("id") == 2 or ("удал" in title.lower()) or ("удал" in desc.lower())
                loc_str = f"Удалённо ({town})" if (is_rem and town) else ("Удалённо" if is_rem else town)

                results.append({
                    "id": f"sj_{vid}",
                    "raw_id": vid,
                    "source": "superjob",
                    "source_name": "SuperJob",
                    "source_emoji": "💼",
                    "title": title,
                    "company": company,
                    "salary": salary_str,
                    "location": loc_str,
                    "link": link,
                    "description": desc
                })
            return results
        elif resp.status_code == 403:
            logger.warning("SuperJob API вернул 403 (необходим действующий X-Api-App-Id ключ).")
    except Exception as e:
        logger.error(f"Ошибка поиска SuperJob ({query_item}): {e}")
    return []




# --- ИСТОЧНИК 7: LINKEDIN (Guest Search & Details API) ---

HTTP_HEADERS_LI = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9,ru;q=0.8"
}

def fetch_linkedin_vacancies(query_item, max_results=15):
    """
    Публичный гостевой поиск вакансий LinkedIn без авторизации и платных API.
    Поддерживает фильтры опыта: f_E=1 (Internship), f_E=2 (Entry level)
    и workplace type: f_WT=2 (Remote)
    """
    if isinstance(query_item, dict):
        kw = query_item.get("keywords", "Junior Java")
        loc = query_item.get("location", "Remote")
        f_wt = query_item.get("f_WT", "2")
    else:
        kw = str(query_item)
        loc = "Remote"
        f_wt = "2"

    wt_param = f"&f_WT={f_wt}" if f_wt else ""
    url = f"https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?keywords={requests.utils.quote(kw)}&location={requests.utils.quote(loc)}&f_E=1,2{wt_param}&start=0"
    results = []
    try:
        resp = requests.get(url, headers=HTTP_HEADERS_LI, timeout=12)
        if resp.status_code == 200:
            cards = re.findall(r'<div class="[^"]*base-search-card[^"]*"(.*?)</li>', resp.text, re.DOTALL)
            for c in cards[:max_results]:
                jid_m = re.search(r'data-entity-urn="urn:li:jobPosting:(\d+)"', c)
                if not jid_m:
                    continue
                jid = jid_m.group(1)

                title_m = re.search(r'<h3 class="base-search-card__title">\s*(.*?)\s*</h3>', c, re.DOTALL)
                comp_m = re.search(r'<h4 class="base-search-card__subtitle">\s*(.*?)\s*</h4>', c, re.DOTALL)
                loc_m = re.search(r'<span class="job-search-card__location">\s*(.*?)\s*</span>', c, re.DOTALL)
                link_m = re.search(r'<a class="base-card__full-link[^"]*"\s+href="([^"]+)"', c)

                title = re.sub(r'<[^>]+>', '', html.unescape(title_m.group(1))).strip() if title_m else "Без названия"
                company = re.sub(r'<[^>]+>', '', html.unescape(comp_m.group(1))).strip() if comp_m else "Не указана"
                location = re.sub(r'<[^>]+>', '', html.unescape(loc_m.group(1))).strip() if loc_m else ""
                link = link_m.group(1).split("?")[0] if link_m else f"https://www.linkedin.com/jobs/view/{jid}"

                results.append({
                    "id": f"li_{jid}",
                    "raw_id": jid,
                    "source": "linkedin",
                    "source_name": "LinkedIn",
                    "source_emoji": "🌐",
                    "title": title,
                    "company": company,
                    "location": location,
                    "salary": "Не указана",
                    "link": link,
                    "description": f"Позиция {title} в {company}. Локация: {location}."
                })
        elif resp.status_code == 429:
            logger.warning("LinkedIn Guest API Rate limit (429). Пауза 5 сек перед следующими запросами...")
            time.sleep(5.0)
        else:
            logger.warning(f"LinkedIn HTTP error {resp.status_code}")
    except Exception as e:
        logger.error(f"Ошибка сбора вакансий LinkedIn ({query_item}): {e}")
    return results


def fetch_linkedin_vacancy_full(raw_id):
    """
    Получение полного описания вакансии LinkedIn через публичный гостевой эндпоинт.
    """
    url = f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{raw_id}"
    try:
        resp = requests.get(url, headers=HTTP_HEADERS_LI, timeout=10)
        if resp.status_code == 200:
            desc_m = re.search(r'<div class="show-more-less-html__markup[^"]*">\s*(.*?)\s*</div>', resp.text, re.DOTALL)
            if desc_m:
                raw_html = desc_m.group(1)
                clean_text = re.sub(r'<br\s*/?>', '\n', raw_html)
                clean_text = re.sub(r'<[^>]+>', '', clean_text)
                return html.unescape(clean_text).strip()
        elif resp.status_code == 429:
            logger.warning(f"LinkedIn full vacancy 429 для ID {raw_id}. Пауза 3 сек...")
            time.sleep(3.0)
    except Exception as e:
        logger.error(f"Ошибка получения деталей вакансии LinkedIn ({raw_id}): {e}")
    return ""




# --- ИСТОЧНИК 8: TELEGRAM PUBLIC CHANNELS (IT & Junior/Intern Feeds) ---

def fetch_tg_channel_vacancies(channel_name, max_messages=25):
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
        if resp.status_code == 200:
            chunks = resp.text.split('class="tgme_widget_message ')
            now_utc = datetime.now(timezone.utc)
            for c in chunks[1:]:
                post_m = re.search(r'data-post="([^"]+)"', c)
                text_m = re.search(r'<div class="[^"]*tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', c, re.DOTALL)
                if not post_m or not text_m:
                    continue

                # Проверка свежести: пропускаем посты старше 7 дней
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

                # Извлечение внешней ссылки для отклика
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
                # Проверка: пост должен быть описанием вакансии
                is_job = any(k in clean_lower for k in [
                    "ваканси", "ищем", "требуется", "developer", "разработчик",
                    "стажер", "стажёр", "junior", "intern", "в поиске", "локация:",
                    "формат работы:", "требования:", "стек:", "грейд / опыт"
                ])
                if not is_job:
                    continue

                # Игнорируем рекламу курсов, опросы и мемы
                if any(k in clean_lower for k in ["опрос", "что делаете с таким", "вебинар", "бесплатный интенсив", "почему ты получаешь отказ"]):
                    continue

                # Игнорируем подборки и дайджесты из множества вакансий
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
                    "source_emoji": "📢"
                })
    except Exception as e:
        logger.error(f"Ошибка сбора постов из TG канала @{channel_name}: {e}")
    return results


# --- ИСТОЧНИК 9: РАБОТА.РУ (rabota.ru - Экосистема Сбера и IT РФ) ---

NON_IT_TITLES_RABOTA = [
    "турбин", "газов", "окон", "недвижим", "бариста", "повар", "риелтор", "досмотр",
    "шлифовщик", "парикмахер", "водитель", "курьер", "уборщ", "склад", "продавец",
    "кассир", "бухгалтер", "юрист", "механик", "электрик", "слесарь", "монтажник",
    "врач", "медсестр", "фармацевт", "охранник", "грузчик", "комплектовщик", "токарь",
    "расточник", "станк", "чпу", "камн", "автомобил", "ремонт", "оклейк", "производств"
]

IT_KEYWORDS_RABOTA = [
    "java", "spring", "jvm", "разработчик", "программист", "developer", "backend",
    "бэкенд", "engineer", "инженер-программист", "тестировщик", "qa", "aqa", "devops",
    "data science", "it-стажер", "it-стажёр", "it стажер", "it стажёр", "fullstack",
    "full-stack", "бэкэнд", "администрировани", "аналитик"
]

def fetch_rabota_vacancies(query):
    """
    Парсер вакансий с Работа.ру (rabota.ru) - ключевого агрегатора экосистемы Сбера.
    Ищет по всей России (&all_regions=1), отсекает не-IT шум.
    """
    clean_q = requests.utils.quote(str(query))
    url = f"https://www.rabota.ru/vacancy/?query={clean_q}&all_regions=1"
    results = []
    try:
        resp = requests.get(url, headers=HTTP_HEADERS, timeout=12)
        if resp.status_code == 200:
            articles = re.findall(r'<article[^>]*class="[^"]*vacancy-preview-card[^"]*"[^>]*>(.*?)</article>', resp.text, re.DOTALL)
            for art in articles:
                link_m = re.search(r'href="(/vacancy/(\d+)/?[^"]*)"[^>]*>(.*?)</a>', art, re.DOTALL)
                if not link_m:
                    continue
                rel_link = link_m.group(1).split('?')[0]
                vac_id = link_m.group(2)
                title = html.unescape(re.sub(r'<[^>]+>', '', link_m.group(3)).strip())
                title_lower = title.lower()

                # Отсекаем не-IT профессии (бариста, риелторы, мастера и т.д.)
                if any(k in title_lower for k in NON_IT_TITLES_RABOTA):
                    continue

                # Должно соответствовать IT-профилю
                if not any(k in title_lower for k in IT_KEYWORDS_RABOTA):
                    continue

                # Если запрос специфичен для Java, проверяем соответствие
                if "java" in str(query).lower() and not any(k in title_lower for k in ["java", "jvm", "стажер", "стажёр", "junior", "intern", "trainee"]):
                    continue

                comp_m = re.search(r'class="[^"]*company-name[^"]*"[^>]*>(.*?)</span>', art, re.DOTALL)
                company = html.unescape(re.sub(r'<[^>]+>', '', comp_m.group(1)).strip()) if comp_m else "Работа.ру"

                sal_m = re.search(r'class="[^"]*vacancy-preview-card__salary[^"]*"[^>]*>(.*?)</div>', art, re.DOTALL)
                salary = ""
                if sal_m:
                    salary = ' '.join(html.unescape(re.sub(r'<[^>]+>', ' ', sal_m.group(1))).split())

                loc_m = re.search(r'class="[^"]*address-text[^"]*"[^>]*>(.*?)<', art, re.DOTALL)
                loc = html.unescape(loc_m.group(1).strip()) if loc_m else ""

                desc_m = re.search(r'class="[^"]*short-description[^"]*"[^>]*>(.*?)</div>', art, re.DOTALL)
                desc = ""
                if desc_m:
                    desc = ' '.join(html.unescape(re.sub(r'<[^>]+>', ' ', desc_m.group(1))).split())
                if not desc:
                    desc = f"{title}. Компания: {company}. Локация: {loc}"

                full_link = f"https://www.rabota.ru{rel_link}"
                results.append({
                    "id": f"rabota_{vac_id}",
                    "raw_id": vac_id,
                    "title": title,
                    "company": company,
                    "salary": salary,
                    "location": loc,
                    "link": full_link,
                    "description": desc,
                    "source": "rabota",
                    "source_name": "Работа.ру",
                    "source_emoji": "🔴"
                })
    except Exception as e:
        logger.error(f"Ошибка сбора вакансий с Работа.ру ({query}): {e}")
    return results


def fetch_rabota_vacancy_full(vac_id):
    """
    Получение полного описания вакансии с Работа.ру через Nuxt state или HTML.
    """
    url = f"https://www.rabota.ru/vacancy/{vac_id}/"
    try:
        resp = requests.get(url, headers=HTTP_HEADERS, timeout=10)
        if resp.status_code == 200:
            # Извлечение полного описания из window.__NUXT__
            matches = re.findall(r'description:"(.*?)"(?=,\w+:)', resp.text)
            best = ""
            for m in matches:
                if len(m) > len(best):
                    best = m
            if best:
                try:
                    decoded = best.encode('utf-8').decode('unicode-escape')
                    clean = re.sub(r'</?(?:p|li|div|ul|h\d|strong|b|em|i|br)[^>]*>', '\n', decoded)
                    clean = re.sub(r'<[^>]+>', ' ', clean)
                    clean = html.unescape(clean)
                    text = '\n'.join([l.strip() for l in clean.splitlines() if l.strip()])
                    if len(text) > 80:
                        return text
                except Exception:
                    pass

            # Резервный парсинг HTML-тегов
            clean_html = re.sub(r'<script.*?</script>', '', resp.text, flags=re.DOTALL)
            clean_html = re.sub(r'<style.*?</style>', '', clean_html, flags=re.DOTALL)
            clean_html = re.sub(r'</?(?:p|li|div|ul|h\d)[^>]*>', '\n', clean_html)
            clean_text = re.sub(r'<[^>]+>', ' ', clean_html)
            clean_text = html.unescape(clean_text)
            lines = [l.strip() for l in clean_text.splitlines() if l.strip()]
            return '\n'.join(lines[:120])
    except Exception as e:
        logger.error(f"Ошибка получения деталей вакансии Работа.ру ({vac_id}): {e}")
    return ""


# --- ИСТОЧНИК 10: РАБОТА РОССИИ (opendata.trudvsem.ru - Официальный портал РФ) ---

def fetch_trudvsem_vacancies(query="java", limit=40):
    """
    Официальный открытый REST API портала 'Работа России' (opendata.trudvsem.ru).
    Возвращает вакансии аккредитованных IT-компаний, НИИ, банков и предприятий РФ.
    """
    clean_q = requests.utils.quote(str(query))
    url = f"https://opendata.trudvsem.ru/api/v1/vacancies?text={clean_q}"
    results = []
    try:
        resp = requests.get(url, headers=HTTP_HEADERS, timeout=25)
        if resp.status_code == 200:
            data = resp.json()
            vac_list = data.get("results", {}).get("vacancies", [])
            for item in vac_list[:limit]:
                vac = item.get("vacancy", {})
                vid = vac.get("id")
                if not vid:
                    continue
                title = vac.get("job-name", "Без названия")
                title_lower = title.lower()

                # Базовый отсев не-IT шума
                if any(k in title_lower for k in NON_IT_TITLES_RABOTA):
                    continue

                company = vac.get("company", {}).get("name", "Не указана")

                sal_min = vac.get("salary_min")
                sal_max = vac.get("salary_max")
                if sal_min and sal_max and sal_min != sal_max:
                    salary = f"{sal_min} - {sal_max} руб."
                elif sal_min or sal_max:
                    salary = f"{sal_min or sal_max} руб."
                else:
                    salary = ""

                duty = vac.get("duty", "")
                req = vac.get("requirement", {}).get("qualification", "")
                desc = f"{duty}\nТребования: {req}".strip()
                if not desc:
                    desc = f"Позиция {title} в {company}."

                vac_url = vac.get("vac_url") or f"https://trudvsem.ru/vacancy/card/{vac.get('company', {}).get('ogrn', '')}/{vid}"

                addresses = vac.get("addresses", {}).get("address", [])
                loc = ""
                if addresses and isinstance(addresses, list):
                    loc = addresses[0].get("location", "")
                elif isinstance(addresses, dict):
                    loc = addresses.get("location", "")

                results.append({
                    "id": f"trud_{vid}",
                    "raw_id": vid,
                    "title": title,
                    "company": company,
                    "salary": salary,
                    "location": loc,
                    "link": vac_url,
                    "description": desc,
                    "source": "trudvsem",
                    "source_name": "Работа России",
                    "source_emoji": "🇷🇺"
                })
    except Exception as e:
        logger.error(f"Ошибка сбора вакансий с Работа России ({query}): {e}")
    return results


def fetch_remotive_vacancies():
    results = []
    for cat in ["software-dev", "devops"]:
        try:
            url = f"https://remotive.com/api/remote-jobs?category={cat}&limit=15"
            resp = requests.get(url, headers=HTTP_HEADERS, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                for job in data.get("jobs", [])[:15]:
                    jid = str(job.get("id"))
                    results.append({
                        "id": f"rem_{jid}",
                        "raw_id": jid,
                        "source": "remotive",
                        "source_name": "Remotive Remote",
                        "source_emoji": "🟢",
                        "title": job.get("title", ""),
                        "company": job.get("company_name", "Remote IT Company"),
                        "salary": job.get("salary", ""),
                        "link": job.get("url", ""),
                        "description": clean_html(job.get("description", ""))
                    })
        except Exception as e:
            logger.error(f"Ошибка запроса Remotive API ({cat}): {e}")
    return results


# --- ИСТОЧНИК 5: СЕТИ (vseti.app - IT & Digital Вакансии) ---

def fetch_vseti_vacancy_full(url):
    try:
        r = requests.get(url, headers=HTTP_HEADERS, timeout=8)
        if r.status_code != 200:
            return "", ""

        m = re.search(r'<div[^>]*class="[^"]*w-richtext[^"]*"[^>]*>(.*?)</div>', r.text, re.DOTALL)
        desc = ""
        if m:
            desc = clean_html(m.group(1))

        salary = ""
        sal_m = re.search(r'(\d+[\d\s]*(?:–|-)\s*\d+[\d\s]*\s*(?:₽|руб|\$|EUR|usd))', desc, re.I)
        if not sal_m:
            sal_m = re.search(r'((?:от|до)\s*\d+[\d\s]*\s*(?:₽|руб|\$|EUR|usd))', desc, re.I)
        if sal_m:
            salary = sal_m.group(1).strip()

        return desc, salary
    except Exception as e:
        logger.error(f"Ошибка загрузки деталей {url}: {e}")
        return "", ""


def fetch_vseti_vacancies(max_pages=6):
    results = []
    seen_ids = set()

    for p in range(1, max_pages + 1):
        url = f"https://www.vseti.app/jobs?3835ea8c_page={p}"
        try:
            r = requests.get(url, headers=HTTP_HEADERS, timeout=10)
            if r.status_code != 200:
                break

            cards = re.findall(r'<a([^>]*class="[^"]*card-jobs[^"]*"[^>]*)>(.*?)</a>', r.text, re.DOTALL)
            if not cards:
                break

            for tag_attrs, inner in cards:
                href_m = re.search(r'href="([^"]+)"', tag_attrs)
                id_m = re.search(r'id="([^"]+)"', tag_attrs)
                title_m = re.search(r'fs-cmsfilter-field="jobs-name"[^>]*>(.*?)</', inner)
                comp_m = re.search(r'fs-cmsfilter-field="company-name"[^>]*>(.*?)</', inner)
                format_m = re.search(r'fs-cmsfilter-field="format"[^>]*>(.*?)</', inner)
                location_m = re.search(r'fs-cmsfilter-field="location"[^>]*>(.*?)</', inner)

                link = href_m.group(1) if href_m else ""
                oid = id_m.group(1) if id_m else (link.split("/")[-1] if link else "")
                if not oid or oid in seen_ids:
                    continue
                seen_ids.add(oid)

                title = html.unescape(title_m.group(1).strip()) if title_m else ""
                comp = html.unescape(comp_m.group(1).strip()) if comp_m else "Не указана"
                fmt = html.unescape(format_m.group(1).strip()) if format_m else ""
                loc = html.unescape(location_m.group(1).strip()) if location_m else ""

                t_low = title.lower()
                # Быстрый фильтр: интересует только разработка/IT (Java, VPN, backend, стажер, junior)
                if not any(k in t_low for k in ["java", "spring", "jvm", "kotlin", "backend", "бэкенд", "разработчик", "developer", "vpn", "стажер", "стажёр", "junior"]):
                    continue

                if any(k in t_low for k in ["qa", "тестиров", "ios", "android", "flutter", "react", "frontend", "дизайн", "маркетолог", "менеджер", "копирайтер", "recruiter", "hr"]):
                    continue

                time.sleep(0.3)
                desc, salary = fetch_vseti_vacancy_full(link)

                loc_str = fmt
                if loc and loc != "Другое" and loc not in loc_str:
                    loc_str = f"{fmt} ({loc})" if fmt else loc

                results.append({
                    "id": f"vseti_{oid}",
                    "raw_id": oid,
                    "source": "vseti",
                    "source_name": "СЕТИ",
                    "source_emoji": "🌐",
                    "title": title,
                    "company": comp,
                    "salary": salary,
                    "location": loc_str,
                    "link": link,
                    "description": desc
                })

        except Exception as e:
            logger.error(f"Ошибка парсинга vseti.app стр. {p}: {e}")
            break

    return results


# --- ЕДИНЫЙ ПОИСК ВАКАНСИИ ПО ID ---

def fetch_vacancy_by_id(v_id):
    cached = get_from_cache(v_id)
    if cached and len(cached.get("description", "")) > 80:
        return cached

    if v_id.startswith("hh_"):
        raw_id = v_id.replace("hh_", "")
        v = fetch_hh_vacancy_full(raw_id)
        if v:
            save_to_cache(v)
            return v

    elif v_id.startswith("habr_") or v_id.isdigit():
        v = fetch_habr_vacancy_by_id(v_id)
        if v:
            save_to_cache(v)
            return v

    elif v_id.startswith("vseti_"):
        raw_id = v_id.replace("vseti_", "")
        url = f"https://www.vseti.app/vakansii/{raw_id}"
        desc, sal = fetch_vseti_vacancy_full(url)
        if desc:
            cached = cached or {}
            cached.update({"id": v_id, "raw_id": raw_id, "source": "vseti", "source_name": "СЕТИ", "link": url, "description": desc, "salary": sal})
            save_to_cache(cached)
            return cached

    elif v_id.startswith("rabota_"):
        raw_id = v_id.replace("rabota_", "")
        desc = fetch_rabota_vacancy_full(raw_id)
        if desc:
            cached = cached or {}
            cached.update({"id": v_id, "raw_id": raw_id, "source": "rabota", "source_name": "Работа.ру", "description": desc})
            save_to_cache(cached)
            return cached

    elif v_id.startswith("sj_"):
        raw_id = v_id.replace("sj_", "")
        try:
            config = load_config()
            sj_key = config.get("superjob_secret_key") or DEFAULT_SUPERJOB_KEY
            r = requests.get(f"https://api.superjob.ru/2.0/vacancies/{raw_id}/", headers={"X-Api-App-Id": sj_key, "User-Agent": "SuperJob-Bot/1.0"}, timeout=10)
            if r.status_code == 200:
                d = r.json()
                cand = d.get("candidat", "") or ""
                work = d.get("work", "") or ""
                full_desc = f"{cand}\n\n{work}".strip()
                cached = cached or {}
                cached.update({"id": v_id, "raw_id": raw_id, "source": "superjob", "source_name": "SuperJob", "description": full_desc})
                save_to_cache(cached)
                return cached
        except Exception as e:
            logger.error(f"Ошибка получения деталей SuperJob ({raw_id}): {e}")

    return cached


def quick_prefilter_reject(title, description, company=""):
    title_lower = title.lower()
    desc_lower = description.lower() if description else ""
    comp_lower = company.lower() if company else ""
    full_text = f"{title_lower} {desc_lower} {comp_lower}"

    # 0.0. Отсев дайджестов, подборок и агрегированных постов
    if any(d in title_lower for d in ["подборка", "дайджест", "digest", "вакансии недели", "вакансии дня"]):
        return True
    if any(d in full_text[:140] for d in ["подборка вакансий", "дайджест вакансий", "подборка junior", "подборка стажер", "подборка стажёр"]):
        return True

    # 0.1. Строгий отсев не-разработки: QA / AQA / Тестирование
    if re.search(r'\b(qa|aqa|tester|testing)\b', title_lower) or any(qa in title_lower for qa in ["тестиров", "тестер", "automation qa", "qa automation", "автотест", "test engineer", "test automation"]):
        return True

    # 0.2. Строгий отсев техподдержки, дежурных инженеров, сопровождения
    if any(sup in title_lower for sup in ["поддержк", "сопровожден", "дежурный инженер", "support", "helpdesk", "service desk", "администратор бд"]):
        return True

    # 0.3. Строгий отсев AI-тренеров, разметчиков данных, репетиторов и онлайн-школ (преподавание строго отсекаем)
    if any(trn in title_lower for trn in ["ai-тренер", "ai тренер", "тренер", "разметчик", "разметка кода", "code trainer", "преподавател", "тьютор", "репетитор", "учитель", "педагог", "ментор", "наставник", "куратор"]):
        return True

    # 0.4. Строгий отсев не-IT профессий и аналитики данных
    non_target_professions = [
        "юрист", "адвокат", "редактор", "копирайтер", "переводчик", "филолог", "врач", "медицин",
        "историк", "химик", "биолог", "гуманитар", "лингвист", "бухгалтер", "менеджер по продажам",
        "маркетолог", "дизайнер", "контент", "smm", "шахмат", "вожатый", "аниматор", "мебел",
        "data scientist", "дата сайентист", "data analyst", "аналитик данных", "bi аналитик", "системный аналитик", "бизнес-аналитик", "бизнес аналитик"
    ]
    for ntp in non_target_professions:
        if ntp in title_lower:
            return True

    # 0.5. Черный список компаний (Aston и др.)
    config = load_config()
    blacklisted = config.get("blacklisted_companies", ["aston", "астон"])
    for b in blacklisted:
        b_clean = b.lower().strip()
        if not b_clean:
            continue
        if b_clean in comp_lower or b_clean in title_lower:
            return True
        if f"компания {b_clean}" in desc_lower or f"компания «{b_clean}" in desc_lower or f'компания "{b_clean}"' in desc_lower:
            return True
        if b_clean in ["aston", "астон"] and (f" {b_clean} " in full_text or f"«{b_clean}»" in full_text):
            return True

    # 0.6. Отсев курсов и инфобизнеса
    course_ad_markers = [
        "бесплатные курс", "бесплатных курс", "обучение на курс",
        "пройти курс", "лаборатория обучени", "школа программирования", "обучающий курс",
        "интенсив с последующ", "пройти обучение на наш", "вступительные испытания на курс",
        "предлагаем обучение на", "бесплатном обучении", "курсы для начинающ",
        "стоимость курса", "платный курс", "платные курсы", "купить курс"
    ]
    for cm in course_ad_markers:
        if cm in full_text:
            return True
    if any(cm in title_lower for cm in ["курс программирования", "интенсив с трудоустройством", "обучение разработчик"]):
        return True
    if ("онлайн-курс" in full_text or "онлайн курс" in full_text):
        if not any(perk in full_text for perk in ["оплат", "компенсац", "доступ к", "бюджет на", "льгот", "бенефит"]):
            return True

    # 1. Строгий отсев Middle / Senior / Lead по заголовку
    hard_stops_title = [
        "middle", "мидл", "миддл", "senior", "синьор", "сеньор", "старший",
        "lead", "лид", "лидер", "тимлид", "техлид", "teamlead", "techlead",
        "ведущий", "главный", "архитектор", "директор", "руководитель", "начальник",
        "head of", "director", "expert", "эксперт", "principal", "руководящ",
        "управляющий", "chief", "cto", "cio", "cpo", "vp ", "vp-", "vp_", "vice president",
        "от 3 лет", "от 3-х лет", "от 5 лет", "от 4 лет", "от 2 лет", "от 2-х лет",
        "middle+", "middle / senior", "middle/senior"
    ]
    for stop in hard_stops_title:
        if stop in title_lower:
            return True

    # 2. Теги в заголовке/описании
    grade_tags = [
        "#middle", "#middle+", "#middle_plus", "#мидл",
        "#senior", "#senior+", "#сеньор", "#синьор",
        "#lead", "#teamlead", "#techlead", "#лид"
    ]
    for tag in grade_tags:
        if tag in desc_lower or tag in title_lower:
            return True

    # 3. Отсев псевдо-джуниоров: HighLoad
    hl_patterns = [
        r"опыт\s+(?:разработки|проектирования|работы\s+с|создания)\s+высоконагруженн",
        r"опыт\s+highload",
        r"highload\s+от\s+\d+",
        r"проектирование\s+высоконагруженн"
    ]
    for hlp in hl_patterns:
        if re.search(hlp, desc_lower):
            return True

    # 3.1. Явные маркеры грейда Middle / Senior в описании
    grade_markers = [
        "грейд: middle", "грейд: senior", "грейд: lead",
        "квалификация: middle", "квалификация: senior", "квалификация: lead",
        "уровень: middle", "уровень: senior", "уровень: lead",
        "уровень позиции: middle", "уровень позиции: senior",
        "middle developer", "middle разработчик", "middle java",
        "senior developer", "senior разработчик", "senior java",
        "lead developer", "lead разработчик",
        "пяти лет", "пять лет", "четырех лет", "четырёх лет", "четыре года",
        "трех лет", "трёх лет", "три года",
        "от 5 лет", "от 5-ти лет", "от 5ти лет", "от 4 лет", "от 4-х лет",
        "от 3 лет", "от 3-х лет", "от 3-ух лет",
        "3-6 лет", "3–6 лет", "3 - 6 лет",
        "опыт работы: от 3", "опыт работы: от 4", "опыт работы: от 5", "опыт работы: от 6",
        "опыт работы от 3", "опыт работы от 4", "опыт работы от 5",
        "требуемый опыт: от 3", "требуемый опыт от 3",
        "менторство стаж", "менторство разработчик", "обучение стажер", "наставничество стажер",
        "управление командой", "руководство команд", "код-ревью команды", "ревью кода коллег"
    ]
    for marker in grade_markers:
        if marker in desc_lower:
            return True

    if re.search(r'(?:опыт|стаж|требован|коммерческ)\D{0,25}от\s+(?:[3-9]|\d{2})\s*(?:-ти|-ми|-х|-ух|-го)?\s*лет', desc_lower):
        return True
    if re.search(r'от\s+(?:трех|трёх|четырех|четырёх|пяти|шести)\s+лет', desc_lower):
        return True

    # 4. Чужие языки, платформы и чужой бэкенд (отсекаем ДО целевых ролей)
    # 4.1. 1С
    if re.search(r'\b(1с|1c)\b', title_lower) or any(k in title_lower for k in ["1с-", "1c-", "1с:", "1c:"]):
        return True

    # 4.2. C# / .NET
    if any(k in title_lower for k in ["c#", ".net", "dotnet", "c sharp", "си шарп"]) or "c#-" in title_lower or "c#-разработчик" in title_lower:
        if "java" not in title_lower and "javascript" not in title_lower:
            return True

    # 4.3. Go / Golang
    if any(k in title_lower for k in ["golang", "go-разработчик", "go разработчик", "go developer", "разработчик go", "стажёр go", "стажер go", "junior go"]):
        if "java" not in title_lower:
            return True

    # 4.4. PHP / Ruby / Rust
    if any(k in title_lower for k in ["php", "laravel", "symfony", "bitrix", "битрикс", "ruby", "rails", "rust"]):
        if "java" not in title_lower and "javascript" not in title_lower and "python" not in title_lower:
            return True

    # 4.5. C / C++ / Embedded
    if re.search(r'\b(c\+\+|с\+\+|embedded|stm32|микроконтроллер)\b', title_lower) or "c++-" in title_lower or "с++-" in title_lower:
        if "java" not in title_lower and "python" not in title_lower:
            return True

    # 4.6. GameDev / 3D
    if any(g in title_lower for g in ["unity", "unreal", "godot", "gamedev", "геймдев", "3d-", "3d ", "3д "]):
        return True

    # 4.7. Мобильная разработка (iOS / Swift / Flutter / Android) — если не web и не Java
    if any(m in title_lower for m in ["ios", "swift", "flutter", "dart"]):
        if "java" not in title_lower and "javascript" not in title_lower:
            return True
    if any(m in title_lower for m in ["разработчик мобильных", "mobile developer", "мобильный разработчик", "мобильной разработке"]):
        if not any(k in full_text for k in ["react native", "java", "javascript", "react"]):
            return True

    # 4.8. DevOps / SRE / DBA
    if any(d in title_lower for d in ["devops", "sre", "dba", "системный администратор", "сисадмин", "администратор баз"]):
        return True

    # 5. Определение целевых ролей кандидата (ТОЛЬКО РАЗРАБОТКА ПО)
    is_vpn_dev = any(k in full_text for k in ["remnawave", "xray", "vless", "sing-box"]) or (
        "vpn" in full_text and any(d in full_text for d in ["разработчик", "developer", "backend", "программист"])
    )
    is_ai_dev = any(k in full_text for k in [
        "spring ai", "spring-ai", "langchain4j", "langchain-4j", "langchain",
        "llm", "ai agent", "ai-агент", "ai агент", "genai", "rag", "инженер ии",
        "разработчик ии", "ai engineer", "ai developer", "llm engineer", "ai разработчик"
    ]) or any(k in title_lower for k in [
        "ai engineer", "ai developer", "llm developer", "инженер ии", "разработчик ии", "ai разработчик"
    ])
    is_java = any(k in title_lower for k in ["java", "kotlin", "spring"]) or (
        any(k in full_text for k in ["java", "kotlin"]) and any(d in full_text for d in ["разработчик", "developer", "backend", "стажер", "junior"])
    )
    is_junior = any(j in title_lower for j in [
        "junior", "джуниор", "джун", "младший", "начинающий", "стажер", "стажёр", 
        "intern", "trainee", "практикант"
    ]) or any(j in full_text[:400] for j in [
        "junior", "джуниор", "джун", "стажер", "стажёр", "intern", "trainee", "начинающий"
    ])

    is_python_backend = (
        any(k in title_lower for k in ["python", "питон", "fastapi", "django", "flask"]) or
        (any(k in full_text for k in ["python", "fastapi", "django", "flask"]) and any(d in full_text for d in ["backend", "бэкенд", "разработчик", "developer", "api"]))
    ) and (is_junior or any(j in title_lower for j in ["junior", "джуниор", "стажер", "стажёр", "trainee", "intern", "младший", "начинающий", "разработчик", "developer"]))

    is_fullstack_js = (
        any(k in title_lower for k in [
            "fullstack", "full-stack", "фулстек", "фуллстек", "full stack",
            "frontend", "front-end", "фронтенд", "веб-разработчик", "веб разработчик", "web developer",
            "javascript", "typescript", "react", "node", "nodejs"
        ]) or (
            any(k in full_text for k in ["fullstack", "фулстек", "react", "node.js", "nodejs"]) and
            any(d in full_text for d in ["разработчик", "developer", "программист", "стажер", "стажёр", "junior"])
        )
    ) and (is_junior or any(j in title_lower for j in ["junior", "джуниор", "стажер", "стажёр", "trainee", "intern", "младший", "начинающий"]))

    is_backend_dev = any(k in title_lower for k in ["backend", "бэкенд"]) and (
        is_junior or any(s in title_lower for s in ["стажер", "стажёр", "junior", "младший", "intern", "trainee", "начинающий"])
    )

    is_generic_junior = (
        any(s in title_lower for s in ["стажер", "стажёр", "intern", "trainee", "практикант", "младший", "начинающий", "junior", "джуниор"]) and
        any(d in title_lower for d in ["разработчик", "developer", "программист", "специалист"])
    ) and any(j in full_text for j in [
        "java", "jvm", "spring", "backend", "бэкенд", "fullstack", "фулстек", "frontend", "фронтенд",
        "react", "node", "javascript", "typescript", "python", "fastapi", "rest api", "postgresql"
    ])

    # Если вакансия НЕ по целевой разработке — отсекаем
    if not is_java and not is_vpn_dev and not is_ai_dev and not is_backend_dev and not is_fullstack_js and not is_generic_junior and not is_python_backend:
        return True

    # 5.1. Отсев опыта работы
    # Для позиций Junior / Intern шаблоны "1-3 года" и "от 1 года" в тексте допустимы (стандартные шаблоны платформ).
    # Отсекаем только если позиция НЕ Junior/Intern и требует от 2+ лет опыта.
    if not is_junior:
        strict_non_junior_markers = [
            "двух лет", "два года", "полутора лет",
            "от 2 лет", "от 2-х лет", "от 2-ух лет",
            "опыт работы: от 2", "опыт работы от 2", "требуемый опыт: от 2", "требуемый опыт от 2",
            "опыт коммерческой разработки на java — от", "опыт коммерческой разработки на java - от",
            "опыт коммерческой разработки от 2", "коммерческий опыт разработки от 2"
        ]
        for marker in strict_non_junior_markers:
            if marker in desc_lower:
                return True
        if re.search(r'(?:опыт(?:а)?(?:\s+коммерческой)?(?:\s+разработки)?(?:\s+работы)?\s*(?:на\s+java)?)?\s*от\s+2\s*(?:-ти|-ми|-х|-ух|-го)?\s*лет', desc_lower):
            return True
        if re.search(r'от\s+(?:двух)\s+лет', desc_lower):
            return True

    # 6. Строгий отсев нерелевантного железа, физического монтажа и эникея
    hard_sysadmin_rejects = [
        "выездной инженер", "картридж", "ремонт принтер", "сборка пк", "монтажник скд",
        "монтаж кабел", "слаботоч", "охранно-пожарн", "видеонаблюдение", "эникей", "сисадмин в офис",
        "системный администратор windows", "заправка картриджей"
    ]
    for stop in hard_sysadmin_rejects:
        if stop in title_lower or stop in full_text[:300]:
            return True

    # 7. Отсев иероглифов, французских/немецких локальных вакансий и Украины
    if re.search(r'[一-鿿]', full_text):
        return True

    non_target_markers = [
        "développeur", "alternance", "stage -", "entwickler", "m/w/d", "werkstudent",
        "ukraine", "украин", "lviv", "львов", "kyiv", "киев", "kharkiv", "днепр", "dnipro"
    ]
    for ntm in non_target_markers:
        if ntm in full_text:
            return True

    # 8. Международные стоп-факторы и жесткие требования виз/гражданства других стран
    intl_stops = [
        "us citizen", "us citizenship", "green card", "authorized to work in the us",
        "work authorization in the us", "must reside in the us", "must be located in the us",
        "security clearance", "ts/sci", "w2 only", "c2c only", "eu citizenship",
        "eu work permit", "right to work in the uk", "right to work in the eu",
        "must be located in the uk", "must reside in germany", "german b2", "german c1",
        "fluent german", "no contractors in russia", "no candidates from russia",
        "not eligible for visa sponsorship", "sponsorship is not available"
    ]
    for stop in intl_stops:
        if stop in desc_lower:
            return True

    return False

def analyze_vacancy_with_groq(api_key, model, title, company, description, delay=2.5):
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    short_desc = description[:1800] if description else ""

    prompt = f"""
Ты — персональный карьерный ассистент и технический рекрутер для начинающего IT-специалиста Никиты (21 год).

Профиль кандидата:
- Образование: Неоконченное высшее (технический ВУЗ) + СПО (Информационные системы) + Школа 21 от Сбера.
- Главный фокус: Junior Java Backend Developer (Java 21, Spring Boot 3, PostgreSQL, Docker, Clean Architecture, REST API, многопоточность).
- Дополнительные целевые роли (РАЗРАБОТКА ПО):
  • AI & LLM Engineer / Разработчик ИИ (Spring AI, LangChain, OpenAI/Groq API, AI-агенты, RAG, pgvector, prompt engineering)
  • Junior Python Backend Developer (Python 3, FastAPI, Flask, REST API, PostgreSQL, Docker, ООП, скрипты автоматизации и интеграции)
  • Junior / Trainee Fullstack JS / Frontend Developer (JavaScript, TypeScript, React 18, Node.js, Express, Sequelize, REST API, MobX, Nginx, Linux VPS). Развернул и задеплоил живой веб-проект https://park.shadowkek.space
  • Разработчик VPN/прокси (Xray, Sing-box, Remnawave, сетевые протоколы)
  • Junior / Intern Backend Developer (бэкенд, REST API)
- Никита не имеет многолетнего коммерческого опыта в IT, но имеет сильные учебные и пет-проекты, понимание архитектуры и базы Computer Science.

Проанализируй вакансию:
- Название: {title}
- Компания: {company}
- Описание и требования: {short_desc}

Твоя задача — беспристрастно оценить вакансию:
1. Подходит ли вакансия по направлению РАЗРАБОТКИ ПО:
   - "java": Junior Java Backend Developer (Java 21, Spring Boot, REST API, SQL/PostgreSQL, Docker)
   - "python_dev": Junior Python Backend Developer (Python 3, FastAPI, Flask, REST API, SQL, Docker, скрипты)
   - "ai_dev": AI & LLM Engineer / Разработчик ИИ (Spring AI, LangChain, Python AI, LLM API, AI-агенты, RAG)
   - "fullstack_js": Junior / Trainee Fullstack JS или Frontend разработчик (JavaScript, TypeScript, React, Node.js, Web-разработка, SPA, верстка с логикой)
   - "vpn_dev": Разработчик VPN сервисов и протоколов (Xray, Remnawave, VLESS, Sing-box, сетевые сервисы)
   - "backend": Junior / Intern Backend Developer (бэкенд разработка, REST API)
2. Отсев не-разработки и запрещенных сфер:
   - ПРЕПОДАВАНИЕ, НАСТАВНИЧЕСТВО, РЕПЕТИТОРСТВО, МЕНТОРСТВО, КУРАТОРСТВО — СТРОГО suitable=false, match_score=1! Никиту интересует ТОЛЬКО инженерная разработка ПО, НЕ преподавание!
   - ТЕСТИРОВАНИЕ (ручной QA, AQA автотесты, тестировщик ПО) — СТРОГО suitable=false, match_score=1! Никакого тестирования и QA!
   - Саппорт, техподдержка пользователей L1, разметка данных / AI-тренеры, операторы ПК, холодные продажи, эникей/ремонт ПК — ставь suitable=false, match_score=1.
   - Чужие стеки без Java/JS/Python: Go, C#, .NET, PHP, Ruby, Rust, C/C++, 1C, мобильная разработка (iOS, Swift, Flutter, Android без Java), GameDev (Unity/Unreal) — СТРОГО ставь suitable=false, match_score=1..2!
   - Дайджесты и подборки вакансий: если в тексте перечислено несколько разных вакансий/ссылок — СТРОГО ставь suitable=false, match_score=1.
   - Для вакансий Junior/Trainee Python Backend на FastAPI/Flask — ставь suitable=true, role="python_dev", match_score=7..9!
   - Для вакансий Junior/Trainee AI / LLM Engineer (интеграция ИИ, нейросети, LLM) — ставь suitable=true, role="ai_dev", match_score=7..9!
   - Для вакансий Junior/Trainee Fullstack JS / Frontend на React/Node.js/TypeScript — ставь suitable=true, role="fullstack_js", match_score=7..9!
   - Для вакансий Junior/Trainee Java Developer (Java 21, Spring Boot) — ставь suitable=true, role="java", match_score=7..9!
3. Опыт работы:
   - Для позиций Junior / Стажёр / Trainee / Начинающий разработчик: стандартная HR-формулировка 'требуемый опыт: 1-3 года' или 'опыт от 1 года' в тексте вакансии НЕ является поводом для отказа, если по задачам и стеку это начальная позиция начинающего специалиста.
   - Отсекать (suitable=false, match_score=1..2) ТОЛЬКО если это реальный Middle+/Senior/Lead с жестким требованием от 2-3+ лет боевого продакшн-опыта, проектирования HighLoad систем или руководства командой.
4. Локация, удаленка и зарплата:
   - УДАЛЕННЫЙ ФОРМАТ (Remote / Удаленка / Дистанционно / Из любого города РФ): ПОДХОДИТ ВСЕГДА (suitable=true) независимо от того, указана зарплата или нет.
   - СТАЖИРОВКИ (Intern / Стажер / Практикант / Trainee): ПОДХОДЯТ ВСЕГДА (suitable=true), даже если зарплата не указана или не оплачивается (дает ценный опыт в резюме).
   - ЕСЛИ ЗАРПЛАТА НЕ УКАЗАНА (по договоренности, не указана в тексте): НЕ ОТСЕКАЙ! Одобряй (suitable=true), если стек и уровень подходят, а в recommendation посоветуй уточнить вилку на первом скрининге.
   - Офис/гибрид Москва/СПб: подходит при зарплате от 80k+, либо если это официальная стажировка известной компании (Сбер, Т-Банк, Яндекс, ВТБ и др.), либо возможна удаленка. Отсекать только если это жесткий офис в Москве/СПб с явно указанной низкой зарплатой менее 70 000 руб.
5. Вакансии LinkedIn / International:
   - Подходит (suitable=true), если это Remote для РФ/СНГ/Worldwide, либо международная/русскоязычная компания, готовая оформлять удаленщиков (ТК РФ, ИП, самозанятость, Deel, Solar Staff, крипта).
   - Отсекать (suitable=false, match_score=1) только при жестком требовании работы в офисе за рубежом, требовании гражданства США/ЕС или прямого запрета найма резидентов РФ/РБ.
6. Образование: у Никиты неоконченное высшее (технический ВУЗ), колледж (СПО) и Школа 21 от Сбера. Если требуется высшее образование — одобряй, education_warning=null.
7. Черный список компаний: Aston (Астон), Deepra (Дипра) — строго suitable=false, match_score=1.

Верни ответ СТРОГО в формате JSON:
{{
  "suitable": true/false,
  "role": "java" | "python_dev" | "ai_dev" | "fullstack_js" | "vpn_dev" | "backend",
  "match_score": целое число от 1 до 10,
  "education_warning": null,
  "verdict": "краткое резюме (1-2 предложения, почему подходит или почему нет)",
  "recommendation": "практический совет Никите, что подчеркнуть в отклике"
}}
"""

    models_to_try = [model]
    for fallback in ["qwen/qwen3.8-27b", "openai/gpt-oss-20b", "openai/gpt-oss-120b"]:
        if fallback not in models_to_try:
            models_to_try.append(fallback)

    for current_model in models_to_try:
        payload = {
            "model": current_model,
            "messages": [
                {"role": "system", "content": "Ты карьерный AI-консультант. Отвечай только валидным JSON."},
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.1,
            "response_format": {"type": "json_object"}
        }

        for attempt in range(2):
            try:
                time.sleep(delay)
                resp = requests.post(url, headers=headers, json=payload, timeout=15)

                if resp.status_code == 200:
                    content = resp.json()["choices"][0]["message"]["content"]
                    return json.loads(content)

                elif resp.status_code == 429:
                    err_msg = ""
                    try:
                        err_msg = resp.json().get("error", {}).get("message", "")
                    except Exception:
                        err_msg = resp.text

                    is_daily = any(k in err_msg.lower() for k in ["tokens per day", "tpd", "limit 200000", "exceeded your current quota"])
                    if is_daily:
                        logger.warning(f"Groq суточный лимит TPD на модели {current_model}. Немедленно переключаемся на следующую модель...")
                        break

                    raw_retry = resp.headers.get("Retry-After", "3")
                    try:
                        wait_sec = min(max(float(raw_retry), 2.5), 6.0)
                    except ValueError:
                        wait_sec = 3.0

                    if attempt == 0:
                        logger.warning(f"Groq TPM 429 на модели {current_model}. Ждём {wait_sec:.1f}с перед повторной попыткой...")
                        time.sleep(wait_sec)
                        continue
                    else:
                        logger.warning(f"Groq 429 на модели {current_model} после повтора. Переключаемся на резервную модель...")
                        break

                else:
                    logger.error(f"Groq API error ({resp.status_code}) на {current_model}: {resp.text}")
                    break

            except Exception as e:
                logger.error(f"Ошибка вызова Groq API ({current_model}, попытка {attempt+1}): {e}")
                time.sleep(1)

    return None


def generate_cover_letter(api_key, model, vacancy):
    """
    Генерирует взрослое, профессиональное сопроводительное письмо для отклика (Java Backend или VPN/Proxy).
    """
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    clean_title = vacancy.get('title', '').replace("Требуется «", "").replace("»", "").strip()
    clean_company = vacancy.get('company', '').replace("Компания «", "").replace("»", "").strip()
    raw_desc = vacancy.get('description', '')
    clean_desc = clean_html(raw_desc)[:1800].strip()

    cfg_data = load_config()
    resume_url = (vacancy.get("resume_url") or cfg_data.get("resume_quick_tunnel_url") or "https://south-tomato-therapist-minimum.trycloudflare.com/").strip()
    if not resume_url.endswith("/"):
        resume_url += "/"

    full_text = f"{clean_title} {clean_desc}".lower()
    is_vpn_dev = vacancy.get("role") == "vpn_dev" or any(k in full_text for k in ["remnawave", "xray", "vless", "sing-box"])
    is_java_ai = vacancy.get("role") in ["ai_dev", "java_ai"] or any(k in full_text for k in ["spring ai", "spring-ai", "langchain4j", "langchain", "ai agent", "ai-агент", "llm", "rag", "инженер ии", "разработчик ии", "ai engineer"])
    is_python_backend = vacancy.get("role") == "python_dev" or (any(k in full_text for k in ["python", "fastapi", "django", "flask"]) and any(d in full_text for d in ["backend", "бэкенд", "разработчик", "developer", "стажер", "junior"]) and not ("java" in clean_title.lower() and "javascript" not in clean_title.lower()))
    is_fullstack_js = vacancy.get("role") == "fullstack_js" or (any(k in full_text for k in ["fullstack", "фулстек", "full stack", "react", "node", "javascript", "frontend", "front-end", "фронтенд"]) and not ("java" in clean_title.lower() and "javascript" not in clean_title.lower()))

    if is_vpn_dev:
        sys_msg = (
            "Ты — квалифицированный инженер и разработчик VPN/Proxy инфраструктуры Никита Фролов. "
            "Ты составляешь профессиональные, грамотные и зрелые сопроводительные письма. "
            "Твой стиль: деловой, сдержанный, глубоко технический. Без панибратства, без школьных шаблонов."
        )
        user_prompt = f"""Напиши профессиональное, зрелое и убедительное сопроводительное письмо на позицию разработчика VPN/прокси инфраструктуры.

Кандидат: Никита Фролов
- Специализация: Разработка и эксплуатация VPN/Proxy инфраструктуры.
- Технологический стек: Xray-core, VLESS (Reality, Vision), Sing-box, панели управления Remnawave и 3X-UI, протоколы обхода блокировок.
- Практический опыт: Настройка и администрирование парка Linux VPS нод, проектирование маршрутизации трафика (routing rules), интеграция через REST API панелей управления, мониторинг доступности узлов.

Вакансия:
- Позиция: {clean_title}
- Компания: {clean_company or 'Работодатель'}
- Требования и описание:
{clean_desc}

СТРОГИЕ ПРАВИЛА СОСТАВЛЕНИЯ:
1. ВЗРОСЛЫЙ ИНЖЕНЕРНЫЙ ТОН:
   - Начни строго с уважительного делового приветствия: «Здравствуйте!» или «Добрый день! Меня заинтересовала вакансия {clean_title} в {clean_company}.».
   - КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО детское или панибратское начало («Привет!», «Привет! Меня зовут Никита...»).
   - Текст должен звучать как письмо компетентного инженера, понимающего специфику современных сетевых протоколов.
2. ПРИВЯЗКА К СТЕКУ ВАКАНСИИ:
   - Подчеркни именно те технологии из описания, с которыми есть реальный опыт (Xray, Sing-box, VLESS, Remnawave, автоматизация на Linux).
3. ПРАКТИЧЕСКИЙ ОПЫТ:
   - Сделай акцент на реальной практике: управление распределенными нодами, настройка роутинга трафика, работа с API панелей и Linux VPS.
4. ОБЪЕМ И СТРУКТУРА:
   - 2-3 компактных, логичных абзаца (примерно 80-120 слов). Без лишней «воды» и пустых клише.
5. ФИНАЛ:
   - Заверши профессиональным предложением обсудить технические задачи команды на собеседовании или созвоне.
6. СТРОГИЙ ЗАПРЕТ:
   - НИ В КОЕМ СЛУЧАЕ НЕ ПРЕДЛАГАЙ тестовые задания.
   - Выдавай ТОЛЬКО готовый текст письма, без кавычек, без пометок типа «Тема:» или комментариев.
"""
    elif is_java_ai:
        sys_msg = (
            "Ты — AI / LLM Integration & Backend разработчик Никита Фролов. "
            "Ты составляешь профессиональные, грамотные и лаконичные сопроводительные письма. "
            "Твой стиль: инженерный, современный, структурированный, строго со ссылками на проекты и резюме."
        )
        user_prompt = f"""Напиши профессиональное сопроводительное письмо на позицию разработчика ИИ / интегратора AI & LLM.

Кандидат: Никита Фролов
- Ссылки на проекты и резюме:
  • Резюме: {resume_url}
  • Проект по ИИ (ML/NLP): https://github.com/nikitos7626/mvideo-nlp
  • Живой веб-проект (деплой): https://park.shadowkek.space
- Стек и фокус: AI / LLM интеграции (LLM API, Function Calling, RAG, AI-агенты, LangChain, Spring AI), бэкенд (Python/Java, REST API, PostgreSQL, Docker). Чистая архитектура, самостоятельный деплой на Linux VPS под Nginx с SSL.
- Бэкграунд: Школа 21 от Сбера, разработка NLP/AI решений (хакатон М.Видео по извлечению атрибутов).
- База и готовность: Базу по бэкенду, REST API, SQL и Git понимаю. Готов выполнить тестовое задание.

Вакансия:
- Позиция: {clean_title}
- Компания: {clean_company or 'Работодатель'}
- Описание:
{clean_desc}

СТРОГАЯ СТРУКТУРА ПИСЬМА:
1. Приветствие: «Здравствуйте! Откликаюсь на вакансию {clean_title}{' в ' + clean_company if clean_company else ''}.»
2. Обязательный блок ссылок буллетами:
   Прикрепляю ссылки на резюме и практические проекты:
   • Резюме: {resume_url}
   • Проект по ИИ (ML/NLP): https://github.com/nikitos7626/mvideo-nlp
   • Сайт (деплой): https://park.shadowkek.space
3. Стек: AI / LLM интеграции (LLM API, Function Calling, RAG, AI-агенты), бэкенд (Python/Java, REST API, PostgreSQL, Docker). Чистая архитектура, самостоятельный деплой на Linux VPS под Nginx с SSL.
4. Финал: Базу по бэкенду, ООП, SQL и Git понимаю. Готов выполнить тестовое задание.
"""
    elif is_python_backend:
        sys_msg = (
            "Ты — Junior Python Backend разработчик Никита Фролов. "
            "Ты составляешь профессиональные, грамотные и лаконичные сопроводительные письма. "
            "Твой стиль: деловой, инженерный, структурированный, со ссылками на проекты и резюме."
        )
        user_prompt = f"""Напиши профессиональное сопроводительное письмо на позицию Python Backend разработчика.

Кандидат: Никита Фролов
- Ссылки на проекты и резюме:
  • Резюме: {resume_url}
  • Проект на Python (NLP/ML/AI): https://github.com/nikitos7626/mvideo-nlp
  • Telegram-бот на Python: https://github.com/nikitos7626/ExCalendar
  • Живой веб-проект (деплой): https://park.shadowkek.space
- Стек: Python 3, FastAPI, Flask, PostgreSQL, Docker, REST API, Clean Architecture, скрипты автоматизации и интеграции.
- Практический опыт:
  * Проектирую и разрабатываю бэкенд-сервисы с REST API и реляционными БД.
  * Все сервисы самостоятельно развернул и настроил на Linux VPS под Nginx с SSL.
  * Практический опыт разработки на Python (пет-проекты, боты, NLP хакатон М.Видео).
- База и готовность: Базу по Python, ООП, REST API, SQL и Git понимаю. Готов выполнить тестовое задание.

Вакансия:
- Позиция: {clean_title}
- Компания: {clean_company or 'Работодатель'}
- Описание:
{clean_desc}

СТРОГАЯ СТРУКТУРА ПИСЬМА:
1. Приветствие: «Здравствуйте! Откликаюсь на вакансию {clean_title}{' в компании ' + clean_company if clean_company else ''}.»
2. Обязательный блок ссылок буллетами:
   Прикрепляю ссылки на резюме и практические проекты:
   • Резюме: {resume_url}
   • Проект на Python (NLP/ML/AI): https://github.com/nikitos7626/mvideo-nlp
   • Telegram-бот на Python: https://github.com/nikitos7626/ExCalendar
   • Сайт (деплой): https://park.shadowkek.space
3. Стек: Python 3, FastAPI, PostgreSQL, Docker, REST API, чистая архитектура, самостоятельный деплой на Linux VPS под Nginx с SSL.
4. Финал: Базу по Python, ООП, SQL и Git понимаю. Готов выполнить тестовое задание.
"""
    elif is_fullstack_js:
        sys_msg = (
            "Ты — Fullstack JS / Frontend разработчик Никита Фролов. "
            "Ты составляешь профессиональные, четкие и лаконичные сопроводительные письма. "
            "Стиль: живой, структурированный, уверенный, строго со структурированными буллетами на ссылки и проекты."
        )
        user_prompt = f"""Напиши профессиональное сопроводительное письмо на позицию стажёра / Junior Fullstack JS разработчика.

Кандидат: Никита Фролов
- Ссылки на резюме и пет-проекты:
  • Резюме: {resume_url}
  • Сайт (деплой): https://park.shadowkek.space
  • Репозиторий фронта: https://github.com/nikitos7626/park-client
  • Репозиторий бэка: https://github.com/nikitos7626/Park-server
- Стек проекта: React 18 (MobX), Node.js (Express, Sequelize). Реализована JWT-авторизация, роли, интеграция с API. Всё сам развернул и настроил на Linux VPS под Nginx с SSL.
- База и готовность: Базу по JS (ES6+, промисы, async/await, DOM), Git и React понимаю. Готов выполнить тестовое задание.

Вакансия:
- Позиция: {clean_title}
- Компания: {clean_company or 'Работодатель'}
- Описание:
{clean_desc}

СТРОГАЯ СТРУКТУРА ПИСЬМА:
1. Приветствие: «Привет! Откликаюсь на вакансию {clean_title}.»
2. Обязательный блок ссылок буллетами:
   Прикрепляю ссылки на резюме и живой пет-проект, как указано в требованиях вакансии:
   • Резюме: {resume_url}
   • Сайт (деплой): https://park.shadowkek.space
   • Репозиторий фронта: https://github.com/nikitos7626/park-client
   • Репозиторий бэка: https://github.com/nikitos7626/Park-server
3. Стек проекта: React 18 (MobX), Node.js (Express, Sequelize). Реализована JWT-авторизация, роли, интеграция с API. Всё сам развернул и настроил на Linux VPS под Nginx с SSL.
4. Финал: Базу по JS (ES6+, промисы, async/await, DOM), Git и React понимаю. Готов выполнить тестовое задание.
"""
    else:
        sys_msg = (
            "Ты — квалифицированный Junior Java Backend разработчик Никита Фролов. "
            "Ты составляешь профессиональные, грамотные и лаконичные сопроводительные письма. "
            "Твой стиль: деловой, инженерный, уверенный, со структурированными буллетами на ссылки и проекты."
        )
        user_prompt = f"""Напиши профессиональное, зрелое и убедительное сопроводительное письмо на позицию Java-разработчика.

Кандидат: Никита Фролов
- Позиционирование: Java Backend разработчик.
- Ссылки на проекты и резюме:
  • Резюме: {resume_url}
  • Репозиторий бэкенда: https://github.com/nikitos7626/BackendSpringBlog
  • Живой веб-проект (деплой): https://park.shadowkek.space
- Стек: Java 21, Spring Boot 3 (Spring Data JPA, Spring Security), PostgreSQL, Docker, Clean Architecture, REST API.
- Практический опыт:
  * Имею крепкий практический опыт разработки реальных проектов (опыт некоммерческий, но построен на современных промышленных стандартах и чистой архитектуре).
  * Реализовал собственный проект: сервис управления подписками с интеграцией биллинга и REST API.
  * Все свои сервисы сам развернул и настроил на Linux VPS под Nginx с SSL.
- База и готовность: Базу по Java Core, ООП, SQL и Git понимаю. Готов выполнить тестовое задание.

Вакансия:
- Позиция: {clean_title}
- Компания: {clean_company or 'Работодатель'}
- Требования и описание:
{clean_desc}

СТРОГАЯ СТРУКТУРА ПИСЬМА:
1. Приветствие: «Здравствуйте! Откликаюсь на вакансию {clean_title}{' в компании ' + clean_company if clean_company else ''}.»
2. Обязательный блок ссылок буллетами:
   Прикрепляю ссылки на резюме и практические проекты:
   • Резюме: {resume_url}
   • Репозиторий бэкенда: https://github.com/nikitos7626/BackendSpringBlog
   • Сайт (деплой): https://park.shadowkek.space
3. Стек: Java 21, Spring Boot 3 (Spring Data JPA, Security), PostgreSQL, Docker. Реализовал архитектуру Clean Architecture и REST API. Все сервисы развернул и настроил на Linux VPS под Nginx с SSL.
4. Финал: Базу по Java Core, ООП, SQL и Git понимаю. Готов выполнить тестовое задание.
"""

    models_to_try = [model]
    for fallback in ["qwen/qwen3.8-27b", "openai/gpt-oss-20b", "openai/gpt-oss-120b"]:
        if fallback not in models_to_try:
            models_to_try.append(fallback)

    for current_model in models_to_try:
        payload = {
            "model": current_model,
            "messages": [
                {"role": "system", "content": sys_msg},
                {"role": "user", "content": user_prompt}
            ],
            "temperature": 0.35
        }

        for attempt in range(2):
            try:
                resp = requests.post(url, headers=headers, json=payload, timeout=22)
                if resp.status_code == 200:
                    letter = resp.json()["choices"][0]["message"]["content"].strip()
                    if (letter.startswith('"') and letter.endswith('"')) or (letter.startswith('«') and letter.endswith('»')):
                        letter = letter[1:-1].strip()
                    if resume_url and resume_url not in letter:
                        letter = letter + f"\n\n• Резюме: {resume_url}"
                    if is_java_ai:
                        if "mvideo-nlp" not in letter:
                            letter = letter + "\n• Проект по ИИ (ML/NLP): https://github.com/nikitos7626/mvideo-nlp"
                    elif is_python_backend:
                        if "mvideo-nlp" not in letter and "ExCalendar" not in letter:
                            letter = letter + "\n• Проект на Python: https://github.com/nikitos7626/mvideo-nlp"
                    elif is_fullstack_js:
                        if "park-client" not in letter:
                            letter = letter + "\n• Репозиторий фронта: https://github.com/nikitos7626/park-client"
                        if "Park-server" not in letter:
                            letter = letter + "\n• Репозиторий бэка: https://github.com/nikitos7626/Park-server"
                    elif not is_vpn_dev:
                        if "BackendSpringBlog" not in letter:
                            letter = letter + "\n• Репозиторий бэкенда: https://github.com/nikitos7626/BackendSpringBlog"
                    if not is_vpn_dev and "park.shadowkek.space" not in letter:
                        letter = letter + "\n• Живой проект (деплой): https://park.shadowkek.space"
                    return letter
                elif resp.status_code == 429:
                    logger.warning(f"Groq Rate Limit (429) при генерации Cover Letter на {current_model}. Пробуем резервную...")
                    break
                else:
                    logger.error(f"Groq Cover Letter error ({resp.status_code}) на {current_model}: {resp.text}")
                    break
            except Exception as e:
                logger.error(f"Ошибка вызова Groq Cover Letter ({current_model}): {e}")
                time.sleep(1)

    # Профессиональный фоллбек на случай недоступности нейросети
    if is_java_ai:
        comp_str = f" в {clean_company}" if clean_company else ""
        return (
            f"Здравствуйте! Откликаюсь на вакансию {clean_title}{comp_str}.\n\n"
            f"Прикрепляю ссылки на резюме и практические проекты:\n"
            f"• Резюме: {resume_url}\n"
            f"• Проект по ИИ (ML/NLP): https://github.com/nikitos7626/mvideo-nlp\n"
            f"• Сайт (деплой): https://park.shadowkek.space\n\n"
            f"Стек: AI / LLM интеграции (LLM API, Function Calling, RAG, AI-агенты, LangChain, Spring AI), бэкенд (Python/Java, REST API, PostgreSQL, Docker). Чистая архитектура, самостоятельный деплой на Linux VPS под Nginx с SSL.\n\n"
            f"Базу по бэкенду, ООП, SQL и Git понимаю. Готов выполнить тестовое задание."
        )

    if is_python_backend:
        comp_str = f" в компании {clean_company}" if clean_company else ""
        return (
            f"Здравствуйте! Откликаюсь на вакансию {clean_title}{comp_str}.\n\n"
            f"Прикрепляю ссылки на резюме и практические проекты:\n"
            f"• Резюме: {resume_url}\n"
            f"• Проект на Python (NLP/ML/AI): https://github.com/nikitos7626/mvideo-nlp\n"
            f"• Telegram-бот на Python: https://github.com/nikitos7626/ExCalendar\n"
            f"• Сайт (деплой): https://park.shadowkek.space\n\n"
            f"Стек: Python 3, FastAPI, PostgreSQL, Docker, REST API, чистая архитектура, самостоятельный деплой на Linux VPS под Nginx с SSL.\n\n"
            f"Базу по Python, ООП, SQL и Git понимаю. Готов выполнить тестовое задание."
        )

    if is_fullstack_js:
        return (
            f"Привет! Откликаюсь на вакансию {clean_title}.\n\n"
            f"Прикрепляю ссылки на резюме и живой пет-проект, как указано в требованиях вакансии:\n"
            f"• Резюме: {resume_url}\n"
            f"• Сайт (деплой): https://park.shadowkek.space\n"
            f"• Репозиторий фронта: https://github.com/nikitos7626/park-client\n"
            f"• Репозиторий бэка: https://github.com/nikitos7626/Park-server\n\n"
            f"Стек проекта: React 18 (MobX), Node.js (Express, Sequelize). Реализована JWT-авторизация, роли, интеграция с API. Всё сам развернул и настроил на Linux VPS под Nginx с SSL.\n\n"
            f"Базу по JS (ES6+, промисы, async/await, DOM), Git и React понимаю. Готов выполнить тестовое задание."
        )

    if is_vpn_dev:
        comp_str = f" в компании {clean_company}" if clean_company else ""
        return (
            f"Здравствуйте! Откликаюсь на позицию {clean_title}{comp_str}.\n\n"
            f"Прикрепляю ссылки на резюме и проекты:\n"
            f"• Резюме: {resume_url}\n"
            f"• Развёрнутые сервисы (деплой): https://park.shadowkek.space\n\n"
            f"Стек: Xray-core, VLESS (Reality, Vision), Sing-box, панели Remnawave и 3X-UI, маршрутизация трафика. Развёртывание и администрирование узлов на Linux VPS под Nginx с SSL.\n\n"
            f"Сетевой стек (TCP/IP, routing, DNS, TLS), Linux и Git понимаю. Готов выполнить тестовое задание."
        )

    comp_str = f" в компании {clean_company}" if clean_company else ""
    return (
        f"Здравствуйте! Откликаюсь на вакансию {clean_title}{comp_str}.\n\n"
        f"Прикрепляю ссылки на резюме и практические проекты:\n"
        f"• Резюме: {resume_url}\n"
        f"• Репозиторий бэкенда: https://github.com/nikitos7626/BackendSpringBlog\n"
        f"• Сайт (деплой): https://park.shadowkek.space\n\n"
        f"Стек проекта: Java 21, Spring Boot 3 (Spring Data JPA, Security), PostgreSQL, Docker. Реализовал архитектуру Clean Architecture и REST API. Все сервисы развернул и настроил сам на Linux VPS под Nginx с SSL.\n\n"
        f"Базу по Java Core, ООП, SQL и Git понимаю. Готов выполнить тестовое задание."
    )



def format_ai_vacancy_message(v, analysis):
    clean_title = v["title"].replace("Требуется «", "").replace("»", "").strip()
    score = analysis.get("match_score", 0)
    role = analysis.get("role", "java")
    source_name = v.get("source_name", "IT-Площадка")
    source_emoji = v.get("source_emoji", "💼")

    esc_title = html.escape(clean_title)
    esc_company = html.escape(v.get("company", "Не указана"))
    esc_source_name = html.escape(source_name)

    if score >= 8:
        score_badge = f"🔥 <b>Совпадение: {score}/10 (Отличный шанс!)</b>"
    else:
        score_badge = f"⚡ <b>Совпадение: {score}/10 (Хороший матч)</b>"

    if role == "vpn_dev":
        role_badge = "🛡️ [VPN / Xray Core Dev]"
    elif role in ["java_ai", "ai_dev"]:
        role_badge = "🤖 [AI & LLM Integration Developer]"
    elif role == "backend":
        role_badge = "⚡ [Junior Backend Developer]"
    elif role == "fullstack_js":
        role_badge = "🌐 [Fullstack JS / Frontend]"
    elif role == "python_dev":
        role_badge = "🐍 [Python / Backend & Интеграции]"
    else:
        role_badge = "☕ [Java Backend]"
    msg = f"{role_badge} • {source_emoji} <b>{esc_source_name}</b>\n"
    msg += f"💼 <b>{esc_title}</b>\n"
    msg += f"🏢 <b>Компания:</b> {esc_company}\n"
    
    if v.get("salary"):
        msg += f"💰 <b>Зарплата:</b> {html.escape(v['salary'])}\n"

    # Выделенный блок стека технологий
    desc = v.get("description", "").strip()
    stack = extract_tech_stack(desc, clean_title)
    if stack:
        stack_str = " • ".join(f"<code>{html.escape(s)}</code>" for s in stack[:8])
        msg += f"\n🛠 <b>Стек технологий:</b>\n{stack_str}\n"

    # Читаемый блок условий и формата (без RSS мусора)
    if "Требуемые навыки:" in desc:
        info_part = desc.split("Требуемые навыки:")[0]
        info_part = re.sub(r'^Компания\s+[«"].*?[»"]\s+ищет\s+хорошего\s+специалиста\s+на\s+вакансию\s+[«"].*?[»"]\.?\s*', '', info_part)
        conditions = [c.strip() for c in info_part.split('.') if c.strip()]
        if conditions:
            msg += f"\n📍 <b>Формат:</b> {html.escape(' • '.join(conditions))}\n"

    msg += f"\n🤖 <b>AI-Анализ вакансии:</b>\n"
    msg += f"{score_badge}\n"
    verdict = analysis.get('verdict', '')
    if verdict:
        msg += f"💡 <b>Вердикт:</b> {html.escape(verdict)}\n"

    edu_warn = analysis.get("education_warning")
    if edu_warn:
        msg += f"⚠️ <b>Образование:</b> {html.escape(edu_warn)}\n"

    recom = analysis.get('recommendation')
    if recom:
        msg += f"🎯 <b>Совет к отклику:</b> {html.escape(recom)}\n\n"
    else:
        msg += "\n"

    return msg


# --- ИНТЕГРАЦИЯ С HEADHUNTER API (Авто-отклики и отслеживание приглашений) ---

def get_hh_headers(access_token):
    return {
        "Authorization": f"Bearer {access_token.strip()}",
        "User-Agent": "CareerAssistantBot/2.0 (contact: nikitos7626@mail.ru)"
    }


def check_hh_token(access_token):
    """Проверяет валидность токена HeadHunter API и возвращает профиль пользователя."""
    if not access_token:
        return None
    try:
        url = "https://api.hh.ru/me"
        resp = requests.get(url, headers=get_hh_headers(access_token), timeout=10)
        if resp.status_code == 200:
            return resp.json()
        logger.warning(f"Проверка HH токена ({resp.status_code}): {resp.text}")
    except Exception as e:
        logger.error(f"Ошибка проверки HH токена: {e}")
    return None


def fetch_hh_resumes(access_token):
    """Получает список резюме кандидата из HeadHunter."""
    if not access_token:
        return []
    try:
        url = "https://api.hh.ru/resumes/mine"
        resp = requests.get(url, headers=get_hh_headers(access_token), timeout=10)
        if resp.status_code == 200:
            return resp.json().get("items", [])
    except Exception as e:
        logger.error(f"Ошибка получения резюме HH: {e}")
    return []


def auto_select_hh_resume(access_token):
    """
    Автоматически находит целевое Java / Backend резюме пользователя,
    строго отдавая приоритет активным опубликованным резюме.
    """
    resumes = fetch_hh_resumes(access_token)
    if not resumes:
        return None
    # 1. Опубликованное резюме Java
    for r in resumes:
        title = r.get("title", "").lower()
        status_id = r.get("status", {}).get("id", "")
        if "java" in title and status_id == "published":
            return r
    # 2. Опубликованное резюме Backend / Разработчик
    for r in resumes:
        title = r.get("title", "").lower()
        status_id = r.get("status", {}).get("id", "")
        if any(k in title for k in ["backend", "бэкенд", "разработчик", "developer"]) and status_id == "published":
            return r
    # 3. Любое опубликованное резюме
    for r in resumes:
        if r.get("status", {}).get("id") == "published":
            return r
    # 4. Любое Java резюме
    for r in resumes:
        if "java" in r.get("title", "").lower():
            return r
    return resumes[0]


def apply_hh_vacancy_api(raw_id, resume_id, cover_letter, access_token):
    """
    Отправляет отклик на вакансию через официальный API HeadHunter (POST /negotiations).
    Корректно обрабатывает multipart payload, редиректы (direct vacancies), лимиты и тесты.
    """
    if not raw_id:
        return {"ok": False, "status": "error", "detail": "Отсутствует ID вакансии"}

    clean_id = str(raw_id).replace("hh_", "").strip()
    if not clean_id.isdigit():
        return {"ok": False, "status": "error", "detail": f"Некорректный ID вакансии: {raw_id}"}

    # HeadHunter ограничивает длину сопроводительного письма (макс. 10 000 символов)
    clean_letter = (cover_letter or "").strip()
    if len(clean_letter) > 9500:
        clean_letter = clean_letter[:9450] + "\n..."

    url = "https://api.hh.ru/negotiations"
    headers = get_hh_headers(access_token)
    headers["Content-Type"] = "application/x-www-form-urlencoded"
    data = {
        "vacancy_id": clean_id,
        "resume_id": str(resume_id),
        "message": clean_letter
    }
    try:
        resp = requests.post(url, headers=headers, data=data, timeout=15, allow_redirects=False)
        if resp.status_code == 201:
            loc = resp.headers.get("Location", "")
            return {"ok": True, "status": "sent", "detail": "Отклик успешно зарегистрирован", "negotiation_url": loc}

        # Вакансии с прямым откликом на внешнем сайте работодателя (тип direct -> 303 See Other)
        if resp.status_code == 303:
            loc = resp.headers.get("Location", "")
            return {"ok": False, "status": "direct_required", "detail": "Требуется прямой отклик на сайте работодателя", "redirect_url": loc}

        err_data = {}
        try:
            err_data = resp.json()
        except Exception:
            pass

        err_str = str(err_data).lower()
        logger.warning(f"HH negotiation failed for vacancy {clean_id} ({resp.status_code}): {resp.text}")

        if "already_applied" in err_str:
            return {"ok": True, "status": "already_applied", "detail": "Ранее уже был подан отклик"}
        if "test_required" in err_str or "test" in err_str:
            return {"ok": False, "status": "test_required", "detail": "Требуется обязательный тест/анкета работодателя"}
        if "limit_exceeded" in err_str:
            return {"ok": False, "status": "limit_exceeded", "detail": "Достигнут дневной лимит откликов на hh.ru (200/сутки)"}
        if resp.status_code in [401, 403] and any(k in err_str for k in ["bad_authorization", "oauth", "token", "token_expired", "invalid_token"]):
            return {"ok": False, "status": "token_expired", "detail": "Токен HeadHunter API недействителен или истёк"}
        if "resume_not_found" in err_str:
            return {"ok": False, "status": "resume_error", "detail": "Резюме не найдено, скрыто или не опубликовано"}
        if "resume_visibility_conflict" in err_str or "application_denied" in err_str:
            return {"ok": False, "status": "denied", "detail": "Отклик заблокирован настройками видимости резюме"}

        desc = err_data.get("description") or (err_data.get("errors", [{}])[0].get("value") if err_data.get("errors") else None) or f"Код {resp.status_code}"
        return {"ok": False, "status": "error", "detail": str(desc)}
    except Exception as e:
        logger.error(f"Исключение при отклике на HH #{clean_id}: {e}")
        return {"ok": False, "status": "error", "detail": str(e)}


def check_hh_web_cookie(cookie_str):
    """
    Проверяет валидность веб-сессии HeadHunter через /applicant/profile/me.
    Возвращает dict с инфо о пользователе (имя, резюме) или None.
    """
    if not cookie_str:
        return None
    try:
        host_m = re.search(r'redirect_host=([^;]+)', cookie_str)
        host = host_m.group(1).strip() if host_m else "volgograd.hh.ru"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36",
            "Cookie": cookie_str,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        r = requests.get(f"https://{host}/applicant/profile/me", headers=headers, timeout=12, allow_redirects=False)
        if r.status_code == 200 and ("Никита" in r.text or "Фролов" in r.text or "applicant" in r.text):
            resumes = list(set(re.findall(r'/resume/([a-f0-9]{38})', r.text)))
            return {
                "ok": True,
                "name": "Никита Фролов",
                "host": host,
                "resumes": resumes
            }
        return None
    except Exception as e:
        logger.error(f"Ошибка проверки web cookie HH: {e}")
        return None


def apply_hh_vacancy_web(raw_id, resume_hash, cover_letter, cookie_str):
    """
    Отправляет отклик на вакансию через официальный веб-эндпоинт HeadHunter
    (/applicant/vacancy_response/popup) с использованием аутентифицированной сессии браузера.
    Работает в условиях прекращения публичного API соискателей HeadHunter с 15.12.2025.
    """
    if not raw_id:
        return {"ok": False, "status": "error", "detail": "Отсутствует ID вакансии"}

    clean_id = str(raw_id).replace("hh_", "").strip()
    digits = re.findall(r"\d+", clean_id)
    if not digits:
        return {"ok": False, "status": "error", "detail": f"Некорректный ID вакансии: {raw_id}"}
    clean_id = digits[0]

    if not cookie_str:
        return {"ok": False, "status": "no_cookie", "detail": "Не настроены cookies веб-сессии HeadHunter"}

    clean_letter = (cover_letter or "").strip()
    if len(clean_letter) > 9500:
        clean_letter = clean_letter[:9450] + "\n..."

    xsrf_m = re.search(r'_xsrf=([^;]+)', cookie_str)
    xsrf = xsrf_m.group(1).strip() if xsrf_m else ""

    host_m = re.search(r'redirect_host=([^;]+)', cookie_str)
    host = host_m.group(1).strip() if host_m else "volgograd.hh.ru"

    url = f"https://{host}/applicant/vacancy_response/popup"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
        "Cookie": cookie_str,
        "X-Xsrftoken": xsrf,
        "X-Requested-With": "XMLHttpRequest",
        "Sec-Ch-Ua": '"Not A(Brand";v="8", "Chromium";v="132", "Google Chrome";v="132"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
        "Origin": f"https://{host}",
        "Referer": f"https://{host}/applicant/vacancy_response?vacancyId={clean_id}",
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"
    }

    payload = {
        "vacancy_id": clean_id,
        "resume_hash": str(resume_hash or "b81ca639ff1137b70d0039ed1f57755649766f"),
        "ignore_postponed": "true",
        "incomplete": "false",
        "lux": "true",
        "withoutTest": "no",
        "letter": clean_letter
    }

    try:
        resp = requests.post(url, headers=headers, data=payload, timeout=15, allow_redirects=False)

        if resp.status_code == 200:
            data = resp.json()
            if data.get("topic_id") == "SPAM":
                return {"ok": False, "status": "spam_blocked", "detail": "Отклик заблокирован анти-спам фильтром HH"}
            chat_id = data.get("chat_id")
            return {
                "ok": True,
                "status": "applied",
                "detail": "Отклик успешно зарегистрирован через веб-сессию HH",
                "chat_id": chat_id,
                "data": data
            }

        if resp.status_code in [302, 303]:
            loc = resp.headers.get("Location", "")
            return {"ok": False, "status": "direct_required", "detail": "Требуется прямой отклик на сайте работодателя", "redirect_url": loc}

        err_data = {}
        try:
            err_data = resp.json()
        except Exception:
            pass

        err_code = err_data.get("error", "")
        err_str = (str(err_data) + " " + resp.text).lower()

        if err_code == "alreadyApplied" or "alreadyapplied" in err_str or "already_applied" in err_str:
            return {"ok": True, "status": "already_applied", "detail": "Ранее уже был подан отклик"}

        if err_code in ["testRequired", "test_required"] or "test" in err_str:
            return {"ok": False, "status": "test_required", "detail": "Требуется обязательный тест/анкета работодателя"}

        if err_code in ["limitExceeded", "limit_exceeded"] or "limit" in err_str:
            return {"ok": False, "status": "limit_exceeded", "detail": "Достигнут дневной лимит откликов на hh.ru (200/сутки)"}

        if "hhcaptcha" in err_str:
            return {"ok": False, "status": "captcha_required", "detail": "Требуется решение капчи работодателя на HeadHunter"}

        if resp.status_code in [401, 403] or "login" in resp.headers.get("Location", ""):
            return {"ok": False, "status": "cookie_expired", "detail": "Сессия HeadHunter устарела или требует повторного входа"}

        if resp.status_code == 429:
            return {"ok": False, "status": "rate_limited", "detail": "Превышен лимит запросов HeadHunter (429)"}

        return {"ok": False, "status": "error", "detail": err_code or f"HTTP {resp.status_code}: {resp.text[:100]}"}

    except Exception as e:
        logger.error(f"Исключение при веб-отклике на HH #{clean_id}: {e}")
        return {"ok": False, "status": "error", "detail": str(e)}


def load_hh_negotiations_tracker():
    if not os.path.exists(HH_NEGOTIATIONS_FILE):
        return {}
    try:
        with open(HH_NEGOTIATIONS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_hh_negotiations_tracker(tracker):
    try:
        with open(HH_NEGOTIATIONS_FILE, "w", encoding="utf-8") as f:
            json.dump(tracker, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error(f"Ошибка сохранения hh_negotiations_tracker: {e}")


def delete_hh_negotiation_web(topic_id, cookie_str, host=None):
    """
    Удаляет/скрывает отклик (отказ) из профиля соискателя на HeadHunter
    через официальный веб-эндпоинт корзины (/applicant/negotiations/trash).
    """
    if not topic_id or not cookie_str:
        return {"ok": False, "detail": "Отсутствует topic_id или cookie"}
    try:
        if not host:
            host_m = re.search(r'redirect_host=([^;]+)', cookie_str)
            host = host_m.group(1).strip() if host_m else "volgograd.hh.ru"

        xsrf_m = re.search(r'_xsrf=([^;]+)', cookie_str)
        xsrf = xsrf_m.group(1).strip() if xsrf_m else ""

        url = f"https://{host}/applicant/negotiations/trash"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
            "Cookie": cookie_str,
            "X-Xsrftoken": xsrf,
            "X-Requested-With": "XMLHttpRequest",
            "Origin": f"https://{host}",
            "Referer": f"https://{host}/applicant/negotiations",
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"
        }
        payload = {
            "topic": str(topic_id),
            "substate": "HIDE"
        }
        resp = requests.post(url, headers=headers, data=payload, timeout=12)
        if resp.status_code in [200, 204]:
            return {"ok": True, "detail": "Отказ успешно удален из профиля HH"}
        return {"ok": False, "detail": f"HTTP {resp.status_code}: {resp.text[:100]}"}
    except Exception as e:
        logger.error(f"Исключение при удалении отказа HH {topic_id}: {e}")
        return {"ok": False, "detail": str(e)}


def remove_vacancy_from_profile_and_history(v_id):
    """
    Удаляет вакансию с отказом из истории и профиля в боте:
    - Из vacancy_statuses.json (чтобы не отображалась в откликах Mini App и статистики)
    - Из vacancies_cache.json
    """
    if not v_id:
        return
    clean_id = str(v_id).replace("hh_", "").strip()
    keys = [str(v_id), f"hh_{clean_id}", clean_id]

    try:
        statuses = load_statuses()
        changed = False
        for k in keys:
            if k in statuses:
                del statuses[k]
                changed = True
        if changed:
            save_statuses(statuses)
            logger.info(f"Вакансия {v_id} удалена из vacancy_statuses.json")
    except Exception as e:
        logger.error(f"Ошибка удаления вакансии {v_id} из статусов: {e}")

    for k in keys:
        try:
            delete_from_cache(k)
        except Exception as e:
            logger.error(f"Ошибка удаления вакансии {k} из кэша: {e}")


def poll_hh_negotiations_and_notify(bot_token, chat_id, access_token=None, cookie_str=None):
    """
    Периодически проверяет обновления в откликах на HH:
    - Обнаруживает отказы (DISCARD) и автоматически удаляет их из профиля HH и истории Career Hub
    - Приглашения на собеседование
    - Новые сообщения от работодателей
    - Изменения статусов
    Поддерживает как веб-сессию (cookie), так и официальный OAuth токен.
    Защищено от спама старыми историческими откликами при первом запуске.
    """
    if not access_token and not cookie_str:
        return

    tracker = load_hh_negotiations_tracker()
    is_initial_run = (len(tracker) == 0 and not os.path.exists(HH_NEGOTIATIONS_FILE))
    updated = False

    # 1. Веб-сессия соискателя (HTML /applicant/negotiations)
    if cookie_str:
        try:
            host_m = re.search(r'redirect_host=([^;]+)', cookie_str)
            host = host_m.group(1).strip() if host_m else "volgograd.hh.ru"
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
                "Cookie": cookie_str,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            }
            resp = requests.get(f"https://{host}/applicant/negotiations", headers=headers, timeout=15, allow_redirects=False)
            if resp.status_code == 200:
                # 1.1. Сначала пробуем парсить Redux state из template HH-Lux-InitialState
                m_state = re.search(r'<template[^>]*id="HH-Lux-InitialState"[^>]*>(.*?)</template>', resp.text, re.DOTALL)
                if m_state:
                    try:
                        state_data = json.loads(html.unescape(m_state.group(1)))
                        topics = state_data.get("applicantNegotiations", {}).get("topicList", [])
                        vac_list = state_data.get("vacanciesShort", {}).get("vacanciesList", [])
                        vac_dict = {str(v.get("vacancyId")): v for v in vac_list if v.get("vacancyId")}

                        for topic in topics:
                            t_id = str(topic.get("id", ""))
                            vac_id = str(topic.get("vacancyId", ""))
                            last_state = str(topic.get("lastState", "")).upper()

                            vac_info = vac_dict.get(vac_id, {})
                            raw_title = vac_info.get("name") or f"Вакансия #{vac_id}"
                            raw_comp = vac_info.get("employer", {}).get("name") or "Работодатель"
                            vac_title = html.escape(raw_title)
                            comp_name = html.escape(raw_comp)
                            vac_url = vac_info.get("links", {}).get("desktop") or f"https://{host}/vacancy/{vac_id}"

                            is_discard = (last_state == "DISCARD")
                            is_invitation = (last_state in ["INVITATION", "INVITE", "INTERVIEW", "HIRED"])
                            is_viewed = bool(topic.get("viewedByOpponent"))

                            prev = tracker.get(vac_id)
                            prev_state = prev.get("state_id") if prev else None

                            # Автоматическое удаление отказа из профиля и истории
                            if is_discard:
                                if prev_state != "discard_deleted":
                                    delete_hh_negotiation_web(t_id, cookie_str, host)
                                    tracker[vac_id] = {
                                        "state_id": "discard_deleted",
                                        "state_name": "Отказ (удален)",
                                        "topic_id": t_id,
                                        "updated_at": time.time()
                                    }
                                    updated = True

                                    # Удаляем из истории и профиля в Career Hub
                                    remove_vacancy_from_profile_and_history(f"hh_{vac_id}")

                                    # Оповещаем пользователя в Telegram
                                    if bot_token and chat_id:
                                        msg = (
                                            f"🗑 <b>Отказ автоматически удален из профиля и истории!</b>\n\n"
                                            f"💼 <b>{vac_title}</b>\n"
                                            f"🏢 <b>Компания:</b> {comp_name}\n\n"
                                            f"✅ <i>Отклик удален из списка на HeadHunter и скрыт из истории Career Hub.</i>"
                                        )
                                        send_message(bot_token, chat_id, msg)
                                    logger.info(f"Отказ по вакансии {vac_id} (topic {t_id}) удален из HH и истории.")
                                continue

                            state_id = "invitation" if is_invitation else ("viewed" if is_viewed else "sent")
                            state_name = "Приглашение на собеседование" if is_invitation else ("Просмотрено" if is_viewed else "Отправлено")

                            if prev is None:
                                tracker[vac_id] = {
                                    "state_id": state_id,
                                    "state_name": state_name,
                                    "updated_at": time.time()
                                }
                                updated = True
                                if not is_initial_run and is_invitation:
                                    msg = (
                                        f"🎉 <b>ПРИГЛАШЕНИЕ НА СОБЕСЕДОВАНИЕ! (HeadHunter)</b>\n\n"
                                        f"💼 <b>{vac_title}</b>\n"
                                        f"🏢 <b>Компания:</b> {comp_name}\n"
                                        f"📌 <b>Статус:</b> {html.escape(state_name)} 🔥\n\n"
                                        f"👉 <b><a href='{vac_url}'>Открыть вакансию на HeadHunter</a></b>"
                                    )
                                    send_message(bot_token, chat_id, msg, reply_markup={
                                        "inline_keyboard": [
                                            [{"text": "🚀 Открыть на HH", "url": vac_url}],
                                            [{"text": "💼 Mini App", "web_app": {"url": "https://jobs.shadowkek.space/"}}]
                                        ]
                                    })
                                continue

                            if state_id != prev_state:
                                tracker[vac_id] = {
                                    "state_id": state_id,
                                    "state_name": state_name,
                                    "updated_at": time.time()
                                }
                                updated = True
                                if state_id == "invitation":
                                    msg = (
                                        f"🎉 <b>ПРИГЛАШЕНИЕ НА СОБЕСЕДОВАНИЕ! (HeadHunter)</b>\n\n"
                                        f"💼 <b>{vac_title}</b>\n"
                                        f"🏢 <b>Компания:</b> {comp_name}\n"
                                        f"📌 <b>Статус:</b> {html.escape(state_name)} 🔥\n\n"
                                        f"👉 <b><a href='{vac_url}'>Перейти к согласованию интервью</a></b>"
                                    )
                                    send_message(bot_token, chat_id, msg, reply_markup={
                                        "inline_keyboard": [
                                            [{"text": "🚀 Открыть на HH", "url": vac_url}],
                                            [{"text": "💼 Mini App", "web_app": {"url": "https://jobs.shadowkek.space/"}}]
                                        ]
                                    })

                        if updated:
                            save_hh_negotiations_tracker(tracker)
                        return
                    except Exception as parse_e:
                        logger.warning(f"Ошибка обработки InitialState HH negotiations: {parse_e}")

                # 1.2. Fallback парсинга HTML, если InitialState не доступен
                items = re.findall(r'<div[^>]*data-qa="negotiations-item"[^>]*>(.*?)</div>\s*</div>\s*</div>', resp.text, re.DOTALL)
                for item_html in items:
                    vac_m = re.search(r'href="([^"]*/vacancy/(\d+)[^"]*)"[^>]*data-qa="negotiations-item-vacancy"[^>]*>(.*?)</a>', item_html)
                    if not vac_m:
                        vac_m = re.search(r'data-qa="negotiations-item-vacancy"[^>]*href="([^"]*/vacancy/(\d+)[^"]*)"[^>]*>(.*?)</a>', item_html)
                    if not vac_m:
                        continue
                    vac_url = vac_m.group(1)
                    neg_vac_id = vac_m.group(2)
                    vac_title = html.escape(re.sub(r'<[^>]+>', '', vac_m.group(3)).strip())

                    comp_m = re.search(r'data-qa="negotiations-item-company"[^>]*>(.*?)</a>', item_html)
                    comp_name = html.escape(re.sub(r'<[^>]+>', '', comp_m.group(1)).strip()) if comp_m else "Работодатель"

                    is_discard = ("discard" in item_html.lower() or "отказ" in item_html.lower())
                    is_invitation = ("invitation" in item_html.lower() or "приглашение" in item_html.lower() or "собеседован" in item_html.lower())
                    is_viewed = ("negotiations-item-viewed" in item_html or "просмотрено" in item_html.lower())

                    prev = tracker.get(neg_vac_id)
                    prev_state = prev.get("state_id") if prev else None

                    if is_discard:
                        if prev_state != "discard_deleted":
                            tracker[neg_vac_id] = {
                                "state_id": "discard_deleted",
                                "state_name": "Отказ (удален)",
                                "updated_at": time.time()
                            }
                            updated = True
                            remove_vacancy_from_profile_and_history(f"hh_{neg_vac_id}")
                            if bot_token and chat_id:
                                msg = (
                                    f"🗑 <b>Отказ автоматически удален из профиля и истории!</b>\n\n"
                                    f"💼 <b>{vac_title}</b>\n"
                                    f"🏢 <b>Компания:</b> {comp_name}\n\n"
                                    f"✅ <i>Отклик удален из профиля на HeadHunter и скрыт из истории Career Hub.</i>"
                                )
                                send_message(bot_token, chat_id, msg)
                        continue

                    state_id = "invitation" if is_invitation else ("viewed" if is_viewed else "sent")
                    state_name = "Приглашение на собеседование" if is_invitation else ("Просмотрено" if is_viewed else "Отправлено")

                    prev = tracker.get(neg_vac_id)
                    if prev is None:
                        tracker[neg_vac_id] = {
                            "state_id": state_id,
                            "state_name": state_name,
                            "updated_at": time.time()
                        }
                        updated = True
                        if not is_initial_run and is_invitation:
                            msg = (
                                f"🎉 <b>ПРИГЛАШЕНИЕ НА СОБЕСЕДОВАНИЕ! (HeadHunter)</b>\n\n"
                                f"💼 <b>{vac_title}</b>\n"
                                f"🏢 <b>Компания:</b> {comp_name}\n"
                                f"📌 <b>Статус:</b> {html.escape(state_name)} 🔥\n\n"
                                f"👉 <b><a href='https://{host}{vac_url}'>Открыть вакансию на HeadHunter</a></b>"
                            )
                            send_message(bot_token, chat_id, msg, reply_markup={
                                "inline_keyboard": [
                                    [{"text": "🚀 Открыть на HH", "url": f"https://{host}{vac_url}"}],
                                    [{"text": "💼 Mini App", "web_app": {"url": "https://jobs.shadowkek.space/"}}]
                                ]
                            })
                        continue

                    prev_state = prev.get("state_id")
                    if state_id != prev_state:
                        tracker[neg_vac_id] = {
                            "state_id": state_id,
                            "state_name": state_name,
                            "updated_at": time.time()
                        }
                        updated = True
                        if state_id == "invitation":
                            msg = (
                                f"🎉 <b>ПРИГЛАШЕНИЕ НА СОБЕСЕДОВАНИЕ! (HeadHunter)</b>\n\n"
                                f"💼 <b>{vac_title}</b>\n"
                                f"🏢 <b>Компания:</b> {comp_name}\n"
                                f"📌 <b>Статус:</b> {html.escape(state_name)} 🔥\n\n"
                                f"👉 <b><a href='https://{host}{vac_url}'>Перейти к согласованию интервью</a></b>"
                            )
                            send_message(bot_token, chat_id, msg, reply_markup={
                                "inline_keyboard": [
                                    [{"text": "🚀 Открыть на HH", "url": f"https://{host}{vac_url}"}],
                                    [{"text": "💼 Mini App", "web_app": {"url": "https://jobs.shadowkek.space/"}}]
                                ]
                            })
                if updated:
                    save_hh_negotiations_tracker(tracker)
                return
        except Exception as e:
            logger.error(f"Ошибка проверки web negotiations HH: {e}")

    # 2. Официальный API (fallback если настроен токен)
    if not access_token:
        return
    try:
        url = "https://api.hh.ru/negotiations"
        resp = requests.get(url, headers=get_hh_headers(access_token), timeout=12)
        if resp.status_code != 200:
            if resp.status_code in [401, 403]:
                logger.warning(f"HH negotiations auth error ({resp.status_code}): токен устарел.")
            return
        items = resp.json().get("items", [])
        tracker = load_hh_negotiations_tracker()
        is_initial_run = (len(tracker) == 0 and not os.path.exists(HH_NEGOTIATIONS_FILE))
        updated = False

        for neg in items:
            neg_id = str(neg.get("id", ""))
            if not neg_id:
                continue
            state = neg.get("state", {})
            state_id = state.get("id", "")
            state_name = state.get("name", "Обновление")
            has_updates = neg.get("has_updates", False)
            vacancy = neg.get("vacancy", {})
            vac_title = html.escape(vacancy.get("name", "Вакансия"))
            employer = html.escape(vacancy.get("employer", {}).get("name", "Работодатель"))
            chat_url = neg.get("alternate_url") or f"https://hh.ru/applicant/negotiations/{neg_id}"

            prev = tracker.get(neg_id)

            if state_id in ["discard", "rejected"]:
                if prev is None or prev.get("state_id") != "discard_deleted":
                    tracker[neg_id] = {
                        "state_id": "discard_deleted",
                        "state_name": "Отказ (удален)",
                        "updated_at": neg.get("updated_at")
                    }
                    updated = True
                    vac_raw_id = vacancy.get("id") or neg_id
                    remove_vacancy_from_profile_and_history(f"hh_{vac_raw_id}")
                    if not is_initial_run and bot_token and chat_id:
                        msg = (
                            f"🗑 <b>Отказ автоматически удален из профиля и истории!</b>\n\n"
                            f"💼 <b>{vac_title}</b>\n"
                            f"🏢 <b>Компания:</b> {employer}\n\n"
                            f"✅ <i>Отклик скрыт из истории Career Hub.</i>"
                        )
                        send_message(bot_token, chat_id, msg)
                continue

            if prev is None:
                tracker[neg_id] = {
                    "state_id": state_id,
                    "state_name": state_name,
                    "updated_at": neg.get("updated_at")
                }
                updated = True
                # Если это самый первый запуск без базы отслеживания, не спамим старыми приглашениями
                # Уведомляем только если есть непрочитанные обновления (has_updates)
                if not is_initial_run and state_id == "invitation":
                    msg = (
                        f"🎉 <b>ПРИГЛАШЕНИЕ НА СОБЕСЕДОВАНИЕ! (HeadHunter)</b>\n\n"
                        f"💼 <b>{vac_title}</b>\n"
                        f"🏢 <b>Компания:</b> {employer}\n"
                        f"📌 <b>Статус:</b> {html.escape(state_name)} 🔥\n\n"
                        f"👉 <b><a href='{chat_url}'>Открыть переписку на HeadHunter</a></b>"
                    )
                    reply_markup = {
                        "inline_keyboard": [
                            [{"text": "🚀 Открыть чат на HH", "url": chat_url}],
                            [{"text": "💼 Career Hub Mini App", "web_app": {"url": "https://jobs.shadowkek.space/"}}]
                        ]
                    }
                    send_message(bot_token, chat_id, msg, reply_markup=reply_markup)
                continue

            prev_state_id = prev.get("state_id")
            if state_id != prev_state_id or has_updates:
                tracker[neg_id] = {
                    "state_id": state_id,
                    "state_name": state_name,
                    "updated_at": neg.get("updated_at")
                }
                updated = True

                if state_id == "invitation":
                    msg = (
                        f"🎉 <b>ПРИГЛАШЕНИЕ НА СОБЕСЕДОВАНИЕ! (HeadHunter)</b>\n\n"
                        f"💼 <b>{vac_title}</b>\n"
                        f"🏢 <b>Компания:</b> {employer}\n"
                        f"📌 <b>Статус:</b> {html.escape(state_name)} 🔥\n\n"
                        f"👉 <b><a href='{chat_url}'>Перейти к согласованию интервью</a></b>"
                    )
                    reply_markup = {
                        "inline_keyboard": [
                            [{"text": "🚀 Открыть чат на HH", "url": chat_url}],
                            [{"text": "💼 Career Hub Mini App", "web_app": {"url": "https://jobs.shadowkek.space/"}}]
                        ]
                    }
                    send_message(bot_token, chat_id, msg, reply_markup=reply_markup)
                elif has_updates and state_id not in ["discard"]:
                    msg = (
                        f"💬 <b>Новое сообщение / Ответ от работодателя! (HeadHunter)</b>\n\n"
                        f"💼 <b>{vac_title}</b> ({employer})\n"
                        f"📌 <b>Текущий статус:</b> {html.escape(state_name)}\n\n"
                        f"👉 <b><a href='{chat_url}'>Открыть диалог</a></b>"
                    )
                    reply_markup = {
                        "inline_keyboard": [
                            [{"text": "💬 Открыть диалог", "url": chat_url}]
                        ]
                    }
                    send_message(bot_token, chat_id, msg, reply_markup=reply_markup)

        if updated:
            save_hh_negotiations_tracker(tracker)
    except Exception as e:
        logger.error(f"Ошибка проверки negotiations HH: {e}")


def check_and_send_scheduled_digest(bot_token, chat_id, config):
    """Отправляет тихую сводку раз в 24 часа по накопленным подходящим вакансиям (скор 6-8)."""
    last_time = config.get("last_digest_time") if config else None
    now = time.time()
    
    # Если запуск первый и таймстемп не инициализирован вовсе, фиксируем текущее время без спама
    if last_time is None:
        cfg = load_config()
        cfg["last_digest_time"] = now
        save_config(cfg)
        if isinstance(config, dict):
            config["last_digest_time"] = now
        return

    try:
        if now - float(last_time) < 86400:
            return
    except (TypeError, ValueError):
        return

    cache = load_cache()
    statuses = load_statuses()
    notify_min_score = config.get("notify_min_score", 9) if config else 9
    quiet_vacs = []

    for v_id, v in cache.items():
        score = v.get("match_score", 0)
        if 6 <= score < notify_min_score:
            st = statuses.get(v_id, {}).get("status", "inbox")
            if st != "archived":
                quiet_vacs.append(v)

    if quiet_vacs:
        text = (
            f"📊 <b>Сводка Career Hub за 24 часа</b>\n\n"
            f"В фоновом режиме найдено и обработано <b>{len(quiet_vacs)}</b> подходящих вакансий (скор 6-{notify_min_score-1}).\n"
            f"Они сохранены в вашей веб-панели и ждут просмотра!\n\n"
            f"💡 <i>Мгновенные уведомления в Telegram приходят только по топ-матчам ({notify_min_score}-10/10) "
            f"и откликам, чтобы не спамить в чат.</i>"
        )
        reply_markup = {
            "inline_keyboard": [
                [{"text": "💼 Открыть Career Hub Mini App", "web_app": {"url": "https://jobs.shadowkek.space/"}}]
            ]
        }
        send_message(bot_token, chat_id, text, reply_markup=reply_markup)

    cur_cfg = load_config()
    cur_cfg["last_digest_time"] = now
    save_config(cur_cfg)
    if isinstance(config, dict):
        config["last_digest_time"] = now


def process_vacancy_auto_apply_and_notify(v, analysis, config, token, chat_id, groq_key, groq_model):
    """
    Обрабатывает подходящую вакансию:
    1. Авто-отклик (если включен и score >= auto_apply_min_score):
       - Генерирует персональное сопроводительное письмо через Groq AI
       - Если есть прямая интеграция (HeadHunter API с токеном), отправляет отклик через API
       - Если нет прямой интеграции, выполняет авто-подготовку (письмо + быстрый отклик)
    2. Уведомление в Telegram:
       - Отправляется ТОЛЬКО для:
         а) Успешного авто-отклика через API
         б) Требования ручного действия по вакансии со скором >= notify_min_score (тест или прямой сайт)
         в) Обычных вакансий со скором >= notify_min_score (топ-матчи 9-10)
       - Вакансии со средним скором (6-8) сохраняются в Mini App тихо без спама в чат.
    """
    v_id = v["id"]
    raw_id = v.get("raw_id") or (str(v_id).replace("hh_", "") if str(v_id).startswith("hh_") else str(v_id))
    score = analysis.get("match_score", 0)
    raw_title = v.get("title", "")
    clean_title = raw_title.replace("Требуется «", "").replace("»", "").strip()
    company = v.get("company", "Не указана")
    source_name = v.get("source_name", "IT-Площадка")
    source_emoji = v.get("source_emoji", "💼")
    salary = v.get("salary")
    link = v.get("link", "#")

    # Используем переданную конфигурацию или загружаем актуальную
    cfg = dict(config) if config else load_config()
    auto_apply_enabled = cfg.get("auto_apply_enabled", True)
    auto_apply_min_score = cfg.get("auto_apply_min_score", 7)
    notify_min_score = cfg.get("notify_min_score", 9)
    hh_token = cfg.get("hh_access_token", "").strip().strip('"').strip("'")
    hh_resume_id = cfg.get("hh_resume_id", "").strip()
    hh_cookie = cfg.get("hh_web_cookie", "").strip()
    hh_resume_hash = cfg.get("hh_resume_hash", "b81ca639ff1137b70d0039ed1f57755649766f").strip()

    cover_letter = None

    # Экранированные переменные для безопасной вставки в Telegram HTML
    esc_title = html.escape(clean_title)
    esc_company = html.escape(company)
    esc_salary = html.escape(salary) if salary else None
    esc_source_name = html.escape(source_name)

    # 1. Сценарий: Авто-отклик (скор >= auto_apply_min_score, по умолч. 7+)
    if auto_apply_enabled and score >= auto_apply_min_score:
        logger.info(f"Генерация авто-отклика для [{source_name}] {clean_title} (Score {score})...")
        try:
            cover_letter = generate_cover_letter(groq_key, groq_model, v)
        except Exception as e:
            logger.error(f"Ошибка генерации письма для авто-отклика {v_id}: {e}")
            cover_letter = None

        if cover_letter:
            v["cover_letter"] = cover_letter
            esc_letter = html.escape(cover_letter)

            # Проверяем прямую отправку через HeadHunter (веб-сессия или API токен)
            if v.get("source") in ["hh", "headhunter"] or str(v_id).startswith("hh_"):
                hh_res = None
                applied_method = None
                if hh_cookie:
                    hh_res = apply_hh_vacancy_web(raw_id, hh_resume_hash, cover_letter, hh_cookie)
                    applied_method = "hh_web"
                elif hh_token:
                    if not hh_resume_id:
                        resume_obj = auto_select_hh_resume(hh_token)
                        if resume_obj:
                            hh_resume_id = resume_obj.get("id", "")
                            cfg_save = load_config()
                            cfg_save["hh_resume_id"] = hh_resume_id
                            save_config(cfg_save)
                            if isinstance(config, dict):
                                config["hh_resume_id"] = hh_resume_id

                    if hh_resume_id:
                        hh_res = apply_hh_vacancy_api(raw_id, hh_resume_id, cover_letter, hh_token)
                        applied_method = "hh_api"

                if hh_res:
                    if hh_res.get("ok"):
                        update_vacancy_status(
                            v_id, "applied",
                            cover_letter=cover_letter,
                            auto_applied=True,
                            method=applied_method or "hh_web",
                            detail=hh_res.get("detail", "")
                        )
                        msg = (
                            f"🚀 <b>АВТО-ОТКЛИК ОТПРАВЛЕН! [HeadHunter]</b>\n\n"
                            f"🔥 <b>Совпадение: {score}/10</b>\n"
                            f"💼 <b>{esc_title}</b>\n"
                            f"🏢 <b>Компания:</b> {esc_company}\n"
                        )
                        if esc_salary:
                            msg += f"💰 <b>Зарплата:</b> {esc_salary}\n"
                        msg += (
                            f"\n📝 <b>Отправленное сопроводительное письмо:</b>\n"
                            f"<blockquote>{esc_letter}</blockquote>\n\n"
                            f"✅ <i>Резюме и письмо успешно зарегистрированы на hh.ru.</i>"
                        )
                        reply_markup = {
                            "inline_keyboard": [
                                [
                                    {"text": "🔗 Открыть вакансию на HH", "url": link},
                                    {"text": "💼 Открыть в Mini App", "web_app": {"url": "https://jobs.shadowkek.space/"}}
                                ]
                            ]
                        }
                        send_message(token, chat_id, msg, reply_markup=reply_markup)
                        logger.info(f"Авто-отклик на HH {v_id} отправлен и доставлен в TG.")
                        return True, True

                    elif hh_res.get("status") == "already_applied":
                        update_vacancy_status(
                            v_id, "applied",
                            cover_letter=cover_letter,
                            auto_applied=True,
                            method="already_applied",
                            detail=hh_res.get("detail", "Отклик уже зарегистрирован на hh.ru")
                        )
                        msg = (
                            f"🚀 <b>ОТКЛИК УЖЕ ЗАРЕГИСТРИРОВАН НА HeadHunter!</b>\n\n"
                            f"🔥 <b>Совпадение: {score}/10</b>\n"
                            f"💼 <b>{esc_title}</b>\n"
                            f"🏢 <b>Компания:</b> {esc_company}\n"
                        )
                        if esc_salary:
                            msg += f"💰 <b>Зарплата:</b> {esc_salary}\n"
                        if esc_letter:
                            msg += (
                                f"\n📝 <b>Сопроводительное письмо:</b>\n"
                                f"<blockquote>{esc_letter}</blockquote>\n\n"
                            )
                        msg += f"✅ <i>Отклик на эту вакансию уже зарегистрирован в вашем личном кабинете на hh.ru.</i>"
                        reply_markup = {
                            "inline_keyboard": [
                                [
                                    {"text": "🔗 Открыть вакансию на HH", "url": link},
                                    {"text": "💼 Открыть в Mini App", "web_app": {"url": "https://jobs.shadowkek.space/"}}
                                ]
                            ]
                        }
                        send_message(token, chat_id, msg, reply_markup=reply_markup)
                        logger.info(f"Отклик на HH {v_id} уже зарегистрирован, подтверждение отправлено в TG.")
                        return True, True

                    elif hh_res.get("status") == "captcha_required":
                        update_vacancy_status(v_id, "inbox", cover_letter=cover_letter, auto_prepared=True, captcha_required=True)
                        if score >= notify_min_score:
                            msg = (
                                f"⚠️ <b>HeadHunter запросил проверку (капчу)!</b>\n\n"
                                f"🔥 <b>Совпадение: {score}/10</b> • 🔴 <b>HeadHunter</b>\n"
                                f"💼 <b>{esc_title}</b>\n"
                                f"🏢 <b>Компания:</b> {esc_company}\n"
                            )
                            if esc_salary:
                                msg += f"💰 <b>Зарплата:</b> {esc_salary}\n"
                            msg += (
                                f"\n📝 <b>Сопроводительное письмо уже подготовлено:</b>\n"
                                f"<blockquote>{esc_letter}</blockquote>\n\n"
                                f"💡 <i>HeadHunter запросил проверку на человека (капчу). Перейдите по кнопке ниже и подтвердите отправку отклика в 1 клик с уже готовым письмом.</i>"
                            )
                            reply_markup = {
                                "inline_keyboard": [
                                    [{"text": "🚀 Откликнуться на HH (пройти проверку)", "url": link}],
                                    [{"text": "✅ Отметить 'Откликнулся'", "callback_data": f"mark_applied_{v_id}"}],
                                    [{"text": "💼 Открыть в Mini App", "web_app": {"url": "https://jobs.shadowkek.space/"}}]
                                ]
                            }
                            send_message(token, chat_id, msg, reply_markup=reply_markup)
                            return False, True
                        return False, False

                    elif hh_res.get("status") == "test_required":
                        update_vacancy_status(v_id, "inbox", cover_letter=cover_letter, auto_prepared=True, needs_test=True)
                        if score >= notify_min_score:
                            msg = (
                                f"⚠️ <b>Внимание: Требуется анкета / тест работодателя!</b>\n\n"
                                f"🔥 <b>Совпадение: {score}/10</b> • 🔴 <b>HeadHunter</b>\n"
                                f"💼 <b>{esc_title}</b>\n"
                                f"🏢 <b>Компания:</b> {esc_company}\n"
                            )
                            if esc_salary:
                                msg += f"💰 <b>Зарплата:</b> {esc_salary}\n"
                            msg += (
                                f"\n📝 <b>Сопроводительное письмо уже подготовлено:</b>\n"
                                f"<blockquote>{esc_letter}</blockquote>\n\n"
                                f"💡 <i>Работодатель требует ответить на вопросы или пройти опрос на hh.ru перед подачей.</i>"
                            )
                            reply_markup = {
                                "inline_keyboard": [
                                    [{"text": "🚀 Пройти тест и откликнуться", "url": link}],
                                    [{"text": "✅ Отметить 'Откликнулся'", "callback_data": f"mark_applied_{v_id}"}],
                                    [{"text": "💼 Открыть в Mini App", "web_app": {"url": "https://jobs.shadowkek.space/"}}]
                                ]
                            }
                            send_message(token, chat_id, msg, reply_markup=reply_markup)
                            return False, True
                        return False, False

                    elif hh_res.get("status") == "direct_required":
                        direct_url = hh_res.get("redirect_url") or link
                        update_vacancy_status(v_id, "inbox", cover_letter=cover_letter, auto_prepared=True, direct_url=direct_url)
                        if score >= notify_min_score:
                            msg = (
                                f"🌐 <b>Внимание: Требуется прямой отклик на сайте работодателя!</b>\n\n"
                                f"🔥 <b>Совпадение: {score}/10</b> • 🔴 <b>HeadHunter</b>\n"
                                f"💼 <b>{esc_title}</b>\n"
                                f"🏢 <b>Компания:</b> {esc_company}\n"
                            )
                            if esc_salary:
                                msg += f"💰 <b>Зарплата:</b> {esc_salary}\n"
                            msg += (
                                f"\n📝 <b>Сопроводительное письмо уже подготовлено:</b>\n"
                                f"<blockquote>{esc_letter}</blockquote>\n\n"
                                f"💡 <i>Работодатель принимает отклики через свою внешнюю систему подбора персонала.</i>"
                            )
                            reply_markup = {
                                "inline_keyboard": [
                                    [{"text": "🚀 Перейти к подаче заявки", "url": direct_url}],
                                    [{"text": "✅ Отметить 'Откликнулся'", "callback_data": f"mark_applied_{v_id}"}],
                                    [{"text": "💼 Открыть в Mini App", "web_app": {"url": "https://jobs.shadowkek.space/"}}]
                                ]
                            }
                            send_message(token, chat_id, msg, reply_markup=reply_markup)
                            return False, True
                        return False, False

                    elif hh_res.get("status") == "token_expired":
                        update_vacancy_status(v_id, "inbox", cover_letter=cover_letter, auto_prepared=True, token_expired=True)
                        send_message(token, chat_id, 
                            "⚠️ <b>Внимание: Токен HeadHunter API недействителен или истёк!</b>\n\n"
                            "Автоматическая подача временно переключена в режим авто-подготовки.\n"
                            "Пожалуйста, обновите токен командой: <code>/set_hh_token &lt;новый_токен&gt;</code>"
                        )

                    elif hh_res.get("status") == "cookie_expired":
                        update_vacancy_status(v_id, "inbox", cover_letter=cover_letter, auto_prepared=True, cookie_expired=True)
                        logger.warning(f"HeadHunter web-сессия и стекла/требует обновления для {v_id}. Переключение в режим авто-подготовки.")

                    elif hh_res.get("status") == "limit_exceeded":
                        update_vacancy_status(v_id, "inbox", cover_letter=cover_letter, auto_prepared=True, limit_exceeded=True)
                        send_message(token, chat_id,
                            "⚠️ <b>Достигнут суточный лимит откликов на HeadHunter (200 откликов в сутки).</b>\n\n"
                            "Дальнейшие вакансии будут автоматически подготавливаться с готовыми письмами в Career Hub."
                        )

            # Платформа без прямой API-авторизации (Хабр, GetMatch, СЕТИ, SuperJob и т.д., либо HH без токена)
            # Выполняем Смарт-Автоподготовку: сохраняем готовое письмо
            update_vacancy_status(v_id, "inbox", cover_letter=cover_letter, auto_prepared=True)

            # Уведомляем в Telegram ТОЛЬКО если скор высокий (score >= notify_min_score, по умолч. 9-10)
            if score >= notify_min_score:
                role = analysis.get("role", "java")
                if role == "vpn_dev":
                    role_badge = "🛡️ [VPN / Xray Core Dev]"
                elif role in ["ai_dev", "java_ai"]:
                    role_badge = "🤖 [AI & LLM Integration Developer]"
                elif role == "python_dev":
                    role_badge = "🐍 [Python Backend Developer]"
                elif role == "backend":
                    role_badge = "⚡ [Junior Backend Developer]"
                elif role == "fullstack_js":
                    role_badge = "🌐 [Fullstack JS / Frontend]"
                else:
                    role_badge = "☕ [Java Backend]"

                msg = (
                    f"{role_badge} • {source_emoji} <b>{esc_source_name}</b>\n"
                    f"🔥 <b>ТОП-СОВПАДЕНИЕ: {score}/10 (Отличный шанс!)</b>\n\n"
                    f"💼 <b>{esc_title}</b>\n"
                    f"🏢 <b>Компания:</b> {esc_company}\n"
                )
                if esc_salary:
                    msg += f"💰 <b>Зарплата:</b> {esc_salary}\n"

                stack = extract_tech_stack(v.get("description", ""), clean_title)
                if stack:
                    msg += f"\n🛠 <b>Стек:</b> {' • '.join(f'<code>{html.escape(s)}</code>' for s in stack[:6])}\n"

                verdict_str = analysis.get('verdict', '')
                if verdict_str:
                    msg += f"\n💡 <b>Вердикт:</b> {html.escape(verdict_str)}\n"
                msg += (
                    f"\n📝 <b>Авто-подготовленное сопроводительное письмо:</b>\n"
                    f"<blockquote>{esc_letter}</blockquote>\n\n"
                    f"💡 <i>Нажмите на текст письма выше для копирования в 1 клик. Нажмите 'Откликнуться' для мгновенной подачи!</i>"
                )
                reply_markup = {
                    "inline_keyboard": [
                        [
                            {"text": f"🚀 Откликнуться на {source_name}", "url": link},
                            {"text": "✅ Отметить 'Откликнулся'", "callback_data": f"mark_applied_{v_id}"}
                        ],
                        [
                            {"text": "🔄 Перегенерировать письмо", "callback_data": f"apply_{v_id}"},
                            {"text": "💼 Открыть в Mini App", "web_app": {"url": "https://jobs.shadowkek.space/"}}
                        ]
                    ]
                }
                send_message(token, chat_id, msg, reply_markup=reply_markup)
                logger.info(f"Топ-вакансия [{source_name}] {clean_title} (Score {score}) с готовым письмом доставлена в TG.")
                return False, True

            return False, False

    # 2. Обычная вакансия (авто-отклик выключен или скор ниже auto_apply_min_score)
    # Отправляем в Telegram ТОЛЬКО если скор высокий (score >= notify_min_score)
    if score >= notify_min_score:
        msg = format_ai_vacancy_message(v, analysis)
        reply_markup = {
            "inline_keyboard": [
                [
                    {"text": "📝 Подготовить отклик", "callback_data": f"apply_{v_id}"},
                    {"text": f"🔗 Открыть на {source_name}", "url": link}
                ],
                [
                    {"text": "✅ Отметить 'Откликнулся'", "callback_data": f"mark_applied_{v_id}"},
                    {"text": "💼 Открыть в Mini App", "web_app": {"url": "https://jobs.shadowkek.space/"}}
                ]
            ]
        }
        send_message(token, chat_id, msg, reply_markup=reply_markup)
        logger.info(f"Топ-вакансия [{source_name}] {clean_title} (Score {score}) доставлена в TG.")
        return False, True

    # Иначе (score 6-8): Тихо сохранено в кэше для Mini App, уведомление НЕ шлем (без спама)
    logger.info(f"Вакансия [{source_name}] {clean_title} (Score {score}) сохранена в кэш без Telegram-уведомления (порог: {notify_min_score}).")
    return False, False


# --- ФОНОВЫЙ СЛУШАТЕЛЬ TELEGRAM (Обработка кнопок и команд) ---

def telegram_updates_worker():
    logger.info("Запуск обработчика входящих событий Telegram...")
    config = load_config()
    token = config["bot_token"]
    groq_key = config["groq_api_key"]
    groq_model = config.get("groq_model", "openai/gpt-oss-120b")

    offset = 0
    while True:
        try:
            url = f"https://api.telegram.org/bot{token}/getUpdates"
            payload = {
                "offset": offset,
                "timeout": 20,
                "allowed_updates": ["message", "callback_query"]
            }
            resp = requests.post(url, json=payload, timeout=25)

            if resp.status_code == 200:
                data = resp.json()
                for update in data.get("result", []):
                    offset = update["update_id"] + 1

                    if "callback_query" in update:
                        cb = update["callback_query"]
                        cb_id = cb["id"]
                        cb_data = cb.get("data", "")
                        from_user = cb["from"]["id"]
                        message = cb.get("message", {})
                        chat_id = message.get("chat", {}).get("id", from_user)

                        cfg = load_config()
                        allowed_owner_id = cfg.get("allowed_telegram_id") or cfg.get("chat_id") or 1139872910
                        if from_user != allowed_owner_id and chat_id != allowed_owner_id:
                            logger.warning(f"Попытка доступа от неизвестного пользователя {from_user} в callback {cb_data}")
                            answer_callback_query(token, cb_id, "⛔ Доступ запрещён", show_alert=True)
                            continue

                        logger.info(f"Получен callback_query: '{cb_data}' от пользователя {from_user}")

                        if cb_data == "send_login_link":
                            answer_callback_query(token, cb_id, "Генерация ссылки...")
                            ot_token = create_one_time_token(from_user)
                            login_url = f"https://jobs.shadowkek.space/?auth={ot_token}"
                            send_message(token, chat_id, 
                                "🔑 <b>Ссылка для входа в Career Hub на ПК:</b>\n\n"
                                f"👉 <b><a href='{login_url}'>Нажмите здесь для входа</a></b>\n\n"
                                f"<code>{login_url}</code>\n\n"
                                "⏳ Ссылка действует 10 минут. После перехода авторизация сохранится на этом компьютере на <b>30 дней</b>.",
                                reply_markup={
                                    "inline_keyboard": [
                                        [{"text": "🚀 Войти в браузере", "url": login_url}]
                                    ]
                                }
                            )
                            continue

                        elif cb_data.startswith("apply_"):
                            v_id = cb_data.replace("apply_", "")

                            answer_callback_query(token, cb_id, "⏳ Groq AI составляет отклик...")

                            vacancy = fetch_vacancy_by_id(v_id)

                            clean_title = vacancy.get("title", f"Вакансия #{v_id}").replace("Требуется «", "").replace("»", "").strip() if vacancy else f"Вакансия #{v_id}"
                            company = vacancy.get("company", "Не указана") if vacancy else "Не указана"
                            v_link = vacancy.get("link", "#") if vacancy else "#"
                            source_name = vacancy.get("source_name", "Площадка") if vacancy else "Площадка"

                            if not vacancy:
                                logger.warning(f"Не удалось найти данные вакансии {v_id}")
                                not_found_text = (
                                    f"⚠️ <b>Данные вакансии не найдены</b>\n\n"
                                    f"Возможно, вакансия уже закрыта или ссылка устарела.\n"
                                    f"Проверить напрямую: {v_link}"
                                )
                                send_message(token, chat_id, not_found_text)
                                continue

                            status_text = (
                                f"⏳ <b>Идёт составление отклика...</b>\n\n"
                                f"💼 <b>Позиция:</b> {clean_title}\n"
                                f"🏢 <b>Компания:</b> {company}\n"
                                f"🌐 <b>Источник:</b> {source_name}\n\n"
                                f"🤖 <i>Groq AI анализирует требования вакансии и адаптирует ваше сопроводительное письмо... (2-4 сек)</i>"
                            )
                            status_msg = send_message(token, chat_id, status_text)
                            status_msg_id = status_msg.get("result", {}).get("message_id") if status_msg else None

                            try:
                                cover_letter = generate_cover_letter(groq_key, groq_model, vacancy)

                                if not cover_letter or len(cover_letter.strip()) < 30:
                                    raise ValueError("Нейросеть вернула пустой или некорректный ответ.")

                                response_text = (
                                    f"📝 <b>Согласование сопроводительного письма</b>\n\n"
                                    f"💼 <b>Позиция:</b> {clean_title}\n"
                                    f"🏢 <b>Компания:</b> {company}\n"
                                    f"🌐 <b>Источник:</b> {source_name}\n\n"
                                    f"<blockquote>{cover_letter}</blockquote>\n\n"
                                    f"💡 <i>Нажмите на текст письма выше, чтобы скопировать его в буфер в 1 клик.</i>"
                                )

                                reply_markup = {
                                    "inline_keyboard": [
                                        [
                                            {"text": f"🚀 Открыть на {source_name}", "url": v_link},
                                            {"text": "🔄 Перегенерировать", "callback_data": f"apply_{v_id}"}
                                        ],
                                        [
                                            {"text": "💼 Открыть в Mini App", "web_app": {"url": "https://jobs.shadowkek.space/"}}
                                        ]
                                    ]
                                }

                                if status_msg_id:
                                    edit_message_text(token, chat_id, status_msg_id, response_text, reply_markup=reply_markup)
                                else:
                                    send_message(token, chat_id, response_text, reply_markup=reply_markup)

                                logger.info(f"Сопроводительное письмо для {clean_title} успешно доставлено в чат {chat_id}.")

                            except Exception as gen_err:
                                logger.error(f"Ошибка генерации Cover Letter для {v_id}: {gen_err}")
                                error_text = (
                                    f"❌ <b>Произошла ошибка при генерации отклика:</b>\n\n"
                                    f"<code>{str(gen_err)[:250]}</code>\n\n"
                                    f"Вы можете попробовать нажать кнопку ещё раз или откликнуться напрямую по ссылке."
                                )
                                error_markup = {
                                    "inline_keyboard": [
                                        [
                                            {"text": "🔄 Попробовать снова", "callback_data": f"apply_{v_id}"},
                                            {"text": f"🔗 Открыть на {source_name}", "url": v_link}
                                        ]
                                    ]
                                }
                                if status_msg_id:
                                    edit_message_text(token, chat_id, status_msg_id, error_text, reply_markup=error_markup)
                                else:
                                    send_message(token, chat_id, error_text, reply_markup=error_markup)

                        elif cb_data.startswith("mark_applied_"):
                            v_id = cb_data.replace("mark_applied_", "")
                            update_vacancy_status(v_id, "applied")
                            answer_callback_query(token, cb_id, "Статус обновлен на 'Откликнулся' ✅")
                            msg_id = message.get("message_id")
                            if msg_id:
                                try:
                                    orig_kb = message.get("reply_markup", {}).get("inline_keyboard", [])
                                    new_kb = []
                                    for row in orig_kb:
                                        new_row = []
                                        for btn in row:
                                            if btn.get("callback_data") == cb_data:
                                                new_row.append({"text": "✅ Откликнулись!", "callback_data": "noop"})
                                            else:
                                                new_row.append(btn)
                                        if new_row:
                                            new_kb.append(new_row)
                                    edit_message_reply_markup(token, chat_id, msg_id, {"inline_keyboard": new_kb})
                                except Exception:
                                    pass
                            continue

                        elif cb_data == "hh_check_now":
                            answer_callback_query(token, cb_id, "Проверка откликов на HeadHunter...")
                            cfg = load_config()
                            hh_tok = cfg.get("hh_access_token", "")
                            hh_cookie = cfg.get("hh_web_cookie", "")
                            if not hh_tok and not hh_cookie:
                                send_message(token, chat_id, "ℹ️ HeadHunter не настроен. Используйте /set_hh_cookie <cookie> или /set_hh_token <токен>.")
                            else:
                                poll_hh_negotiations_and_notify(token, chat_id, access_token=hh_tok, cookie_str=hh_cookie)
                                send_message(token, chat_id, "✅ Проверка HeadHunter завершена.")
                            continue

                        elif cb_data == "btn_clean_discards":
                            answer_callback_query(token, cb_id, "Очистка отказов...")
                            cfg = load_config()
                            hh_tok = cfg.get("hh_access_token", "")
                            hh_cookie = cfg.get("hh_web_cookie", "")
                            if not hh_tok and not hh_cookie:
                                send_message(token, chat_id, "ℹ️ HeadHunter не настроен. Используйте /set_hh_cookie <cookie>.")
                            else:
                                poll_hh_negotiations_and_notify(token, chat_id, access_token=hh_tok, cookie_str=hh_cookie)
                                send_message(token, chat_id, "✨ <b>Очистка завершена!</b> Все отказы удалены из профиля HeadHunter и скрыты из истории.")
                            continue

                        elif cb_data == "btn_settings":
                            answer_callback_query(token, cb_id)
                            cfg = load_config()
                            auto_on = cfg.get("auto_apply_enabled", True)
                            auto_score = cfg.get("auto_apply_min_score", 7)
                            notify_score = cfg.get("notify_min_score", 9)
                            hh_tok = bool(cfg.get("hh_access_token", "").strip())
                            cache = load_cache()
                            statuses = load_statuses()
                            applied_count = sum(1 for s in statuses.values() if s.get("status") == "applied")

                            text = (
                                f"⚙️ <b>Центр управления Career Assistant</b>\n\n"
                                f"🔔 <b>Уведомления в Telegram:</b> Только топ ({notify_score}-10/10)\n"
                                f"   <i>(вакансии со скором 6-{notify_score-1} тихо сохраняются в Mini App)</i>\n\n"
                                f"🤖 <b>Авто-отклики:</b> {'🟢 Включены' if auto_on else '🔴 Выключены'}\n"
                                f"   • Порог авто-отклика: <b>{auto_score}+ баллов</b>\n"
                                f"   • Генерация писем: <b>Groq AI</b>\n\n"
                                f"🔌 <b>Интеграции:</b>\n"
                                f"   • HeadHunter API: {'🟢 Подключён' if hh_tok else '⚪ Без токена (авто-подготовка в 1 клик)'}\n"
                                f"   • Хабр / СЕТИ / SuperJob / LinkedIn: 🟢 Активны\n\n"
                                f"📊 <b>Статистика:</b>\n"
                                f"   • Вакансий в кэше: <b>{len(cache)}</b>\n"
                                f"   • Откликов подано: <b>{applied_count}</b>\n\n"
                                f"<i>Команды для быстрой настройки:</i>\n"
                                f"• <code>/notify_score 8</code> или <code>9</code> — порог уведомлений\n"
                                f"• <code>/auto_apply on</code> / <code>off</code> — режим авто-отклика\n"
                                f"• <code>/set_hh_token &lt;токен&gt;</code> — привязать HeadHunter\n"
                                f"• <code>/digest</code> — сводка за 24ч"
                            )
                            send_message(token, chat_id, text, reply_markup={
                                "inline_keyboard": [
                                    [{"text": "💼 Открыть Career Hub Mini App", "web_app": {"url": "https://jobs.shadowkek.space/"}}],
                                    [{"text": "🔑 Вход на ПК", "callback_data": "send_login_link"}]
                                ]
                            })
                            continue

                    elif "message" in update:
                        msg = update["message"]
                        msg_text = msg.get("text", "").strip()
                        msg_chat_id = msg.get("chat", {}).get("id")
                        from_user = msg.get("from", {}).get("id", msg_chat_id)

                        cfg = load_config()
                        allowed_owner_id = cfg.get("allowed_telegram_id") or cfg.get("chat_id") or 1139872910

                        if from_user != allowed_owner_id and msg_chat_id != allowed_owner_id:
                            logger.warning(f"Попытка доступа от неизвестного пользователя {from_user} (chat {msg_chat_id})")
                            send_message(token, msg_chat_id, "⛔ <b>Доступ запрещён</b>\n\nЭтот бот и веб-панель Career Hub настроены исключительно для владельца.")
                            continue

                        # Подтверждение сессии браузера через глубокую ссылку /start login_<session_id>
                        if msg_text.startswith("/start login_") or msg_text.startswith("/start auth_"):
                            session_id = msg_text.replace("/start login_", "").replace("/start auth_", "").strip()
                            with pending_logins_lock:
                                pending = pending_logins.get(session_id)
                                if pending:
                                    perm_token, expires_at = create_user_session(from_user, device="Desktop PC", days=30)
                                    pending["approved"] = True
                                    pending["token"] = perm_token
                                    send_message(token, msg_chat_id, 
                                        "✅ <b>Вход в Career Hub подтверждён!</b>\n\n"
                                        "🖥️ Доступ на вашем компьютере успешно активирован на <b>30 дней</b>.\n"
                                        "Окно в браузере обновится автоматически через секунду.",
                                        reply_markup={
                                            "inline_keyboard": [
                                                [{"text": "💼 Открыть в Mini App", "web_app": {"url": "https://jobs.shadowkek.space/"}}]
                                            ]
                                        }
                                    )
                                else:
                                    send_message(token, msg_chat_id, "⚠️ <b>Сессия авторизации не найдена или истекла.</b>\n\nОбновите страницу входа в браузере и попробуйте снова.")
                            continue

                        # Подтверждение по 6-значному PIN коду
                        if re.fullmatch(r"^\d{6}$", msg_text):
                            matched_session_id = None
                            with pending_logins_lock:
                                for s_id, s_data in pending_logins.items():
                                    if s_data.get("code") == msg_text and not s_data.get("approved"):
                                        matched_session_id = s_id
                                        perm_token, expires_at = create_user_session(from_user, device="Desktop PC", days=30)
                                        s_data["approved"] = True
                                        s_data["token"] = perm_token
                                        break
                            if matched_session_id:
                                send_message(token, msg_chat_id, 
                                    f"✅ <b>Код {msg_text} подтверждён!</b>\n\n"
                                    "🖥️ Вход на компьютере выполнен успешно! Сессия сохранена на <b>30 дней</b>.",
                                    reply_markup={
                                        "inline_keyboard": [
                                            [{"text": "💼 Открыть в Mini App", "web_app": {"url": "https://jobs.shadowkek.space/"}}]
                                        ]
                                    }
                                )
                                continue
                            else:
                                send_message(token, msg_chat_id, f"⚠️ Не найдено активных запросов на вход с кодом <code>{msg_text}</code>.")
                                continue

                        # Быстрая ссылка на вход через команду /login или /web
                        if msg_text in ["/login", "/web"]:
                            ot_token = create_one_time_token(from_user)
                            login_url = f"https://jobs.shadowkek.space/?auth={ot_token}"
                            send_message(token, msg_chat_id, 
                                "🔑 <b>Ссылка для входа в Career Hub на ПК:</b>\n\n"
                                f"👉 <b><a href='{login_url}'>Нажмите сюда для автоматического входа</a></b>\n\n"
                                f"Или скопируйте:\n<code>{login_url}</code>\n\n"
                                "⏳ Ссылка активна 10 минут. После перехода авторизация сохранится на этом компьютере на <b>30 дней</b>.",
                                reply_markup={
                                    "inline_keyboard": [
                                        [{"text": "🚀 Войти в браузере", "url": login_url}],
                                        [{"text": "💼 Открыть в Mini App", "web_app": {"url": "https://jobs.shadowkek.space/"}}]
                                    ]
                                }
                            )
                            continue

                        if msg_text.startswith("/auto_apply"):
                            parts = msg_text.split()
                            cfg = load_config()
                            if len(parts) > 1:
                                arg = parts[1].lower()
                                if arg in ["on", "1", "true", "вкл", "да"]:
                                    cfg["auto_apply_enabled"] = True
                                elif arg in ["off", "0", "false", "выкл", "нет"]:
                                    cfg["auto_apply_enabled"] = False
                                save_config(cfg)
                            st_str = "🟢 <b>ВКЛЮЧЕНЫ</b>" if cfg.get("auto_apply_enabled", True) else "🔴 <b>ВЫКЛЮЧЕНЫ</b>"
                            score_th = cfg.get("auto_apply_min_score", 7)
                            hh_tok = cfg.get("hh_access_token", "")
                            hh_status_str = "✅ HeadHunter API подключён" if hh_tok else "ℹ️ HeadHunter API не подключён (подготовка писем в 1 клик)"
                            resp_text = (
                                f"🤖 <b>Режим авто-откликов:</b> {st_str}\n\n"
                                f"🎯 <b>Порог скора для авто-отклика:</b> {score_th}+ баллов\n"
                                f"🔌 <b>Статус интеграции:</b> {hh_status_str}\n\n"
                                f"• Вакансии {score_th}+ баллов получают готовое персональное письмо.\n"
                                f"• При подключённом токене HH отклик отправляется автоматически через API.\n"
                                f"• На других источниках письмо прикрепляется к карточке в 1 клик.\n\n"
                                f"<i>Команды:</i>\n"
                                f"• <code>/auto_apply on</code> / <code>/auto_apply off</code> — управление\n"
                                f"• <code>/auto_score 7</code> — изменить порог скора\n"
                                f"• <code>/set_hh_token &lt;токен&gt;</code> — подключить HeadHunter API"
                            )
                            send_message(token, msg_chat_id, resp_text)
                            continue

                        if msg_text.startswith("/notify_score"):
                            parts = msg_text.split()
                            cfg = load_config()
                            if len(parts) > 1:
                                try:
                                    val = int(parts[1])
                                    if 1 <= val <= 10:
                                        cfg["notify_min_score"] = val
                                        save_config(cfg)
                                        send_message(token, msg_chat_id, 
                                            f"🔔 <b>Порог уведомлений в Telegram обновлён: {val}/10!</b>\n\n"
                                            f"Теперь бот будет присылать мгновенные уведомления только для вакансий с оценкой <b>{val} и выше</b> (топ-матчи).\n"
                                            f"Все вакансии с оценкой от 6 до {val-1} будут тихо сохраняться в Career Hub без спама."
                                        )
                                        continue
                                    else:
                                        send_message(token, msg_chat_id, "⚠️ Укажите число от 1 до 10 (например: <code>/notify_score 9</code>).")
                                        continue
                                except ValueError:
                                    send_message(token, msg_chat_id, "⚠️ Укажите число от 1 до 10 (например: <code>/notify_score 9</code>).")
                                    continue
                            cur_val = cfg.get("notify_min_score", 9)
                            send_message(token, msg_chat_id, 
                                f"🔔 <b>Текущий порог мгновенных уведомлений в Telegram: {cur_val}/10</b>\n\n"
                                f"Вакансии ниже {cur_val} баллов тихо сохраняются в Mini App (без спама в чат).\n"
                                f"Чтобы изменить порог, отправьте: <code>/notify_score 8</code> или <code>/notify_score 9</code>."
                            )
                            continue

                        if msg_text.startswith("/auto_score"):
                            parts = msg_text.split()
                            cfg = load_config()
                            if len(parts) > 1:
                                try:
                                    val = int(parts[1])
                                    if 1 <= val <= 10:
                                        cfg["auto_apply_min_score"] = val
                                        save_config(cfg)
                                        send_message(token, msg_chat_id, 
                                            f"🎯 <b>Порог скора для авто-отклика установлен на {val}/10!</b>\n\n"
                                            f"Вакансии с оценкой {val} и выше будут автоматически получать подготовленное сопроводительное письмо и отправляться через API."
                                        )
                                        continue
                                    else:
                                        send_message(token, msg_chat_id, "⚠️ Укажите число от 1 до 10.")
                                        continue
                                except ValueError:
                                    send_message(token, msg_chat_id, "⚠️ Укажите число от 1 до 10.")
                                    continue
                            cur_val = cfg.get("auto_apply_min_score", 7)
                            send_message(token, msg_chat_id, f"🎯 Порог скора для авто-отклика: <b>{cur_val}/10</b>. Для изменения: <code>/auto_score 7</code>.")
                            continue

                        if msg_text.startswith("/set_hh_cookie"):
                            parts = msg_text.split(maxsplit=1)
                            if len(parts) < 2 or not parts[1].strip():
                                send_message(token, msg_chat_id, 
                                    "ℹ️ <b>Подключение HeadHunter через веб-сессию (Cookie)</b>\n\n"
                                    "Вставьте строку Cookie из браузера:\n"
                                    "<code>/set_hh_cookie &lt;cookie_string&gt;</code>\n\n"
                                    "Для отключения: <code>/set_hh_cookie off</code>\n\n"
                                    "Бот проверит профиль, привяжет ваше опубликованное Java-резюме и активирует прямую подачу откликов."
                                )
                                continue
                            new_cookie = parts[1].strip().strip('"').strip("'")
                            if new_cookie.lower() in ["off", "clear", "remove", "disable", "выкл", "удалить"]:
                                cfg = load_config()
                                cfg["hh_web_cookie"] = ""
                                save_config(cfg)
                                send_message(token, msg_chat_id, "⚪ <b>Веб-сессия HeadHunter отключена.</b>\nБот переведён в режим авто-подготовки сопроводительных писем в 1 клик.")
                                continue

                            send_message(token, msg_chat_id, "⏳ Проверка сессии HeadHunter...")
                            prof = check_hh_web_cookie(new_cookie)
                            if not prof:
                                send_message(token, msg_chat_id, "❌ <b>Сессия недействительна или отклонена HeadHunter.</b>\nУбедитесь, что скопировали полную строку Cookie.")
                                continue

                            cfg = load_config()
                            cfg["hh_web_cookie"] = new_cookie
                            if not cfg.get("hh_resume_hash"):
                                cfg["hh_resume_hash"] = "b81ca639ff1137b70d0039ed1f57755649766f"
                            save_config(cfg)

                            res_count = len(prof.get("resumes", []))
                            send_message(token, msg_chat_id, 
                                f"🎉 <b>Веб-сессия HeadHunter успешно подключена!</b>\n\n"
                                f"👤 <b>Кандидат:</b> {html.escape(prof.get('name', 'Никита Фролов'))}\n"
                                f"🌐 <b>Регион/Хост:</b> <code>{html.escape(prof.get('host', 'volgograd.hh.ru'))}</code>\n"
                                f"📄 <b>Активное резюме:</b> Java-разработчик (<code>{cfg.get('hh_resume_hash', '')}</code>)\n"
                                f"📋 <b>Резюме в профиле:</b> {res_count}\n\n"
                                f"🚀 <i>Теперь авто-отклики на HeadHunter будут подаваться напрямую с вашим письмом, "
                                f"а бот будет мгновенно уведомлять о приглашениях на собеседования!</i>"
                            )
                            poll_hh_negotiations_and_notify(token, msg_chat_id, cookie_str=new_cookie)
                            continue

                        if msg_text.startswith("/set_hh_token"):
                            parts = msg_text.split(maxsplit=1)
                            if len(parts) < 2 or not parts[1].strip():
                                send_message(token, msg_chat_id, 
                                    "ℹ️ <b>Подключение HeadHunter API</b>\n\n"
                                    "Для автоматической отправки откликов на hh.ru и отслеживания приглашений укажите ваш OAuth токен:\n"
                                    "<code>/set_hh_token &lt;ваш_access_token&gt;</code>\n\n"
                                    "Для отключения токена: <code>/set_hh_token off</code>\n\n"
                                    "Бот проверит профиль, автоматически привяжет ваше опубликованное Java-резюме и активирует прямую отправку."
                                )
                                continue
                            new_tok = parts[1].strip().strip('"').strip("'")
                            if new_tok.lower() in ["off", "clear", "remove", "disable", "выкл", "удалить"]:
                                cfg = load_config()
                                cfg["hh_access_token"] = ""
                                cfg["hh_resume_id"] = ""
                                save_config(cfg)
                                send_message(token, msg_chat_id, "⚪ <b>HeadHunter API отключён.</b>\nБот переведён в режим авто-подготовки сопроводительных писем в 1 клик.")
                                continue

                            send_message(token, msg_chat_id, "⏳ Проверка токена через HeadHunter API...")
                            me = check_hh_token(new_tok)
                            if not me:
                                send_message(token, msg_chat_id, "❌ <b>Токен недействителен или отклонён HeadHunter API.</b>\nПроверьте правильность токена.")
                                continue
                            
                            user_name = f"{me.get('first_name', '')} {me.get('last_name', '')}".strip() or me.get("email", "Пользователь")
                            resumes = fetch_hh_resumes(new_tok)
                            best_resume = auto_select_hh_resume(new_tok)
                            resume_id = best_resume.get("id") if best_resume else ""
                            resume_title = best_resume.get("title", "Не найдено") if best_resume else "Нет активных резюме"

                            cfg = load_config()
                            cfg["hh_access_token"] = new_tok
                            if resume_id:
                                cfg["hh_resume_id"] = resume_id
                            save_config(cfg)

                            send_message(token, msg_chat_id, 
                                f"🎉 <b>HeadHunter API успешно подключён!</b>\n\n"
                                f"👤 <b>Кандидат:</b> {html.escape(user_name)}\n"
                                f"📄 <b>Выбранное резюме:</b> {html.escape(resume_title)}\n"
                                f"🆔 <b>Resume ID:</b> <code>{html.escape(resume_id)}</code>\n"
                                f"📋 <b>Всего резюме в аккаунте:</b> {len(resumes)}\n\n"
                                f"🚀 <i>Теперь авто-отклики на HeadHunter будут отправляться напрямую через официальный API, "
                                f"а бот будет мгновенно уведомлять о приглашениях на собеседования!</i>"
                            )
                            # Инициализируем отслеживание
                            poll_hh_negotiations_and_notify(token, msg_chat_id, access_token=new_tok)
                            continue

                        if msg_text.startswith("/set_hh_resume"):
                            parts = msg_text.split(maxsplit=1)
                            if len(parts) < 2 or not parts[1].strip():
                                send_message(token, msg_chat_id, "Использование: <code>/set_hh_resume &lt;resume_hash_или_id&gt;</code>")
                                continue
                            val = parts[1].strip()
                            cfg = load_config()
                            if len(val) >= 30:
                                cfg["hh_resume_hash"] = val
                            else:
                                cfg["hh_resume_id"] = val
                            save_config(cfg)
                            send_message(token, msg_chat_id, f"✅ Активное резюме HeadHunter установлено: <code>{val}</code>")
                            continue

                        if msg_text.startswith("/set_resume_url"):
                            parts = msg_text.split(maxsplit=1)
                            if len(parts) < 2 or not parts[1].strip():
                                send_message(token, msg_chat_id, "Использование: <code>/set_resume_url &lt;https://...trycloudflare.com&gt;</code>")
                                continue
                            val = parts[1].strip()
                            cfg = load_config()
                            cfg["resume_quick_tunnel_url"] = val
                            save_config(cfg)
                            send_message(token, msg_chat_id, f"✅ Ссылка на интерактивное резюме обновлена:\n<code>{val}</code>")
                            continue

                        if msg_text == "/hh_status":
                            cfg = load_config()
                            tok = cfg.get("hh_access_token", "").strip()
                            cookie = cfg.get("hh_web_cookie", "").strip()
                            tracker = load_hh_negotiations_tracker()

                            if not tok and not cookie:
                                send_message(token, msg_chat_id, 
                                    "🔴 <b>HeadHunter не подключён.</b>\n\n"
                                    "Подключите веб-сессию из браузера командой:\n"
                                    "<code>/set_hh_cookie &lt;cookies&gt;</code>\n"
                                    "Или API токен: <code>/set_hh_token &lt;токен&gt;</code>"
                                )
                                continue

                            status_lines = []
                            if cookie:
                                prof = check_hh_web_cookie(cookie)
                                if prof:
                                    res_hash = cfg.get("hh_resume_hash", "b81ca639ff1137b70d0039ed1f57755649766f")
                                    status_lines.append(
                                        f"🟢 <b>Веб-сессия HeadHunter: Активна</b>\n"
                                        f"👤 <b>Кандидат:</b> {prof.get('name', 'Никита Фролов')}\n"
                                        f"📄 <b>Резюме:</b> Java-разработчик (<code>{res_hash}</code>)\n"
                                        f"🌐 <b>Хост:</b> {prof.get('host', 'volgograd.hh.ru')}"
                                    )
                                else:
                                    status_lines.append("⚠️ <b>Веб-сессия HeadHunter: Требует обновления cookie</b>")

                            if tok:
                                me = check_hh_token(tok)
                                if me:
                                    status_lines.append(f"🟢 <b>API OAuth: Активен</b> ({me.get('first_name', '')} {me.get('last_name', '')})")
                                else:
                                    status_lines.append("⚠️ <b>API OAuth токен устарел</b>")

                            msg_text_out = "\n\n".join(status_lines)
                            msg_text_out += f"\n\n📊 <b>Отслеживаемых откликов в базе:</b> {len(tracker)}\n<i>Проверка приглашений происходит автоматически в каждом цикле.</i>"

                            send_message(token, msg_chat_id, msg_text_out, reply_markup={
                                "inline_keyboard": [
                                    [{"text": "🔄 Проверить отклики сейчас", "callback_data": "hh_check_now"}],
                                    [{"text": "🧹 Очистить отказы из профиля", "callback_data": "btn_clean_discards"}]
                                ]
                            })
                            continue

                        if msg_text in ["/clean_discards", "/clean_refusals", "/clear_discards"]:
                            cfg = load_config()
                            hh_tok = cfg.get("hh_access_token", "").strip()
                            hh_cookie = cfg.get("hh_web_cookie", "").strip()
                            if not hh_tok and not hh_cookie:
                                send_message(token, msg_chat_id, "ℹ️ HeadHunter не подключён. Настройте сессию через /set_hh_cookie")
                            else:
                                send_message(token, msg_chat_id, "🔍 <b>Поиск отказов и очистка истории...</b>")
                                poll_hh_negotiations_and_notify(token, msg_chat_id, access_token=hh_tok, cookie_str=hh_cookie)
                                send_message(token, msg_chat_id, "✨ <b>Очистка завершена!</b> Все найденные отказы удалены из профиля HeadHunter и скрыты из истории Career Hub.")
                            continue

                        if msg_text in ["/settings", "/status"]:
                            cfg = load_config()
                            auto_on = cfg.get("auto_apply_enabled", True)
                            auto_score = cfg.get("auto_apply_min_score", 7)
                            notify_score = cfg.get("notify_min_score", 9)
                            hh_tok = bool(cfg.get("hh_access_token", "").strip())
                            resume_tunnel = cfg.get("resume_quick_tunnel_url", "https://south-tomato-therapist-minimum.trycloudflare.com")

                            cache = load_cache()
                            statuses = load_statuses()
                            applied_count = sum(1 for s in statuses.values() if s.get("status") == "applied")

                            text = (
                                f"⚙️ <b>Центр управления Career Assistant</b>\n\n"
                                f"🔔 <b>Уведомления в Telegram:</b> Только топ ({notify_score}-10/10)\n"
                                f"   <i>(вакансии со скором 6-{notify_score-1} тихо сохраняются в Mini App)</i>\n\n"
                                f"🤖 <b>Авто-отклики:</b> {'🟢 Включены' if auto_on else '🔴 Выключены'}\n"
                                f"   • Порог для авто-отклика: <b>{auto_score}+ баллов</b>\n"
                                f"   • Генерация писем: <b>Groq AI</b>\n"
                                f"   • Резюме / портфолио: <code>{resume_tunnel}</code>\n\n"
                                f"🔌 <b>Интеграции:</b>\n"
                                f"   • HeadHunter: {'🟢 Веб-сессия активна' if cfg.get('hh_web_cookie') else ('🟢 API активен' if hh_tok else '⚪ Не подключён')}\n"
                                f"   • Хабр / СЕТИ / SuperJob / LinkedIn: 🟢 Активны\n\n"
                                f"📊 <b>Статистика:</b>\n"
                                f"   • Вакансий в кэше: <b>{len(cache)}</b>\n"
                                f"   • Откликов подано: <b>{applied_count}</b>\n\n"
                                f"💡 <i>Команды для быстрой настройки:</i>\n"
                                f"• <code>/auto_score 7</code> — порог скора для авто-отклика\n"
                                f"• <code>/notify_score 9</code> — порог уведомлений в чат\n"
                                f"• <code>/set_resume_url &lt;url&gt;</code> — обновить quick tunnel ссылку\n"
                                f"• <code>/auto_apply on/off</code> — режим авто-отклика\n"
                                f"• <code>/hh_status</code> — статус подключения к HeadHunter\n"
                                f"• <code>/digest</code> — сводка вакансий за 24ч\n"
                                f"• <code>/applied</code> — история откликов"
                            )
                            send_message(token, msg_chat_id, text, reply_markup={
                                "inline_keyboard": [
                                    [{"text": "💼 Открыть Career Hub Mini App", "web_app": {"url": "https://jobs.shadowkek.space/"}}],
                                    [{"text": "🔑 Вход на ПК", "callback_data": "send_login_link"}]
                                ]
                            })
                            continue

                        if msg_text == "/digest":
                            cfg = load_config()
                            cache = load_cache()
                            statuses = load_statuses()
                            notify_score = cfg.get("notify_min_score", 9)

                            quiet_vacs = [v for vid, v in cache.items() if 6 <= v.get("match_score", 0) < notify_score and statuses.get(vid, {}).get("status") != "archived"]

                            if not quiet_vacs:
                                send_message(token, msg_chat_id, "ℹ️ Сейчас нет новых необработанных вакансий со скором 6-8 в кэше.")
                            else:
                                sample_lines = []
                                for qv in quiet_vacs[:5]:
                                    title_c = qv.get("title", "").replace("Требуется «", "").replace("»", "")[:35]
                                    sample_lines.append(f"• [{qv.get('source_name', 'IT')}] <b>{title_c}</b> ({qv.get('match_score')}/10)")
                                samples_str = "\n".join(sample_lines)

                                text = (
                                    f"📊 <b>Сводка доступных вакансий (скор 6-{notify_score-1}): {len(quiet_vacs)} шт.</b>\n\n"
                                    f"Эти вакансии были сохранены тихо, чтобы не отвлекать уведомлениями:\n\n"
                                    f"{samples_str}\n"
                                    f"{'<i>...и ещё ' + str(len(quiet_vacs)-5) + ' вакансий</i>' if len(quiet_vacs) > 5 else ''}\n\n"
                                    f"👉 Все они с фильтрами и генерацией писем доступны в Career Hub:"
                                )
                                send_message(token, msg_chat_id, text, reply_markup={
                                    "inline_keyboard": [
                                        [{"text": "💼 Открыть в Career Hub", "web_app": {"url": "https://jobs.shadowkek.space/"}}]
                                    ]
                                })
                            continue

                        if msg_text == "/applied":
                            statuses = load_statuses()
                            cache = load_cache()
                            applied_list = [(vid, s) for vid, s in statuses.items() if s.get("status") == "applied"]
                            if not applied_list:
                                send_message(token, msg_chat_id, "ℹ️ Список откликов пока пуст.")
                                continue

                            applied_list.sort(key=lambda x: x[1].get("applied_at", ""), reverse=True)
                            lines = []
                            for vid, st in applied_list[:10]:
                                v_data = cache.get(vid) or fetch_vacancy_by_id(vid) or {}
                                title_c = v_data.get("title", f"Вакансия #{vid}").replace("Требуется «", "").replace("»", "")[:32]
                                comp_c = v_data.get("company", "")
                                method = " [HH API]" if st.get("auto_applied") else ""
                                dt = st.get("applied_at", "")[:10]
                                lines.append(f"• <b>{title_c}</b> ({comp_c}){method} — {dt}")

                            resp = (
                                f"📋 <b>Последние отклики ({len(applied_list)} всего):</b>\n\n"
                                + "\n".join(lines) +
                                f"\n\n💼 <i>Подробная история доступна во вкладке 'Отклики' в Career Hub.</i>"
                            )
                            send_message(token, msg_chat_id, resp, reply_markup={
                                "inline_keyboard": [
                                    [{"text": "💼 Открыть Career Hub", "web_app": {"url": "https://jobs.shadowkek.space/"}}]
                                ]
                            })
                            continue

                        if msg_text in ["/start", "/help", "/jobs"] and msg_chat_id:
                            welcome_text = (
                                "👋 <b>Привет, Никита!</b>\n\n"
                                "Я твой персональный AI-ассистент по поиску вакансий и авто-откликам.\n\n"
                                "⚡ <b>Умная система фильтрации и авто-откликов:</b>\n"
                                "• 🔇 <b>Без спама:</b> Вакансии со средним скором (6-8/10) тихо сохраняются в Career Hub без лишних уведомлений.\n"
                                "• 🔔 <b>Только важное:</b> Мгновенные алерты приходят только для топ-вакансий (9-10/10), подтверждений откликов и приглашений от работодателей!\n"
                                "• 🤖 <b>Авто-отклики:</b> Автоматическая генерация писем через Groq AI и отправка через HeadHunter API при подключении токена.\n\n"
                                "🎯 <b>Целевой стек:</b>\n"
                                "  ☕ <i>Junior / Trainee Java Backend Developer (Java 21, Spring Boot 3, REST API, Docker, PostgreSQL)</i>\n"
                                "  🤖 <i>Java AI / Spring AI & LangChain4j</i>\n"
                                "  🛡️ <i>Remnawave / Xray VPN Core Dev</i>\n\n"
                                "⚙️ <b>Полезные команды:</b>\n"
                                "• <code>/status</code> — текущие настройки и статистика\n"
                                "• <code>/notify_score 9</code> — порог скора для уведомлений (6-10)\n"
                                "• <code>/auto_apply on/off</code> — режим авто-отклика\n"
                                "• <code>/set_hh_token &lt;токен&gt;</code> — подключить HeadHunter API\n"
                                "• <code>/hh_status</code> — статус подключения к HeadHunter\n"
                                "• <code>/digest</code> — сводка накопленных вакансий за 24ч\n"
                                "• <code>/applied</code> — история отправленных откликов\n"
                                "• <code>/login</code> — ссылка для входа на ПК"
                            )
                            app_markup = {
                                "inline_keyboard": [
                                    [
                                        {"text": "💼 Открыть Career Hub Mini App", "web_app": {"url": "https://jobs.shadowkek.space/"}}
                                    ],
                                    [
                                        {"text": "⚙️ Настройки бота", "callback_data": "btn_settings"},
                                        {"text": "🔑 Вход на ПК (/login)", "callback_data": "send_login_link"}
                                    ]
                                ]
                            }
                            send_message(token, msg_chat_id, welcome_text, reply_markup=app_markup)

        except Exception as e:
            logger.error(f"Ошибка в telegram_updates_worker: {e}")
            time.sleep(3)


# --- ОСНОВНОЙ ЦИКЛ МОНИТОРИНГА ВАКАНСИЙ (Мульти-источник) ---

def vacancy_monitor_worker():
    logger.info("Запуск фонового мониторинга вакансий со всех источников...")
    config = load_config()
    token = config["bot_token"]
    chat_id = config["chat_id"]
    groq_key = config["groq_api_key"]
    groq_model = config.get("groq_model", "openai/gpt-oss-120b")
    min_score = config.get("min_match_score", 6)
    interval = config.get("check_interval_seconds", 600)
    max_analyses = config.get("max_ai_analyses_per_cycle", 35)
    ai_delay = config.get("ai_delay_seconds", 2.8)

    seen_vacancies = load_seen_vacancies()
    logger.info(f"Загружено {len(seen_vacancies)} ранее сохраненных вакансий.")

    while True:
        logger.info("Начало цикла сбора вакансий со всех подключенных платформ...")
        config = load_config()
        token = config["bot_token"]
        chat_id = config["chat_id"]
        groq_key = config["groq_api_key"]
        groq_model = config.get("groq_model", "openai/gpt-oss-120b")
        min_score = config.get("min_match_score", 6)
        interval = config.get("check_interval_seconds", 600)
        sent_count = 0
        quiet_count = 0
        ai_analyses_count = 0

        # Проверка приглашений, ответов и очистка отказов на HeadHunter
        hh_token = config.get("hh_access_token", "").strip()
        hh_cookie = config.get("hh_web_cookie", "").strip()
        if hh_token or hh_cookie:
            poll_hh_negotiations_and_notify(token, chat_id, access_token=hh_token, cookie_str=hh_cookie)

        # Проверка необходимости отправки тихой сводки за 24 часа
        if config.get("daily_digest_enabled", True):
            check_and_send_scheduled_digest(token, chat_id, config)

        by_source = {
            "hh": [],
            "getmatch": [],
            "habr": [],
            "vseti": [],
            "remotive": [],
            "superjob": [],
            "linkedin": [],
            "telegram": [],
            "rabota": [],
            "trudvsem": []
        }

        # 1. Сбор с HeadHunter (hh.ru) - приоритетный джуниорский пул
        if config.get("enable_hh", True):
            for hh_item in config.get("hh_queries", []):
                q = hh_item.get("query", "")
                exp = hh_item.get("experience")
                sched = hh_item.get("schedule")
                vacs = fetch_hh_vacancies(q, experience=exp, schedule=sched)
                by_source["hh"].extend(vacs)

        # 2. Сбор с GetMatch
        if config.get("enable_getmatch", True):
            gm_vacs = fetch_getmatch_vacancies()
            by_source["getmatch"].extend(gm_vacs)

        # 3. Сбор с Хабр Карьеры
        if config.get("enable_habr", True):
            for query_url in config.get("habr_queries", []):
                vacs = fetch_habr_vacancies(query_url)
                by_source["habr"].extend(vacs)

        # 4. Сбор с СЕТИ (vseti.app)
        if config.get("enable_vseti", True):
            vseti_vacs = fetch_vseti_vacancies(config.get("vseti_pages", 6))
            by_source["vseti"].extend(vseti_vacs)

        # 5. Сбор с Remotive (Remote)
        if config.get("enable_remotive", True):
            rem_vacs = fetch_remotive_vacancies()
            by_source["remotive"].extend(rem_vacs)

        # 6. Сбор с SuperJob
        if config.get("enable_superjob", True) and config.get("superjob_secret_key"):
            sj_key = config.get("superjob_secret_key")
            for sj_q in config.get("superjob_queries", []):
                by_source["superjob"].extend(fetch_superjob_vacancies(sj_q, secret_key=sj_key))
        # 7. Сбор с LinkedIn (Public Guest Search)
        if config.get("enable_linkedin", True):
            for li_q in config.get("linkedin_queries", []):
                by_source["linkedin"].extend(fetch_linkedin_vacancies(li_q))
                time.sleep(2.0)
        # 8. Сбор с профильных Telegram-каналов (IT / Java / Junior)
        if config.get("enable_tg_channels", True):
            for tg_ch in config.get("tg_channels", []):
                by_source["telegram"].extend(fetch_tg_channel_vacancies(tg_ch))
        # 9. Сбор с Работа.ру (rabota.ru) - экосистема Сбера и IT РФ
        if config.get("enable_rabota", True):
            for rab_q in config.get("rabota_queries", [
                "стажер java",
                "junior java",
                "java разработчик",
                "java",
                "spring"
            ]):
                by_source["rabota"].extend(fetch_rabota_vacancies(rab_q))
        # 10. Сбор с Работа России (opendata.trudvsem.ru - Официальный портал РФ)
        if config.get("enable_trudvsem", False):
            for trud_q in config.get("trudvsem_queries", ["java", "spring"]):

                by_source["trudvsem"].extend(fetch_trudvsem_vacancies(trud_q))

        logger.info(f"Собрано сырых вакансий: HH={len(by_source['hh'])}, GetMatch={len(by_source['getmatch'])}, Habr={len(by_source['habr'])}, СЕТИ={len(by_source['vseti'])}, Remotive={len(by_source['remotive'])}, SuperJob={len(by_source['superjob'])}, LinkedIn={len(by_source['linkedin'])}, TG={len(by_source['telegram'])}, Работа={len(by_source['rabota'])}, ТрудВсем={len(by_source['trudvsem'])}")

        # Равномерное чередование источников (Round-Robin), чтобы ни один источник не голодал
        all_candidates = []
        max_len = max((len(lst) for lst in by_source.values()), default=0)
        for i in range(max_len):
            for src_name in ["hh", "getmatch", "habr", "vseti", "remotive", "superjob", "linkedin", "telegram", "rabota", "trudvsem"]:
                if i < len(by_source[src_name]):
                    all_candidates.append(by_source[src_name][i])

        logger.info(f"Сформирована очередь из {len(all_candidates)} вакансий с чередованием источников.")

        for v in all_candidates:
            v_id = v["id"]
            if v_id in seen_vacancies:
                continue

            # Быстрый отсев неподходящих (Middle+, Senior, QA, 1C, чужой стек, черный список компаний)
            if quick_prefilter_reject(v["title"], v.get("description", ""), v.get("company", "")):
                seen_add(seen_vacancies, v_id)
                delete_from_cache(v_id)
                continue

            if ai_analyses_count >= max_analyses:
                logger.info(f"Достигнут лимит анализов ({max_analyses}) за цикл.")
                break

            # Если вакансия из hh.ru и описание краткое, подтягиваем полное описание для качественного скоринга
            if v.get("source") == "hh" and len(v.get("description", "")) < 100:
                time.sleep(0.3)
                full_v = fetch_hh_vacancy_full(v.get("raw_id"))
                if full_v and full_v.get("description"):
                    v["description"] = full_v["description"]
                    if full_v.get("salary") and not v.get("salary"):
                        v["salary"] = full_v["salary"]
            # Если вакансия из LinkedIn и описание краткое, подтягиваем полное описание
            if v.get("source") == "linkedin" and len(v.get("description", "")) < 200:
                time.sleep(0.4)
                full_li = fetch_linkedin_vacancy_full(v.get("raw_id"))
                if full_li:
                    v["description"] = full_li
            # Если вакансия из Работа.ру и описание краткое, подтягиваем полное описание
            if v.get("source") == "rabota" and len(v.get("description", "")) < 200:
                time.sleep(0.4)
                full_rab = fetch_rabota_vacancy_full(v.get("raw_id"))
                if full_rab:
                    v["description"] = full_rab
            # НОВОЕ: SuperJob — подгрузка полного описания до AI (search API часто возвращает пустое поле)
            if v.get("source") == "superjob" and len(v.get("description", "")) < 250:
                time.sleep(0.3)
                full_sj = fetch_vacancy_by_id(v_id)
                if full_sj and full_sj.get("description") and len(full_sj["description"]) > len(v.get("description", "")):
                    v["description"] = full_sj["description"]
                    if full_sj.get("salary") and not v.get("salary"):
                        v["salary"] = full_sj["salary"]
            # НОВОЕ: ТрудВсем (Работа России) — подгрузка полного описания до AI
            if v.get("source") == "trudvsem" and len(v.get("description", "")) < 250:
                time.sleep(0.3)
                full_trud = fetch_vacancy_by_id(v_id)
                if full_trud and full_trud.get("description") and len(full_trud["description"]) > len(v.get("description", "")):
                    v["description"] = full_trud["description"]

            # Повторная быстрая проверка после подтягивания полного описания
            if quick_prefilter_reject(v["title"], v.get("description", ""), v.get("company", "")):
                seen_add(seen_vacancies, v_id)
                delete_from_cache(v_id)
                continue

            logger.info(f"Отправка на AI-анализ ({ai_analyses_count + 1}/{max_analyses}) [{v.get('source_name')}]: {v['title']}")
            analysis = analyze_vacancy_with_groq(
                groq_key,
                groq_model,
                v["title"],
                v["company"],
                v["description"],
                delay=ai_delay
            )
            ai_analyses_count += 1

            if not analysis:
                logger.warning(f"AI-анализ не вернул результат для '{v['title']}' (лимит 429 или сбой Groq). Вакансия сохранена для следующего цикла.")
                continue

            # Фиксируем ID в seen_vacancies ТОЛЬКО после успешного завершения AI-анализа
            seen_add(seen_vacancies, v_id)

            score = analysis.get("match_score", 0)
            is_suitable = analysis.get("suitable", False)
            v["role"] = analysis.get("role", "java")
            v["match_score"] = score
            v["analysis"] = analysis

            logger.info(f"AI Вердикт для '{v['title']}': suitable={is_suitable}, score={score}, role={v['role']}")

            # В кэш сохраняем ТОЛЬКО подходящие вакансии
            if is_suitable and score >= min_score:
                save_to_cache(v)
                auto_applied, notified = process_vacancy_auto_apply_and_notify(
                    v=v,
                    analysis=analysis,
                    config=config,
                    token=token,
                    chat_id=chat_id,
                    groq_key=groq_key,
                    groq_model=groq_model
                )
                if notified:
                    sent_count += 1
                    time.sleep(2)
                else:
                    quiet_count += 1
            else:
                delete_from_cache(v_id)

        save_seen_vacancies(seen_vacancies)
        logger.info(f"Цикл завершен. AI анализов: {ai_analyses_count}, отправлено важных в TG: {sent_count}, сохранено без спама: {quiet_count}. Пауза {interval} сек.")
        time.sleep(interval)


# --- TELEGRAM MINI APP HTTP REST API & СТАТИКА ---

def normalize_vacancy_for_api(v, statuses):
    v_id = str(v.get("id", ""))
    raw_title = v.get("title", "")
    title = raw_title.replace("Требуется «", "").replace("»", "").strip()
    company = v.get("company", "Не указана")
    link = v.get("link", "#")
    salary = v.get("salary", "")
    desc = v.get("description", "")
    pub_date = v.get("pub_date", "")

    # Исключение сомнительных ресурсов: Работа России (trudvsem)
    if v.get("source") == "trudvsem" or v_id.startswith("trud_"):
        return None

    # Жесткий отсев middle, senior, нерелевантного стека и черного списка компаний
    if quick_prefilter_reject(title, desc, company):
        return None


    analysis = v.get("analysis")
    if analysis and isinstance(analysis, dict):
        if analysis.get("suitable") is False or analysis.get("match_score", 0) < 5:
            return None

    source = v.get("source")
    source_name = v.get("source_name")
    if not source:
        if v_id.startswith("hh_"):
            source = "hh"
            source_name = "HeadHunter"
        elif v_id.startswith("habr_") or v_id.isdigit():
            source = "habr"
            source_name = "Хабр Карьера"
        elif v_id.startswith("getmatch_") or v_id.startswith("gm_"):
            source = "getmatch"
            source_name = "GetMatch"
        elif v_id.startswith("vseti_"):
            source = "vseti"
            source_name = "СЕТИ"
        elif v_id.startswith("remotive_"):
            source = "remotive"
            source_name = "Remotive"
        elif v_id.startswith("sj_"):
            source = "superjob"
            source_name = "SuperJob"
        elif v_id.startswith("li_"):
            source = "linkedin"
            source_name = "LinkedIn"
        elif v_id.startswith("tg_"):
            source = "telegram"
            source_name = v.get("source_name", "Telegram")
        else:
            source = "other"
            source_name = "Площадка"

    if source == "vseti" and not source_name:
        source_name = "СЕТИ"

    # Роль и проверка валидности: СТРОГО разработка ПО (Java, Java AI, VPN Dev, Backend)
    full_text = f"{title} {desc}".lower()
    role = v.get("role")
    if not role and v.get("analysis") and isinstance(v.get("analysis"), dict):
        role = v.get("analysis").get("role")

    # Исключаем любые не-разработческие роли
    if role in ["aqa_java", "ai_trainer", "support_l2", "qa", "support"]:
        return None

    if not role:
        if any(k in full_text for k in ["remnawave", "xray", "vless", "sing-box"]) or ("vpn" in full_text and any(d in full_text for d in ["разработчик", "developer", "backend", "программист"])):
            role = "vpn_dev"
        elif any(k in full_text for k in ["spring ai", "spring-ai", "langchain4j", "langchain-4j"]) or (any(k in full_text for k in ["llm", "ai agent", "ai-агент", "ai агент", "genai", "rag"]) and any(j in full_text for j in ["java", "jvm", "spring"])):
            role = "java_ai"
        elif any(k in title.lower() for k in ["java", "kotlin", "spring"]) or (any(k in full_text for k in ["java", "kotlin"]) and any(k in full_text for k in ["developer", "разработчик", "backend", "junior", "стажер"])):
            role = "java"
        elif any(k in title.lower() for k in ["backend", "бэкенд", "разработчик", "developer"]):
            role = "backend"
        else:
            return None

    match_score = v.get("match_score")
    analysis = v.get("analysis")
    if analysis and isinstance(analysis, dict):
        if not match_score:
            match_score = analysis.get("match_score", 7)
    else:
        if not match_score:
            match_score = 8 if ("junior" in title.lower() or "стажер" in title.lower()) else 7
        role_labels = {
            "vpn_dev": "VPN Dev (Xray/Protocols)",
            "java_ai": "Java AI & LLM Integration",
            "backend": "Junior Backend Developer",
            "python_dev": "Backend Developer",
            "java": "Java Backend"
        }
        label = role_labels.get(role, "IT")
        analysis = {
            "match_score": match_score,
            "suitable": True,
            "role": role,
            "verdict": f"Релевантная вакансия по направлению {label}.",
            "education_warning": None,
            "recommendation": "Сделайте акцент на практических проектах и самостоятельном стеке."
        }

    status_info = statuses.get(v_id, {})
    current_status = status_info.get("status", "inbox")

    stack_tags = extract_tech_stack(desc, title or raw_title)
    structured_desc = format_structured_description(desc, source)

    return {
        "id": v_id,
        "title": title or raw_title,
        "company": company,
        "location": v.get("location", ""),
        "link": link,
        "salary": salary,
        "description": desc,
        "structured_description": structured_desc,
        "stack": stack_tags,
        "pub_date": pub_date,
        "source": source,
        "source_name": source_name,
        "role": role,
        "match_score": match_score,
        "analysis": analysis,
        "status": current_status,
        "applied_at": status_info.get("applied_at"),
        "cover_letter": status_info.get("cover_letter") or v.get("cover_letter"),
        "auto_applied": status_info.get("auto_applied", False),
        "auto_prepared": status_info.get("auto_prepared", False)
    }


class MiniAppRequestHandler(BaseHTTPRequestHandler):
    def _send_cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")

    def do_OPTIONS(self):
        self.send_response(204)
        self._send_cors_headers()
        self.end_headers()

    def do_HEAD(self):
        self.send_response(200)
        self._send_cors_headers()
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()

    def get_auth_token(self):
        auth_header = self.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            return auth_header[7:].strip()
        cookie_header = self.headers.get("Cookie", "")
        if cookie_header:
            for item in cookie_header.split(";"):
                parts = item.strip().split("=", 1)
                if len(parts) == 2 and parts[0] in ["career_hub_token", "career_hub_session"]:
                    return parts[1]
        return None

    def is_authenticated(self):
        token = self.get_auth_token()
        session = verify_session_token(token)
        if not session:
            return None
        config = load_config()
        allowed_id = config.get("allowed_telegram_id") or config.get("chat_id") or 1139872910
        if session.get("user_id") == allowed_id:
            return session
        return None

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        if path == "":
            path = "/"

        if path in ["/", "/index.html"]:
            html_file = MINIAPP_HTML_PATH
            if not os.path.exists(html_file):
                html_file = os.path.join(os.path.dirname(__file__), "miniapp_index.html")
            if not os.path.exists(html_file):
                html_file = "/app/miniapp_index.html"

            if os.path.exists(html_file):
                try:
                    with open(html_file, "r", encoding="utf-8") as f:
                        content = f.read().encode("utf-8")
                    self.send_response(200)
                    self._send_cors_headers()
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(content)))
                    self.end_headers()
                    self.wfile.write(content)
                    return
                except Exception as e:
                    logger.error(f"Ошибка отдачи Mini App HTML: {e}")

            self.send_response(404)
            self._send_cors_headers()
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"Mini App HTML file not found.")
            return

        elif path == "/api/auth/me":
            session = self.is_authenticated()
            if not session:
                self.send_response(401)
                self._send_cors_headers()
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": False, "error": "Unauthorized"}).encode("utf-8"))
                return

            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps({
                "ok": True,
                "user_id": session.get("user_id"),
                "device": session.get("device"),
                "expires_at": session.get("expires_at")
            }).encode("utf-8"))
            return

        elif path == "/api/auth/check_login_session":
            parsed_qs = parse_qs(parsed.query)
            s_id = parsed_qs.get("session_id", [""])[0]
            if not s_id:
                self.send_response(400)
                self._send_cors_headers()
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": False, "error": "Missing session_id"}).encode("utf-8"))
                return

            with pending_logins_lock:
                p = pending_logins.get(s_id)
                if not p:
                    resp_data = {"ok": False, "status": "expired"}
                elif p.get("approved"):
                    token = p.get("token")
                    pending_logins.pop(s_id, None)
                    resp_data = {"ok": True, "status": "approved", "token": token}
                else:
                    resp_data = {"ok": True, "status": "pending"}

            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps(resp_data).encode("utf-8"))
            return

        elif path == "/api/vacancies":
            session = self.is_authenticated()
            if not session:
                self.send_response(401)
                self._send_cors_headers()
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": False, "error": "Unauthorized"}).encode("utf-8"))
                return

            try:
                cache = load_cache()
                statuses = load_statuses()

                vacancies_list = []
                stats = {"inbox": 0, "saved": 0, "applied": 0, "interview": 0, "archived": 0}

                # Отдаем в обратном порядке (новые сверху), пропуская отсеянные вакансии
                for v_id, v in reversed(list(cache.items())):
                    norm_v = normalize_vacancy_for_api(v, statuses)
                    if not norm_v:
                        continue
                    vacancies_list.append(norm_v)
                    st = norm_v["status"]
                    if st in stats:
                        stats[st] += 1
                    else:
                        stats["inbox"] += 1

                response_data = {
                    "ok": True,
                    "total": len(vacancies_list),
                    "stats": stats,
                    "statuses": statuses,
                    "vacancies": vacancies_list
                }

                content = json.dumps(response_data, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self._send_cors_headers()
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
                return
            except Exception as e:
                logger.error(f"Ошибка API /api/vacancies: {e}")
                self.send_response(500)
                self._send_cors_headers()
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": False, "error": str(e)}).encode("utf-8"))
                return

        elif path == "/api/settings":
            session = self.is_authenticated()
            if not session:
                self.send_response(401)
                self._send_cors_headers()
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": False, "error": "Unauthorized"}).encode("utf-8"))
                return

            cfg = load_config()
            cache = load_cache()
            statuses = load_statuses()
            applied_count = sum(1 for s in statuses.values() if s.get("status") == "applied")

            hh_cookie = (cfg.get("hh_web_cookie") or "").strip()
            hh_cookie_masked = ""
            hh_cookie_valid = False
            hh_user_name = None

            if hh_cookie:
                parts = []
                for p in hh_cookie.split(";"):
                    p = p.strip()
                    if "=" in p:
                        k, val = p.split("=", 1)
                        if k in ["hhtoken", "_xsrf", "crypted_hhuid"]:
                            parts.append(f"{k}={val[:4]}...{val[-4:] if len(val) > 8 else ''}")
                hh_cookie_masked = "; ".join(parts) if parts else "Куки сохранены"
                hh_cookie_info = check_hh_web_cookie(hh_cookie)
                if hh_cookie_info and hh_cookie_info.get("ok"):
                    hh_cookie_valid = True
                    hh_user_name = hh_cookie_info.get("name", "Никита Фролов")

            resp_data = {
                "ok": True,
                "auto_apply_enabled": cfg.get("auto_apply_enabled", True),
                "auto_apply_min_score": cfg.get("auto_apply_min_score", 7),
                "notify_min_score": cfg.get("notify_min_score", 9),
                "min_match_score": cfg.get("min_match_score", 6),
                "hh_connected": bool(cfg.get("hh_access_token", "").strip()) or hh_cookie_valid,
                "hh_web_cookie_configured": bool(hh_cookie),
                "hh_web_cookie_valid": hh_cookie_valid,
                "hh_web_cookie_masked": hh_cookie_masked,
                "hh_user_name": hh_user_name,
                "total_vacancies": len(cache),
                "total_applied": applied_count
            }
            content = json.dumps(resp_data, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
            return

        elif path == "/health":
            content = json.dumps({"status": "ok", "app": "Career Hub TMA"}).encode("utf-8")
            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
            return

        else:
            self.send_response(404)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": False, "error": "Not Found"}).encode("utf-8"))

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        content_length = int(self.headers.get("Content-Length", 0))
        post_body = self.rfile.read(content_length)

        try:
            data = json.loads(post_body.decode("utf-8")) if post_body else {}
        except Exception:
            data = {}

        if path == "/api/auth/telegram_webapp":
            init_data = data.get("initData", "")
            config = load_config()
            bot_token = config["bot_token"]
            allowed_id = config.get("allowed_telegram_id") or config.get("chat_id") or 1139872910

            user_info = validate_telegram_init_data(init_data, bot_token)
            if not user_info:
                self.send_response(401)
                self._send_cors_headers()
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": False, "error": "Недействительные данные Telegram WebApp"}).encode("utf-8"))
                return

            uid = user_info.get("id")
            if uid != allowed_id:
                logger.warning(f"Попытка входа с чужим Telegram ID: {uid} (ожидался {allowed_id})")
                self.send_response(403)
                self._send_cors_headers()
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": False, "error": "Доступ разрешён только владельцу бота."}).encode("utf-8"))
                return

            token, exp = create_user_session(uid, device="Telegram Mini App", days=30)
            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Set-Cookie", f"career_hub_token={token}; Max-Age=2592000; Path=/; SameSite=Lax; Secure")
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps({
                "ok": True,
                "token": token,
                "user": user_info,
                "expires_at": exp
            }).encode("utf-8"))
            return

        elif path == "/api/auth/exchange_token":
            one_time_token = data.get("token", "")
            uid = consume_one_time_token(one_time_token)
            config = load_config()
            allowed_id = config.get("allowed_telegram_id") or config.get("chat_id") or 1139872910

            if not uid or uid != allowed_id:
                self.send_response(401)
                self._send_cors_headers()
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": False, "error": "Ссылка для входа устарела или недействительна"}).encode("utf-8"))
                return

            perm_token, exp = create_user_session(uid, device="Browser Magic Link", days=30)
            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Set-Cookie", f"career_hub_token={perm_token}; Max-Age=2592000; Path=/; SameSite=Lax; Secure")
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps({
                "ok": True,
                "token": perm_token,
                "expires_at": exp
            }).encode("utf-8"))
            return

        elif path == "/api/auth/request_login_session":
            s_id = secrets.token_hex(16)
            code = f"{secrets.randbelow(900000) + 100000}"
            with pending_logins_lock:
                now = time.time()
                to_del = [k for k, v in pending_logins.items() if now - v.get("created_at", 0) > 900]
                for k in to_del:
                    del pending_logins[k]

                pending_logins[s_id] = {
                    "code": code,
                    "created_at": now,
                    "approved": False,
                    "token": None
                }

            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps({
                "ok": True,
                "session_id": s_id,
                "code": code,
                "bot_username": "asistant7626_bot",
                "bot_link": f"https://t.me/asistant7626_bot?start=login_{s_id}"
            }).encode("utf-8"))
            return

        elif path == "/api/auth/logout":
            token = self.get_auth_token()
            revoke_session_token(token)
            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Set-Cookie", "career_hub_token=; Max-Age=0; Path=/; SameSite=Lax")
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": True}).encode("utf-8"))
            return

        # Все последующие API запросы требуют валидной сессии
        session = self.is_authenticated()
        if not session:
            self.send_response(401)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": False, "error": "Unauthorized"}).encode("utf-8"))
            return

        if path == "/api/status":
            v_id = data.get("id")
            status = data.get("status")
            extra_cover = data.get("cover_letter")
            if not v_id or not status:
                self.send_response(400)
                self._send_cors_headers()
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": False, "error": "Missing id or status"}).encode("utf-8"))
                return

            updated = update_vacancy_status(v_id, status, cover_letter=extra_cover)
            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": True, "data": updated}).encode("utf-8"))
            return

        elif path == "/api/auto_apply":
            v_id = data.get("id")
            if not v_id:
                self.send_response(400)
                self._send_cors_headers()
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": False, "error": "Missing id"}).encode("utf-8"))
                return

            vacancy = fetch_vacancy_by_id(v_id)
            if not vacancy:
                self.send_response(404)
                self._send_cors_headers()
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": False, "error": "Вакансия не найдена"}).encode("utf-8"))
                return

            raw_id = vacancy.get("raw_id") or (str(v_id).replace("hh_", "") if str(v_id).startswith("hh_") else str(v_id))

            config = load_config()
            groq_key = config["groq_api_key"]
            groq_model = config.get("groq_model", "openai/gpt-oss-120b")
            letter = data.get("cover_letter") or generate_cover_letter(groq_key, groq_model, vacancy)

            hh_tok = config.get("hh_access_token", "").strip().strip('"').strip("'")
            hh_res_id = config.get("hh_resume_id", "").strip()
            hh_cookie = config.get("hh_web_cookie", "").strip()
            hh_res_hash = config.get("hh_resume_hash", "b81ca639ff1137b70d0039ed1f57755649766f").strip()
            applied_method = "manual"

            if vacancy.get("source") == "hh":
                if hh_cookie:
                    web_res = apply_hh_vacancy_web(raw_id, hh_res_hash, letter, hh_cookie)
                    if web_res.get("ok"):
                        applied_method = "hh_web"
                    else:
                        api_error = web_res.get("detail", "Ошибка отклика через веб-сессию")
                        self.send_response(400)
                        self._send_cors_headers()
                        self.send_header("Content-Type", "application/json; charset=utf-8")
                        self.end_headers()
                        self.wfile.write(json.dumps({
                            "ok": False,
                            "error": api_error,
                            "status": web_res.get("status"),
                            "cover_letter": letter
                        }, ensure_ascii=False).encode("utf-8"))
                        return
                elif hh_tok:
                    if not hh_res_id:
                        res_obj = auto_select_hh_resume(hh_tok)
                        if res_obj:
                            hh_res_id = res_obj.get("id", "")
                            cfg_save = load_config()
                            cfg_save["hh_resume_id"] = hh_resume_id
                            save_config(cfg_save)

                    if hh_res_id:
                        api_res = apply_hh_vacancy_api(raw_id, hh_res_id, letter, hh_tok)
                        if api_res.get("ok"):
                            applied_method = "hh_api"
                        else:
                            api_error = api_res.get("detail", "Ошибка отклика через API")
                            self.send_response(400)
                            self._send_cors_headers()
                            self.send_header("Content-Type", "application/json; charset=utf-8")
                            self.end_headers()
                            self.wfile.write(json.dumps({
                                "ok": False,
                                "error": api_error,
                                "status": api_res.get("status"),
                                "cover_letter": letter
                            }, ensure_ascii=False).encode("utf-8"))
                            return

            updated = update_vacancy_status(v_id, "applied", cover_letter=letter, auto_applied=(applied_method in ["hh_api", "hh_web"]), method=applied_method)
            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": True, "data": updated, "cover_letter": letter, "applied_method": applied_method}, ensure_ascii=False).encode("utf-8"))
            return

        elif path == "/api/settings":
            cfg = load_config()
            cookie_updated = False
            cookie_valid = None
            cookie_msg = None

            if "hh_web_cookie" in data:
                new_cookie = (data.get("hh_web_cookie") or "").strip()
                if new_cookie:
                    check_res = check_hh_web_cookie(new_cookie)
                    if check_res and check_res.get("ok"):
                        cfg["hh_web_cookie"] = new_cookie
                        if check_res.get("resumes"):
                            cfg["hh_resume_hash"] = check_res["resumes"][0]
                        cookie_updated = True
                        cookie_valid = True
                        cookie_msg = f"Сессия HeadHunter подтверждена ({check_res.get('name', 'Пользователь')})!"
                    else:
                        cookie_valid = False
                        cookie_msg = "Введённые куки не прошли проверку на hh.ru (возможно, истекли или не содержат hhtoken/_xsrf)."
                else:
                    cfg["hh_web_cookie"] = ""
                    cookie_updated = True
                    cookie_valid = False
                    cookie_msg = "Куки HeadHunter удалены."

            if "auto_apply_enabled" in data:
                cfg["auto_apply_enabled"] = bool(data["auto_apply_enabled"])
            if "auto_apply_min_score" in data:
                try:
                    cfg["auto_apply_min_score"] = int(data["auto_apply_min_score"])
                except Exception:
                    pass
            if "notify_min_score" in data:
                try:
                    cfg["notify_min_score"] = int(data["notify_min_score"])
                except Exception:
                    pass

            save_config(cfg)
            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps({
                "ok": True,
                "cookie_valid": cookie_valid,
                "cookie_msg": cookie_msg,
                "settings": {
                    "auto_apply_enabled": cfg.get("auto_apply_enabled", True),
                    "auto_apply_min_score": cfg.get("auto_apply_min_score", 7),
                    "notify_min_score": cfg.get("notify_min_score", 9),
                    "hh_web_cookie_configured": bool((cfg.get("hh_web_cookie") or "").strip()),
                    "hh_web_cookie_valid": bool(cookie_valid if cookie_valid is not None else cfg.get("hh_web_cookie"))
                }
            }, ensure_ascii=False).encode("utf-8"))
            return

        elif path == "/api/check_cookie":
            test_cookie = (data.get("cookie") or "").strip()
            if not test_cookie:
                self.send_response(400)
                self._send_cors_headers()
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": False, "error": "Пустая строка cookie"}, ensure_ascii=False).encode("utf-8"))
                return

            res = check_hh_web_cookie(test_cookie)
            if res and res.get("ok"):
                self.send_response(200)
                self._send_cors_headers()
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({
                    "ok": True,
                    "valid": True,
                    "name": res.get("name", "Никита Фролов"),
                    "host": res.get("host", "hh.ru"),
                    "resumes_count": len(res.get("resumes", []))
                }, ensure_ascii=False).encode("utf-8"))
            else:
                self.send_response(200)
                self._send_cors_headers()
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({
                    "ok": True,
                    "valid": False,
                    "error": "Сессия не авторизована на hh.ru (проверьте наличие hhtoken и _xsrf)"
                }, ensure_ascii=False).encode("utf-8"))
            return

        elif path == "/api/cover_letter":
            v_id = data.get("id")
            if not v_id:
                self.send_response(400)
                self._send_cors_headers()
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": False, "error": "Missing id"}).encode("utf-8"))
                return

            vacancy = fetch_vacancy_by_id(v_id)
            if not vacancy:
                self.send_response(404)
                self._send_cors_headers()
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": False, "error": "Вакансия не найдена"}).encode("utf-8"))
                return

            config = load_config()
            groq_key = config["groq_api_key"]
            groq_model = config.get("groq_model", "openai/gpt-oss-120b")

            letter = generate_cover_letter(groq_key, groq_model, vacancy)
            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": True, "cover_letter": letter}).encode("utf-8"))
            return

        else:
            self.send_response(404)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": False, "error": "Not Found"}).encode("utf-8"))

    def log_message(self, format, *args):
        # Отключаем шумные access-логи в stdout
        pass


def http_server_worker(host="0.0.0.0", port=8088):
    logger.info(f"Запуск HTTP сервера Mini App на {host}:{port}...")
    server = ThreadingHTTPServer((host, port), MiniAppRequestHandler)
    try:
        server.serve_forever()
    except Exception as e:
        logger.error(f"Ошибка в HTTP сервере: {e}")


def run():
    logger.info("Старт Мульти-платформенного сервиса Career AI Bot...")
    
    # 1. Фоновый слушатель Telegram
    t_tg = threading.Thread(target=telegram_updates_worker, daemon=True)
    t_tg.start()

    # 2. Фоновый HTTP сервер для Telegram Mini App
    t_http = threading.Thread(target=http_server_worker, daemon=True)
    t_http.start()

    # 3. Основной цикл мониторинга вакансий
    vacancy_monitor_worker()


if __name__ == "__main__":
    run()
