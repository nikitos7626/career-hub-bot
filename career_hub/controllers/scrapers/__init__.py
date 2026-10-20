"""
Career Hub Scrapers Package.
"""

import logging
import requests
from career_hub.models.config import load_config
from career_hub.models.storage import get_from_cache, save_to_cache
from career_hub.controllers.scrapers.base import HTTP_HEADERS, quick_prefilter_reject
from career_hub.controllers.scrapers.hh import fetch_hh_vacancies, fetch_hh_vacancy_full
from career_hub.controllers.scrapers.habr import fetch_habr_vacancies, fetch_habr_vacancy_by_id, build_habr_rss_url
from career_hub.controllers.scrapers.getmatch import fetch_getmatch_vacancies
from career_hub.controllers.scrapers.superjob import fetch_superjob_vacancies, DEFAULT_SUPERJOB_KEY
from career_hub.controllers.scrapers.linkedin import fetch_linkedin_vacancies, fetch_linkedin_vacancy_full
from career_hub.controllers.scrapers.telegram import (
    fetch_tg_channel_vacancies,
    extract_telegram_contacts,
)
from career_hub.controllers.scrapers.rabota import fetch_rabota_vacancies, fetch_rabota_vacancy_full
from career_hub.controllers.scrapers.trudvsem import fetch_trudvsem_vacancies
from career_hub.controllers.scrapers.remotive import fetch_remotive_vacancies
from career_hub.controllers.scrapers.vseti import fetch_vseti_vacancies, fetch_vseti_vacancy_full

logger = logging.getLogger("career_hub.scrapers")


def fetch_vacancy_by_id(v_id):
    cached = get_from_cache(v_id)
    if cached and len(cached.get("description", "")) > 80:
        return cached

    if v_id.startswith("hh_"):
        raw_id = v_id.replace("hh_", "")
        v = fetch_hh_vacancy_full(raw_id)
        if v:
            save_to_cache(v)
            return v

    elif v_id.startswith("habr_") or v_id.isdigit():
        v = fetch_habr_vacancy_by_id(v_id)
        if v:
            save_to_cache(v)
            return v

    elif v_id.startswith("vseti_"):
        raw_id = v_id.replace("vseti_", "")
        url = f"https://www.vseti.app/vakansii/{raw_id}"
        desc, sal = fetch_vseti_vacancy_full(url)
        if desc:
            cached = cached or {}
            cached.update({"id": v_id, "raw_id": raw_id, "source": "vseti", "source_name": "СЕТИ", "link": url, "description": desc, "salary": sal})
            save_to_cache(cached)
            return cached

    elif v_id.startswith("rabota_"):
        raw_id = v_id.replace("rabota_", "")
        desc = fetch_rabota_vacancy_full(raw_id)
        if desc:
            cached = cached or {}
            cached.update({"id": v_id, "raw_id": raw_id, "source": "rabota", "source_name": "Работа.ру", "description": desc})
            save_to_cache(cached)
            return cached

    elif v_id.startswith("sj_"):
        raw_id = v_id.replace("sj_", "")
        try:
            config = load_config()
            sj_key = config.get("superjob_secret_key") or DEFAULT_SUPERJOB_KEY
            r = requests.get(f"https://api.superjob.ru/2.0/vacancies/{raw_id}/", headers={"X-Api-App-Id": sj_key, "User-Agent": "SuperJob-Bot/1.0"}, timeout=10)
            if r.status_code == 200:
                d = r.json()
                cand = d.get("candidat", "") or ""
                work = d.get("work", "") or ""
                full_desc = f"{cand}\n\n{work}".strip()
                cached = cached or {}
                cached.update({"id": v_id, "raw_id": raw_id, "source": "superjob", "source_name": "SuperJob", "description": full_desc})
                save_to_cache(cached)
                return cached
        except Exception as e:
            logger.error(f"Ошибка получения деталей SuperJob ({raw_id}): {e}")

    return cached


__all__ = [
    "HTTP_HEADERS",
    "quick_prefilter_reject",
    "fetch_hh_vacancies",
    "fetch_hh_vacancy_full",
    "fetch_habr_vacancies",
    "fetch_habr_vacancy_by_id",
    "build_habr_rss_url",
    "fetch_getmatch_vacancies",
    "fetch_superjob_vacancies",
    "DEFAULT_SUPERJOB_KEY",
    "fetch_linkedin_vacancies",
    "fetch_linkedin_vacancy_full",
    "fetch_tg_channel_vacancies",
    "extract_telegram_contacts",
    "fetch_rabota_vacancies",
    "fetch_rabota_vacancy_full",
    "fetch_trudvsem_vacancies",
    "fetch_remotive_vacancies",
    "fetch_vseti_vacancies",
    "fetch_vseti_vacancy_full",
    "fetch_vacancy_by_id",
]
