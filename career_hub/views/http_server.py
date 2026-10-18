"""
HTTP Server and Mini App REST API Handler for Career Hub.
Provides the backend for the Telegram Mini App (TMA) and Web UI.
"""

import os
import sys
import json
import time
import secrets
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from career_hub.models.config import load_config, save_config
from career_hub.models.storage import (
    load_cache,
    load_statuses,
    update_vacancy_status,
    MINIAPP_HTML_PATH,
)
from career_hub.models.vacancy import normalize_vacancy_for_api
from career_hub.views.auth_view import (
    get_bot_username,
    create_user_session,
    verify_session_token,
    revoke_session_token,
    consume_one_time_token,
    validate_telegram_init_data,
)
from career_hub.controllers.ai_controller import generate_cover_letter
from career_hub.controllers.auto_apply import (
    apply_hh_vacancy_api,
    apply_hh_vacancy_web,
    check_hh_web_cookie,
    auto_select_hh_resume,
)
from career_hub.controllers.scrapers import fetch_vacancy_by_id

logger = logging.getLogger("career_hub.views.http")

pending_logins = {}
pending_logins_lock = threading.Lock()


def _get_bot_ref(attr_name, fallback):
    bot_mod = sys.modules.get("bot")
    if bot_mod and hasattr(bot_mod, attr_name):
        return getattr(bot_mod, attr_name)
    return fallback


class MiniAppRequestHandler(BaseHTTPRequestHandler):
    def _send_cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")

    def do_OPTIONS(self):
        self.send_response(204)
        self._send_cors_headers()
        self.end_headers()

    def do_HEAD(self):
        self.send_response(200)
        self._send_cors_headers()
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()

    def get_auth_token(self):
        auth_header = self.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            return auth_header[7:].strip()
        cookie_header = self.headers.get("Cookie", "")
        if cookie_header:
            for item in cookie_header.split(";"):
                parts = item.strip().split("=", 1)
                if len(parts) == 2 and parts[0] in ["career_hub_token", "career_hub_session"]:
                    return parts[1]
        return None

    def is_authenticated(self):
        token = self.get_auth_token()
        verify_fn = _get_bot_ref("verify_session_token", verify_session_token)
        session = verify_fn(token)
        if not session:
            return None
        cfg_fn = _get_bot_ref("load_config", load_config)
        config = cfg_fn()
        allowed_id = config.get("allowed_telegram_id") or config.get("chat_id") or 1139872910
        if session.get("user_id") == allowed_id:
            return session
        return None

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        if path == "":
            path = "/"

        if path in ["/", "/index.html"]:
            html_file = _get_bot_ref("MINIAPP_HTML_PATH", MINIAPP_HTML_PATH)
            if not os.path.exists(html_file):
                html_file = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "miniapp_index.html")
            if not os.path.exists(html_file):
                html_file = "/app/miniapp_index.html"

            if os.path.exists(html_file):
                try:
                    with open(html_file, "r", encoding="utf-8") as f:
                        content = f.read().encode("utf-8")
                    self.send_response(200)
                    self._send_cors_headers()
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(content)))
                    self.end_headers()
                    self.wfile.write(content)
                    return
                except Exception as e:
                    logger.error(f"Ошибка отдачи Mini App HTML: {e}")

            self.send_response(404)
            self._send_cors_headers()
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"Mini App HTML file not found.")
            return

        elif path == "/api/auth/me":
            session = self.is_authenticated()
            if not session:
                self.send_response(401)
                self._send_cors_headers()
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": False, "error": "Unauthorized"}).encode("utf-8"))
                return

            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps({
                "ok": True,
                "user_id": session.get("user_id"),
                "device": session.get("device"),
                "expires_at": session.get("expires_at")
            }).encode("utf-8"))
            return

        elif path == "/api/auth/check_login_session":
            parsed_qs = parse_qs(parsed.query)
            s_id = parsed_qs.get("session_id", [""])[0]
            if not s_id:
                self.send_response(400)
                self._send_cors_headers()
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": False, "error": "Missing session_id"}).encode("utf-8"))
                return

            p_logins = _get_bot_ref("pending_logins", pending_logins)
            p_lock = _get_bot_ref("pending_logins_lock", pending_logins_lock)
            with p_lock:
                p = p_logins.get(s_id)
                if not p:
                    resp_data = {"ok": False, "status": "expired"}
                elif p.get("approved"):
                    token = p.get("token")
                    p_logins.pop(s_id, None)
                    resp_data = {"ok": True, "status": "approved", "token": token}
                else:
                    resp_data = {"ok": True, "status": "pending"}

            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps(resp_data).encode("utf-8"))
            return

        elif path == "/api/vacancies":
            session = self.is_authenticated()
            if not session:
                self.send_response(401)
                self._send_cors_headers()
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": False, "error": "Unauthorized"}).encode("utf-8"))
                return

            try:
                load_c_fn = _get_bot_ref("load_cache", load_cache)
                load_s_fn = _get_bot_ref("load_statuses", load_statuses)
                norm_fn = _get_bot_ref("normalize_vacancy_for_api", normalize_vacancy_for_api)

                cache = load_c_fn()
                statuses = load_s_fn()

                vacancies_list = []
                stats = {"inbox": 0, "saved": 0, "applied": 0, "interview": 0, "archived": 0}

                # Отдаем в обратном порядке (новые сверху), пропуская отсеянные вакансии
                for v_id, v in reversed(list(cache.items())):
                    norm_v = norm_fn(v, statuses)
                    if not norm_v:
                        continue
                    vacancies_list.append(norm_v)
                    st = norm_v["status"]
                    if st in stats:
                        stats[st] += 1
                    else:
                        stats["inbox"] += 1

                response_data = {
                    "ok": True,
                    "total": len(vacancies_list),
                    "stats": stats,
                    "statuses": statuses,
                    "vacancies": vacancies_list
                }

                content = json.dumps(response_data, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self._send_cors_headers()
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
                return
            except Exception as e:
                logger.error(f"Ошибка API /api/vacancies: {e}")
                self.send_response(500)
                self._send_cors_headers()
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": False, "error": str(e)}).encode("utf-8"))
                return

        elif path == "/api/settings":
            session = self.is_authenticated()
            if not session:
                self.send_response(401)
                self._send_cors_headers()
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": False, "error": "Unauthorized"}).encode("utf-8"))
                return

            cfg_fn = _get_bot_ref("load_config", load_config)
            cache_fn = _get_bot_ref("load_cache", load_cache)
            stat_fn = _get_bot_ref("load_statuses", load_statuses)
            check_cookie_fn = _get_bot_ref("check_hh_web_cookie", check_hh_web_cookie)

            cfg = cfg_fn()
            cache = cache_fn()
            statuses = stat_fn()
            applied_count = sum(1 for s in statuses.values() if s.get("status") == "applied")

            hh_cookie = (cfg.get("hh_web_cookie") or "").strip()
            hh_cookie_masked = ""
            hh_cookie_valid = False
            hh_user_name = None

            if hh_cookie:
                parts = []
                for p in hh_cookie.split(";"):
                    p = p.strip()
                    if "=" in p:
                        k, val = p.split("=", 1)
                        if k in ["hhtoken", "_xsrf", "crypted_hhuid"]:
                            parts.append(f"{k}={val[:4]}...{val[-4:] if len(val) > 8 else ''}")
                hh_cookie_masked = "; ".join(parts) if parts else "Куки сохранены"
                hh_cookie_info = check_cookie_fn(hh_cookie)
                if hh_cookie_info and hh_cookie_info.get("ok"):
                    hh_cookie_valid = True
                    hh_user_name = hh_cookie_info.get("name", "Соискатель")

            resp_data = {
                "ok": True,
                "auto_apply_enabled": cfg.get("auto_apply_enabled", True),
                "auto_apply_min_score": cfg.get("auto_apply_min_score", 7),
                "notify_min_score": cfg.get("notify_min_score", 9),
                "min_match_score": cfg.get("min_match_score", 6),
                "hh_connected": bool(cfg.get("hh_access_token", "").strip()) or hh_cookie_valid,
                "hh_web_cookie_configured": bool(hh_cookie),
                "hh_web_cookie_valid": hh_cookie_valid,
                "hh_web_cookie_masked": hh_cookie_masked,
                "hh_user_name": hh_user_name,
                "total_vacancies": len(cache),
                "total_applied": applied_count
            }
            content = json.dumps(resp_data, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
            return

        elif path == "/health":
            content = json.dumps({"status": "ok", "app": "Career Hub TMA"}).encode("utf-8")
            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
            return

        else:
            self.send_response(404)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": False, "error": "Not Found"}).encode("utf-8"))

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        content_length = int(self.headers.get("Content-Length", 0))
        post_body = self.rfile.read(content_length)

        try:
            data = json.loads(post_body.decode("utf-8")) if post_body else {}
        except Exception:
            data = {}

        if path == "/api/auth/telegram_webapp":
            init_data = data.get("initData", "")
            cfg_fn = _get_bot_ref("load_config", load_config)
            config = cfg_fn()
            bot_token = config["bot_token"]
            allowed_id = config.get("allowed_telegram_id") or config.get("chat_id") or 1139872910

            val_fn = _get_bot_ref("validate_telegram_init_data", validate_telegram_init_data)
            user_info = val_fn(init_data, bot_token)
            if not user_info:
                self.send_response(401)
                self._send_cors_headers()
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": False, "error": "Недействительные данные Telegram WebApp"}).encode("utf-8"))
                return

            uid = user_info.get("id")
            if uid != allowed_id:
                logger.warning(f"Попытка входа с чужим Telegram ID: {uid} (ожидался {allowed_id})")
                self.send_response(403)
                self._send_cors_headers()
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": False, "error": "Доступ разрешён только владельцу бота."}).encode("utf-8"))
                return

            create_sess_fn = _get_bot_ref("create_user_session", create_user_session)
            token, exp = create_sess_fn(uid, device="Telegram Mini App", days=30)
            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Set-Cookie", f"career_hub_token={token}; Max-Age=2592000; Path=/; SameSite=Lax; Secure")
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps({
                "ok": True,
                "token": token,
                "user": user_info,
                "expires_at": exp
            }).encode("utf-8"))
            return

        elif path == "/api/auth/exchange_token":
            one_time_token = data.get("token", "")
            consume_fn = _get_bot_ref("consume_one_time_token", consume_one_time_token)
            uid = consume_fn(one_time_token)
            cfg_fn = _get_bot_ref("load_config", load_config)
            config = cfg_fn()
            allowed_id = config.get("allowed_telegram_id") or config.get("chat_id") or 1139872910

            if not uid or uid != allowed_id:
                self.send_response(401)
                self._send_cors_headers()
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": False, "error": "Ссылка для входа устарела или недействительна"}).encode("utf-8"))
                return

            create_sess_fn = _get_bot_ref("create_user_session", create_user_session)
            perm_token, exp = create_sess_fn(uid, device="Browser Magic Link", days=30)
            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Set-Cookie", f"career_hub_token={perm_token}; Max-Age=2592000; Path=/; SameSite=Lax; Secure")
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps({
                "ok": True,
                "token": perm_token,
                "expires_at": exp
            }).encode("utf-8"))
            return

        elif path == "/api/auth/request_login_session":
            s_id = secrets.token_hex(16)
            code = f"{secrets.randbelow(900000) + 100000}"
            p_logins = _get_bot_ref("pending_logins", pending_logins)
            p_lock = _get_bot_ref("pending_logins_lock", pending_logins_lock)
            with p_lock:
                now = time.time()
                to_del = [k for k, v in p_logins.items() if now - v.get("created_at", 0) > 900]
                for k in to_del:
                    del p_logins[k]

                p_logins[s_id] = {
                    "code": code,
                    "created_at": now,
                    "approved": False,
                    "token": None
                }

            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            uname_fn = _get_bot_ref("get_bot_username", get_bot_username)
            uname = uname_fn()
            self.wfile.write(json.dumps({
                "ok": True,
                "session_id": s_id,
                "code": code,
                "bot_username": uname,
                "bot_link": f"https://t.me/{uname}?start=login_{s_id}"
            }).encode("utf-8"))
            return

        elif path == "/api/auth/logout":
            token = self.get_auth_token()
            revoke_fn = _get_bot_ref("revoke_session_token", revoke_session_token)
            revoke_fn(token)
            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Set-Cookie", "career_hub_token=; Max-Age=0; Path=/; SameSite=Lax")
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": True}).encode("utf-8"))
            return

        # Все последующие API запросы требуют валидной сессии
        session = self.is_authenticated()
        if not session:
            self.send_response(401)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": False, "error": "Unauthorized"}).encode("utf-8"))
            return

        if path == "/api/status":
            v_id = data.get("id")
            status = data.get("status")
            extra_cover = data.get("cover_letter")
            if not v_id or not status:
                self.send_response(400)
                self._send_cors_headers()
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": False, "error": "Missing id or status"}).encode("utf-8"))
                return

            update_fn = _get_bot_ref("update_vacancy_status", update_vacancy_status)
            updated = update_fn(v_id, status, cover_letter=extra_cover)
            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": True, "data": updated}).encode("utf-8"))
            return

        elif path == "/api/auto_apply":
            v_id = data.get("id")
            if not v_id:
                self.send_response(400)
                self._send_cors_headers()
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": False, "error": "Missing id"}).encode("utf-8"))
                return

            fetch_v_fn = _get_bot_ref("fetch_vacancy_by_id", fetch_vacancy_by_id)
            vacancy = fetch_v_fn(v_id)
            if not vacancy:
                self.send_response(404)
                self._send_cors_headers()
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": False, "error": "Вакансия не найдена"}).encode("utf-8"))
                return

            raw_id = vacancy.get("raw_id") or (str(v_id).replace("hh_", "") if str(v_id).startswith("hh_") else str(v_id))

            cfg_fn = _get_bot_ref("load_config", load_config)
            save_cfg_fn = _get_bot_ref("save_config", save_config)
            config = cfg_fn()
            groq_key = config["groq_api_key"]
            groq_model = config.get("groq_model", "llama-3.3-70b-versatile")
            gen_letter_fn = _get_bot_ref("generate_cover_letter", generate_cover_letter)
            letter = data.get("cover_letter") or gen_letter_fn(groq_key, groq_model, vacancy)

            hh_tok = config.get("hh_access_token", "").strip().strip('"').strip("'")
            hh_res_id = config.get("hh_resume_id", "").strip()
            hh_cookie = config.get("hh_web_cookie", "").strip()
            hh_res_hash = config.get("hh_resume_hash", os.environ.get("HH_RESUME_HASH", "")).strip()
            applied_method = "manual"

            if vacancy.get("source") == "hh":
                if hh_cookie:
                    apply_web_fn = _get_bot_ref("apply_hh_vacancy_web", apply_hh_vacancy_web)
                    web_res = apply_web_fn(raw_id, hh_res_hash, letter, hh_cookie)
                    if web_res.get("ok"):
                        applied_method = "hh_web"
                    else:
                        api_error = web_res.get("detail", "Ошибка отклика через веб-сессию")
                        self.send_response(400)
                        self._send_cors_headers()
                        self.send_header("Content-Type", "application/json; charset=utf-8")
                        self.end_headers()
                        self.wfile.write(json.dumps({
                            "ok": False,
                            "error": api_error,
                            "status": web_res.get("status"),
                            "cover_letter": letter
                        }, ensure_ascii=False).encode("utf-8"))
                        return
                elif hh_tok:
                    if not hh_res_id:
                        sel_resume_fn = _get_bot_ref("auto_select_hh_resume", auto_select_hh_resume)
                        res_obj = sel_resume_fn(hh_tok)
                        if res_obj:
                            hh_res_id = res_obj.get("id", "")
                            cfg_save = cfg_fn()
                            cfg_save["hh_resume_id"] = hh_res_id
                            save_cfg_fn(cfg_save)

                    if hh_res_id:
                        apply_api_fn = _get_bot_ref("apply_hh_vacancy_api", apply_hh_vacancy_api)
                        api_res = apply_api_fn(raw_id, hh_res_id, letter, hh_tok)
                        if api_res.get("ok"):
                            applied_method = "hh_api"
                        else:
                            api_error = api_res.get("detail", "Ошибка отклика через API")
                            self.send_response(400)
                            self._send_cors_headers()
                            self.send_header("Content-Type", "application/json; charset=utf-8")
                            self.end_headers()
                            self.wfile.write(json.dumps({
                                "ok": False,
                                "error": api_error,
                                "status": api_res.get("status"),
                                "cover_letter": letter
                            }, ensure_ascii=False).encode("utf-8"))
                            return

            update_fn = _get_bot_ref("update_vacancy_status", update_vacancy_status)
            updated = update_fn(v_id, "applied", cover_letter=letter, auto_applied=(applied_method in ["hh_api", "hh_web"]), method=applied_method)
            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": True, "data": updated, "cover_letter": letter, "applied_method": applied_method}, ensure_ascii=False).encode("utf-8"))
            return

        elif path == "/api/settings":
            cfg_fn = _get_bot_ref("load_config", load_config)
            save_cfg_fn = _get_bot_ref("save_config", save_config)
            check_cookie_fn = _get_bot_ref("check_hh_web_cookie", check_hh_web_cookie)

            cfg = cfg_fn()
            cookie_updated = False
            cookie_valid = None
            cookie_msg = None

            if "hh_web_cookie" in data:
                new_cookie = (data.get("hh_web_cookie") or "").strip()
                if new_cookie:
                    check_res = check_cookie_fn(new_cookie)
                    if check_res and check_res.get("ok"):
                        cfg["hh_web_cookie"] = new_cookie
                        if check_res.get("resumes"):
                            cfg["hh_resume_hash"] = check_res["resumes"][0]
                        cookie_updated = True
                        cookie_valid = True
                        cookie_msg = f"Сессия HeadHunter подтверждена ({check_res.get('name', 'Пользователь')})!"
                    else:
                        cookie_valid = False
                        cookie_msg = "Введённые куки не прошли проверку на hh.ru (возможно, истекли или не содержат hhtoken/_xsrf)."
                else:
                    cfg["hh_web_cookie"] = ""
                    cookie_updated = True
                    cookie_valid = False
                    cookie_msg = "Куки HeadHunter удалены."

            if "auto_apply_enabled" in data:
                cfg["auto_apply_enabled"] = bool(data["auto_apply_enabled"])
            if "auto_apply_min_score" in data:
                try:
                    cfg["auto_apply_min_score"] = int(data["auto_apply_min_score"])
                except Exception:
                    pass
            if "notify_min_score" in data:
                try:
                    cfg["notify_min_score"] = int(data["notify_min_score"])
                except Exception:
                    pass

            save_cfg_fn(cfg)
            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps({
                "ok": True,
                "cookie_valid": cookie_valid,
                "cookie_msg": cookie_msg,
                "settings": {
                    "auto_apply_enabled": cfg.get("auto_apply_enabled", True),
                    "auto_apply_min_score": cfg.get("auto_apply_min_score", 7),
                    "notify_min_score": cfg.get("notify_min_score", 9),
                    "hh_web_cookie_configured": bool((cfg.get("hh_web_cookie") or "").strip()),
                    "hh_web_cookie_valid": bool(cookie_valid if cookie_valid is not None else cfg.get("hh_web_cookie"))
                }
            }, ensure_ascii=False).encode("utf-8"))
            return

        elif path == "/api/check_cookie":
            test_cookie = (data.get("cookie") or "").strip()
            if not test_cookie:
                self.send_response(400)
                self._send_cors_headers()
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": False, "error": "Пустая строка cookie"}, ensure_ascii=False).encode("utf-8"))
                return

            check_cookie_fn = _get_bot_ref("check_hh_web_cookie", check_hh_web_cookie)
            res = check_cookie_fn(test_cookie)
            if res and res.get("ok"):
                self.send_response(200)
                self._send_cors_headers()
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({
                    "ok": True,
                    "valid": True,
                    "name": res.get("name", "Соискатель"),
                    "host": res.get("host", "hh.ru"),
                    "resumes_count": len(res.get("resumes", []))
                }, ensure_ascii=False).encode("utf-8"))
            else:
                self.send_response(200)
                self._send_cors_headers()
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({
                    "ok": True,
                    "valid": False,
                    "error": "Сессия не авторизована на hh.ru (проверьте наличие hhtoken и _xsrf)"
                }, ensure_ascii=False).encode("utf-8"))
            return

        elif path == "/api/cover_letter":
            v_id = data.get("id")
            if not v_id:
                self.send_response(400)
                self._send_cors_headers()
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": False, "error": "Missing id"}).encode("utf-8"))
                return

            fetch_v_fn = _get_bot_ref("fetch_vacancy_by_id", fetch_vacancy_by_id)
            vacancy = fetch_v_fn(v_id)
            if not vacancy:
                self.send_response(404)
                self._send_cors_headers()
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": False, "error": "Вакансия не найдена"}).encode("utf-8"))
                return

            cfg_fn = _get_bot_ref("load_config", load_config)
            config = cfg_fn()
            groq_key = config["groq_api_key"]
            groq_model = config.get("groq_model", "llama-3.3-70b-versatile")

            gen_letter_fn = _get_bot_ref("generate_cover_letter", generate_cover_letter)
            letter = gen_letter_fn(groq_key, groq_model, vacancy)
            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": True, "cover_letter": letter}).encode("utf-8"))
            return

        else:
            self.send_response(404)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": False, "error": "Not Found"}).encode("utf-8"))

    def log_message(self, format, *args):
        # Отключаем шумные access-логи в stdout
        pass


def http_server_worker(host="0.0.0.0", port=8088):
    logger.info(f"Запуск HTTP сервера Mini App на {host}:{port}...")
    server = ThreadingHTTPServer((host, port), MiniAppRequestHandler)
    try:
        server.serve_forever()
    except Exception as e:
        logger.error(f"Ошибка в HTTP сервере: {e}")
