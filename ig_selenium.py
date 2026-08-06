#!/usr/bin/env python3
"""
ig_selenium.py — Instagram automation via real Chrome browser (Selenium)
Replaces the raw `requests` private-API calls from the Termux build.

WHY SELENIUM ON DESKTOP:
  Instagram's private API (the endpoints the old script hit directly with
  `requests`) is heavily fingerprinted and easy to get flagged/rate-limited
  from a script that never renders a page. A real Chrome instance driven by
  Selenium looks like an actual logged-in browser session — same cookies,
  same JS execution, same network fingerprint a human has — so it's far
  more durable on a PC where there's no need to fake a mobile user-agent.

CAVEAT (be honest with yourself about this):
  Instagram's DOM uses obfuscated class names that rotate. This module
  prefers aria-label / role / svg-title selectors because those are the
  most stable ones IG has, but they WILL occasionally break when IG ships
  a redesign. Run with SELENIUM_DEBUG=1 to dump a screenshot + page source
  snippet to ~/cortana_debug/ whenever a selector lookup fails, so you can
  patch it fast instead of guessing blind.

Install:
    pip install selenium --break-system-packages
    sudo apt install chromium-chromedriver     # or match your Chrome version
"""

import os
import readline  # noqa: F401 — see ig_manager.py for why
import json
import time
import random
from datetime import datetime, timedelta
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    TimeoutException, NoSuchElementException, ElementClickInterceptedException,
    StaleElementReferenceException,
)

try:
    import requests as _requests
except ImportError:
    _requests = None

HOME = os.path.expanduser("~")
COOKIE_FILE = os.path.join(HOME, "ig_cookies.json")
DEBUG_DIR = os.path.join(HOME, "cortana_debug")
DEBUG = os.environ.get("SELENIUM_DEBUG", "0") == "1"
ID_CACHE_FILE = os.path.join(HOME, "python", "id_cache.json")

IG_BASE = "https://www.instagram.com"

R="\033[0m"; DIM="\033[2m"
GRN="\033[92m"; RED="\033[91m"; YLW="\033[93m"

# Headers for the private API calls used ONLY for the unfollow-by-id step
# (see unfollow_batch). Ported from hunter15.py. We deliberately do NOT use
# raw requests for login or browsing — only for this one POST, riding on
# cookies from an already-authenticated, already-rendering browser session,
# to keep the fingerprint risk this module was built to avoid as low as
# possible while still being reliable.
_API_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Linux; Android 11; Pixel 5) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36"),
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "X-IG-App-ID": "936619743392459",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": "https://www.instagram.com/",
    "Origin": "https://www.instagram.com",
}


# ══════════════════════════════════════════════════════════
# DRIVER SETUP
# ══════════════════════════════════════════════════════════
def _binary_location():
    """Find whichever Chromium-based browser is actually installed.
    Linux Mint blocks the snap-based chromium-browser package on purpose,
    so we check for real Google Chrome and Brave (both work fine with
    Selenium's Chrome driver since they're both Chromium under the hood)."""
    for path in (
        "/usr/bin/google-chrome", "/usr/bin/google-chrome-stable",
        "/usr/bin/brave-browser", "/usr/bin/chromium", "/usr/bin/chromium-browser",
    ):
        if os.path.exists(path):
            return path
    return None


def make_driver(headless=False):
    opts = Options()
    if headless:
        opts.add_argument("--headless=new")
    opts.add_argument("--start-maximized")
    opts.add_argument("--disable-notifications")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)

    binary = _binary_location()
    if binary:
        opts.binary_location = binary

    # Auto-fetch a chromedriver that matches whichever browser is installed
    # (Google Chrome or Brave) instead of relying on apt, which Mint blocks
    # for the chromium-chromedriver package. Falls back to PATH if
    # webdriver-manager isn't installed.
    driver = None
    try:
        from webdriver_manager.chrome import ChromeDriverManager
        from selenium.webdriver.chrome.service import Service
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=opts)
    except ImportError:
        print(" (tip: pip install webdriver-manager --break-system-packages"
              " to stop having to manage chromedriver by hand)")
        driver = webdriver.Chrome(options=opts)
    except Exception as e:
        print(f" webdriver-manager failed ({e}), falling back to PATH chromedriver...")
        driver = webdriver.Chrome(options=opts)

    driver.execute_cdp_cmd(
        "Page.addScriptToEvaluateOnNewDocument",
        {"source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"},
    )
    return driver


def _debug_dump(driver, tag):
    if not DEBUG:
        return
    os.makedirs(DEBUG_DIR, exist_ok=True)
    ts = datetime.now().strftime("%H%M%S")
    try:
        driver.save_screenshot(os.path.join(DEBUG_DIR, f"{tag}_{ts}.png"))
        with open(os.path.join(DEBUG_DIR, f"{tag}_{ts}.html"), "w") as f:
            f.write(driver.page_source[:20000])
    except Exception:
        pass


def _dismiss_popups(driver):
    """Click away 'Save login info' / 'Turn on notifications' dialogs."""
    for label in ("Not Now", "Not now", "Cancel"):
        try:
            btn = WebDriverWait(driver, 3).until(
                EC.element_to_be_clickable((By.XPATH, f"//button[text()='{label}']"))
            )
            btn.click()
            time.sleep(1)
        except TimeoutException:
            pass


# ══════════════════════════════════════════════════════════
# LOGIN — cookie persistence so you don't 2FA every run
# ══════════════════════════════════════════════════════════
def load_cookies(driver):
    if not os.path.exists(COOKIE_FILE):
        return False
    driver.get(IG_BASE)
    time.sleep(2)
    with open(COOKIE_FILE) as f:
        cookies = json.load(f)
    for c in cookies:
        c.pop("sameSite", None)
        try:
            driver.add_cookie(c)
        except Exception:
            pass
    driver.get(f"{IG_BASE}/")
    time.sleep(2)
    return is_logged_in(driver)


def save_cookies(driver):
    with open(COOKIE_FILE, "w") as f:
        json.dump(driver.get_cookies(), f)
    os.chmod(COOKIE_FILE, 0o600)


def clean_username(raw):
    """Strip anything that isn't a valid Instagram username character.

    Terminal input can occasionally leak raw control/escape bytes into a
    typed string — e.g. pressing an arrow key sends ESC[A/B/C/D, and if the
    terminal isn't fully absorbing that into line-editing, those bytes ride
    along invisibly. .strip() only trims whitespace, so this catches the
    rest. Instagram usernames are letters, digits, periods, underscores
    only, max 30 chars.
    """
    import re
    return re.sub(r"[^A-Za-z0-9_.]", "", raw).strip(".")[:30].lower()


def is_logged_in(driver):
    try:
        driver.find_element(By.XPATH, "//a[@href='/direct/inbox/']")
        return True
    except NoSuchElementException:
        try:
            driver.find_element(By.XPATH, "//*[@aria-label='Home']")
            return True
        except NoSuchElementException:
            return False


def login(username=None, password=None, headless=False):
    """
    Returns a logged-in driver, or None on failure.
    Tries saved cookies first; falls back to interactive login (asks for
    2FA in the terminal same as the old version did).
    """
    driver = make_driver(headless=headless)
    if load_cookies(driver):
        print(" ✓ Restored Instagram session from cookies.")
        return driver

    if not username:
        username = input(" Instagram username: ").strip()
    if not password:
        password = __import__("getpass").getpass(" Instagram password: ")

    driver.get(f"{IG_BASE}/accounts/login/")
    wait = WebDriverWait(driver, 15)
    try:
        user_field = wait.until(EC.presence_of_element_located((By.NAME, "username")))
        pass_field = driver.find_element(By.NAME, "password")
        user_field.clear(); user_field.send_keys(username)
        pass_field.clear(); pass_field.send_keys(password)
        pass_field.send_keys(Keys.RETURN)
    except TimeoutException:
        _debug_dump(driver, "login_form_not_found")
        print(" Could not find login form — Instagram may have changed the page.")
        return None

    time.sleep(4)

    # 2FA prompt
    try:
        code_field = WebDriverWait(driver, 5).until(
            EC.presence_of_element_located((By.NAME, "verificationCode"))
        )
        code = input(" 2FA code: ").strip()
        code_field.send_keys(code)
        code_field.send_keys(Keys.RETURN)
        time.sleep(4)
    except TimeoutException:
        pass

    _dismiss_popups(driver)

    if is_logged_in(driver):
        save_cookies(driver)
        print(" ✓ Logged in and cookies saved.")
        return driver

    _debug_dump(driver, "login_failed")
    print(" Login failed — check credentials, or Instagram is showing a challenge.")
    print(" If a 'suspicious login' / captcha page opened, solve it manually")
    print(" in the browser window, then press Enter here to retry the check.")
    input(" Press Enter once resolved (or Ctrl+C to abort)... ")
    if is_logged_in(driver):
        save_cookies(driver)
        return driver
    return None


# ══════════════════════════════════════════════════════════
# SHARED — opening the followers/following dialog reliably
# ══════════════════════════════════════════════════════════
def _find_scrollable(driver, dialog_xpath="//div[@role='dialog']"):
    """Find the real scrollable list container inside the dialog via the
    DOM itself (scrollHeight > clientHeight) instead of guessing at an
    inline style string, which breaks the instant IG changes markup."""
    try:
        dialog = driver.find_element(By.XPATH, dialog_xpath)
    except NoSuchElementException:
        return None
    try:
        candidates = driver.execute_script(
            "return Array.from(arguments[0].querySelectorAll('*')).filter("
            "  el => el.scrollHeight > el.clientHeight + 20 && el.clientHeight > 100"
            ");",
            dialog,
        )
    except Exception:
        return None
    return candidates[0] if candidates else None


def _find_stat_link(driver, username, what):
    """Try several selector strategies for the 'X following'/'X followers'
    stat, most-specific first — IG's own-profile stat elements aren't
    reliably plain <a href> across accounts/rollouts."""
    candidates = [
        (By.XPATH, f"//a[contains(@href,'/{username}/{what}/')]"),
        (By.XPATH, f"//*[@role='link'][contains(@href,'/{username}/{what}/')]"),
        (By.XPATH, f"//a[contains(@href,'/{what}/')]"),
        (By.XPATH, f"//header//*[contains(text(),'{what}')]/ancestor-or-self::*[@role='link' or self::a][1]"),
    ]
    for by, xpath in candidates:
        try:
            el = WebDriverWait(driver, 4).until(EC.presence_of_element_located((by, xpath)))
            return el
        except TimeoutException:
            continue
    return None


def open_profile_list_dialog(driver, username, what):
    """Open the followers/following modal for `username`. Tries a cold
    direct nav first (fast path); if IG doesn't actually render the modal
    from that (common — it's SPA-routed), falls back to loading the profile
    and clicking the real stat link, which triggers the client-side router.
    Returns True/False.
    """
    driver.get(f"{IG_BASE}/{username}/{what}/")
    _dismiss_popups(driver)
    try:
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.XPATH, "//div[@role='dialog']"))
        )
        return True
    except TimeoutException:
        pass

    # fallback: load profile like a human, click the stat link
    driver.get(f"{IG_BASE}/{username}/")
    _dismiss_popups(driver)
    time.sleep(1.5)
    link = _find_stat_link(driver, username, what)
    if link is None:
        _debug_dump(driver, f"{what}_dialog_missing")
        return False
    try:
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", link)
        time.sleep(0.3)
        driver.execute_script("arguments[0].click();", link)
        WebDriverWait(driver, 8).until(
            EC.presence_of_element_located((By.XPATH, "//div[@role='dialog']"))
        )
        return True
    except TimeoutException:
        _debug_dump(driver, f"{what}_dialog_missing")
        return False


# ══════════════════════════════════════════════════════════
# UNFOLLOW
# ══════════════════════════════════════════════════════════
def _api_session_from_driver(driver):
    """Build a requests.Session() authenticated with the same cookies the
    already-logged-in Selenium browser is using. Returns (session, user_id)
    or (None, None) if requests isn't installed or a required cookie is
    missing (falls back to DOM clicking in that case)."""
    if _requests is None:
        return None, None
    cookies = {c["name"]: c["value"] for c in driver.get_cookies()}
    sessionid = cookies.get("sessionid")
    csrftoken = cookies.get("csrftoken")
    user_id = cookies.get("ds_user_id")
    if not (sessionid and csrftoken and user_id):
        return None, None
    s = _requests.Session()
    s.headers.update(_API_HEADERS)
    s.headers["X-CSRFToken"] = csrftoken
    s.cookies.set("sessionid", sessionid, domain=".instagram.com")
    s.cookies.set("csrftoken", csrftoken, domain=".instagram.com")
    s.cookies.set("ds_user_id", user_id, domain=".instagram.com")
    return s, user_id


def _fetch_following_ids(session, user_id, targets_set, cache_path=ID_CACHE_FILE):
    """Bulk-fetch numeric user IDs for accounts you follow, ~200 per page,
    instead of a per-target DOM search. Ported from hunter15.py. Caches to
    disk so repeat runs don't re-fetch people we already have an ID for."""
    cached = {}
    if os.path.exists(cache_path):
        try:
            cached = json.load(open(cache_path))
        except Exception:
            cached = {}
    already = {u: cached[u] for u in targets_set if u in cached}
    need = targets_set - set(already)
    if not need:
        print(f" All {len(already)} target IDs already cached.")
        return already

    id_map = dict(cached)
    next_page = None
    page = 1
    while True:
        url = f"{IG_BASE}/api/v1/friendships/{user_id}/following/?count=200"
        if next_page:
            url += f"&max_id={next_page}"
        try:
            r = session.get(url, timeout=20)
        except Exception as e:
            print(f" ⚠ network error fetching following page {page}: {e}")
            break
        if r.status_code == 429:
            print(" ⚠ rate limited fetching following list — waiting 60s...")
            time.sleep(60)
            continue
        if r.status_code != 200:
            print(f" ⚠ unexpected status {r.status_code} fetching following page {page} — "
                  f"falling back to DOM clicking for remaining accounts.")
            break
        try:
            data = r.json()
            users = data.get("users", [])
        except Exception:
            break
        for u in users:
            uname = (u.get("username") or "").lower()
            uid = str(u.get("pk") or u.get("id") or "")
            if uname and uid:
                id_map[uname] = uid
        print(f" page {page}: {len(users)} accounts scanned "
              f"({sum(1 for u in targets_set if u in id_map)}/{len(targets_set)} targets found so far)")
        json.dump(id_map, open(cache_path, "w"))
        next_page = data.get("next_max_id")
        if not next_page or not users:
            break
        page += 1
        time.sleep(random.uniform(3, 6))  # polite pause between pages

    return {u: id_map[u] for u in targets_set if u in id_map}


def _unfollow_by_id(session, user_id):
    try:
        r = session.post(
            f"{IG_BASE}/api/v1/friendships/destroy/{user_id}/",
            data={"user_id": user_id}, timeout=15,
        )
        if r.status_code == 429:
            return "rate_limited"
        return "ok" if r.status_code == 200 else "error"
    except Exception:
        return "error"


def unfollow_batch(driver, my_username, targets, max_batch=20, log_path="unfollow_log.json"):
    """
    Unfollows the given usernames. Prefers Instagram's private API (fast,
    ID-based, no DOM guessing) using cookies from the already-logged-in
    browser; falls back to clicking through the following dialog only if
    an API session can't be built.
    """
    log = []
    if os.path.exists(log_path):
        try:
            log = json.load(open(log_path))
        except Exception:
            log = []

    if not my_username:
        print(" No username given — can't open a following list for nobody.")
        return 0, 0, len(targets)

    batch = targets[:max_batch]

    # ── Preferred: API-based unfollow ───────────────────────────────────
    session, user_id = _api_session_from_driver(driver)
    if session is not None:
        print(" Using API-based unfollow (reusing your browser's login cookies)...")
        targets_set = set(batch)
        id_map = _fetch_following_ids(session, user_id, targets_set)
        done = skipped = errors = 0
        total = len(id_map)
        i = 0
        for username in batch:
            uid = id_map.get(username)
            if not uid:
                print(f" ⚠ @{username} — not in your following list (already gone)")
                skipped += 1
                log.append({"username": username, "status": "not_found",
                            "timestamp": datetime.now().isoformat()})
                json.dump(log, open(log_path, "w"), indent=2)
                continue
            i += 1
            result = _unfollow_by_id(session, uid)
            if result == "ok":
                print(f" ✔ [{i}/{total}] @{username}")
                done += 1
                log.append({"username": username, "uid": uid, "status": "unfollowed",
                            "timestamp": datetime.now().isoformat()})
            elif result == "rate_limited":
                print(f" ⚠ @{username} — rate limited, backing off 90s")
                log.append({"username": username, "status": "rate_limited",
                            "timestamp": datetime.now().isoformat()})
                json.dump(log, open(log_path, "w"), indent=2)
                time.sleep(90)
                continue
            else:
                print(f" ✗ @{username} — API error")
                errors += 1
                log.append({"username": username, "uid": uid, "status": "error",
                            "timestamp": datetime.now().isoformat()})
            json.dump(log, open(log_path, "w"), indent=2)
            time.sleep(random.uniform(25, 55))  # keep it human-paced regardless of API speed
        return done, skipped, errors

    # ── Fallback: click through the following dialog ────────────────────
    print(" Could not build an API session from cookies (requests not installed, "
          "or a cookie was missing) — falling back to clicking through the UI.")
    if not open_profile_list_dialog(driver, my_username, "following"):
        print(f" Could not open the following list for @{my_username}.")
        print(" Check: is that the right username? Is the account private")
        print(" and you're not logged in as it? Run with SELENIUM_DEBUG=1")
        print(" and check ~/cortana_debug/ for a screenshot of what loaded.")
        return 0, 0, len(targets)

    time.sleep(2)
    done = skipped = errors = 0
    batch = targets[:max_batch]
    print(f"\n Unfollowing {len(batch)} accounts...\n")

    for username in batch:
        try:
            search_box = WebDriverWait(driver, 8).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "input[placeholder='Search']"))
            )
            search_box.clear()
            search_box.send_keys(username)
            time.sleep(1.5)

            row_xpath = f"//a[contains(@href, '/{username}/')]"
            try:
                row_link = WebDriverWait(driver, 6).until(
                    EC.presence_of_element_located((By.XPATH, row_xpath))
                )
            except TimeoutException:
                # genuinely not in the search results — already unfollowed,
                # deactivated, or IG's search just missed it. Real skip.
                print(f" ⚠ @{username} — not in search results, skipping")
                skipped += 1
                log.append({"username": username, "status": "not_found",
                            "timestamp": datetime.now().isoformat()})
                json.dump(log, open(log_path, "w"), indent=2)
                time.sleep(random.uniform(25, 55))
                continue

            # Walk up to the row's <li> — every follower/following dialog
            # row is a list item, which is far more stable than guessing at
            # an inline style string on some intermediate div.
            try:
                row_li = row_link.find_element(By.XPATH, "./ancestor::li[1]")
            except NoSuchElementException:
                row_li = row_link.find_element(By.XPATH, "./ancestor::div[.//button][1]")

            unfollow_btn = None
            for btn_xpath in (
                ".//button[text()='Following' or text()='Requested']",
                ".//button[contains(text(),'Following') or contains(text(),'Requested')]",
                ".//button",  # last resort — whatever single button is in this row
            ):
                try:
                    unfollow_btn = row_li.find_element(By.XPATH, btn_xpath)
                    break
                except NoSuchElementException:
                    continue

            if unfollow_btn is None:
                # row is visibly there but no button matched any strategy —
                # this is a real markup-mismatch bug, not a "gone" account.
                # Flag it distinctly and dump evidence instead of hiding it
                # under the same bucket as accounts that are actually gone.
                print(f" ✗ @{username} — row found but no follow-button matched (markup changed?)")
                errors += 1
                log.append({"username": username, "status": "button_not_found",
                            "timestamp": datetime.now().isoformat()})
                _debug_dump(driver, f"unfollow_button_missing_{username}")
                json.dump(log, open(log_path, "w"), indent=2)
                time.sleep(random.uniform(25, 55))
                continue

            unfollow_btn.click()
            time.sleep(1)
            confirm = WebDriverWait(driver, 5).until(
                EC.element_to_be_clickable((
                    By.XPATH,
                    "//button[text()='Unfollow' or contains(text(),'Unfollow')]",
                ))
            )
            confirm.click()
            print(f" ✔ @{username}")
            done += 1
            log.append({"username": username, "status": "unfollowed",
                        "timestamp": datetime.now().isoformat()})
        except StaleElementReferenceException:
            print(f" ⚠ @{username} — page changed mid-click, will retry next run")
            skipped += 1
            log.append({"username": username, "status": "stale",
                        "timestamp": datetime.now().isoformat()})
        except ElementClickInterceptedException:
            print(f" ✗ @{username} — click blocked, skipping")
            errors += 1
        except Exception as e:
            print(f" ✗ @{username} — {e}")
            errors += 1
            _debug_dump(driver, f"unfollow_error_{username}")

        json.dump(log, open(log_path, "w"), indent=2)
        time.sleep(random.uniform(25, 55))  # keep it human-paced even without API rate limits

    return done, skipped, errors


# ══════════════════════════════════════════════════════════
# SEED FOLLOW (grow)
# ══════════════════════════════════════════════════════════
def seed_follow(driver, seed_account, max_follows=20, log_path="follow_log.json"):
    log = {}
    if os.path.exists(log_path):
        try:
            log = json.load(open(log_path))
        except Exception:
            log = {}

    if not seed_account:
        print(" No seed account given.")
        return 0

    driver.get(f"{IG_BASE}/{seed_account}/followers/")
    _dismiss_popups(driver)
    if not open_profile_list_dialog(driver, seed_account, "followers"):
        print(f" Could not open followers list for @{seed_account}.")
        print(" Check the username is right and the account isn't private.")
        return 0

    time.sleep(2)
    followed = 0
    dialog_xpath = "//div[@role='dialog']"
    scroll_box = _find_scrollable(driver, dialog_xpath)

    stagnant_scrolls = 0
    seen_rows = set()

    while followed < max_follows and stagnant_scrolls < 6:
        rows = driver.find_elements(By.XPATH, f"{dialog_xpath}//li")
        new_this_pass = 0
        for row in rows:
            if followed >= max_follows:
                break
            try:
                link = row.find_element(By.XPATH, ".//a[contains(@href,'/')]")
                uname = link.get_attribute("href").rstrip("/").split("/")[-1]
                if uname in log or uname in seen_rows or not uname:
                    continue
                seen_rows.add(uname)
                new_this_pass += 1
                follow_btn = row.find_element(By.XPATH, ".//button[text()='Follow']")
                follow_btn.click()
                print(f" + @{uname} ✔")
                log[uname] = {"followed_at": datetime.now().isoformat(), "followed_back": None}
                json.dump(log, open(log_path, "w"), indent=2)
                followed += 1
                time.sleep(random.uniform(20, 45))
            except (NoSuchElementException, StaleElementReferenceException):
                continue
            except Exception as e:
                print(f" ✗ row error: {e}")

        if new_this_pass == 0:
            stagnant_scrolls += 1
        else:
            stagnant_scrolls = 0

        if scroll_box is None:
            scroll_box = _find_scrollable(driver, dialog_xpath)
        if scroll_box is not None:
            try:
                driver.execute_script("arguments[0].scrollTop += 400;", scroll_box)
            except StaleElementReferenceException:
                scroll_box = None
        time.sleep(1.5)

    return followed


# ══════════════════════════════════════════════════════════
# DM AUTO-REPLY
# ══════════════════════════════════════════════════════════
def dm_check_and_reply(driver, reply_fn, avoid_fn, log_path="dm_log.json", max_threads=15):
    """
    reply_fn(context, message, history) -> str   — plug in your Groq call
    avoid_fn(text) -> bool                        — plug in avoid-topics check

    Slower than the private-API route but drives the real UI (types into the
    real message box, clicks the real send), so it doesn't carry the flagged
    X-IG-App-ID header the API route does. Every step prints what it's
    actually finding so a zero-thread or zero-reply run tells you why
    instead of silently doing nothing.
    """
    log = {}
    if os.path.exists(log_path):
        try:
            log = json.load(open(log_path))
        except Exception:
            log = {}

    driver.get(f"{IG_BASE}/direct/inbox/")
    _dismiss_popups(driver)

    # Wait for the inbox to actually finish rendering instead of a blind
    # sleep — IG's SPA can take a couple seconds, and a fixed sleep(3) can
    # fire before the thread list mounts, which silently finds 0 threads.
    try:
        WebDriverWait(driver, 12).until(
            EC.presence_of_element_located((By.XPATH, "//a[contains(@href,'/direct/t/')]"))
        )
    except TimeoutException:
        print(f" {RED}No DM thread links appeared within 12s.{R}")
        print(f" {DIM}landed on: {driver.current_url}{R}")
        print(f" {DIM}page title: {driver.title}{R}")
        was_debug = DEBUG
        globals()["DEBUG"] = True
        _debug_dump(driver, "inbox_no_threads")
        globals()["DEBUG"] = was_debug
        print(f" {DIM}(saved screenshot + html to ~/cortana_debug/ regardless of SELENIUM_DEBUG){R}")
        return 0

    thread_links = driver.find_elements(By.XPATH, "//a[contains(@href,'/direct/t/')]")[:max_threads]
    thread_hrefs = list({t.get_attribute("href") for t in thread_links})
    print(f" {DIM}Inbox: {len(thread_hrefs)} thread(s) found.{R}")
    replied = 0

    for href in thread_hrefs:
        driver.get(href)
        _dismiss_popups(driver)
        try:
            WebDriverWait(driver, 8).until(
                EC.presence_of_element_located((By.XPATH, "//div[@dir='auto']"))
            )
        except TimeoutException:
            print(f" {YLW}{href}: no message bubbles rendered — skipping.{R}")
            continue

        try:
            sender = driver.title.split("•")[0].strip() or "unknown"
        except Exception:
            sender = "unknown"

        try:
            bubbles = driver.find_elements(By.XPATH, "//div[@dir='auto']")
            recent = []
            for b in reversed(bubbles):
                t = b.text.strip()
                if t:
                    recent.append(t)
                if len(recent) >= 5:
                    break
            recent.reverse()  # back to chronological order (oldest of the 5 first)
            last_text = recent[-1] if recent else ""
        except Exception as e:
            print(f" {YLW}{href}: error reading bubbles: {e}{R}")
            recent = []
            last_text = ""

        if not last_text:
            print(f" {YLW}{href}: found the thread but no readable message text — skipping.{R}")
            continue

        key = f"{href}:{hash(last_text)}"
        if key in log:
            print(f" {DIM}{sender}: already replied to this message — skipping.{R}")
            continue
        if avoid_fn(last_text):
            print(f" {DIM}{sender}: matched an avoided topic — skipping.{R}")
            log[key] = {"skipped": True}
            json.dump(log, open(log_path, "w"), indent=2)
            continue

        try:
            reply = reply_fn(f"IG DM from {sender}", last_text, recent)
            # IG has used a few different markups for the reply box over
            # time — try each, most specific first, instead of betting on
            # a single exact placeholder string that a locale/UI change
            # can silently break.
            box = None
            for by, xpath in [
                (By.XPATH, "//textarea[@placeholder='Message...']"),
                (By.XPATH, "//textarea[contains(@placeholder,'Message')]"),
                (By.XPATH, "//div[@contenteditable='true'][contains(@aria-label,'Message')]"),
                (By.XPATH, "//div[@role='textbox'][@contenteditable='true']"),
            ]:
                try:
                    box = WebDriverWait(driver, 4).until(EC.presence_of_element_located((by, xpath)))
                    break
                except TimeoutException:
                    continue

            if box is None:
                raise TimeoutException("no reply box matched any known selector")

            box.click()
            box.send_keys(reply)
            box.send_keys(Keys.RETURN)
            print(f" {GRN}✔ Replied to {sender}: {reply[:50]}{R}")
            log[key] = {"sender": sender, "message": last_text, "reply": reply,
                        "sent_at": datetime.now().isoformat(), "status": "sent"}
            replied += 1
            time.sleep(random.uniform(6, 12))
        except TimeoutException as e:
            print(f" {RED}{sender}: couldn't find the reply box ({e}).{R}")
            was_debug = DEBUG
            globals()["DEBUG"] = True
            _debug_dump(driver, "dm_box_missing")
            globals()["DEBUG"] = was_debug
            log[key] = {"status": "failed"}
        except Exception as e:
            print(f" {RED}{sender}: error sending reply: {e}{R}")
            log[key] = {"status": "failed"}

        json.dump(log, open(log_path, "w"), indent=2)

    return replied


    return replied


# ══════════════════════════════════════════════════════════
# COMMENT AUTO-REPLY
# ══════════════════════════════════════════════════════════
def reply_to_comments(driver, my_username, reply_fn, avoid_fn, log_path="comment_log.json", max_posts=8):
    log = {}
    if os.path.exists(log_path):
        try:
            log = json.load(open(log_path))
        except Exception:
            log = {}

    driver.get(f"{IG_BASE}/{my_username}/")
    time.sleep(3)
    post_links = driver.find_elements(By.XPATH, "//a[contains(@href,'/p/')]")[:max_posts]
    post_hrefs = list({p.get_attribute("href") for p in post_links})
    replied = 0

    for href in post_hrefs:
        driver.get(href)
        time.sleep(2)
        comment_rows = driver.find_elements(By.XPATH, "//ul//li[.//a[contains(@href,'/')]]")
        for row in comment_rows:
            try:
                commenter_link = row.find_element(By.XPATH, ".//a")
                commenter = commenter_link.text.strip()
                if not commenter or commenter == my_username:
                    continue
                text_el = row.find_element(By.XPATH, ".//span")
                text = text_el.text.strip()
                key = f"{href}:{commenter}:{hash(text)}"
                if not text or key in log:
                    continue
                if avoid_fn(text):
                    log[key] = {"skipped": True}
                    continue

                reply = reply_fn(f"@{commenter} commented on your post", text, None)
                reply_btn = row.find_element(By.XPATH, ".//button[text()='Reply']")
                reply_btn.click()
                time.sleep(1)
                box = WebDriverWait(driver, 6).until(
                    EC.presence_of_element_located((By.XPATH, "//textarea[@aria-label='Add a comment…']"))
                )
                box.send_keys(f"@{commenter} {reply}")
                post_btn = driver.find_element(By.XPATH, "//button[text()='Post']")
                post_btn.click()
                print(f" ✔ Replied to @{commenter}: {reply[:40]}")
                log[key] = {"commenter": commenter, "comment": text, "reply": reply,
                            "sent_at": datetime.now().isoformat(), "status": "sent"}
                replied += 1
                json.dump(log, open(log_path, "w"), indent=2)
                time.sleep(random.uniform(15, 30))
            except (NoSuchElementException, StaleElementReferenceException):
                continue
            except Exception as e:
                print(f" ✗ comment error: {e}")

    return replied


# ══════════════════════════════════════════════════════════
# STORY POST
# ══════════════════════════════════════════════════════════
def post_story(driver, image_path, caption=""):
    if not os.path.exists(image_path):
        return False, "File not found."
    driver.get(IG_BASE)
    time.sleep(2)
    try:
        new_post_btn = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.XPATH, "//*[@aria-label='New post']"))
        )
        new_post_btn.click()
        time.sleep(1)
        story_option = WebDriverWait(driver, 6).until(
            EC.element_to_be_clickable((By.XPATH, "//*[text()='Story']"))
        )
        story_option.click()
        time.sleep(1)

        file_input = WebDriverWait(driver, 8).until(
            EC.presence_of_element_located((By.XPATH, "//input[@type='file']"))
        )
        file_input.send_keys(os.path.abspath(image_path))
        time.sleep(3)

        if caption:
            try:
                text_tool = driver.find_element(By.XPATH, "//*[@aria-label='Text']")
                text_tool.click()
                time.sleep(1)
                active = driver.switch_to.active_element
                active.send_keys(caption)
                driver.find_element(By.XPATH, "//button[text()='Done']").click()
            except NoSuchElementException:
                pass  # caption is a nice-to-have, don't fail the post over it

        share_btn = WebDriverWait(driver, 8).until(
            EC.element_to_be_clickable((By.XPATH, "//*[text()='Share to Story' or text()='Share']"))
        )
        share_btn.click()
        time.sleep(3)
        return True, "Posted."
    except TimeoutException:
        _debug_dump(driver, "story_post_failed")
        return False, "Selector mismatch — check ~/cortana_debug (run with SELENIUM_DEBUG=1)."
