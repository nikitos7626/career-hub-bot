import json
import os
import sys
sys.path.insert(0, "/app")
sys.path.insert(0, ".")
from bot import load_config, save_config, load_cache, load_statuses, save_statuses, CACHE_FILE


def main():
    print("=== Disabling and Cleaning Trudvsem (Работа России) ===")
    
    # 1. Update config
    cfg = load_config()
    cfg["enable_trudvsem"] = False
    save_config(cfg)
    print("1. Config updated: enable_trudvsem = False")

    # 2. Clean cache
    cache = load_cache()
    if isinstance(cache, dict):
        trud_cache_keys = [k for k in cache.keys() if str(k).startswith("trud_") or cache[k].get("source") == "trudvsem"]
        for k in trud_cache_keys:
            del cache[k]
        
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=2, ensure_ascii=False)
        print(f"2. Cleaned {len(trud_cache_keys)} vacancies from vacancies_cache.json")

    # 3. Clean statuses
    statuses = load_statuses()
    trud_status_keys = [k for k in statuses.keys() if str(k).startswith("trud_")]
    for k in trud_status_keys:
        del statuses[k]
    save_statuses(statuses)
    print(f"3. Cleaned {len(trud_status_keys)} records from vacancy_statuses.json")

    print("Done! 'Работа России' has been completely removed from search and database.")

if __name__ == "__main__":
    main()
