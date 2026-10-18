"""
Rabota.ru (rabota.ru) Scraper for Sber ecosystem & Russian IT vacancies.
"""

import re
import html
import logging
import requests
from career_hub.controllers.scrapers.base import HTTP_HEADERS

logger = logging.getLogger("career_hub.scrapers.rabota")

NON_IT_TITLES_RABOTA = [
    "турбин", "газов", "окон", "недвижим", "бариста", "повар", "риелтор", "досмотр",
    "шлифовщик", "парикмахер", "водитель", "курьер", "уборщ", "склад", "продавец",
    "кассир", "бухгалтер", "юрист", "механик", "электрик", "слесарь", "монтажник",
    "врач", "медсестр", "фармацевт", "охранник", "грузчик", "комплектовщик", "токарь",
    "расточник", "станк", "чпу", "камн", "автомобил", "ремонт", "оклейк", "производств"
]

IT_KEYWORDS_RABOTA = [
    "java", "spring", "jvm", "разработчик", "программист", "developer", "backend",
    "бэкенд", "engineer", "инженер-программист", "тестировщик", "qa", "aqa", "devops",
    "data science", "it-стажер", "it-стажёр", "it стажер", "it стажёр", "fullstack",
    "full-stack", "бэкэнд", "администрировани", "аналитик"
]


def fetch_rabota_vacancies(query: str) -> list:
    """Парсер вакансий с Работа.ру (rabota.ru)."""
    clean_q = requests.utils.quote(str(query))
    url = f"https://www.rabota.ru/vacancy/?query={clean_q}&all_regions=1"
    results = []
    try:
        resp = requests.get(url, headers=HTTP_HEADERS, timeout=12)
        if resp.status_code == 200:
            articles = re.findall(r'<article[^>]*class="[^"]*vacancy-preview-card[^"]*"[^>]*>(.*?)</article>', resp.text, re.DOTALL)
            for art in articles:
                link_m = re.search(r'href="(/vacancy/(\d+)/?[^"]*)"[^>]*>(.*?)</a>', art, re.DOTALL)
                if not link_m:
                    continue
                rel_link = link_m.group(1).split('?')[0]
                vac_id = link_m.group(2)
                title = html.unescape(re.sub(r'<[^>]+>', '', link_m.group(3)).strip())
                title_lower = title.lower()

                if any(k in title_lower for k in NON_IT_TITLES_RABOTA):
                    continue

                if not any(k in title_lower for k in IT_KEYWORDS_RABOTA):
                    continue

                if "java" in str(query).lower() and not any(k in title_lower for k in ["java", "jvm", "стажер", "стажёр", "junior", "intern", "trainee"]):
                    continue

                comp_m = re.search(r'class="[^"]*company-name[^"]*"[^>]*>(.*?)</span>', art, re.DOTALL)
                company = html.unescape(re.sub(r'<[^>]+>', '', comp_m.group(1)).strip()) if comp_m else "Работа.ру"

                sal_m = re.search(r'class="[^"]*vacancy-preview-card__salary[^"]*"[^>]*>(.*?)</div>', art, re.DOTALL)
                salary = ""
                if sal_m:
                    salary = ' '.join(html.unescape(re.sub(r'<[^>]+>', ' ', sal_m.group(1))).split())

                loc_m = re.search(r'class="[^"]*address-text[^"]*"[^>]*>(.*?)<', art, re.DOTALL)
                loc = html.unescape(loc_m.group(1).strip()) if loc_m else ""

                desc_m = re.search(r'class="[^"]*short-description[^"]*"[^>]*>(.*?)</div>', art, re.DOTALL)
                desc = ""
                if desc_m:
                    desc = ' '.join(html.unescape(re.sub(r'<[^>]+>', ' ', desc_m.group(1))).split())
                if not desc:
                    desc = f"{title}. Компания: {company}. Локация: {loc}"

                full_link = f"https://www.rabota.ru{rel_link}"
                results.append({
                    "id": f"rabota_{vac_id}",
                    "raw_id": vac_id,
                    "title": title,
                    "company": company,
                    "salary": salary,
                    "location": loc,
                    "link": full_link,
                    "description": desc,
                    "source": "rabota",
                    "source_name": "Работа.ру",
                    "source_emoji": "🔴"
                })
    except Exception as e:
        logger.error(f"Ошибка сбора вакансий с Работа.ру ({query}): {e}")
    return results


def fetch_rabota_vacancy_full(vac_id: str) -> str:
    """Получение полного описания вакансии с Работа.ру."""
    url = f"https://www.rabota.ru/vacancy/{vac_id}/"
    try:
        resp = requests.get(url, headers=HTTP_HEADERS, timeout=10)
        if resp.status_code == 200:
            matches = re.findall(r'description:"(.*?)"(?=,\w+:)', resp.text)
            best = ""
            for m in matches:
                if len(m) > len(best):
                    best = m
            if best:
                try:
                    decoded = best.encode('utf-8').decode('unicode-escape')
                    decoded = decoded.replace(r'\"', '"').replace(r'\/', '/')
                    clean = re.sub(r'<[^>]+>', ' ', decoded)
                    clean = html.unescape(clean)
                    return ' '.join(clean.split()).strip()
                except Exception:
                    pass
    except Exception as e:
        logger.error(f"Ошибка получения деталей вакансии Работа.ру ({vac_id}): {e}")
    return ""
