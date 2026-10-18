import json
import sys
import time

sys.path.insert(0, "/app")
sys.path.insert(0, ".")

from bot import (
    load_config,
    load_cache,
    load_statuses,
    update_vacancy_status,
    save_statuses,
    generate_cover_letter,
    normalize_vacancy_for_api,
    CACHE_FILE
)

def main():
    print("=== Syncing and Verifying All Cover Letters ===")
    cfg = load_config()
    api_key = cfg.get("groq_api_key")
    model = cfg.get("groq_model", "qwen/qwen3.8-27b")

    cache = load_cache()
    statuses = load_statuses()

    print(f"Loaded: {len(cache)} cached items, {len(statuses)} status items.")

    # 1. Sync from statuses to cache where missing in cache
    synced_to_cache = 0
    for vid, v in cache.items():
        st_letter = (statuses.get(vid, {}).get("cover_letter") or "").strip()
        cache_letter = (v.get("cover_letter") or "").strip()
        if st_letter and not cache_letter:
            v["cover_letter"] = st_letter
            cache[vid]["cover_letter"] = st_letter
            synced_to_cache += 1

    print(f"1. Synced from statuses to cache: {synced_to_cache} vacancies.")

    # 2. Sync from cache to statuses where missing in statuses
    synced_to_statuses = 0
    for vid, v in cache.items():
        st = statuses.get(vid, {})
        st_letter = (st.get("cover_letter") or "").strip()
        cache_letter = (v.get("cover_letter") or "").strip()
        if cache_letter and not st_letter:
            cur_status = st.get("status", "inbox")
            st["status"] = cur_status
            st["cover_letter"] = cache_letter
            st["auto_prepared"] = True
            statuses[vid] = st
            synced_to_statuses += 1

    if synced_to_statuses > 0:
        save_statuses(statuses)
    print(f"2. Synced from cache to statuses: {synced_to_statuses} vacancies.")

    # 3. Check for any remaining vacancies in cache or statuses that have NO letter in either
    truly_missing = []
    for vid, v in cache.items():
        letter = (v.get("cover_letter") or "").strip()
        if not letter:
            truly_missing.append((vid, v))

    print(f"3. Vacancies in cache still missing letter: {len(truly_missing)}")
    for vid, v in truly_missing:
        title = v.get("title", "")
        company = v.get("company", "")
        print(f"   Generating for {vid} ({title} @ {company})...", end=" ", flush=True)
        try:
            letter = generate_cover_letter(api_key, model, v)
            if letter and len(letter.strip()) > 40:
                letter_clean = letter.strip()
                v["cover_letter"] = letter_clean
                cache[vid]["cover_letter"] = letter_clean
                st = statuses.get(vid, {})
                st_status = st.get("status", "inbox")
                st["status"] = st_status
                st["cover_letter"] = letter_clean
                st["auto_prepared"] = True
                statuses[vid] = st
                print(f"OK ({len(letter_clean)} chars)")
            else:
                print("FAILED (empty)")
        except Exception as e:
            print(f"ERROR: {e}")
        time.sleep(1.2)

    # Save both files
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2, ensure_ascii=False)
    save_statuses(statuses)
    print("4. Saved cache and statuses.")

    # 4. Verification for Web UI (normalize_vacancy_for_api)
    api_vacs = [normalize_vacancy_for_api(v, statuses) for v in cache.values()]
    api_vacs = [v for v in api_vacs if v is not None]

    ui_missing = [v for v in api_vacs if not (v.get("cover_letter") or "").strip()]

    print(f"\n=== Verification Report ===")
    print(f"Total visible vacancies in UI: {len(api_vacs)}")
    print(f"UI vacancies missing letter: {len(ui_missing)}")
    if ui_missing:
        for v in ui_missing:
            print(f" * MISSING: {v['id']} ({v.get('source')}): {v.get('title')}")
    else:
        print("PERFECT: 100% of visible vacancies have valid, non-empty cover letters!")

if __name__ == "__main__":
    main()
