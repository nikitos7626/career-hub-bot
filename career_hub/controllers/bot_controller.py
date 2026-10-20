"""
Telegram Bot Controller and Vacancy Monitoring Engine for Career Hub.
Handles incoming Telegram commands/callbacks, notifications, digests, and multi-source scraping loops.
"""

import os
import sys
import re
import json
import time
import logging
import threading
import requests

from career_hub.models.config import load_config, save_config, safe_int
from career_hub.models.storage import (
    load_seen_vacancies,
    seen_add,
    save_seen_vacancies,
    load_cache,
    save_to_cache,
    delete_from_cache,
    load_statuses,
    update_vacancy_status,
)
from career_hub.controllers.scrapers import (
    fetch_hh_vacancies,
    fetch_hh_vacancy_full,
    fetch_getmatch_vacancies,
    fetch_habr_vacancies,
    fetch_vseti_vacancies,
    fetch_remotive_vacancies,
    fetch_superjob_vacancies,
    fetch_linkedin_vacancies,
    fetch_linkedin_vacancy_full,
    fetch_tg_channel_vacancies,
    fetch_rabota_vacancies,
    fetch_rabota_vacancy_full,
    fetch_trudvsem_vacancies,
    fetch_vacancy_by_id,
    quick_prefilter_reject,
)
from career_hub.controllers.ai_controller import (
    analyze_vacancy_with_groq,
    generate_cover_letter,
)
from career_hub.controllers.auto_apply import (
    poll_hh_negotiations_and_notify,
    process_vacancy_auto_apply_and_notify,
    get_miniapp_url,
)
from career_hub.views.auth_view import (
    create_user_session,
    create_one_time_token,
)
from career_hub.views.http_server import (
    pending_logins,
    pending_logins_lock,
)

logger = logging.getLogger("career_hub.controllers.bot")


def _get_bot_ref(attr_name, fallback):
    bot_mod = sys.modules.get("bot") or sys.modules.get("__main__")
    if bot_mod and hasattr(bot_mod, attr_name):
        return getattr(bot_mod, attr_name)
    bot_ctrl = sys.modules.get("career_hub.controllers.bot_controller")
    if bot_ctrl and hasattr(bot_ctrl, attr_name):
        return getattr(bot_ctrl, attr_name)
    return fallback


def send_message(token, chat_id, text, reply_markup=None):
    if not token:
        logger.warning("Невозможно отправить сообщение в Telegram: bot_token не настроен")
        return None
    cid = safe_int(chat_id)
    if not cid:
        logger.info("Ожидание первого старта диалога от владельца в Telegram (chat_id не настроен). Отправьте /start боту.")
        return None

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": cid,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        req_post = _get_bot_ref("requests", requests)
        resp = req_post.post(url, json=payload, timeout=10)
        if resp.status_code == 200:
            return resp.json()

        # Fallback: if HTML parsing failed, retry once as plain text without HTML tags
        if resp.status_code == 400 and "can't parse entities" in resp.text.lower():
            logger.warning("Telegram parse error, retrying without HTML parse_mode...")
            plain_text = re.sub(r'<[^>]+>', '', text)
            payload["text"] = plain_text
            payload.pop("parse_mode", None)
            retry_resp = req_post.post(url, json=payload, timeout=10)
            if retry_resp.status_code == 200:
                return retry_resp.json()
            logger.error(f"Telegram API fallback ошибка ({retry_resp.status_code}): {retry_resp.text}")
            return None

        logger.error(f"Telegram API ошибка ({resp.status_code}): {resp.text}")
    except Exception as e:
        logger.error(f"Ошибка отправки в Telegram: {e}")
    return None


def edit_message_text(token, chat_id, message_id, text, reply_markup=None):
    if not token:
        return None
    cid = safe_int(chat_id)
    if not cid:
        return None

    url = f"https://api.telegram.org/bot{token}/editMessageText"
    payload = {
        "chat_id": cid,
        "message_id": message_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        req_post = _get_bot_ref("requests", requests)
        resp = req_post.post(url, json=payload, timeout=10)
        if resp.status_code == 200:
            return resp.json()

        # Fallback if HTML parsing fails
        if resp.status_code == 400 and "can't parse entities" in resp.text.lower():
            logger.warning("Telegram edit parse error, retrying without HTML parse_mode...")
            plain_text = re.sub(r'<[^>]+>', '', text)
            payload["text"] = plain_text
            payload.pop("parse_mode", None)
            retry_resp = req_post.post(url, json=payload, timeout=10)
            if retry_resp.status_code == 200:
                return retry_resp.json()

        logger.error(f"Telegram API editMessageText ошибка ({resp.status_code}): {resp.text}")
    except Exception as e:
        logger.error(f"Ошибка editMessageText: {e}")
    return None


def edit_message_reply_markup(token, chat_id, message_id, reply_markup):
    if not token:
        return None
    cid = safe_int(chat_id)
    if not cid:
        return None

    url = f"https://api.telegram.org/bot{token}/editMessageReplyMarkup"
    payload = {
        "chat_id": cid,
        "message_id": message_id,
        "reply_markup": reply_markup
    }
    try:
        req_post = _get_bot_ref("requests", requests)
        resp = req_post.post(url, json=payload, timeout=10)
        if resp.status_code == 200:
            return resp.json()
        logger.error(f"Telegram API editMessageReplyMarkup ошибка ({resp.status_code}): {resp.text}")
    except Exception as e:
        logger.error(f"Ошибка editMessageReplyMarkup: {e}")
    return None


def answer_callback_query(token, callback_query_id, text=None, show_alert=False):
    if not token or not callback_query_id:
        return None
    url = f"https://api.telegram.org/bot{token}/answerCallbackQuery"
    payload = {"callback_query_id": callback_query_id, "show_alert": show_alert}
    if text:
        payload["text"] = text
    try:
        req_post = _get_bot_ref("requests", requests)
        req_post.post(url, json=payload, timeout=5)
    except Exception:
        pass


def check_and_send_scheduled_digest(bot_token, chat_id, config):
    """Отправляет тихую сводку раз в 24 часа по накопленным подходящим вакансиям (скор 6-8)."""
    cfg_fn = _get_bot_ref("load_config", load_config)
    save_cfg_fn = _get_bot_ref("save_config", save_config)
    load_cache_fn = _get_bot_ref("load_cache", load_cache)
    load_stat_fn = _get_bot_ref("load_statuses", load_statuses)
    send_msg_fn = _get_bot_ref("send_message", send_message)

    last_time = config.get("last_digest_time") if config else None
    now = time.time()
    
    # Если запуск первый и таймстемп не инициализирован вовсе, фиксируем текущее время без спама
    if last_time is None:
        cfg = cfg_fn()
        cfg["last_digest_time"] = now
        save_cfg_fn(cfg)
        if isinstance(config, dict):
            config["last_digest_time"] = now
        return

    try:
        if now - float(last_time) < 86400:
            return
    except (TypeError, ValueError):
        return

    cache = load_cache_fn()
    statuses = load_stat_fn()
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
                [{"text": "💼 Открыть Career Hub Mini App", "web_app": {"url": get_miniapp_url()}}]
            ]
        }
        send_msg_fn(bot_token, chat_id, text, reply_markup=reply_markup)

    cur_cfg = cfg_fn()
    cur_cfg["last_digest_time"] = now
    save_cfg_fn(cur_cfg)
    if isinstance(config, dict):
        config["last_digest_time"] = now


def telegram_updates_worker():
    logger.info("Запуск обработчика входящих событий Telegram...")
    cfg_fn = _get_bot_ref("load_config", load_config)
    save_cfg_fn = _get_bot_ref("save_config", save_config)
    send_msg_fn = _get_bot_ref("send_message", send_message)
    edit_msg_fn = _get_bot_ref("edit_message_text", edit_message_text)
    edit_markup_fn = _get_bot_ref("edit_message_reply_markup", edit_message_reply_markup)
    ans_cb_fn = _get_bot_ref("answer_callback_query", answer_callback_query)
    fetch_v_fn = _get_bot_ref("fetch_vacancy_by_id", fetch_vacancy_by_id)
    gen_letter_fn = _get_bot_ref("generate_cover_letter", generate_cover_letter)
    update_stat_fn = _get_bot_ref("update_vacancy_status", update_vacancy_status)
    create_sess_fn = _get_bot_ref("create_user_session", create_user_session)
    create_ot_fn = _get_bot_ref("create_one_time_token", create_one_time_token)
    poll_hh_fn = _get_bot_ref("poll_hh_negotiations_and_notify", poll_hh_negotiations_and_notify)

    offset = 0
    consecutive_errors = 0
    last_waiting_log = 0.0

    while True:
        try:
            config = cfg_fn()
            token = (config.get("bot_token") or os.environ.get("BOT_TOKEN", "")).strip()
            chat_id_cfg = safe_int(config.get("chat_id", 0))
            groq_key = config.get("groq_api_key", "")
            groq_model = config.get("groq_model", "llama-3.3-70b-versatile")

            if not token:
                logger.warning("Telegram polling: bot_token не настроен. Ожидание настройки bot_token (пауза 10 сек)...")
                time.sleep(10)
                continue

            now = time.time()
            if not chat_id_cfg and (now - last_waiting_log > 60):
                logger.info("Ожидание первого старта диалога от владельца (/start в боте Telegram)...")
                last_waiting_log = now

            url = f"https://api.telegram.org/bot{token}/getUpdates"
            payload = {
                "offset": offset,
                "timeout": 20,
                "allowed_updates": ["message", "callback_query"]
            }
            req_post = _get_bot_ref("requests", requests)
            try:
                resp = req_post.post(url, json=payload, timeout=25)
            except requests.exceptions.Timeout:
                time.sleep(0.5)
                continue
            except Exception as conn_err:
                consecutive_errors += 1
                backoff = min(30, 2 ** min(consecutive_errors, 5))
                logger.warning(f"Сбой соединения при получении Telegram updates ({conn_err}). Пауза {backoff} сек...")
                time.sleep(backoff)
                continue

            if resp.status_code == 200:
                consecutive_errors = 0
                try:
                    data = resp.json()
                except Exception:
                    data = None

                if not data or not data.get("ok"):
                    logger.warning("Telegram getUpdates вернул некорректный ответ.")
                    time.sleep(2)
                    continue

                updates = data.get("result", [])
                if not updates:
                    time.sleep(0.5)
                    continue

                for update in updates:
                    offset = update["update_id"] + 1

                    if "callback_query" in update:
                        cb = update["callback_query"]
                        cb_id = cb["id"]
                        cb_data = cb.get("data", "")
                        from_user = safe_int(cb.get("from", {}).get("id"))
                        message = cb.get("message", {})
                        chat_id = safe_int(message.get("chat", {}).get("id", from_user))

                        cfg = cfg_fn()
                        allowed_owner_id = safe_int(cfg.get("allowed_telegram_id") or cfg.get("chat_id") or os.environ.get("ALLOWED_TELEGRAM_ID", "0"))
                        if allowed_owner_id and from_user != allowed_owner_id and chat_id != allowed_owner_id:
                            logger.warning(f"Попытка доступа от неизвестного пользователя {from_user} в callback {cb_data}")
                            ans_cb_fn(token, cb_id, "⛔ Доступ запрещён", show_alert=True)
                            continue

                        logger.info(f"Получен callback_query: '{cb_data}' от пользователя {from_user}")

                        if cb_data == "send_login_link":
                            ans_cb_fn(token, cb_id, "Генерация ссылки...")
                            ot_token = create_ot_fn(from_user)
                            login_url = f"{get_miniapp_url().rstrip('/')}/?auth={ot_token}"
                            send_msg_fn(token, chat_id, 
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
                            ans_cb_fn(token, cb_id, "⏳ Groq AI составляет отклик...")

                            vacancy = fetch_v_fn(v_id)

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
                                send_msg_fn(token, chat_id, not_found_text)
                                continue

                            status_text = (
                                f"⏳ <b>Идёт составление отклика...</b>\n\n"
                                f"💼 <b>Позиция:</b> {clean_title}\n"
                                f"🏢 <b>Компания:</b> {company}\n"
                                f"🌐 <b>Источник:</b> {source_name}\n\n"
                                f"🤖 <i>Groq AI анализирует требования вакансии и адаптирует ваше сопроводительное письмо... (2-4 сек)</i>"
                            )
                            status_msg = send_msg_fn(token, chat_id, status_text)
                            status_msg_id = status_msg.get("result", {}).get("message_id") if status_msg else None

                            try:
                                cover_letter = gen_letter_fn(groq_key, groq_model, vacancy)

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
                                            {"text": "💼 Открыть в Mini App", "web_app": {"url": get_miniapp_url()}}
                                        ]
                                    ]
                                }

                                if status_msg_id:
                                    edit_msg_fn(token, chat_id, status_msg_id, response_text, reply_markup=reply_markup)
                                else:
                                    send_msg_fn(token, chat_id, response_text, reply_markup=reply_markup)

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
                                    edit_msg_fn(token, chat_id, status_msg_id, error_text, reply_markup=error_markup)
                                else:
                                    send_msg_fn(token, chat_id, error_text, reply_markup=error_markup)

                        elif cb_data.startswith("mark_applied_"):
                            v_id = cb_data.replace("mark_applied_", "")
                            update_stat_fn(v_id, "applied")
                            ans_cb_fn(token, cb_id, "Статус обновлен на 'Откликнулся' ✅")
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
                                    edit_markup_fn(token, chat_id, msg_id, {"inline_keyboard": new_kb})
                                except Exception:
                                    pass
                            continue

                        elif cb_data == "hh_check_now":
                            ans_cb_fn(token, cb_id, "Проверка откликов на HeadHunter...")
                            cfg = cfg_fn()
                            hh_tok = cfg.get("hh_access_token", "")
                            hh_cookie = cfg.get("hh_web_cookie", "")
                            if not hh_tok and not hh_cookie:
                                send_msg_fn(token, chat_id, "ℹ️ HeadHunter не настроен. Используйте /set_hh_cookie <cookie> или /set_hh_token <токен>.")
                            else:
                                poll_hh_fn(token, chat_id, access_token=hh_tok, cookie_str=hh_cookie)
                                send_msg_fn(token, chat_id, "✅ Проверка HeadHunter завершена.")
                            continue

                        elif cb_data == "btn_clean_discards":
                            ans_cb_fn(token, cb_id, "Очистка отказов...")
                            cfg = cfg_fn()
                            hh_tok = cfg.get("hh_access_token", "")
                            hh_cookie = cfg.get("hh_web_cookie", "")
                            if not hh_tok and not hh_cookie:
                                send_msg_fn(token, chat_id, "ℹ️ HeadHunter не настроен. Используйте /set_hh_cookie <cookie>.")
                            else:
                                poll_hh_fn(token, chat_id, access_token=hh_tok, cookie_str=hh_cookie)
                                send_msg_fn(token, chat_id, "✨ <b>Очистка завершена!</b> Все отказы удалены из профиля HeadHunter и скрыты из истории.")
                            continue

                        elif cb_data == "btn_settings":
                            ans_cb_fn(token, cb_id)
                            cfg = cfg_fn()
                            auto_on = cfg.get("auto_apply_enabled", True)
                            auto_score = cfg.get("auto_apply_min_score", 7)
                            notify_score = cfg.get("notify_min_score", 9)
                            hh_tok = bool(cfg.get("hh_access_token", "").strip())
                            cache = _get_bot_ref("load_cache", load_cache)()
                            statuses = _get_bot_ref("load_statuses", load_statuses)()
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
                            send_msg_fn(token, chat_id, text, reply_markup={
                                "inline_keyboard": [
                                    [{"text": "💼 Открыть Career Hub Mini App", "web_app": {"url": get_miniapp_url()}}],
                                    [{"text": "🔑 Вход на ПК", "callback_data": "send_login_link"}]
                                ]
                            })
                            continue

                    elif "message" in update:
                        msg = update["message"]
                        msg_text = msg.get("text", "").strip()
                        msg_chat_id = safe_int(msg.get("chat", {}).get("id"))
                        from_user = safe_int(msg.get("from", {}).get("id", msg_chat_id))

                        cfg = cfg_fn()
                        allowed_owner_id = safe_int(cfg.get("allowed_telegram_id") or cfg.get("chat_id") or os.environ.get("ALLOWED_TELEGRAM_ID", "0"))

                        if allowed_owner_id and from_user != allowed_owner_id and msg_chat_id != allowed_owner_id:
                            logger.warning(f"Попытка доступа от неизвестного пользователя {from_user} (chat {msg_chat_id})")
                            send_msg_fn(token, msg_chat_id, "⛔ <b>Доступ запрещён</b>\n\nЭтот бот и веб-панель Career Hub настроены исключительно для владельца.")
                            continue
                        elif not allowed_owner_id and from_user:
                            cfg["allowed_telegram_id"] = from_user
                            cfg["chat_id"] = msg_chat_id
                            save_cfg_fn(cfg)
                            logger.info(f"Владелец бота успешно зарегистрирован: ID {from_user}, chat_id {msg_chat_id}")

                        if not cfg.get("chat_id") or safe_int(cfg.get("chat_id")) != msg_chat_id:
                            cfg["chat_id"] = msg_chat_id
                            save_cfg_fn(cfg)
                            logger.info(f"Диалог с владельцем успешно инициирован! Привязан chat_id={msg_chat_id}")

                        # Подтверждение сессии браузера через глубокую ссылку /start login_<session_id>
                        p_logins = _get_bot_ref("pending_logins", pending_logins)
                        p_lock = _get_bot_ref("pending_logins_lock", pending_logins_lock)

                        if msg_text.startswith("/start login_") or msg_text.startswith("/start auth_"):
                            session_id = msg_text.replace("/start login_", "").replace("/start auth_", "").strip()
                            with p_lock:
                                pending = p_logins.get(session_id)
                                if pending:
                                    perm_token, expires_at = create_sess_fn(from_user, device="Desktop PC", days=30)
                                    pending["approved"] = True
                                    pending["token"] = perm_token
                                    send_msg_fn(token, msg_chat_id, 
                                        "✅ <b>Вход в Career Hub подтверждён!</b>\n\n"
                                        "🖥️ Доступ на вашем компьютере успешно активирован на <b>30 дней</b>.\n"
                                        "Окно в браузере обновится автоматически через секунду.",
                                        reply_markup={
                                            "inline_keyboard": [
                                                [{"text": "💼 Открыть в Mini App", "web_app": {"url": get_miniapp_url()}}]
                                            ]
                                        }
                                    )
                                else:
                                    send_msg_fn(token, msg_chat_id, "⚠️ <b>Сессия авторизации не найдена или истекла.</b>\n\nОбновите страницу входа в браузере и попробуйте снова.")
                            continue

                        # Подтверждение по 6-значному PIN коду
                        if re.fullmatch(r"^\d{6}$", msg_text):
                            matched_session_id = None
                            with p_lock:
                                for s_id, s_data in p_logins.items():
                                    if s_data.get("code") == msg_text and not s_data.get("approved"):
                                        matched_session_id = s_id
                                        perm_token, expires_at = create_sess_fn(from_user, device="Desktop PC", days=30)
                                        s_data["approved"] = True
                                        s_data["token"] = perm_token
                                        break
                            if matched_session_id:
                                send_msg_fn(token, msg_chat_id, 
                                    f"✅ <b>Код {msg_text} подтверждён!</b>\n\n"
                                    "🖥️ Вход на компьютере выполнен успешно! Сессия сохранена на <b>30 дней</b>.",
                                    reply_markup={
                                        "inline_keyboard": [
                                            [{"text": "💼 Открыть в Mini App", "web_app": {"url": get_miniapp_url()}}]
                                        ]
                                    }
                                )
                                continue
                            else:
                                send_msg_fn(token, msg_chat_id, f"⚠️ Не найдено активных запросов на вход с кодом <code>{msg_text}</code>.")
                                continue

                        # Быстрая ссылка на вход через команду /login или /web
                        if msg_text in ["/login", "/web"]:
                            ot_token = create_ot_fn(from_user)
                            login_url = f"{get_miniapp_url().rstrip('/')}/?auth={ot_token}"
                            send_msg_fn(token, msg_chat_id, 
                                "🔑 <b>Ссылка для входа в Career Hub на ПК:</b>\n\n"
                                f"👉 <b><a href='{login_url}'>Нажмите сюда для автоматического входа</a></b>\n\n"
                                f"Или скопируйте:\n<code>{login_url}</code>\n\n"
                                "⏳ Ссылка активна 10 минут. После перехода авторизация сохранится на этом компьютере на <b>30 дней</b>.",
                                reply_markup={
                                    "inline_keyboard": [
                                        [{"text": "🚀 Войти в браузере", "url": login_url}],
                                        [{"text": "💼 Открыть в Mini App", "web_app": {"url": get_miniapp_url()}}]
                                    ]
                                }
                            )
                            continue

                        if msg_text.startswith("/auto_apply"):
                            parts = msg_text.split()
                            cfg = cfg_fn()
                            if len(parts) > 1:
                                arg = parts[1].lower()
                                if arg in ["on", "1", "true", "вкл", "да"]:
                                    cfg["auto_apply_enabled"] = True
                                elif arg in ["off", "0", "false", "выкл", "нет"]:
                                    cfg["auto_apply_enabled"] = False
                                save_cfg_fn(cfg)
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
                            send_msg_fn(token, msg_chat_id, resp_text)
                            continue

                        if msg_text.startswith("/notify_score"):
                            parts = msg_text.split()
                            cfg = cfg_fn()
                            if len(parts) > 1:
                                try:
                                    val = int(parts[1])
                                    if 1 <= val <= 10:
                                        cfg["notify_min_score"] = val
                                        save_cfg_fn(cfg)
                                        send_msg_fn(token, msg_chat_id, 
                                            f"🔔 <b>Порог уведомлений в Telegram обновлён: {val}/10!</b>\n\n"
                                            f"Теперь бот будет присылать мгновенные уведомления только для вакансий с оценкой <b>{val} и выше</b> (топ-матчи).\n"
                                            f"Все вакансии с оценкой от 6 до {val-1} будут тихо сохраняться в Career Hub без спама."
                                        )
                                        continue
                                    else:
                                        send_msg_fn(token, msg_chat_id, "⚠️ Укажите число от 1 до 10 (например: <code>/notify_score 9</code>).")
                                        continue
                                except ValueError:
                                    send_msg_fn(token, msg_chat_id, "⚠️ Укажите число от 1 до 10 (например: <code>/notify_score 9</code>).")
                                    continue
                            cur_val = cfg.get("notify_min_score", 9)
                            send_msg_fn(token, msg_chat_id, 
                                f"🔔 <b>Текущий порог мгновенных уведомлений в Telegram: {cur_val}/10</b>\n\n"
                                f"Вакансии ниже {cur_val} баллов тихо сохраняются в Mini App (без спама в чат).\n"
                                f"Чтобы изменить порог, отправьте: <code>/notify_score 8</code> или <code>/notify_score 9</code>."
                            )
                            continue

                        if msg_text.startswith("/auto_score"):
                            parts = msg_text.split()
                            cfg = cfg_fn()
                            if len(parts) > 1:
                                try:
                                    val = int(parts[1])
                                    if 1 <= val <= 10:
                                        cfg["auto_apply_min_score"] = val
                                        save_cfg_fn(cfg)
                                        send_msg_fn(token, msg_chat_id, 
                                            f"🎯 <b>Порог скора для авто-отклика установлен на {val}/10!</b>\n\n"
                                            f"Вакансии с оценкой {val} и выше будут автоматически получать подготовленное сопроводительное письмо и отправляться через API."
                                        )
                                        continue
                                    else:
                                        send_msg_fn(token, msg_chat_id, "⚠️ Укажите число от 1 до 10.")
                                        continue
                                except ValueError:
                                    send_msg_fn(token, msg_chat_id, "⚠️ Укажите число от 1 до 10.")
                                    continue
                            cur_val = cfg.get("auto_apply_min_score", 7)
                            send_msg_fn(token, msg_chat_id, f"🎯 Порог скора для авто-отклика: <b>{cur_val}/10</b>. Для изменения: <code>/auto_score 7</code>.")
                            continue

                        if msg_text.startswith("/set_hh_cookie"):
                            parts = msg_text.split(maxsplit=1)
                            if len(parts) < 2 or not parts[1].strip():
                                send_msg_fn(token, msg_chat_id, 
                                    "ℹ️ <b>Подключение HeadHunter через веб-сессию (Cookie)</b>\n\n"
                                    "Позволяет откликаться и отслеживать статусы без регистрации приложения API.\n\n"
                                    "<b>Как получить куки:</b>\n"
                                    "1. Откройте <code>hh.ru</code> на ПК в Chrome/Firefox/Edge и войдите в аккаунт.\n"
                                    "2. Нажмите <code>F12</code> -> вкладка <b>Сеть (Network)</b>.\n"
                                    "3. Обновите страницу и кликните на первый запрос <code>hh.ru</code>.\n"
                                    "4. В заголовках запроса найдите <code>Cookie</code> и скопируйте значение целиком.\n\n"
                                    "Отправьте команду в формате:\n<code>/set_hh_cookie hhtoken=...; _xsrf=...</code>"
                                )
                                continue

                            cookie_val = parts[1].strip()
                            check_cookie_fn = _get_bot_ref("check_hh_web_cookie", check_hh_web_cookie)
                            check_info = check_cookie_fn(cookie_val)
                            if check_info and check_info.get("ok"):
                                cfg = cfg_fn()
                                cfg["hh_web_cookie"] = cookie_val
                                if check_info.get("resumes"):
                                    cfg["hh_resume_hash"] = check_info["resumes"][0]
                                save_cfg_fn(cfg)
                                send_msg_fn(token, msg_chat_id, 
                                    f"✅ <b>Сессия HeadHunter успешно подключена!</b>\n\n"
                                    f"👤 Пользователь: <b>{check_info.get('name', 'Кандидат')}</b>\n"
                                    f"📄 Активных резюме: <b>{len(check_info.get('resumes', []))}</b>\n\n"
                                    "Теперь авто-отклики и проверка приглашений будут работать в фоновом режиме!"
                                )
                            else:
                                send_msg_fn(token, msg_chat_id, "❌ Не удалось авторизоваться на hh.ru с переданными Cookie. Убедитесь, что сессия активна и содержит <code>hhtoken</code>.")
                            continue

                        if msg_text.startswith("/set_hh_token"):
                            parts = msg_text.split(maxsplit=1)
                            if len(parts) < 2 or not parts[1].strip():
                                send_msg_fn(token, msg_chat_id, "ℹ️ Использование: <code>/set_hh_token &lt;ваш_access_token&gt;</code>")
                                continue
                            new_token = parts[1].strip().strip('"').strip("'")
                            cfg = cfg_fn()
                            cfg["hh_access_token"] = new_token
                            save_cfg_fn(cfg)
                            send_msg_fn(token, msg_chat_id, "✅ HeadHunter access token сохранён!")
                            continue

                        if msg_text in ["/digest", "/summary"]:
                            cache = _get_bot_ref("load_cache", load_cache)()
                            statuses = _get_bot_ref("load_statuses", load_statuses)()
                            cfg = cfg_fn()
                            notify_min_score = cfg.get("notify_min_score", 9)
                            quiet_vacs = []
                            for v_id, v in cache.items():
                                score = v.get("match_score", 0)
                                if 6 <= score < notify_min_score:
                                    st = statuses.get(v_id, {}).get("status", "inbox")
                                    if st != "archived":
                                        quiet_vacs.append(v)
                            send_msg_fn(token, msg_chat_id, 
                                f"📊 <b>Сводка подходящих вакансий:</b>\n\n"
                                f"В кэше накоплено <b>{len(quiet_vacs)}</b> вакансий со скором 6-{notify_min_score-1}.\n"
                                f"Всего вакансий в базе: <b>{len(cache)}</b>\n\n"
                                f"Откройте веб-приложение для просмотра деталей.",
                                reply_markup={
                                    "inline_keyboard": [
                                        [{"text": "💼 Открыть Career Hub Mini App", "web_app": {"url": get_miniapp_url()}}]
                                    ]
                                }
                            )
                            continue

                        if msg_text in ["/status", "/settings", "/start"]:
                            cfg = cfg_fn()
                            auto_on = cfg.get("auto_apply_enabled", True)
                            auto_score = cfg.get("auto_apply_min_score", 7)
                            notify_score = cfg.get("notify_min_score", 9)
                            hh_tok = bool(cfg.get("hh_access_token", "").strip())
                            hh_cookie = bool(cfg.get("hh_web_cookie", "").strip())
                            cache = _get_bot_ref("load_cache", load_cache)()
                            statuses = _get_bot_ref("load_statuses", load_statuses)()
                            applied_count = sum(1 for s in statuses.values() if s.get("status") == "applied")

                            hh_status = "🟢 Веб-сессия активна" if hh_cookie else ("🟢 API подключён" if hh_tok else "⚪ Не подключён")

                            welcome_text = (
                                f"👋 <b>Добро пожаловать в Career Hub Bot!</b>\n\n"
                                f"🤖 <b>Авто-отклики:</b> {'🟢 Включены' if auto_on else '🔴 Выключены'} (порог: {auto_score}+)\n"
                                f"🔔 <b>Уведомления в чат:</b> Только топ ({notify_score}-10/10)\n"
                                f"🔌 <b>HeadHunter:</b> {hh_status}\n\n"
                                f"📊 <b>База вакансий:</b> {len(cache)} сохранено | {applied_count} откликов\n\n"
                                f"👇 Нажмите кнопку ниже, чтобы открыть веб-интерфейс:"
                            )
                            reply_markup = {
                                "inline_keyboard": [
                                    [{"text": "💼 Открыть Career Hub Mini App", "web_app": {"url": get_miniapp_url()}}],
                                    [{"text": "🔑 Войти на ПК", "callback_data": "send_login_link"}, {"text": "🔄 Проверить HH", "callback_data": "hh_check_now"}],
                                    [{"text": "🧹 Очистить отказы", "callback_data": "btn_clean_discards"}, {"text": "⚙️ Настройки", "callback_data": "btn_settings"}]
                                ]
                            }
                            send_msg_fn(token, msg_chat_id, welcome_text, reply_markup=reply_markup)
                            continue

            elif resp.status_code in (401, 404):
                consecutive_errors += 1
                logger.error(f"Неверный токен Telegram (HTTP {resp.status_code}). Проверьте bot_token в конфигурации. Пауза 10 сек...")
                time.sleep(10)
            elif resp.status_code == 429:
                consecutive_errors += 1
                retry_after = 5
                try:
                    retry_after = int(resp.json().get("parameters", {}).get("retry_after", 5))
                except Exception:
                    pass
                logger.warning(f"Telegram Rate Limit (HTTP 429). Ожидание {retry_after} сек...")
                time.sleep(retry_after)
            elif resp.status_code == 409:
                consecutive_errors += 1
                logger.warning("Конфликт getUpdates: уже запущен другой экземпляр бота (HTTP 409). Пауза 5 сек...")
                time.sleep(5)
            else:
                consecutive_errors += 1
                backoff = min(30, 2 ** min(consecutive_errors, 5))
                logger.warning(f"Ошибка Telegram getUpdates (HTTP {resp.status_code}): {resp.text[:200]}. Пауза {backoff} сек...")
                time.sleep(backoff)

        except Exception as e:
            consecutive_errors += 1
            backoff = min(30, 2 ** min(consecutive_errors, 5))
            logger.error(f"Ошибка в цикле telegram_updates_worker: {e}. Пауза {backoff} сек...")
            time.sleep(backoff)


def vacancy_monitor_worker():
    logger.info("Запуск фонового мониторинга вакансий со всех источников...")
    cfg_fn = _get_bot_ref("load_config", load_config)
    load_seen_fn = _get_bot_ref("load_seen_vacancies", load_seen_vacancies)
    save_seen_fn = _get_bot_ref("save_seen_vacancies", save_seen_vacancies)
    seen_add_fn = _get_bot_ref("seen_add", seen_add)
    delete_cache_fn = _get_bot_ref("delete_from_cache", delete_from_cache)
    save_cache_fn = _get_bot_ref("save_to_cache", save_to_cache)
    prefilter_fn = _get_bot_ref("quick_prefilter_reject", quick_prefilter_reject)
    ai_analyze_fn = _get_bot_ref("analyze_vacancy_with_groq", analyze_vacancy_with_groq)
    process_apply_fn = _get_bot_ref("process_vacancy_auto_apply_and_notify", process_vacancy_auto_apply_and_notify)
    poll_hh_fn = _get_bot_ref("poll_hh_negotiations_and_notify", poll_hh_negotiations_and_notify)
    digest_fn = _get_bot_ref("check_and_send_scheduled_digest", check_and_send_scheduled_digest)
    fetch_v_fn = _get_bot_ref("fetch_vacancy_by_id", fetch_vacancy_by_id)

    seen_vacancies = load_seen_fn()
    logger.info(f"Загружено {len(seen_vacancies)} ранее сохраненных вакансий.")

    while True:
        logger.info("Начало цикла сбора вакансий со всех подключенных платформ...")
        config = cfg_fn()
        token = config.get("bot_token", "")
        chat_id = safe_int(config.get("chat_id", 0))
        groq_key = config.get("groq_api_key", "")
        groq_model = config.get("groq_model", "llama-3.3-70b-versatile")
        min_score = config.get("min_match_score", 6)
        interval = config.get("check_interval_seconds", 600)
        max_analyses = config.get("max_ai_analyses_per_cycle", 35)
        ai_delay = config.get("ai_delay_seconds", 2.8)

        if not chat_id:
            logger.info("Мониторинг вакансий: ожидание первого старта диалога от владельца (/start в боте Telegram)...")

        sent_count = 0
        quiet_count = 0
        ai_analyses_count = 0

        # Проверка приглашений, ответов и очистка отказов на HeadHunter
        hh_token = config.get("hh_access_token", "").strip()
        hh_cookie = config.get("hh_web_cookie", "").strip()
        if hh_token or hh_cookie:
            poll_hh_fn(token, chat_id, access_token=hh_token, cookie_str=hh_cookie)

        # Проверка необходимости отправки тихой сводки за 24 часа
        if config.get("daily_digest_enabled", True):
            digest_fn(token, chat_id, config)

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

        # 1. Сбор с HeadHunter (hh.ru)
        if config.get("enable_hh", True):
            for hh_item in config.get("hh_queries", []):
                q = hh_item.get("query", "")
                exp = hh_item.get("experience")
                sched = hh_item.get("schedule")
                vacs = _get_bot_ref("fetch_hh_vacancies", fetch_hh_vacancies)(q, experience=exp, schedule=sched)
                by_source["hh"].extend(vacs)

        # 2. Сбор с GetMatch
        if config.get("enable_getmatch", True):
            gm_vacs = _get_bot_ref("fetch_getmatch_vacancies", fetch_getmatch_vacancies)()
            by_source["getmatch"].extend(gm_vacs)

        # 3. Сбор с Хабр Карьеры
        if config.get("enable_habr", True):
            for query_url in config.get("habr_queries", []):
                vacs = _get_bot_ref("fetch_habr_vacancies", fetch_habr_vacancies)(query_url)
                by_source["habr"].extend(vacs)

        # 4. Сбор с СЕТИ (vseti.app)
        if config.get("enable_vseti", True):
            vseti_vacs = _get_bot_ref("fetch_vseti_vacancies", fetch_vseti_vacancies)(config.get("vseti_pages", 6))
            by_source["vseti"].extend(vseti_vacs)

        # 5. Сбор с Remotive (Remote)
        if config.get("enable_remotive", True):
            rem_vacs = _get_bot_ref("fetch_remotive_vacancies", fetch_remotive_vacancies)()
            by_source["remotive"].extend(rem_vacs)

        # 6. Сбор с SuperJob
        if config.get("enable_superjob", True) and config.get("superjob_secret_key"):
            sj_key = config.get("superjob_secret_key")
            for sj_q in config.get("superjob_queries", []):
                by_source["superjob"].extend(_get_bot_ref("fetch_superjob_vacancies", fetch_superjob_vacancies)(sj_q, secret_key=sj_key))

        # 7. Сбор с LinkedIn (Public Guest Search)
        if config.get("enable_linkedin", True):
            for li_q in config.get("linkedin_queries", []):
                by_source["linkedin"].extend(_get_bot_ref("fetch_linkedin_vacancies", fetch_linkedin_vacancies)(li_q))
                time.sleep(2.0)

        # 8. Сбор с профильных Telegram-каналов (IT / Java / Junior)
        if config.get("enable_tg_channels", True):
            for tg_ch in config.get("tg_channels", []):
                by_source["telegram"].extend(_get_bot_ref("fetch_tg_channel_vacancies", fetch_tg_channel_vacancies)(tg_ch))

        # 9. Сбор с Работа.ру (rabota.ru)
        if config.get("enable_rabota", True):
            for rab_q in config.get("rabota_queries", [
                "стажер java",
                "junior java",
                "java разработчик",
                "java",
                "spring"
            ]):
                by_source["rabota"].extend(_get_bot_ref("fetch_rabota_vacancies", fetch_rabota_vacancies)(rab_q))

        # 10. Сбор с Работа России (opendata.trudvsem.ru)
        if config.get("enable_trudvsem", False):
            for trud_q in config.get("trudvsem_queries", ["java", "spring"]):
                by_source["trudvsem"].extend(_get_bot_ref("fetch_trudvsem_vacancies", fetch_trudvsem_vacancies)(trud_q))

        logger.info(f"Собрано сырых вакансий: HH={len(by_source['hh'])}, GetMatch={len(by_source['getmatch'])}, Habr={len(by_source['habr'])}, СЕТИ={len(by_source['vseti'])}, Remotive={len(by_source['remotive'])}, SuperJob={len(by_source['superjob'])}, LinkedIn={len(by_source['linkedin'])}, TG={len(by_source['telegram'])}, Работа={len(by_source['rabota'])}, ТрудВсем={len(by_source['trudvsem'])}")

        # Round-Robin чередование
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

            # Быстрый отсев
            if prefilter_fn(v["title"], v.get("description", ""), v.get("company", "")):
                seen_add_fn(seen_vacancies, v_id)
                delete_cache_fn(v_id)
                continue

            if ai_analyses_count >= max_analyses:
                logger.info(f"Достигнут лимит анализов ({max_analyses}) за цикл.")
                break

            # Подтягивание деталей
            if v.get("source") == "hh" and len(v.get("description", "")) < 100:
                time.sleep(0.3)
                full_v = _get_bot_ref("fetch_hh_vacancy_full", fetch_hh_vacancy_full)(v.get("raw_id"))
                if full_v and full_v.get("description"):
                    v["description"] = full_v["description"]
                    if full_v.get("salary") and not v.get("salary"):
                        v["salary"] = full_v["salary"]

            if v.get("source") == "linkedin" and len(v.get("description", "")) < 200:
                time.sleep(0.4)
                full_li = _get_bot_ref("fetch_linkedin_vacancy_full", fetch_linkedin_vacancy_full)(v.get("raw_id"))
                if full_li:
                    v["description"] = full_li

            if v.get("source") == "rabota" and len(v.get("description", "")) < 200:
                time.sleep(0.4)
                full_rab = _get_bot_ref("fetch_rabota_vacancy_full", fetch_rabota_vacancy_full)(v.get("raw_id"))
                if full_rab:
                    v["description"] = full_rab

            if v.get("source") == "superjob" and len(v.get("description", "")) < 250:
                time.sleep(0.3)
                full_sj = fetch_v_fn(v_id)
                if full_sj and full_sj.get("description") and len(full_sj["description"]) > len(v.get("description", "")):
                    v["description"] = full_sj["description"]
                    if full_sj.get("salary") and not v.get("salary"):
                        v["salary"] = full_sj["salary"]

            if v.get("source") == "trudvsem" and len(v.get("description", "")) < 250:
                time.sleep(0.3)
                full_trud = fetch_v_fn(v_id)
                if full_trud and full_trud.get("description") and len(full_trud["description"]) > len(v.get("description", "")):
                    v["description"] = full_trud["description"]

            # Повторный отсев
            if prefilter_fn(v["title"], v.get("description", ""), v.get("company", "")):
                seen_add_fn(seen_vacancies, v_id)
                delete_cache_fn(v_id)
                continue

            logger.info(f"Отправка на AI-анализ ({ai_analyses_count + 1}/{max_analyses}) [{v.get('source_name')}]: {v['title']}")
            analysis = ai_analyze_fn(
                groq_key,
                groq_model,
                v["title"],
                v["company"],
                v["description"],
                delay=ai_delay
            )
            ai_analyses_count += 1

            if not analysis:
                logger.warning(f"AI-анализ не вернул результат для '{v['title']}'. Вакансия сохранена для следующего цикла.")
                continue

            seen_add_fn(seen_vacancies, v_id)

            score = analysis.get("match_score", 0)
            is_suitable = analysis.get("suitable", False)
            v["role"] = analysis.get("role", "java")
            v["match_score"] = score
            v["analysis"] = analysis

            logger.info(f"AI Вердикт для '{v['title']}': suitable={is_suitable}, score={score}, role={v['role']}")

            if is_suitable and score >= min_score:
                save_cache_fn(v)
                auto_applied, notified = process_apply_fn(
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
                delete_cache_fn(v_id)

        save_seen_fn(seen_vacancies)
        logger.info(f"Цикл завершен. AI анализов: {ai_analyses_count}, отправлено в TG: {sent_count}, сохранено: {quiet_count}. Пауза {interval} сек.")
        time.sleep(interval)
