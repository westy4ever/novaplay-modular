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
  extractors/base.py, net.py, htmlmedia.py, referers.py, registry.py,
  plugin_imagecache.py, novaplay_tracker.py, hosts.py (after PATCH 35)
Patched & working: everything in the ledger above
Never fully reviewed: plugin.py, plugin_util.py, plugin_downloads.py,
  plugin_health.py, plugin_tmdb.py, novaplay_subtitles.py, novaplay_proxy.py,
  novaplay_substudio.py, plugin_screen_search.py, plugin_screen_splash.py,
  plugin_common.py, most site extractors (egydead partially, others not at all)

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
