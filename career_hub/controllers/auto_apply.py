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
import requests
from career_hub.models.config import load_config, save_config
from career_hub.models.storage import (
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
            return cfg_fn().get("miniapp_url") or os.environ.get("MINIAPP_URL") or "https://jobs.example.com/"
        except Exception:
            pass
    return os.environ.get("MINIAPP_URL") or "https://jobs.example.com/"


def get_hh_headers(access_token: str) -> dict:
    cfg_fn = _get_bot_ref("load_config", None)
    contact = "applicant@example.com"
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
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36",
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
    raw_id = v.get("raw_id") or (str(v_id).replace("hh_", "") if str(v_id).startswith("hh_") else str(v_id))
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

    send_msg = _get_bot_ref("send_message", None)
    gen_letter = _get_bot_ref("generate_cover_letter", generate_cover_letter)
    apply_web = _get_bot_ref("apply_hh_vacancy_web", apply_hh_vacancy_web)
    apply_api = _get_bot_ref("apply_hh_vacancy_api", apply_hh_vacancy_api)
    sel_res = _get_bot_ref("auto_select_hh_resume", auto_select_hh_resume)
    upd_status = _get_bot_ref("update_vacancy_status", update_vacancy_status)

    cover_letter = None
    esc_title = html.escape(clean_title)
    esc_company = html.escape(company)
    esc_salary = html.escape(salary) if salary else None
    esc_source_name = html.escape(source_name)

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
                send_msg(token, chat_id, msg, reply_markup={
                    "inline_keyboard": [
                        [
                            {"text": f"🚀 Откликнуться на {source_name}", "url": link},
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
