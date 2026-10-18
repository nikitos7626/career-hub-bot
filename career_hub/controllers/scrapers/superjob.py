"""
SuperJob (superjob.ru) API Scraper.
"""

import logging
import requests

logger = logging.getLogger("career_hub.scrapers.superjob")

DEFAULT_SUPERJOB_KEY = "v1.r00000001769221ce7a8d0a2dd47c132c9fed70d06afa65121b02277f364d08fd_api3web.5be7a889f65ecefb876179d577ff16aff5a9db59"


def fetch_superjob_vacancies(query_item, count: int = 40, secret_key: str = None) -> list:
    """Ищет вакансии через официальный REST API SuperJob."""
    key = secret_key or DEFAULT_SUPERJOB_KEY
    params = {
        "order_by[date]": "desc",
        "count": count
    }
    if isinstance(query_item, dict):
        params.update(query_item)
    else:
        params["keyword"] = str(query_item)

    headers = {
        "X-Api-App-Id": key,
        "User-Agent": "SuperJob-Career-Bot/1.0"
    }
    try:
        resp = requests.get("https://api.superjob.ru/2.0/vacancies/", headers=headers, params=params, timeout=12)
        if resp.status_code == 200:
            data = resp.json()
            results = []
            for obj in data.get("objects", []):
                vid = str(obj.get("id", ""))
                if not vid:
                    continue
                title = obj.get("profession", "")
                company = obj.get("firm_name", "Не указана")
                link = obj.get("link", f"https://www.superjob.ru/vakansii/{vid}.html")
                cand_text = obj.get("candidat", "") or ""
                work_text = obj.get("work", "") or ""
                desc = f"{cand_text}\n\n{work_text}".strip()

                p_from = obj.get("payment_from")
                p_to = obj.get("payment_to")
                cur = obj.get("currency", "rub")
                salary_str = ""
                if p_from and p_to:
                    salary_str = f"от {p_from} до {p_to} {cur}"
                elif p_from:
                    salary_str = f"от {p_from} {cur}"
                elif p_to:
                    salary_str = f"до {p_to} {cur}"

                town = obj.get("town", {}).get("title", "")
                is_rem = obj.get("work_type", {}).get("id") == 2 or obj.get("place_of_work", {}).get("id") == 2 or ("удал" in title.lower()) or ("удал" in desc.lower())
                loc_str = f"Удалённо ({town})" if (is_rem and town) else ("Удалённо" if is_rem else town)

                results.append({
                    "id": f"sj_{vid}",
                    "raw_id": vid,
                    "source": "superjob",
                    "source_name": "SuperJob",
                    "source_emoji": "💼",
                    "title": title,
                    "company": company,
                    "salary": salary_str,
                    "location": loc_str,
                    "link": link,
                    "description": desc
                })
            return results
        elif resp.status_code == 403:
            logger.warning("SuperJob API вернул 403 (необходим действующий X-Api-App-Id ключ).")
    except Exception as e:
        logger.error(f"Ошибка поиска SuperJob ({query_item}): {e}")
    return []
