"""
Config Model for Career Hub.
Handles default settings, environment overrides, candidate profile, and config caching.
"""

import os
import json
import logging
import threading

logger = logging.getLogger("career_hub.config")

_config_cache = None
_config_cache_mtime = 0.0
_config_cache_lock = threading.Lock()

def safe_int(val, default: int = 0) -> int:
    """Безопасно преобразует значение в int, возвращая default при ошибке или пустом значении."""
    try:
        if val is None or val == "":
            return default
        return int(str(val).strip())
    except (ValueError, TypeError):
        return default


DEFAULT_CONFIG = {
    "bot_token": os.environ.get("BOT_TOKEN", ""),
    "bot_username": os.environ.get("BOT_USERNAME", ""),
    "chat_id": safe_int(os.environ.get("CHAT_ID", "0")),
    "allowed_telegram_id": safe_int(os.environ.get("ALLOWED_TELEGRAM_ID", "0")),
    "groq_api_key": os.environ.get("GROQ_API_KEY", ""),
    "groq_model": os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile"),
    "min_match_score": 6,
    "notify_min_score": 7,
    "auto_apply_enabled": True,
    "auto_apply_min_score": 7,
    "hh_access_token": "",
    "hh_resume_id": "",
    "hh_web_cookie": "",
    "habr_web_cookie": "",
    "hh_resume_hash": os.environ.get("HH_RESUME_HASH", ""),
    "resume_quick_tunnel_url": os.environ.get("RESUME_QUICK_TUNNEL_URL", ""),
    "miniapp_url": os.environ.get("MINIAPP_URL", "https://jobs.example.com/"),
    "portfolio_url": os.environ.get("PORTFOLIO_URL", "https://park.example.com"),
    "contact_email": os.environ.get("CONTACT_EMAIL", "applicant@example.com"),
    "daily_digest_enabled": True,
    "last_digest_time": 0,
    "check_interval_seconds": 600,
    "max_ai_analyses_per_cycle": 25,
    "ai_delay_seconds": 2.5,
    "enable_hh": True,
    "enable_habr": True,
    "enable_getmatch": False,
    "enable_remotive": True,
    "enable_superjob": True,
    "enable_linkedin": True,
    "enable_telegram": True,
    "enable_rabota": True,
    "enable_trudvsem": False,
    "blacklisted_companies": [
        "Aston", "Астон", "Deepra", "Лига Цифровой Экономики",
        "Effective Mobile", "Эффектив Мобайл", "IT-Rev", "Игры Разума",
        "Т1", "Крок", "Ланит", "Иннотех", "Andersen", "Softline"
    ],
    "habr_queries": [
        {"query": "junior java", "divisions": ["development", "backend"]},
        {"query": "java", "divisions": ["development", "backend"]},
        {"query": "стажер java", "divisions": ["development", "backend"]},
        {"query": "junior fullstack", "divisions": ["development", "backend", "frontend"]},
        {"query": "fullstack react", "divisions": ["development", "backend", "frontend"]},
        {"query": "junior frontend react", "divisions": ["development", "frontend"]},
        {"query": "стажер react", "divisions": ["development", "frontend"]},
        {"query": "стажер fullstack", "divisions": ["development", "backend", "frontend"]},
        {"query": "junior python backend", "divisions": ["development", "backend"]},
        {"query": "fastapi", "divisions": ["development", "backend"]},
        {"query": "Spring AI", "divisions": ["development", "backend"]},
        {"query": "LangChain4j", "divisions": ["development", "backend"]},
        {"query": "Java AI", "divisions": ["development", "backend"]},
        {"query": "AI engineer", "divisions": ["development", "backend"]},
        {"query": "LLM developer", "divisions": ["development", "backend"]},
        {"query": "инженер ИИ", "divisions": ["development", "backend"]},
        {"query": "разработчик ИИ", "divisions": ["development", "backend"]},
        {"query": "AI разработчик", "divisions": ["development", "backend"]},
        {"query": "junior AI", "divisions": ["development", "backend"]},
        {"query": "стажер AI", "divisions": ["development", "backend"]},
        {"query": "prompt engineer", "divisions": ["development", "backend"]},
        {"query": "промпт инженер", "divisions": ["development", "backend"]},
        {"query": "стажер prompt", "divisions": ["development", "backend"]},
        {"query": "AI agent", "divisions": ["development", "backend"]},
        {"query": "xray developer", "divisions": ["development", "backend"]},
        {"query": "remnawave", "divisions": ["development", "backend"]},
        {"query": "разработчик vpn", "divisions": ["development", "backend"]}
    ],
    "hh_queries": [
        {"query": "junior java", "experience": "noExperience"},
        {"query": "junior java", "experience": "between1And3"},
        {"query": "стажер java", "experience": "noExperience"},
        {"query": "junior fullstack react", "experience": "noExperience"},
        {"query": "junior fullstack", "experience": "noExperience"},
        {"query": "fullstack react", "experience": "noExperience"},
        {"query": "junior frontend react", "experience": "noExperience"},
        {"query": "стажер frontend react", "experience": "noExperience"},
        {"query": "стажер fullstack", "experience": "noExperience"},
        {"query": "junior python backend", "experience": "noExperience"},
        {"query": "fastapi", "experience": "noExperience"},
        {"query": "Spring AI"},
        {"query": "LangChain4j"},
        {"query": "Java AI"},
        {"query": "AI engineer"},
        {"query": "LLM developer"},
        {"query": "инженер ИИ"},
        {"query": "разработчик ИИ"},
        {"query": "AI разработчик"},
        {"query": "junior AI"},
        {"query": "стажер AI"},
        {"query": "prompt engineer"},
        {"query": "промпт инженер"},
        {"query": "промпт-инженер"},
        {"query": "стажер prompt"},
        {"query": "стажер промпт"},
        {"query": "AI агент"},
        {"query": "AI agent"},
        {"query": "xray developer"},
        {"query": "remnawave"},
        {"query": "разработчик vpn"}
    ]
}

CANDIDATE_PROFILE = """
Профиль кандидата (Никита Фролов, 21 год):
Образование: Неоконченное высшее (технический ВУЗ) + СПО (колледж, Информационные системы и программирование) + Школа 21 от Сбера.
База и алгоритмы: 2.5 года вел практические занятия и код-ревью работ (алгоритмическая культура чистого кода, выявление багов).

ЦЕЛЕВОЙ ПРОФИЛЬ (СТРОГО РАЗРАБОТКА ПО):
1. Junior Java Backend Developer:
   - Стек: Java 21, Spring Boot 3 (Spring Web, Spring Security, Spring Data JPA/JDBC), PostgreSQL, Docker, Clean Architecture, REST API, многопоточность.
2. AI / LLM Integration & AI Engineer (РАБОТА С ИИ И НЕЙРОСЕТЯМИ):
   - Разработка и интеграция AI-решений, агентов и LLM пайплайнов (Spring AI, LangChain, OpenAI/Groq API, Function Calling, RAG, pgvector, prompt engineering).
3. Junior Python Backend Developer:
   - Стек: Python 3, FastAPI, Flask, REST API, PostgreSQL, Docker, ООП, скрипты автоматизации и интеграции.
4. Junior / Trainee Fullstack JS / Frontend:
   - React 18, Node.js, Express, Sequelize, REST API, Nginx, Linux VPS. Деплой веб-проектов и SPA.
5. Разработчик VPN / Прокси сервисов (Xray, Remnawave, VLESS, Sing-box, сетевые протоколы).
6. Junior / Intern Backend Developer (стажер или младший бэкенд-разработчик).

КАТЕГОРИЧЕСКИ НЕ ИНТЕРЕСУЕТ (СТРОЖАЙШИЙ ОТСЕВ):
- ПРЕПОДАВАНИЕ / УЧИТЕЛЬ / МЕНТОР / НАСТАВНИК / РЕПЕТИТОР: любые вакансии в сфере обучения строго запрещены! Никиту интересует ТОЛЬКО инженерная разработка ПО, НЕ преподавание!
- ТЕСТИРОВАНИЕ И QA / AQA: любые вакансии тестировщиков, автотестеров и QA строго запрещены!
- ТЕХНИЧЕСКАЯ ПОДДЕРЖКА / САППОРТ / L1-L3 / СОПРОВОЖДЕНИЕ: запрещены!
- РАЗМЕТКА ДАННЫХ И AI-ТРЕНЕРСТВО: запрещены!
- ВАКАНСИИ С ТРЕБОВАНИЕМ ОПЫТА: Исключать вакансии уровня Middle+ / Senior (требование от 2-3+ лет реального коммерческого опыта, руководство, архитектура). Шаблоны вида 'опыт 1-3 года' или 'от 1 года' на позициях Junior / Стажер допустимы, если стек подходит.
- ЛЮБЫЕ обучающие платные курсы, онлайн-школы, академии, инкубаторы.
- Ручной эникей / выездной мастер / ремонт ПК / картриджи / телефонный спам-коллцентр.
- Позиции Middle+, Senior, Lead, Тимлид, Архитектор, Руководитель.
- 1С, PHP, Ruby, Мобильная разработка.
"""


def _get_config_path():
    import sys
    bot_mod = sys.modules.get("bot")
    if bot_mod and hasattr(bot_mod, "CONFIG_FILE"):
        return bot_mod.CONFIG_FILE
    from career_hub.models.storage import CONFIG_FILE
    return CONFIG_FILE


def load_config():
    global _config_cache, _config_cache_mtime
    import sys
    bot_mod = sys.modules.get("bot")
    if bot_mod and hasattr(bot_mod, "_config_cache") and bot_mod._config_cache is None:
        _config_cache = None
        _config_cache_mtime = 0.0

    config_file = _get_config_path()
    with _config_cache_lock:
        try:
            current_mtime = os.path.getmtime(config_file) if os.path.exists(config_file) else 0.0
        except OSError:
            current_mtime = 0.0

        if _config_cache is not None and current_mtime == _config_cache_mtime:
            return _config_cache

        if not os.path.exists(config_file):
            os.makedirs(os.path.dirname(config_file), exist_ok=True)
            with open(config_file, "w", encoding="utf-8") as f:
                json.dump(DEFAULT_CONFIG, f, indent=4, ensure_ascii=False)
            _config_cache = dict(DEFAULT_CONFIG)
            _config_cache_mtime = os.path.getmtime(config_file)
            return _config_cache

        try:
            with open(config_file, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            updated = False
            for k, v in DEFAULT_CONFIG.items():
                if k not in cfg:
                    cfg[k] = v
                    updated = True

            if "habr_queries" not in cfg:
                cfg["habr_queries"] = DEFAULT_CONFIG["habr_queries"]
                updated = True
            if "hh_queries" not in cfg:
                cfg["hh_queries"] = DEFAULT_CONFIG["hh_queries"]
                updated = True

            if updated:
                with open(config_file, "w", encoding="utf-8") as f:
                    json.dump(cfg, f, indent=4, ensure_ascii=False)
                current_mtime = os.path.getmtime(config_file)

            _config_cache = cfg
            _config_cache_mtime = current_mtime
            return cfg
        except Exception as e:
            logger.error(f"Ошибка чтения {config_file}: {e}")
            if _config_cache is not None:
                return _config_cache
            return dict(DEFAULT_CONFIG)


def save_config(config):
    global _config_cache, _config_cache_mtime
    config_file = _get_config_path()
    with _config_cache_lock:
        os.makedirs(os.path.dirname(config_file), exist_ok=True)
        # Atomic write
        temp_file = f"{config_file}.tmp.{os.getpid()}"
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=4, ensure_ascii=False)
        os.replace(temp_file, config_file)
        _config_cache = None
        _config_cache_mtime = 0.0
