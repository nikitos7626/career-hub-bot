# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Career Hub AI is an autonomous multi-platform IT vacancy aggregator, LLM-powered scoring engine (Groq Cloud), customized cover letter generator, and auto-apply system with an integrated Telegram Mini App (TMA) and desktop SPA dashboard.

## Development & Execution Commands

### Environment Setup
- Copy `.env.example` to `.env` and configure required variables (`BOT_TOKEN`, `CHAT_ID`, `ALLOWED_TELEGRAM_ID`, `GROQ_API_KEY`).
- Optional custom search queries / blacklists: copy `bot_config.example.json` to `data/bot_config.json`.
- Install dependencies: `pip install -r requirements.txt` (only `requests>=2.31.0` and `urllib3>=2.0.0`; all other logic uses Python stdlib).

### Running the Application
- **Direct run**: `python bot.py` (spawns Telegram poller, HTTP REST server on `:8088`, and scraping/scoring loop).
- **Docker Compose**: `docker compose up -d --build` (runs container with volume mounts for `data/` and auto-restart).
- **Web Dashboard / Mini App**: accessible at `http://127.0.0.1:8088/` or through reverse proxy / Cloudflare Tunnel.

### Testing
- **Run all unit & integration tests**:
  ```bash
  python -m unittest test_auto_apply_system.py
  ```
- **Run tests with verbose output**:
  ```bash
  python -m unittest test_auto_apply_system.py -v
  ```
- **Run a specific test class**:
  ```bash
  python -m unittest test_auto_apply_system.TestAutoApplyAndNotifications
  ```
- **Run a single test method**:
  ```bash
  python -m unittest test_auto_apply_system.TestAutoApplyAndNotifications.test_default_config_fields
  ```
- **Run tests matching a pattern**:
  ```bash
  python -m unittest discover -p "test_*.py" -k "hh_api"
  ```

## Architecture & Code Structure

The project implements a lightweight, dependency-free MVC pattern under the `career_hub/` package. The top-level `bot.py` acts as a facade re-exporting all symbols for backward compatibility.

### Concurrency Model (`career_hub/__init__.py`)
`run()` initializes three concurrent components:
1. `telegram_updates_worker`: daemon thread running long-polling over the Telegram Bot API with HTML entity fallback and command processing.
2. `http_server_worker`: daemon thread running `ThreadingHTTPServer` on port 8088 serving `miniapp_index.html` and REST API endpoints.
3. `vacancy_monitor_worker`: foreground execution loop iterating across enabled scrapers, deduplicating, scoring, and auto-applying.

### Core Modules

- **`career_hub/models/`**:
  - `config.py`: Configuration loader with file mtime caching, environment variable overrides, candidate profile definition, and query lists.
  - `storage.py`: Thread-safe persistence using re-entrant locks (`RLock`) and atomic file replacement (`_atomic_write_json` via temporary files). Manages `data/vacancies_cache.json`, `data/vacancy_statuses.json`, `data/seen_vacancies.json` (7-day TTL), `data/hh_negotiations_tracker.json`, and `data/auth_sessions.json`.
  - `vacancy.py`: Data normalization for API consumption, HTML sanitization, and regex-based tech stack extraction (`KNOWN_TECH`).

- **`career_hub/controllers/`**:
  - `scrapers/`: Modular fetchers for 9+ job boards:
    - HeadHunter (`hh.py`), Habr Career (`habr.py` RSS), SuperJob (`superjob.py`), GetMatch (`getmatch.py`), LinkedIn (`linkedin.py`), Rabota.ru (`rabota.py`), TrudVsem (`trudvsem.py`), Remotive (`remotive.py`), Vseti (`vseti.py`), and Telegram channels (`telegram.py`).
    - `base.py`: `quick_prefilter_reject()` evaluates stop-words (QA, tech support, courses, senior/lead titles, blacklisted companies) *before* sending payloads to LLMs to minimize API cost.
  - `ai_controller.py`:
    - `analyze_vacancy_with_groq()`: Evaluates vacancies (1–10 score) using Groq API with automatic fallback across models (`llama-3.3-70b-versatile`, `llama-3.1-8b-instant`, `gemma2-9b-it`, `mixtral-8x7b-32768`).
    - `generate_cover_letter()`: Generates tailored cover letters targeting candidate profiles (Java Backend, AI Engineer, Python Dev, Fullstack JS, VPN/Proxy infrastructure).
  - `auto_apply.py`: Multi-platform application dispatcher (`apply_vacancy_dispatcher`):
    - HeadHunter: official API (`apply_hh_vacancy_api`) and web session with cookies + `_xsrf` (`apply_hh_vacancy_web`).
    - Habr Career: web cookie session (`apply_habr_vacancy_web`).
    - SuperJob, GetMatch, Rabota.ru, TrudVsem, Telegram recruiter deep-links, and SMTP email applications.
    - Automated cleanup: `remove_vacancy_from_profile_and_history` purges rejected applications from HH profile.
  - `bot_controller.py`: Telegram event handler, desktop login approval flows (`/start login_<id>`, 6-digit PIN, `/login`), notification throttling (only scores ≥ `notify_min_score` trigger instant alerts), and daily digest scheduler.

- **`career_hub/views/`**:
  - `auth_view.py`: Telegram WebApp `initData` HMAC-SHA256 signature verification, one-time token generation, and 30-day session management.
  - `http_server.py`: REST API handling `/api/vacancies`, `/api/status`, `/api/auto_apply`, `/api/settings`, credential verification (`/api/check_*`), and serving the SPA frontend (`miniapp_index.html`).

### Key Conventions
- **Zero Heavy Frameworks**: No `aiohttp`, `aiogram`, or `fastapi`. Use Python standard library (`http.server`, `threading`, `urllib`) + `requests`.
- **Thread Safety**: Any read/write to files in `data/` must acquire the corresponding `storage.py` lock (`cache_lock`, `statuses_lock`, `sessions_lock`, etc.) or use the provided helper functions.
- **Score Filtering**:
  - Score < 6: discarded or archived.
  - Score 6–7: silently saved to Mini App database without Telegram spam.
  - Score ≥ 8: prepared for auto-apply.
  - Score ≥ `notify_min_score` (default 7–9): triggers instant priority Telegram alert.
