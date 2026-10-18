"""
GetMatch (getmatch.ru) JSON API Scraper.
"""

import logging
import requests
from career_hub.controllers.scrapers.base import HTTP_HEADERS
from career_hub.models.vacancy import clean_html

logger = logging.getLogger("career_hub.scrapers.getmatch")


def fetch_getmatch_vacancies() -> list:
    """Запрашивает вакансии через публичный JSON API GetMatch."""
    results = []
    seen_ids = set()
    for sp in ["java_scala", "kotlin"]:
        url = f"https://getmatch.ru/api/offers?page=1&sp={sp}"
        try:
            resp = requests.get(url, headers=HTTP_HEADERS, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                for off in data.get("offers", []):
                    oid = str(off.get("id"))
                    if oid in seen_ids:
                        continue
                    seen_ids.add(oid)

                    title = off.get("position", "")
                    comp_obj = off.get("company")
                    comp_name = "IT-Компания"
                    if isinstance(comp_obj, dict) and comp_obj.get("name"):
                        comp_name = comp_obj.get("name")
                    elif off.get("company_name"):
                        comp_name = off.get("company_name")

                    url_path = off.get("url", "")
                    link = f"https://getmatch.ru{url_path}" if url_path else f"https://getmatch.ru/vacancies/{oid}"
                    desc = clean_html(off.get("offer_description", ""))

                    t_low = title.lower()
                    d_low = desc.lower()
                    if not any(k in t_low or k in d_low for k in ["junior", "джуниор", "джун", "стажер", "стажёр", "trainee", "intern"]):
                        continue

                    salary_str = ""
                    s_from = off.get("salary_display_from")
                    s_to = off.get("salary_display_to")
                    cur = off.get("salary_currency") or "RUB"
                    if s_from and s_to:
                        salary_str = f"от {s_from} до {s_to} {cur}"
                    elif s_from:
                        salary_str = f"от {s_from} {cur}"
                    elif s_to:
                        salary_str = f"до {s_to} {cur}"

                    loc_items = off.get("location_items", [])
                    loc_labels = [it.get("label", "") for it in loc_items if it.get("label")]
                    is_gm_remote = any(it.get("format") == "remote" for it in loc_items)
                    loc_str = ", ".join(loc_labels)
                    if is_gm_remote:
                        loc_str = f"Удалённо ({loc_str})" if loc_str else "Удалённо"

                    results.append({
                        "id": f"gm_{oid}",
                        "raw_id": oid,
                        "source": "getmatch",
                        "source_name": "GetMatch",
                        "source_emoji": "🟣",
                        "title": title,
                        "company": comp_name,
                        "salary": salary_str,
                        "location": loc_str,
                        "link": link,
                        "description": desc
                    })
        except Exception as e:
            logger.error(f"Ошибка запроса GetMatch API ({sp}): {e}")
    return results
