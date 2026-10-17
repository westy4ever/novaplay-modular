# NovaPlay Site Test Guide

_Last updated 2026-10-17_

## What it does

`run_site_test.py` checks a site the way the plugin uses it, against the real website, and tells you which step breaks. It runs the same extractor code the plugin runs, step by step, and finishes by downloading the first few kilobytes of the actual video with the headers the player would send.

The chain it walks:

1. **Categories**: `get_categories()` returns the site's menu.
2. **Items**: `get_category_items()` on the first category that has titles, opening sub-folders if a category holds only folders.
3. **Search**: `search()` with the first word of the first title found (or your own word).
4. **Page**: `get_page()` on the first title, then into season and episode until servers appear.
5. **Servers**: `extract_stream()` on each server, with the site's cookies attached, as the plugin does.
6. **Stream**: fetches the start of the video. For HLS it checks the master playlist, the quality playlist and the first segment.

```
Categories ─> Items ─> Page ─> Servers ─> Resolve stream ─> Fetch video start ─> PASS
               ↺          ↺        ↑            │                   │
       sub-folder:   season /      │     magnet: → TorrServer       │
       open first    episode:      │     (counts as PASS)           │
       (≤3 levels)   open first    └──── not playable: next server ─┘
```

A failure early in the chain stops the test there; a dead server only moves it on to the next one.

Nothing is played, saved or changed. Enigma2 doesn't need to be stopped, and the plugin can stay open.

## Install or update NovaPlay

The test script comes with NovaPlay, so install the plugin first. Open a terminal on the box (see step 2 below), paste this one line and press Enter:

```
wget -qO /tmp/install_novaplay.sh https://raw.githubusercontent.com/westy4ever/novaplay-modular/main/install_novaplay.sh && sh /tmp/install_novaplay.sh
```

It downloads the latest NovaPlay from GitHub, backs up the current install, keeps your `api_keys.conf`, removes the old AdvancedArabicPlayer plugin if it's there (your settings, favourites and history carry over), checks every file and restarts Enigma2. Run the same line again any time to update.

**Box without internet access?** On your PC, open the GitHub page, click **Code > Download ZIP**, copy the ZIP and `install_novaplay.sh` to `/tmp` on the box with FileZilla or WinSCP, then run:

```
sh /tmp/install_novaplay.sh /tmp/novaplay-modular-main.zip
```

Add `--no-restart` to either command to skip the restart. Backups go to `/media/hdd/NovaPlay/backups` (or `/home/root/NovaPlay-backups` without a hard disk).

## Before you start

The script lives in the plugin folder next to `run_tests.py` and `run_release_gate.py`. Open a terminal on the box (SSH or Telnet) and go there first:

```
cd /usr/lib/enigma2/python/Plugins/Extensions/NovaPlay
```

- **Get or update it:** it comes with NovaPlay. The NovaPlay installer (`install_novaplay.sh`) puts it in the plugin folder, and running the installer again updates it with the rest of the plugin.
- **No restart needed:** it loads the extractors fresh each time it runs. Restart Enigma2 (`init 4 && init 3`) only for the plugin's own screens to pick up changed files.
- **Same settings as the plugin:** it applies your browser-proxy setting from the plugin's Settings, so Cloudflare-protected sites behave as they do on screen.
- **Python 3:** run it with `python3`. It uses only what the plugin already uses.

## How to run it, step by step

You need the box's IP address and a PC on the same network. Find the IP in the box's network settings menu or in your router's list of devices, for example `192.168.1.50`.

1. **Make sure NovaPlay is installed.** The installer adds `run_site_test.py` automatically. To update only this script by hand: open FileZilla or WinSCP, connect to the box's IP as `root` (FTP, or SFTP on port 22), go to `/usr/lib/enigma2/python/Plugins/Extensions/NovaPlay/` and drag the new `run_site_test.py` into it.
2. **Open a terminal on the box.** On Windows, open PuTTY, type the box's IP, choose SSH (port 22) or Telnet (port 23), and log in as `root`. On Windows 10 and 11 you can also open Terminal and type `ssh root@192.168.1.50`.
3. **Go to the plugin folder.** Type `cd /usr/lib/enigma2/python/Plugins/Extensions/NovaPlay` and press Enter.
4. **See the site names.** Type `python3 run_site_test.py --list` and press Enter. The first column is the name to use.
5. **Run a test.** Type `python3 run_site_test.py sflix_pro` (or any name from the list) and press Enter. Each step prints as soon as it finishes. A full test takes from about 10 seconds to 2 minutes, and `--all` takes longer.
6. **Stop it early, if needed.** Press `Ctrl+C`.
7. **Read the report again later.** Type `cat /tmp/site_test_sflix_pro.txt` to print the saved report.
8. **Copy the report to your PC.** In FileZilla or WinSCP, go to `/tmp` on the box and drag `site_test_sflix_pro.txt` (or `site_test_ALL.txt`) to your PC. Attach it in the chat when you want help.

If you see `No such file or directory`, the script isn't in that folder yet: repeat step 1. If you see `python3: not found`, try `python` instead of `python3`.

## Commands

Give it either a site key or a path to an extractor file, plus any options.

| Command | What it does |
|---|---|
| `python3 run_site_test.py --list` | Shows every site key, for example `sflix_pro`, `egydead`, `yts` |
| `python3 run_site_test.py sflix_pro` | Full test of one site: categories to stream |
| `python3 run_site_test.py --all` | Quick check of every site: categories and items only, then a summary |
| `python3 run_site_test.py extractors/new_site.py` | Tests a file that isn't registered yet, such as a new or generated extractor |
| `... egydead --search "batman"` | Tests search with your word instead of the automatic one |
| `... sflix_pro --page sflix_movie_1153576` | Skips browsing and tests one title directly, using the URL the extractor gives that title |
| `... sflix_pro --servers 8` | Tries up to 8 servers instead of the default 5 |
| `... sflix_pro --all-servers` | Keeps testing after a working server, to see which servers work |

Options can be combined, for example `python3 run_site_test.py cineby --page cineby_movie_969681 --all-servers`.

By default it stops at the first server that plays, because that answers "does this site work". Use `--all-servers` when you want the full server picture, for example to decide the server order.

## Reading the report

Each step prints one status line, its time in seconds, and indented details. The last line gives the verdict.

| Status | Meaning | Counts as |
|---|---|---|
| `PASS` | The step worked | OK |
| `WARN` | It worked, but something is missing or odd (no posters, empty search, downloads only) | Warning |
| `BAD` | One server didn't work. Sites with several servers usually have a few dead ones | Warning |
| `FAIL` | The site can't be used past this point (no items, no page, or no server plays) | Failure |

**The verdict:**

- `PASSED`: every step worked.
- `PASSED with warnings`: the site works and at least one server plays, but some things need a look.
- `FAILED`: a user would hit a dead end. The `FAIL` line above it says where.

**An example, step by step** (a series on a site with four servers):

```
[PASS] get_categories() (0.4s)
         12 categories; testing 'Action'
[WARN] get_category_items('Action') (2.1s)
         24 items (+1 navigation entries)
         first: 'Batman Begins' -> https://site/series/batman
         3 of 24 item(s) have no poster
[PASS] search('Batman') (1.8s)
[PASS] get_page(https://site/series/batman) (1.2s)
         title: 'Batman Begins' | type: series | servers: 0 | sub-items: 2
         -> opening first sub-item (season/episode)
[PASS] get_page(https://site/season/1) (0.9s)
[PASS] get_page(https://site/episode/1) (1.1s)
         title: 'E1' | type: episode | servers: 4 | sub-items: 0
[BAD ] server 1/4: Dood (3.0s)
         extract_stream found no stream
[BAD ] server 2/4: Uqload (2.4s)
         stream request failed: HTTP 403 Forbidden
         headers sent: User-Agent
[PASS] server 3/4: VidHide (2.2s)
         resolved [720p]: https://cdn.../master.m3u8|Referer=https://...
         HLS OK: 3 variant(s), 512 segment(s), first segment HTTP 200
         (1 more server(s) not tested -- add --all-servers to test them)
RESULT: PASSED with warnings  (0 fail, 3 warn)
saved: /tmp/site_test_egydead.txt
```

Reading it: browsing works, a few posters are missing, the series opened down to an episode, and 2 of the first 3 servers are dead. The third plays, so the site works for users. Server 2 failed with only `User-Agent` sent, which points to a missing `Referer` in that host's resolver.

**Files and exit codes:**

- Each run saves its report to `/tmp/site_test_<site>.txt`. `--all` saves the summary to `/tmp/site_test_ALL.txt`.
- The plugin's own detailed log keeps going to `/tmp/novaplay.log`. Look there for the reason behind a failed step.
- Exit code `0` means passed (with or without warnings), `1` means failed, and `2` means the command was wrong (for example an unknown site key).

## How each site type is handled

The test follows the same rules as the plugin's screens, so all 31 registered sites are covered.

| Site type | Examples | How the test treats it |
|---|---|---|
| Arabic scraper sites | EgyDead, WeCima, Akwam, Arabseed, Shahid4u, CimaWebas | Normal chain. The site's cookies are attached to the stream, as the plugin does |
| TMDB-based sites | SFlix, Cineby, Vidsrc, IMDB.su | Normal chain. Categories come from TMDB, servers are embed players |
| Torrent sites | YTS, Torrentio | `magnet:` servers count as working and go to TorrServer, as in the plugin. Nothing is downloaded |
| Series | any site | Follows series, then season, then episode (up to 4 levels), opening only the types the detail screen lists: `series`, `season`, `episode` |
| Categories with sub-folders | genre or collection menus | Opens the first sub-folder (up to 3 levels) when a category holds no titles |
| Download-only pages | some movie pages | `WARN`, not `FAIL`: the blue download key still works in the plugin |
| Cloudflare-protected sites | several Arabic sites | Uses your browser-proxy setting, so results match what you see on screen |
| Site families | EgyDead / Coupons / CA, Arabseed x3, Akwam x2 | Each member is its own site key. Test them one by one, or use `--all` |

Next-page tiles ("Next", "التالي", "Page 2") are never counted as titles.

## Common results and what to do

| Message | Likely cause | What to do |
|---|---|---|
| `returned an empty list` (items) | Site down, blocked, moved domain, or its page layout changed | Open the site on your PC. If it loads, capture it with Stream Recorder Pro and send the JSON |
| `no usable categories` | Extractor broken at the start, or site unreachable | Check `/tmp/novaplay.log` for the first error |
| `timed out after 90s` | Site extremely slow or hanging | Run it again. If it repeats, the site may need the browser proxy |
| `extract_stream found no stream` | That host changed or has no resolver | Normal for one server. If every server shows it, capture a working server in the browser |
| `HTTP 403 Forbidden`, `headers sent: User-Agent` | Host needs a `Referer` or `Origin` the resolver doesn't send | Send me the report: usually a one-line resolver fix |
| `got an HTML page instead of video` | Link expired, blocked, or wrong headers | Re-run. If it repeats, send the report |
| `first segment failed` | Playlist works, video pieces are blocked | Usually a header the segments need. Send the report |
| `no stream servers, but N download link(s)` | Page has downloads only | Fine for users. Downloads aren't tested |
| `no servers and nothing to open` | Page parsed, but servers weren't found | Layout changed on the watch page. Capture it |
| `none of the N server(s) tried produced a playable stream` | Every server tried is dead | Try `--servers 10`. If still none, the site needs work |
| `HTTP Error 404` or `Connection refused` in a server | That mirror is gone | Ignore if another server plays |
| `unknown site key` | Typo in the key | Use `--list` to see the exact keys |

If many sites fail the same way, check the box first: in NovaPlay open **Settings**, press **Menu**, and run **System Diagnostics**. It shows whether the internet, the browser proxy, TorrServer and each optional package (curl_cffi for Cloudflare sites, Pillow, yt-dlp and others) are working. Anything missing can be added with **Install Missing Dependencies** from the same menu.

## Workflows

**Weekly health check (about 5 minutes)**

1. Run `python3 run_site_test.py --all`.
2. Look at the summary. Any site marked `FAIL` has broken browsing.
3. Run the full test on each failed site, for example `python3 run_site_test.py egydead`, to see which step breaks.

**After changing or updating a file**

1. Copy the new file onto the box.
2. Run the full test for the site it affects, for example `python3 run_site_test.py sflix_pro --all-servers`.
3. When it passes, restart Enigma2 (`init 4 && init 3`) and try it on screen.

**Testing a new or generated extractor**

1. Put the file in `extractors/`, for example `extractors/new_site.py`. It doesn't need to be in the registry yet.
2. Run `python3 run_site_test.py extractors/new_site.py`.
3. Fix whatever the first `FAIL` points at, then run it again.
4. Register it in `extractors/registry.py` only once it passes.

**Sending results for help**

- Send `/tmp/site_test_<site>.txt`. It's short and has everything the test saw.
- For a failed server, also send the last 100 lines of the plugin log: `tail -100 /tmp/novaplay.log`.
- If the site needs a new resolver, add a Stream Recorder Pro capture of the working server from your PC: `networkLog`, `xhrBodies` and `frameDocuments`.

## What it doesn't test

- **Downloads:** it reports download links but doesn't download them.
- **Playback on the box:** it confirms the stream is reachable and is real video, not that the box's player decodes it. A `PASS` that won't play on screen points to the player or codec side.
- **Every title:** it tests the first title of the first working category. Another title can still have a dead server.
- **Subtitles, TMDB posters and the home screen layout.**
- **TorrServer:** magnet links are counted as working, but TorrServer itself isn't contacted.
- **Pages beyond the first** of a category, and every season of a series: it opens only the first of each.
