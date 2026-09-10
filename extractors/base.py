# -*- coding: utf-8 -*-
"""base.py — BaseExtractor + re-export shim.

PHASE-2 SPLIT: the implementation moved to:
    net.py         — fetch/fetch_json, curl_cffi, browser proxy, cookies,
                     CF detection, validate_manifest
    referers.py    — THE single referer table + get_referer()
    htmlmedia.py   — m3u8/mp4 finders, quality variants, placeholders
    hosts.py       — every resolve_* host resolver, the ordered
                     dispatcher, resolve_iframe_chain, extract_stream

This module keeps the ORIGINAL public surface so every extractor and
every consumer imports EXACTLY as before:

    from .base import fetch, log, resolve_host, extract_stream, ...

Nothing outside this package changed. Adding a site is still: an
extractor file + a registry entry.

If some extractor imports a name not re-exported below, the ImportError
names it on first use — add one line here. (Names below are the
complete set observed across all 17 extractors plus the player,
proxy, downloads, subtitle and diagnostics modules.)
"""

import re
from urllib.parse import urljoin

# ─── net.py surface ────────────────────────────────────────────────────────
from .net import (
    log, fetch, fetch_json,
    UA, TIMEOUT,
    set_browser_proxy, get_proxy_used,
    set_curl_failed_needs_proxy, get_curl_failed_needs_proxy,
    clear_cookies, get_cookie_header_for_url,
    validate_manifest,
    # FIX: missing re-exports — needed by faselhd_hdx.py:
    _BROWSER_PROXY_URL, _fetch_via_browser_proxy,
)

# ─── htmlmedia.py surface ──────────────────────────────────────────────────
from .htmlmedia import (
    find_m3u8, find_m3u8_all,
    find_mp4, find_mp4_all,
    _best_media_url, _correct_stream_url,
    # FIX: missing re-export — needed by egydead.py:
    _extract_quality_from_streamruby_url,
    _is_placeholder_media_url, _label_quality_variant, _label_quality_variants,
    get_last_quality_variants, get_synthesized_variants,
    _quality_tls, extract_iframes, _QUALITY_SUFFIX_LABELS,
)

# ─── hosts.py surface ──────────────────────────────────────────────────────
from .hosts import (
    resolve_host, resolve_iframe_chain, resolve_generic_embed,
    extract_stream, extract_stream_all,
    HOST_RESOLVERS, find_packed_links, _unpack_all,
    decode_packer, Unbaser,
    # FIX: missing re-exports — needed by egydead.py / vidsrc.py:
    _parse_hls_master_variants,
    extract_stream_urls_from_text,
)


class BaseExtractor(object):
    """Base class for all site extractors.
    Each site should implement:
      - get_categories(self, mtype="movie") → list of category dicts
      - get_category_items(self, url, page=1) → list of item dicts
      - search(self, query, page=1) → list of item dicts
      - get_page(self, url, m_type=None) → detail dict
      - extract_stream(self, url) → (stream_url, quality, referer, variants)
    """

    def __init__(self):
        self.main_url = None
        self.base_url = None
        self._resolved_base = None

    def get_categories(self, mtype="movie"):
        raise NotImplementedError("Subclasses must implement get_categories()")

    def get_category_items(self, url, page=1):
        raise NotImplementedError("Subclasses must implement get_category_items()")

    def search(self, query, page=1):
        raise NotImplementedError("Subclasses must implement search()")

    def get_page(self, url, m_type=None):
        raise NotImplementedError("Subclasses must implement get_page()")

    def extract_stream(self, url):
        return extract_stream(url)

    def _normalize_url(self, url):
        if not url:
            return ""
        url = str(url).strip()
        if url.startswith("//"):
            return "https:" + url
        if not url.startswith("http"):
            return urljoin(self._get_base(), url)
        return url

    def _get_base(self):
        if self._resolved_base:
            return self._resolved_base
        return self.main_url or ""

    def _clean_title(self, title):
        if not title:
            return ""
        title = re.sub(r'<[^>]+>', ' ', title)
        title = title.replace("&amp;", "&")
        title = re.sub(r'\s+', ' ', title).strip()
        return title

    def _strip_tags(self, text):
        if not text:
            return ""
        text = re.sub(r'<[^>]+>', ' ', text)
        return re.sub(r'\s+', ' ', text).strip()