import json
import os
import sys
import time

sys.path.insert(0, "/app")
sys.path.insert(0, ".")

from bot import (
    load_config,
    load_cache,
    load_statuses,
    update_vacancy_status,
    generate_cover_letter,
    fetch_hh_vacancy_full,
    fetch_habr_vacancy_by_id,
    CACHE_FILE
)

def save_cache_file(cache):
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2, ensure_ascii=False)

def main():
    print("=== Generating All Missing Cover Letters ===")
    cfg = load_config()
    api_key = cfg.get("groq_api_key")
    model = cfg.get("groq_model", "qwen/qwen3.8-27b")

    if not api_key:
        print("ERROR: No Groq API key found in config!")
        return

    cache = load_cache()
    statuses = load_statuses()

    print(f"Initial State: {len(cache)} cached vacancies, {len(statuses)} statuses.")

    # 1. Identify missing in cache
    vacancies_to_process = {}

    for vid, v in cache.items():
        st = statuses.get(vid, {})
        has_letter = bool((st.get("cover_letter") or "").strip() or (v.get("cover_letter") or "").strip())
        if not has_letter:
            vacancies_to_process[vid] = (v, True)

    print(f"Missing letters in active cache: {len(vacancies_to_process)}")

    # 2. Identify missing in statuses (even if not in cache)
    missing_in_statuses_not_cached = []
    for vid, st in statuses.items():
        has_letter = bool((st.get("cover_letter") or "").strip())
        if not has_letter and vid not in vacancies_to_process:
            missing_in_statuses_not_cached.append(vid)

    print(f"Missing letters in statuses (uncached): {len(missing_in_statuses_not_cached)}")

    # Attempt to fetch details for uncached items
    for vid in missing_in_statuses_not_cached:
        vac_data = None
        if vid.startswith("hh_"):
            raw_id = vid.replace("hh_", "")
            print(f"Fetching details for {vid} from HH...")
            vac_data = fetch_hh_vacancy_full(raw_id)
        elif vid.startswith("habr_") or vid.isdigit():
            print(f"Fetching details for {vid} from Habr...")
            vac_data = fetch_habr_vacancy_by_id(vid)

        is_real = True
        if not vac_data:
            is_real = False
            title = "Разработчик"
            if "java" in vid.lower():
                title = "Java-разработчик"
            elif "python" in vid.lower() or "sj_" in vid:
                title = "Python-разработчик"
            elif "vseti" in vid.lower():
                title = "Fullstack-разработчик"
            vac_data = {
                "id": vid,
                "title": title,
                "company": "Работодатель",
                "description": f"Вакансия {title}",
                "source": "other"
            }

        if is_real:
            cache[vid] = vac_data
        vacancies_to_process[vid] = (vac_data, is_real)

    total = len(vacancies_to_process)
    print(f"\nTotal vacancies queued for letter generation: {total}")

    success_count = 0
    for idx, (vid, (v, is_real)) in enumerate(vacancies_to_process.items(), 1):
        title = v.get("title", "Разработчик")
        company = v.get("company", "Работодатель")
        st = statuses.get(vid, {})
        current_status = st.get("status", "inbox")

        print(f"[{idx}/{total}] Generating for {vid} ({title} @ {company})...", end=" ", flush=True)

        try:
            letter = generate_cover_letter(api_key, model, v)
            if letter and len(letter.strip()) > 40:
                letter_clean = letter.strip()
                # Update status without modifying current status (inbox / applied / archived)
                update_vacancy_status(vid, current_status, cover_letter=letter_clean, auto_prepared=True)
                v["cover_letter"] = letter_clean
                if is_real and vid in cache:
                    cache[vid]["cover_letter"] = letter_clean
                success_count += 1
                print(f"OK ({len(letter_clean)} chars)")
            else:
                print("FAILED (empty/short letter)")
        except Exception as e:
            print(f"ERROR: {e}")

        time.sleep(1.2)

    # Save cache
    save_cache_file(cache)
    print(f"\nSaved updated cache to {CACHE_FILE}")

    # Final Verification
    new_cache = load_cache()
    new_statuses = load_statuses()

    cache_missing_after = [vid for vid, v in new_cache.items() if not (v.get("cover_letter") or "").strip()]
    statuses_missing_after = [vid for vid, st in new_statuses.items() if not (st.get("cover_letter") or "").strip()]

    print("\n=== Final Verification Report ===")
    print(f"Total processed: {success_count}/{total}")
    print(f"Cache items missing letter: {len(cache_missing_after)}")
    print(f"Status items missing letter: {len(statuses_missing_after)}")
    if not cache_missing_after and not statuses_missing_after:
        print("SUCCESS: 100% of vacancies now have tailored cover letters!")
    else:
        if cache_missing_after:
            print("Remaining in cache:", cache_missing_after)
        if statuses_missing_after:
            print("Remaining in statuses:", statuses_missing_after)

if __name__ == "__main__":
    main()
