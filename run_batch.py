"""
run_batch.py — The Unattended Headless Queue Manager
======================================================
"""

import time
import logging
from urllib.parse import urlparse
from ai_agent_test import execute_form_filler_agent
from ai_agent_db import get_db, BlacklistedDomain

# Set up clean logging for the batch runner
logging.basicConfig(level=logging.INFO, format="%(asctime)s [BATCH] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("batch_runner")


def run_queue(url_list: list, profile_id: int = 1):
    total_urls = len(url_list)
    log.info(f"Starting unattended batch processing for {total_urls} URLs...")
    log.info("Running in headless mode. No browsers will be visible.")

    success_count = 0
    fail_count = 0
    captcha_count = 0

    for index, target_url in enumerate(url_list, 1):
        domain = urlparse(target_url).netloc

        # THE BOUNCER: Check the blacklist before doing anything
        with get_db() as db:
            is_banned = db.query(BlacklistedDomain).filter_by(domain=domain).first()
            if is_banned:
                log.warning(f"\n[{index}/{total_urls}] SKIPPING {domain}: Blacklisted ({is_banned.reason})")
                fail_count += 1
                continue

        log.info(f"\n[{index}/{total_urls}] Processing: {target_url}")

        try:
            # 1. Fire the agent in Headless mode
            # 2. Tell it to aggressively submit safe forms
            result = execute_form_filler_agent(
                profile_id=profile_id,
                target_url=target_url,
                headless=True,
                submit_form=True
            )

            # Tally the results for the final report
            status = result.get("status", "FAILED")
            if status == "SUCCESS":
                success_count += 1
            elif status == "CAPTCHA_ERROR":
                captcha_count += 1
            else:
                fail_count += 1

        except Exception as e:
            # The Ultimate Safety Net: If Playwright completely crashes
            log.error(f"Critical failure on {target_url}: {e}")
            fail_count += 1

        # Brief pause between URLs to let the CPU breathe
        time.sleep(2)

    log.info("\n" + "=" * 40)
    log.info("🎯 BATCH PROCESSING COMPLETE")
    log.info("=" * 40)
    log.info(f"Total Processed : {total_urls}")
    log.info(f"Successful      : {success_count}")
    log.info(f"CAPTCHA Traps   : {captcha_count} (Ready for run_recovery.py)")
    log.info(f"Errors/Failed   : {fail_count}")


if __name__ == "__main__":
    # For testing, we use a small hardcoded list.
    test_queue = [
        "https://ultimateqa.com/filling-out-forms/",
        "https://practice-automation.com/form-fields/",
        "https://demoqa.com/automation-practice-form",
        "https://compendiumdev.co.uk/selenium/testpages/html5_form_test.html",
        "https://the-internet.herokuapp.com/login"
    ]

    run_queue(test_queue)