"""
run_recovery.py — The 3-Tab CAPTCHA Recovery Manager
======================================================
"""

import time
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright

from ai_agent_db import get_db, FormSession, AnonymousProfile, FieldMappingCache
from ai_agent_test import extract_all_form_elements

# Configuration
CHUNK_SIZE = 3


def get_failed_sessions() -> list:
    """Fetch unsuccessful sessions and group them by URL so you only solve a site once."""
    with get_db() as db:
        sessions = db.query(FormSession).filter(
            FormSession.status.in_(["CAPTCHA_ERROR", "PARTIAL", "FAILED"])
        ).all()

        # Group duplicates: { "url": [session_id_1, session_id_2] }
        unique_urls = {}
        for s in sessions:
            if s.target_url not in unique_urls:
                unique_urls[s.target_url] = {
                    "url": s.target_url,
                    "domain": s.domain,
                    "session_ids": [],
                    "profile_id": s.profile_id  # We just use the first profile for the speed fill
                }
            unique_urls[s.target_url]["session_ids"].append(s.id)

        return list(unique_urls.values())


def mark_sessions_resolved(session_ids: list):
    """Update all duplicate database records at once when the tab is closed."""
    with get_db() as db:
        for s_id in session_ids:
            session = db.query(FormSession).filter_by(id=s_id).first()
            if session:
                session.status = "SUCCESS (RECOVERED)"
                print(f"  ✅ Session #{s_id} marked as recovered.")


def speed_fill_page(page, domain: str, profile_id: int):
    """Instantly fills the safe fields using the cached DB rules."""
    with get_db() as db:
        profile = db.query(AnonymousProfile).filter_by(id=profile_id).first()
        if not profile: return
        profile_data = profile.to_dynamic_dict()

        cache_records = db.query(FieldMappingCache).filter_by(website_domain=domain).all()
        rules = [{"placeholder": r.html_placeholder, "db_key": r.mapped_db_key} for r in cache_records]

        if not rules: return

    scraped_fields = []
    element_map = {}
    for frame in [page.main_frame] + page.frames:
        try:
            fields, el_map = extract_all_form_elements(page, frame)
            scraped_fields.extend(fields)
            element_map.update(el_map)
        except Exception:
            pass

    for rule in rules:
        placeholder = rule["placeholder"]
        target_key = next((k for k in element_map if k.startswith(f"{placeholder}_")), None)

        if not target_key: continue

        element = element_map[target_key]
        db_key = rule["db_key"]

        if db_key.startswith("GENERATED_TEXT:"):
            val = db_key.replace("GENERATED_TEXT:", "").strip()
        else:
            val = str(profile_data.get(db_key, ""))

        if not val or val in ("None", "null"): continue

        try:
            tag = element.evaluate("el => el.tagName.toLowerCase()")
            type_attr = element.get_attribute("type") or "text"

            if tag == "select":
                element.select_option(label=val, timeout=1000)
            elif type_attr in ("checkbox", "radio"):
                if val.lower() in ("true", "yes", "on"):
                    if not element.is_checked():
                        element.evaluate("el => el.click()")
            else:
                element.fill(val)
        except Exception:
            pass


def run_recovery_mode():
    grouped_sessions = get_failed_sessions()

    if not grouped_sessions:
        print("🎉 No failed sessions found! The queue is clean.")
        return

    print(f"🔍 Found {len(grouped_sessions)} UNIQUE URLs to recover.")
    print(f"📦 Processing in chunks of {CHUNK_SIZE}...\n")

    chunks = [grouped_sessions[i:i + CHUNK_SIZE] for i in range(0, len(grouped_sessions), CHUNK_SIZE)]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, args=["--start-maximized"])

        for chunk_index, chunk in enumerate(chunks, 1):
            # NEW: Check if the user killed the browser window
            if not browser.is_connected():
                print("\n🛑 Browser window was closed manually. Aborting recovery mode.")
                break

            print(f"\n{'=' * 40}")
            print(f"🚀 Loading Chunk {chunk_index} of {len(chunks)}...")
            print(f"{'=' * 40}")

            context = browser.new_context(no_viewport=True)
            open_pages = []

            for session_data in chunk:
                s_url = session_data["url"]
                s_ids = session_data["session_ids"]
                domain = session_data["domain"]

                print(f"  🌐 Opening: {s_url}")
                page = context.new_page()

                try:
                    page.goto(s_url, wait_until="domcontentloaded", timeout=20000)
                    speed_fill_page(page, domain, session_data["profile_id"])
                    open_pages.append((s_ids, page))
                except Exception as e:
                    print(f"  ❌ Failed to load {s_url}: {e}")
                    print(f"  ⚠️ Marking as UNRECOVERABLE to remove from queue.")
                    # BOUNTY FIX: Kill the Zombie URLs in the database
                    with get_db() as db:
                        for s_id in s_ids:
                            session = db.query(FormSession).filter_by(id=s_id).first()
                            if session:
                                session.status = "UNRECOVERABLE"

            print("\n🛑 HANDOFF: Please solve the CAPTCHAs and submit the forms.")
            print("👉 Close a tab when you are finished with it.")

            for s_ids, page in open_pages:
                try:
                    if not page.is_closed():
                        page.wait_for_event("close", timeout=0)

                        # Update all duplicates at once!
                    mark_sessions_resolved(s_ids)
                except Exception:
                    pass

            try:
                context.close()
            except Exception:
                pass

            print(f"✨ Chunk {chunk_index} complete.")
            time.sleep(1)

        print("\n🎉 All chunks processed. Shutting down recovery mode.")
        try:
            browser.close()
        except Exception:
            pass


if __name__ == "__main__":
    run_recovery_mode()