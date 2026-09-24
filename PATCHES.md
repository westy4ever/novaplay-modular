# NovaPlay Plugin — Patch Ledger & Maintenance Guide
Generated: 2026-09-14

## WHAT THIS FILE IS
A history of every fix applied to this plugin, plus the working habits that
keep it healthy. Paste this file at the start of any new AI conversation to
restore full context instantly.

## THE GOLDEN RULES (read first)
1. After ANY file edit, restart Enigma2 before testing:   init 4 && init 3
   (Edits are invisible to the running system until restart.)
2. Always check the log when something misbehaves:
   tail -50 /tmp/arabicplayer.log
3. python3 -m py_compile FILENAME  — checks a file for typos after editing.
4. Positions/badges/strip update LIVE in state; a plugin restart is only
   needed after editing FILES, not after watching something.

## PATCH LEDGER (1–39)
Format: number — what it does — file — grep marker

1    — remove duplicated stall-watchdog init          plugin_screen_player.py  [PATCH 1]
2a/2b— fix duplicate __audioSelect (gating restored)  plugin_screen_player.py  [PATCH 2a]/[PATCH 2b]
3    — remove duplicate flush_style in studio         plugin_screen_player.py  [PATCH 3]
4    — fix dead ternary in _studioOk                  plugin_screen_player.py  [PATCH 4]
5    — green-key restart double-tap guard             plugin_screen_player.py  [PATCH 5]
6    — autoNextProgress = real ProgressBar            plugin_screen_player.py  [PATCH 6]
7    — font browser: EXIT keeps font, YELLOW resets   plugin_screen_player.py  [PATCH 7]
8    — recording thread py2-safe (daemon attr)        plugin_screen_player.py  [PATCH 8]
9    — subtitle sync uses SYNC_STEP_MS                plugin_screen_player.py  [PATCH 9]
10   — local-file fallback carries resume/item_url    plugin_screen_player.py  [PATCH 10]
11   — aspect bars show/hide (later removed — see 37)
12   — grid badges: block back inside column loop     plugin_screen_home.py    [PATCH 12]
13   — continue-strip per-poster time badges          plugin_gridlist.py + home [PATCH 13]
14/14b— vertical category list (TextListGrid)         plugin_gridlist.py + home [PATCH 14]
15+16— bigger poster captions (3 lines, 20px)         plugin_gridlist.py       [PATCH 15+16]
17   — detail skin cleanup (tmdb_note, bg, overlap)   plugin_screen_detail.py
18   — resume thresholds unified to >30s               plugin_state.py/watched  (pos <= 30)
19   — server-list rows: tight columns + 95px height  plugin_widgets.py        [PATCH 19]
19+  — row content optically centered (y 12/52)       plugin_widgets.py        [PATCH 19+]
21   — downloads: شاهد/حمّل choice + watch branch      plugin_screen_detail.py  [PATCH 21]
22/23— autosub: X-API-Key + reuse subtitle module     novaplay_autosub.py     [PATCH 23]
24   — strip posters swap in live (poll in home mode)  plugin_screen_home.py    [PATCH 24]
25   — quality cap: pick best rendition ≤ cap         plugin_screen_detail.py  [PATCH 25]
26   — auto-next: module-anchored deferred open       plugin_screen_player.py  [PATCH 26]
27   — max-quality setting (tools menu dropdown)      plugin_screen_settings.py[PATCH 27]
28   — RTL rows for Arabic titles + _has_arabic       plugin_gridlist.py       [PATCH 28]
29   — REAL aspect via eAVSwitch (probe) — see 37
30a-d— torrentio: lazy TMDB ids (1 req/page), TS      extractors/torrentio.py  [PATCH 30]
       word-boundary, seeders/size parsing, log cleanup
32   — per-site "candidate that worked" memory        plugin_screen_player.py  [PATCH 32]
33   — verifySeek trusts demuxer (honest positions)   plugin_screen_player.py  [PATCH 33]
34a-e— yts fixes: TS boundary, ?s= URLs, seeders,     extractors/yts.py        [PATCH 34]
        hash validation; separator rows in list
35   — extract_stream failure returns proper 4-tuple  extractors/hosts.py      [PATCH 35]
36   — proxy confirm needs >50KB traffic (no fake    plugin_screen_player.py  (>50000)
        confirms on starved streams)
37   — aspect probe verdict: eAVSwitch no-op on this
        box; cycle reverted to bar modes
38   — Arabic server names: strip brackets at source  extractors/topcinema.py   [PATCH 39]
        (supersedes 38 in detail screen)
NOT APPLIED: [PATCH 31] (torrentio TMDB key dedup — deliberately skipped)

## KEY REMAPS (current)
Player: OK=pause · 0-9=% seek · ◀▶±10s · ▲▼±60s · RED=subtitle delay ·
YELLOW=quality menu · GREEN=restart+resume · BLUE=subtitles · MENU=menu

## FILES MAP
Reviewed & clean (no bugs found):
  extractors/base.py, net.py, referers.py, registry.py,
  plugin_imagecache.py, novaplay_tracker.py, plugin_health.py
Patched & working: everything in the ledger above, plus (this session)
  extractors/wecima.py, wecima_sarl.py, topcinema.py, egybest.py, hosts.py,
  htmlmedia.py, onlyflix.py, aflaam.py, plugin_gridlist.py,
  plugin_screen_home.py, browser_proxy_v2.py (separate PC service)
Never fully reviewed: plugin.py, plugin_util.py, plugin_downloads.py,
  plugin_tmdb.py, novaplay_subtitles.py, novaplay_proxy.py,
  novaplay_substudio.py, plugin_screen_search.py, plugin_screen_splash.py,
  plugin_common.py, plugin_state.py (read in full but not exhaustively
  bug-hunted beyond what surfaced this session), most other site extractors
  (mywecima partially, many others not at all)

## KNOWN ISSUES / QUIRKS
- topcinema CDN sometimes blocks the box's IP (all direct candidates time
  out; only proxy "confirms" with tiny traffic). Wait it out; other sites
  (egydead, akwam, YTS/TorrServer) unaffected. Diagnostic:
  curl -s --max-time 10 -o /dev/null -w "%{http_code} %{size_download}B\n" \
    "<fresh master.m3u8 from log>" -H "Referer: https://topcinema.io/"
- 720p "_h" streams: resume seeks sometimes don't take (GStreamer drops
  them while buffering). PATCH 33 keeps saved positions honest anyway.
- wecima sites need the browser proxy (Cloudflare) — enable in Settings.
- Dead mirror domains (hls3 .space/.shop/.cyou variants) always fail —
  expected; dead-marking removes them for the session after one failure.

## MAINTENANCE WORKFLOW
1. Notice issue → collect evidence:
   grep -n "SOMETHING" FILE.py
   tail -50 /tmp/arabicplayer.log
2. Paste evidence + question to your assistant.
3. Apply the patch it gives (edit file, or nano + paste).
4. python3 -m py_compile THE_FILE.py
5. init 4 && init 3
6. Test. Paste log lines back if it failed.

## OPEN ITEMS (as of this writing)
- CDN test pending (see KNOWN ISSUES)
- Feature tests pending: quality cap log line, auto-next opening line,
  strip poster swap, RTL rows
- Egydead download-links scraper: screen side complete; needs the site's
  download-section HTML (browser → right-click a download button →
  Inspect → copy that markup) to finish extraction
- Idea backlog: number-key page jumps in lists, search history,
  downloads-manager screen, per-series quality memory

## SESSION: 2026-09-24 (OnlyFlix + Aflaam + home-screen UI + vidsrc-family recursion)
All entries below verified against real captures/logs, not guessed. Backup suffix on disk
matches the tag shown (e.g. hosts.py.bak-h1 for [H1]).

### wecima / topcinema / egybest / mywecima
117 — revert dual-theme GridItem from wecima.py (Wecima-Card only)  extractors/wecima.py
118 — wecima_sarl URL scheme-repair: https:// was becoming          extractors/wecima_sarl.py
        http://s://... (bad substring check)
119 — Stream Recorder: primaryDomain no longer hijacked by ad       background/message-handlers.js
        popups on every pageLoaded event
120 — wecima_sarl secure_stream download gateway decoder            extractors/wecima_sarl.py
        (base64+zlib, tolerant of non-verifying ADLER32)
121 — azrak.mycima.cv download resolver (check_status->start_download) extractors/hosts.py
T1  — topcinema download-links: real markup has href before class   extractors/topcinema.py
        (opposite of assumed order); 8 real providers confirmed
T2  — Streamtape decoy-assignment fix: page ships multiple garbled  extractors/hosts.py
        get_video candidates, only chain-evaluator picks the real one
E1  — EgyBest watch-server extraction: real markup is               extractors/egybest.py
        onclick="loadIframe(this,'URL?url=BASE64')", not .servList
E2  — cybervynx.com dispatch (EgyBest's default server)              extractors/hosts.py
E3  — EgyBest download gateway: real link in <a id="btn"             extractors/hosts.py
        data-url="BASE64">, not the 10s-countdown JS
M1  — stmruby.com dispatch (mywecima embed domain)                   extractors/hosts.py
M2  — mxdrop.top dispatch (MixDrop variant)                          extractors/hosts.py
--  — AISubtitles stale duplicate extractors/ folder synced (was     (folder sync, no file)
        silently shadowing every patch via sys.modules load order)
--  — net.py Brotli/curl_cffi: EgyBest pages were returning garbled  extractors/net.py
        binary because Cloudflare ignores declared Accept-Encoding
        and this box has no Brotli decoder (no compiler); fixed by
        detecting Content-Encoding: br with brotli unavailable and
        falling back to curl_cffi's native decompression

### OnlyFlix (site registry addition + fixes)
O1  — bogus "servers" from related-movie cards: data-url attribute  extractors/onlyflix.py
        collided between the card parser and a legacy server-scan
O2  — TV series pages misclassified as type=episode (the embedded   extractors/onlyflix.py
        "play episode 1" widget's data-player-content-type was
        wrongly read as the PAGE's own type)
O3  — allSeasonsData found 0 episodes: page uses const, regex only  extractors/onlyflix.py
        matched var
O4  — "Best of Year" rail (ofb-card markup) returned 0 items -- a   extractors/onlyflix.py
        third card structure neither existing parser targeted
O5  — "Coming Soon" removed from categories (not browsable -- cards extractors/onlyflix.py
        have no <a href>, notify-me only, nothing to navigate to)

### Aflaam (site registry addition + fixes)
A1  — watch-server list + stream resolution: real /watch/<id>/ link extractors/aflaam.py
        pattern found; stream resolves via the page's own JSON-LD
        VideoObject contentUrl
A2  — bogus "server" pointing at aflaam.com/login (site's own header extractors/aflaam.py
        login icon, picked up by an over-broad data-* scan)
A3  — series pages found 0 episodes: real anchor text is just a     extractors/aflaam.py
        bare number + English title, never contains the Arabic word
        for "episode" "حلقة" or "Episode"

### Home-screen UI
G1  — moveLeft/moveRight: wrap_nav checked before page-change, so   plugin_gridlist.py
        Left/Right could never cross pages when one existed
G2  — MemoizedPixmapCache could get permanently poisoned if a       plugin_gridlist.py
        widget wasn't ready on first set() (cache marked "done"
        with nothing ever painted)
G3  — home grid: 7 columns (was 6), 260px tiles, whole-grid          plugin_gridlist.py,
        centering (was a lopsided 140px gap on every full row)      plugin_screen_home.py
        [HOTFIX same day: missing HOME_CENTER_OFFSET_X import
        crashed the plugin on launch -- caught from a real crash log]
G4  — home grid: health-based sort (ok/unknown>blocked>down) +      plugin_screen_home.py
        24h-recency tiebreak, stable; proactive background health
        sweep (was purely reactive -- dots never appeared before you
        already found a problem manually)
G5  — continue-strip hero backdrop: never had a fallback when an    plugin_screen_home.py
        entry carried no fanart/backdrop (which is always, since
        plugin_state.py never stores one) -- added the same TMDB
        fallback the separate site-detail view already had

### Infrastructure (recursion + entity-decoding)
H1  — resolve_vidsrc_family Stage 6 recursed into resolve_host with extractors/hosts.py
        no depth/cycle guard -- vidapi.xyz iframing itself blew the
        call stack ("maximum recursion depth exceeded"), which then
        poisoned every other fetch on the same thread until it
        unwound. Fixed with a per-thread in-progress-hosts set.
H2  — extract_iframes never HTML-unescaped a src attribute -- a     extractors/htmlmedia.py
        page's iframe src="...&amp;ref=..." was fetched literally,
        with "&amp;" intact, breaking the query string. Generic
        helper; affects any resolver reaching an escaped iframe src.
BP1 — browser_proxy_v2.py (separate PC-side service, not part of    browser_proxy_v2.py
        this plugin): stale Referer header leaked across /fetch
        calls on a reused per-domain context when a later call
        didn't pass one -- confirmed empirically against a real
        headless Chromium instance, not just reasoned from docs.

