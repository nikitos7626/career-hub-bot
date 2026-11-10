"""
AI Controller for Career Hub.
Handles Groq LLM scoring, structured JSON analysis, and customized cover letter generation.
"""

import os
import sys
import re
import json
import time
import html
import logging
import requests
from career_hub.models.config import load_config, CANDIDATE_PROFILE
from career_hub.models.vacancy import clean_html

logger = logging.getLogger("career_hub.ai")


def analyze_vacancy_with_groq(api_key: str, model: str, title: str, company: str, description: str, delay: float = 2.5) -> dict | None:
    """Оценивает соответствие вакансии профилю кандидата через Groq LLM."""
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    short_desc = description[:1800] if description else ""
    cfg_data = load_config()
    candidate_profile = (cfg_data.get("candidate_profile") or CANDIDATE_PROFILE).strip()

    prompt = f"""
Ты — персональный карьерный ассистент и технический рекрутер для IT-специалиста.

{candidate_profile}

Проанализируй вакансию:
- Название: {title}
- Компания: {company}
- Описание и требования: {short_desc}

Твоя задача — беспристрастно оценить вакансию:
1. Подходит ли вакансия по направлению РАЗРАБОТКИ ПО:
   - "java": Junior Java Backend Developer (Java 21, Spring Boot, REST API, SQL/PostgreSQL, Docker)
   - "python_dev": Junior Python Backend Developer (Python 3, FastAPI, Flask, REST API, SQL, Docker, скрипты)
   - "ai_dev": AI & LLM Engineer / Разработчик ИИ (Spring AI, LangChain, Python AI, LLM API, AI-агенты, RAG)
   - "fullstack_js": Junior / Trainee Fullstack JS или Frontend разработчик (JavaScript, TypeScript, React, Node.js, Web-разработка, SPA, верстка с логикой)
   - "vpn_dev": Разработчик VPN сервисов и протоколов (Xray, Remnawave, VLESS, Sing-box, сетевые сервисы)
   - "backend": Junior / Intern Backend Developer (бэкенд разработка, REST API)
2. Отсев не-разработки и запрещенных сфер:
   - ПРЕПОДАВАНИЕ, НАСТАВНИЧЕСТВО, РЕПЕТИТОРСТВО, МЕНТОРСТВО, КУРАТОРСТВО — СТРОГО suitable=false, match_score=1! Кандидата интересует ТОЛЬКО инженерная разработка ПО!
   - ТЕСТИРОВАНИЕ (ручной QA, AQA автотесты, тестировщик ПО) — СТРОГО suitable=false, match_score=1!
   - Саппорт, техподдержка пользователей L1, разметка данных / AI-тренеры, операторы ПК, холодные продажи, эникей/ремонт ПК — ставь suitable=false, match_score=1.
   - Дайджесты и подборки вакансий: если в тексте перечислено несколько разных вакансий/ссылок — СТРОГО suitable=false, match_score=1.
3. Опыт работы:
   - Для позиций Junior / Стажёр / Trainee / Начинающий разработчик: стандартная HR-формулировка 'требуемый опыт: 1-3 года' или 'опыт от 1 года' в тексте вакансии НЕ является поводом для отказа, если по задачам и стеку это начальная позиция.
   - Отсекать (suitable=false, match_score=1..2) ТОЛЬКО если это реальный Middle+/Senior/Lead с жестким требованием от 2-3+ лет боевого продакшн-опыта или руководства командой.
4. Локация, удаленка и зарплата:
   - УДАЛЕННЫЙ ФОРМАТ (Remote / Удаленка / Дистанционно): подходит независимо от того, указана зарплата или нет.
   - СТАЖИРОВКИ (Intern / Стажер / Практикант / Trainee): подходят, даже если зарплата не указана или не оплачивается.
   - ЕСЛИ ЗАРПЛАТА НЕ УКАЗАНА: не отсекай, если стек и уровень подходят, а в recommendation посоветуй уточнить вилку на первом скрининге.
5. Вакансии LinkedIn / International:
   - Подходит, если это Remote для СНГ/Worldwide либо международная компания с оформлением удалёнщиков.
6. Черный список компаний из конфигурации — строго suitable=false, match_score=1.

Верни ответ СТРОГО в формате JSON:
{{
  "suitable": true/false,
  "role": "java" | "python_dev" | "ai_dev" | "fullstack_js" | "vpn_dev" | "backend",
  "match_score": целое число от 1 до 10,
  "education_warning": null,
  "verdict": "краткое резюме (1-2 предложения, почему подходит или почему нет)",
  "recommendation": "практический совет кандидату, что подчеркнуть в отклике"
}}
"""

    models_to_try = [model]
    for fallback in ["openai/gpt-oss-120b", "openai/gpt-oss-20b"]:
        if fallback not in models_to_try:
            models_to_try.append(fallback)

    for current_model in models_to_try:
        payload = {
            "model": current_model,
            "messages": [
                {"role": "system", "content": "Ты карьерный AI-консультант. Отвечай только валидным JSON."},
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.1,
            "response_format": {"type": "json_object"}
        }

        for attempt in range(2):
            try:
                time.sleep(delay)
                resp = requests.post(url, headers=headers, json=payload, timeout=15)

                if resp.status_code == 200:
                    content = resp.json()["choices"][0]["message"]["content"]
                    return json.loads(content)

                elif resp.status_code == 429:
                    err_msg = ""
                    try:
                        err_msg = resp.json().get("error", {}).get("message", "")
                    except Exception:
                        err_msg = resp.text

                    is_daily = any(k in err_msg.lower() for k in ["tokens per day", "tpd", "limit 200000", "exceeded your current quota"])
                    if is_daily:
                        logger.warning(f"Groq суточный лимит TPD на модели {current_model}. Немедленно переключаемся на следующую модель...")
                        break

                    raw_retry = resp.headers.get("Retry-After", "3")
                    try:
                        wait_sec = min(max(float(raw_retry), 2.5), 6.0)
                    except ValueError:
                        wait_sec = 3.0

                    if attempt == 0:
                        logger.warning(f"Groq TPM 429 на модели {current_model}. Ждём {wait_sec:.1f}с перед повторной попыткой...")
                        time.sleep(wait_sec)
                        continue
                    else:
                        logger.warning(f"Groq 429 на модели {current_model} после повтора. Переключаемся на резервную модель...")
                        break

                else:
                    logger.error(f"Groq API error ({resp.status_code}) на {current_model}: {resp.text}")
                    break

            except Exception as e:
                logger.error(f"Ошибка вызова Groq API ({current_model}, попытка {attempt+1}): {e}")
                time.sleep(1)

    return None


def analyze_vacancy(api_key: str, model: str, vacancy: dict, delay: float = 2.5) -> dict | None:
    """Обертка над analyze_vacancy_with_groq для словаря вакансии."""
    return analyze_vacancy_with_groq(
        api_key=api_key,
        model=model,
        title=vacancy.get("title", ""),
        company=vacancy.get("company", ""),
        description=vacancy.get("description", ""),
        delay=delay
    )


def get_portfolio_url() -> str:
    bot_mod = sys.modules.get("bot") or sys.modules.get("__main__")
    if bot_mod and hasattr(bot_mod, "load_config"):
        try:
            return bot_mod.load_config().get("portfolio_url") or os.environ.get("PORTFOLIO_URL") or ""
        except Exception:
            pass
    try:
        from career_hub.models.config import load_config
        return load_config().get("portfolio_url") or os.environ.get("PORTFOLIO_URL") or ""
    except Exception:
        return os.environ.get("PORTFOLIO_URL") or ""


def _build_role_links(cfg_data: dict, role_key: str, resume_url: str, portfolio_url: str) -> list[str]:
    links: list[str] = []
    if resume_url:
        links.append(f"• Резюме: {resume_url}")
    proj_map = cfg_data.get("project_links") or {}
    for entry in proj_map.get(role_key, []):
        if isinstance(entry, dict):
            label = entry.get("label", "Проект")
            url_val = entry.get("url", "")
            if url_val:
                links.append(f"• {label}: {url_val}")
        elif isinstance(entry, str) and entry.strip():
            links.append(entry.strip())
    if portfolio_url and not any(portfolio_url in x for x in links):
        links.append(f"• Живой проект (деплой): {portfolio_url}")
    return links


def generate_cover_letter(api_key: str, model: str, vacancy: dict) -> str:
    """Генерирует индивидуальное сопроводительное письмо с настраиваемыми ссылками на резюме и пет-проекты."""
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    clean_title = vacancy.get('title', '').replace("Требуется «", "").replace("»", "").strip()
    clean_company = vacancy.get('company', '').replace("Компания «", "").replace("»", "").strip()
    raw_desc = vacancy.get('description', '')
    clean_desc = clean_html(raw_desc)[:1800].strip()

    cfg_data = load_config()
    candidate_name = (cfg_data.get("candidate_name") or cfg_data.get("smtp_from_name") or "Кандидат").strip()
    resume_url = (vacancy.get("resume_url") or cfg_data.get("resume_quick_tunnel_url") or os.environ.get("RESUME_QUICK_TUNNEL_URL", "")).strip()
    if resume_url and not resume_url.endswith("/"):
        resume_url += "/"
    portfolio_url = (vacancy.get("portfolio_url") or cfg_data.get("portfolio_url") or get_portfolio_url()).strip()

    full_text = f"{clean_title} {clean_desc}".lower()
    is_vpn_dev = vacancy.get("role") == "vpn_dev" or any(k in full_text for k in ["remnawave", "xray", "vless", "sing-box"])
    is_java_ai = vacancy.get("role") in ["ai_dev", "java_ai"] or any(k in full_text for k in ["spring ai", "spring-ai", "langchain4j", "langchain", "ai agent", "ai-агент", "llm", "rag", "инженер ии", "разработчик ии", "ai engineer", "prompt", "промпт", "mcp", "gigachat", "gigacode"])
    is_python_backend = vacancy.get("role") == "python_dev" or (any(k in full_text for k in ["python", "fastapi", "django", "flask"]) and any(d in full_text for d in ["backend", "бэкенд", "разработчик", "developer", "стажер", "junior"]) and not ("java" in clean_title.lower() and "javascript" not in clean_title.lower()))
    is_fullstack_js = vacancy.get("role") == "fullstack_js" or (any(k in full_text for k in ["fullstack", "фулстек", "full stack", "react", "node", "javascript", "frontend", "front-end", "фронтенд"]) and not ("java" in clean_title.lower() and "javascript" not in clean_title.lower()))

    role_key = "vpn_dev" if is_vpn_dev else ("ai_dev" if is_java_ai else ("python_dev" if is_python_backend else ("fullstack_js" if is_fullstack_js else "java")))
    links_list = _build_role_links(cfg_data, role_key, resume_url, portfolio_url)
    links_block = "\n  ".join(links_list) if links_list else "• Ссылки на проекты и резюме предоставляются по запросу"

    if is_vpn_dev:
        sys_msg = (
            f"Ты — квалифицированный инженер и разработчик сетевой/VPN инфраструктуры {candidate_name}. "
            "Ты составляешь профессиональные, грамотные и зрелые сопроводительные письма. "
            "Твой стиль: деловой, сдержанный, глубоко технический. Без панибратства."
        )
        stack_summary = "Xray-core, VLESS (Reality, Vision), Sing-box, панели управления Remnawave и 3X-UI, маршрутизация трафика и администрирование Linux VPS."
    elif is_java_ai:
        sys_msg = (
            f"Ты — AI / LLM Integration & Backend разработчик {candidate_name}. "
            "Ты составляешь профессиональные, грамотные и лаконичные сопроводительные письма. "
            "Твой стиль: инженерный, современный, структурированный."
        )
        stack_summary = "AI / LLM интеграции (LLM API, Function Calling, RAG, AI-агенты, LangChain, Spring AI), бэкенд (Python/Java, REST API, PostgreSQL, Docker). Чистая архитектура, деплой на Linux VPS под Nginx с SSL."
    elif is_python_backend:
        sys_msg = (
            f"Ты — Junior Python Backend разработчик {candidate_name}. "
            "Ты составляешь профессиональные, грамотные и лаконичные сопроводительные письма. "
            "Твой стиль: инженерный, современный, структурированный."
        )
        stack_summary = "Python 3, FastAPI, PostgreSQL, Docker, REST API, чистая архитектура, самостоятельный деплой на Linux VPS под Nginx с SSL."
    elif is_fullstack_js:
        sys_msg = (
            f"Ты — Fullstack JS / Frontend разработчик {candidate_name}. "
            "Ты составляешь профессиональные, четкие и лаконичные сопроводительные письма. "
            "Стиль: живой, структурированный, уверенный."
        )
        stack_summary = "React 18 (MobX), Node.js (Express, Sequelize), TypeScript, JWT-авторизация, интеграция с REST API, деплой на Linux VPS под Nginx с SSL."
    else:
        sys_msg = (
            f"Ты — Junior Java Backend разработчик {candidate_name}. "
            "Ты составляешь профессиональные, грамотные и лаконичные сопроводительные письма. "
            "Твой стиль: деловой, инженерный, уверенный."
        )
        stack_summary = "Java 21, Spring Boot 3 (Spring Data JPA, Security), PostgreSQL, Docker, Clean Architecture и REST API, деплой на Linux VPS под Nginx с SSL."

    user_prompt = f"""Напиши профессиональное, убедительное сопроводительное письмо на позицию {clean_title}.

Кандидат: {candidate_name}
- Ссылки на проекты и резюме:
  {links_block}
- Стек и фокус: {stack_summary}

Вакансия:
- Позиция: {clean_title}
- Компания: {clean_company or 'Работодатель'}
- Описание:
{clean_desc}

СТРОГАЯ СТРУКТУРА ПИСЬМА:
1. Уважительное деловое приветствие («Здравствуйте! Откликаюсь на вакансию {clean_title}{' в компании ' + clean_company if clean_company else ''}.»).
2. Если есть ссылки на проекты/резюме выше — вставь их аккуратным списком.
3. Кратко покажи релевантность стека задачам вакансии (2–3 компактных абзаца, 80–120 слов).
4. Заверши деловым предложением обсудить задачи на собеседовании (готов выполнить тестовое задание).
Выдавай ТОЛЬКО готовый текст письма без лишних пометок и кавычек.
"""

    models_to_try = [model]
    for fallback in ["openai/gpt-oss-120b", "openai/gpt-oss-20b"]:
        if fallback not in models_to_try:
            models_to_try.append(fallback)

    for current_model in models_to_try:
        payload = {
            "model": current_model,
            "messages": [
                {"role": "system", "content": sys_msg},
                {"role": "user", "content": user_prompt}
            ],
            "temperature": 0.35
        }

        for attempt in range(2):
            try:
                resp = requests.post(url, headers=headers, json=payload, timeout=22)
                if resp.status_code == 200:
                    letter = resp.json()["choices"][0]["message"]["content"].strip()
                    if (letter.startswith('"') and letter.endswith('"')) or (letter.startswith('«') and letter.endswith('»')):
                        letter = letter[1:-1].strip()
                    for link_line in links_list:
                        url_token = link_line.split(": ", 1)[-1].strip()
                        if url_token and url_token not in letter:
                            letter = letter + f"\n{link_line}"
                    return letter
                elif resp.status_code == 429:
                    logger.warning(f"Groq Rate Limit (429) при генерации Cover Letter на {current_model}. Пробуем резервную...")
                    break
                else:
                    logger.error(f"Groq Cover Letter error ({resp.status_code}) на {current_model}: {resp.text}")
                    break
            except Exception as e:
                logger.error(f"Ошибка вызова Groq Cover Letter ({current_model}): {e}")
                time.sleep(1)

    comp_str = f" в компании {clean_company}" if clean_company else ""
    links_section = ("\n\nПрикрепляю ссылки на резюме и практические проекты:\n" + "\n".join(links_list)) if links_list else ""
    base_note = "Сетевой стек (TCP/IP, routing, DNS, TLS), Linux и Git понимаю." if is_vpn_dev else "Базу по бэкенду, ООП, SQL и Git понимаю."
    return (
        f"Здравствуйте! Откликаюсь на вакансию {clean_title}{comp_str}."
        f"{links_section}\n\n"
        f"Стек: {stack_summary}\n\n"
        f"{base_note} Готов выполнить тестовое задание."
    )


def format_ai_vacancy_message(v: dict, analysis: dict) -> str:
    """Форматирует карточку вакансии для мгновенного уведомления в Telegram."""
    clean_title = v["title"].replace("Требуется «", "").replace("»", "").strip()
    score = analysis.get("match_score", 0)
    role = analysis.get("role", "java")
    source_name = v.get("source_name", "IT-Площадка")
    source_emoji = v.get("source_emoji", "💼")

    esc_title = html.escape(clean_title)
    esc_company = html.escape(v.get("company", "Не указана"))
    esc_source_name = html.escape(source_name)

    if score >= 8:
        score_badge = f"🔥 <b>{score}/10 — Топ матч!</b>"
    elif score >= 6:
        score_badge = f"🎯 <b>{score}/10 — Релевантно</b>"
    else:
        score_badge = f"⚠️ <b>{score}/10 — Низкий матч</b>"

    role_titles = {
        "java": "☕ Junior Java Backend",
        "python_dev": "🐍 Python Backend Dev",
        "ai_dev": "🤖 AI & LLM Engineer",
        "fullstack_js": "⚡ Fullstack JS / React",
        "vpn_dev": "🛡️ VPN & Network Infrastructure",
        "backend": "⚡ Backend Developer"
    }
    role_str = role_titles.get(role, "💻 Разработка ПО")

    verdict = html.escape(analysis.get("verdict", "Подходит под стек кандидата."))
    rec = html.escape(analysis.get("recommendation", "Сделайте акцент на практических пет-проектах."))

    msg = (
        f"{source_emoji} <b>{esc_title}</b>\n"
        f"🏢 <b>{esc_company}</b> | {esc_source_name}\n"
    )
    if v.get("salary"):
        msg += f"💰 <b>{html.escape(v['salary'])}</b>\n"

    msg += (
        f"\n{score_badge}\n"
        f"📌 Направление: <b>{role_str}</b>\n\n"
        f"💡 <i>{verdict}</i>\n"
        f"🎯 <b>Совет:</b> {rec}\n\n"
        f"🔗 <a href='{v.get('link', '#')}'>Открыть вакансию на источнике</a>"
    )
    return msg
