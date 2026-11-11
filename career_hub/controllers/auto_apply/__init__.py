"""
Career Hub Auto-Apply Controller Package.
Modular multi-platform adapters for HeadHunter, Habr, SuperJob, GetMatch, Email, Telegram, and Rabota.ru.
"""

from career_hub.controllers.auto_apply.base import (
    _get_bot_ref,
    get_miniapp_url,
    logger,
)
from career_hub.controllers.auto_apply.hh_client import (
    apply_hh_vacancy_api,
    apply_hh_vacancy_web,
    auto_select_hh_resume,
    check_hh_token,
    check_hh_web_cookie,
    delete_hh_negotiation_web,
    fetch_hh_resumes,
    get_hh_headers,
    poll_hh_negotiations_and_notify,
    remove_vacancy_from_profile_and_history,
)
from career_hub.controllers.auto_apply.habr_client import (
    apply_habr_vacancy_web,
    check_habr_web_cookie,
)
from career_hub.controllers.auto_apply.superjob_client import (
    apply_superjob_vacancy,
    check_superjob_session,
)
from career_hub.controllers.auto_apply.getmatch_client import (
    apply_getmatch_vacancy,
    check_getmatch_session,
)
from career_hub.controllers.auto_apply.email_client import (
    check_smtp_connection,
    send_email_application,
)
from career_hub.controllers.auto_apply.telegram_client import (
    apply_telegram_vacancy,
    build_telegram_recruiter_link,
)
from career_hub.controllers.auto_apply.rabota_client import (
    apply_rabota_vacancy,
    apply_trudvsem_vacancy,
    check_rabota_session,
)
from career_hub.controllers.auto_apply.dispatcher import (
    apply_vacancy_dispatcher,
    process_vacancy_auto_apply_and_notify,
)

__all__ = [
    "_get_bot_ref",
    "get_miniapp_url",
    "logger",
    "get_hh_headers",
    "check_hh_token",
    "fetch_hh_resumes",
    "auto_select_hh_resume",
    "apply_hh_vacancy_api",
    "check_hh_web_cookie",
    "apply_hh_vacancy_web",
    "check_habr_web_cookie",
    "apply_habr_vacancy_web",
    "build_telegram_recruiter_link",
    "apply_telegram_vacancy",
    "check_superjob_session",
    "apply_superjob_vacancy",
    "check_getmatch_session",
    "apply_getmatch_vacancy",
    "check_smtp_connection",
    "send_email_application",
    "check_rabota_session",
    "apply_rabota_vacancy",
    "apply_trudvsem_vacancy",
    "apply_vacancy_dispatcher",
    "delete_hh_negotiation_web",
    "remove_vacancy_from_profile_and_history",
    "poll_hh_negotiations_and_notify",
    "process_vacancy_auto_apply_and_notify",
]
