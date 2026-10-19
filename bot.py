"""
Career Hub Bot - Autonomous IT Vacancy Aggregator, AI Scoring & Auto-Apply Assistant.
Facade Module providing backward compatibility with modular MVC architecture (career_hub package).
"""

import os
import sys
import re
import json
import time
import html
import logging
import threading
import secrets
import hmac
import hashlib
from urllib.parse import urlparse, parse_qs
import requests

# Ensure UTF-8 output encoding for cross-platform compatibility
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Set up root logging format
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("career_hub")

# Alias this module to 'bot' in sys.modules so sys.modules.get("bot") works when executed as __main__
sys.modules["bot"] = sys.modules[__name__]

# =====================================================================
# Import and Re-export Models (Data, Config, Storage, Vacancy formatters)
# =====================================================================
from career_hub.models.config import (
    DEFAULT_CONFIG,
    CANDIDATE_PROFILE,
    _config_cache,
    _config_cache_mtime,
    _config_cache_lock,
    load_config,
    save_config,
)
from career_hub.models.storage import (
    DATA_DIR,
    CONFIG_FILE,
    STATUSES_FILE,
    CACHE_FILE,
    AUTH_SESSIONS_FILE,
    SEEN_FILE,
    STATE_FILE,
    HH_NEGOTIATIONS_FILE,
    MINIAPP_HTML_PATH,
    cache_lock,
    statuses_lock,
    sessions_lock,
    seen_lock,
    hh_neg_lock,
    load_statuses,
    save_statuses,
    update_vacancy_status,
    load_cache,
    save_to_cache,
    get_from_cache,
    delete_from_cache,
    load_seen_vacancies,
    seen_add,
    save_seen_vacancies,
    load_hh_negotiations_tracker,
    save_hh_negotiations_tracker,
    load_sessions,
    save_sessions,
)
SESSIONS_FILE = AUTH_SESSIONS_FILE

from career_hub.models.vacancy import (
    clean_html,
    KNOWN_TECH,
    TAG_REMAP,
    extract_tech_stack,
    format_structured_description,
    normalize_vacancy_for_api,
)

# =====================================================================
# Import and Re-export Scrapers & Scraper Base
# =====================================================================
from career_hub.controllers.scrapers import (
    HTTP_HEADERS,
    quick_prefilter_reject,
    fetch_hh_vacancies,
    fetch_hh_vacancy_full,
    fetch_habr_vacancies,
    fetch_habr_vacancy_by_id,
    fetch_getmatch_vacancies,
    fetch_superjob_vacancies,
    DEFAULT_SUPERJOB_KEY,
    fetch_linkedin_vacancies,
    fetch_linkedin_vacancy_full,
    fetch_tg_channel_vacancies,
    fetch_rabota_vacancies,
    fetch_rabota_vacancy_full,
    fetch_trudvsem_vacancies,
    fetch_remotive_vacancies,
    fetch_vseti_vacancies,
    fetch_vseti_vacancy_full,
    fetch_vacancy_by_id,
)
from career_hub.controllers.scrapers.rabota import (
    NON_IT_TITLES_RABOTA,
    IT_KEYWORDS_RABOTA,
)

# =====================================================================
# Import and Re-export AI Controller & Auto-Apply
# =====================================================================
from career_hub.controllers.ai_controller import (
    analyze_vacancy_with_groq,
    generate_cover_letter,
    format_ai_vacancy_message,
)
from career_hub.controllers.auto_apply import (
    get_hh_headers,
    check_hh_token,
    fetch_hh_resumes,
    auto_select_hh_resume,
    apply_hh_vacancy_api,
    check_hh_web_cookie,
    apply_hh_vacancy_web,
    delete_hh_negotiation_web,
    remove_vacancy_from_profile_and_history,
    poll_hh_negotiations_and_notify,
    process_vacancy_auto_apply_and_notify,
)

# =====================================================================
# Import and Re-export Views (Auth & Mini App HTTP Server)
# =====================================================================
from career_hub.views.auth_view import (
    validate_telegram_init_data,
    create_user_session,
    verify_session_token,
    revoke_session_token,
    create_one_time_token,
    consume_one_time_token,
    get_bot_username,
    one_time_tokens,
)
from career_hub.views.http_server import (
    MiniAppRequestHandler,
    http_server_worker,
    pending_logins,
    pending_logins_lock,
)

# =====================================================================
# Import and Re-export Bot Controller (Telegram loops, Digest, Monitor)
# =====================================================================
from career_hub.controllers.bot_controller import (
    send_message,
    edit_message_text,
    edit_message_reply_markup,
    answer_callback_query,
    check_and_send_scheduled_digest,
    telegram_updates_worker,
    vacancy_monitor_worker,
)

from career_hub import run, main

if __name__ == "__main__":
    run()
