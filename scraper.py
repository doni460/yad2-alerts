import json
import os
import re
import sys
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright

# --- Paths ---
CONFIG_PATH = Path("config.json")
STATE_PATH = Path("seen.json")
DEBUG_HTML = Path("debug_page.html")
DEBUG_SHOT = Path("debug_screenshot.png")

# --- Telegram secrets (from GitHub Actions secrets) ---
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

# Yad2 listing links look like: /realestate/item/<token>
ITEM_HREF_RE = re.compile(r"/realestate/item/([A-Za-z0-9]+)")


def load_config():
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def load_state():
    """Returns a set of seen tokens, or None if this is the first run."""
    if STATE_PATH.exists():
        try:
            return set(json.loads(STATE_PATH.read_text(encoding="utf-8")))
        except Exception:
            return set()
    return None


def save_state(seen):
    STATE_PATH.write_text(
        json.dumps(sorted(seen), ensure_ascii=False, indent=0),
        encoding="utf-8",
    )


def send_telegram(text):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("WARNING: Telegram secrets missing; skipping send.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        resp = requests.post(
            url,
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
                "disable_web_page_preview": False,
            },
            timeout=30,
        )
        if resp.status_code != 200:
            print(f"Telegram error {resp.status_code}: {resp.text}")
    except Exception as e:
        print(f"Telegram request failed: {e}")


def scrape_listings(search_url):
    """Load the search page in a headless browser and return {token: {url, text}}."""
    listings = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        context = browser.new_context(
            locale="he-IL",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1366, "height": 900},
        )
        page = context.new_page()
        page.goto(search_url, wait_until="domcontentloaded", timeout=60000)

        # Wait for listing links to render (SPA fetches them after load)
        try:
            page.wait_for_selector('a[href*="/realestate/item/"]', timeout=30000)
        except Exception:
            print("No listing links appeared within the timeout.")

        page.wait_for_timeout(3000)  # let the rest settle

        # Save diagnostics so we can verify it isn't blocked
        try:
            DEBUG_HTML.write_text(page.content(), encoding="utf-8")
            page.screenshot(path=str(DEBUG_SHOT), full_page=True)
        except Exception as e:
            print(f"Diagnostics save failed: {e}")

        for a in page.query_selector_all('a[href*="/realestate/item/"]'):
            href = a.get_attribute("href") or ""
            m = ITEM_HREF_RE.search(href)
            if not m:
                continue
            token = m.group(1)
            if href.startswith("/"):
                href = "https://www.yad2.co.il" + href
            text = (a.inner_text() or "").strip()
            listings.setdefault(token, {"url": href, "text": text})

        browser.close()
    return listings


def main():
    cfg = load_config()
    urls = cfg.get("search_urls") or (
        [cfg["search_url"]] if cfg.get("search_url") else []
    )
    if not urls:
        print("No search_urls in config.json")
        sys.exit(1)

    all_listings = {}
    for u in urls:
        print(f"Scraping: {u}")
        found = scrape_listings(u)
        print(f"  found {len(found)} listing links")
        all_listings.update(found)

    print(f"FOUND_LISTINGS={len(all_listings)}")

    prev = load_state()

    # First run ever: record everything as a baseline, do NOT spam.
    if prev is None:
        seen = set(all_listings.keys())
        save_state(seen)
        send_telegram(
            "\u2705 \u05d1\u05d5\u05d8 \u05d9\u05d42 \u05e4\u05e2\u05d9\u05dc!\n"
            f"\u05e0\u05e8\u05e9\u05de\u05d5 {len(seen)} \u05de\u05d5\u05d3\u05e2\u05d5\u05ea "
            "\u05e7\u05d9\u05d9\u05de\u05d5\u05ea \u05db\u05d1\u05e1\u05d9\u05e1.\n"
            "\u05de\u05e2\u05db\u05e9\u05d9\u05d5 \u05ea\u05e7\u05d1\u05dc "
            "\u05d4\u05ea\u05e8\u05d0\u05d4 \u05e8\u05e7 \u05e2\u05dc "
            "\u05de\u05d5\u05d3\u05e2\u05d5\u05ea \u05d7\u05d3\u05e9\u05d5\u05ea."
        )
        print("Baseline recorded.")
        return

    # Safety: if the page returned 0 listings (possible block / outage),
    # keep the existing state and send nothing, so we don't lose the baseline.
    if len(all_listings) == 0:
        print("Zero listings found - likely blocked or empty. Keeping state, no alerts.")
        return

    new_tokens = [t for t in all_listings if t not in prev]
    print(f"NEW_LISTINGS={len(new_tokens)}")

    for t in new_tokens:
        item = all_listings[t]
        msg = "\U0001f3e0 \u05de\u05d5\u05d3\u05e2\u05d4 \u05d7\u05d3\u05e9\u05d4 \u05d1\u05d9\u05d42!\n"
        if item["text"]:
            msg += item["text"][:300] + "\n"
        msg += item["url"]
        send_telegram(msg)

    seen = set(prev) | set(all_listings.keys())
    save_state(seen)
    print("State updated.")


if __name__ == "__main__":
    main()
