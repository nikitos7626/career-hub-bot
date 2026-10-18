"""
Storage Model for Career Hub.
Thread-safe, atomic file persistence for cache, statuses, seen vacancies, negotiations, and sessions.
"""

import os
import sys
import json
import logging
import threading
from datetime import datetime, timezone

logger = logging.getLogger("career_hub.storage")

DATA_DIR = os.environ.get("DATA_DIR", os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data"))
CONFIG_FILE = os.path.join(DATA_DIR, "bot_config.json")
STATUSES_FILE = os.path.join(DATA_DIR, "vacancy_statuses.json")
CACHE_FILE = os.path.join(DATA_DIR, "vacancies_cache.json")
AUTH_SESSIONS_FILE = os.path.join(DATA_DIR, "auth_sessions.json")
SEEN_FILE = os.path.join(DATA_DIR, "seen_vacancies.json")
STATE_FILE = SEEN_FILE
HH_NEGOTIATIONS_FILE = os.path.join(DATA_DIR, "hh_negotiations_tracker.json")
MINIAPP_HTML_PATH = os.path.join(DATA_DIR, "miniapp", "index.html")

# Locks (RLock allows safe re-entrant acquisition within the same thread)
cache_lock = threading.RLock()
statuses_lock = threading.RLock()
sessions_lock = threading.RLock()
seen_lock = threading.RLock()
hh_neg_lock = threading.RLock()

SEEN_VACANCIES_TTL_DAYS = 7


def _get_path(attr_name, default_val):
    bot_mod = sys.modules.get("bot")
    if bot_mod and hasattr(bot_mod, attr_name):
        return getattr(bot_mod, attr_name)
    return default_val


def _atomic_write_json(filepath, data, indent=2):
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    tmp_path = f"{filepath}.tmp.{os.getpid()}"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=indent, ensure_ascii=False)
    os.replace(tmp_path, filepath)


# ==========================================
# Vacancy Statuses
# ==========================================

def load_statuses():
    fpath = _get_path("STATUSES_FILE", STATUSES_FILE)
    if not os.path.exists(fpath):
        return {}
    with statuses_lock:
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Ошибка чтения статусов {fpath}: {e}")
            return {}


def save_statuses(statuses):
    fpath = _get_path("STATUSES_FILE", STATUSES_FILE)
    with statuses_lock:
        try:
            _atomic_write_json(fpath, statuses, indent=2)
        except Exception as e:
            logger.error(f"Ошибка сохранения статусов {fpath}: {e}")


def update_vacancy_status(v_id, status, **kwargs):
    with statuses_lock:
        fpath = _get_path("STATUSES_FILE", STATUSES_FILE)
        statuses = {}
        if os.path.exists(fpath):
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    statuses = json.load(f)
            except Exception:
                statuses = {}
        now_iso = datetime.now(timezone.utc).isoformat()
        cur = statuses.get(v_id, {})
        cur["status"] = status
        cur["updated_at"] = now_iso
        if "created_at" not in cur:
            cur["created_at"] = now_iso
        for k, v in kwargs.items():
            if v is not None:
                cur[k] = v
        statuses[v_id] = cur
        _atomic_write_json(fpath, statuses, indent=2)
        return cur


# ==========================================
# Vacancies Cache
# ==========================================

def load_cache():
    fpath = _get_path("CACHE_FILE", CACHE_FILE)
    if not os.path.exists(fpath):
        return {}
    with cache_lock:
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}


def save_to_cache(vacancy):
    fpath = _get_path("CACHE_FILE", CACHE_FILE)
    with cache_lock:
        cache = {}
        if os.path.exists(fpath):
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    cache = json.load(f)
            except Exception:
                cache = {}
        cache[vacancy["id"]] = vacancy
        if len(cache) > 500:
            keys_to_del = list(cache.keys())[:-500]
            for k in keys_to_del:
                del cache[k]
        _atomic_write_json(fpath, cache, indent=2)


def get_from_cache(v_id):
    cache = load_cache()
    return cache.get(v_id)


def delete_from_cache(v_id):
    fpath = _get_path("CACHE_FILE", CACHE_FILE)
    with cache_lock:
        cache = {}
        if os.path.exists(fpath):
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    cache = json.load(f)
            except Exception:
                cache = {}
        if v_id in cache:
            del cache[v_id]
            try:
                _atomic_write_json(fpath, cache, indent=2)
            except Exception as e:
                logger.error(f"Ошибка удаления из кэша: {e}")


# ==========================================
# Seen Vacancies & History
# ==========================================

def load_seen_vacancies():
    fpath = _get_path("STATE_FILE", _get_path("SEEN_FILE", SEEN_FILE))
    if not os.path.exists(fpath):
        return {}
    with seen_lock:
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                data = json.load(f)

            now = datetime.now(timezone.utc)
            cutoff = now.timestamp() - (SEEN_VACANCIES_TTL_DAYS * 86400)
            statuses_path = _get_path("STATUSES_FILE", STATUSES_FILE)
            statuses = {}
            if os.path.exists(statuses_path):
                try:
                    with open(statuses_path, "r", encoding="utf-8") as sf:
                        statuses = json.load(sf)
                except Exception:
                    pass

            if isinstance(data, list):
                ts_str = now.isoformat()
                return {v_id: ts_str for v_id in data}

            purged = 0
            seen = {}
            for v_id, ts_str in data.items():
                try:
                    ts = datetime.fromisoformat(ts_str).timestamp()
                    if ts >= cutoff or v_id in statuses:
                        seen[v_id] = ts_str
                    else:
                        purged += 1
                except Exception:
                    seen[v_id] = ts_str
            return seen
        except Exception as e:
            logger.error(f"Ошибка загрузки истории вакансий: {e}")
            return {}


def seen_add(seen: dict, v_id: str):
    seen[v_id] = datetime.now(timezone.utc).isoformat()
    return seen


def save_seen_vacancies(seen):
    fpath = _get_path("STATE_FILE", _get_path("SEEN_FILE", SEEN_FILE))
    with seen_lock:
        if isinstance(seen, set):
            seen = {v_id: datetime.now(timezone.utc).isoformat() for v_id in seen}
        if len(seen) > 5000:
            sorted_items = sorted(seen.items(), key=lambda x: x[1], reverse=True)[:5000]
            seen = dict(sorted_items)
        _atomic_write_json(fpath, seen, indent=2)


# ==========================================
# HH Negotiations Tracker
# ==========================================

def load_hh_negotiations_tracker():
    fpath = _get_path("HH_NEGOTIATIONS_FILE", HH_NEGOTIATIONS_FILE)
    if not os.path.exists(fpath):
        return {}
    with hh_neg_lock:
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}


def save_hh_negotiations_tracker(tracker):
    fpath = _get_path("HH_NEGOTIATIONS_FILE", HH_NEGOTIATIONS_FILE)
    with hh_neg_lock:
        try:
            _atomic_write_json(fpath, tracker, indent=2)
        except Exception as e:
            logger.error(f"Ошибка сохранения hh_negotiations_tracker: {e}")


# ==========================================
# Auth Sessions
# ==========================================

def load_sessions():
    fpath = _get_path("AUTH_SESSIONS_FILE", AUTH_SESSIONS_FILE)
    if not os.path.exists(fpath):
        return {}
    with sessions_lock:
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}


def save_sessions(sessions):
    fpath = _get_path("AUTH_SESSIONS_FILE", AUTH_SESSIONS_FILE)
    with sessions_lock:
        try:
            _atomic_write_json(fpath, sessions, indent=2)
        except Exception as e:
            logger.error(f"Ошибка сохранения auth_sessions: {e}")
