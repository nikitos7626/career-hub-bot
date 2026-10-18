"""
AI Controller for Career Hub.
Handles Groq LLM scoring, structured JSON analysis, and customized cover letter generation.
"""

import os
import re
import json
import time
import html
import logging
import requests
from career_hub.models.config import load_config
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

    prompt = f"""
Ты — персональный карьерный ассистент и технический рекрутер для начинающего IT-специалиста Никиты (21 год).

Профиль кандидата:
- Образование: Неоконченное высшее (технический ВУЗ) + СПО (Информационные системы) + Школа 21 от Сбера.
- Главный фокус: Junior Java Backend Developer (Java 21, Spring Boot 3, PostgreSQL, Docker, Clean Architecture, REST API, многопоточность).
- Дополнительные целевые роли (РАЗРАБОТКА ПО):
  • AI & LLM Engineer / Разработчик ИИ (Spring AI, LangChain, OpenAI/Groq API, AI-агенты, RAG, pgvector, prompt engineering)
  • Junior Python Backend Developer (Python 3, FastAPI, Flask, REST API, PostgreSQL, Docker, ООП, скрипты автоматизации и интеграции)
  • Junior / Trainee Fullstack JS / Frontend Developer (JavaScript, TypeScript, React 18, Node.js, Express, Sequelize, REST API, MobX, Nginx, Linux VPS). Развернул и задеплоил живой веб-проект https://park.shadowkek.space
  • Разработчик VPN/прокси (Xray, Sing-box, Remnawave, сетевые протоколы)
  • Junior / Intern Backend Developer (бэкенд, REST API)
- Никита не имеет многолетнего коммерческого опыта в IT, но имеет сильные учебные и пет-проекты, понимание архитектуры и базы Computer Science.

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
   - ПРЕПОДАВАНИЕ, НАСТАВНИЧЕСТВО, РЕПЕТИТОРСТВО, МЕНТОРСТВО, КУРАТОРСТВО — СТРОГО suitable=false, match_score=1! Никиту интересует ТОЛЬКО инженерная разработка ПО, НЕ преподавание!
   - ТЕСТИРОВАНИЕ (ручной QA, AQA автотесты, тестировщик ПО) — СТРОГО suitable=false, match_score=1! Никакого тестирования и QA!
   - Саппорт, техподдержка пользователей L1, разметка данных / AI-тренеры, операторы ПК, холодные продажи, эникей/ремонт ПК — ставь suitable=false, match_score=1.
   - Чужие стеки без Java/JS/Python: Go, C#, .NET, PHP, Ruby, Rust, C/C++, 1C, мобильная разработка (iOS, Swift, Flutter, Android без Java), GameDev (Unity/Unreal) — СТРОГО ставь suitable=false, match_score=1..2!
   - Дайджесты и подборки вакансий: если в тексте перечислено несколько разных вакансий/ссылок — СТРОГО ставь suitable=false, match_score=1.
   - Для вакансий Junior/Trainee Python Backend на FastAPI/Flask — ставь suitable=true, role="python_dev", match_score=7..9!
   - Для вакансий Junior/Trainee AI / LLM Engineer (интеграция ИИ, нейросети, LLM) — ставь suitable=true, role="ai_dev", match_score=7..9!
   - Для вакансий Junior/Trainee Fullstack JS / Frontend на React/Node.js/TypeScript — ставь suitable=true, role="fullstack_js", match_score=7..9!
   - Для вакансий Junior/Trainee Java Developer (Java 21, Spring Boot) — ставь suitable=true, role="java", match_score=7..9!
3. Опыт работы:
   - Для позиций Junior / Стажёр / Trainee / Начинающий разработчик: стандартная HR-формулировка 'требуемый опыт: 1-3 года' или 'опыт от 1 года' в тексте вакансии НЕ является поводом для отказа, если по задачам и стеку это начальная позиция начинающего специалиста.
   - Отсекать (suitable=false, match_score=1..2) ТОЛЬКО если это реальный Middle+/Senior/Lead с жестким требованием от 2-3+ лет боевого продакшн-опыта, проектирования HighLoad систем или руководства командой.
4. Локация, удаленка и зарплата:
   - УДАЛЕННЫЙ ФОРМАТ (Remote / Удаленка / Дистанционно / Из любого города РФ): ПОДХОДИТ ВСЕГДА (suitable=true) независимо от того, указана зарплата или нет.
   - СТАЖИРОВКИ (Intern / Стажер / Практикант / Trainee): ПОДХОДЯТ ВСЕГДА (suitable=true), даже если зарплата не указана или не оплачивается (дает ценный опыт в резюме).
   - ЕСЛИ ЗАРПЛАТА НЕ УКАЗАНА (по договоренности, не указана в тексте): НЕ ОТСЕКАЙ! Одобряй (suitable=true), если стек и уровень подходят, а в recommendation посоветуй уточнить вилку на первом скрининге.
   - Офис/гибрид Москва/СПб: подходит при зарплате от 80k+, либо если это официальная стажировка известной компании (Сбер, Т-Банк, Яндекс, ВТБ и др.), либо возможна удаленка. Отсекать только если это жесткий офис в Москве/СПб с явно указанной низкой зарплатой менее 70 000 руб.
5. Вакансии LinkedIn / International:
   - Подходит (suitable=true), если это Remote для РФ/СНГ/Worldwide, либо международная/русскоязычная компания, готовая оформлять удаленщиков (ТК РФ, ИП, самозанятость, Deel, Solar Staff, крипта).
   - Отсекать (suitable=false, match_score=1) только при жестком требовании работы в офисе за рубежом, требовании гражданства США/ЕС или прямого запрета найма резидентов РФ/РБ.
6. Образование: у Никиты неоконченное высшее (технический ВУЗ), колледж (СПО) и Школа 21 от Сбера. Если требуется высшее образование — одобряй, education_warning=null.
7. Черный список компаний: Aston (Астон), Deepra (Дипра) — строго suitable=false, match_score=1.

Верни ответ СТРОГО в формате JSON:
{{
  "suitable": true/false,
  "role": "java" | "python_dev" | "ai_dev" | "fullstack_js" | "vpn_dev" | "backend",
  "match_score": целое число от 1 до 10,
  "education_warning": null,
  "verdict": "краткое резюме (1-2 предложения, почему подходит или почему нет)",
  "recommendation": "практический совет Никите, что подчеркнуть в отклике"
}}
"""

    models_to_try = [model]
    for fallback in ["llama-3.1-8b-instant", "qwen-2.5-32b"]:
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


def generate_cover_letter(api_key: str, model: str, vacancy: dict) -> str:
    """Генерирует индивидуальное сопроводительное письмо с релевантными ссылками на пет-проекты."""
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
    resume_url = (vacancy.get("resume_url") or cfg_data.get("resume_quick_tunnel_url") or os.environ.get("RESUME_QUICK_TUNNEL_URL", "")).strip()
    if resume_url and not resume_url.endswith("/"):
        resume_url += "/"

    full_text = f"{clean_title} {clean_desc}".lower()
    is_vpn_dev = vacancy.get("role") == "vpn_dev" or any(k in full_text for k in ["remnawave", "xray", "vless", "sing-box"])
    is_java_ai = vacancy.get("role") in ["ai_dev", "java_ai"] or any(k in full_text for k in ["spring ai", "spring-ai", "langchain4j", "langchain", "ai agent", "ai-агент", "llm", "rag", "инженер ии", "разработчик ии", "ai engineer"])
    is_python_backend = vacancy.get("role") == "python_dev" or (any(k in full_text for k in ["python", "fastapi", "django", "flask"]) and any(d in full_text for d in ["backend", "бэкенд", "разработчик", "developer", "стажер", "junior"]) and not ("java" in clean_title.lower() and "javascript" not in clean_title.lower()))
    is_fullstack_js = vacancy.get("role") == "fullstack_js" or (any(k in full_text for k in ["fullstack", "фулстек", "full stack", "react", "node", "javascript", "frontend", "front-end", "фронтенд"]) and not ("java" in clean_title.lower() and "javascript" not in clean_title.lower()))

    if is_vpn_dev:
        sys_msg = (
            "Ты — квалифицированный инженер и разработчик VPN/Proxy инфраструктуры Никита Фролов. "
            "Ты составляешь профессиональные, грамотные и зрелые сопроводительные письма. "
            "Твой стиль: деловой, сдержанный, глубоко технический. Без панибратства, без школьных шаблонов."
        )
        user_prompt = f"""Напиши профессиональное, зрелое и убедительное сопроводительное письмо на позицию разработчика VPN/прокси инфраструктуры.

Кандидат: Никита Фролов
- Специализация: Разработка и эксплуатация VPN/Proxy инфраструктуры.
- Технологический стек: Xray-core, VLESS (Reality, Vision), Sing-box, панели управления Remnawave и 3X-UI, протоколы обхода блокировок.
- Практический опыт: Настройка и администрирование парка Linux VPS нод, проектирование маршрутизации трафика (routing rules), интеграция через REST API панелей управления, мониторинг доступности узлов.

Вакансия:
- Позиция: {clean_title}
- Компания: {clean_company or 'Работодатель'}
- Требования и описание:
{clean_desc}

СТРОГИЕ ПРАВИЛА СОСТАВЛЕНИЯ:
1. ВЗРОСЛЫЙ ИНЖЕНЕРНЫЙ ТОН:
   - Начни строго с уважительного делового приветствия: «Здравствуйте!» или «Добрый день! Меня заинтересовала вакансия {clean_title} в {clean_company}.».
   - КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО детское или панибратское начало («Привет!», «Привет! Меня зовут Никита...»).
   - Текст должен звучать как письмо компетентного инженера, понимающего специфику современных сетевых протоколов.
2. ПРИВЯЗКА К СТЕКУ ВАКАНСИИ:
   - Подчеркни именно те технологии из описания, с которыми есть реальный опыт (Xray, Sing-box, VLESS, Remnawave, автоматизация на Linux).
3. ПРАКТИЧЕСКИЙ ОПЫТ:
   - Сделай акцент на реальной практике: управление распределенными нодами, настройка роутинга трафика, работа с API панелей и Linux VPS.
4. ОБЪЕМ И СТРУКТУРА:
   - 2-3 компактных, логичных абзаца (примерно 80-120 слов). Без лишней «воды» и пустых клише.
5. ФИНАЛ:
   - Заверши профессиональным предложением обсудить технические задачи команды на собеседовании или созвоне.
6. СТРОГИЙ ЗАПРЕТ:
   - НИ В КОЕМ СЛУЧАЕ НЕ ПРЕДЛАГАЙ тестовые задания.
   - Выдавай ТОЛЬКО готовый текст письма, без кавычек, без пометок типа «Тема:» или комментариев.
"""
    elif is_java_ai:
        sys_msg = (
            "Ты — AI / LLM Integration & Backend разработчик Никита Фролов. "
            "Ты составляешь профессиональные, грамотные и лаконичные сопроводительные письма. "
            "Твой стиль: инженерный, современный, структурированный, строго со ссылками на проекты и резюме."
        )
        user_prompt = f"""Напиши профессиональное сопроводительное письмо на позицию разработчика ИИ / интегратора AI & LLM.

Кандидат: Никита Фролов
- Ссылки на проекты и резюме:
  • Резюме: {resume_url}
  • Проект по ИИ (ML/NLP): https://github.com/nikitos7626/mvideo-nlp
  • Живой веб-проект (деплой): https://park.shadowkek.space
- Стек и фокус: AI / LLM интеграции (LLM API, Function Calling, RAG, AI-агенты, LangChain, Spring AI), бэкенд (Python/Java, REST API, PostgreSQL, Docker). Чистая архитектура, самостоятельный деплой на Linux VPS под Nginx с SSL.
- База и готовность: Базу по бэкенду, ООП, SQL и Git понимаю. Готов выполнить тестовое задание.

Вакансия:
- Позиция: {clean_title}
- Компания: {clean_company or 'Работодатель'}
- Описание:
{clean_desc}

СТРОГАЯ СТРУКТУРА ПИСЬМА:
1. Приветствие: «Здравствуйте! Откликаюсь на вакансию {clean_title}{' в компании ' + clean_company if clean_company else ''}.»
2. Обязательный блок ссылок буллетами:
   Прикрепляю ссылки на резюме и практические проекты:
   • Резюме: {resume_url}
   • Проект по ИИ (ML/NLP): https://github.com/nikitos7626/mvideo-nlp
   • Сайт (деплой): https://park.shadowkek.space
3. Стек: AI / LLM интеграции (LLM API, Function Calling, RAG, AI-агенты, LangChain, Spring AI), бэкенд (Python/Java, REST API, PostgreSQL, Docker). Чистая архитектура, самостоятельный деплой на Linux VPS под Nginx с SSL.
4. Финал: Базу по бэкенду, ООП, SQL и Git понимаю. Готов выполнить тестовое задание.
"""
    elif is_python_backend:
        sys_msg = (
            "Ты — Junior Python Backend разработчик Никита Фролов. "
            "Ты составляешь профессиональные, грамотные и лаконичные сопроводительные письма. "
            "Твой стиль: инженерный, современный, структурированный, строго со ссылками на проекты и резюме."
        )
        user_prompt = f"""Напиши профессиональное сопроводительное письмо на позицию Python Backend разработчика.

Кандидат: Никита Фролов
- Ссылки на проекты и резюме:
  • Резюме: {resume_url}
  • Проект на Python (NLP/ML/AI): https://github.com/nikitos7626/mvideo-nlp
  • Telegram-бот на Python: https://github.com/nikitos7626/ExCalendar
  • Сайт (деплой): https://park.shadowkek.space
- Стек и фокус: Python 3, FastAPI, PostgreSQL, Docker, REST API, чистая архитектура, самостоятельный деплой на Linux VPS под Nginx с SSL.
- База и готовность: Базу по Python, ООП, SQL и Git понимаю. Готов выполнить тестовое задание.

Вакансия:
- Позиция: {clean_title}
- Компания: {clean_company or 'Работодатель'}
- Описание:
{clean_desc}

СТРОГАЯ СТРУКТУРА ПИСЬМА:
1. Приветствие: «Здравствуйте! Откликаюсь на вакансию {clean_title}{' в компании ' + clean_company if clean_company else ''}.»
2. Обязательный блок ссылок буллетами:
   Прикрепляю ссылки на резюме и практические проекты:
   • Резюме: {resume_url}
   • Проект на Python (NLP/ML/AI): https://github.com/nikitos7626/mvideo-nlp
   • Telegram-бот на Python: https://github.com/nikitos7626/ExCalendar
   • Сайт (деплой): https://park.shadowkek.space
3. Стек: Python 3, FastAPI, PostgreSQL, Docker, REST API, чистая архитектура, самостоятельный деплой на Linux VPS под Nginx с SSL.
4. Финал: Базу по Python, ООП, SQL и Git понимаю. Готов выполнить тестовое задание.
"""
    elif is_fullstack_js:
        sys_msg = (
            "Ты — Fullstack JS / Frontend разработчик Никита Фролов. "
            "Ты составляешь профессиональные, четкие и лаконичные сопроводительные письма. "
            "Стиль: живой, структурированный, уверенный, строго со структурированными буллетами на ссылки и проекты."
        )
        user_prompt = f"""Напиши профессиональное сопроводительное письмо на позицию стажёра / Junior Fullstack JS разработчика.

Кандидат: Никита Фролов
- Ссылки на резюме и пет-проекты:
  • Резюме: {resume_url}
  • Сайт (деплой): https://park.shadowkek.space
  • Репозиторий фронта: https://github.com/nikitos7626/park-client
  • Репозиторий бэка: https://github.com/nikitos7626/Park-server
- Стек проекта: React 18 (MobX), Node.js (Express, Sequelize). Реализована JWT-авторизация, роли, интеграция с API. Всё сам развернул и настроил на Linux VPS под Nginx с SSL.
- База и готовность: Базу по JS (ES6+, промисы, async/await, DOM), Git и React понимаю. Готов выполнить тестовое задание.

Вакансия:
- Позиция: {clean_title}
- Компания: {clean_company or 'Работодатель'}
- Описание:
{clean_desc}

СТРОГАЯ СТРУКТУРА ПИСЬМА:
1. Приветствие: «Привет! Откликаюсь на вакансию {clean_title}.»
2. Обязательный блок ссылок буллетами:
   Прикрепляю ссылки на резюме и живой пет-проект, как указано в требованиях вакансии:
   • Резюме: {resume_url}
   • Сайт (деплой): https://park.shadowkek.space
   • Репозиторий фронта: https://github.com/nikitos7626/park-client
   • Репозиторий бэка: https://github.com/nikitos7626/Park-server
3. Стек проекта: React 18 (MobX), Node.js (Express, Sequelize). Реализована JWT-авторизация, роли, интеграция с API. Всё сам развернул и настроил на Linux VPS под Nginx с SSL.
4. Финал: Базу по JS (ES6+, промисы, async/await, DOM), Git и React понимаю. Готов выполнить тестовое задание.
"""
    else:
        sys_msg = (
            "Ты — квалифицированный Junior Java Backend разработчик Никита Фролов. "
            "Ты составляешь профессиональные, грамотные и лаконичные сопроводительные письма. "
            "Твой стиль: деловой, инженерный, уверенный, со структурированными буллетами на ссылки и проекты."
        )
        user_prompt = f"""Напиши профессиональное, зрелое и убедительное сопроводительное письмо на позицию Java-разработчика.

Кандидат: Никита Фролов
- Ссылки на резюме и пет-проекты:
  • Резюме: {resume_url}
  • Репозиторий бэкенда: https://github.com/nikitos7626/BackendSpringBlog
  • Сайт (деплой): https://park.shadowkek.space
- Стек проекта: Java 21, Spring Boot 3 (Spring Data JPA, Security), PostgreSQL, Docker. Реализовал архитектуру Clean Architecture и REST API. Все сервисы развернул и настроил сам на Linux VPS под Nginx с SSL.
- База и готовность: Базу по Java Core, ООП, SQL и Git понимаю. Готов выполнить тестовое задание.

Вакансия:
- Позиция: {clean_title}
- Компания: {clean_company or 'Работодатель'}
- Описание:
{clean_desc}

СТРОГАЯ СТРУКТУРА ПИСЬМА:
1. Приветствие: «Здравствуйте! Откликаюсь на вакансию {clean_title}{' в компании ' + clean_company if clean_company else ''}.»
2. Обязательный блок ссылок буллетами:
   Прикрепляю ссылки на резюме и практические проекты:
   • Резюме: {resume_url}
   • Репозиторий бэкенда: https://github.com/nikitos7626/BackendSpringBlog
   • Сайт (деплой): https://park.shadowkek.space
3. Стек: Java 21, Spring Boot 3 (Spring Data JPA, Security), PostgreSQL, Docker. Реализовал архитектуру Clean Architecture и REST API. Все сервисы развернул и настроил на Linux VPS под Nginx с SSL.
4. Финал: Базу по Java Core, ООП, SQL и Git понимаю. Готов выполнить тестовое задание.
"""

    models_to_try = [model]
    for fallback in ["llama-3.1-8b-instant", "qwen-2.5-32b"]:
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
                    if resume_url and resume_url not in letter:
                        letter = letter + f"\n\n• Резюме: {resume_url}"
                    if is_java_ai:
                        if "mvideo-nlp" not in letter:
                            letter = letter + "\n• Проект по ИИ (ML/NLP): https://github.com/nikitos7626/mvideo-nlp"
                    elif is_python_backend:
                        if "mvideo-nlp" not in letter and "ExCalendar" not in letter:
                            letter = letter + "\n• Проект на Python: https://github.com/nikitos7626/mvideo-nlp"
                    elif is_fullstack_js:
                        if "park-client" not in letter:
                            letter = letter + "\n• Репозиторий фронта: https://github.com/nikitos7626/park-client"
                        if "Park-server" not in letter:
                            letter = letter + "\n• Репозиторий бэка: https://github.com/nikitos7626/Park-server"
                    elif not is_vpn_dev:
                        if "BackendSpringBlog" not in letter:
                            letter = letter + "\n• Репозиторий бэкенда: https://github.com/nikitos7626/BackendSpringBlog"
                    if not is_vpn_dev and "park.shadowkek.space" not in letter:
                        letter = letter + "\n• Живой проект (деплой): https://park.shadowkek.space"
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

    # Профессиональный фоллбек на случай недоступности нейросети
    if is_java_ai:
        comp_str = f" в {clean_company}" if clean_company else ""
        return (
            f"Здравствуйте! Откликаюсь на вакансию {clean_title}{comp_str}.\n\n"
            f"Прикрепляю ссылки на резюме и практические проекты:\n"
            f"• Резюме: {resume_url}\n"
            f"• Проект по ИИ (ML/NLP): https://github.com/nikitos7626/mvideo-nlp\n"
            f"• Сайт (деплой): https://park.shadowkek.space\n\n"
            f"Стек: AI / LLM интеграции (LLM API, Function Calling, RAG, AI-агенты, LangChain, Spring AI), бэкенд (Python/Java, REST API, PostgreSQL, Docker). Чистая архитектура, самостоятельный деплой на Linux VPS под Nginx с SSL.\n\n"
            f"Базу по бэкенду, ООП, SQL и Git понимаю. Готов выполнить тестовое задание."
        )

    if is_python_backend:
        comp_str = f" в компании {clean_company}" if clean_company else ""
        return (
            f"Здравствуйте! Откликаюсь на вакансию {clean_title}{comp_str}.\n\n"
            f"Прикрепляю ссылки на резюме и практические проекты:\n"
            f"• Резюме: {resume_url}\n"
            f"• Проект на Python (NLP/ML/AI): https://github.com/nikitos7626/mvideo-nlp\n"
            f"• Telegram-бот на Python: https://github.com/nikitos7626/ExCalendar\n"
            f"• Сайт (деплой): https://park.shadowkek.space\n\n"
            f"Стек: Python 3, FastAPI, PostgreSQL, Docker, REST API, чистая архитектура, самостоятельный деплой на Linux VPS под Nginx с SSL.\n\n"
            f"Базу по Python, ООП, SQL и Git понимаю. Готов выполнить тестовое задание."
        )

    if is_fullstack_js:
        return (
            f"Привет! Откликаюсь на вакансию {clean_title}.\n\n"
            f"Прикрепляю ссылки на резюме и живой пет-проект, как указано в требованиях вакансии:\n"
            f"• Резюме: {resume_url}\n"
            f"• Сайт (деплой): https://park.shadowkek.space\n"
            f"• Репозиторий фронта: https://github.com/nikitos7626/park-client\n"
            f"• Репозиторий бэка: https://github.com/nikitos7626/Park-server\n\n"
            f"Стек проекта: React 18 (MobX), Node.js (Express, Sequelize). Реализована JWT-авторизация, роли, интеграция с API. Всё сам развернул и настроил на Linux VPS под Nginx с SSL.\n\n"
            f"Базу по JS (ES6+, промисы, async/await, DOM), Git и React понимаю. Готов выполнить тестовое задание."
        )

    if is_vpn_dev:
        comp_str = f" в компании {clean_company}" if clean_company else ""
        return (
            f"Здравствуйте! Откликаюсь на позицию {clean_title}{comp_str}.\n\n"
            f"Прикрепляю ссылки на резюме и проекты:\n"
            f"• Резюме: {resume_url}\n"
            f"• Развёрнутые сервисы (деплой): https://park.shadowkek.space\n\n"
            f"Стек: Xray-core, VLESS (Reality, Vision), Sing-box, панели Remnawave и 3X-UI, маршрутизация трафика. Развёртывание и администрирование узлов на Linux VPS под Nginx с SSL.\n\n"
            f"Сетевой стек (TCP/IP, routing, DNS, TLS), Linux и Git понимаю. Готов выполнить тестовое задание."
        )

    comp_str = f" в компании {clean_company}" if clean_company else ""
    return (
        f"Здравствуйте! Откликаюсь на вакансию {clean_title}{comp_str}.\n\n"
        f"Прикрепляю ссылки на резюме и практические проекты:\n"
        f"• Резюме: {resume_url}\n"
        f"• Репозиторий бэкенда: https://github.com/nikitos7626/BackendSpringBlog\n"
        f"• Сайт (деплой): https://park.shadowkek.space\n\n"
        f"Стек проекта: Java 21, Spring Boot 3 (Spring Data JPA, Security), PostgreSQL, Docker. Реализовал архитектуру Clean Architecture и REST API. Все сервисы развернул и настроил сам на Linux VPS под Nginx с SSL.\n\n"
        f"Базу по Java Core, ООП, SQL и Git понимаю. Готов выполнить тестовое задание."
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
