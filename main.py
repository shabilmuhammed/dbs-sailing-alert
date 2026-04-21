"""DBS Sailing at the Bay – May Registration Monitor.

Watches the main page for the registration button to change from
"April Registration" to "May Registration" (or any May-related text).
Sends a Telegram alert on the first detection.
"""

import logging
import os
import re
import sys
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
URL = "https://www.dbs.com/sailing/index.html"
STATUS_FILE = Path(__file__).with_name("status.txt")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID = os.environ.get("CHAT_ID", "")
TARGET_MONTH = "april"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# Patterns to find the registration button / link
# The page has a red button like "April Registration" or "May Registration"
REGISTRATION_PATTERN = re.compile(
    r"(january|february|march|april|may|june|july|august|september|october|november|december)"
    r"\s*registration",
    re.I,
)

MAX_RETRIES = 2
RETRY_DELAY_SECS = 5

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------


def fetch_page(url: str = URL) -> str | None:
    """Fetch the HTML content of the page with one retry on failure."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            log.info("Fetching %s (attempt %d/%d)", url, attempt, MAX_RETRIES)
            resp = requests.get(url, headers=HEADERS, timeout=30)
            resp.raise_for_status()
            log.info("Fetch successful – HTTP %d, %d bytes", resp.status_code, len(resp.text))
            return resp.text
        except requests.RequestException as exc:
            log.warning("Fetch attempt %d failed: %s", attempt, exc)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY_SECS)
    log.error("All fetch attempts failed.")
    return None


def check_availability(html: str) -> str:
    """Check if the registration button has switched to May.

    Scans all links/buttons and the full page text for a pattern like
    "May Registration". Returns 'MAY_OPEN' if found, 'NOT_YET' otherwise.
    """
    try:
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
    except Exception as exc:
        log.error("HTML parsing failed: %s – defaulting to NOT_YET", exc)
        return "NOT_YET"

    # --- Strategy 1: Look at link / button text (most reliable) ---
    for tag in soup.find_all(["a", "button"]):
        tag_text = tag.get_text(strip=True).lower()
        match = REGISTRATION_PATTERN.search(tag_text)
        if match:
            month = match.group(1).lower()
            log.info("Found registration button: '%s' (month=%s)", tag_text, month)
            if month == TARGET_MONTH:
                log.info("Result: MAY_OPEN – button says May Registration!")
                return "MAY_OPEN"
            else:
                log.info("Button still shows '%s Registration' – not May yet.", month.title())

    # --- Strategy 2: Full page text scan (fallback) ---
    text = soup.get_text(separator=" ", strip=True).lower()
    match = REGISTRATION_PATTERN.search(text)
    if match:
        month = match.group(1).lower()
        log.info("Found registration text in page body: month=%s", month)
        if month == TARGET_MONTH:
            log.info("Result: MAY_OPEN – page text mentions May Registration!")
            return "MAY_OPEN"
        else:
            log.info("Page text shows '%s Registration' – not May yet.", month.title())

    # --- Strategy 3: Raw HTML scan (last resort for hidden/dynamic text) ---
    raw = html.lower()
    if re.search(r"may\s*registration", raw):
        log.info("Result: MAY_OPEN – raw HTML contains 'may registration'!")
        return "MAY_OPEN"

    log.info("Result: NOT_YET – no May registration detected.")
    return "NOT_YET"


def read_state() -> str:
    """Read the persisted state from disk. Returns 'NOT_YET' if missing."""
    try:
        return STATUS_FILE.read_text(encoding="utf-8").strip().upper()
    except FileNotFoundError:
        return "NOT_YET"
    except Exception as exc:
        log.warning("Could not read %s: %s – defaulting to NOT_YET", STATUS_FILE, exc)
        return "NOT_YET"


def write_state(state: str) -> None:
    """Persist the current state to disk."""
    STATUS_FILE.write_text(state + "\n", encoding="utf-8")
    log.info("State written to %s: %s", STATUS_FILE, state)


def send_telegram(message: str) -> bool:
    """Send a message via the Telegram Bot API. Returns True on success."""
    if not TELEGRAM_TOKEN or not CHAT_ID:
        log.error("TELEGRAM_TOKEN or CHAT_ID not set – skipping notification.")
        return False

    api_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }

    try:
        resp = requests.post(api_url, json=payload, timeout=15)
        resp.raise_for_status()
        log.info("Telegram message sent successfully.")
        return True
    except requests.RequestException as exc:
        log.error("Failed to send Telegram message: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------


def main() -> None:
    log.info("=== DBS Sailing Monitor – Watching for May Registration ===")

    html = fetch_page()
    if html is None:
        log.error("Could not fetch page – aborting run (state unchanged).")
        sys.exit(0)  # exit 0 so the GH Actions job stays green

    current = check_availability(html)
    previous = read_state()
    log.info("Previous state: %s | Current state: %s", previous, current)

    if previous != "MAY_OPEN" and current == "MAY_OPEN":
        log.info("May Registration detected – sending alert!")
        send_telegram(
            "\U0001f6a8 DBS Sailing: May Registration is now live!\n"
            "Book now: https://www.dbs.com/sailing/index.html"
        )
    else:
        log.info("No change – still waiting for May Registration.")

    write_state(current)
    log.info("=== DBS Sailing Monitor – Done ===")


if __name__ == "__main__":
    main()
