"""
Rabota.ru (rabota.ru) and TrudVsem (trudvsem.ru / Работа России) Adapters.
"""

import requests
from career_hub.controllers.auto_apply.base import _get_bot_ref, logger


def check_rabota_session(cookie_or_token: str = "") -> dict:
    """Проверяет сессию соискателя на Работа.ру."""
    val = (cookie_or_token or "").strip()
    if not val:
        return {"ok": False, "detail": "Сессионные данные Работа.ру не указаны"}

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
        url = "https://api.rabota.ru/v1/users/current"
        resp = req_mod.get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            name = "Соискатель Работа.ру"
            if isinstance(data, dict):
                name = data.get("first_name") or data.get("name") or name
            return {"ok": True, "detail": "Сессия Работа.ру активна", "user_name": name}
        elif resp.status_code in [401, 403]:
            return {"ok": False, "detail": "Сессия Работа.ру устарела или отклонена"}
        return {"ok": False, "detail": f"Ответ Работа.ру HTTP {resp.status_code}"}
    except Exception as e:
        logger.error(f"Ошибка проверки сессии Работа.ру: {e}")
        return {"ok": False, "detail": str(e)}


def apply_rabota_vacancy(raw_id: str, cover_letter: str, cookie_or_token: str = "") -> dict:
    """Отправляет отклик на вакансию Работа.ру."""
    clean_id = str(raw_id).replace("rabota_", "").strip()
    val = (cookie_or_token or "").strip()
    if not val:
        return {"ok": False, "status": "no_credentials", "detail": "Не настроены куки или токен Работа.ру"}

    url = "https://api.rabota.ru/v1/resumes/responses"
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
        "vacancy_id": clean_id,
        "letter": (cover_letter or "").strip(),
    }
    req_mod = _get_bot_ref("requests", requests)
    try:
        resp = req_mod.post(url, headers=headers, json=payload, timeout=12)
        if resp.status_code in [200, 201]:
            return {"ok": True, "status": "applied", "method": "rabota_api", "detail": "Отклик успешно зарегистрирован на Работа.ру"}
        resp_lower = resp.text.lower()
        if "already" in resp_lower or "уже откликнулись" in resp_lower:
            return {"ok": True, "status": "already_applied", "method": "rabota_api", "detail": "Ранее уже был подан отклик на Работа.ру"}
        if resp.status_code in [401, 403]:
            return {"ok": False, "status": "cookie_expired", "detail": "Сессия Работа.ру устарела"}
        return {"ok": False, "status": "error", "detail": f"HTTP {resp.status_code}: {resp.text[:120]}"}
    except Exception as e:
        logger.error(f"Исключение при отклике на Работа.ру #{clean_id}: {e}")
        return {"ok": False, "status": "error", "detail": str(e)}


def apply_trudvsem_vacancy(raw_id: str, cover_letter: str, direct_url: str = None) -> dict:
    """Формирует отклик для портала 'Работа России' (trudvsem.ru)."""
    clean_id = str(raw_id).replace("tv_", "").replace("trudvsem_", "").strip()
    target_url = direct_url or f"https://trudvsem.ru/vacancy/card/{clean_id}"
    return {
        "ok": True,
        "status": "direct_required",
        "method": "trudvsem_direct",
        "detail": "Требуется авторизация через Госуслуги (ЕСИА). Письмо скопировано для отправки на сайте.",
        "redirect_url": target_url,
        "cover_letter": cover_letter,
    }
