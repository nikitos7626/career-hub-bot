"""
Authentication and Session View Layer for Career Hub.
Handles Telegram WebApp HMAC validation, desktop login requests, and session tokens.
"""

import os
import sys
import json
import time
import hmac
import secrets
import hashlib
import logging
import threading
import urllib.parse
import requests
from career_hub.models.config import load_config
from career_hub.models.storage import load_sessions, save_sessions, sessions_lock

logger = logging.getLogger("career_hub.views.auth")

one_time_tokens = {}
one_time_tokens_lock = threading.RLock()
_BOT_USERNAME_CACHE = None


def get_bot_username(bot_token: str = None) -> str:
    """Динамически запрашивает username бота через Telegram getMe API и кэширует его."""
    global _BOT_USERNAME_CACHE
    if _BOT_USERNAME_CACHE:
        return _BOT_USERNAME_CACHE
    cfg = {}
    try:
        cfg = load_config()
    except Exception:
        pass
    if not bot_token:
        bot_token = cfg.get("bot_token", "")
    if bot_token:
        try:
            bot_mod = sys.modules.get("bot")
            req_mod = getattr(bot_mod, "requests", requests) if bot_mod else requests
            r = req_mod.get(f"https://api.telegram.org/bot{bot_token}/getMe", timeout=5)
            if r.status_code == 200 and r.json().get("ok"):
                uname = r.json().get("result", {}).get("username")
                if uname:
                    _BOT_USERNAME_CACHE = uname
                    return uname
        except Exception as e:
            logger.debug(f"Could not fetch bot username via getMe: {e}")
    return os.environ.get("BOT_USERNAME") or cfg.get("bot_username", "") or ""


def create_user_session(user_id: int, device: str = "Browser", days: int = 30) -> tuple[str, int]:
    """Создает защищенный session token для браузера на указанное количество дней."""
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
        now = int(time.time())
        sessions = {t: data for t, data in sessions.items() if data.get("expires_at", 0) > now}
        save_sessions(sessions)
        return token, expires_at


def verify_session_token(token: str) -> dict | None:
    """Проверяет валидность session токена."""
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


def revoke_session_token(token: str):
    """Отзывает session токен при выходе."""
    if not token:
        return
    with sessions_lock:
        sessions = load_sessions()
        if token in sessions:
            del sessions[token]
            save_sessions(sessions)


def create_one_time_token(user_id: int) -> str:
    """Генерирует одноразовый токен авторизации (TTL 10 минут) с автоматической очисткой устаревших."""
    token = secrets.token_urlsafe(24)
    with one_time_tokens_lock:
        now = time.time()
        # Очистка устаревших токенов
        expired = [k for k, v in one_time_tokens.items() if v.get("expires_at", 0) < now]
        for k in expired:
            one_time_tokens.pop(k, None)
        # Ограничение размера пула для предотвращения утечки памяти
        if len(one_time_tokens) >= 50:
            oldest = sorted(one_time_tokens.items(), key=lambda x: x[1].get("expires_at", 0))[:25]
            for k, _ in oldest:
                one_time_tokens.pop(k, None)

        one_time_tokens[token] = {
            "user_id": int(user_id),
            "expires_at": now + 600
        }
    return token


def consume_one_time_token(token: str) -> int | None:
    """Проверяет и атомарно погашает одноразовый токен."""
    if not token:
        return None
    with one_time_tokens_lock:
        data = one_time_tokens.pop(token, None)
        if not data:
            return None
        if data.get("expires_at", 0) < time.time():
            return None
        return data.get("user_id")


def validate_telegram_init_data(init_data: str, bot_token: str) -> dict | None:
    """
    Проверяет цифровую подпись initData от Telegram WebApp по алгоритму HMAC-SHA256
    с защитой от Replay-атак (проверка auth_date).
    """
    if not init_data:
        return None
    try:
        parsed = dict(urllib.parse.parse_qsl(init_data, keep_blank_values=True))
        auth_hash = parsed.pop("hash", None)
        if not auth_hash:
            return None

        # Защита от Replay-атак: проверка времени подписи Telegram (максимум 24 часа)
        auth_date_str = parsed.get("auth_date")
        if not auth_date_str:
            logger.warning("Telegram initData отклонена: отсутствует auth_date")
            return None
        try:
            auth_date = int(auth_date_str)
            if abs(time.time() - auth_date) > 86400:
                logger.warning(f"Telegram initData отклонена: срок действия подписи истёк (auth_date={auth_date})")
                return None
        except (ValueError, TypeError):
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
