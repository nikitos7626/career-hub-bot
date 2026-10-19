"""
Vacancy Model, Normalization, Tech Stack Extraction and Formatting for Career Hub.
"""

import re
import html
import logging

logger = logging.getLogger("career_hub.vacancy")


def clean_html(raw_html: str) -> str:
    """Очищает HTML-теги, разэкранирует сущности и схлопывает лишние пробелы."""
    if not raw_html:
        return ""
    text = re.sub(r'<[^>]+>', ' ', raw_html)
    text = html.unescape(text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


KNOWN_TECH = [
    ("Java 21", r'\bjava\s*21\b'),
    ("Java 17", r'\bjava\s*17\b'),
    ("Java", r'\bjava\b'),
    ("Kotlin", r'\bkotlin\b'),
    ("Spring Boot", r'\b(spring\s*boot|springboot|javaspringframework)\b'),
    ("Spring", r'\bspring(\s*framework)?\b'),
    ("PostgreSQL", r'\b(postgresql|postgres)\b'),
    ("MySQL", r'\bmysql\b'),
    ("SQL", r'\bsql\b'),
    ("Hibernate", r'\bhibernate\b'),
    ("JPA", r'\bjpa\b'),
    ("Docker", r'\bdocker\b'),
    ("Kubernetes", r'\b(kubernetes|k8s)\b'),
    ("Kafka", r'\b(apache\s*kafka|apachekafka|kafka)\b'),
    ("RabbitMQ", r'\brabbitmq\b'),
    ("Redis", r'\bredis\b'),
    ("REST API", r'\b(rest\s*api|restful|rest)\b'),
    ("Git", r'\bgit\b'),
    ("Maven", r'\bmaven\b'),
    ("Gradle", r'\bgradle\b'),
    ("Linux", r'\blinux\b'),
    ("CI/CD", r'\bci[/-]?cd\b'),
    ("Microservices", r'\b(microservices|микросервис[а-я]*)\b'),
    ("Clean Architecture", r'\b(clean\s*architecture|чистая\s*архитектура)\b'),
    ("JUnit / Mockito", r'\b(junit|mockito)\b'),
    ("Xray / VLESS", r'\b(xray(-core)?|vless|reality)\b'),
    ("Spring AI", r'\bspring\s*ai\b'),
    ("LangChain4j", r'\blangchain4j\b'),
    ("LLM / AI Agents", r'\b(llm|ai\s*agent[s]?|rag|genai|function\s*calling|pgvector)\b'),
    ("Sing-box", r'\bsing-box\b'),
    ("Remnawave", r'\bremnawave\b'),
    ("3X-UI", r'\b3x-ui\b'),
]

TAG_REMAP = {
    "javaspringframework": "Spring Boot",
    "springboot": "Spring Boot",
    "apachekafka": "Kafka",
    "postgresql": "PostgreSQL",
    "docker": "Docker",
    "kubernetes": "Kubernetes",
    "rabbitmq": "RabbitMQ",
    "redis": "Redis",
    "kotlin": "Kotlin",
    "java": "Java",
    "git": "Git",
    "hibernate": "Hibernate",
    "linux": "Linux",
    "gradle": "Gradle",
    "maven": "Maven",
    "remnawave": "Remnawave",
    "xray": "Xray / VLESS",
    "singbox": "Sing-box",
    "sing-box": "Sing-box",
    "restapi": "REST API",
    "rest": "REST API",
}


def extract_tech_stack(text: str, title: str = "") -> list:
    """Извлекает стек технологий по ключевым словам и хештегам."""
    combined = f"{title} {text}".lower()
    stack = []
    seen = set()

    habr_tags = re.findall(r'#([A-Za-z0-9_а-яА-Я]+)', text)
    for tag in habr_tags:
        t_low = tag.lower()
        if t_low in ["junior", "middle", "senior", "lead", "стажер", "intern"]:
            continue
        cleaned_name = TAG_REMAP.get(t_low, tag)
        if cleaned_name.lower() not in seen:
            seen.add(cleaned_name.lower())
            stack.append(cleaned_name)

    for name, pattern in KNOWN_TECH:
        if name.lower() in seen:
            continue
        if re.search(pattern, combined, re.IGNORECASE):
            if name == "Spring" and "Spring Boot" in stack:
                continue
            if name == "Java" and any(j in stack for j in ["Java 21", "Java 17"]):
                continue
            seen.add(name.lower())
            stack.append(name)

    return stack


def format_structured_description(desc: str, source: str = "") -> str:
    """Форматирует сплошной текст вакансии в читаемый структурированный HTML."""
    if not desc:
        return "Описание не указано"

    text = desc.strip()

    if "Требуемые навыки:" in text:
        parts = text.split("Требуемые навыки:")
        info_part = parts[0].strip()
        skills_part = parts[1].strip() if len(parts) > 1 else ""

        info_part = re.sub(r'^Компания\s+[«"].*?[»"]\s+ищет\s+хорошего\s+специалиста\s+на\s+вакансию\s+[«"].*?[»"]\.?\s*', '', info_part)

        formatted_lines = []
        if info_part:
            conditions = [c.strip() for c in info_part.split('.') if c.strip()]
            if conditions:
                formatted_lines.append("<div class='mb-2 text-slate-300 font-medium'>📍 " + " • ".join(conditions) + "</div>")

        tags = [t.strip().lstrip('#') for t in skills_part.split(',') if t.strip()]
        tags = [t for t in tags if t.lower() not in ['junior', 'middle', 'senior', 'стажер', 'intern']]
        cleaned_tags = []
        seen = set()
        for t in tags:
            clean = TAG_REMAP.get(t.lower(), t)
            if clean.lower() not in seen:
                seen.add(clean.lower())
                cleaned_tags.append(clean)

        if cleaned_tags:
            formatted_lines.append("<div class='text-xs text-indigo-300 font-semibold mb-1'>🛠 Ключевые требования:</div><div class='text-slate-300 leading-relaxed'>" + " • ".join(cleaned_tags) + "</div>")

        if formatted_lines:
            return "".join(formatted_lines)

    text = re.sub(r'(Требования|Обязанности|Условия|Мы предлагаем|Задачи|Стек|Стек технологий|Стек проекта):', r'\n\n###\1###\n', text)

    paragraphs = text.split('\n\n')
    out_paras = []
    for p in paragraphs:
        p = p.strip()
        if not p:
            continue
        m = re.match(r'^###([^#]+)###\s*(.*)$', p, re.DOTALL)
        if m:
            sec_name = m.group(1).strip()
            sec_body = m.group(2).strip()
            out_paras.append(f"<div class='mt-2.5 mb-1 text-xs font-bold text-indigo-300 uppercase tracking-wider'>📋 {sec_name}</div>")
            if sec_body:
                items = [it.strip(' .;•-') for it in sec_body.split(';') if it.strip(' .;•-')]
                if len(items) > 1:
                    bullets = "".join(f"<li class='ml-4 text-xs text-slate-300 leading-relaxed'>• {it}</li>" for it in items)
                    out_paras.append(f"<ul class='space-y-1 mb-2'>{bullets}</ul>")
                else:
                    out_paras.append(f"<div class='text-xs text-slate-300 leading-relaxed mb-2'>{sec_body}</div>")
        else:
            items = [it.strip(' .;•-') for it in p.split(';') if it.strip(' .;•-')]
            if len(items) >= 3:
                bullets = "".join(f"<li class='ml-4 text-xs text-slate-300 leading-relaxed'>• {it}</li>" for it in items)
                out_paras.append(f"<ul class='space-y-1 mb-2'>{bullets}</ul>")
            else:
                out_paras.append(f"<p class='text-xs text-slate-300 leading-relaxed mb-2'>{p}</p>")

    return "".join(out_paras)


def normalize_vacancy_for_api(v: dict, statuses: dict) -> dict | None:
    """Приводит сырую вакансию к единой нормализованной схеме для Mini App и REST API."""
    v_id = str(v.get("id", ""))
    raw_title = v.get("title", "")
    title = raw_title.replace("Требуется «", "").replace("»", "").strip()
    company = v.get("company", "Не указана")
    link = v.get("link", "#")
    salary = v.get("salary", "")
    desc = v.get("description", "")
    pub_date = v.get("pub_date", "")

    # Исключение сомнительных ресурсов
    if v.get("source") == "trudvsem" or v_id.startswith("trud_"):
        return None

    # Жесткий отсев middle, senior, нерелевантного стека и черного списка
    import sys
    bot_mod = sys.modules.get("bot")
    if bot_mod and hasattr(bot_mod, "quick_prefilter_reject"):
        rej_fn = bot_mod.quick_prefilter_reject
    else:
        from career_hub.controllers.scrapers.base import quick_prefilter_reject as rej_fn

    if rej_fn(title, desc, company):
        return None

    analysis = v.get("analysis")
    if analysis and isinstance(analysis, dict):
        if analysis.get("suitable") is False or analysis.get("match_score", 0) < 5:
            return None

    source = v.get("source")
    source_name = v.get("source_name")
    if not source:
        if v_id.startswith("hh_"):
            source = "hh"
            source_name = "HeadHunter"
        elif v_id.startswith("habr_") or v_id.isdigit():
            source = "habr"
            source_name = "Хабр Карьера"
        elif v_id.startswith("getmatch_") or v_id.startswith("gm_"):
            source = "getmatch"
            source_name = "GetMatch"
        elif v_id.startswith("vseti_"):
            source = "vseti"
            source_name = "СЕТИ"
        elif v_id.startswith("remotive_"):
            source = "remotive"
            source_name = "Remotive"
        elif v_id.startswith("sj_"):
            source = "superjob"
            source_name = "SuperJob"
        elif v_id.startswith("li_"):
            source = "linkedin"
            source_name = "LinkedIn"
        elif v_id.startswith("tg_"):
            source = "telegram"
            source_name = v.get("source_name", "Telegram")
        else:
            source = "other"
            source_name = "Площадка"

    if source == "vseti" and not source_name:
        source_name = "СЕТИ"

    full_text = f"{title} {desc}".lower()
    role = v.get("role")
    if not role and v.get("analysis") and isinstance(v.get("analysis"), dict):
        role = v.get("analysis").get("role")

    if role in ["aqa_java", "ai_trainer", "support_l2", "qa", "support"]:
        return None

    if not role:
        if any(k in full_text for k in ["remnawave", "xray", "vless", "sing-box"]) or ("vpn" in full_text and any(d in full_text for d in ["разработчик", "developer", "backend", "программист"])):
            role = "vpn_dev"
        elif any(k in title.lower() for k in ["prompt", "промпт", "ai agent", "ai-агент", "llm", "агент"]):
            role = "java_ai"
        elif any(k in full_text for k in ["prompt engineer", "промпт-инженер", "промпт инженер", "mcp-сервер", "mcp server", "gigacode", "gigachat"]):
            role = "java_ai"
        elif any(k in full_text for k in ["spring ai", "spring-ai", "langchain4j", "langchain-4j"]) or (any(k in full_text for k in ["llm", "ai agent", "ai-агент", "ai агент", "genai", "rag"]) and any(j in full_text for j in ["java", "jvm", "spring"])):
            role = "java_ai"
        elif any(k in title.lower() for k in ["java", "kotlin", "spring"]) or (any(k in full_text for k in ["java", "kotlin"]) and any(k in full_text for k in ["developer", "разработчик", "backend", "junior", "стажер"])):
            role = "java"
        elif any(k in title.lower() for k in ["backend", "бэкенд", "разработчик", "developer"]):
            role = "backend"
        else:
            return None

    match_score = v.get("match_score")
    analysis = v.get("analysis")
    if analysis and isinstance(analysis, dict):
        if not match_score:
            match_score = analysis.get("match_score", 7)
    else:
        if not match_score:
            match_score = 8 if ("junior" in title.lower() or "стажер" in title.lower()) else 7
        role_labels = {
            "vpn_dev": "VPN Dev (Xray/Protocols)",
            "java_ai": "Java AI & LLM Integration",
            "backend": "Junior Backend Developer",
            "python_dev": "Backend Developer",
            "java": "Java Backend"
        }
        label = role_labels.get(role, "IT")
        analysis = {
            "match_score": match_score,
            "suitable": True,
            "role": role,
            "verdict": f"Релевантная вакансия по направлению {label}.",
            "education_warning": None,
            "recommendation": "Сделайте акцент на практических проектах и самостоятельном стеке."
        }

    status_info = statuses.get(v_id, {})
    current_status = status_info.get("status", "inbox")

    stack_tags = extract_tech_stack(desc, title or raw_title)
    structured_desc = format_structured_description(desc, source)

    return {
        "id": v_id,
        "title": title or raw_title,
        "company": company,
        "location": v.get("location", ""),
        "link": link,
        "salary": salary,
        "description": desc,
        "structured_description": structured_desc,
        "stack": stack_tags,
        "pub_date": pub_date,
        "source": source,
        "source_name": source_name,
        "role": role,
        "match_score": match_score,
        "analysis": analysis,
        "status": current_status,
        "applied_at": status_info.get("applied_at"),
        "cover_letter": status_info.get("cover_letter") or v.get("cover_letter"),
        "auto_applied": status_info.get("auto_applied", False),
        "auto_prepared": status_info.get("auto_prepared", False)
    }
