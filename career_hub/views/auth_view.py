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
import urllib.parse
import requests
from career_hub.models.config import load_config
from career_hub.models.storage import load_sessions, save_sessions, sessions_lock

logger = logging.getLogger("career_hub.views.auth")

one_time_tokens = {}
_BOT_USERNAME_CACHE = None


def get_bot_username(bot_token: str = None) -> str:
    """Динамически запрашивает username бота через Telegram getMe API и кэширует его."""
    global _BOT_USERNAME_CACHE
    if _BOT_USERNAME_CACHE:
        return _BOT_USERNAME_CACHE
    if not bot_token:
        try:
            bot_token = load_config().get("bot_token", "")
        except Exception:
            bot_token = ""
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
    return os.environ.get("BOT_USERNAME", "asistant7626_bot")


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
    """Генерирует одноразовый токен авторизации (TTL 10 минут)."""
    token = secrets.token_urlsafe(24)
    one_time_tokens[token] = {
        "user_id": int(user_id),
        "expires_at": time.time() + 600
    }
    return token


def consume_one_time_token(token: str) -> int | None:
    """Проверяет и погашает одноразовый токен."""
    if not token or token not in one_time_tokens:
        return None
    data = one_time_tokens.pop(token)
    if data.get("expires_at", 0) < time.time():
        return None
    return data.get("user_id")


def validate_telegram_init_data(init_data: str, bot_token: str) -> dict | None:
    """
    Проверяет цифровую подпись initData от Telegram WebApp по алгоритму HMAC-SHA256.
    """
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
