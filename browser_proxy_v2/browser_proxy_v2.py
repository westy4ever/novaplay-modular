#!/usr/bin/env python3
"""ArabicPlayer Browser Proxy v2.1 — persistent per-domain contexts.

v2.1 changes:
  - FETCH LOCK: Playwright sync API is not thread-safe; v2's
    threaded=True could crash on overlapping requests. All fetches are
    now serialized; /health and /stats stay responsive.
  - Challenge handling: poll every 2s up to the budget instead of one
    fixed 10s sleep — fast clears return fast, slow ones get full time.
  - Honors optional "timeout" (seconds) in the request JSON as the
    challenge budget (default 20, clamped 5-120).
  - Per-fetch duration logging; /stats shows solved/busy state.
  - Persistent contexts per domain (from v2): CF challenge solved once,
    then cookie reuse makes later pages ~1-2s.
  - Same /fetch + /health API → drop-in replacement.

Run:  python3 browser_proxy_v2_1.py
Needs: pip install flask playwright && playwright install chromium
"""

import threading
import time
import traceback
from flask import Flask, request, jsonify
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

app = Flask(__name__)

# ─── Browser management ────────────────────────────────────────────────
_playwright = None
_browser = None
_browser_lock = threading.Lock()
_browser_error = False

# v2.1: serialize all fetches — one box, one user, no contention in
# practice, and Playwright sync objects stop being a crash risk.
_FETCH_LOCK = threading.Lock()

# ─── Per-domain context cache ──────────────────────────────────────────
_CONTEXTS = {}
_CONTEXTS_LOCK = threading.Lock()
_CONTEXT_TTL = 30 * 60           # idle expiry: 30 minutes
_CHALLENGE_MARKERS = ('cf-browser-verification', 'just a moment',
                      'checking your browser', 'turnstile',
                      'cf-chl', 'cf_chl_')
_DEFAULT_UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
               'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')


def _domain_key(url):
    try:
        from urllib.parse import urlparse
        return urlparse(url).netloc.lower() or "unknown"
    except Exception:
        return "unknown"


def _is_challenge(html):
    lowered = (html or "").lower()
    return any(m in lowered for m in _CHALLENGE_MARKERS)


def _ensure_browser():
    """Launch/relaunch the shared headless Chromium."""
    global _playwright, _browser, _browser_error
    with _browser_lock:
        if _browser_error or _browser is None:
            if _browser:
                try: _browser.close()
                except Exception: pass
            if _playwright:
                try: _playwright.stop()
                except Exception: pass
            try:
                _playwright = sync_playwright().start()
                _browser = _playwright.chromium.launch(
                    headless=True,
                    args=['--no-sandbox', '--disable-dev-shm-usage'])
                _browser_error = False
                print("[Proxy] Browser launched successfully")
            except Exception as e:
                _browser_error = True
                print(f"[Proxy] Failed to launch browser: {e}")
                raise
    return _browser


def _close_context(key):
    """Tear down one domain context."""
    with _CONTEXTS_LOCK:
        entry = _CONTEXTS.pop(key, None)
    if entry:
        try: entry["page"].close()
        except Exception: pass
        try: entry["context"].close()
        except Exception: pass


def _close_all_contexts():
    with _CONTEXTS_LOCK:
        keys = list(_CONTEXTS.keys())
    for k in keys:
        _close_context(k)


def _get_context(domain_key):
    """Return the context entry for this domain, creating it if needed."""
    with _CONTEXTS_LOCK:
        now = time.time()
        for k in [k for k, e in _CONTEXTS.items()
                  if now - e["last_used"] > _CONTEXT_TTL]:
            _CONTEXTS.pop(k, None)
        entry = _CONTEXTS.get(domain_key)
    if entry is not None:
        entry["last_used"] = now
        return entry
    # Create fresh
    browser = _ensure_browser()
    context = browser.new_context(
        user_agent=_DEFAULT_UA,
        viewport={'width': 1920, 'height': 1080})
    page = context.new_page()
    entry = {"context": context, "page": page, "ua": _DEFAULT_UA,
             "last_used": time.time(), "solved": False}
    with _CONTEXTS_LOCK:
        _CONTEXTS[domain_key] = entry
    return entry


def _wait_challenge_clear(page, budget_s):
    """Poll until the challenge disappears or budget runs out.
    Returns the final HTML (may still be the challenge page)."""
    deadline = time.time() + budget_s
    html = page.content()
    while _is_challenge(html) and time.time() < deadline:
        time.sleep(2)
        try:
            html = page.content()
        except Exception:
            break
    return html


def _proxy_fetch(url, referer, challenge_budget_s):
    """Fetch with per-domain persistence + challenge recycle logic.
    Returns (html, final_url, user_agent, duration_ms)."""
    domain_key = _domain_key(url)
    for attempt in (1, 2):
        entry = _get_context(domain_key)
        page = entry["page"]
        if referer:
            try:
                page.set_extra_http_headers({'Referer': referer})
            except Exception:
                pass
        try:
            t0 = time.time()
            try:
                page.goto(url, wait_until='domcontentloaded', timeout=30000)
            except PlaywrightTimeoutError:
                try:
                    page.wait_for_load_state('networkidle', timeout=10000)
                except Exception:
                    pass
            time.sleep(2)                     # settle for JS rendering
            html = page.content()
            final_url = page.url
            if _is_challenge(html):
                print(f"[Proxy] Challenge on {domain_key} — polling up to {challenge_budget_s}s")
                html = _wait_challenge_clear(page, challenge_budget_s)
                final_url = page.url
                if _is_challenge(html):
                    if attempt == 1:
                        print(f"[Proxy] Not cleared — recycling context for {domain_key}")
                        _close_context(domain_key)
                        continue              # retry with fresh context
                    print(f"[Proxy] Challenge NOT cleared for {domain_key} — returning as-is")
            entry["solved"] = True
            entry["last_used"] = time.time()
            return html, final_url, entry["ua"], int((time.time() - t0) * 1000)
        except Exception:
            # Page-level hard failure → drop this context, retry once
            _close_context(domain_key)
            if attempt == 2:
                raise
    raise RuntimeError("unreachable")


def _mark_broken_browser():
    """Full teardown so the next request relaunches the browser."""
    global _browser_error, _browser, _playwright
    with _browser_lock:
        _browser_error = True
        if _browser:
            try: _browser.close()
            except Exception: pass
            _browser = None
        if _playwright:
            try: _playwright.stop()
            except Exception: pass
            _playwright = None


@app.route('/fetch', methods=['POST'])
def proxy_fetch():
    data = request.json or {}
    url = (data.get('url') or '').strip()
    referer = (data.get('referer') or '').strip() or None
    try:
        budget = int(data.get('timeout') or 20)
    except Exception:
        budget = 20
    budget = max(5, min(120, budget))

    if not url:
        return jsonify({'success': False, 'error': 'No URL provided'}), 400

    print(f"[Proxy] Fetching: {url[:80]}...")
    if not _FETCH_LOCK.acquire(timeout=budget + 60):
        return jsonify({'success': False, 'error': 'proxy busy'}), 503
    try:
        html, final_url, ua, ms = _proxy_fetch(url, referer, budget)
        print(f"[Proxy] Success {ms}ms, {len(html or '')} bytes <- {final_url[:80]}")
        return jsonify({'success': True, 'html': html,
                        'url': final_url, 'user_agent': ua})
    except Exception as e:
        print(f"[Proxy] ERROR: {str(e)[:200]}\n{traceback.format_exc()[:400]}")
        _mark_broken_browser()
        _close_all_contexts()
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        _FETCH_LOCK.release()


@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok'})


@app.route('/stats', methods=['GET'])
def stats():
    with _CONTEXTS_LOCK:
        now = time.time()
        info = {k: {'idle_for': int(now - e['last_used']),
                    'solved': e['solved']}
                for k, e in _CONTEXTS.items()}
    return jsonify({'status': 'ok',
                    'busy': _FETCH_LOCK.locked(),
                    'contexts': info})


if __name__ == '__main__':
    print("=" * 50)
    print("ArabicPlayer Browser Proxy v2.1")
    print("  persistent per-domain contexts, serialized fetches")
    print("  POST /fetch   GET /health   GET /stats")
    print("Running on http://0.0.0.0:5000")
    print("=" * 50)
    app.run(host='0.0.0.0', port=5000, threaded=True)