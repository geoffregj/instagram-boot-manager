# IG bundle — current state & known problems

## What's in here

| File | What it is |
|---|---|
| `ig_manager.py` | Standalone console — run this directly (`python3 ig_manager.py`). Menu-driven: analyze followers, unfollow, seed-follow, DM/comment auto-reply, story posting, growth log, profile audit, bulk like, hashtag research, notifications digest. |
| `ig_selenium.py` | Shared library `ig_manager.py` imports for the actual browser actions (login, cookies, unfollow, DM read/send, comments, stories). Not meant to be run on its own. |
| `cortana_mint.py` | A separate, older all-in-one assistant (device control, music/movie downloads, M-PESA SMS parsing, plus its own Instagram automation) that also imports `ig_selenium.py`. Independent of `ig_manager.py` — the two don't call each other, they just share the same underlying `ig_selenium` module. |
| `self.example.json` | Template for a personal-profile file you copy to `self.json`, fill in, and point the Gemini reply backend at — it gets folded into every DM reply as background context. |

All three Python files require the same dependencies: `pip install selenium webdriver-manager --break-system-packages` (plus `requests`/`pypdf` for `cortana_mint.py`'s non-IG features, which aren't needed just to run `ig_manager.py`).

## Problems found so far, in order

**1. DM auto-reply silently did nothing ("Replied to 0 threads")**
The original DM-reading code scraped rendered HTML (`//div[@dir='auto']` bubbles) instead of reading structured data. When Instagram's markup didn't match — page not fully loaded, wrong bubble picked up, etc. — it failed with no error, just an empty result. No way to tell *why* it found nothing.

**2. Attempted fix: read DMs via Instagram's private JSON API instead of scraping HTML**
Faster and more reliable in principle — mirrors what `cortana_mint.py`'s older Instagram code did. Implemented two ways:
- A separate Python `requests.Session` built from the Selenium browser's cookies.
- Later, `fetch()` calls run *inside* the live authenticated browser tab via `driver.execute_async_script`, to rule out any header/cookie mismatch.

Both were rejected by Instagram with `HTTP 400: {"message":"useragent mismatch","status":"fail"}` — including immediately after a completely fresh interactive login. That rules out stale sessions or mismatched headers as the cause.

**Working theory:** the specific header used to call this API (`X-IG-App-ID: 936619743392459`) is extremely widely reused across public IG-scraper tools and tutorials. It's plausible Instagram's abuse detection flags that header on sight, independent of how clean everything else looks. This is functionally an arms race against Instagram's anti-bot systems — not a bug in our code — so it was abandoned rather than chased further with more header-spoofing.

**3. Current approach: back to driving the real UI, but hardened**
`ig_selenium.dm_check_and_reply` now:
- Waits for the inbox/thread list to actually render (`WebDriverWait`) instead of a fixed `sleep()`.
- Prints exactly what it finds at every step (thread count, per-thread skip reason, why a reply failed) instead of failing silently.
- Tries four different selectors for the reply text box instead of one exact placeholder string, since Instagram changes this without notice.
- Auto-dumps a screenshot + HTML snapshot to `~/cortana_debug/` on any failure, regardless of the `SELENIUM_DEBUG` env var.

This is **slower** (real typing, real clicking, waits between threads) and it's still automation against Instagram's Terms of Service — that risk hasn't gone away, it's just no longer compounded by also sending a flagged API header. Not yet confirmed working end-to-end; that's the next thing to test.

## Standing risks / things to know

- **Automating Instagram (unfollow batches, auto-follow, auto-DM, auto-comment, bulk-like) violates Instagram's Terms of Service.** This whole toolkit runs that risk regardless of which internal method is used — accounts can get rate-limited, checkpointed, or banned.
- **API keys shown on screen in earlier debugging screenshots (Gemini) should be treated as burned** — regenerate them in Google AI Studio rather than reusing.
- `self.json` (your filled-in personal profile) is not included here — only the `.example` template. Don't commit or share your real `self.json`, since it'll contain personal details you wrote into it for the bot to use.
