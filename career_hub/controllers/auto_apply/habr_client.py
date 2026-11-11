"""
Habr Career (career.habr.com) Web Session & Application Adapter.
"""

import html
import json
import logging
import re
import requests

from career_hub.controllers.auto_apply.base import _get_bot_ref, logger


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

            has_auth_indicator = any(k in html_text for k in [
                "sign_out", "/users/sign_out", "user-info", "profile-header",
                "profile-name", "data-user-name", "header-user",
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
                "url": getattr(resp, "url", "https://career.habr.com/profile"),
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
                        "response_id": existing_resp.get("id") if isinstance(existing_resp, dict) else None,
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
                "data": resp_data,
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
