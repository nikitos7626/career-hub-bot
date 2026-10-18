"""
HeadHunter (hh.ru) Scraper and Detail Parser.
"""

import re
import html
import json
import time
import urllib.parse
import logging
import requests
from career_hub.controllers.scrapers.base import HTTP_HEADERS
from career_hub.models.vacancy import clean_html

logger = logging.getLogger("career_hub.scrapers.hh")


def fetch_hh_vacancies(query: str, experience: str = "noExperience", max_pages: int = 2, schedule: str = None) -> list:
    """Парсит поисковую выдачу hh.ru через встроенный Redux State."""
    encoded_q = urllib.parse.quote(query)
    results = []
    seen_page_vids = set()
    for page in range(max_pages):
        exp_part = f"&experience={experience}" if experience else ""
        sched_part = f"&schedule={schedule}" if schedule else ""
        url = f"https://hh.ru/search/vacancy?text={encoded_q}{exp_part}{sched_part}&order_by=publication_time&page={page}"
        try:
            resp = requests.get(url, headers=HTTP_HEADERS, timeout=12)
            if resp.status_code == 200:
                templates = re.findall(r'<template[^>]*>(.*?)</template>', resp.text, re.DOTALL)
                if templates:
                    data = json.loads(html.unescape(templates[0]))
                    vacancies = data.get("vacancySearchResult", {}).get("vacancies", [])
                    if not vacancies:
                        break
                    for v in vacancies:
                        vid = str(v.get("vacancyId", ""))
                        if not vid or vid in seen_page_vids:
                            continue
                        seen_page_vids.add(vid)
                        name = v.get("name", "")
                        company = v.get("company", {}).get("name", "Не указана")
                        url_link = v.get("links", {}).get("desktop") or f"https://hh.ru/vacancy/{vid}"

                        comp = v.get("compensation", {})
                        salary_str = ""
                        if comp and not comp.get("noCompensation"):
                            cur = comp.get("currencyCode", "RUR")
                            if comp.get("from") and comp.get("to"):
                                salary_str = f"от {comp['from']} до {comp['to']} {cur}"
                            elif comp.get("from"):
                                salary_str = f"от {comp['from']} {cur}"
                            elif comp.get("to"):
                                salary_str = f"до {comp['to']} {cur}"

                        exp = v.get("workExperience", experience)
                        tags_list = [t.get("text") for t in v.get("tags", []) if isinstance(t, dict) and t.get("text")]
                        tags_str = ", ".join(tags_list)

                        results.append({
                            "id": f"hh_{vid}",
                            "raw_id": vid,
                            "source": "hh",
                            "source_name": "HeadHunter",
                            "source_emoji": "🔴",
                            "title": name,
                            "company": company,
                            "salary": salary_str,
                            "link": url_link,
                            "description": f"Опыт: {exp}. Теги: {tags_str}"
                        })
            time.sleep(0.3)
        except Exception as e:
            logger.error(f"Ошибка парсинга HeadHunter ('{query}', {experience}, page={page}): {e}")
            break
    return results


def fetch_hh_vacancy_full(raw_id: str) -> dict | None:
    """Загружает полное детальное описание вакансии с hh.ru."""
    url = f"https://hh.ru/vacancy/{raw_id}"
    try:
        resp = requests.get(url, headers=HTTP_HEADERS, timeout=12)
        if resp.status_code == 200:
            templates = re.findall(r'<template[^>]*>(.*?)</template>', resp.text, re.DOTALL)
            if templates:
                data = json.loads(html.unescape(templates[0]))
                full = data.get("vacancyView", {}).get("vacancyFull", {}).get("vacancy", {})
                if full:
                    clean_desc = clean_html(full.get("description", ""))
                    comp = full.get("compensation", {})
                    salary_str = ""
                    if comp and not comp.get("noCompensation"):
                        cur = comp.get("currencyCode", "RUR")
                        if comp.get("from") and comp.get("to"):
                            salary_str = f"от {comp['from']} до {comp['to']} {cur}"
                        elif comp.get("from"):
                            salary_str = f"от {comp['from']} {cur}"
                        elif comp.get("to"):
                            salary_str = f"до {comp['to']} {cur}"

                    return {
                        "id": f"hh_{raw_id}",
                        "raw_id": raw_id,
                        "source": "hh",
                        "source_name": "HeadHunter",
                        "source_emoji": "🔴",
                        "title": full.get("name", ""),
                        "company": full.get("company", {}).get("name", "Не указана"),
                        "description": clean_desc,
                        "salary": salary_str,
                        "link": url
                    }
    except Exception as e:
        logger.error(f"Не удалось загрузить детальное описание hh.ru #{raw_id}: {e}")
    return None
