"""
Vseti (vseti.app) IT & Digital Vacancies Scraper.
"""

import re
import html
import time
import logging
import requests
from career_hub.controllers.scrapers.base import HTTP_HEADERS
from career_hub.models.vacancy import clean_html

logger = logging.getLogger("career_hub.scrapers.vseti")


def fetch_vseti_vacancy_full(url: str):
    """Загружает детальное описание вакансии с vseti.app."""
    try:
        r = requests.get(url, headers=HTTP_HEADERS, timeout=8)
        if r.status_code != 200:
            return "", ""

        m = re.search(r'<div[^>]*class="[^"]*w-richtext[^"]*"[^>]*>(.*?)</div>', r.text, re.DOTALL)
        desc = ""
        if m:
            desc = clean_html(m.group(1))

        salary = ""
        sal_m = re.search(r'(\d+[\d\s]*(?:–|-)\s*\d+[\d\s]*\s*(?:₽|руб|\$|EUR|usd))', desc, re.I)
        if not sal_m:
            sal_m = re.search(r'((?:от|до)\s*\d+[\d\s]*\s*(?:₽|руб|\$|EUR|usd))', desc, re.I)
        if sal_m:
            salary = sal_m.group(1).strip()

        return desc, salary
    except Exception as e:
        logger.error(f"Ошибка загрузки деталей {url}: {e}")
        return "", ""


def fetch_vseti_vacancies(max_pages: int = 6) -> list:
    """Парсит вакансии с платформы vseti.app."""
    results = []
    seen_ids = set()

    for p in range(1, max_pages + 1):
        url = f"https://www.vseti.app/jobs?3835ea8c_page={p}"
        try:
            r = requests.get(url, headers=HTTP_HEADERS, timeout=10)
            if r.status_code != 200:
                break

            cards = re.findall(r'<a([^>]*class="[^"]*card-jobs[^"]*"[^>]*)>(.*?)</a>', r.text, re.DOTALL)
            if not cards:
                break

            for tag_attrs, inner in cards:
                href_m = re.search(r'href="([^"]+)"', tag_attrs)
                id_m = re.search(r'id="([^"]+)"', tag_attrs)
                title_m = re.search(r'fs-cmsfilter-field="jobs-name"[^>]*>(.*?)</', inner)
                comp_m = re.search(r'fs-cmsfilter-field="company-name"[^>]*>(.*?)</', inner)
                format_m = re.search(r'fs-cmsfilter-field="format"[^>]*>(.*?)</', inner)
                location_m = re.search(r'fs-cmsfilter-field="location"[^>]*>(.*?)</', inner)

                link = href_m.group(1) if href_m else ""
                oid = id_m.group(1) if id_m else (link.split("/")[-1] if link else "")
                if not oid or oid in seen_ids:
                    continue
                seen_ids.add(oid)

                title = html.unescape(title_m.group(1).strip()) if title_m else ""
                comp = html.unescape(comp_m.group(1).strip()) if comp_m else "Не указана"
                fmt = html.unescape(format_m.group(1).strip()) if format_m else ""
                loc = html.unescape(location_m.group(1).strip()) if location_m else ""

                t_low = title.lower()
                if not any(k in t_low for k in ["java", "spring", "jvm", "kotlin", "backend", "бэкенд", "разработчик", "developer", "vpn", "стажер", "стажёр", "junior"]):
                    continue

                if any(k in t_low for k in ["qa", "тестиров", "ios", "android", "flutter", "react", "frontend", "дизайн", "маркетолог", "менеджер", "копирайтер", "recruiter", "hr"]):
                    continue

                time.sleep(0.3)
                desc, salary = fetch_vseti_vacancy_full(link)

                loc_str = fmt
                if loc and loc != "Другое" and loc not in loc_str:
                    loc_str = f"{fmt} ({loc})" if fmt else loc

                results.append({
                    "id": f"vseti_{oid}",
                    "raw_id": oid,
                    "source": "vseti",
                    "source_name": "СЕТИ",
                    "source_emoji": "🌐",
                    "title": title,
                    "company": comp,
                    "salary": salary,
                    "location": loc_str,
                    "link": link,
                    "description": desc
                })
        except Exception as e:
            logger.error(f"Ошибка запроса vseti.app (стр {p}): {e}")
            break
    return results
