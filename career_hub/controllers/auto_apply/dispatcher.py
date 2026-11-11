"""
Universal Multi-Platform Auto-Apply Dispatcher & Notification Orchestrator.
"""

import html
import os
import urllib.parse

from career_hub.controllers.ai_controller import format_ai_vacancy_message, generate_cover_letter
from career_hub.controllers.auto_apply.base import _get_bot_ref, get_miniapp_url, logger
from career_hub.controllers.auto_apply.email_client import send_email_application
from career_hub.controllers.auto_apply.getmatch_client import apply_getmatch_vacancy
from career_hub.controllers.auto_apply.habr_client import apply_habr_vacancy_web
from career_hub.controllers.auto_apply.hh_client import (
    apply_hh_vacancy_api,
    apply_hh_vacancy_web,
    auto_select_hh_resume,
)
from career_hub.controllers.auto_apply.rabota_client import (
    apply_rabota_vacancy,
    apply_trudvsem_vacancy,
)
from career_hub.controllers.auto_apply.superjob_client import apply_superjob_vacancy
from career_hub.controllers.auto_apply.telegram_client import (
    apply_telegram_vacancy,
    build_telegram_recruiter_link,
)
from career_hub.models.config import load_config, save_config
from career_hub.models.storage import update_vacancy_status
from career_hub.models.vacancy import extract_tech_stack


def apply_vacancy_dispatcher(vacancy: dict, cover_letter: str, config: dict = None) -> dict:
    """Универсальный диспетчер откликов по всем агрегаторам и источникам вакансий."""
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

    # 6. Direct Email
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

    return {"ok": True, "status": "manual", "method": "manual", "detail": "Отклик подготовлен для ручной отправки", "cover_letter": cover_letter}


def _send_platform_applied_alert(
    send_msg, token: str, chat_id: int, v_id: str, clean_title: str,
    esc_title: str, esc_company: str, esc_salary: str | None, esc_letter: str,
    score: int, link: str, platform_label: str, btn_label: str, footer_note: str,
    already_applied: bool = False,
):
    if not send_msg:
        logger.warning(f"send_message недоступен, уведомление для {v_id} не отправлено")
        return
    if already_applied:
        logger.info(f"Отправка Telegram-уведомления о существующем отклике для {v_id} в chat_id={chat_id}")
        header = f"🚀 <b>ОТКЛИК УЖЕ ЗАРЕГИСТРИРОВАН НА {platform_label}!</b>\n\n"
    else:
        logger.info(f"Отправка Telegram-уведомления об авто-отклике для {v_id} ({clean_title}) в chat_id={chat_id}")
        header = f"🚀 <b>АВТО-ОТКЛИК ОТПРАВЛЕН! [{platform_label}]</b>\n\n"

    msg = (
        f"{header}"
        f"🔥 <b>Совпадение: {score}/10</b>\n"
        f"💼 <b>{esc_title}</b>\n"
        f"🏢 <b>Компания:</b> {esc_company}\n"
    )
    if esc_salary:
        msg += f"💰 <b>Зарплата:</b> {esc_salary}\n"
    if esc_letter:
        label = "Сопроводительное письмо" if already_applied else "Отправленное сопроводительное письмо"
        msg += f"\n📝 <b>{label}:</b>\n<blockquote>{esc_letter}</blockquote>\n\n"
    msg += f"✅ <i>{footer_note}</i>"

    tg_res = send_msg(token, chat_id, msg, reply_markup={
        "inline_keyboard": [[
            {"text": btn_label, "url": link},
            {"text": "💼 Открыть в Mini App", "web_app": {"url": get_miniapp_url()}},
        ]]
    })
    if not tg_res and not already_applied:
        if not chat_id:
            logger.info(f"Telegram-уведомление об авто-отклике {v_id} отложено: ожидание первого старта диалога (/start)")
        else:
            logger.error(f"Не удалось доставить уведомление об авто-отклике {v_id} в Telegram")


def _send_action_required_alert(
    send_msg, token: str, chat_id: int, v_id: str,
    esc_title: str, esc_company: str, esc_salary: str | None, esc_letter: str,
    score: int, action_url: str, platform_badge: str, is_test: bool,
):
    if is_test:
        header = f"⚠️ <b>Внимание: Требуется анкета / тест работодателя!</b>\n\n"
        btn_text = "🚀 Пройти тест и откликнуться"
        note = "Работодатель требует ответить на вопросы или пройти опрос перед подачей."
    else:
        header = f"🌐 <b>Внимание: Требуется прямой отклик на сайте работодателя!</b>\n\n"
        btn_text = "🚀 Перейти к подаче заявки"
        note = "Работодатель принимает отклики через свою внешнюю систему подбора персонала."

    msg = (
        f"{header}"
        f"🔥 <b>Совпадение: {score}/10</b> • {platform_badge}\n"
        f"💼 <b>{esc_title}</b>\n"
        f"🏢 <b>Компания:</b> {esc_company}\n"
    )
    if esc_salary:
        msg += f"💰 <b>Зарплата:</b> {esc_salary}\n"
    msg += f"\n📝 <b>Сопроводительное письмо уже подготовлено:</b>\n<blockquote>{esc_letter}</blockquote>\n\n💡 <i>{note}</i>"

    send_msg(token, chat_id, msg, reply_markup={
        "inline_keyboard": [
            [{"text": btn_text, "url": action_url}],
            [{"text": "✅ Отметить 'Откликнулся'", "callback_data": f"mark_applied_{v_id}"}],
            [{"text": "💼 Открыть в Mini App", "web_app": {"url": get_miniapp_url()}}],
        ]
    })


def process_vacancy_auto_apply_and_notify(v: dict, analysis: dict, config: dict, token: str, chat_id: int, groq_key: str, groq_model: str):
    """Обрабатывает подходящую вакансию: авто-отклик и уведомление в Telegram."""
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

    esc_title = html.escape(clean_title)
    esc_company = html.escape(company)
    esc_salary = html.escape(salary) if salary else None
    esc_source_name = html.escape(source_name)

    blacklisted = cfg.get("blacklisted_companies", [])
    comp_lower = company.lower() if company else ""
    title_lower = clean_title.lower() if clean_title else ""
    for b in blacklisted:
        b_clean = b.lower().strip()
        if b_clean and (b_clean in comp_lower or b_clean in title_lower):
            logger.info(f"Пропуск авто-отклика для {v_id}: компания '{company}' находится в черном списке ({b_clean})")
            return False, False

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

            # 1. HeadHunter
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
                    status = hh_res.get("status")
                    if hh_res.get("ok"):
                        upd_status(v_id, "applied", cover_letter=cover_letter, auto_applied=True, method=applied_method or "hh_web", detail=hh_res.get("detail", ""))
                        _send_platform_applied_alert(
                            send_msg, token, chat_id, v_id, clean_title, esc_title, esc_company, esc_salary, esc_letter,
                            score, link, "HeadHunter", "🔗 Открыть вакансию на HH", "Резюме и письмо успешно зарегистрированы на hh.ru.",
                        )
                        return True, True
                    elif status == "already_applied":
                        upd_status(v_id, "applied", cover_letter=cover_letter, auto_applied=True, method="already_applied", detail=hh_res.get("detail", "Отклик уже зарегистрирован на hh.ru"))
                        _send_platform_applied_alert(
                            send_msg, token, chat_id, v_id, clean_title, esc_title, esc_company, esc_salary, esc_letter,
                            score, link, "HeadHunter", "🔗 Открыть вакансию на HH", "Отклик на эту вакансию уже зарегистрирован в вашем личном кабинете на hh.ru.",
                            already_applied=True,
                        )
                        return True, True
                    elif status in ("test_required", "direct_required"):
                        is_test = (status == "test_required")
                        target_url = link if is_test else (hh_res.get("redirect_url") or link)
                        upd_status(v_id, "inbox", cover_letter=cover_letter, auto_prepared=True, **({"needs_test": True} if is_test else {"direct_url": target_url}))
                        if score >= notify_min_score and send_msg:
                            _send_action_required_alert(send_msg, token, chat_id, v_id, esc_title, esc_company, esc_salary, esc_letter, score, target_url, "🔴 <b>HeadHunter</b>", is_test)
                            return False, True
                        return False, False

            # 2. Хабр Карьера
            elif (v.get("source") in ["habr", "habrcareer"] or str(v_id).startswith("habr_")) and habr_cookie:
                habr_res = apply_habr_web(raw_id, cover_letter, habr_cookie)
                if habr_res:
                    status = habr_res.get("status")
                    if habr_res.get("ok"):
                        upd_status(v_id, "applied", cover_letter=cover_letter, auto_applied=True, method="habr_web", detail=habr_res.get("detail", ""))
                        _send_platform_applied_alert(
                            send_msg, token, chat_id, v_id, clean_title, esc_title, esc_company, esc_salary, esc_letter,
                            score, link, "Хабр Карьера", "🔗 Открыть вакансию на Хабре", "Резюме и письмо успешно зарегистрированы на career.habr.com.",
                        )
                        return True, True
                    elif status == "already_applied":
                        upd_status(v_id, "applied", cover_letter=cover_letter, auto_applied=True, method="already_applied", detail=habr_res.get("detail", "Отклик уже зарегистрирован на Хабр Карьере"))
                        _send_platform_applied_alert(
                            send_msg, token, chat_id, v_id, clean_title, esc_title, esc_company, esc_salary, esc_letter,
                            score, link, "Хабр Карьера", "🔗 Открыть вакансию на Хабре", "Отклик на эту вакансию уже зарегистрирован в вашем личном кабинете на career.habr.com.",
                            already_applied=True,
                        )
                        return True, True
                    elif status in ("test_required", "direct_required"):
                        is_test = (status == "test_required")
                        target_url = link if is_test else (habr_res.get("redirect_url") or link)
                        upd_status(v_id, "inbox", cover_letter=cover_letter, auto_prepared=True, **({"needs_test": True} if is_test else {"direct_url": target_url}))
                        if score >= notify_min_score and send_msg:
                            _send_action_required_alert(send_msg, token, chat_id, v_id, esc_title, esc_company, esc_salary, esc_letter, score, target_url, "🔵 <b>Хабр Карьера</b>", is_test)
                            return False, True
                        return False, False

            # 3. SuperJob
            elif (v.get("source") in ["superjob", "sj"] or str(v_id).startswith("sj_")) and (cfg.get("superjob_token") or cfg.get("superjob_cookie")):
                sj_res = apply_superjob_vacancy(raw_id, cover_letter, token=cfg.get("superjob_token"), cookie_str=cfg.get("superjob_cookie"), resume_id=cfg.get("superjob_resume_id"))
                if sj_res and sj_res.get("ok"):
                    upd_status(v_id, "applied", cover_letter=cover_letter, auto_applied=True, method="superjob_api", detail=sj_res.get("detail", ""))
                    _send_platform_applied_alert(
                        send_msg, token, chat_id, v_id, clean_title, esc_title, esc_company, esc_salary, esc_letter,
                        score, link, "SuperJob", "🔗 Открыть вакансию на SuperJob", "Отклик успешно зарегистрирован через SuperJob API.",
                    )
                    return True, True

            # 4. GetMatch
            elif (v.get("source") in ["getmatch", "gm"] or str(v_id).startswith("gm_")) and (cfg.get("getmatch_token") or cfg.get("getmatch_cookie")):
                gm_cred = cfg.get("getmatch_token") or cfg.get("getmatch_cookie")
                gm_res = apply_getmatch_vacancy(raw_id, cover_letter, gm_cred)
                if gm_res and gm_res.get("ok"):
                    upd_status(v_id, "applied", cover_letter=cover_letter, auto_applied=True, method="getmatch_api", detail=gm_res.get("detail", ""))
                    _send_platform_applied_alert(
                        send_msg, token, chat_id, v_id, clean_title, esc_title, esc_company, esc_salary, esc_letter,
                        score, link, "GetMatch", "🔗 Открыть вакансию на GetMatch", "Отклик успешно зарегистрирован через GetMatch API.",
                    )
                    return True, True

            # 5. Работа.ру
            elif (v.get("source") in ["rabota"] or str(v_id).startswith("rabota_")) and (cfg.get("rabota_cookie") or cfg.get("rabota_token")):
                rab_cred = cfg.get("rabota_cookie") or cfg.get("rabota_token")
                rab_res = apply_rabota_vacancy(raw_id, cover_letter, rab_cred)
                if rab_res and rab_res.get("ok"):
                    upd_status(v_id, "applied", cover_letter=cover_letter, auto_applied=True, method="rabota_api", detail=rab_res.get("detail", ""))
                    _send_platform_applied_alert(
                        send_msg, token, chat_id, v_id, clean_title, esc_title, esc_company, esc_salary, esc_letter,
                        score, link, "Работа.ру", "🔗 Открыть вакансию на Работа.ру", "Отклик успешно зарегистрирован через Работа.ру.",
                    )
                    return True, True

            # Fallback: сохранить подготовленное письмо во входящие
            upd_status(v_id, "inbox", cover_letter=cover_letter, auto_prepared=True)

            if score >= notify_min_score and send_msg:
                role = analysis.get("role", "java")
                role_badges = {
                    "vpn_dev": "🛡️ [VPN / Xray Core Dev]",
                    "ai_dev": "🤖 [AI & LLM Integration Developer]",
                    "java_ai": "🤖 [AI & LLM Integration Developer]",
                    "python_dev": "🐍 [Python Backend Developer]",
                    "backend": "⚡ [Junior Backend Developer]",
                    "fullstack_js": "🌐 [Fullstack JS / Frontend]",
                }
                role_badge = role_badges.get(role, "☕ [Java Backend]")

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
                        [action_btn, {"text": "✅ Отметить 'Откликнулся'", "callback_data": f"mark_applied_{v_id}"}],
                        [{"text": "🔄 Перегенерировать письмо", "callback_data": f"apply_{v_id}"}, {"text": "💼 Открыть в Mini App", "web_app": {"url": get_miniapp_url()}}],
                    ]
                })
                return False, True

            return False, False

    if score >= notify_min_score and send_msg:
        msg = format_ai_vacancy_message(v, analysis)
        send_msg(token, chat_id, msg, reply_markup={
            "inline_keyboard": [
                [{"text": "📝 Подготовить отклик", "callback_data": f"apply_{v_id}"}, {"text": f"🔗 Открыть на {source_name}", "url": link}],
                [{"text": "✅ Отметить 'Откликнулся'", "callback_data": f"mark_applied_{v_id}"}, {"text": "💼 Открыть в Mini App", "web_app": {"url": get_miniapp_url()}}],
            ]
        })
        return False, True

    return False, False
