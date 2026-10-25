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

from career_hub.models.config import load_config, save_config, safe_int
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
    apply_habr_vacancy_web,
    check_habr_web_cookie,
    auto_select_hh_resume,
    build_telegram_recruiter_link,
    apply_telegram_vacancy,
    check_superjob_session,
    apply_superjob_vacancy,
    check_getmatch_session,
    apply_getmatch_vacancy,
    check_smtp_connection,
    send_email_application,
    check_rabota_session,
    apply_rabota_vacancy,
    apply_trudvsem_vacancy,
    apply_vacancy_dispatcher,
)
from career_hub.controllers.scrapers import fetch_vacancy_by_id

logger = logging.getLogger("career_hub.views.http")

pending_logins = {}
pending_logins_lock = threading.RLock()
_login_ip_rate_limits = {}
_login_ip_lock = threading.RLock()


def _check_ip_rate_limit(ip: str, max_requests: int = 10, window_seconds: int = 60) -> bool:
    """Ограничивает частоту запросов на авторизацию с одного IP для защиты от DoS и перебора."""
    with _login_ip_lock:
        now = time.time()
        for k in list(_login_ip_rate_limits.keys()):
            _login_ip_rate_limits[k] = [t for t in _login_ip_rate_limits[k] if now - t < window_seconds]
            if not _login_ip_rate_limits[k]:
                del _login_ip_rate_limits[k]

        reqs = _login_ip_rate_limits.setdefault(ip, [])
        if len(reqs) >= max_requests:
            return False
        reqs.append(now)
        return True


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


def _get_allowed_telegram_id(config=None) -> int:
    if config is None:
        cfg_fn = _get_bot_ref("load_config", load_config)
        config = cfg_fn()
    safe_int_fn = _get_bot_ref("safe_int", safe_int)
    raw = config.get("allowed_telegram_id") or config.get("chat_id") or os.environ.get("ALLOWED_TELEGRAM_ID", "0")
    return safe_int_fn(raw)


class MiniAppRequestHandler(BaseHTTPRequestHandler):
    def _send_cors_headers(self):
        # Защитные HTTP-заголовки
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        self.send_header("Content-Security-Policy", "frame-ancestors 'self' https://web.telegram.org https://*.telegram.org;")
        
        # Валидация Origin
        origin = self.headers.get("Origin", "")
        host = self.headers.get("Host", "")
        allowed = None
        if origin:
            try:
                parsed_orig = urlparse(origin)
                hostname = (parsed_orig.hostname or "").lower()
                host_domain = (host.split(":")[0] if host else "").lower()

                cfg_fn = _get_bot_ref("load_config", None)
                miniapp_host = ""
                if cfg_fn:
                    try:
                        cfg_url = cfg_fn().get("miniapp_url") or os.environ.get("MINIAPP_URL") or ""
                        if cfg_url:
                            miniapp_host = (urlparse(cfg_url).hostname or "").lower()
                    except Exception:
                        pass

                allowed_hosts = {"localhost", "127.0.0.1"}
                if host_domain:
                    allowed_hosts.add(host_domain)
                if miniapp_host:
                    allowed_hosts.add(miniapp_host)

                if (
                    hostname in allowed_hosts
                    or hostname == "telegram.org"
                    or hostname.endswith(".telegram.org")
                ):
                    allowed = origin
            except Exception:
                pass

        if allowed:
            self.send_header("Access-Control-Allow-Origin", allowed)
            self.send_header("Access-Control-Allow-Credentials", "true")
            self.send_header("Vary", "Origin")
        else:
            self.send_header("Access-Control-Allow-Origin", "null")

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
        allowed_id = _get_allowed_telegram_id(config)
        user_id = safe_int(session.get("user_id"))
        if allowed_id and user_id == allowed_id:
            return session
        elif not allowed_id and user_id:
            return session
        return None

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        if path == "":
            path = "/"

        if path in ["/", "/index.html"]:
            repo_html = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "miniapp_index.html")
            container_html = "/app/miniapp_index.html"
            configured_html = _get_bot_ref("MINIAPP_HTML_PATH", MINIAPP_HTML_PATH)

            html_file = None
            for cand in [container_html, repo_html, configured_html]:
                if cand and os.path.exists(cand):
                    html_file = cand
                    break

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
            check_habr_cookie_fn = _get_bot_ref("check_habr_web_cookie", check_habr_web_cookie)

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

            habr_cookie = (cfg.get("habr_web_cookie") or "").strip()
            habr_cookie_masked = ""
            habr_cookie_valid = False
            habr_user_name = None

            if habr_cookie:
                parts = []
                for p in habr_cookie.split(";"):
                    p = p.strip()
                    if "=" in p:
                        k, val = p.split("=", 1)
                        if k in ["remember_user_token", "_career_session", "_habr_career_session", "habr_uuid", "hl"]:
                            parts.append(f"{k}={val[:4]}...{val[-4:] if len(val) > 8 else ''}")
                habr_cookie_masked = "; ".join(parts) if parts else "Куки сохранены"
                habr_cookie_info = check_habr_cookie_fn(habr_cookie)
                if habr_cookie_info and habr_cookie_info.get("ok"):
                    habr_cookie_valid = True
                    habr_user_name = habr_cookie_info.get("name", "Соискатель")

            # SuperJob
            sj_tok = (cfg.get("superjob_token") or "").strip()
            sj_cookie = (cfg.get("superjob_cookie") or "").strip()
            sj_configured = bool(sj_tok or sj_cookie)
            sj_masked = ""
            if sj_tok:
                sj_masked = f"token={sj_tok[:4]}...{sj_tok[-4:] if len(sj_tok) > 8 else ''}"
            elif sj_cookie:
                sj_masked = "Куки SuperJob сохранены"

            # GetMatch
            gm_tok = (cfg.get("getmatch_token") or "").strip()
            gm_cookie = (cfg.get("getmatch_cookie") or "").strip()
            gm_configured = bool(gm_tok or gm_cookie)
            gm_masked = ""
            if gm_tok:
                gm_masked = f"bearer={gm_tok[:4]}...{gm_tok[-4:] if len(gm_tok) > 8 else ''}"
            elif gm_cookie:
                gm_masked = "Куки GetMatch сохранены"

            # Rabota.ru
            rabota_cookie = (cfg.get("rabota_cookie") or "").strip()
            rabota_tok = (cfg.get("rabota_token") or "").strip()
            rabota_configured = bool(rabota_cookie or rabota_tok)
            rabota_masked = ""
            if rabota_tok:
                rabota_masked = f"token={rabota_tok[:4]}...{rabota_tok[-4:] if len(rabota_tok) > 8 else ''}"
            elif rabota_cookie:
                rabota_masked = "Куки Работа.ру сохранены"

            # SMTP Mailer
            smtp_host = (cfg.get("smtp_host") or "").strip()
            smtp_user = (cfg.get("smtp_user") or "").strip()
            smtp_configured = bool(smtp_host and smtp_user and cfg.get("smtp_password"))

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
                "habr_connected": habr_cookie_valid,
                "habr_web_cookie_configured": bool(habr_cookie),
                "habr_web_cookie_valid": habr_cookie_valid,
                "habr_web_cookie_masked": habr_cookie_masked,
                "habr_user_name": habr_user_name,
                "superjob_connected": sj_configured,
                "superjob_configured": sj_configured,
                "superjob_masked": sj_masked,
                "getmatch_connected": gm_configured,
                "getmatch_configured": gm_configured,
                "getmatch_masked": gm_masked,
                "rabota_connected": rabota_configured,
                "rabota_configured": rabota_configured,
                "rabota_masked": rabota_masked,
                "smtp_connected": smtp_configured,
                "smtp_configured": smtp_configured,
                "smtp_host": smtp_host,
                "smtp_port": cfg.get("smtp_port", 465),
                "smtp_user": smtp_user,
                "smtp_from_name": cfg.get("smtp_from_name", "Никита Фролов"),
                "contact_email": cfg.get("contact_email", "nikita@shadowkek.space"),
                "resume_quick_tunnel_url": cfg.get("resume_quick_tunnel_url", "https://south-tomato-therapist-minimum.trycloudflare.com/"),
                "portfolio_url": cfg.get("portfolio_url", "https://park.shadowkek.space"),
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

        try:
            content_length = int(self.headers.get("Content-Length", 0))
        except (ValueError, TypeError):
            content_length = 0

        if content_length > 1024 * 1024:
            self.send_response(413)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": False, "error": "Превышен максимальный размер тела запроса (1MB)"}).encode("utf-8"))
            return

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
            allowed_id = _get_allowed_telegram_id(config)

            val_fn = _get_bot_ref("validate_telegram_init_data", validate_telegram_init_data)
            user_info = val_fn(init_data, bot_token)
            if not user_info:
                self.send_response(401)
                self._send_cors_headers()
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": False, "error": "Недействительные данные Telegram WebApp"}).encode("utf-8"))
                return

            uid = safe_int(user_info.get("id"))
            if not allowed_id:
                logger.warning(f"Попытка входа через WebApp до инициализации владельца: UID {uid}")
                self.send_response(403)
                self._send_cors_headers()
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({
                    "ok": False,
                    "error": "Бот не инициализирован. Выполните команду /start в Telegram с аккаунта владельца."
                }).encode("utf-8"))
                return
            elif uid != allowed_id:
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
            self.send_header("Set-Cookie", f"career_hub_token={token}; Max-Age=2592000; Path=/; SameSite=Lax; Secure; HttpOnly")
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
            uid = safe_int(consume_fn(one_time_token))
            cfg_fn = _get_bot_ref("load_config", load_config)
            config = cfg_fn()
            allowed_id = _get_allowed_telegram_id(config)

            if not uid or (allowed_id and uid != allowed_id):
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
            self.send_header("Set-Cookie", f"career_hub_token={perm_token}; Max-Age=2592000; Path=/; SameSite=Lax; Secure; HttpOnly")
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps({
                "ok": True,
                "token": perm_token,
                "expires_at": exp
            }).encode("utf-8"))
            return

        elif path == "/api/auth/request_login_session":
            client_ip = self.client_address[0] if self.client_address else "unknown"
            if not _check_ip_rate_limit(client_ip, max_requests=10, window_seconds=60):
                self.send_response(429)
                self._send_cors_headers()
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": False, "error": "Слишком много запросов на вход. Подождите 1 минуту."}).encode("utf-8"))
                return

            p_logins = _get_bot_ref("pending_logins", pending_logins)
            p_lock = _get_bot_ref("pending_logins_lock", pending_logins_lock)
            with p_lock:
                now = time.time()
                # Удаляем сессии старше 15 минут
                to_del = [k for k, v in p_logins.items() if now - v.get("created_at", 0) > 900 or v.get("approved")]
                for k in to_del:
                    del p_logins[k]

                # Ограничиваем пул одновременных сессий
                if len(p_logins) >= 15:
                    oldest = sorted(p_logins.items(), key=lambda x: x[1].get("created_at", 0))[:5]
                    for k, _ in oldest:
                        p_logins.pop(k, None)

                # Исключаем коллизии кодов
                existing_codes = {v.get("code") for v in p_logins.values() if not v.get("approved")}
                code = f"{secrets.randbelow(900000) + 100000}"
                for _ in range(50):
                    if code not in existing_codes:
                        break
                    code = f"{secrets.randbelow(900000) + 100000}"

                s_id = secrets.token_hex(16)
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
            uname = uname_fn() or ""
            bot_link = f"https://t.me/{uname}?start=login_{s_id}" if uname else ""
            self.wfile.write(json.dumps({
                "ok": True,
                "session_id": s_id,
                "code": code,
                "bot_username": uname,
                "bot_link": bot_link
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

            cfg_fn = _get_bot_ref("load_config", load_config)
            config = cfg_fn()
            groq_key = config.get("groq_api_key", "")
            groq_model = config.get("groq_model", "llama-3.3-70b-versatile")
            gen_letter_fn = _get_bot_ref("generate_cover_letter", generate_cover_letter)
            letter = data.get("cover_letter") or gen_letter_fn(groq_key, groq_model, vacancy)

            dispatch_fn = _get_bot_ref("apply_vacancy_dispatcher", apply_vacancy_dispatcher)
            res = dispatch_fn(vacancy, letter, config=config)

            if not res.get("ok"):
                self.send_response(400)
                self._send_cors_headers()
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({
                    "ok": False,
                    "error": res.get("detail", "Ошибка отклика"),
                    "status": res.get("status"),
                    "redirect_url": res.get("redirect_url"),
                    "cover_letter": letter
                }, ensure_ascii=False).encode("utf-8"))
                return

            applied_method = res.get("method") or "manual"
            auto_applied = applied_method in ["hh_api", "hh_web", "habr_web", "superjob_api", "getmatch_api", "email_smtp", "rabota_api"]
            update_fn = _get_bot_ref("update_vacancy_status", update_vacancy_status)
            updated = update_fn(v_id, "applied", cover_letter=letter, auto_applied=auto_applied, method=applied_method)
            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps({
                "ok": True,
                "data": updated,
                "cover_letter": letter,
                "applied_method": applied_method,
                "status": res.get("status"),
                "detail": res.get("detail"),
                "direct_link": res.get("direct_link"),
                "web_link": res.get("web_link"),
                "redirect_url": res.get("redirect_url")
            }, ensure_ascii=False).encode("utf-8"))
            return

        elif path == "/api/settings":
            cfg_fn = _get_bot_ref("load_config", load_config)
            save_cfg_fn = _get_bot_ref("save_config", save_config)
            check_cookie_fn = _get_bot_ref("check_hh_web_cookie", check_hh_web_cookie)
            check_habr_cookie_fn = _get_bot_ref("check_habr_web_cookie", check_habr_web_cookie)

            cfg = cfg_fn()
            cookie_updated = False
            cookie_valid = None
            cookie_msg = None

            habr_cookie_updated = False
            habr_cookie_valid = None
            habr_cookie_msg = None
            habr_user_name = None

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

            if "habr_web_cookie" in data:
                new_habr_cookie = (data.get("habr_web_cookie") or "").strip()
                if new_habr_cookie:
                    check_habr_res = check_habr_cookie_fn(new_habr_cookie)
                    if check_habr_res and check_habr_res.get("ok"):
                        cfg["habr_web_cookie"] = new_habr_cookie
                        habr_cookie_updated = True
                        habr_cookie_valid = True
                        habr_user_name = check_habr_res.get("name", "Соискатель")
                        habr_cookie_msg = f"Сессия Хабр Карьеры подтверждена ({habr_user_name})!"
                    else:
                        habr_cookie_valid = False
                        habr_cookie_msg = "Введённые куки не прошли проверку на career.habr.com (возможно, истекли)."
                else:
                    cfg["habr_web_cookie"] = ""
                    habr_cookie_updated = True
                    habr_cookie_valid = False
                    habr_cookie_msg = "Куки Хабр Карьеры удалены."

            if "superjob_token" in data:
                cfg["superjob_token"] = (data.get("superjob_token") or "").strip()
            if "superjob_cookie" in data:
                cfg["superjob_cookie"] = (data.get("superjob_cookie") or "").strip()
            if "superjob_resume_id" in data:
                cfg["superjob_resume_id"] = (data.get("superjob_resume_id") or "").strip()

            if "getmatch_token" in data:
                cfg["getmatch_token"] = (data.get("getmatch_token") or "").strip()
            if "getmatch_cookie" in data:
                cfg["getmatch_cookie"] = (data.get("getmatch_cookie") or "").strip()

            if "rabota_cookie" in data:
                cfg["rabota_cookie"] = (data.get("rabota_cookie") or "").strip()
            if "rabota_token" in data:
                cfg["rabota_token"] = (data.get("rabota_token") or "").strip()

            if "smtp_host" in data:
                cfg["smtp_host"] = (data.get("smtp_host") or "").strip()
            if "smtp_port" in data:
                cfg["smtp_port"] = safe_int(data.get("smtp_port"), 465)
            if "smtp_user" in data:
                cfg["smtp_user"] = (data.get("smtp_user") or "").strip()
            if "smtp_password" in data:
                val = (data.get("smtp_password") or "").strip()
                if val:
                    cfg["smtp_password"] = val
            if "smtp_ssl" in data:
                cfg["smtp_ssl"] = bool(data.get("smtp_ssl", True))
            if "smtp_from_name" in data:
                cfg["smtp_from_name"] = (data.get("smtp_from_name") or "").strip()
            if "contact_email" in data:
                cfg["contact_email"] = (data.get("contact_email") or "").strip()
            if "resume_quick_tunnel_url" in data:
                cfg["resume_quick_tunnel_url"] = (data.get("resume_quick_tunnel_url") or "").strip()
            if "portfolio_url" in data:
                cfg["portfolio_url"] = (data.get("portfolio_url") or "").strip()
            if "resume_pdf_path" in data:
                raw_path = (data.get("resume_pdf_path") or "").strip()
                if raw_path:
                    if ".." not in raw_path and raw_path.lower().endswith(".pdf"):
                        cfg["resume_pdf_path"] = raw_path
                else:
                    cfg["resume_pdf_path"] = ""

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
                "habr_cookie_valid": habr_cookie_valid,
                "habr_cookie_msg": habr_cookie_msg,
                "habr_user_name": habr_user_name,
                "settings": {
                    "auto_apply_enabled": cfg.get("auto_apply_enabled", True),
                    "auto_apply_min_score": cfg.get("auto_apply_min_score", 7),
                    "notify_min_score": cfg.get("notify_min_score", 9),
                    "hh_web_cookie_configured": bool((cfg.get("hh_web_cookie") or "").strip()),
                    "hh_web_cookie_valid": bool(cookie_valid if cookie_valid is not None else cfg.get("hh_web_cookie")),
                    "habr_web_cookie_configured": bool((cfg.get("habr_web_cookie") or "").strip()),
                    "habr_web_cookie_valid": bool(habr_cookie_valid if habr_cookie_valid is not None else cfg.get("habr_web_cookie")),
                    "habr_user_name": habr_user_name
                }
            }, ensure_ascii=False).encode("utf-8"))
            return

        elif path == "/api/check_superjob":
            token = (data.get("token") or "").strip()
            cookie_str = (data.get("cookie") or "").strip()
            check_sj_fn = _get_bot_ref("check_superjob_session", check_superjob_session)
            res = check_sj_fn(token=token, cookie_str=cookie_str)
            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps(res, ensure_ascii=False).encode("utf-8"))
            return

        elif path == "/api/check_getmatch":
            token_or_cookie = (data.get("token") or data.get("cookie") or "").strip()
            check_gm_fn = _get_bot_ref("check_getmatch_session", check_getmatch_session)
            res = check_gm_fn(token_or_cookie=token_or_cookie)
            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps(res, ensure_ascii=False).encode("utf-8"))
            return

        elif path == "/api/check_smtp":
            smtp_host = (data.get("smtp_host") or "").strip()
            smtp_port = safe_int(data.get("smtp_port"), 465)
            smtp_user = (data.get("smtp_user") or "").strip()
            smtp_password = (data.get("smtp_password") or "").strip()
            use_ssl = bool(data.get("smtp_ssl", True))
            check_smtp_fn = _get_bot_ref("check_smtp_connection", check_smtp_connection)
            res = check_smtp_fn(smtp_host, smtp_port, smtp_user, smtp_password, use_ssl=use_ssl)
            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps(res, ensure_ascii=False).encode("utf-8"))
            return

        elif path == "/api/check_rabota":
            val = (data.get("cookie") or data.get("token") or "").strip()
            check_rab_fn = _get_bot_ref("check_rabota_session", check_rabota_session)
            res = check_rab_fn(cookie_or_token=val)
            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps(res, ensure_ascii=False).encode("utf-8"))
            return


        elif path == "/api/check_habr_cookie":
            test_cookie = (data.get("cookie") or "").strip()
            if not test_cookie:
                self.send_response(400)
                self._send_cors_headers()
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": False, "error": "Пустая строка cookie"}, ensure_ascii=False).encode("utf-8"))
                return

            check_habr_cookie_fn = _get_bot_ref("check_habr_web_cookie", check_habr_web_cookie)
            res = check_habr_cookie_fn(test_cookie)
            if res and res.get("ok"):
                self.send_response(200)
                self._send_cors_headers()
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({
                    "ok": True,
                    "valid": True,
                    "name": res.get("name", "Соискатель"),
                    "url": res.get("url", "https://career.habr.com/profile")
                }, ensure_ascii=False).encode("utf-8"))
            else:
                self.send_response(200)
                self._send_cors_headers()
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({
                    "ok": True,
                    "valid": False,
                    "error": "Сессия не авторизована на career.habr.com (проверьте корректность куки)"
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
