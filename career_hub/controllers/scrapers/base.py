"""
Base scraper definitions, shared HTTP headers, and strict prefilter logic.
"""

import re
import logging
from career_hub.models.config import load_config

logger = logging.getLogger("career_hub.scrapers.base")

HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
}


def quick_prefilter_reject(title: str, description: str, company: str = "") -> bool:
    """
    Быстрый локальный фильтр нерелевантных вакансий без траты токенов LLM.
    Возвращает True, если вакансия должна быть немедленно отброшена.
    """
    title_lower = title.lower() if title else ""
    desc_lower = description.lower() if description else ""
    comp_lower = company.lower() if company else ""
    full_text = f"{title_lower} {desc_lower} {comp_lower}"

    # 0.0. Отсев дайджестов, подборок и агрегированных постов
    if any(d in title_lower for d in ["подборка", "дайджест", "digest", "вакансии недели", "вакансии дня"]):
        return True
    if any(d in full_text[:140] for d in ["подборка вакансий", "дайджест вакансий", "подборка junior", "подборка стажер", "подборка стажёр"]):
        return True

    # 0.1. Строгий отсев не-разработки: QA / AQA / Тестирование
    if re.search(r'\b(qa|aqa|tester|testing)\b', title_lower) or any(qa in title_lower for qa in ["тестиров", "тестер", "automation qa", "qa automation", "автотест", "test engineer", "test automation"]):
        return True

    # 0.2. Строгий отсев техподдержки, дежурных инженеров, сопровождения
    if any(sup in title_lower for sup in ["поддержк", "сопровожден", "дежурный инженер", "support", "helpdesk", "service desk", "администратор бд"]):
        return True

    # 0.3. Строгий отсев AI-тренеров, разметчиков данных, репетиторов и онлайн-школ (преподавание строго отсекаем)
    if any(trn in title_lower for trn in ["ai-тренер", "ai тренер", "тренер", "разметчик", "разметка кода", "code trainer", "преподавател", "тьютор", "репетитор", "учитель", "педагог", "ментор", "наставник", "куратор"]):
        return True

    # 0.4. Строгий отсев не-IT профессий и аналитики данных
    non_target_professions = [
        "юрист", "адвокат", "редактор", "копирайтер", "переводчик", "филолог", "врач", "медицин",
        "историк", "химик", "биолог", "гуманитар", "лингвист", "бухгалтер", "менеджер по продажам",
        "маркетолог", "дизайнер", "контент", "smm", "шахмат", "вожатый", "аниматор", "мебел",
        "data scientist", "дата сайентист", "data analyst", "аналитик данных", "bi аналитик", "системный аналитик", "бизнес-аналитик", "бизнес аналитик"
    ]
    for ntp in non_target_professions:
        if ntp in title_lower:
            return True

    # 0.5. Черный список компаний (Aston и др.)
    config = load_config()
    blacklisted = config.get("blacklisted_companies", ["aston", "астон"])
    for b in blacklisted:
        b_clean = b.lower().strip()
        if not b_clean:
            continue
        if b_clean in comp_lower or b_clean in title_lower:
            return True
        if f"компания {b_clean}" in desc_lower or f"компания «{b_clean}" in desc_lower or f'компания "{b_clean}"' in desc_lower:
            return True
        if b_clean in ["aston", "астон"] and (f" {b_clean} " in full_text or f"«{b_clean}»" in full_text):
            return True

    # 0.6. Отсев курсов и инфобизнеса
    course_ad_markers = [
        "бесплатные курс", "бесплатных курс", "обучение на курс",
        "пройти курс", "лаборатория обучени", "школа программирования", "обучающий курс",
        "интенсив с последующ", "пройти обучение на наш", "вступительные испытания на курс",
        "предлагаем обучение на", "бесплатном обучении", "курсы для начинающ",
        "стоимость курса", "платный курс", "платные курсы", "купить курс"
    ]
    for cm in course_ad_markers:
        if cm in full_text:
            return True
    if any(cm in title_lower for cm in ["курс программирования", "интенсив с трудоустройством", "обучение разработчик"]):
        return True
    if ("онлайн-курс" in full_text or "онлайн курс" in full_text):
        if not any(perk in full_text for perk in ["оплат", "компенсац", "доступ к", "бюджет на", "льгот", "бенефит"]):
            return True

    # 1. Строгий отсев Middle / Senior / Lead по заголовку
    hard_stops_title = [
        "middle", "мидл", "миддл", "senior", "синьор", "сеньор", "старший",
        "lead", "лид", "лидер", "тимлид", "техлид", "teamlead", "techlead",
        "ведущий", "главный", "архитектор", "директор", "руководитель", "начальник",
        "head of", "director", "expert", "эксперт", "principal", "руководящ",
        "управляющий", "chief", "cto", "cio", "cpo", "vp ", "vp-", "vp_", "vice president",
        "от 3 лет", "от 3-х лет", "от 5 лет", "от 4 лет", "от 2 лет", "от 2-х лет",
        "middle+", "middle / senior", "middle/senior"
    ]
    for stop in hard_stops_title:
        if stop in title_lower:
            return True

    # 2. Теги в заголовке/описании
    grade_tags = [
        "#middle", "#middle+", "#middle_plus", "#мидл",
        "#senior", "#senior+", "#сеньор", "#синьор",
        "#lead", "#teamlead", "#techlead", "#лид"
    ]
    for tag in grade_tags:
        if tag in desc_lower or tag in title_lower:
            return True

    # 3. Отсев псевдо-джуниоров: HighLoad
    hl_patterns = [
        r"опыт\s+(?:разработки|проектирования|работы\s+с|создания)\s+высоконагруженн",
        r"опыт\s+highload",
        r"highload\s+от\s+\d+",
        r"проектирование\s+высоконагруженн"
    ]
    for hlp in hl_patterns:
        if re.search(hlp, desc_lower):
            return True

    # 3.1. Явные маркеры грейда Middle / Senior в описании
    grade_markers = [
        "грейд: middle", "грейд: senior", "грейд: lead",
        "квалификация: middle", "квалификация: senior", "квалификация: lead",
        "уровень: middle", "уровень: senior", "уровень: lead",
        "уровень позиции: middle", "уровень позиции: senior",
        "middle developer", "middle разработчик", "middle java",
        "senior developer", "senior разработчик", "senior java",
        "lead developer", "lead разработчик",
        "пяти лет", "пять лет", "четырех лет", "четырёх лет", "четыре года",
        "трех лет", "трёх лет", "три года",
        "от 5 лет", "от 5-ти лет", "от 5ти лет", "от 4 лет", "от 4-х лет",
        "от 3 лет", "от 3-х лет", "от 3-ух лет",
        "3-6 лет", "3–6 лет", "3 - 6 лет",
        "опыт работы: от 3", "опыт работы: от 4", "опыт работы: от 5", "опыт работы: от 6",
        "опыт работы от 3", "опыт работы от 4", "опыт работы от 5",
        "требуемый опыт: от 3", "требуемый опыт от 3",
        "менторство стаж", "менторство разработчик", "обучение стажер", "наставничество стажер",
        "управление командой", "руководство команд", "код-ревью команды", "ревью кода коллег"
    ]
    for marker in grade_markers:
        if marker in desc_lower:
            return True

    if re.search(r'(?:опыт|стаж|требован|коммерческ)\D{0,25}от\s+(?:[3-9]|\d{2})\s*(?:-ти|-ми|-х|-ух|-го)?\s*лет', desc_lower):
        return True
    if re.search(r'от\s+(?:трех|трёх|четырех|четырёх|пяти|шести)\s+лет', desc_lower):
        return True

    # 4. Чужие языки, платформы и чужой бэкенд (отсекаем ДО целевых ролей)
    # 4.1. 1С
    if re.search(r'\b(1с|1c)\b', title_lower) or any(k in title_lower for k in ["1с-", "1c-", "1с:", "1c:"]):
        return True

    # 4.2. C# / .NET
    if any(k in title_lower for k in ["c#", ".net", "dotnet", "c sharp", "си шарп"]) or "c#-" in title_lower or "c#-разработчик" in title_lower:
        if "java" not in title_lower and "javascript" not in title_lower:
            return True

    # 4.3. Go / Golang
    if any(k in title_lower for k in ["golang", "go-разработчик", "go разработчик", "go developer", "разработчик go", "стажёр go", "стажер go", "junior go"]):
        if "java" not in title_lower:
            return True

    # 4.4. PHP / Ruby / Rust
    if any(k in title_lower for k in ["php", "laravel", "symfony", "bitrix", "битрикс", "ruby", "rails", "rust"]):
        if "java" not in title_lower and "javascript" not in title_lower and "python" not in title_lower:
            return True

    # 4.5. C / C++ / Embedded
    if re.search(r'\b(c\+\+|с\+\+|embedded|stm32|микроконтроллер)\b', title_lower) or "c++-" in title_lower or "с++-" in title_lower:
        if "java" not in title_lower and "python" not in title_lower:
            return True

    # 4.6. GameDev / 3D
    if any(g in title_lower for g in ["unity", "unreal", "godot", "gamedev", "геймдев", "3d-", "3d ", "3д "]):
        return True

    # 4.7. Мобильная разработка (iOS / Swift / Flutter / Android) — если не web и не Java
    if any(m in title_lower for m in ["ios", "swift", "flutter", "dart"]):
        if "java" not in title_lower and "javascript" not in title_lower:
            return True
    if any(m in title_lower for m in ["разработчик мобильных", "mobile developer", "мобильный разработчик", "мобильной разработке"]):
        if not any(k in full_text for k in ["react native", "java", "javascript", "react"]):
            return True

    # 4.8. DevOps / SRE / DBA
    if any(d in title_lower for d in ["devops", "sre", "dba", "системный администратор", "сисадмин", "администратор баз"]):
        return True

    # 5. Определение целевых ролей кандидата (ТОЛЬКО РАЗРАБОТКА ПО)
    is_vpn_dev = any(k in full_text for k in ["remnawave", "xray", "vless", "sing-box"]) or (
        "vpn" in full_text and any(d in full_text for d in ["разработчик", "developer", "backend", "программист"])
    )
    is_ai_dev = any(k in full_text for k in [
        "spring ai", "spring-ai", "langchain4j", "langchain-4j", "langchain",
        "llm", "ai agent", "ai-агент", "ai агент", "genai", "rag", "инженер ии",
        "разработчик ии", "ai engineer", "ai developer", "llm engineer", "ai разработчик"
    ]) or any(k in title_lower for k in [
        "ai engineer", "ai developer", "llm developer", "инженер ии", "разработчик ии", "ai разработчик"
    ])
    is_java = any(k in title_lower for k in ["java", "kotlin", "spring"]) or (
        any(k in full_text for k in ["java", "kotlin"]) and any(d in full_text for d in ["разработчик", "developer", "backend", "стажер", "junior"])
    )
    is_junior = any(j in title_lower for j in [
        "junior", "джуниор", "джун", "младший", "начинающий", "стажер", "стажёр", 
        "intern", "trainee", "практикант"
    ]) or any(j in full_text[:400] for j in [
        "junior", "джуниор", "джун", "стажер", "стажёр", "intern", "trainee", "начинающий"
    ])

    is_python_backend = (
        any(k in title_lower for k in ["python", "питон", "fastapi", "django", "flask"]) or
        (any(k in full_text for k in ["python", "fastapi", "django", "flask"]) and any(d in full_text for d in ["backend", "бэкенд", "разработчик", "developer", "api"]))
    ) and (is_junior or any(j in title_lower for j in ["junior", "джуниор", "стажер", "стажёр", "trainee", "intern", "младший", "начинающий", "разработчик", "developer"]))

    is_fullstack_js = (
        any(k in title_lower for k in [
            "fullstack", "full-stack", "фулстек", "фуллстек", "full stack",
            "frontend", "front-end", "фронтенд", "веб-разработчик", "веб разработчик", "web developer",
            "javascript", "typescript", "react", "node", "nodejs"
        ]) or (
            any(k in full_text for k in ["fullstack", "фулстек", "react", "node.js", "nodejs"]) and
            any(d in full_text for d in ["разработчик", "developer", "программист", "стажер", "стажёр", "junior"])
        )
    ) and (is_junior or any(j in title_lower for j in ["junior", "джуниор", "стажер", "стажёр", "trainee", "intern", "младший", "начинающий"]))

    is_backend_dev = any(k in title_lower for k in ["backend", "бэкенд"]) and (
        is_junior or any(s in title_lower for s in ["стажер", "стажёр", "junior", "младший", "intern", "trainee", "начинающий"])
    )

    is_generic_junior = (
        any(s in title_lower for s in ["стажер", "стажёр", "intern", "trainee", "практикант", "младший", "начинающий", "junior", "джуниор"]) and
        any(d in title_lower for d in ["разработчик", "developer", "программист", "специалист"])
    ) and any(j in full_text for j in [
        "java", "jvm", "spring", "backend", "бэкенд", "fullstack", "фулстек", "frontend", "фронтенд",
        "react", "node", "javascript", "typescript", "python", "fastapi", "rest api", "postgresql"
    ])

    # Если вакансия НЕ по целевой разработке — отсекаем
    if not is_java and not is_vpn_dev and not is_ai_dev and not is_backend_dev and not is_fullstack_js and not is_generic_junior and not is_python_backend:
        return True

    # 5.1. Отсев опыта работы
    if not is_junior:
        strict_non_junior_markers = [
            "двух лет", "два года", "полутора лет",
            "от 2 лет", "от 2-х лет", "от 2-ух лет",
            "опыт работы: от 2", "опыт работы от 2", "требуемый опыт: от 2", "требуемый опыт от 2",
            "опыт коммерческой разработки на java — от", "опыт коммерческой разработки на java - от",
            "опыт коммерческой разработки от 2", "коммерческий опыт разработки от 2"
        ]
        for marker in strict_non_junior_markers:
            if marker in desc_lower:
                return True
        if re.search(r'(?:опыт(?:а)?(?:\s+коммерческой)?(?:\s+разработки)?(?:\s+работы)?\s*(?:на\s+java)?)?\s*от\s+2\s*(?:-ти|-ми|-х|-ух|-го)?\s*лет', desc_lower):
            return True
        if re.search(r'от\s+(?:двух)\s+лет', desc_lower):
            return True

    # 6. Строгий отсев нерелевантного железа, физического монтажа и эникея
    hard_sysadmin_rejects = [
        "выездной инженер", "картридж", "ремонт принтер", "сборка пк", "монтажник скд",
        "монтаж кабел", "слаботоч", "охранно-пожарн", "видеонаблюдение", "эникей", "сисадмин в офис",
        "системный администратор windows", "заправка картриджей"
    ]
    for stop in hard_sysadmin_rejects:
        if stop in title_lower or stop in full_text[:300]:
            return True

    # 7. Отсев иероглифов, французских/немецких локальных вакансий и Украины
    if re.search(r'[一-鿿]', full_text):
        return True

    non_target_markers = [
        "développeur", "alternance", "stage -", "entwickler", "m/w/d", "werkstudent",
        "ukraine", "украин", "lviv", "львов", "kyiv", "киев", "kharkiv", "днепр", "dnipro"
    ]
    for ntm in non_target_markers:
        if ntm in full_text:
            return True

    # 8. Международные стоп-факторы и требования виз/гражданства других стран
    intl_stops = [
        "us citizen", "us citizenship", "green card", "authorized to work in the us",
        "work authorization in the us", "must reside in the us", "must be located in the us",
        "security clearance", "ts/sci", "w2 only", "c2c only", "eu citizenship",
        "eu work permit", "right to work in the uk", "right to work in the eu",
        "must be located in the uk", "must reside in germany", "german b2", "german c1",
        "fluent german", "no contractors in russia", "no candidates from russia",
        "not eligible for visa sponsorship", "sponsorship is not available"
    ]
    for stop in intl_stops:
        if stop in desc_lower:
            return True

    return False
