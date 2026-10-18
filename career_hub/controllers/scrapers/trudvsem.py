"""
Работа России (opendata.trudvsem.ru) API Scraper.
"""

import logging
import requests
from career_hub.controllers.scrapers.base import HTTP_HEADERS
from career_hub.controllers.scrapers.rabota import NON_IT_TITLES_RABOTA

logger = logging.getLogger("career_hub.scrapers.trudvsem")


def fetch_trudvsem_vacancies(query="java", limit=40):
    """
    Официальный открытый REST API портала 'Работа России' (opendata.trudvsem.ru).
    Возвращает вакансии аккредитованных IT-компаний, НИИ, банков и предприятий РФ.
    """
    clean_q = requests.utils.quote(str(query))
    url = f"https://opendata.trudvsem.ru/api/v1/vacancies?text={clean_q}"
    results = []
    try:
        resp = requests.get(url, headers=HTTP_HEADERS, timeout=25)
        if resp.status_code == 200:
            data = resp.json()
            vac_list = data.get("results", {}).get("vacancies", [])
            for item in vac_list[:limit]:
                vac = item.get("vacancy", {})
                vid = vac.get("id")
                if not vid:
                    continue
                title = vac.get("job-name", "Без названия")
                title_lower = title.lower()

                # Базовый отсев не-IT шума
                if any(k in title_lower for k in NON_IT_TITLES_RABOTA):
                    continue

                company = vac.get("company", {}).get("name", "Не указана")

                sal_min = vac.get("salary_min")
                sal_max = vac.get("salary_max")
                if sal_min and sal_max and sal_min != sal_max:
                    salary = f"{sal_min} - {sal_max} руб."
                elif sal_min or sal_max:
                    salary = f"{sal_min or sal_max} руб."
                else:
                    salary = ""

                duty = vac.get("duty", "")
                req = vac.get("requirement", {}).get("qualification", "")
                desc = f"{duty}\nТребования: {req}".strip()
                if not desc:
                    desc = f"Позиция {title} в {company}."

                vac_url = vac.get("vac_url") or f"https://trudvsem.ru/vacancy/card/{vac.get('company', {}).get('ogrn', '')}/{vid}"

                addresses = vac.get("addresses", {}).get("address", [])
                loc = ""
                if addresses and isinstance(addresses, list):
                    loc = addresses[0].get("location", "")
                elif isinstance(addresses, dict):
                    loc = addresses.get("location", "")

                results.append({
                    "id": f"trud_{vid}",
                    "raw_id": vid,
                    "title": title,
                    "company": company,
                    "salary": salary,
                    "location": loc,
                    "link": vac_url,
                    "description": desc,
                    "source": "trudvsem",
                    "source_name": "Работа России",
                    "source_emoji": "🇷🇺"
                })
    except Exception as e:
        logger.error(f"Ошибка сбора вакансий с Работа России ({query}): {e}")
    return results
