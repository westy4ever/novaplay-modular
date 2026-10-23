# NovaPlay — map of the plugin

Where things live, where new things go, and how to check a change before it
reaches the box. (Last updated 2026-10-23.)

Box: Vu+ DUO4K, OpenATV 7.6, Python 3.13. Installed to
`/usr/lib/enigma2/python/Plugins/Extensions/NovaPlay` by `install_novaplay.sh`,
which downloads the repo, keeps `api_keys.conf`, removes `tests/`, compiles
everything and restarts Enigma2.

## How a session flows

```
plugin.py ─► splash ─► Panel home (hub) ─► site screen ─► detail screen ─► player
                         │                                   │
                         ├─ search / favorites / history     ├─ torrents → TorrServer
                         ├─ downloads                        ├─ trailers
                         └─ settings                         └─ download to HDD
```

## Screens

| File | Screen |
|---|---|
| `plugin.py` | Enigma2 entry point; lazy loading, old names re-exported |
| `plugin_screen_splash.py` | splash, preloads modules, opens the hub |
| `plugin_screen_hub.py` | Panel home: sidebar, Continue Watching, site cards, library tiles |
| `plugin_screen_site.py` | one site: categories + poster grid / carousel, badges, paging |
| `plugin_screen_detail.py` + `detail_*.py` | detail page (see below) |
| `plugin_screen_player.py` + `player_*.py` | the player (see below) |
| `plugin_screen_search.py` | search with suggestions and scope |
| `plugin_screen_downloads.py` | downloads manager |
| `plugin_screen_settings.py` | settings (sidebar sections, 3×3 cards) |
| `plugin_screen_family.py` | version picker for grouped sites (EgyDead, Akwam…) |
| `novaplay_diagnostics.py` | Settings → Tools → Diagnostics |
| `plugin_player_panel.py` | the player's column panels (Quality/Server/Speed, Audio/Subs/More) |
| `novaplay_choice.py` | NovaPlay-styled message box and choice list (used everywhere) |

### Detail screen parts

| File | Part |
|---|---|
| `plugin_screen_detail.py` | layout (both styles), settings, `__init__`, nav keys |
| `detail_header.py` | title fit, tagline, details chip, facts, cast, poster strip, backdrop, `_onLoaded` |
| `detail_band.py` | Modern style's server / episode cards band |
| `detail_list.py` | loading, the list, OK / EXIT, favorites, TMDb refresh, closing |
| `detail_torrent.py` | quality tabs + order, TorrServer add, file pick, buffering |
| `detail_play.py` | extraction → quality choices → `_onStreamFound` → player |
| `detail_trailer.py` | trailers |
| `detail_downloads.py` | download to HDD |
| `detail_base.py` | imports, skins, trailer cache, picture helpers |

### Player parts

| File | Part |
|---|---|
| `plugin_screen_player.py` | layout, settings, `__init__`, `_play()` |
| `player_osd.py` | overlay: show / hide / slide, times, seek bar + bubble, pause sign, toasts |
| `player_fallback.py` | trying playback methods in turn, stall watchdog, end of film |
| `player_switch.py` | method order, quality / server / speed switching |
| `player_controls.py` | remote keys, pause, seek, restart, exit |
| `player_autonext.py` | next-episode card and countdown |
| `player_menus.py` | audio, sleep, recording, MENU panel, chapters, aspect |
| `player_subs.py` | subtitles panel, online search, sync studio |
| `player_base.py` | imports, `_build_remote_play_candidates`, service capture / restore |

The parts are mixin classes (`class PlayerOSD(object)`), combined in the main
class. They reach each other through `self`. The player's private methods are
named `_pl_*` (not `__*`), because Python renames `__name` per class and that
breaks across files.

## Shared modules

| File | What |
|---|---|
| `plugin_common.py` | `my_log`, `my_debug`, `set_play_id`, version |
| `plugin_state.py` | settings (`_get_config` / `_set_config`), favorites, history, positions |
| `plugin_util.py` | text helpers (wrap, single line, strip year / Arabic), plot picking, server sort |
| `plugin_gridlist.py` | `sc()`, `scale_skin_xml()`, site icons, Animations setting (`anim_*`) |
| `novaplay_ui.py` | `UiHelpers` mixin: `_color`, `_mr` (cached move/resize) |
| `novaplay_cardlist.py` | card-style list rows (subtitles, folder browser, diagnostics) |
| `plugin_toast.py` | pop-up toasts |
| `plugin_rowzoom.py` | smooth row zoom |
| `plugin_widgets.py` | `StreamList` (detail Classic list) |
| `plugin_tmdb.py` | TMDb: metadata, cast, tagline, trailers (English unless Arabic original) |
| `plugin_imagecache.py` | async poster / artwork cache |
| `plugin_health.py` | per-site health (Working / Needs proxy / Down) |
| `plugin_server_rank.py`, `plugin_play_memory.py` | which servers / play methods worked before |
| `plugin_watched.py` | watched / in-progress state |
| `plugin_downloads.py`, `novaplay_transmission.py` | download engine, Transmission |
| `novaplay_proxy.py`, `novaplay_cffi_helper.py` | local streaming proxy, curl_cffi helper process |
| `novaplay_subtitles.py`, `novaplay_substudio.py`, `novaplay_autosub.py` | subtitles |
| `novaplay_tracker.py` | live playback position |
| `novaplay_fonts.py` | Arabic fallback font while NovaPlay is open |
| `novaplay_kbfix.py` | keyboard Google suggestions fix (in memory) |
| `novaplay_thread.py` | `callInMainThread` |

## Sites and hosts (`extractors/`)

* One file per site, a class based on `BaseExtractor` with `get_categories`,
  `get_category_items(url, page)`, `search(q)`, `get_page(url)`,
  `extract_stream(url)`.
* Registered in `extractors/registry.py` (`_SITE_REGISTRY`: class, title,
  tagline). The hub lists what is registered.
* `extractors/net.py`: `fetch()` / `fetch_json()` (curl_cffi for Cloudflare,
  retries, cookies) and the log. Everything should fetch through these.
* `extractors/hosts.py`: video-host resolvers (Mixdrop, Streamtape, …). Large;
  split a host family out the next time it is fixed.

**Add a site:** new `extractors/<site>.py` + registry entry + logo
`images/<site>.png`. Test it live on the box: `python3 run_site_test.py <site>`.

## Logging

`/tmp/novaplay.log` (2 MB, previous one kept as `.1`). Every line:

```
[2026-10-23 21:04:12] [player_fallback._pl_playNext] #P7K Play attempt: 5002 exteplayer3
```

* `[module.function]`: where the line came from (added automatically).
* `#P7K`: the playback it belongs to (`playback start` … `playback end`).
  `grep "#P7K" /tmp/novaplay.log` gives one attempt.
* Settings → Tools → **Debug Log**: also one line per web request
  (`web <url> -> 51234 bytes (0.8s)`). Use `my_debug()` for lines that should
  only appear then.

## Checking a change

On a PC, from the repo (no box needed):

```
python3 tests/run_checks.py
```

1. every `.py` compiles
2. every screen builds with fake Enigma2 modules; widgets in code and skin
   match; no lambda in `onLayoutFinish` (Enigma2 exec()s it → crash)
3. logic tests (year stripping, runtime, torrent order, player times, …)
4. sites played back from recordings in `tests/fixtures/`

Recording a site for (4), on the box:

```
python3 run_site_test.py cinejoy --record
# -> /tmp/novaplay_fixtures/cinejoy.json.gz  → copy into tests/fixtures/
```

When the site itself changes, record it again.

## Settings keys worth knowing

`screen_style` (classic / modern), `layout_style` (grid / carousel),
`anim_mode` (full / light / off), `trailer_mode`, `torrent_quality`,
`torrserver_url`, `debug_log`, `watched_poster_bw`, `tmdb_api_key`,
`browser_proxy`.
