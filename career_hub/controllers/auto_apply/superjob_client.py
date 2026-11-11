"""
SuperJob (superjob.ru) API & Session Adapter.
"""

import requests
from career_hub.controllers.auto_apply.base import _get_bot_ref, logger


def check_superjob_session(token: str = "", cookie_str: str = "") -> dict:
    """Проверяет валидность токена API или веб-сессии соискателя на SuperJob."""
    token = (token or "").strip()
    cookie_str = (cookie_str or "").strip()
    if not token and not cookie_str:
        return {"ok": False, "detail": "SuperJob токен или куки не указаны"}

    headers = {
        "User-Agent": "SuperJob-Career-Hub/1.0",
        "Accept": "application/json",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if cookie_str:
        headers["Cookie"] = cookie_str

    req_mod = _get_bot_ref("requests", requests)
    try:
        url = "https://api.superjob.ru/2.0/user_cvs/"
        resp = req_mod.get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            cvs = data.get("objects", []) if isinstance(data, dict) else []
            user_name = "Соискатель SuperJob"
            if cvs and isinstance(cvs[0], dict):
                user_name = cvs[0].get("name") or cvs[0].get("profession") or user_name
            return {
                "ok": True,
                "detail": "Сессия SuperJob активна",
                "user_name": user_name,
                "resumes": cvs,
            }
        elif resp.status_code in [401, 403]:
            return {"ok": False, "detail": "Токен или куки SuperJob недействительны или истекли"}
        return {"ok": False, "detail": f"Ответ SuperJob HTTP {resp.status_code}"}
    except Exception as e:
        logger.error(f"Ошибка проверки сессии SuperJob: {e}")
        return {"ok": False, "detail": str(e)}


def apply_superjob_vacancy(raw_id: str, cover_letter: str, token: str = "", cookie_str: str = "", resume_id: str = None) -> dict:
    """Отправляет отклик на вакансию SuperJob через официальный REST API или веб-сессию."""
    clean_id = str(raw_id).replace("sj_", "").strip()
    if not clean_id:
        return {"ok": False, "status": "error", "detail": "Не указан ID вакансии SuperJob"}

    token = (token or "").strip()
    cookie_str = (cookie_str or "").strip()
    if not token and not cookie_str:
        return {"ok": False, "status": "no_credentials", "detail": "Не настроен токен или куки SuperJob"}

    url = f"https://api.superjob.ru/2.0/vacancies/{clean_id}/send/"
    headers = {
        "User-Agent": "SuperJob-Career-Hub/1.0",
        "Accept": "application/json",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if cookie_str:
        headers["Cookie"] = cookie_str

    payload = {
        "comment": (cover_letter or "").strip(),
    }
    if resume_id:
        payload["id_cv"] = str(resume_id).strip()

    req_mod = _get_bot_ref("requests", requests)
    try:
        resp = req_mod.post(url, headers=headers, json=payload, timeout=12)
        resp_lower = resp.text.lower()
        if "already" in resp_lower or "уже откликнулись" in resp_lower:
            return {"ok": True, "status": "already_applied", "method": "superjob_api", "detail": "Ранее уже был подан отклик на SuperJob"}

        if resp.status_code in [200, 201]:
            return {"ok": True, "status": "applied", "method": "superjob_api", "detail": "Отклик успешно зарегистрирован на SuperJob"}

        if resp.status_code in [401, 403]:
            return {"ok": False, "status": "cookie_expired", "detail": "Сессия SuperJob устарела или отклонена"}

        if resp.status_code == 400:
            return {"ok": False, "status": "error", "detail": f"SuperJob API: {resp.text[:120]}"}

        return {"ok": False, "status": "error", "detail": f"HTTP {resp.status_code}: {resp.text[:120]}"}
    except Exception as e:
        logger.error(f"Исключение при отклике на SuperJob #{clean_id}: {e}")
        return {"ok": False, "status": "error", "detail": str(e)}
