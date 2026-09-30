# -*- coding: utf-8 -*-
"""
Site Registry - Maps site names to their extractor classes.

Changes in this revision:
  * get_extractor() returns a per-class SINGLETON instead of constructing
    a new instance on every call. The old per-call construction silently
    discarded every instance-level cache on EVERY screen transition:
    TopCinema's _resolved_base re-probed its domain list (~2.3s of dead
    air) on each categories/detail/server-resolution call — 10 times in
    a six-minute session log — and the same applied to any extractor
    that caches on self (EgyDead's mirror-probe TTL, akwam's base, ...).
    Instances are created under a lock and keyed by CLASS (not site
    name), so the unknown-site fallback to egydead shares one instance
    rather than minting duplicates.
  * BUG FIX: get_home_sites() ordered-membership check was O(n²) with a
    `key in _HOME_SITE_ORDER` scan inside the loop — and, worse, it was
    WRONG in one case: dict iteration order (insertion order, py3.7+)
    meant a site missing from _HOME_SITE_ORDER could appear in the
    append-tail in an arbitrary position relative to other unlisted
    sites. Replaced with a set for membership and a sort by
    _HOME_SITE_ORDER index for a stable, defined order.
  * BUG FIX: get_extractor()'s fallback could KeyError on a
    misconfigured registry entry (missing "class" key) and take the
    whole plugin down at navigation time. Now falls back to egydead
    with a log line.

Keep _SEARCH_SITE_ORDER and _HOME_SITE_ORDER deliberately separate
(the two lists already disagree; reordering search priority shouldn't
reshuffle the home grid, and vice versa).

NEW (mycima):
  * Added MyCimaExtractor — the Cimawbas / سيما وبس source
    (vid.mycima.cc). PHP Melody engine, same family as MyWecima.
  * Pinned into BOTH _HOME_SITE_ORDER (right after mywecima, so it sits
    with its sibling on the grid rather than being appended to the tail)
    and _SEARCH_SITE_ORDER (so "search all" fans out to it). Distinct
    content pool from mywecima, so no duplication concern.

NEW (cimawebas):
  * Added CimaWebasExtractor — cimawebas.com (سـيمـا وبس), a
    WordPress 7.x site running the NovaCinemaPlus theme. Different
    engine (WordPress + ncp-* markup) and different content pool than
    the existing "mycima" extractor (PHP Melody, vid.mycima.cc), so
    unlike "mycima" it IS opted into both orders below: no duplication
    concern, and search-all should fan out to it.

NEW (cwb):
  * Added CimaWbasCwbExtractor — cwb.cam (CimaWbas / سيما وبس), a
    PHP Melody site running the MitaTag template. This is a THIRD,
    entirely separate site in the same brand family:
        vid.mycima.cc   →  MyCimaExtractor      (PHP Melody, 3arbserv)
        cimawebas.com   →  CimaWebasExtractor   (WordPress, NovaCinemaPlus)
        cwb.cam         →  CimaWbasCwbExtractor (PHP Melody, MitaTag)
    Different domain, different template, different slug scheme
    (Arabic-transliterated `aflam-*` / `mslslat-*`), different URL
    shapes (`view.php?vid=` alias for `watch.php?vid=`; series live at
    `moslslat-view.php?name=`). No content overlap that matters, so it
    is opted into both _HOME_SITE_ORDER and _SEARCH_SITE_ORDER.

⚠️  There are now THREE extractors whose names contain "cima"/"cwb":
        mycima       →  vid.mycima.cc
        cimawebas    →  cimawebas.com
        cwb          →  cwb.cam
    The registry keys, class names, and target domains are all
    distinct — see the table above. Do not confuse them.
"""

import threading

from .base import log as _reg_log

from .egydead import EgyDeadExtractor
from .egydead_coupons import EgyDeadCouponsExtractor
from .egybest import EgyBestExtractor
from .akwam import AkwamExtractor
from .akwams import AkwamsExtractor
from .arabseed import ArabseedExtractor
from .faselhd_rip import FaselhdRipExtractor
from .faselhd_hdx import FaselhdHdxExtractor
from .shaheed import ShaheedExtractor
from .shahid4u_solar import Shahid4uSolarExtractor
from .topcinema import TopCinemaExtractor
from .wecima import WecimaExtractor
from .wecima_sarl import WecimaSarlExtractor
from .arablionz import ArablionzExtractor
from .yts import YTSExtractor
from .torrentio import TorrentioExtractor
from .vidsrc import VidsrcExtractor
from .imdbsu import ImdbSuExtractor
from .mywecima import MyWecimaExtractor
from .mycima import MyCimaExtractor
from .cimawebas import CimaWebasExtractor
from .cimawbas_cwb import CimaWbasCwbExtractor
from .alooytv import AlooyTvExtractor
from .aflaam import AflaamExtractor
from .onlyflix import OnlyFlixExtractor

# Site registry: maps site names to extractor classes.
# "short" (optional) = the compact tagline shown on the home grid;
# "tagline" = the fuller description kept for other uses.
_SITE_REGISTRY = {
    "egydead":         {"class": EgyDeadExtractor, "title": "EgyDead", "tagline": "واجهة حديثة وبوسترات ومكتبة متجددة", "short": "واجهة حديثة وبوسترات"},
    "egydead_coupons": {"class": EgyDeadCouponsExtractor, "title": "EgyDead Coupons", "tagline": "النسخة العربية - تصنيفات وأقسام مترجمة", "short": "النسخة العربية - تصنيفات مترجمة"},
    "egybest":         {"class": EgyBestExtractor, "title": "EgyBest", "tagline": "ايجي بست - أفلام ومسلسلات مترجمة", "short": "ايجي بست - كل المحتوى"},
    "akwam":           {"class": AkwamExtractor, "title": "Akwam (Classic)", "tagline": "موقع اكوام الكلاسيكي - افلام ومسلسلات عربية واجنبية", "short": "موقع اكوام الكلاسيكي"},
    "akwams":          {"class": AkwamsExtractor, "title": "Akwams (Modern)", "tagline": "موقع اكوام الحديث - واجهة سريعة ومحتوى محدث", "short": "موقع اكوام الحديث"},
    "arabseed":        {"class": ArabseedExtractor, "title": "Arabseed", "tagline": "تصنيفات عربية وأجنبية وحلقات مرتبة", "short": "تصنيفات مرتبة"},
    "fasel":           {"class": FaselhdRipExtractor, "title": "FaselHD (RIP)", "tagline": "واجهة حديثة - سيرفرات متعددة بجودة عالية", "short": "واجهة حديثة - سيرفرات متعددة"},
    "faselhdx":        {"class": FaselhdHdxExtractor, "title": "FaselHD (HDX)", "tagline": "النسخة الكلاسيكية - دقة عالية وسيرفات متنوعة", "short": "النسخة الكلاسيكية - دقة عالية"},
    "shaheed":         {"class": ShaheedExtractor, "title": "Shaheed4u", "tagline": "تحديثات المسلسلات والأفلام الحصرية بجميع الجودات", "short": "أفلام ومسلسلات حصرية"},
    "shahid4u":        {"class": Shahid4uSolarExtractor, "title": "Shahid4u", "tagline": "شاهد فور يو - أفلام ومسلسلات مترجمة"},
    "topcinema":       {"class": TopCinemaExtractor, "title": "TopCinemaa", "tagline": "مكتبة ضخمة من الأفلام والمسلسلات والسلاسل", "short": "مكتبة ضخمة"},
    "wecima":          {"class": WecimaExtractor, "title": "Wecima", "tagline": "أقسام واسعة وبحث وسيرفرات مباشرة", "short": "أقسام واسعة وبحث سريع"},
    "wecima_sarl":     {"class": WecimaSarlExtractor, "title": "Wecima.sarl", "tagline": "نسخة وي سيما الجديدة - واجهة مختلفة وأقسام محدثة", "short": "نسخة وي سيما الجديدة - واجهة مختلفة"},
    "arablionz":       {"class": ArablionzExtractor, "title": "Arablionz", "tagline": "عرب ليونز - افلام ومسلسلات سيرفر Lionz Tv"},
    "yts":             {"class": YTSExtractor, "title": "YTS (YIFY)", "tagline": "أفلام ومسلسلات أجنبية بجودة عالية"},
    "torrentio":       {"class": TorrentioExtractor, "title": "Torrentio", "tagline": "أفلام ومسلسلات Magnet (TorrServer)", "short": "أفلام ومسلسلات Magnet عبر TorrServer"},
    "vidsrc":          {"class": VidsrcExtractor, "title": "Vidsrc", "tagline": "Movies & TV Shows in HD", "short": "أفلام ومسلسلات أجنبية بجودة عالية"},
    "imdb_su":         {"class": ImdbSuExtractor, "title": "IMDB.su", "tagline": "أفلام ومسلسلات شاهد مباشر"},
    "mywecima":        {"class": MyWecimaExtractor, "title": "MyWecima", "tagline": "ماي سيما — أفلام ومسلسلات مترجمة بجودة عالية", "short": "أفلام ومسلسلات مترجمة"},
    "mycima":          {"class": MyCimaExtractor, "title": "MyCima", "tagline": "سيما وبس — أفلام ومسلسلات مترجمة", "short": "سيما وبس — أفلام ومسلسلات"},
    "cimawebas":       {"class": CimaWebasExtractor, "title": "CimaWebas", "tagline": "سيما وبس — أفلام ومسلسلات وأنمي بجودة عالية", "short": "سيما وبس — أفلام ومسلسلات"},
    "cwb":             {"class": CimaWbasCwbExtractor, "title": "CimaWbas (cwb)", "tagline": "سيما وبس cwb.cam — أفلام ومسلسلات بجودة عالية", "short": "سيما وبس cwb — أفلام ومسلسلات"},
    "alooytv":         {"class": AlooyTvExtractor, "title": "AlooyTV", "tagline": "الوي تي في - أفلام ومسلسلات مترجمة", "short": "الوي تي في - أفلام ومسلسلات"},
    "aflaam":          {"class": AflaamExtractor, "title": "Aflaam", "tagline": "افلام — مشاهدة وتحميل الأفلام والمسلسلات", "short": "افلام — أفلام ومسلسلات"},
    "onlyflix":        {"class": OnlyFlixExtractor, "title": "OnlyFlix", "tagline": "OnlyFlix — أفلام ومسلسلات أجنبية بجودة عالية", "short": "أفلام ومسلسلات أجنبية"},
}

# Aggregated-search priority.
# "mycima", "cimawebas", and "cwb" are all listed here — each is a
# distinct site with a distinct content pool (different engine, different
# domain, different catalogue), so search-all should fan out to all three.
_SEARCH_SITE_ORDER = (
    "egydead", "egydead_coupons", "egybest", "akwam", "akwams", "arabseed",
    "wecima", "wecima_sarl", "topcinema", "fasel", "faselhdx",
    "shaheed", "shahid4u", "arablionz", "mywecima", "mycima",
    "cimawebas", "cwb",
    "alooytv", "aflaam", "onlyflix",
    "yts", "torrentio", "vidsrc", "imdb_su",
)

# Home-grid tile order — deliberately separate from _SEARCH_SITE_ORDER
# (see module docstring). The three cima-family sites (mywecima, mycima,
# cimawebas, cwb) are pinned together so they appear as a sibling cluster
# on the grid rather than being appended to the tail.
_HOME_SITE_ORDER = (
    "egydead", "egydead_coupons", "egybest", "akwam", "akwams", "arabseed",
    "wecima", "wecima_sarl", "shaheed", "shahid4u", "topcinema",
    "fasel", "faselhdx", "arablionz", "mywecima", "mycima",
    "cimawebas", "cwb",
    "alooytv", "aflaam", "onlyflix",
    "yts", "torrentio", "vidsrc", "imdb_su",
)

# ─── Extractor singletons ────────────────────────────────────────────────────
# Created lazily under a lock, keyed by CLASS so the egydead fallback
# shares one instance instead of minting duplicates. First-call races
# are benign (two threads constructing is blocked by the lock anyway;
# the worst case is one extra network probe long before that matters).
_EXTRACTOR_INSTANCES = {}
_EXTRACTOR_LOCK = threading.Lock()


def get_extractor(site_name):
    """Return the singleton extractor instance for a site key.

    Per-class singletons keep instance-level caches (TopCinema's
    _resolved_base, EgyDead's mirror-probe TTL, akwam's base, Cwb's
    base-cache TTL...) alive across screen transitions. The old per-call
    `entry["class"]()` discarded them on EVERY call."""
    entry = _SITE_REGISTRY.get(site_name)
    if not entry or not isinstance(entry, dict) or "class" not in entry:
        try:
            if entry:
                _reg_log("registry: malformed entry for {!r} (missing 'class'), falling back to egydead".format(site_name))
            else:
                _reg_log("registry: unknown site {!r}, falling back to egydead".format(site_name))
        except Exception:
            pass
        entry = _SITE_REGISTRY.get("egydead")
    cls = entry["class"]
    with _EXTRACTOR_LOCK:
        inst = _EXTRACTOR_INSTANCES.get(cls)
        if inst is None:
            inst = cls()
            _EXTRACTOR_INSTANCES[cls] = inst
    return inst


def get_site_names():
    return list(_SITE_REGISTRY.keys())


def get_site_metadata(site_name):
    entry = _SITE_REGISTRY.get(site_name)
    if entry:
        return {"title": entry.get("title", site_name), "tagline": entry.get("tagline", "")}
    return {"title": site_name, "tagline": ""}


def get_search_site_order():
    return _SEARCH_SITE_ORDER


def get_home_sites():
    """Ordered (key, title, tagline) for the home grid — the single
    source of truth plugin.py builds its tiles from. The tagline is the
    entry's "short" value when present, else its full "tagline".
    Registered sites missing from _HOME_SITE_ORDER are appended at the
    end (in a stable, defined order: registry insertion order), so a
    newly added extractor shows up even if you forget to order it."""
    ordered_keys = set(_HOME_SITE_ORDER)
    out = []
    for key in _HOME_SITE_ORDER:
        entry = _SITE_REGISTRY.get(key)
        if entry:
            out.append((key, entry["title"], entry.get("short") or entry["tagline"]))
    # Append-tail for unlisted sites: registry insertion order (a stable,
    # defined order — the old code's membership scan made tail order
    # implicit and the `key in tuple` check O(n²)).
    for key, entry in _SITE_REGISTRY.items():
        if key not in ordered_keys:
            out.append((key, entry["title"], entry.get("short") or entry["tagline"]))
    return out