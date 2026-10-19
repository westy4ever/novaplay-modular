# Adding and Testing a New Site

_Last updated 2026-10-19_

How a new streaming site gets into NovaPlay, from the first browser recording to the box, and how to keep it working afterwards. The tools used here are described in their own guides: **Stream Recorder Pro** (`docs/GUIDE.md` in its repo) and **run_site_test.py** (`docs/SITE_TEST_GUIDE.md`).

## The whole path in short

1. **Record** the site in your browser with Stream Recorder Pro (4.5.0 or newer).
2. **Send** the exported files (and a few HTML snapshots) in the chat.
3. **Build**: the extractor file is written from the recording, never from guesses.
4. **Register** it in `extractors/registry.py` and add a logo.
5. **Test** on the box with `run_site_test.py`, then play on the TV.
6. **Maintain**: run `--all` now and then; sites change domains and layouts.

---

## 1. Record the site

One recording per site. Use a normal Chrome/Brave tab, not Incognito.

1. Open the Stream Recorder Pro popup, check **Record: Current Tab** and **Auto-snapshot** are on, **XHR/Fetch only** off, then **Start**.
2. Open the site's **home page** and scroll down once, so every row loads.
3. Open **one category** (for example Movies), and go to **page 2** once.
4. **Type a title in the site's search box** (for example "batman") and open the results. Since 4.5.0 the extension records what is typed in search boxes (never passwords or other fields), so the generator finds the real search request.
5. Open **one movie** page.
6. Press **Watch / Play**, pick a **server**, and let the video **really play for 20–30 seconds**. The stream requests only happen once it plays. Close any ad tab and press play again if needed.
7. **Switch to a second server** in the player and let it play a few seconds. If there are qualities (1080p/720p) or languages (Sub/Dub), switch once.
8. **For series**: open one series, change the season once, and play one episode.
9. **Stop**, wait a second, then export.

**Send these files** (the export shows them with their sizes):

| File | Why |
|---|---|
| `networkLog` (all parts) | Every request, its headers (Referer, tokens) and order |
| `xhrBodies` (all parts) | The API replies: lists, servers, stream links |
| `scriptBodies` | The site's JavaScript: how requests are signed or replies decrypted |
| `frameDocuments` | Player pages inside iframes, and the open server menus |
| `userJourney` | What you clicked, in order |

Also save **HTML snapshots** (Snapshot button) of: the home page, a category page, a movie page, and the player page with its server menu open.

You can skip `actions` and `contentPipeline`.

---

## 2. What kind of site is it?

The recording decides how the extractor is built. Stream Recorder Pro's **Full Extractor** button writes these findings at the top of its draft file (4.5.0+).

| Site type | How to recognise it | How it's built | Example in NovaPlay |
|---|---|---|---|
| **TMDB-numbered** | Pages like `/movie/550`, `/tv/1399`; TMDB posters | Lists and search from TMDB; servers built from the TMDB id | `sflix_pro.py`, `movy.py`, `cineby.py` |
| **Next.js** | `<script id="__NEXT_DATA__">` in the page; `/_next/data/…json` requests | Read the JSON, not the HTML; fetch the build id from the home page each time | `movy.py` (home rows) |
| **HTML / WordPress** (most Arabic sites) | Cards in the page HTML; `/category/…/page/2/` | Regex on the HTML; domain list for when the site moves | `egydead.py`, `arabseed.py`, `wecima.py` |
| **JSON API** | Lists come from `/api/…` replies | Call the API directly | `egydead_ca.py` |
| **Same engine as an existing site** | Same card classes and URLs as a site already in NovaPlay | Subclass it and change only the domain list / categories | `arabseed_liv.py`, `arabseed_original.py` |

**Servers and streams** fall into three cases:

- **Embed links to a known host** (VidSrc-Embed, EmbedMaster, VidFast, VidCore, MoviesAPI, DoodStream, StreamWish…): nothing new needed. NovaPlay's `extractors/hosts.py` already resolves them.
- **A new host**: a resolver is added to `hosts.py` and registered in its `HOST_RESOLVERS` table.
- **The site's own API, possibly encrypted** (like Movy's `/seed` + `/<city>/sources`): the decoder is ported from the site's script (see `extractors/movy_api.py`).

---

## 3. What an extractor file contains

A file in `extractors/`, one class that subclasses `BaseExtractor`, with these methods (exact signatures, the plugin calls them this way):

| Method | Returns |
|---|---|
| `get_categories(self, mtype="movie")` | List of `{"title", "url", "type": "category"}` rows; `"type": "separator"` for headings |
| `get_category_items(self, url, page=1)` | List of items `{"title", "url", "poster", "type": "movie"/"series", "year", "rating"}`, plus a last `{"title": "➡️ Page 2 (Next)", "url": …, "type": "category"}` row when there are more pages |
| `search(self, query, page=1)` | Same item list as above |
| `get_page(self, url, m_type=None)` | `{"title", "plot", "poster", "year", "rating", "genres", "type", "servers": [{"name", "url", "quality"}], "items": [...seasons or episodes...]}` |
| `extract_stream(self, url)` | Usually not needed: the default sends server URLs to `hosts.py` |

Rules that have caught us before:

- `fetch()` returns **two values**: `html, final_url = fetch(url, referer=…)`. Using the result directly as text is a silent bug (it made Movy's home rows fall back to TMDB).
- Posters in **webp** don't show on the box; use the JPEG (TMDB posters: `https://image.tmdb.org/t/p/w342/…`).
- A site that changes domains keeps a **domain list**; a domain that answers with a Cloudflare page, a parking page or an "account suspended" page is not a working domain.
- Don't scrape a trailer (`imdb-video`, YouTube) as the film's stream.

---

## 4. Register the site

In `extractors/registry.py`:

1. **Import** the class: `from .newsite import NewSiteExtractor`
2. **Add it to `_SITE_REGISTRY`**: `"newsite": {"class": NewSiteExtractor, "title": "NewSite", "tagline": "…", "short": "…"},`
3. **Add the key to `_SEARCH_SITE_ORDER`** (so "search all" includes it).
4. **Add the key to `_HOME_SITE_ORDER`** for its own tile, **or** to a list in `_SITE_FAMILIES` to share a tile with sister sites (for example a new ArabSeed mirror goes into the `"arabseed"` family).

**Logo**: `images/<key>.png`, **520 × 184** pixels, PNG.

The installer copies the whole folder, so no install-script change is needed.

---

## 5. Test it

### On the box

Update first, then go to the plugin folder:

```
wget -qO /tmp/install_novaplay.sh https://raw.githubusercontent.com/westy4ever/novaplay-modular/main/install_novaplay.sh && sh /tmp/install_novaplay.sh
cd /usr/lib/enigma2/python/Plugins/Extensions/NovaPlay
```

| Step | Command | Pass when |
|---|---|---|
| Whole site, first pass | `python3 run_site_test.py newsite` | Categories, items, a page with servers, and a stream that loads |
| Search | `python3 run_site_test.py newsite --search "batman"` | Results come back |
| One title, every server | `python3 run_site_test.py newsite --page <item url> --all-servers` | Most servers PASS; failures are on the site's side |
| A file not registered yet | `python3 run_site_test.py extractors/newsite.py` | Same as above |
| All sites, quick | `python3 run_site_test.py --all` | Only categories and lists, one line per site at the end |

The report is saved in `/tmp/site_test_<key>.txt`. When something fails, the details are in the logs:

```
grep -i "newsite" /tmp/novaplay.log | tail -60
tail -80 /tmp/novaplay_proxy.log
```

### On the TV

1. Open the site's tile: the categories and the posters show.
2. Search for a title.
3. Open a movie: details, plot and servers show.
4. Press OK on a server: the Quality | Server panel opens; pick a quality and it plays.
5. In the player: **Yellow** switch quality and server, **Red** next server, **Blue** subtitles, **0–9** jump, then exit and reopen to check the resume point.
6. For series: open a season, play an episode, and check **Green** offers the next episode near the end.

---

## 6. When something fails

| What you see | Where | What it means | What to do |
|---|---|---|---|
| `403 Forbidden` + "Cloudflare challenge … proxy is NOT configured" | novaplay.log | The site needs a real browser to pass Cloudflare | Set up the browser proxy in NovaPlay Settings, or use a sister site |
| `curl_cffi OK: …/cgi-sys/suspendedpage.cgi` | novaplay.log | The site's hosting account is suspended | Find its new domain; otherwise remove the site |
| Every category returns an empty list, HTTP 200 | test report | The page layout changed | Send a fresh HTML snapshot of a category page |
| The domain answers with a redirect to an unknown host | novaplay.log | The site moved | Add the new domain to its domain list |
| Servers found but `stream request failed: HTTP 500/403` | test report | That host is down or wants different headers | Try other servers; a capture of that server playing shows the headers |
| `decrypt failed` | novaplay.log | The site changed its encryption | New Stream Recorder Pro recording of a title playing |
| Plays in the test but not on the TV | novaplay_proxy.log | Playlist/segment format (for example fMP4) | Send both logs; the local proxy may need to convert it |
| `extract_stream error: timed out after 90s` | test report | A resolver waited too long | Send the log lines for that server |

---

## 7. Keep sites working

- Run `python3 run_site_test.py --all` every few weeks, or when a tile looks empty, and send `/tmp/site_test_ALL.txt` with any FAIL lines.
- Arabic sites change domains often: when one stops, look for its new address (the site's Telegram/Facebook page usually posts it) and send it.
- When a site's servers stop playing but its lists still work, a new recording of one title playing is the fastest way to fix it.
