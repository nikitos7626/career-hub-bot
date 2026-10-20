"""
Habr Career (career.habr.com) Scraper and Parser.
"""

import re
import logging
import urllib.parse
import requests
import xml.etree.ElementTree as ET
from career_hub.controllers.scrapers.base import HTTP_HEADERS
from career_hub.models.vacancy import clean_html

logger = logging.getLogger("career_hub.scrapers.habr")


def build_habr_rss_url(query_item) -> str:
    """
    Преобразует строку или словарь параметров поискового запроса в корректный строковый URL RSS-ленты Хабр Карьеры.
    Предотвращает сбой 'No connection adapters were found', если передаётся dict.
    """
    if not query_item:
        return "https://career.habr.com/vacancies/rss"

    if isinstance(query_item, str):
        query_str = query_item.strip()
        if not query_str:
            return "https://career.habr.com/vacancies/rss"
        if query_str.startswith("http://") or query_str.startswith("https://"):
            return query_str
        return f"https://career.habr.com/vacancies/rss?q={urllib.parse.quote_plus(query_str)}"
    elif isinstance(query_item, dict):
        if query_item.get("url") and isinstance(query_item["url"], str) and query_item["url"].strip().startswith("http"):
            return query_item["url"].strip()

        params = []
        q = str(query_item.get("query") or query_item.get("q") or "").strip()
        if q:
            params.append(f"q={urllib.parse.quote_plus(q)}")

        # Divisions (e.g. ['development', 'backend'])
        divs = query_item.get("divisions") or []
        if isinstance(divs, str):
            divs = [divs]
        for d in divs:
            if d:
                params.append(f"divisions[]={urllib.parse.quote_plus(str(d).strip())}")

        # Qualifications (s[]: 1 = стажер, 2 = junior, etc.)
        s_vals = query_item.get("s") or query_item.get("qualifications") or []
        if isinstance(s_vals, (int, str)):
            s_vals = [s_vals]
        for s in s_vals:
            if s:
                params.append(f"s[]={urllib.parse.quote_plus(str(s).strip())}")

        qs = "&".join(params)
        return f"https://career.habr.com/vacancies/rss?{qs}" if qs else "https://career.habr.com/vacancies/rss"
    else:
        return "https://career.habr.com/vacancies/rss"


def fetch_habr_vacancies(query_url) -> list:
    """Парсит вакансии из RSS ленты Хабр Карьеры."""
    if not query_url:
        return []
    url = build_habr_rss_url(query_url)
    if not url or not url.startswith(("http://", "https://")):
        return []
    try:
        resp = requests.get(url, headers=HTTP_HEADERS, timeout=12)
        if resp.status_code != 200:
            logger.warning(f"Ошибка загрузки RSS {url}: статус {resp.status_code}")
            return []

        root = ET.fromstring(resp.content)
        items = root.findall("./channel/item")
        vacancies = []

        for item in items:
            guid_el = item.find("guid")
            guid = (guid_el.text or "").strip() if guid_el is not None else ""
            title_el = item.find("title")
            title = (title_el.text or "").strip() if title_el is not None else ""
            link_el = item.find("link")
            link = (link_el.text or "").strip() if link_el is not None else ""
            author_el = item.find("author")
            company = (author_el.text or "").strip() if author_el is not None else "Не указана"
            pub_date_el = item.find("pubDate")
            pub_date = (pub_date_el.text or "").strip() if pub_date_el is not None else ""
            desc_el = item.find("description")
            description = (desc_el.text or "").strip() if desc_el is not None else ""

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
        logger.error(f"Ошибка парсинга RSS {url}: {e}")
        return []


def fetch_habr_vacancy_by_id(v_id: str) -> dict | None:
    """Загружает детальное описание вакансии с Хабр Карьеры по ID."""
    raw_id = str(v_id or "").replace("habr_", "").strip()
    if not raw_id:
        return None
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
