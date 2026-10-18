"""
Remotive (remotive.com) Global Remote Jobs Scraper.
"""

import logging
import requests
from career_hub.controllers.scrapers.base import HTTP_HEADERS
from career_hub.models.vacancy import clean_html

logger = logging.getLogger("career_hub.scrapers.remotive")


def fetch_remotive_vacancies() -> list:
    """Запрашивает глобальные удаленные вакансии через публичный API Remotive."""
    results = []
    for cat in ["software-dev", "devops"]:
        try:
            url = f"https://remotive.com/api/remote-jobs?category={cat}&limit=15"
            resp = requests.get(url, headers=HTTP_HEADERS, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                for job in data.get("jobs", [])[:15]:
                    jid = str(job.get("id"))
                    results.append({
                        "id": f"rem_{jid}",
                        "raw_id": jid,
                        "source": "remotive",
                        "source_name": "Remotive Remote",
                        "source_emoji": "🟢",
                        "title": job.get("title", ""),
                        "company": job.get("company_name", "Remote IT Company"),
                        "salary": job.get("salary", ""),
                        "link": job.get("url", ""),
                        "description": clean_html(job.get("description", ""))
                    })
        except Exception as e:
            logger.error(f"Ошибка запроса Remotive API ({cat}): {e}")
    return results
