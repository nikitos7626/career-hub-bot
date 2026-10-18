"""
Habr Career (career.habr.com) Scraper and Parser.
"""

import re
import logging
import requests
import xml.etree.ElementTree as ET
from career_hub.controllers.scrapers.base import HTTP_HEADERS
from career_hub.models.vacancy import clean_html

logger = logging.getLogger("career_hub.scrapers.habr")


def fetch_habr_vacancies(query_url: str) -> list:
    """Парсит вакансии из RSS ленты Хабр Карьеры."""
    try:
        resp = requests.get(query_url, headers=HTTP_HEADERS, timeout=12)
        if resp.status_code != 200:
            logger.warning(f"Ошибка загрузки RSS {query_url}: статус {resp.status_code}")
            return []

        root = ET.fromstring(resp.content)
        items = root.findall("./channel/item")
        vacancies = []

        for item in items:
            guid = item.find("guid").text if item.find("guid") is not None else ""
            title = item.find("title").text if item.find("title") is not None else ""
            link = item.find("link").text if item.find("link") is not None else ""
            company = item.find("author").text if item.find("author") is not None else "Не указана"
            pub_date = item.find("pubDate").text if item.find("pubDate") is not None else ""
            description = item.find("description").text if item.find("description") is not None else ""

            vid = f"habr_{guid}" if not guid.startswith("habr_") else guid
            vacancies.append({
                "id": vid,
                "raw_id": guid,
                "source": "habr",
                "source_name": "Хабр Карьера",
                "source_emoji": "🔵",
                "title": title,
                "link": link,
                "company": company,
                "pub_date": pub_date,
                "salary": "",
                "description": clean_html(description)
            })

        return vacancies
    except Exception as e:
        logger.error(f"Ошибка парсинга RSS {query_url}: {e}")
        return []


def fetch_habr_vacancy_by_id(v_id: str) -> dict | None:
    """Загружает детальное описание вакансии с Хабр Карьеры по ID."""
    raw_id = v_id.replace("habr_", "")
    url = f"https://career.habr.com/vacancies/{raw_id}"
    try:
        resp = requests.get(url, headers=HTTP_HEADERS, timeout=12)
        if resp.status_code == 200:
            html_text = resp.text
            title_m = re.search(r'<title>([^<]+)</title>', html_text)
            raw_title = title_m.group(1) if title_m else f"Вакансия #{raw_id}"
            title = raw_title.replace("— Хабр Карьера", "").replace("Вакансия", "").strip()
            company = "Не указана"
            if "в компании" in title:
                parts = title.split("в компании")
                title = parts[0].strip()
                company = parts[1].strip()

            desc_m = re.search(r'<meta name="description" content="([^"]+)"', html_text)
            desc = desc_m.group(1) if desc_m else ""

            return {
                "id": f"habr_{raw_id}",
                "raw_id": raw_id,
                "source": "habr",
                "source_name": "Хабр Карьера",
                "source_emoji": "🔵",
                "title": title,
                "company": company,
                "link": url,
                "salary": "",
                "description": clean_html(desc)
            }
    except Exception as e:
        logger.error(f"Не удалось загрузить детальное описание Хабр #{raw_id}: {e}")
    return None
