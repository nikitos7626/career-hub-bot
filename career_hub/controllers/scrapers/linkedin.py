"""
LinkedIn Guest Search and Details API Scraper.
"""

import re
import html
import time
import logging
import requests

logger = logging.getLogger("career_hub.scrapers.linkedin")

HTTP_HEADERS_LI = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9,ru;q=0.8"
}


def fetch_linkedin_vacancies(query_item, max_results: int = 15) -> list:
    """
    Публичный гостевой поиск вакансий LinkedIn без авторизации и платных API.
    Поддерживает фильтры опыта: f_E=1 (Internship), f_E=2 (Entry level)
    и workplace type: f_WT=2 (Remote)
    """
    if isinstance(query_item, dict):
        kw = query_item.get("keywords", "Junior Java")
        loc = query_item.get("location", "Remote")
        f_wt = query_item.get("f_WT", "2")
    else:
        kw = str(query_item)
        loc = "Remote"
        f_wt = "2"

    wt_param = f"&f_WT={f_wt}" if f_wt else ""
    url = f"https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?keywords={requests.utils.quote(kw)}&location={requests.utils.quote(loc)}&f_E=1,2{wt_param}&start=0"
    results = []
    try:
        resp = requests.get(url, headers=HTTP_HEADERS_LI, timeout=12)
        if resp.status_code == 200:
            cards = re.findall(r'<div class="[^"]*base-search-card[^"]*"(.*?)</li>', resp.text, re.DOTALL)
            for c in cards[:max_results]:
                jid_m = re.search(r'data-entity-urn="urn:li:jobPosting:(\d+)"', c)
                if not jid_m:
                    continue
                jid = jid_m.group(1)

                title_m = re.search(r'<h3 class="base-search-card__title">\s*(.*?)\s*</h3>', c, re.DOTALL)
                comp_m = re.search(r'<h4 class="base-search-card__subtitle">\s*(.*?)\s*</h4>', c, re.DOTALL)
                loc_m = re.search(r'<span class="job-search-card__location">\s*(.*?)\s*</span>', c, re.DOTALL)
                link_m = re.search(r'<a class="base-card__full-link[^"]*"\s+href="([^"]+)"', c)

                title = re.sub(r'<[^>]+>', '', html.unescape(title_m.group(1))).strip() if title_m else "Без названия"
                company = re.sub(r'<[^>]+>', '', html.unescape(comp_m.group(1))).strip() if comp_m else "Не указана"
                location = re.sub(r'<[^>]+>', '', html.unescape(loc_m.group(1))).strip() if loc_m else ""
                link = link_m.group(1).split("?")[0] if link_m else f"https://www.linkedin.com/jobs/view/{jid}"

                results.append({
                    "id": f"li_{jid}",
                    "raw_id": jid,
                    "source": "linkedin",
                    "source_name": "LinkedIn",
                    "source_emoji": "🌐",
                    "title": title,
                    "company": company,
                    "location": location,
                    "salary": "Не указана",
                    "link": link,
                    "description": f"Позиция {title} в {company}. Локация: {location}."
                })
        elif resp.status_code == 429:
            logger.warning("LinkedIn Guest API Rate limit (429). Пауза 5 сек перед следующими запросами...")
            time.sleep(5.0)
        else:
            logger.warning(f"LinkedIn HTTP error {resp.status_code}")
    except Exception as e:
        logger.error(f"Ошибка сбора вакансий LinkedIn ({query_item}): {e}")
    return results


def fetch_linkedin_vacancy_full(raw_id: str) -> str:
    """Получение полного описания вакансии LinkedIn через публичный гостевой эндпоинт."""
    url = f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{raw_id}"
    try:
        resp = requests.get(url, headers=HTTP_HEADERS_LI, timeout=10)
        if resp.status_code == 200:
            desc_m = re.search(r'<div class="show-more-less-html__markup[^"]*">\s*(.*?)\s*</div>', resp.text, re.DOTALL)
            if desc_m:
                raw_html = desc_m.group(1)
                clean_text = re.sub(r'<br\s*/?>', '\n', raw_html)
                clean_text = re.sub(r'<[^>]+>', '', clean_text)
                return html.unescape(clean_text).strip()
        elif resp.status_code == 429:
            logger.warning(f"LinkedIn full vacancy 429 для ID {raw_id}. Пауза 3 сек...")
            time.sleep(3.0)
    except Exception as e:
        logger.error(f"Ошибка получения деталей вакансии LinkedIn ({raw_id}): {e}")
    return ""
