"""
Auto-Apply Controller and HeadHunter Negotiation Manager for Career Hub.
"""

import os
import re
import sys
import json
import time
import html
import logging
import urllib.parse
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
import requests
from career_hub.models.config import load_config, save_config, safe_int
from career_hub.models.storage import (
    DATA_DIR,
    load_statuses,
    save_statuses,
    update_vacancy_status,
    load_cache,
    save_to_cache,
    delete_from_cache,
    load_hh_negotiations_tracker,
    save_hh_negotiations_tracker,
    HH_NEGOTIATIONS_FILE,
)
from career_hub.models.vacancy import extract_tech_stack
from career_hub.controllers.ai_controller import generate_cover_letter, format_ai_vacancy_message

logger = logging.getLogger("career_hub.auto_apply")


def _get_bot_ref(attr_name, fallback):
    bot_mod = sys.modules.get("bot") or sys.modules.get("__main__")
    if bot_mod and hasattr(bot_mod, attr_name):
        return getattr(bot_mod, attr_name)
    bot_ctrl = sys.modules.get("career_hub.controllers.bot_controller")
    if bot_ctrl and hasattr(bot_ctrl, attr_name):
        return getattr(bot_ctrl, attr_name)
    try:
        from career_hub.controllers import bot_controller
        if hasattr(bot_controller, attr_name):
            return getattr(bot_controller, attr_name)
    except Exception:
        pass
    return fallback


def get_miniapp_url() -> str:
    cfg_fn = _get_bot_ref("load_config", None)
    if cfg_fn:
        try:
            return cfg_fn().get("miniapp_url") or os.environ.get("MINIAPP_URL") or "https://jobs.shadowkek.space/"
        except Exception:
            pass
    return os.environ.get("MINIAPP_URL") or "https://jobs.shadowkek.space/"


def get_hh_headers(access_token: str) -> dict:
    cfg_fn = _get_bot_ref("load_config", None)
    contact = "nikita@shadowkek.space"
    if cfg_fn:
        try:
            contact = cfg_fn().get("contact_email") or os.environ.get("CONTACT_EMAIL") or contact
        except Exception:
            pass
    return {
        "Authorization": f"Bearer {access_token}",
        "User-Agent": f"CareerHubBot/1.0 ({contact})"
    }


def check_hh_token(access_token: str):
    """Проверяет валидность токена HeadHunter API и возвращает профиль пользователя."""
    if not access_token:
        return None
    try:
        url = "https://api.hh.ru/me"
        req_post = _get_bot_ref("requests", requests)
        resp = req_post.get(url, headers=get_hh_headers(access_token), timeout=10)
        if resp.status_code == 200:
            return resp.json()
        logger.warning(f"Проверка HH токена ({resp.status_code}): {resp.text}")
    except Exception as e:
        logger.error(f"Ошибка проверки HH токена: {e}")
    return None


def fetch_hh_resumes(access_token: str) -> list:
    """Получает список резюме кандидата из HeadHunter."""
    if not access_token:
        return []
    try:
        url = "https://api.hh.ru/resumes/mine"
        req_post = _get_bot_ref("requests", requests)
        resp = req_post.get(url, headers=get_hh_headers(access_token), timeout=10)
        if resp.status_code == 200:
            return resp.json().get("items", [])
    except Exception as e:
        logger.error(f"Ошибка получения резюме HH: {e}")
    return []


def auto_select_hh_resume(access_token: str) -> dict | None:
    """Автоматически находит целевое Java / Backend резюме пользователя."""
    fetch_func = _get_bot_ref("fetch_hh_resumes", fetch_hh_resumes)
    resumes = fetch_func(access_token)
    if not resumes:
        return None
    for r in resumes:
        title = r.get("title", "").lower()
        status_id = r.get("status", {}).get("id", "")
        if "java" in title and status_id == "published":
            return r
    for r in resumes:
        title = r.get("title", "").lower()
        status_id = r.get("status", {}).get("id", "")
        if any(k in title for k in ["backend", "бэкенд", "разработчик", "developer"]) and status_id == "published":
            return r
    for r in resumes:
        if r.get("status", {}).get("id") == "published":
            return r
    for r in resumes:
        if "java" in r.get("title", "").lower():
            return r
    return resumes[0]


def apply_hh_vacancy_api(raw_id: str, resume_id: str, cover_letter: str, access_token: str) -> dict:
    """Отправляет отклик на вакансию через официальный API HeadHunter (POST /negotiations)."""
    if not raw_id:
        return {"ok": False, "status": "error", "detail": "Отсутствует ID вакансии"}

    clean_id = str(raw_id).replace("hh_", "").strip()
    if not clean_id.isdigit():
        return {"ok": False, "status": "error", "detail": f"Некорректный ID вакансии: {raw_id}"}

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
        req_post = _get_bot_ref("requests", requests)
        resp = req_post.post(url, headers=headers, data=data, timeout=15, allow_redirects=False)
        if resp.status_code == 201:
            loc = resp.headers.get("Location", "")
            return {"ok": True, "status": "sent", "detail": "Отклик успешно зарегистрирован", "negotiation_url": loc}

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


def check_hh_web_cookie(cookie_str: str) -> dict | None:
    """Проверяет валидность веб-сессии HeadHunter через /applicant/profile/me."""
    if not cookie_str:
        return None
    try:
        host_m = re.search(r'redirect_host=([^;]+)', cookie_str)
        host = host_m.group(1).strip() if host_m else "volgograd.hh.ru"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
            "Cookie": cookie_str,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        req_mod = _get_bot_ref("requests", requests)
        r = req_mod.get(f"https://{host}/applicant/profile/me", headers=headers, timeout=12, allow_redirects=False)
        if r.status_code == 200 and ("applicant" in r.text or "resume" in r.text or "profile" in r.text):
            resumes = list(set(re.findall(r'/resume/([a-f0-9]{38})', r.text)))
            name_m = re.search(r'data-qa="(?:applicant|profile)-name">([^<]+)<', r.text) or re.search(r'"firstName":"([^"]+)".*?"lastName":"([^"]+)"', r.text)
            c_name = "Соискатель"
            if name_m:
                c_name = name_m.group(1).strip() if len(name_m.groups()) == 1 else f"{name_m.group(1)} {name_m.group(2)}"
            return {
                "ok": True,
                "name": c_name,
                "host": host,
                "resumes": resumes
            }
        return None
    except Exception as e:
        logger.error(f"Ошибка проверки web cookie HH: {e}")
        return None


def apply_hh_vacancy_web(raw_id: str, resume_hash: str, cover_letter: str, cookie_str: str) -> dict:
    """Отправляет отклик на вакансию через веб-эндпоинт HeadHunter."""
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
        "resume_hash": str(resume_hash or os.environ.get("HH_RESUME_HASH", "")),
        "ignore_postponed": "true",
        "incomplete": "false",
        "lux": "true",
        "withoutTest": "no",
        "letter": clean_letter
    }

    try:
        req_mod = _get_bot_ref("requests", requests)
        resp = req_mod.post(url, headers=headers, data=payload, timeout=15, allow_redirects=False)

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


def check_habr_web_cookie(cookie_str: str) -> dict | None:
    """Проверяет валидность веб-сессии Хабр Карьеры через /profile и возвращает профиль соискателя."""
    if not cookie_str or not str(cookie_str).strip():
        return None
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
            "Cookie": cookie_str,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        req_mod = _get_bot_ref("requests", requests)
        resp = req_mod.get("https://career.habr.com/profile", headers=headers, timeout=12, allow_redirects=True)

        if resp.status_code == 200:
            resp_url = str(getattr(resp, "url", "")).lower()
            if any(k in resp_url for k in ["sign_in", "login", "account.habr.com"]):
                return None
            if resp_url.rstrip("/") == "https://career.habr.com":
                return None

            html_text = resp.text
            lower_text = html_text.lower()

            # Check for authentic user session indicators
            has_auth_indicator = any(k in html_text for k in [
                "sign_out", "/users/sign_out", "user-info", "profile-header",
                "profile-name", "data-user-name", "header-user"
            ])
            if not has_auth_indicator and ("sign_in" in lower_text or "войти" in lower_text or "авториз" in lower_text):
                return None

            c_name = "Соискатель"
            name_m = (
                re.search(r'class="[^"]*(?:user-info__name|user-info__title|profile-header__title|profile-name)[^"]*">([^<]+)<', html_text)
                or re.search(r'data-user-name="([^"]+)"', html_text)
                or re.search(r'<meta\s+property="og:title"\s+content="([^"]+)"', html_text)
                or re.search(r'<title>([^<—–|/]+)(?:—|–|\||\/).*?Хабр', html_text)
            )
            if name_m:
                cand_name = html.unescape(name_m.group(1).strip())
                if cand_name and not any(cand_name.lower().startswith(bad) for bad in ["вход", "авторизац", "войти", "sign in", "хабр", "работа в it"]):
                    c_name = cand_name

            if c_name == "Соискатель" and not has_auth_indicator:
                return None

            return {
                "ok": True,
                "name": c_name,
                "url": getattr(resp, "url", "https://career.habr.com/profile")
            }
        return None
    except Exception as e:
        logger.error(f"Ошибка проверки web cookie Habr: {e}")
        return None


def apply_habr_vacancy_web(raw_id: str, cover_letter: str, cookie_str: str) -> dict:
    """Отправляет отклик на вакансию через веб-сессию Хабр Карьеры (career.habr.com)."""
    if not raw_id:
        return {"ok": False, "status": "error", "detail": "Отсутствует ID вакансии"}

    clean_id = str(raw_id).replace("habr_", "").strip()
    id_m = re.search(r'(?:vacancies/|^)(\d+)', clean_id)
    if id_m:
        clean_id = id_m.group(1)
    else:
        digits = re.findall(r"\d+", clean_id)
        if not digits:
            return {"ok": False, "status": "error", "detail": f"Некорректный ID вакансии: {raw_id}"}
        clean_id = digits[-1]

    if not cookie_str or not str(cookie_str).strip():
        return {"ok": False, "status": "no_cookie", "detail": "Не настроены cookies веб-сессии Хабр Карьеры"}

    clean_letter = (cover_letter or "").strip()
    if len(clean_letter) > 9500:
        clean_letter = clean_letter[:9450] + "\n..."

    req_mod = _get_bot_ref("requests", requests)
    get_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
        "Cookie": cookie_str,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }

    csrf_token = ""
    resume_id = None

    try:
        page_resp = req_mod.get(f"https://career.habr.com/vacancies/{clean_id}", headers=get_headers, timeout=15, allow_redirects=True)
        resp_url = str(getattr(page_resp, "url", "")).lower()

        if page_resp.status_code in [401, 403] or any(k in resp_url for k in ["sign_in", "login", "account.habr.com"]):
            if "challenge" in page_resp.text.lower() or "turnstile" in page_resp.text.lower() or "captcha" in page_resp.text.lower():
                return {"ok": False, "status": "captcha_required", "detail": "Требуется решение капчи или проверка Cloudflare на Хабр Карьере"}
            return {"ok": False, "status": "cookie_expired", "detail": "Сессия Хабр Карьеры устарела или требует повторного входа"}

        if page_resp.status_code == 404:
            return {"ok": False, "status": "not_found", "detail": "Вакансия не найдена или снята с публикации"}

        if page_resp.status_code == 429:
            return {"ok": False, "status": "rate_limited", "detail": "Превышен лимит запросов Хабр Карьеры (429)"}

        if page_resp.status_code != 200:
            return {"ok": False, "status": "error", "detail": f"Ошибка загрузки страницы вакансии (HTTP {page_resp.status_code})"}

        html_text = page_resp.text
        lower_text = html_text.lower()

        if "challenge" in lower_text and ("cloudflare" in lower_text or "cf-turnstile" in lower_text or "challenge-running" in lower_text):
            return {"ok": False, "status": "captcha_required", "detail": "Требуется подтверждение браузера Cloudflare на Хабр Карьере"}

        # Проверка состояния отклика через Vue SSR State
        ssr_m = re.search(r'<script type="application/json" data-ssr-state="true">(.*?)</script>', html_text)
        if ssr_m:
            try:
                ssr_data = json.loads(ssr_m.group(1))
                create_resp_state = ssr_data.get("createResponse") or {}
                existing_resp = create_resp_state.get("response")
                if existing_resp is not None:
                    return {
                        "ok": True,
                        "status": "already_applied",
                        "detail": "Ранее уже был подан отклик на Хабр Карьере",
                        "response_id": existing_resp.get("id") if isinstance(existing_resp, dict) else None
                    }
            except Exception:
                pass

        if any(phrase in lower_text for phrase in ["вы уже откликнулись", "посмотреть отклик", "отклик отправлен", "already_applied", "already applied"]):
            return {"ok": True, "status": "already_applied", "detail": "Ранее уже был подан отклик"}

        if any(phrase in lower_text for phrase in ["вакансия закрыта", "вакансия в архиве", "закрытая вакансия"]):
            return {"ok": False, "status": "closed", "detail": "Вакансия закрыта или перенесена в архив"}

        ext_match = (
            re.search(r'href="([^"]+)"[^>]*class="[^"]*(?:external-button|external_link|btn-external|vacancy-show__external)[^"]*"', html_text, re.IGNORECASE)
            or re.search(r'class="[^"]*(?:external-button|external_link|btn-external|vacancy-show__external)[^"]*"[^>]*href="([^"]+)"', html_text, re.IGNORECASE)
            or re.search(r'<a[^>]+href="(https?://[^"]+)"[^>]*>(?:(?!</a>).)*?(?:Откликнуться на сайте|на сайте компании|на сайте работодателя)', html_text, re.IGNORECASE | re.DOTALL)
        )
        has_response_action = bool(re.search(r'applyToVacancy|create-vacancy-response|откликнуться', lower_text))
        if ext_match and not has_response_action:
            return {"ok": False, "status": "direct_required", "detail": "Требуется прямой отклик на сайте работодателя", "redirect_url": ext_match.group(1)}

        csrf_m = (
            re.search(r'<meta\s+name=["\']csrf-token["\']\s+content=["\']([^"\']+)["\']', html_text, re.IGNORECASE)
            or re.search(r'<meta\s+content=["\']([^"\']+)["\']\s+name=["\']csrf-token["\']', html_text, re.IGNORECASE)
            or re.search(r'<input[^>]+name=["\']authenticity_token["\'][^>]+value=["\']([^"\']+)["\']', html_text, re.IGNORECASE)
            or re.search(r'<input[^>]+value=["\']([^"\']+)["\'][^>]+name=["\']authenticity_token["\']', html_text, re.IGNORECASE)
        )
        if csrf_m:
            csrf_token = csrf_m.group(1).strip()

        resume_m = (
            re.search(r'name=["\']response\[resume_id\]["\'][^>]+value=["\']([^"\']+)["\']', html_text, re.IGNORECASE)
            or re.search(r'value=["\']([^"\']+)["\'][^>]+name=["\']response\[resume_id\]["\']', html_text, re.IGNORECASE)
            or re.search(r'id=["\']response_resume_id["\'][^>]+value=["\']([^"\']+)["\']', html_text, re.IGNORECASE)
            or re.search(r'value=["\']([^"\']+)["\'][^>]+id=["\']response_resume_id["\']', html_text, re.IGNORECASE)
            or re.search(r'<option[^>]+value=["\']([^"\']+)["\'][^>]*selected', html_text, re.IGNORECASE)
            or re.search(r'data-resume-id=["\']([^"\']+)["\']', html_text, re.IGNORECASE)
        )
        if resume_m:
            resume_id = resume_m.group(1).strip()

        if not csrf_token:
            if ext_match:
                return {"ok": False, "status": "direct_required", "detail": "Требуется прямой отклик на сайте работодателя", "redirect_url": ext_match.group(1)}
            if any(k in lower_text for k in ["войти", "авториз", "sign_in"]):
                return {"ok": False, "status": "cookie_expired", "detail": "Сессия Хабр Карьеры не авторизована (требуется повторный вход)"}
            return {"ok": False, "status": "error", "detail": "Не удалось обнаружить форму отклика или CSRF-токен на странице вакансии"}

    except Exception as e:
        logger.error(f"Исключение при загрузке страницы вакансии Хабра #{clean_id}: {e}")
        return {"ok": False, "status": "error", "detail": str(e)}

    # REST API эндпоинт Хабр Карьеры
    post_url = f"https://career.habr.com/api/frontend/vacancies/{clean_id}/responses"
    post_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
        "Cookie": cookie_str,
        "X-CSRF-Token": csrf_token,
        "Accept": "application/json",
        "Origin": "https://career.habr.com",
        "Referer": f"https://career.habr.com/vacancies/{clean_id}",
    }

    payload = {}
    if resume_id:
        payload["response[resume_id]"] = resume_id
    if clean_letter:
        payload["response[message]"] = clean_letter
    if csrf_token:
        payload["authenticity_token"] = csrf_token

    try:
        # Шаг 1: Создание отклика через API
        resp = req_mod.post(post_url, headers=post_headers, data=payload, timeout=15, allow_redirects=False)

        if resp.status_code in [200, 201]:
            lower_resp = resp.text.lower()
            if "<form" in lower_resp and any(err_word in lower_resp for err_word in ["error", "ошибк", "заполните", "не выбран"]):
                err_m = re.search(r'class="[^"]*(?:form-error|error|notice|alert)[^"]*">([^<]+)<', resp.text, re.IGNORECASE)
                err_text = err_m.group(1).strip() if err_m else "Форма отклика отклонена Хабр Карьерой"
                return {"ok": False, "status": "error", "detail": err_text}

            resp_data = {}
            try:
                resp_data = resp.json()
            except Exception:
                pass

            response_obj = resp_data.get("response") or {}
            response_id = response_obj.get("id")

            # Шаг 2: При необходимости патчим сопроводительное письмо
            if response_id and clean_letter and not payload.get("response[message]"):
                patch_url = f"https://career.habr.com/api/frontend/vacancies/{clean_id}/responses/{response_id}"
                try:
                    req_mod.patch(patch_url, headers=post_headers, data={"response[message]": clean_letter}, timeout=12)
                except Exception as pe:
                    logger.warning(f"Ошибка прикрепления письма к отклику Habr #{response_id}: {pe}")

            return {
                "ok": True,
                "status": "applied",
                "detail": "Отклик успешно зарегистрирован через веб-сессию Хабр Карьеры",
                "response_id": response_id,
                "data": resp_data
            }

        lower_resp = resp.text.lower()
        if any(phrase in lower_resp for phrase in ["already_applied", "вы уже откликнулись", "дубликат"]):
            return {"ok": True, "status": "already_applied", "detail": "Ранее уже был подан отклик"}

        if resp.status_code in [400, 422]:
            if "bad request" in lower_resp or "already" in lower_resp:
                return {"ok": True, "status": "already_applied", "detail": "Ранее уже был подан отклик на Хабр Карьере"}
            if "authenticity" in lower_resp or "csrf" in lower_resp:
                return {"ok": False, "status": "cookie_expired", "detail": "CSRF-токен или сессия Хабр Карьеры недействительна"}
            return {"ok": False, "status": "error", "detail": f"Ошибка отклика ({resp.status_code}): {resp.text[:100]}"}

        if resp.status_code in [302, 303]:
            loc = resp.headers.get("Location", "")
            if any(k in loc.lower() for k in ["sign_in", "login", "account.habr.com"]):
                return {"ok": False, "status": "cookie_expired", "detail": "Сессия Хабр Карьеры устарела или требует повторного входа"}
            if loc.startswith("http") and "career.habr.com" not in loc and "habr.com" not in loc:
                return {"ok": False, "status": "direct_required", "detail": "Требуется прямой отклик на сайте работодателя", "redirect_url": loc}
            return {"ok": True, "status": "applied", "detail": "Отклик успешно отправлен на Хабр Карьере", "redirect_url": loc}

        if resp.status_code in [401, 403]:
            return {"ok": False, "status": "cookie_expired", "detail": "Сессия Хабр Карьеры устарела или отклонена"}

        return {"ok": False, "status": "error", "detail": f"HTTP {resp.status_code}: {resp.text[:100]}"}
    except Exception as e:
        logger.error(f"Исключение при веб-отклике на Хабр #{clean_id}: {e}")
        return {"ok": False, "status": "error", "detail": str(e)}


# =====================================================================
# Phase 2: Telegram Channels Recruiter Direct Messaging Adapter
# =====================================================================

def build_telegram_recruiter_link(username: str, cover_letter: str) -> dict:
    """
    Генерирует прямые ссылки (Deep Link и Web Link) для открытия диалога с рекрутером
    в Telegram с предварительно заполненным текстом сопроводительного письма.
    """
    clean_user = (username or "").replace("@", "").strip()
    if not clean_user:
        return {"tg_url": "", "web_url": ""}
    encoded_text = urllib.parse.quote(str(cover_letter or "").strip())
    return {
        "tg_url": f"tg://resolve?domain={clean_user}&text={encoded_text}",
        "web_url": f"https://t.me/{clean_user}?text={encoded_text}",
        "username": clean_user
    }


def apply_telegram_vacancy(raw_id: str, cover_letter: str, username: str = None) -> dict:
    """
    Формирует действие отклика на вакансию из Telegram-канала.
    Возвращает глубокие ссылки для открытия чата с рекрутером в 1 клик.
    """
    clean_id = str(raw_id).replace("tg_", "").strip()
    links = build_telegram_recruiter_link(username, cover_letter) if username else {"tg_url": "", "web_url": ""}
    return {
        "ok": True,
        "status": "applied",
        "method": "telegram_direct",
        "detail": f"Сформирована прямая ссылка для диалога с рекрутером @{username}" if username else "Отмечено как откликнуто",
        "direct_link": links.get("tg_url", ""),
        "web_link": links.get("web_url", ""),
        "recruiter_username": username or ""
    }


# =====================================================================
# Phase 3: SuperJob (superjob.ru) API & Session Adapter
# =====================================================================

def check_superjob_session(token: str = "", cookie_str: str = "") -> dict:
    """
    Проверяет валидность токена API или веб-сессии соискателя на SuperJob.
    """
    token = (token or "").strip()
    cookie_str = (cookie_str or "").strip()
    if not token and not cookie_str:
        return {"ok": False, "detail": "SuperJob токен или куки не указаны"}

    headers = {
        "User-Agent": "SuperJob-Career-Hub/1.0",
        "Accept": "application/json"
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
                "resumes": cvs
            }
        elif resp.status_code in [401, 403]:
            return {"ok": False, "detail": "Токен или куки SuperJob недействительны или истекли"}
        return {"ok": False, "detail": f"Ответ SuperJob HTTP {resp.status_code}"}
    except Exception as e:
        logger.error(f"Ошибка проверки сессии SuperJob: {e}")
        return {"ok": False, "detail": str(e)}


def apply_superjob_vacancy(raw_id: str, cover_letter: str, token: str = "", cookie_str: str = "", resume_id: str = None) -> dict:
    """
    Отправляет отклик на вакансию SuperJob через официальный REST API или веб-сессию.
    """
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
        "comment": (cover_letter or "").strip()
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


# =====================================================================
# Phase 4: GetMatch (getmatch.ru) Session & Apply Adapter
# =====================================================================

def check_getmatch_session(token_or_cookie: str = "") -> dict:
    """
    Проверяет валидность токена авторизации или сессионных куки на GetMatch.
    """
    val = (token_or_cookie or "").strip()
    if not val:
        return {"ok": False, "detail": "GetMatch токен или куки не указаны"}

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
        "Accept": "application/json"
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
    """
    Отправляет отклик на вакансию GetMatch.
    """
    clean_id = str(raw_id).replace("gm_", "").strip()
    val = (token_or_cookie or "").strip()
    if not val:
        return {"ok": False, "status": "no_credentials", "detail": "Не настроен токен GetMatch"}

    url = f"https://getmatch.ru/api/v1/vacancies/{clean_id}/applications"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
        "Accept": "application/json",
        "Content-Type": "application/json"
    }
    if "=" in val and ";" in val:
        headers["Cookie"] = val
    else:
        headers["Authorization"] = f"Bearer {val}"

    payload = {
        "cover_letter": (cover_letter or "").strip(),
        "comment": (cover_letter or "").strip()
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


# =====================================================================
# Phase 5: Direct Email Auto-Mailer (SMTP) Adapter
# =====================================================================

def check_smtp_connection(smtp_host: str, smtp_port: int, smtp_user: str, smtp_password: str, use_ssl: bool = True) -> dict:
    """
    Проверяет подключение к SMTP-серверу кандидата (Яндекс.Почта, Mail.ru, Gmail, корпоративный SMTP).
    """
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
    """
    Отправляет профессиональный отклик на email рекрутера с прикреплением резюме (PDF).
    """
    clean_email = (to_email or "").strip().lower()
    if not clean_email or "@" not in clean_email:
        return {"ok": False, "status": "error", "detail": "Некорректный email адрес получателя"}

    cfg = smtp_config or load_config()
    host = (cfg.get("smtp_host") or "").strip()
    port = safe_int(cfg.get("smtp_port", 465), 465)
    user = (cfg.get("smtp_user") or "").strip()
    pwd = (cfg.get("smtp_password") or "").strip()
    use_ssl = bool(cfg.get("smtp_ssl", True))
    from_name = (cfg.get("smtp_from_name") or "Никита Фролов").strip()
    contact_email = (cfg.get("contact_email") or user).strip()

    if not host or not user or not pwd:
        return {"ok": False, "status": "no_credentials", "detail": "SMTP параметры не настроены в Mini App"}

    clean_title = re.sub(r'[\r\n\t]+', ' ', vacancy_title).strip()[:150]
    clean_from_name = re.split(r'[\r\n]', from_name)[0].strip()
    clean_user = re.split(r'[\r\n]', user)[0].strip()
    clean_contact = re.split(r'[\r\n]', contact_email)[0].strip()
    clean_to = re.split(r'[\r\n]', clean_email)[0].strip()
    from_name = clean_from_name
    contact_email = clean_contact

    msg = MIMEMultipart("mixed")
    msg["From"] = f"{clean_from_name} <{clean_user}>"
    msg["To"] = clean_to
    msg["Reply-To"] = clean_contact
    msg["Subject"] = f"Отклик на позицию {clean_title} — {clean_from_name}"

    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText(cover_letter, "plain", "utf-8"))

    port_url = cfg.get("portfolio_url") or "https://park.shadowkek.space"
    port_display = port_url.replace("https://", "").replace("http://", "").rstrip("/")
    res_tunnel = cfg.get("resume_quick_tunnel_url") or "https://south-tomato-therapist-minimum.trycloudflare.com/"

    html_content = f"""<html>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; line-height: 1.6; color: #1e293b;">
    <div style="max-width: 600px; margin: 0 auto; padding: 20px; border: 1px solid #e2e8f0; border-radius: 12px; background-color: #ffffff;">
        <h3 style="color: #4f46e5; margin-top: 0;">Здравствуйте!</h3>
        <div style="white-space: pre-line; font-size: 15px; color: #334155;">{html.escape(cover_letter)}</div>
        <hr style="border: none; border-top: 1px solid #e2e8f0; margin: 24px 0;" />
        <p style="font-size: 13px; color: #64748b; margin-bottom: 4px;">С уважением,<br><strong>{html.escape(from_name)}</strong></p>
        <p style="font-size: 12px; color: #94a3b8; margin: 0;">Контакты: {html.escape(contact_email)} | Портфолио/Резюме: <a href="{html.escape(res_tunnel)}" style="color: #4f46e5;">онлайн-резюме</a> | Проект (деплой): <a href="{html.escape(port_url)}" style="color: #4f46e5;">{html.escape(port_display)}</a></p>
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


# =====================================================================
# Phase 6: Rabota.ru (rabota.ru) Adapter
# =====================================================================

def check_rabota_session(cookie_or_token: str = "") -> dict:
    """
    Проверяет сессию соискателя на Работа.ру.
    """
    val = (cookie_or_token or "").strip()
    if not val:
        return {"ok": False, "detail": "Сессионные данные Работа.ру не указаны"}

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
        "Accept": "application/json"
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
    """
    Отправляет отклик на вакансию Работа.ру.
    """
    clean_id = str(raw_id).replace("rabota_", "").strip()
    val = (cookie_or_token or "").strip()
    if not val:
        return {"ok": False, "status": "no_credentials", "detail": "Не настроены куки или токен Работа.ру"}

    url = "https://api.rabota.ru/v1/resumes/responses"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
        "Accept": "application/json",
        "Content-Type": "application/json"
    }
    if "=" in val and ";" in val:
        headers["Cookie"] = val
    else:
        headers["Authorization"] = f"Bearer {val}"

    payload = {
        "vacancy_id": clean_id,
        "letter": (cover_letter or "").strip()
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


# =====================================================================
# Phase 7: TrudVsem (trudvsem.ru / Работа России) Adapter
# =====================================================================

def apply_trudvsem_vacancy(raw_id: str, cover_letter: str, direct_url: str = None) -> dict:
    """
    Формирует отклик для портала 'Работа России' (trudvsem.ru).
    Портал требует авторизацию через Госуслуги (ЕСИА).
    Возвращает ссылку прямого перехода с готовым текстом сопроводительного письма.
    """
    clean_id = str(raw_id).replace("tv_", "").replace("trudvsem_", "").strip()
    target_url = direct_url or f"https://trudvsem.ru/vacancy/card/{clean_id}"
    return {
        "ok": True,
        "status": "direct_required",
        "method": "trudvsem_direct",
        "detail": "Требуется авторизация через Госуслуги (ЕСИА). Письмо скопировано для отправки на сайте.",
        "redirect_url": target_url,
        "cover_letter": cover_letter
    }


# =====================================================================
# Universal Multi-Platform Auto-Apply Dispatcher
# =====================================================================

def apply_vacancy_dispatcher(vacancy: dict, cover_letter: str, config: dict = None) -> dict:
    """
    Универсальный диспетчер откликов по всем агрегаторам и источникам вакансий.
    """
    if not vacancy:
        return {"ok": False, "status": "error", "detail": "Вакансия не передана"}

    cfg = config or load_config()
    v_id = str(vacancy.get("id", ""))
    source = str(vacancy.get("source", "")).lower()
    raw_id = vacancy.get("raw_id") or v_id

    # Проверка черного списка компаний
    comp_lower = str(vacancy.get("company", "")).lower()
    title_lower = str(vacancy.get("title", "")).lower()
    for b in cfg.get("blacklisted_companies", []):
        b_clean = b.lower().strip()
        if b_clean and (b_clean in comp_lower or b_clean in title_lower):
            return {"ok": False, "status": "blacklisted", "detail": f"Компания находится в черном списке ({b})"}

    # 1. Telegram Channels
    if source == "telegram" or v_id.startswith("tg_"):
        recruiter_user = vacancy.get("recruiter_username") or (vacancy.get("contacts") or {}).get("username")
        apply_tg_fn = _get_bot_ref("apply_telegram_vacancy", apply_telegram_vacancy)
        return apply_tg_fn(raw_id, cover_letter, recruiter_user)

    # 2. HeadHunter
    if source in ["hh", "headhunter"] or v_id.startswith("hh_"):
        hh_cookie = (cfg.get("hh_web_cookie") or "").strip()
        hh_tok = (cfg.get("hh_access_token") or "").strip()
        hh_res_hash = (cfg.get("hh_resume_hash") or "").strip()
        clean_id = raw_id.replace("hh_", "")
        if hh_cookie:
            apply_hh_web_fn = _get_bot_ref("apply_hh_vacancy_web", apply_hh_vacancy_web)
            res = apply_hh_web_fn(clean_id, hh_res_hash, cover_letter, hh_cookie)
            if res.get("ok"):
                res["method"] = "hh_web"
            return res
        elif hh_tok:
            hh_res_id = (cfg.get("hh_resume_id") or "").strip()
            if not hh_res_id:
                sel_res_fn = _get_bot_ref("auto_select_hh_resume", auto_select_hh_resume)
                res_obj = sel_res_fn(hh_tok)
                if res_obj:
                    hh_res_id = res_obj.get("id", "")
            if hh_res_id:
                apply_hh_api_fn = _get_bot_ref("apply_hh_vacancy_api", apply_hh_vacancy_api)
                res = apply_hh_api_fn(clean_id, hh_res_id, cover_letter, hh_tok)
                if res.get("ok"):
                    res["method"] = "hh_api"
                return res
        return {"ok": False, "status": "no_credentials", "detail": "Не настроены куки или API-токен HeadHunter"}

    # 3. Хабр Карьера
    if source in ["habr", "habrcareer"] or v_id.startswith("habr_"):
        habr_cookie = (cfg.get("habr_web_cookie") or "").strip()
        clean_id = raw_id.replace("habr_", "")
        if habr_cookie:
            apply_habr_fn = _get_bot_ref("apply_habr_vacancy_web", apply_habr_vacancy_web)
            res = apply_habr_fn(clean_id, cover_letter, habr_cookie)
            if res.get("ok"):
                res["method"] = "habr_web"
            return res
        return {"ok": False, "status": "no_credentials", "detail": "Не настроены куки Хабр Карьеры"}

    # 4. SuperJob
    if source in ["superjob", "sj"] or v_id.startswith("sj_"):
        sj_tok = (cfg.get("superjob_token") or "").strip()
        sj_cookie = (cfg.get("superjob_cookie") or "").strip()
        sj_res_id = (cfg.get("superjob_resume_id") or "").strip()
        clean_id = raw_id.replace("sj_", "")
        if sj_tok or sj_cookie:
            apply_sj_fn = _get_bot_ref("apply_superjob_vacancy", apply_superjob_vacancy)
            return apply_sj_fn(clean_id, cover_letter, token=sj_tok, cookie_str=sj_cookie, resume_id=sj_res_id)
        return {"ok": False, "status": "no_credentials", "detail": "Не настроен токен или куки SuperJob"}

    # 5. GetMatch
    if source in ["getmatch", "gm"] or v_id.startswith("gm_"):
        gm_cred = (cfg.get("getmatch_token") or cfg.get("getmatch_cookie") or "").strip()
        clean_id = raw_id.replace("gm_", "")
        if gm_cred:
            apply_gm_fn = _get_bot_ref("apply_getmatch_vacancy", apply_getmatch_vacancy)
            return apply_gm_fn(clean_id, cover_letter, gm_cred)
        return {"ok": False, "status": "no_credentials", "detail": "Не настроен токен GetMatch"}

    # 6. Direct Email (если у вакансии есть email рекрутера)
    recruiter_email = vacancy.get("recruiter_email") or (vacancy.get("contacts") or {}).get("email")
    if recruiter_email and cfg.get("smtp_host"):
        send_email_fn = _get_bot_ref("send_email_application", send_email_application)
        return send_email_fn(recruiter_email, vacancy.get("title", "IT Вакансия"), cover_letter, smtp_config=cfg)

    # 7. Работа.ру
    if source in ["rabota"] or v_id.startswith("rabota_"):
        rabota_cred = (cfg.get("rabota_cookie") or cfg.get("rabota_token") or "").strip()
        clean_id = raw_id.replace("rabota_", "")
        if rabota_cred:
            apply_rabota_fn = _get_bot_ref("apply_rabota_vacancy", apply_rabota_vacancy)
            return apply_rabota_fn(clean_id, cover_letter, rabota_cred)
        return {"ok": False, "status": "no_credentials", "detail": "Не настроены куки Работа.ру"}

    # 8. Работа России (Госуслуги)
    if source in ["trudvsem"] or v_id.startswith("tv_"):
        clean_id = raw_id.replace("tv_", "").replace("trudvsem_", "")
        apply_tv_fn = _get_bot_ref("apply_trudvsem_vacancy", apply_trudvsem_vacancy)
        return apply_tv_fn(clean_id, cover_letter, direct_url=vacancy.get("link"))

    # Fallback
    return {"ok": True, "status": "manual", "method": "manual", "detail": "Отклик подготовлен для ручной отправки", "cover_letter": cover_letter}



def delete_hh_negotiation_web(topic_id: str, cookie_str: str, host: str = None) -> dict:
    """Удаляет/скрывает отклик (отказ) из профиля соискателя на HeadHunter."""
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
        req_mod = _get_bot_ref("requests", requests)
        resp = req_mod.post(url, headers=headers, data=payload, timeout=12)
        if resp.status_code in [200, 204]:
            return {"ok": True, "detail": "Отказ успешно удален из профиля HH"}
        return {"ok": False, "detail": f"HTTP {resp.status_code}: {resp.text[:100]}"}
    except Exception as e:
        logger.error(f"Исключение при удалении отказа HH {topic_id}: {e}")
        return {"ok": False, "detail": str(e)}


def remove_vacancy_from_profile_and_history(v_id: str):
    """Удаляет вакансию с отказом из истории и профиля в боте."""
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


def poll_hh_negotiations_and_notify(bot_token: str, chat_id: int, access_token: str = None, cookie_str: str = None):
    """Периодически проверяет обновления в откликах на HH."""
    if not access_token and not cookie_str:
        return

    tracker = load_hh_negotiations_tracker()
    fpath = _get_bot_ref("HH_NEGOTIATIONS_FILE", HH_NEGOTIATIONS_FILE)
    is_initial_run = (len(tracker) == 0 and not os.path.exists(fpath))
    updated = False

    send_msg = _get_bot_ref("send_message", None)
    del_hh = _get_bot_ref("delete_hh_negotiation_web", delete_hh_negotiation_web)
    rem_vac = _get_bot_ref("remove_vacancy_from_profile_and_history", remove_vacancy_from_profile_and_history)
    req_mod = _get_bot_ref("requests", requests)

    if cookie_str:
        try:
            host_m = re.search(r'redirect_host=([^;]+)', cookie_str)
            host = host_m.group(1).strip() if host_m else "volgograd.hh.ru"
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
                "Cookie": cookie_str,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            }
            resp = req_mod.get(f"https://{host}/applicant/negotiations", headers=headers, timeout=15, allow_redirects=False)
            if resp.status_code == 200:
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

                            if is_discard:
                                if prev_state != "discard_deleted":
                                    del_hh(t_id, cookie_str, host)
                                    tracker[vac_id] = {
                                        "state_id": "discard_deleted",
                                        "state_name": "Отказ (удален)",
                                        "topic_id": t_id,
                                        "updated_at": time.time()
                                    }
                                    updated = True
                                    rem_vac(f"hh_{vac_id}")

                                    if bot_token and chat_id and send_msg:
                                        msg = (
                                            f"🗑 <b>Отказ автоматически удален из профиля и истории!</b>\n\n"
                                            f"💼 <b>{vac_title}</b>\n"
                                            f"🏢 <b>Компания:</b> {comp_name}\n\n"
                                            f"✅ <i>Отклик удален из списка на HeadHunter и скрыт из истории Career Hub.</i>"
                                        )
                                        send_msg(bot_token, chat_id, msg)
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
                                if not is_initial_run and is_invitation and send_msg:
                                    msg = (
                                        f"🎉 <b>ПРИГЛАШЕНИЕ НА СОБЕСЕДОВАНИЕ! (HeadHunter)</b>\n\n"
                                        f"💼 <b>{vac_title}</b>\n"
                                        f"🏢 <b>Компания:</b> {comp_name}\n"
                                        f"📌 <b>Статус:</b> {html.escape(state_name)} 🔥\n\n"
                                        f"👉 <b><a href='{vac_url}'>Открыть вакансию на HeadHunter</a></b>"
                                    )
                                    send_msg(bot_token, chat_id, msg, reply_markup={
                                        "inline_keyboard": [
                                            [{"text": "🚀 Открыть на HH", "url": vac_url}],
                                            [{"text": "💼 Mini App", "web_app": {"url": get_miniapp_url()}}]
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
                                if state_id == "invitation" and send_msg:
                                    msg = (
                                        f"🎉 <b>ПРИГЛАШЕНИЕ НА СОБЕСЕДОВАНИЕ! (HeadHunter)</b>\n\n"
                                        f"💼 <b>{vac_title}</b>\n"
                                        f"🏢 <b>Компания:</b> {comp_name}\n"
                                        f"📌 <b>Статус:</b> {html.escape(state_name)} 🔥\n\n"
                                        f"👉 <b><a href='{vac_url}'>Перейти к согласованию интервью</a></b>"
                                    )
                                    send_msg(bot_token, chat_id, msg, reply_markup={
                                        "inline_keyboard": [
                                            [{"text": "🚀 Открыть на HH", "url": vac_url}],
                                            [{"text": "💼 Mini App", "web_app": {"url": get_miniapp_url()}}]
                                        ]
                                    })

                        if updated:
                            save_hh_negotiations_tracker(tracker)
                        return
                    except Exception as parse_e:
                        logger.warning(f"Ошибка обработки InitialState HH negotiations: {parse_e}")

        except Exception as e:
            logger.error(f"Ошибка проверки web negotiations HH: {e}")

    # Fallback на официальный API
    if not access_token:
        return
    try:
        url = "https://api.hh.ru/negotiations"
        resp = req_mod.get(url, headers=get_hh_headers(access_token), timeout=12)
        if resp.status_code != 200:
            return
        items = resp.json().get("items", [])
        tracker = load_hh_negotiations_tracker()
        updated = False

        for neg in items:
            neg_id = str(neg.get("id", ""))
            if not neg_id:
                continue
            state = neg.get("state", {})
            state_id = state.get("id", "")
            state_name = state.get("name", "Обновление")
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
                    rem_vac(f"hh_{vac_raw_id}")
                    if not is_initial_run and bot_token and chat_id and send_msg:
                        msg = (
                            f"🗑 <b>Отказ автоматически удален из профиля и истории!</b>\n\n"
                            f"💼 <b>{vac_title}</b>\n"
                            f"🏢 <b>Компания:</b> {employer}\n\n"
                            f"✅ <i>Отклик скрыт из истории Career Hub.</i>"
                        )
                        send_msg(bot_token, chat_id, msg)
                continue

            if prev is None:
                tracker[neg_id] = {
                    "state_id": state_id,
                    "state_name": state_name,
                    "updated_at": neg.get("updated_at")
                }
                updated = True
                if not is_initial_run and state_id == "invitation" and send_msg:
                    msg = (
                        f"🎉 <b>ПРИГЛАШЕНИЕ НА СОБЕСЕДОВАНИЕ! (HeadHunter)</b>\n\n"
                        f"💼 <b>{vac_title}</b>\n"
                        f"🏢 <b>Компания:</b> {employer}\n"
                        f"📌 <b>Статус:</b> {html.escape(state_name)} 🔥\n\n"
                        f"👉 <b><a href='{chat_url}'>Открыть переписку на HeadHunter</a></b>"
                    )
                    send_msg(bot_token, chat_id, msg, reply_markup={
                        "inline_keyboard": [
                            [{"text": "🚀 Открыть чат на HH", "url": chat_url}],
                            [{"text": "💼 Career Hub Mini App", "web_app": {"url": get_miniapp_url()}}]
                        ]
                    })
                continue

            prev_state_id = prev.get("state_id")
            if state_id != prev_state_id:
                tracker[neg_id] = {
                    "state_id": state_id,
                    "state_name": state_name,
                    "updated_at": neg.get("updated_at")
                }
                updated = True
                if state_id == "invitation" and send_msg:
                    msg = (
                        f"🎉 <b>ПРИГЛАШЕНИЕ НА СОБЕСЕДОВАНИЕ! (HeadHunter)</b>\n\n"
                        f"💼 <b>{vac_title}</b>\n"
                        f"🏢 <b>Компания:</b> {employer}\n"
                        f"📌 <b>Статус:</b> {html.escape(state_name)} 🔥\n\n"
                        f"👉 <b><a href='{chat_url}'>Перейти к согласованию интервью</a></b>"
                    )
                    send_msg(bot_token, chat_id, msg, reply_markup={
                        "inline_keyboard": [
                            [{"text": "🚀 Открыть чат на HH", "url": chat_url}],
                            [{"text": "💼 Career Hub Mini App", "web_app": {"url": get_miniapp_url()}}]
                        ]
                    })

        if updated:
            save_hh_negotiations_tracker(tracker)
    except Exception as e:
        logger.error(f"Ошибка проверки negotiations HH: {e}")


def process_vacancy_auto_apply_and_notify(v: dict, analysis: dict, config: dict, token: str, chat_id: int, groq_key: str, groq_model: str):
    """
    Обрабатывает подходящую вакансию:
    1. Авто-отклик (если включен и score >= auto_apply_min_score).
    2. Уведомление в Telegram по порогу notify_min_score.
    """
    v_id = v["id"]
    raw_id = v.get("raw_id") or (str(v_id).replace("habr_", "") if str(v_id).startswith("habr_") else (str(v_id).replace("hh_", "") if str(v_id).startswith("hh_") else str(v_id)))
    score = analysis.get("match_score", 0)
    raw_title = v.get("title", "")
    clean_title = raw_title.replace("Требуется «", "").replace("»", "").strip()
    company = v.get("company", "Не указана")
    source_name = v.get("source_name", "IT-Площадка")
    source_emoji = v.get("source_emoji", "💼")
    salary = v.get("salary")
    link = v.get("link", "#")

    cfg = dict(config) if config else load_config()
    auto_apply_enabled = cfg.get("auto_apply_enabled", True)
    auto_apply_min_score = cfg.get("auto_apply_min_score", 7)
    notify_min_score = cfg.get("notify_min_score", 9)
    hh_token = cfg.get("hh_access_token", "").strip().strip('"').strip("'")
    hh_resume_id = cfg.get("hh_resume_id", "").strip()
    hh_cookie = cfg.get("hh_web_cookie", "").strip()
    hh_resume_hash = cfg.get("hh_resume_hash", os.environ.get("HH_RESUME_HASH", "")).strip()
    habr_cookie = cfg.get("habr_web_cookie", "").strip()

    send_msg = _get_bot_ref("send_message", None)
    gen_letter = _get_bot_ref("generate_cover_letter", generate_cover_letter)
    apply_web = _get_bot_ref("apply_hh_vacancy_web", apply_hh_vacancy_web)
    apply_api = _get_bot_ref("apply_hh_vacancy_api", apply_hh_vacancy_api)
    apply_habr_web = _get_bot_ref("apply_habr_vacancy_web", apply_habr_vacancy_web)
    sel_res = _get_bot_ref("auto_select_hh_resume", auto_select_hh_resume)
    _raw_upd_status = _get_bot_ref("update_vacancy_status", update_vacancy_status)
    def upd_status(vid, st, **kw):
        kw.setdefault("company", company)
        kw.setdefault("title", clean_title)
        return _raw_upd_status(vid, st, **kw)

    cover_letter = None
    esc_title = html.escape(clean_title)
    esc_company = html.escape(company)
    esc_salary = html.escape(salary) if salary else None
    esc_source_name = html.escape(source_name)

    # Защитный барьер: проверка черного списка компаний
    blacklisted = cfg.get("blacklisted_companies", [])
    comp_lower = company.lower() if company else ""
    title_lower = clean_title.lower() if clean_title else ""
    for b in blacklisted:
        b_clean = b.lower().strip()
        if b_clean and (b_clean in comp_lower or b_clean in title_lower):
            logger.info(f"Пропуск авто-отклика для {v_id}: компания '{company}' находится в черном списке ({b_clean})")
            return False, False

    # Защитный барьер: проверка отключенных платформ (Работа России)
    if (v.get("source") in ["trudvsem"] or str(v_id).startswith("trudvsem_") or str(v_id).startswith("tv_")) and not cfg.get("enable_trudvsem", False):
        logger.info(f"Пропуск вакансии и оповещения для {v_id}: платформа 'Работа России' отключена в настройках")
        return False, False

    if auto_apply_enabled and score >= auto_apply_min_score:
        logger.info(f"Генерация авто-отклика для [{source_name}] {clean_title} (Score {score})...")
        try:
            cover_letter = gen_letter(groq_key, groq_model, v)
        except Exception as e:
            logger.error(f"Ошибка генерации письма для авто-отклика {v_id}: {e}")
            cover_letter = None

        if cover_letter:
            v["cover_letter"] = cover_letter
            esc_letter = html.escape(cover_letter)

            if v.get("source") in ["hh", "headhunter"] or str(v_id).startswith("hh_"):
                hh_res = None
                applied_method = None
                if hh_cookie:
                    hh_res = apply_web(raw_id, hh_resume_hash, cover_letter, hh_cookie)
                    applied_method = "hh_web"
                elif hh_token:
                    if not hh_resume_id:
                        resume_obj = sel_res(hh_token)
                        if resume_obj:
                            hh_resume_id = resume_obj.get("id", "")
                            cfg_save = load_config()
                            cfg_save["hh_resume_id"] = hh_resume_id
                            save_config(cfg_save)
                            if isinstance(config, dict):
                                config["hh_resume_id"] = hh_resume_id

                    if hh_resume_id:
                        hh_res = apply_api(raw_id, hh_resume_id, cover_letter, hh_token)
                        applied_method = "hh_api"

                if hh_res:
                    if hh_res.get("ok"):
                        upd_status(
                            v_id, "applied",
                            cover_letter=cover_letter,
                            auto_applied=True,
                            method=applied_method or "hh_web",
                            detail=hh_res.get("detail", "")
                        )
                        if send_msg:
                            logger.info(f"Отправка Telegram-уведомления об авто-отклике для {v_id} ({clean_title}) в chat_id={chat_id}")
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
                            tg_res = send_msg(token, chat_id, msg, reply_markup={
                                "inline_keyboard": [
                                    [
                                        {"text": "🔗 Открыть вакансию на HH", "url": link},
                                        {"text": "💼 Открыть в Mini App", "web_app": {"url": get_miniapp_url()}}
                                    ]
                                ]
                            })
                            if not tg_res:
                                if not chat_id:
                                    logger.info(f"Telegram-уведомление об авто-отклике {v_id} отложено: ожидание первого старта диалога от владельца (/start в боте Telegram)")
                                else:
                                    logger.error(f"Не удалось доставить уведомление об авто-отклике {v_id} в Telegram")
                        else:
                            logger.warning(f"send_message недоступен, уведомление для {v_id} не отправлено")
                        return True, True

                    elif hh_res.get("status") == "already_applied":
                        upd_status(
                            v_id, "applied",
                            cover_letter=cover_letter,
                            auto_applied=True,
                            method="already_applied",
                            detail=hh_res.get("detail", "Отклик уже зарегистрирован на hh.ru")
                        )
                        if send_msg:
                            logger.info(f"Отправка Telegram-уведомления о существующем отклике для {v_id} в chat_id={chat_id}")
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
                            send_msg(token, chat_id, msg, reply_markup={
                                "inline_keyboard": [
                                    [
                                        {"text": "🔗 Открыть вакансию на HH", "url": link},
                                        {"text": "💼 Открыть в Mini App", "web_app": {"url": get_miniapp_url()}}
                                    ]
                                ]
                            })
                        else:
                            logger.warning(f"send_message недоступен, уведомление для {v_id} не отправлено")
                        return True, True

                    elif hh_res.get("status") == "test_required":
                        upd_status(v_id, "inbox", cover_letter=cover_letter, auto_prepared=True, needs_test=True)
                        if score >= notify_min_score and send_msg:
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
                            send_msg(token, chat_id, msg, reply_markup={
                                "inline_keyboard": [
                                    [{"text": "🚀 Пройти тест и откликнуться", "url": link}],
                                    [{"text": "✅ Отметить 'Откликнулся'", "callback_data": f"mark_applied_{v_id}"}],
                                    [{"text": "💼 Открыть в Mini App", "web_app": {"url": get_miniapp_url()}}]
                                ]
                            })
                            return False, True
                        return False, False

                    elif hh_res.get("status") == "direct_required":
                        direct_url = hh_res.get("redirect_url") or link
                        upd_status(v_id, "inbox", cover_letter=cover_letter, auto_prepared=True, direct_url=direct_url)
                        if score >= notify_min_score and send_msg:
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
                            send_msg(token, chat_id, msg, reply_markup={
                                "inline_keyboard": [
                                    [{"text": "🚀 Перейти к подаче заявки", "url": direct_url}],
                                    [{"text": "✅ Отметить 'Откликнулся'", "callback_data": f"mark_applied_{v_id}"}],
                                    [{"text": "💼 Открыть в Mini App", "web_app": {"url": get_miniapp_url()}}]
                                ]
                            })
                            return False, True
                        return False, False

            elif (v.get("source") in ["habr", "habrcareer"] or str(v_id).startswith("habr_")) and habr_cookie:
                habr_res = apply_habr_web(raw_id, cover_letter, habr_cookie)
                if habr_res:
                    if habr_res.get("ok"):
                        upd_status(
                            v_id, "applied",
                            cover_letter=cover_letter,
                            auto_applied=True,
                            method="habr_web",
                            detail=habr_res.get("detail", "")
                        )
                        if send_msg:
                            logger.info(f"Отправка Telegram-уведомления об авто-отклике для {v_id} ({clean_title}) в chat_id={chat_id}")
                            msg = (
                                f"🚀 <b>АВТО-ОТКЛИК ОТПРАВЛЕН! [Хабр Карьера]</b>\n\n"
                                f"🔥 <b>Совпадение: {score}/10</b>\n"
                                f"💼 <b>{esc_title}</b>\n"
                                f"🏢 <b>Компания:</b> {esc_company}\n"
                            )
                            if esc_salary:
                                msg += f"💰 <b>Зарплата:</b> {esc_salary}\n"
                            msg += (
                                f"\n📝 <b>Отправленное сопроводительное письмо:</b>\n"
                                f"<blockquote>{esc_letter}</blockquote>\n\n"
                                f"✅ <i>Резюме и письмо успешно зарегистрированы на career.habr.com.</i>"
                            )
                            tg_res = send_msg(token, chat_id, msg, reply_markup={
                                "inline_keyboard": [
                                    [
                                        {"text": "🔗 Открыть вакансию на Хабре", "url": link},
                                        {"text": "💼 Открыть в Mini App", "web_app": {"url": get_miniapp_url()}}
                                    ]
                                ]
                            })
                            if not tg_res:
                                if not chat_id:
                                    logger.info(f"Telegram-уведомление об авто-отклике {v_id} отложено: ожидание первого старта диалога от владельца (/start в боте Telegram)")
                                else:
                                    logger.error(f"Не удалось доставить уведомление об авто-отклике {v_id} в Telegram")
                        else:
                            logger.warning(f"send_message недоступен, уведомление для {v_id} не отправлено")
                        return True, True

                    elif habr_res.get("status") == "already_applied":
                        upd_status(
                            v_id, "applied",
                            cover_letter=cover_letter,
                            auto_applied=True,
                            method="already_applied",
                            detail=habr_res.get("detail", "Отклик уже зарегистрирован на Хабр Карьере")
                        )
                        if send_msg:
                            logger.info(f"Отправка Telegram-уведомления о существующем отклике для {v_id} в chat_id={chat_id}")
                            msg = (
                                f"🚀 <b>ОТКЛИК УЖЕ ЗАРЕГИСТРИРОВАН НА Хабр Карьере!</b>\n\n"
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
                            msg += f"✅ <i>Отклик на эту вакансию уже зарегистрирован в вашем личном кабинете на career.habr.com.</i>"
                            send_msg(token, chat_id, msg, reply_markup={
                                "inline_keyboard": [
                                    [
                                        {"text": "🔗 Открыть вакансию на Хабре", "url": link},
                                        {"text": "💼 Открыть в Mini App", "web_app": {"url": get_miniapp_url()}}
                                    ]
                                ]
                            })
                        else:
                            logger.warning(f"send_message недоступен, уведомление для {v_id} не отправлено")
                        return True, True

                    elif habr_res.get("status") == "test_required":
                        upd_status(v_id, "inbox", cover_letter=cover_letter, auto_prepared=True, needs_test=True)
                        if score >= notify_min_score and send_msg:
                            msg = (
                                f"⚠️ <b>Внимание: Требуется анкета / тест работодателя!</b>\n\n"
                                f"🔥 <b>Совпадение: {score}/10</b> • 🔵 <b>Хабр Карьера</b>\n"
                                f"💼 <b>{esc_title}</b>\n"
                                f"🏢 <b>Компания:</b> {esc_company}\n"
                            )
                            if esc_salary:
                                msg += f"💰 <b>Зарплата:</b> {esc_salary}\n"
                            msg += (
                                f"\n📝 <b>Сопроводительное письмо уже подготовлено:</b>\n"
                                f"<blockquote>{esc_letter}</blockquote>\n\n"
                                f"💡 <i>Работодатель требует ответить на вопросы или пройти опрос на career.habr.com перед подачей.</i>"
                            )
                            send_msg(token, chat_id, msg, reply_markup={
                                "inline_keyboard": [
                                    [{"text": "🚀 Пройти тест и откликнуться", "url": link}],
                                    [{"text": "✅ Отметить 'Откликнулся'", "callback_data": f"mark_applied_{v_id}"}],
                                    [{"text": "💼 Открыть в Mini App", "web_app": {"url": get_miniapp_url()}}]
                                ]
                            })
                            return False, True
                        return False, False

                    elif habr_res.get("status") == "direct_required":
                        direct_url = habr_res.get("redirect_url") or link
                        upd_status(v_id, "inbox", cover_letter=cover_letter, auto_prepared=True, direct_url=direct_url)
                        if score >= notify_min_score and send_msg:
                            msg = (
                                f"🌐 <b>Внимание: Требуется прямой отклик на сайте работодателя!</b>\n\n"
                                f"🔥 <b>Совпадение: {score}/10</b> • 🔵 <b>Хабр Карьера</b>\n"
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
                            send_msg(token, chat_id, msg, reply_markup={
                                "inline_keyboard": [
                                    [{"text": "🚀 Перейти к подаче заявки", "url": direct_url}],
                                    [{"text": "✅ Отметить 'Откликнулся'", "callback_data": f"mark_applied_{v_id}"}],
                                    [{"text": "💼 Открыть в Mini App", "web_app": {"url": get_miniapp_url()}}]
                                ]
                            })
                            return False, True
                        return False, False

            elif (v.get("source") in ["superjob", "sj"] or str(v_id).startswith("sj_")) and (config.get("superjob_token") or config.get("superjob_cookie")):
                sj_res = apply_superjob_vacancy(raw_id, cover_letter, token=config.get("superjob_token"), cookie_str=config.get("superjob_cookie"), resume_id=config.get("superjob_resume_id"))
                if sj_res and sj_res.get("ok"):
                    upd_status(v_id, "applied", cover_letter=cover_letter, auto_applied=True, method="superjob_api", detail=sj_res.get("detail", ""))
                    if send_msg:
                        logger.info(f"Отправка Telegram-уведомления об авто-отклике для {v_id} в chat_id={chat_id}")
                        msg = (
                            f"🚀 <b>АВТО-ОТКЛИК ОТПРАВЛЕН! [SuperJob]</b>\n\n"
                            f"🔥 <b>Совпадение: {score}/10</b>\n"
                            f"💼 <b>{esc_title}</b>\n"
                            f"🏢 <b>Компания:</b> {esc_company}\n"
                        )
                        if esc_salary:
                            msg += f"💰 <b>Зарплата:</b> {esc_salary}\n"
                        msg += (
                            f"\n📝 <b>Отправленное сопроводительное письмо:</b>\n"
                            f"<blockquote>{esc_letter}</blockquote>\n\n"
                            f"✅ <i>Отклик успешно зарегистрирован через SuperJob API.</i>"
                        )
                        send_msg(token, chat_id, msg, reply_markup={
                            "inline_keyboard": [
                                [
                                    {"text": "🔗 Открыть вакансию на SuperJob", "url": link},
                                    {"text": "💼 Открыть в Mini App", "web_app": {"url": get_miniapp_url()}}
                                ]
                            ]
                        })
                    return True, True

            elif (v.get("source") in ["getmatch", "gm"] or str(v_id).startswith("gm_")) and (config.get("getmatch_token") or config.get("getmatch_cookie")):
                gm_cred = config.get("getmatch_token") or config.get("getmatch_cookie")
                gm_res = apply_getmatch_vacancy(raw_id, cover_letter, gm_cred)
                if gm_res and gm_res.get("ok"):
                    upd_status(v_id, "applied", cover_letter=cover_letter, auto_applied=True, method="getmatch_api", detail=gm_res.get("detail", ""))
                    if send_msg:
                        logger.info(f"Отправка Telegram-уведомления об авто-отклике для {v_id} в chat_id={chat_id}")
                        msg = (
                            f"🚀 <b>АВТО-ОТКЛИК ОТПРАВЛЕН! [GetMatch]</b>\n\n"
                            f"🔥 <b>Совпадение: {score}/10</b>\n"
                            f"💼 <b>{esc_title}</b>\n"
                            f"🏢 <b>Компания:</b> {esc_company}\n"
                        )
                        if esc_salary:
                            msg += f"💰 <b>Зарплата:</b> {esc_salary}\n"
                        msg += (
                            f"\n📝 <b>Отправленное сопроводительное письмо:</b>\n"
                            f"<blockquote>{esc_letter}</blockquote>\n\n"
                            f"✅ <i>Отклик успешно зарегистрирован через GetMatch API.</i>"
                        )
                        send_msg(token, chat_id, msg, reply_markup={
                            "inline_keyboard": [
                                [
                                    {"text": "🔗 Открыть вакансию на GetMatch", "url": link},
                                    {"text": "💼 Открыть в Mini App", "web_app": {"url": get_miniapp_url()}}
                                ]
                            ]
                        })
                    return True, True

            elif (v.get("source") in ["rabota"] or str(v_id).startswith("rabota_")) and (config.get("rabota_cookie") or config.get("rabota_token")):
                rab_cred = config.get("rabota_cookie") or config.get("rabota_token")
                rab_res = apply_rabota_vacancy(raw_id, cover_letter, rab_cred)
                if rab_res and rab_res.get("ok"):
                    upd_status(v_id, "applied", cover_letter=cover_letter, auto_applied=True, method="rabota_api", detail=rab_res.get("detail", ""))
                    if send_msg:
                        logger.info(f"Отправка Telegram-уведомления об авто-отклике для {v_id} в chat_id={chat_id}")
                        msg = (
                            f"🚀 <b>АВТО-ОТКЛИК ОТПРАВЛЕН! [Работа.ру]</b>\n\n"
                            f"🔥 <b>Совпадение: {score}/10</b>\n"
                            f"💼 <b>{esc_title}</b>\n"
                            f"🏢 <b>Компания:</b> {esc_company}\n"
                        )
                        if esc_salary:
                            msg += f"💰 <b>Зарплата:</b> {esc_salary}\n"
                        msg += (
                            f"\n📝 <b>Отправленное сопроводительное письмо:</b>\n"
                            f"<blockquote>{esc_letter}</blockquote>\n\n"
                            f"✅ <i>Отклик успешно зарегистрирован через Работа.ру.</i>"
                        )
                        send_msg(token, chat_id, msg, reply_markup={
                            "inline_keyboard": [
                                [
                                    {"text": "🔗 Открыть вакансию на Работа.ру", "url": link},
                                    {"text": "💼 Открыть в Mini App", "web_app": {"url": get_miniapp_url()}}
                                ]
                            ]
                        })
                    return True, True

            upd_status(v_id, "inbox", cover_letter=cover_letter, auto_prepared=True)

            if score >= notify_min_score and send_msg:
                role = analysis.get("role", "java")
                role_badge = "☕ [Java Backend]"
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

                # Выбор наиболее эффективной кнопки действия в Telegram
                recruiter_user = v.get("recruiter_username") or (v.get("contacts") or {}).get("username")
                action_btn = {"text": f"🚀 Откликнуться на {source_name}", "url": link}
                if recruiter_user:
                    tg_deep = build_telegram_recruiter_link(recruiter_user, cover_letter)
                    if tg_deep.get("tg_url"):
                        action_btn = {"text": f"💬 Написать @{recruiter_user}", "url": tg_deep["tg_url"]}
                elif v.get("recruiter_email"):
                    rec_em = v.get("recruiter_email")
                    action_btn = {"text": "✉️ Написать на Email", "url": f"mailto:{rec_em}?subject={urllib.parse.quote('Отклик: ' + clean_title)}"}

                send_msg(token, chat_id, msg, reply_markup={
                    "inline_keyboard": [
                        [
                            action_btn,
                            {"text": "✅ Отметить 'Откликнулся'", "callback_data": f"mark_applied_{v_id}"}
                        ],
                        [
                            {"text": "🔄 Перегенерировать письмо", "callback_data": f"apply_{v_id}"},
                            {"text": "💼 Открыть в Mini App", "web_app": {"url": get_miniapp_url()}}
                        ]
                    ]
                })
                return False, True

            return False, False

    if score >= notify_min_score and send_msg:
        msg = format_ai_vacancy_message(v, analysis)
        send_msg(token, chat_id, msg, reply_markup={
            "inline_keyboard": [
                [
                    {"text": "📝 Подготовить отклик", "callback_data": f"apply_{v_id}"},
                    {"text": f"🔗 Открыть на {source_name}", "url": link}
                ],
                [
                    {"text": "✅ Отметить 'Откликнулся'", "callback_data": f"mark_applied_{v_id}"},
                    {"text": "💼 Открыть в Mini App", "web_app": {"url": get_miniapp_url()}}
                ]
            ]
        })
        return False, True

    return False, False
