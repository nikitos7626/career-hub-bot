"""
GetMatch (getmatch.ru) API & Session Adapter.
"""

import requests
from career_hub.controllers.auto_apply.base import _get_bot_ref, logger


def check_getmatch_session(token_or_cookie: str = "") -> dict:
    """Проверяет валидность токена авторизации или сессионных куки на GetMatch."""
    val = (token_or_cookie or "").strip()
    if not val:
        return {"ok": False, "detail": "GetMatch токен или куки не указаны"}

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
        "Accept": "application/json",
    }
    if "=" in val and ";" in val:
        headers["Cookie"] = val
    else:
        headers["Authorization"] = f"Bearer {val}"

    req_mod = _get_bot_ref("requests", requests)
    try:
        url = "https://getmatch.ru/api/v1/profile"
        resp = req_mod.get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            name = "Пользователь GetMatch"
            if isinstance(data, dict):
                name = data.get("name") or data.get("first_name") or name
            return {"ok": True, "detail": "Сессия GetMatch активна", "user_name": name}
        elif resp.status_code in [401, 403]:
            return {"ok": False, "detail": "Сессия GetMatch истекла или токен недействителен"}
        return {"ok": False, "detail": f"Ответ GetMatch HTTP {resp.status_code}"}
    except Exception as e:
        logger.error(f"Ошибка проверки сессии GetMatch: {e}")
        return {"ok": False, "detail": str(e)}


def apply_getmatch_vacancy(raw_id: str, cover_letter: str, token_or_cookie: str = "") -> dict:
    """Отправляет отклик на вакансию GetMatch."""
    clean_id = str(raw_id).replace("gm_", "").strip()
    val = (token_or_cookie or "").strip()
    if not val:
        return {"ok": False, "status": "no_credentials", "detail": "Не настроен токен GetMatch"}

    url = f"https://getmatch.ru/api/v1/vacancies/{clean_id}/applications"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    if "=" in val and ";" in val:
        headers["Cookie"] = val
    else:
        headers["Authorization"] = f"Bearer {val}"

    payload = {
        "cover_letter": (cover_letter or "").strip(),
        "comment": (cover_letter or "").strip(),
    }
    req_mod = _get_bot_ref("requests", requests)
    try:
        resp = req_mod.post(url, headers=headers, json=payload, timeout=12)
        if resp.status_code in [200, 201]:
            return {"ok": True, "status": "applied", "method": "getmatch_api", "detail": "Отклик успешно зарегистрирован на GetMatch"}

        resp_lower = resp.text.lower()
        if resp.status_code == 409 or "already" in resp_lower or "уже откликнулись" in resp_lower:
            return {"ok": True, "status": "already_applied", "method": "getmatch_api", "detail": "Ранее уже был подан отклик на GetMatch"}

        if resp.status_code in [401, 403]:
            return {"ok": False, "status": "cookie_expired", "detail": "Сессия GetMatch устарела или отклонена"}

        return {"ok": False, "status": "error", "detail": f"HTTP {resp.status_code}: {resp.text[:120]}"}
    except Exception as e:
        logger.error(f"Исключение при отклике на GetMatch #{clean_id}: {e}")
        return {"ok": False, "status": "error", "detail": str(e)}
