import os
import re
import math
import time
import random
import logging
import base64
from datetime import datetime, timezone
from urllib.parse import urlparse
from typing import Optional

from dotenv import load_dotenv, find_dotenv
from playwright.sync_api import sync_playwright, Page, Frame, ElementHandle
from playwright_stealth import Stealth

from ai_agent_db import (
    get_db,
    AnonymousProfile,
    FormSession,
)
from ai_agent_brain import ask_brain_to_map_fields

load_dotenv(find_dotenv())

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("agent")

PROXY_SERVER = os.getenv("PROXY_SERVER")
PROXY_USERNAME = os.getenv("PROXY_USERNAME")
PROXY_PASSWORD = os.getenv("PROXY_PASSWORD")

SCREENSHOT_DIR = os.path.join(os.path.dirname(__file__), "screenshots")
os.makedirs(SCREENSHOT_DIR, exist_ok=True)


class HumanSimulator:
    def __init__(self, page: Page):
        self.page = page
        self._last_mouse_x: float = 400.0
        self._last_mouse_y: float = 300.0

    def gaussian_type(self, element: ElementHandle, text: str) -> None:
        element.click(timeout=2000)
        element.fill("")
        for char in text:
            delay_ms = max(28, min(140, random.gauss(65, 22)))
            if random.random() < 0.05:
                time.sleep(random.uniform(0.30, 0.70))
            self.page.keyboard.type(char, delay=delay_ms)

    @staticmethod
    def _cubic_bezier_points(p0, p1, p2, p3, steps):
        points = []
        for i in range(steps + 1):
            t = i / steps
            mt = 1 - t
            x = (mt ** 3) * p0[0] + 3 * (mt ** 2) * t * p1[0] + 3 * mt * (t ** 2) * p2[0] + (t ** 3) * p3[0]
            y = (mt ** 3) * p0[1] + 3 * (mt ** 2) * t * p1[1] + 3 * mt * (t ** 2) * p2[1] + (t ** 3) * p3[1]
            points.append((x, y))
        return points

    def bezier_move_to(self, target_x: float, target_y: float) -> None:
        start_x, start_y = self._last_mouse_x, self._last_mouse_y
        cp1 = (start_x + random.uniform(-80, 120), start_y + random.uniform(-60, 60))
        cp2 = (target_x + random.uniform(-80, 80), target_y + random.uniform(-60, 60))

        path = self._cubic_bezier_points((start_x, start_y), cp1, cp2, (target_x, target_y), 35)
        for px, py in path:
            self.page.mouse.move(px + random.gauss(0, 0.4), py + random.gauss(0, 0.4))
            time.sleep(random.uniform(0.003, 0.012))

        if random.random() < 0.25:
            overshoot = random.uniform(4, 12)
            angle = random.uniform(0, 2 * math.pi)
            self.page.mouse.move(target_x + overshoot * math.cos(angle), target_y + overshoot * math.sin(angle))
            time.sleep(random.uniform(0.04, 0.10))
            self.page.mouse.move(target_x, target_y)
            time.sleep(random.uniform(0.02, 0.06))

        self._last_mouse_x = target_x
        self._last_mouse_y = target_y

    def human_click(self, element: ElementHandle, timeout: int = 2000) -> None:
        try:
            box = element.bounding_box()
            if box:
                cx = box["x"] + box["width"] * random.uniform(0.30, 0.70)
                cy = box["y"] + box["height"] * random.uniform(0.30, 0.70)
                self.bezier_move_to(cx, cy)
                time.sleep(random.uniform(0.05, 0.15))
                self.page.mouse.click(cx, cy)
                return
        except Exception:
            pass
        element.click(timeout=timeout)

    def simulate_page_read(self) -> None:
        try:
            total_scrolled = 0
            page_height = self.page.evaluate("document.body.scrollHeight") or 2000
            while total_scrolled < page_height * 0.6:
                scroll_amount = random.randint(180, 420)
                self.page.mouse.wheel(0, scroll_amount)
                total_scrolled += scroll_amount
                time.sleep(random.uniform(0.25, 0.75))
            self.page.evaluate("window.scrollTo({top: 0, behavior: 'smooth'})")
            time.sleep(random.uniform(0.8, 1.5))
        except Exception:
            pass

    def inter_field_pause(self) -> None:
        time.sleep(random.uniform(0.8, 1.8))


def visual_fallback_scraper(page: Page) -> list[dict]:
    screenshot_path = os.path.join(SCREENSHOT_DIR, f"vlm_input_{int(time.time())}.png")
    page.screenshot(path=screenshot_path, full_page=True)
    with open(screenshot_path, "rb") as f:
        b64_image = base64.standard_b64encode(f.read()).decode("utf-8")
    log.warning("VLM fallback scraper is a stub. Implementation required.")
    raise NotImplementedError("VLM fallback not wired to an API.")


def extract_all_form_elements(page: Page, frame: Frame) -> tuple[list[dict], dict]:
    elements = frame.query_selector_all("input, textarea, select, iframe[src*='captcha'], div[class*='captcha']")
    scraped = []
    el_map = {}

    for element in elements:
        try:
            if not element.is_visible():
                continue

            # Assign a unique ID to the parent form so we can group fields safely
            form_id = element.evaluate("""el => {
                let f = el.closest('form');
                if (!f) return 'global';
                if (!f.dataset.agentId) f.dataset.agentId = 'form_' + Math.random().toString(36).substr(2, 9);
                return f.dataset.agentId;
            }""")

            tag_name = element.evaluate("el => el.tagName.toLowerCase()")
            field_type = element.get_attribute("type") or "text"
            placeholder = element.get_attribute("placeholder") or ""
            name_attr = element.get_attribute("name") or ""
            element_id = element.get_attribute("id") or ""
            class_attr = element.get_attribute("class") or ""
            src_attr = element.get_attribute("src") or ""

            if "captcha" in class_attr.lower() or "captcha" in src_attr.lower() or tag_name == "iframe":
                scraped.append({"placeholder": "SYSTEM_CAPTCHA", "type": "captcha", "form_id": form_id})
                el_map["SYSTEM_CAPTCHA"] = element
                continue

            label_text = ""
            if element_id:
                label_el = frame.query_selector(f"label[for='{element_id}']")
                if label_el:
                    label_text = label_el.inner_text().strip()

            if not label_text:
                label_text = element.evaluate("""el => {
                    let p = el.previousSibling;
                    if (p && p.nodeType === 3) return p.textContent.trim();
                    let n = el.nextSibling;
                    if (n && n.nodeType === 3) return n.textContent.trim();
                    return '';
                }""")

            display_name = (placeholder or label_text or name_attr or element_id).strip()

            if not display_name or field_type in ("submit", "hidden", "file", "button", "image") or len(
                    display_name) > 80:
                continue

            if tag_name == "select":
                opts = [opt.inner_text().strip() for opt in element.query_selector_all("option") if
                        opt.inner_text().strip()]
                scraped.append({"placeholder": display_name, "type": "dropdown", "options": opts, "form_id": form_id})
            elif field_type == "radio":
                scraped.append({"placeholder": display_name, "type": "radio", "group": name_attr, "form_id": form_id})
            else:
                scraped.append({"placeholder": display_name, "type": field_type, "form_id": form_id})

            key = f"{display_name}_{field_type}_{name_attr}_{element_id}"
            el_map[key] = element

        except Exception:
            continue

    return scraped, el_map


def _fill_single_field(element: ElementHandle, field_meta: dict, value: str, placeholder: str,
                       human: HumanSimulator) -> bool:
    field_type = field_meta.get("type", "text")

    try:
        if element.is_disabled():
            log.warning(f"Skipped '{placeholder}': element is disabled.")
            return False

        if field_type == "dropdown":
            log.info(f"Selecting dropdown '{placeholder}' -> '{value}'")
            try:
                element.select_option(label=value, timeout=2500)
            except Exception:
                fallback = next((o for o in field_meta.get("options", []) if o.strip()), None)
                if fallback:
                    log.warning(f"Dropdown fallback applied for '{placeholder}' -> '{fallback}'")
                    element.select_option(label=fallback, timeout=2500)
                else:
                    return False

        elif field_type in ("checkbox", "radio"):
            should_check = value.lower() in ("true", "yes", "on")
            log.info(f"Toggling '{placeholder}' -> {should_check}")
            if should_check != element.is_checked():
                element.evaluate("el => el.click()")
                human.page.wait_for_timeout(400)

        else:
            display_val = f"{value[:40]}..." if len(value) > 40 else value
            log.info(f"Filling '{placeholder}' -> '{display_val}'")
            try:
                element.scroll_into_view_if_needed(timeout=1000)
                human.human_click(element)
                human.gaussian_type(element, value)
            except Exception:
                element.click(force=True)
                element.fill("")
                element.type(value, delay=40)

            try:
                human.page.keyboard.press("Escape")
                time.sleep(0.1)
                element.evaluate("el => el.blur()")
                time.sleep(0.1)
                human.page.evaluate("document.body.click()")
                human.page.wait_for_timeout(600)
            except Exception:
                pass

        return True

    except Exception as e:
        log.warning(f"Skipped '{placeholder}': {str(e).splitlines()[0]}")
        return False


def execute_form_filler_agent(profile_id: int, target_url: str, headless: bool = False,
                              submit_form: bool = False) -> dict:
    domain = urlparse(target_url).netloc
    form_session = FormSession(profile_id=profile_id, target_url=target_url, domain=domain, status="PENDING")

    with get_db() as db:
        db.add(form_session)
        db.flush()
        session_id = form_session.id

    log.info(f"Starting Session #{session_id} for profile {profile_id} on {domain}")

    with get_db() as db:
        profile = db.query(AnonymousProfile).filter_by(id=profile_id).first()
        if not profile:
            _update_session(session_id, status="FAILED", error_detail="Profile not found.")
            return _build_result(session_id, "FAILED", error_detail="Profile not found.")
        profile_data = profile.to_dynamic_dict()

    fields_found, fields_filled, fields_skipped = 0, 0, 0
    screenshot_path = None

    launch_kwargs = {
        "headless": headless,
        "args": ["--start-maximized", "--disable-blink-features=AutomationControlled", "--no-default-browser-check"],
    }
    context_kwargs = {"no_viewport": True, "locale": "en-US", "accept_downloads": False}

    if PROXY_SERVER:
        context_kwargs["proxy"] = {"server": PROXY_SERVER}
        if PROXY_USERNAME and PROXY_PASSWORD:
            context_kwargs["proxy"].update({"username": PROXY_USERNAME, "password": PROXY_PASSWORD})

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(**launch_kwargs)
            context = browser.new_context(**context_kwargs)
            page = context.new_page()
            human = HumanSimulator(page)

            Stealth().apply_stealth_sync(page)
            log.info(f"Navigating to {target_url}")

            try:
                page.goto(target_url, wait_until="domcontentloaded", timeout=20000)
            except Exception:
                log.warning("Navigation soft-timeout. Proceeding with loaded DOM.")

            human.simulate_page_read()

            scraped_fields = []
            element_map = {}

            for frame in [page.main_frame] + page.frames:
                try:
                    fields, el_map = extract_all_form_elements(page, frame)
                    scraped_fields.extend(fields)
                    element_map.update(el_map)
                except Exception:
                    continue

            if not scraped_fields:
                log.warning("DOM scraper empty. Attempting VLM fallback.")
                try:
                    scraped_fields.extend(visual_fallback_scraper(page))
                except NotImplementedError:
                    _update_session(session_id, status="FAILED", error_detail="No fields found.")
                    browser.close()
                    return _build_result(session_id, "FAILED", error_detail="No fields found.")

            # Form-Awareness Logic integration
            has_captcha = any(f["type"] == "captcha" for f in scraped_fields)
            captcha_forms = {f["form_id"] for f in scraped_fields if f["type"] == "captcha"}
            fields_to_map = [f for f in scraped_fields if f["type"] != "captcha"]

            # Deduplicate fields using form_id
            seen = set()
            deduped = []
            for f in fields_to_map:
                uid = f"{f['placeholder']}_{f['type']}_{f.get('form_id', 'global')}"
                if uid not in seen:
                    seen.add(uid)
                    deduped.append(f)

            fields_found = len(deduped)
            log.info(f"Discovered {fields_found} valid fields.")

            decision_matrix = ask_brain_to_map_fields(domain, deduped, list(profile_data.keys()))

            log.info("Beginning field interaction loop.")
            filled_forms = set()

            for placeholder, decision in decision_matrix.items():
                if not decision or decision in ("None", "null"):
                    fields_skipped += 1
                    continue

                target_key = next((k for k in element_map if k.startswith(f"{placeholder}_")), None)
                if not target_key:
                    fields_skipped += 1
                    continue

                element = element_map[target_key]
                field_meta = next((f for f in scraped_fields if f["placeholder"] == placeholder), {})

                if str(decision).startswith("GENERATED_TEXT:"):
                    value = decision.replace("GENERATED_TEXT:", "").strip()
                else:
                    value = str(profile_data.get(decision, ""))

                if not value:
                    log.warning(f"Skipped '{placeholder}': No matching value in database.")
                    fields_skipped += 1
                    continue

                if _fill_single_field(element, field_meta, value, placeholder, human):
                    fields_filled += 1
                    filled_forms.add(field_meta.get("form_id", "global"))
                else:
                    fields_skipped += 1

                human.inter_field_pause()

            if has_captcha:
                log.warning("CAPTCHA detected. Pausing for manual intervention if configured.")
                manual_wait = int(os.getenv("MANUAL_CAPTCHA_WAIT_SECS", "0"))
                for remaining in range(manual_wait, 0, -1):
                    time.sleep(1)

            screenshot_path = os.path.join(SCREENSHOT_DIR, f"session_{session_id}_final.png")
            page.screenshot(path=screenshot_path, full_page=False)

            # Form-Scoped Submission Logic
            final_status = "PARTIAL" if fields_skipped > 0 else "SUCCESS"

            if submit_form:
                submitted = False
                for fid in filled_forms:
                    if fid in captcha_forms:
                        log.warning(f"Skipping submission for form '{fid}' (CAPTCHA detected).")
                        continue

                    try:
                        if fid == "global":
                            btn = page.query_selector("button[type='submit'], input[type='submit']")
                        else:
                            btn = page.query_selector(
                                f"form[data-agent-id='{fid}'] button[type='submit'], form[data-agent-id='{fid}'] input[type='submit']")

                        if btn:
                            human.human_click(btn)
                            page.wait_for_load_state("networkidle", timeout=10000)
                            submitted = True
                            log.info("Successfully submitted safe form.")
                            break
                    except Exception as e:
                        log.warning(f"Failed to click submit: {e}")

                if submitted:
                    final_status = "SUCCESS"
                elif any(fid in captcha_forms for fid in filled_forms):
                    final_status = "CAPTCHA_ERROR"
                else:
                    final_status = "PARTIAL"

            browser.close()

        _update_session(session_id, status=final_status, fields_found=fields_found, fields_filled=fields_filled,
                        fields_skipped=fields_skipped, screenshot_path=screenshot_path)
        return _build_result(session_id, final_status, fields_found=fields_found, fields_filled=fields_filled,
                             fields_skipped=fields_skipped, screenshot_path=screenshot_path)

    except Exception as exc:
        log.error(f"Unrecoverable error in session {session_id}: {exc}")
        _update_session(session_id, status="FAILED", error_detail=str(exc))
        return _build_result(session_id, "FAILED", error_detail=str(exc))


def _update_session(session_id: int, **kwargs) -> None:
    with get_db() as db:
        record = db.query(FormSession).filter_by(id=session_id).first()
        if record:
            for k, v in kwargs.items():
                setattr(record, k, v)
            record.completed_at = datetime.now(timezone.utc)


def _build_result(session_id: int, status: str, fields_found=0, fields_filled=0, fields_skipped=0, screenshot_path=None,
                  error_detail=None) -> dict:
    return {
        "session_id": session_id,
        "status": status,
        "fields_found": fields_found,
        "fields_filled": fields_filled,
        "fields_skipped": fields_skipped,
        "screenshot_path": screenshot_path,
        "error_detail": error_detail,
    }


if __name__ == "__main__":
    user_url = input("Enter target URL: ").strip()
    if user_url:
        result = execute_form_filler_agent(profile_id=1, target_url=user_url, headless=False, submit_form=True)
        print("\nFinal Status:", result)