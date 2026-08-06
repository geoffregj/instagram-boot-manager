#!/usr/bin/env python3
"""
ig_manager.py — Standalone Instagram management console (Selenium / desktop)

Runs on its own — `python3 ig_manager.py` — separate from the Cortana chat
loop, so you can drive Instagram directly without going through Groq at all.
It also still works as a module Cortana can import, same as ig_selenium.py.

╔══════════════════════════════════════════════════════════╗
║  LOGIN — pick whichever fits how you actually log in       ║
╠══════════════════════════════════════════════════════════╣
║  1. Reuse your real Chrome profile  (BEST — no login step) ║
║     Selenium launches Chrome pointed at your actual        ║
║     ~/.config/google-chrome profile. If you're already     ║
║     logged into Instagram there — via password, passkey,   ║
║     a security key, or a synced Google passkey — that      ║
║     session is just... already open. Nothing to automate.  ║
║  2. Cookie import                                           ║
║     Export cookies from a browser extension (Cookie-Editor, ║
║     EditThisCookie — JSON format) after logging in however  ║
║     you like, point this at the file, done.                 ║
║  3. Interactive login                                       ║
║     Opens a blank Chrome window at the IG login page and    ║
║     waits — type your password, tap your security key,      ║
║     approve a passkey prompt, whatever Instagram offers      ║
║     you. Script just polls until it detects you're in.       ║
║  4. Saved session (from a previous run of this script)      ║
╠══════════════════════════════════════════════════════════╣
║  MANAGEMENT — beyond unfollow/DM/comments (still in         ║
║  ig_selenium.py and reused here):                            ║
║    - Direct follower/following scrape (no manual IG data    ║
║      export needed — this reads the live lists off the      ║
║      profile page itself)                                    ║
║    - Growth snapshot log (CSV, one row per day)              ║
║    - Profile audit (bio, counts, verified, link)             ║
║    - Bulk like posts from a hashtag or a profile's grid      ║
║    - Save / unsave a post                                    ║
║    - Hashtag research (top + recent post counts & captions)  ║
║    - Notifications digest                                    ║
║    - Multi-account switcher (config-driven)                  ║
╚══════════════════════════════════════════════════════════╝

pip install selenium --break-system-packages
"""

import os
import readline  # noqa: F401 — no direct use, but importing it makes input()
                  # understand arrow keys / line editing. Without this,
                  # pressing an arrow key at any input() prompt sends the
                  # raw escape bytes (^[[A etc.) straight into the string.
import csv
import json
import time
import random
from datetime import datetime
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    TimeoutException, NoSuchElementException, StaleElementReferenceException,
)

# Reuse the driver-agnostic action functions from the first module instead
# of duplicating unfollow / seed-follow / DM / comment / story logic.
import ig_selenium

HOME = os.path.expanduser("~")
ACCOUNTS_FILE = os.path.join(HOME, "ig_accounts.json")   # multi-account config
GROWTH_LOG = os.path.join(HOME, "ig_growth_log.csv")
DEBUG_DIR = os.path.join(HOME, "cortana_debug")
DEBUG = os.environ.get("SELENIUM_DEBUG", "0") == "1"

IG_BASE = "https://www.instagram.com"

R="\033[0m"; B="\033[1m"; DIM="\033[2m"
CYN="\033[96m"; YLW="\033[93m"; GRN="\033[92m"; RED="\033[91m"; WHT="\033[97m"; BLU="\033[94m"



# ══════════════════════════════════════════════════════════
# ACCOUNT CONFIG (multi-account switcher)
# ══════════════════════════════════════════════════════════
def load_accounts():
    if os.path.exists(ACCOUNTS_FILE):
        try:
            return json.load(open(ACCOUNTS_FILE))
        except Exception:
            pass
    return {}

def save_accounts(data):
    json.dump(data, open(ACCOUNTS_FILE, "w"), indent=2)
    os.chmod(ACCOUNTS_FILE, 0o600)

def register_account(name, cookie_file=None, chrome_profile_dir=None, chrome_profile_name=None):
    accounts = load_accounts()
    accounts[name] = {
        "cookie_file": cookie_file,
        "chrome_profile_dir": chrome_profile_dir,
        "chrome_profile_name": chrome_profile_name,
        "registered_at": datetime.now().isoformat(),
    }
    save_accounts(accounts)
    return accounts[name]


# ══════════════════════════════════════════════════════════
# LOGIN METHOD 1 — reuse your real Chrome profile
# ══════════════════════════════════════════════════════════
def login_with_chrome_profile(profile_dir=None, profile_name="Default", headless=False):
    """
    profile_dir: e.g. os.path.expanduser('~/.config/google-chrome')
                 (the *parent* dir, not the 'Default'/'Profile 1' folder itself)
    profile_name: which profile inside that dir — 'Default', 'Profile 1', etc.

    This makes Chrome BE your everyday browser. Whatever got you logged into
    Instagram there — password, passkey, security key, synced session —
    is already sitting in that profile's cookie jar. No login flow to
    automate at all.

    Caveat: you can't have your real Chrome open on the same profile at the
    same time (Chrome locks the profile dir). Close it first.
    """
    if profile_dir is None:
        # sane default locations depending on which browser is actually installed
        candidates = [
            os.path.expanduser("~/.config/google-chrome"),
            os.path.expanduser("~/.config/BraveSoftware/Brave-Browser"),
            os.path.expanduser("~/.config/chromium"),
        ]
        profile_dir = next((c for c in candidates if os.path.exists(c)), candidates[0])

    opts = Options()
    opts.add_argument(f"--user-data-dir={profile_dir}")
    opts.add_argument(f"--profile-directory={profile_name}")
    if headless:
        opts.add_argument("--headless=new")
    opts.add_argument("--disable-notifications")
    binary = ig_selenium._binary_location()
    if binary:
        opts.binary_location = binary
    try:
        try:
            from webdriver_manager.chrome import ChromeDriverManager
            from selenium.webdriver.chrome.service import Service
            driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=opts)
        except ImportError:
            driver = webdriver.Chrome(options=opts)
    except Exception as e:
        print(f" {RED}Could not launch with that profile: {e}{R}")
        print(f" {YLW}Most common cause: your real Chrome is still open on this profile.")
        print(f" Close Chrome completely (check for background processes too) and retry.{R}")
        return None

    driver.get(f"{IG_BASE}/")
    time.sleep(3)
    if ig_selenium.is_logged_in(driver):
        print(f" {GRN}✓ Already logged in via your Chrome profile — nothing to do.{R}")
        return driver
    print(f" {YLW}Chrome opened but Instagram isn't logged in on this profile.{R}")
    print(f" Log in normally in the window that just opened (password, passkey, whatever).")
    input(" Press Enter once you're logged in... ")
    if ig_selenium.is_logged_in(driver):
        print(f" {GRN}✓ Logged in.{R}")
        return driver
    print(f" {RED}Still not detecting a logged-in session.{R}")
    return driver  # hand it back anyway, might just be a selector miss


# ══════════════════════════════════════════════════════════
# LOGIN METHOD 2 — cookie import (from Cookie-Editor / EditThisCookie export)
# ══════════════════════════════════════════════════════════
def login_with_cookie_file(cookie_json_path, headless=False):
    """
    Export format expected: the standard array-of-objects JSON that
    Cookie-Editor / EditThisCookie produce, e.g.:
      [{"name":"sessionid","value":"...","domain":".instagram.com", ...}, ...]
    Get this by logging into instagram.com in your normal browser (any way
    you like), opening the extension, and exporting cookies for the site.
    """
    if not os.path.exists(cookie_json_path):
        print(f" {RED}Cookie file not found: {cookie_json_path}{R}")
        return None

    driver = ig_selenium.make_driver(headless=headless)
    driver.get(IG_BASE)
    time.sleep(2)

    cookies = json.load(open(cookie_json_path))
    added = 0
    for c in cookies:
        cookie = {
            "name": c.get("name"),
            "value": c.get("value"),
            "domain": c.get("domain", ".instagram.com"),
            "path": c.get("path", "/"),
        }
        if not cookie["name"] or cookie["value"] is None:
            continue
        try:
            driver.add_cookie(cookie)
            added += 1
        except Exception:
            pass

    driver.get(f"{IG_BASE}/")
    time.sleep(2)
    if ig_selenium.is_logged_in(driver):
        print(f" {GRN}✓ Logged in from {added} imported cookies.{R}")
        ig_selenium.save_cookies(driver)  # normalize into our own cookie store too
        return driver
    print(f" {YLW}Cookies imported ({added}) but login not detected — they may be stale.{R}")
    return driver


# ══════════════════════════════════════════════════════════
# LOGIN METHOD 3 — fully interactive (covers passkeys / security keys)
# ══════════════════════════════════════════════════════════
def login_interactive(headless=False):
    """
    Opens the login page and just waits. However Instagram lets you get in —
    password, passkey prompt, security key tap, 'Login with Google' if you
    have that linked — do it in the window. We only poll for success.
    """
    driver = ig_selenium.make_driver(headless=headless)
    driver.get(f"{IG_BASE}/accounts/login/")
    print(f" {CYN}Log in however you normally would in the window that just opened.{R}")
    print(f" {DIM}(password, passkey, security key, linked Google login — all fine){R}")
    input(" Press Enter once you're logged in... ")
    ig_selenium._dismiss_popups(driver)
    if ig_selenium.is_logged_in(driver):
        ig_selenium.save_cookies(driver)
        print(f" {GRN}✓ Logged in and session saved.{R}")
        return driver
    print(f" {RED}Not detecting a logged-in session yet.{R}")
    retry = input(" Try again? (y/n): ").strip().lower()
    if retry == "y":
        input(" Press Enter once you're really logged in... ")
        if ig_selenium.is_logged_in(driver):
            ig_selenium.save_cookies(driver)
            return driver
    return driver


# ══════════════════════════════════════════════════════════
# LOGIN METHOD 4 — saved session (delegate to ig_selenium's cookie store)
# ══════════════════════════════════════════════════════════
def login_saved_session(headless=False):
    driver = ig_selenium.make_driver(headless=headless)
    if ig_selenium.load_cookies(driver):
        print(f" {GRN}✓ Restored saved session.{R}")
        return driver
    print(f" {YLW}No valid saved session found.{R}")
    return None


# ══════════════════════════════════════════════════════════
# MANAGEMENT — direct follower/following scrape (no manual export needed)
# ══════════════════════════════════════════════════════════
def _find_scrollable(driver, dialog_xpath="//div[@role='dialog']"):
    """Find the real scrollable list container inside the dialog by asking
    the DOM directly (scrollHeight > clientHeight) instead of guessing at an
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


def _scroll_and_collect(driver, dialog_xpath="//div[@role='dialog']", max_scrolls=250):
    seen = set()
    stagnant = 0
    scrolls = 0
    scroll_box = _find_scrollable(driver, dialog_xpath)

    while stagnant < 6 and len(seen) < 20000 and scrolls < max_scrolls:
        rows = driver.find_elements(By.XPATH, f"{dialog_xpath}//a[contains(@href,'/')]")
        before = len(seen)
        for r in rows:
            try:
                href = r.get_attribute("href") or ""
                uname = href.rstrip("/").split("/")[-1]
                if uname and "/" not in uname:
                    seen.add(uname)
            except StaleElementReferenceException:
                continue
        if len(seen) == before:
            stagnant += 1
        else:
            stagnant = 0
            if len(seen) % 100 < 20:  # cheap progress ping, not every single row
                print(f" {DIM}...{len(seen)} so far{R}")

        if scroll_box is None:
            scroll_box = _find_scrollable(driver, dialog_xpath)  # list may not have mounted yet on first pass

        try:
            if scroll_box is not None:
                driver.execute_script(
                    "arguments[0].scrollTop += Math.max(arguments[0].clientHeight * 0.85, 400);",
                    scroll_box,
                )
            elif rows:
                # no scrollable container found at all — nudge IG's virtualized
                # list by scrolling the last known row into view instead
                driver.execute_script("arguments[0].scrollIntoView({block:'end'});", rows[-1])
            else:
                break  # nothing to scroll and nothing found — genuinely stuck
        except StaleElementReferenceException:
            scroll_box = None

        scrolls += 1
        time.sleep(1.2)
    return seen

def _diag_on_dialog_fail(driver, username, what):
    """Always-on (no SELENIUM_DEBUG needed) breadcrumb so a failed run
    still tells you *why* instead of just '0 accounts found'."""
    try:
        url = driver.current_url
    except Exception:
        url = "?"
    try:
        title = driver.title
    except Exception:
        title = "?"
    try:
        body_snip = driver.find_element(By.TAG_NAME, "body").text.strip().replace("\n", " ")[:180]
    except Exception:
        body_snip = "?"

    print(f" {RED}Could not open {what} list for @{username}.{R}")
    print(f" {DIM}landed on: {url}{R}")
    print(f" {DIM}page title: {title}{R}")
    print(f" {DIM}visible text: {body_snip}{R}")

    if "/login" in url or "challenge" in url:
        print(f" {YLW}→ Session got bounced to a login/challenge page. Cookies are"
              f" stale or the account got a checkpoint — log in fresh (option 3){R}")
    elif f"/{username}/{what}/" not in url:
        print(f" {YLW}→ Direct nav to the {what} URL didn't stick — IG served the"
              f" plain profile page instead of the dialog. This usually means"
              f" the modal only opens via client-side routing, not a cold page load.{R}")
    else:
        print(f" {YLW}→ URL looks right but no dialog rendered — IG likely changed"
              f" the markup. Run with SELENIUM_DEBUG=1 for a screenshot to re-check selectors.{R}")

    # Always dump HTML/screenshot on failure, not just when SELENIUM_DEBUG=1 —
    # a failed run is exactly when you want the evidence.
    was_debug = ig_selenium.DEBUG
    ig_selenium.DEBUG = True
    ig_selenium._debug_dump(driver, f"{what}_dialog_missing")
    ig_selenium.DEBUG = was_debug
    print(f" {DIM}(saved screenshot + html to ~/cortana_debug/ regardless of SELENIUM_DEBUG){R}")


def _find_stat_link(driver, username, what):
    """IG's own-profile stat links aren't reliably plain <a href> anymore —
    try several selector strategies, most-specific first, and return the
    first clickable element found (or None)."""
    candidates = [
        # classic anchor with matching href
        (By.XPATH, f"//a[contains(@href,'/{username}/{what}/')]"),
        # any element (div/span/a) explicitly role='link' with matching href
        (By.XPATH, f"//*[@role='link'][contains(@href,'/{username}/{what}/')]"),
        # anchor whose href just ends in /following/ or /followers/ (relative nav quirks)
        (By.XPATH, f"//a[contains(@href,'/{what}/')]"),
        # last resort: the header stat <li>/<span> containing the word itself,
        # walk up to nearest ancestor that's actually clickable
        (By.XPATH, f"//header//*[contains(text(),'{what}')]/ancestor-or-self::*[@role='link' or self::a][1]"),
    ]
    for by, xpath in candidates:
        try:
            el = WebDriverWait(driver, 4).until(EC.presence_of_element_located((by, xpath)))
            return el, xpath
        except TimeoutException:
            continue
    return None, None


def _open_list_via_profile_click(driver, username, what):
    """Fallback: load the profile page like a human, then click the
    'X following' / 'X followers' stat link so IG's SPA router opens the
    dialog the way it expects, instead of cold-loading /username/following/.
    Returns (success, reason) so the caller can tell you exactly which
    step failed instead of a single generic timeout."""
    driver.get(f"{IG_BASE}/{username}/")
    ig_selenium._dismiss_popups(driver)
    time.sleep(1.5)  # let the header fully render before hunting for stats

    link, matched_xpath = _find_stat_link(driver, username, what)
    if link is None:
        return False, f"no clickable '{what}' stat link found on the profile page (all selectors missed)"

    try:
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", link)
        time.sleep(0.3)
        # JS click sidesteps 'element click intercepted' from tooltips/overlays
        # (e.g. the 'Add a note' bubble near the avatar) that a native click hits.
        driver.execute_script("arguments[0].click();", link)
    except Exception as e:
        return False, f"found the '{what}' link ({matched_xpath}) but click failed: {e}"

    try:
        WebDriverWait(driver, 8).until(
            EC.presence_of_element_located((By.XPATH, "//div[@role='dialog']"))
        )
        return True, None
    except TimeoutException:
        return False, f"clicked the '{what}' link ({matched_xpath}) but no dialog appeared — IG may be showing an inline panel instead of a modal now"


def scrape_following(driver, username):
    if not username:
        print(f" {RED}No username given.{R}")
        return set()
    driver.get(f"{IG_BASE}/{username}/following/")
    ig_selenium._dismiss_popups(driver)
    try:
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.XPATH, "//div[@role='dialog']"))
        )
    except TimeoutException:
        print(f" {DIM}Direct nav didn't open the dialog — retrying via profile click...{R}")
        ok, reason = _open_list_via_profile_click(driver, username, "following")
        if not ok:
            print(f" {YLW}Fallback also failed: {reason}{R}")
            _diag_on_dialog_fail(driver, username, "following")
            return set()
    time.sleep(2)
    return _scroll_and_collect(driver)

def scrape_followers(driver, username):
    if not username:
        print(f" {RED}No username given.{R}")
        return set()
    driver.get(f"{IG_BASE}/{username}/followers/")
    ig_selenium._dismiss_popups(driver)
    try:
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.XPATH, "//div[@role='dialog']"))
        )
    except TimeoutException:
        print(f" {DIM}Direct nav didn't open the dialog — retrying via profile click...{R}")
        ok, reason = _open_list_via_profile_click(driver, username, "followers")
        if not ok:
            print(f" {YLW}Fallback also failed: {reason}{R}")
            _diag_on_dialog_fail(driver, username, "followers")
            return set()
    time.sleep(2)
    return _scroll_and_collect(driver)

def analyze_live(driver, username, results_path="instagram_results.json"):
    """Same output shape as the old ig_parse_export(), but scraped live —
    no manual 'Download your data' export from Instagram needed anymore."""
    print(f" {CYN}Scraping following for @{username}...{R}")
    following = scrape_following(driver, username)
    print(f" {DIM}{len(following)} accounts found.{R}")
    print(f" {CYN}Scraping followers for @{username}...{R}")
    followers = scrape_followers(driver, username)
    print(f" {DIM}{len(followers)} accounts found.{R}")

    res = {
        "not_following_back": sorted(following - followers),
        "you_dont_follow":    sorted(followers - following),
        "mutual":             sorted(following & followers),
        "following_count":    len(following),
        "followers_count":    len(followers),
        "generated_at":       datetime.now().isoformat(),
    }
    json.dump(res, open(results_path, "w"), indent=2)
    return res


# ══════════════════════════════════════════════════════════
# MANAGEMENT — growth tracking (one CSV row per day)
# ══════════════════════════════════════════════════════════
def profile_counts(driver, username):
    driver.get(f"{IG_BASE}/{username}/")
    time.sleep(3)
    counts = {"posts": None, "followers": None, "following": None}
    try:
        header = driver.find_element(By.XPATH, "//header")
        spans = header.find_elements(By.XPATH, ".//span/span | .//li//span")
        # Fallback: read the three stat links directly
    except NoSuchElementException:
        pass
    for key, text_part in (("posts", "post"), ("followers", "follower"), ("following", "following")):
        try:
            el = driver.find_element(By.XPATH, f"//li[contains(.,'{text_part}')]//span")
            counts[key] = el.get_attribute("title") or el.text
        except NoSuchElementException:
            continue
    return counts

def growth_snapshot(driver, username):
    counts = profile_counts(driver, username)
    row = [datetime.now().strftime("%Y-%m-%d"), username,
           counts.get("followers") or "", counts.get("following") or "", counts.get("posts") or ""]
    new_file = not os.path.exists(GROWTH_LOG)
    with open(GROWTH_LOG, "a", newline="") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["date", "username", "followers", "following", "posts"])
        w.writerow(row)
    print(f" {GRN}✓ Logged: {row}{R}")
    return row


# ══════════════════════════════════════════════════════════
# MANAGEMENT — profile audit
# ══════════════════════════════════════════════════════════
def profile_audit(driver, username):
    driver.get(f"{IG_BASE}/{username}/")
    time.sleep(3)
    info = {"username": username}
    try:
        info["bio"] = driver.find_element(By.XPATH, "//header//span[contains(@class,'')]").text
    except NoSuchElementException:
        info["bio"] = None
    try:
        info["verified"] = bool(driver.find_elements(By.XPATH, "//*[@aria-label='Verified']"))
    except Exception:
        info["verified"] = False
    try:
        info["external_link"] = driver.find_element(By.XPATH, "//header//a[contains(@href,'http')]").get_attribute("href")
    except NoSuchElementException:
        info["external_link"] = None
    info.update(profile_counts(driver, username))
    return info


# ══════════════════════════════════════════════════════════
# MANAGEMENT — bulk like from a hashtag or a profile grid
# ══════════════════════════════════════════════════════════
def bulk_like(driver, source, max_likes=10):
    """source: '#hashtag' or a 'username' — likes the first N posts found."""
    if source.startswith("#"):
        driver.get(f"{IG_BASE}/explore/tags/{source[1:]}/")
    else:
        driver.get(f"{IG_BASE}/{source}/")
    time.sleep(3)

    post_links = driver.find_elements(By.XPATH, "//a[contains(@href,'/p/')]")
    hrefs = list(dict.fromkeys(l.get_attribute("href") for l in post_links))[:max_likes]
    liked = 0
    for href in hrefs:
        driver.get(href)
        time.sleep(2)
        try:
            like_btn = driver.find_element(By.XPATH, "//*[@aria-label='Like']")
            like_btn.click()
            liked += 1
            print(f" ♥ Liked {href}")
            time.sleep(random.uniform(4, 9))
        except NoSuchElementException:
            print(f" {DIM}Already liked or button not found: {href}{R}")
    return liked


# ══════════════════════════════════════════════════════════
# MANAGEMENT — save / unsave a post
# ══════════════════════════════════════════════════════════
def save_post(driver, post_url):
    driver.get(post_url)
    time.sleep(2)
    try:
        driver.find_element(By.XPATH, "//*[@aria-label='Save']").click()
        return True
    except NoSuchElementException:
        return False


# ══════════════════════════════════════════════════════════
# MANAGEMENT — hashtag research
# ══════════════════════════════════════════════════════════
def hashtag_research(driver, tag):
    tag = tag.lstrip("#")
    driver.get(f"{IG_BASE}/explore/tags/{tag}/")
    time.sleep(3)
    result = {"tag": tag, "post_count": None, "sample_posts": []}
    try:
        result["post_count"] = driver.find_element(By.XPATH, "//header//span").text
    except NoSuchElementException:
        pass
    links = driver.find_elements(By.XPATH, "//a[contains(@href,'/p/')]")[:12]
    result["sample_posts"] = list(dict.fromkeys(l.get_attribute("href") for l in links))
    return result


# ══════════════════════════════════════════════════════════
# MANAGEMENT — notifications digest
# ══════════════════════════════════════════════════════════
def notifications_digest(driver, limit=15):
    driver.get(f"{IG_BASE}/accounts/activity/")
    time.sleep(3)
    items = driver.find_elements(By.XPATH, "//div[@role='dialog']//a | //main//a")[:limit]
    out = []
    for it in items:
        txt = it.text.strip()
        if txt:
            out.append(txt)
    return out


# ══════════════════════════════════════════════════════════
# INTERACTIVE MENU — standalone entry point
# ══════════════════════════════════════════════════════════
def _prompt_reply_backend():
    """Pick which LLM writes DM/comment replies. Groq is fast cloud, but if
    you say it's dumb you probably want your own model — route to a local
    Ollama instance instead (same idea as Sylvia's LAN connection)."""
    print(" Reply backend:")
    print("  1. Groq (cloud, needs API key)")
    print("  2. Local Ollama (localhost or LAN)")
    print("  3. Google Gemini (cloud, needs API key)")
    pick = input(" Choice [1]: ").strip() or "1"

    if pick == "3":
        # Prefer an env var so you're not typing a live key into a terminal
        # (and so it can't end up in shell history or a screen recording).
        api_key = os.environ.get("GEMINI_API_KEY", "").strip()
        if not api_key:
            api_key = input(" Gemini API key (or set GEMINI_API_KEY env var instead): ").strip()
        model = input(" Gemini model [gemini-2.5-flash]: ").strip() or "gemini-2.5-flash"

        # Optional "who am I" profile — loaded once, reused for every reply.
        # Free-form JSON: name, bio, tone, facts, boilerplate answers, whatever
        # you want the model to know about you. With a 1M-token window this
        # costs basically nothing to include every call.
        profile_path = input(" Path to self.json profile (blank to skip): ").strip()
        self_profile = {}
        if profile_path and os.path.exists(profile_path):
            try:
                self_profile = json.load(open(profile_path))
                print(f" {DIM}Loaded profile with {len(self_profile)} field(s) from {profile_path}{R}")
            except Exception as e:
                print(f" {YLW}Couldn't parse {profile_path}: {e} — continuing without it.{R}")

        def reply_fn(ctx, msg, hist):
            # Minimal inline REST call — no dependency on the google-genai
            # SDK, same "fully standalone" approach as the Groq branch above.
            import urllib.request, ssl

            sys_parts = ["Reply casually and briefly, like a real person on Instagram."]
            if self_profile:
                sys_parts.append("Background info about the person you're replying as "
                                  "(use it to answer naturally, don't just recite it):")
                sys_parts.append(json.dumps(self_profile, indent=2))
            system_text = "\n\n".join(sys_parts)

            # hist is the last few message bubbles scraped from the thread
            # (both sides, chronological, no sender labels — DM UI doesn't
            # expose who-said-what cleanly, so treat it as rough context,
            # not a clean transcript).
            convo_text = ""
            if hist:
                lines = []
                for h in hist:
                    if isinstance(h, dict):
                        who = "You" if h.get("role") == "assistant" else "Them"
                        lines.append(f"{who}: {h.get('content', '')}")
                    else:
                        lines.append(f"- {h}")
                convo_text = "Recent messages in this thread, oldest first:\n" + "\n".join(lines)

            user_text = f"{ctx}\n\n{convo_text}\n\nMessage to reply to: {msg}".strip()

            url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
                   f"{model}:generateContent?key={api_key}")
            payload = json.dumps({
                "contents": [{"parts": [{"text": user_text}]}],
                "systemInstruction": {"parts": [{"text": system_text}]},
                "generationConfig": {"maxOutputTokens": 100},
            }).encode()
            req = urllib.request.Request(
                url, data=payload, method="POST",
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=20, context=ssl.create_default_context()) as r:
                data = json.loads(r.read())
            try:
                return data["candidates"][0]["content"]["parts"][0]["text"].strip()
            except (KeyError, IndexError):
                # Common cause: the reply got blocked by a safety filter, or
                # the response was truncated — data['candidates'][0].get('finishReason')
                # tells you which.
                print(f" {YLW}Gemini returned no usable text: {data}{R}")
                return "hey!"

        print(f" {DIM}Using Gemini ({model}){R}")
        return reply_fn

    if pick == "2":
        host = input(" Ollama host [http://localhost:11434]: ").strip() or "http://localhost:11434"
        model = input(" Ollama model [qwen2.5-coder:7b]: ").strip() or "qwen2.5-coder:7b"
        if "coder" in model.lower():
            print(f" {DIM}Heads up: coder models tend to write stiff/technical replies —"
                  f" a general chat model (e.g. llama3.1, qwen2.5) usually sounds more human"
                  f" for DMs/comments. Your call.{R}")

        def reply_fn(ctx, msg, hist):
            import urllib.request
            payload = json.dumps({
                "model": model,
                "messages": [
                    {"role": "system", "content": "Reply casually and briefly, like a real person on Instagram."},
                    {"role": "user", "content": msg},
                ],
                "stream": False,
            }).encode()
            req = urllib.request.Request(
                f"{host.rstrip('/')}/api/chat",
                data=payload, method="POST",
                headers={"Content-Type": "application/json"},
            )
            # local inference is slower than a cloud API — generous timeout
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read())["message"]["content"].strip()

        print(f" {DIM}Using local Ollama @ {host} ({model}){R}")
        return reply_fn

    api_key = input(" Groq API key (for reply generation): ").strip()

    def reply_fn(ctx, msg, hist):
        # minimal inline Groq call so this module has zero dependency
        # on cortana_mint.py — fully standalone
        import urllib.request, ssl
        payload = json.dumps({
            "model": "llama-3.3-70b-versatile",
            "messages": [{"role": "system", "content": "Reply casually and briefly."},
                         {"role": "user", "content": msg}],
            "max_tokens": 100,
        }).encode()
        req = urllib.request.Request(
            "https://api.groq.com/openai/v1/chat/completions",
            data=payload, method="POST",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"})
        with urllib.request.urlopen(req, timeout=20, context=ssl.create_default_context()) as r:
            return json.loads(r.read())["choices"][0]["message"]["content"].strip()

    return reply_fn


def _menu_login():
    print(f"\n{CYN} How do you want to log in?{R}")
    print("  1. Reuse my real Chrome profile (close Chrome first)")
    print("  2. Import cookies from a JSON export")
    print("  3. Interactive (password / passkey / security key — do it yourself)")
    print("  4. Use previously saved session")
    choice = input(" Choice: ").strip()
    if choice == "1":
        profile_dir = input(" Chrome profile parent dir [default ~/.config/google-chrome]: ").strip()
        profile_name = input(" Profile name [default 'Default']: ").strip() or "Default"
        return login_with_chrome_profile(profile_dir or None, profile_name)
    if choice == "2":
        path = input(" Path to cookie JSON export: ").strip()
        return login_with_cookie_file(path)
    if choice == "3":
        return login_interactive()
    if choice == "4":
        return login_saved_session()
    print(" Not a valid choice.")
    return None


def main():
    os.system("clear")
    print(f"{CYN}{'═'*54}{R}")
    print(f"{B}{WHT} IG MANAGER — standalone Instagram console{R}")
    print(f"{CYN}{'═'*54}{R}")

    driver = _menu_login()
    if driver is None:
        print(f" {RED}No session — exiting.{R}")
        return

    username = ""
    while not username:
        raw = input(" Your Instagram username (for scrape/audit calls): ")
        username = ig_selenium.clean_username(raw)
        if not username:
            print(" That's blank (or was just control characters) — type your actual username.")
            continue
        ok = input(f" Using @{username} — correct? (y/n): ").strip().lower()
        if ok != "y":
            username = ""

    while True:
        print(f"\n{BLU}{'─'*54}{R}")
        print(" 1. Analyze followers/following (live scrape, no export needed)")
        print(" 2. Unfollow non-followers")
        print(" 3. Seed-follow from an account")
        print(" 4. DM auto-reply (once)")
        print(" 5. Comment auto-reply (once)")
        print(" 6. Post a story")
        print(" 7. Growth snapshot (log today's counts)")
        print(" 8. Profile audit")
        print(" 9. Bulk like (hashtag or profile)")
        print(" 10. Hashtag research")
        print(" 11. Notifications digest")
        print(" 0. Quit")
        choice = input(" Choice: ").strip()

        if choice == "0":
            break
        elif choice == "1":
            res = analyze_live(driver, username)
            print(f" Following:{res['following_count']} Followers:{res['followers_count']} "
                  f"Not following back:{len(res['not_following_back'])}")
        elif choice == "2":
            targets = json.load(open("instagram_results.json")).get("not_following_back", []) \
                if os.path.exists("instagram_results.json") else []
            if not targets:
                print(" Run analyze first (option 1).")
                continue
            n = int(input(f" How many (of {len(targets)})? ").strip() or "10")
            done, skipped, errors = ig_selenium.unfollow_batch(driver, username, targets, n)
            print(f" Done:{done} Skipped:{skipped} Errors:{errors}")
        elif choice == "3":
            seed = ig_selenium.clean_username(input(" Seed account: "))
            n = int(input(" How many to follow? ").strip() or "10")
            print(f" Followed {ig_selenium.seed_follow(driver, seed, n)}")
        elif choice == "4":
            reply_fn = _prompt_reply_backend()
            n = ig_selenium.dm_check_and_reply(driver, reply_fn, lambda text: False)
            print(f" Replied to {n} threads.")
        elif choice == "5":
            reply_fn = _prompt_reply_backend()
            n = ig_selenium.reply_to_comments(driver, username, reply_fn, lambda text: False)
            print(f" Replied to {n} comments.")
        elif choice == "6":
            path = input(" Image path: ").strip()
            caption = input(" Caption (optional): ").strip()
            ok, msg = ig_selenium.post_story(driver, path, caption)
            print(f" {msg}")
        elif choice == "7":
            growth_snapshot(driver, username)
        elif choice == "8":
            info = profile_audit(driver, username)
            for k, v in info.items():
                print(f"  {k}: {v}")
        elif choice == "9":
            source = input(" Hashtag (#tag) or username: ").strip()
            n = int(input(" How many posts to like? ").strip() or "5")
            print(f" Liked {bulk_like(driver, source, n)}")
        elif choice == "10":
            tag = input(" Hashtag: ").strip()
            res = hashtag_research(driver, tag)
            print(f" Post count: {res['post_count']}")
            for p in res["sample_posts"][:5]:
                print(f"  {p}")
        elif choice == "11":
            for line in notifications_digest(driver):
                print(f"  {line}")
        else:
            print(" Not a valid choice.")

    driver.quit()
    print(" Session closed.")


if __name__ == "__main__":
    main()
