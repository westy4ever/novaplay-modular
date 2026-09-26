#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PATCH O4 - the "/best/{movies,tv-shows}/{YEAR}/" pages (a real, documented site category)
returned 0 items. Confirmed against a real capture (onlyflix.to/best/movies/2026/,
2026-09-24): these "Best of Year" rail pages use a completely different card structure
("ofb-card"/"ofb-card-link") from both the primary ("of-title-card-v2") and legacy
("of-media-card") parsers -- neither one's target class appears anywhere on this page type.

Real confirmed structure per card:
  <article class="ofb-card">
    <a class="ofb-card-link" href="https://onlyflix.to/i-was-a-stranger/">
    <img src=".../posters/1487813_medium.jpg" alt="I Was a Stranger">
    <button class="ofb-card-play" data-post-id="1487813" data-kind="movies"
            data-content-type="movie" data-title="I Was a Stranger"
            data-poster-url="...">
    <span class="ofb-card-rating ...">★ 9.3</span>
    <span class="ofb-card-hover__meta">
      <span class="ofb-card-hover__rating ...">★ 9.3</span>
      <span>1h 43m</span>
      <span>2026</span>
    </span>
    <span class="ofb-card-hover__genres">Drama, Thriller</span>
  </article>

Adds a third parser (_extract_cards_best_rail) targeting this structure, tried when both
_extract_cards and _extract_cards_v1 come back empty. Same output shape as the other two
parsers, so nothing downstream needs to change.

Run from the plugin folder:  python3 apply_onlyflix_best_rail_fix.py
Idempotent, all-or-nothing, CRLF-safe, backup: extractors/onlyflix.py.bak-o4
"""
import sys
import shutil
import py_compile

OF = "extractors/onlyflix.py"

NEW_METHOD = r'''    def _extract_cards_best_rail(self, html, max_items=200):
        """
        [PATCH O4] "Best of Year" rail pages (/best/{movies,tv-shows}/{YEAR}/, a real,
        documented category) use a third card structure -- confirmed against a real
        capture, neither of-title-card-v2 nor of-media-card appear on this page type at
        all. Real structure per card (verbatim):

          <article class="ofb-card">
            <a class="ofb-card-link" href="https://onlyflix.to/i-was-a-stranger/">
            <img src=".../posters/1487813_medium.jpg" alt="I Was a Stranger">
            <button class="ofb-card-play" data-post-id="1487813" data-kind="movies"
                    data-content-type="movie" data-title="I Was a Stranger"
                    data-poster-url="...">
            <span class="ofb-card-rating ...">★ 9.3</span>
            <span class="ofb-card-hover__meta">
              <span class="ofb-card-hover__rating ...">★ 9.3</span>
              <span>1h 43m</span>
              <span>2026</span>
            </span>
            <span class="ofb-card-hover__genres">Drama, Thriller</span>
          </article>
        """
        items = []
        seen = set()
        for block_m in re.finditer(
                r'<article[^>]+class="[^"]*\bofb-card\b[^"]*"[^>]*>(.*?)</article>',
                html, re.S | re.I):
            block = block_m.group(1)

            url_m = re.search(r'class="[^"]*ofb-card-link[^"]*"[^>]*href="([^"]+)"',
                              block, re.I)
            if not url_m:
                continue
            url = self._full_url(url_m.group(1))
            if not url or url in seen:
                continue

            title_m = re.search(r'data-title="([^"]+)"', block, re.I)
            if not title_m:
                title_m = re.search(r'<img[^>]+alt="([^"]+)"', block, re.I)
            title = self._clean_title(html_unescape(title_m.group(1))) if title_m else ""
            if not title:
                continue
            seen.add(url)

            poster_m = re.search(r'data-poster-url="([^"]+)"', block, re.I)
            if not poster_m:
                poster_m = re.search(r'<img[^>]+src="([^"]+)"', block, re.I)
            poster = poster_m.group(1) if poster_m else ""

            kind_m = re.search(r'data-content-type="([^"]+)"', block, re.I)
            if not kind_m:
                kind_m = re.search(r'data-kind="([^"]+)"', block, re.I)
            kind = (kind_m.group(1) if kind_m else "").lower()
            item_type = "series" if kind in ("tvshows", "tv", "series") else "movie"

            rating_m = re.search(r'class="[^"]*ofb-card-rating[^"]*"[^>]*>[^\d]*([\d.]+)',
                                 block, re.I)
            rating = rating_m.group(1) if rating_m else ""

            meta_m = re.search(
                r'class="[^"]*ofb-card-hover__meta[^"]*"[^>]*>(.*?)'
                r'(?=<span class="[^"]*ofb-card-hover__genres)',
                block, re.S | re.I)
            runtime = year = ""
            if meta_m:
                spans = re.findall(r'<span[^>]*>([^<]*)</span>', meta_m.group(1))
                spans = [s.strip() for s in spans if s.strip()]
                for s in spans:
                    if re.match(r'^\d{4}$', s):
                        year = s
                    elif re.search(r'\d', s) and not year:
                        runtime = s

            genres_m = re.search(
                r'class="[^"]*ofb-card-hover__genres[^"]*"[^>]*>([^<]*)<',
                block, re.I)
            genres = genres_m.group(1).strip() if genres_m else ""

            items.append({
                "title": title,
                "url": url,
                "poster": poster,
                "plot": "",
                "label": "",
                "rating": rating,
                "runtime": runtime,
                "genres": genres,
                "year": year,
                "type": item_type,
                "_action": "details",
            })
            if len(items) >= max_items:
                break
        return items

'''

OLD_DISPATCH = '''        if not items:
            return self._extract_cards_v1(html, max_items=max_items)
        return items'''

NEW_DISPATCH = '''        if not items:
            items = self._extract_cards_v1(html, max_items=max_items)
        if not items:
            items = self._extract_cards_best_rail(html, max_items=max_items)  # [PATCH O4]
        return items'''


def main():
    raw = open(OF, "rb").read().decode("utf-8")
    crlf = "\r\n" in raw
    s = raw.replace("\r\n", "\n")
    if "[PATCH O4]" in s:
        sys.exit("already applied - nothing to do")

    anchor = "    # ── Legacy card parser (of-media-card, kept as fallback) ──────────────\n    def _extract_cards_v1(self, html, max_items=200):"
    if s.count(anchor) != 1:
        sys.exit("ABORT (nothing written): method anchor found %d times (expected 1)" % s.count(anchor))
    s = s.replace(anchor, NEW_METHOD + anchor, 1)

    if s.count(OLD_DISPATCH) != 1:
        sys.exit("ABORT (nothing written): dispatch anchor found %d times (expected 1)" % s.count(OLD_DISPATCH))
    s = s.replace(OLD_DISPATCH, NEW_DISPATCH, 1)

    shutil.copy(OF, OF + ".bak-o4")
    open(OF, "wb").write((s.replace("\n", "\r\n") if crlf else s).encode("utf-8"))
    try:
        py_compile.compile(OF, doraise=True)
    except Exception as e:
        shutil.copy(OF + ".bak-o4", OF)
        sys.exit("COMPILE FAILED, file restored: %s" % e)
    print("OK: 'Best of Year' rail pages now correctly parsed (backup: %s.bak-o4)" % OF)


main()
