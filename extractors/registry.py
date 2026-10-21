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

NEW (egydead_ca):
  * Added EgydeadCaExtractor — egydead.ca (ايجي ديد), a React /
    React-Router SPA backed by a clean, directly-fetchable JSON API
    (/api/v1/channel/2 for movies, /api/v1/channel/series for series,
    /api/v1/titles/<id> for detail, /api/v1/watch/<id> for streams).
    Nothing is HTML-scraped — every field comes from JSON. Its
    egybestvid.com embeds resolve through the shared hosts.py dispatch
    with no dedicated resolver needed.
    Opted into BOTH orders, pinned right after "egydead_coupons" so the
    egydead family stays clustered on the home grid.
    ⚠️  search() is a deliberate no-op: no search endpoint was ever
    confirmed for this site, so it returns [] rather than guessing at
    an unconfirmed URL shape. Being in _SEARCH_SITE_ORDER means
    "search all" will fan out to it and get an empty list back every
    time. If that bothers you, drop "egydead_ca" from
    _SEARCH_SITE_ORDER — it will still appear on the home grid via
    _HOME_SITE_ORDER and remain fully browsable by category.
    ⚠️  If the existing "egydead" entry already targets egydead.ca
    (check egydead.py's MAIN_URL / DOMAINS), this new extractor
    SUPERSEDES it and you should REPLACE the "egydead" entry rather
    than keep both — otherwise you'll have the same site on the grid
    twice under two different keys.
"""

import threading

from .base import log as _reg_log

from .egydead import EgyDeadExtractor
from .egydead_coupons import EgyDeadCouponsExtractor
from .egydead_ca import EgydeadCaExtractor
from .egybest import EgyBestExtractor
from .akwam import AkwamExtractor
from .akwams import AkwamsExtractor
from .arabseed import ArabseedExtractor
from .arabseed_liv import ArabseedLivExtractor
from .arabseed_original import ArabseedOriginalExtractor
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
from .fivetv import FivetvExtractor
from .cineby import CinebyExtractor
from .sflix_pro import SflixProExtractor
from .movy import MovyExtractor
from .cinejoy import CinejoyExtractor
from .zetmoon import ZetmoonExtractor

# Site registry: maps site names to extractor classes.
# "short" (optional) = the compact tagline shown on the home grid;
# "tagline" = the fuller description kept for other uses.
_SITE_REGISTRY = {
    "egydead":         {"class": EgyDeadExtractor, "title": "EgyDead", "tagline": "Modern layout, posters and a growing library", "short": "Modern layout with posters"},
    "egydead_coupons": {"class": EgyDeadCouponsExtractor, "title": "EgyDead Coupons", "tagline": "Arabic edition — translated categories", "short": "Arabic edition — translated categories"},
    "egydead_ca":      {"class": EgydeadCaExtractor, "title": "EgyDead (CA)", "tagline": "EgyDead — modern layout, subtitled movies & series", "short": "EgyDead — movies & series"},
    "egybest":         {"class": EgyBestExtractor, "title": "EgyBest", "tagline": "EgyBest — subtitled movies & series", "short": "EgyBest — everything"},
    "akwam":           {"class": AkwamExtractor, "title": "Akwam (Classic)", "tagline": "Classic Akwam — Arabic & foreign movies and series", "short": "Classic Akwam"},
    "akwams":          {"class": AkwamsExtractor, "title": "Akwams (Modern)", "tagline": "Modern Akwam — fast layout, fresh content", "short": "Modern Akwam"},
    "arabseed":        {"class": ArabseedExtractor, "title": "Arabseed", "tagline": "Arabic & foreign categories, ordered episodes", "short": "Ordered categories"},
    "arabseed_liv":    {"class": ArabseedLivExtractor, "title": "Arabseed.living", "tagline": "Arabseed (living) — separate library, same categories", "short": "Separate library"},
    "arabseed_original": {"class": ArabseedOriginalExtractor, "title": "Arabseed (original)", "tagline": "Arabseed — original wine edition (separate from trade)", "short": "Original edition"},
    "fasel":           {"class": FaselhdRipExtractor, "title": "FaselHD (RIP)", "tagline": "Modern layout — many HD servers", "short": "Modern layout — many servers"},
    "faselhdx":        {"class": FaselhdHdxExtractor, "title": "FaselHD (HDX)", "tagline": "Classic edition — HD, varied servers", "short": "Classic edition — HD"},
    "shaheed":         {"class": ShaheedExtractor, "title": "Shaheed4u", "tagline": "Exclusive series & movie updates in all qualities", "short": "Exclusive movies & series"},
    "shahid4u":        {"class": Shahid4uSolarExtractor, "title": "Shahid4u", "tagline": "Shahid4u — subtitled movies & series"},
    "topcinema":       {"class": TopCinemaExtractor, "title": "TopCinemaa", "tagline": "Huge library of movies, series and collections", "short": "Huge library"},
    "wecima":          {"class": WecimaExtractor, "title": "Wecima", "tagline": "Wide sections, search and direct servers", "short": "Wide sections, fast search"},
    "wecima_sarl":     {"class": WecimaSarlExtractor, "title": "Wecima.sarl", "tagline": "New Wecima edition — different layout, updated sections", "short": "New Wecima edition — different layout"},
    "arablionz":       {"class": ArablionzExtractor, "title": "Arablionz", "tagline": "Arablionz — movies & series on the Lionz TV server"},
    "yts":             {"class": YTSExtractor, "title": "YTS (YIFY)", "tagline": "Foreign movies & series in HD"},
    "torrentio":       {"class": TorrentioExtractor, "title": "Torrentio", "tagline": "Magnet movies & series (TorrServer)", "short": "Magnet movies & series via TorrServer"},
    "vidsrc":          {"class": VidsrcExtractor, "title": "Vidsrc", "tagline": "Movies & TV Shows in HD", "short": "Foreign movies & series in HD"},
    "imdb_su":         {"class": ImdbSuExtractor, "title": "IMDB.su", "tagline": "Movies & series, watch directly"},
    "mywecima":        {"class": MyWecimaExtractor, "title": "MyWecima", "tagline": "MyWecima — subtitled movies & series in HD", "short": "Subtitled movies & series"},
    "mycima":          {"class": MyCimaExtractor, "title": "MyCima", "tagline": "Cima Wbas — subtitled movies & series", "short": "Cima Wbas — movies & series"},
    "cimawebas":       {"class": CimaWebasExtractor, "title": "CimaWebas", "tagline": "Cima Wbas — movies, series & anime in HD", "short": "Cima Wbas — movies & series"},
    "cwb":             {"class": CimaWbasCwbExtractor, "title": "CimaWbas (cwb)", "tagline": "Cima Wbas cwb.cam — movies & series in HD", "short": "Cima Wbas cwb — movies & series"},
    "alooytv":         {"class": AlooyTvExtractor, "title": "AlooyTV", "tagline": "AlooyTV — subtitled movies & series", "short": "AlooyTV — movies & series"},
    "aflaam":          {"class": AflaamExtractor, "title": "Aflaam", "tagline": "Aflaam — watch & download movies and series", "short": "Aflaam — movies & series"},
    "onlyflix":        {"class": OnlyFlixExtractor, "title": "OnlyFlix", "tagline": "OnlyFlix — foreign movies & series in HD", "short": "Foreign movies & series"},
    "fivetv":          {"class": FivetvExtractor, "title": "Fivetv", "tagline": "Fivetv — subtitled movies, series & episodes in HD", "short": "Fivetv — movies & series"},
    "cineby":          {"class": CinebyExtractor, "title": "Cineby", "tagline": "Cineby — Movies & TV Shows in HD", "short": "Foreign movies & series in HD"},
    "sflix_pro":       {"class": SflixProExtractor, "title": "SFlix", "tagline": "SFlix — Movies & TV Shows in HD", "short": "Foreign movies & series in HD"},
    "movy":            {"class": MovyExtractor, "title": "Movy", "tagline": "Movy — Movies, TV Shows & Anime in HD/4K", "short": "Foreign movies, series & anime in HD"},
    "cinejoy":         {"class": CinejoyExtractor, "title": "Cinejoy", "tagline": "Cinejoy — Movies & TV by provider and curated collections", "short": "Foreign movies & series by provider, curated lists"},
    "zetmoon":         {"class": ZetmoonExtractor, "title": "ZetMoon", "tagline": "ZetMoon — Trending movies & series", "short": "Trending foreign movies & series"},
}

# Aggregated-search priority.
# "mycima", "cimawebas", and "cwb" are all listed here — each is a
# distinct site with a distinct content pool (different engine, different
# domain, different catalogue), so search-all should fan out to all three.
# "egydead_ca" is listed too, but note its search() is a confirmed no-op
# (no endpoint was ever captured) — it contributes nothing to search
# results and can be dropped from this tuple without losing anything.
_SEARCH_SITE_ORDER = (
    "egydead", "egydead_coupons", "egydead_ca", "egybest", "akwam", "akwams",
    "arabseed", "arabseed_liv", "arabseed_original",
    "wecima", "wecima_sarl", "topcinema", "fasel", "faselhdx",
    "shaheed", "shahid4u", "arablionz", "mywecima", "mycima",
    "cimawebas", "cwb",
    "alooytv", "aflaam", "onlyflix", "fivetv", "cineby", "sflix_pro", "movy", "cinejoy", "zetmoon",
    "yts", "torrentio", "vidsrc", "imdb_su",
)

# Home-grid tile order — deliberately separate from _SEARCH_SITE_ORDER
# (see module docstring).
#
# "egydead_coupons", "egydead_ca", "arabseed_liv", "arabseed_original", "akwams",
# "wecima_sarl", "faselhdx", "shahid4u", "mycima", "cimawebas" and "cwb"
# are NOT listed here (only in _SEARCH_SITE_ORDER): each is a
# _SITE_FAMILIES member of its representative ("egydead" / "arabseed" /
# "akwam" / "wecima" / "fasel" / "shaheed" / "mywecima"), so it rides on
# that representative's single grid tile (see get_home_sites()/
# get_family_members() below) rather than getting a tile of its own.
# "Search all" still fans out to every member normally.
_HOME_SITE_ORDER = (
    "egydead", "egybest", "akwam",
    "arabseed",
    "wecima", "shaheed", "topcinema",
    "fasel", "arablionz", "mywecima",
    "alooytv", "aflaam", "onlyflix", "fivetv", "cineby", "sflix_pro", "movy", "cinejoy", "zetmoon",
    "yts", "torrentio", "vidsrc", "imdb_su",
)

# Home-grid "site families": groups of registry keys that share one grid
# tile. The FIRST key in each list is the representative -- the one whose
# tile actually shows on the home grid (via get_home_sites()). Pressing
# that tile opens a small picker (styled like the category list) listing
# every member so the user can choose which one to actually enter.
#
# To add a new same-family site in future: register it in _SITE_REGISTRY
# and _SEARCH_SITE_ORDER as usual, then just append its key to the right
# list here (or add a new list for a new family) -- get_home_sites() and
# the home-screen picker both read this dict, so no other code changes
# are needed for it to show up correctly.
_SITE_FAMILIES = {
    "egydead": ["egydead", "egydead_coupons", "egydead_ca"],
    "arabseed": ["arabseed", "arabseed_liv", "arabseed_original"],
    "akwam": ["akwam", "akwams"],
    "wecima": ["wecima", "wecima_sarl"],
    "fasel": ["fasel", "faselhdx"],                     # FaselHD (RIP) / FaselHD (HDX)
    "shaheed": ["shaheed", "shahid4u"],                  # both titled "Shaheed4u"
    "mywecima": ["mywecima", "mycima", "cimawebas", "cwb"],  # the "cima" cluster
}

# Reverse lookup: member key -> representative key, built once below.
_FAMILY_OF = {}
for _rep, _members in _SITE_FAMILIES.items():
    for _m in _members:
        _FAMILY_OF[_m] = _rep
del _rep, _members, _m

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


def get_family_members(site_key):
    """Return the ordered list of registry keys grouped with site_key on
    the home grid (its own key alone, if it isn't part of any family).

    If the user has long-press-reordered this family's sub-menu rows
    (plugin_state._set_family_order), that saved order is returned
    instead of the hardcoded _SITE_FAMILIES order -- any member missing
    from the saved order (e.g. one added after the reorder) is appended
    at the end in the default order, so nothing silently disappears
    from the picker. The representative (which key gets the home-grid
    tile) is unaffected by this -- it's always the family's first
    _SITE_FAMILIES entry, regardless of row order."""
    rep = _FAMILY_OF.get(site_key, site_key)
    default_order = list(_SITE_FAMILIES.get(rep, [site_key]))
    try:
        from plugin_state import _get_family_order
        custom = _get_family_order(rep)
    except Exception:
        custom = None
    if custom:
        valid = set(default_order)
        ordered = [k for k in custom if k in valid]
        for k in default_order:
            if k not in ordered:
                ordered.append(k)
        return ordered
    return default_order


def is_family_representative(site_key):
    """True if site_key is the one whose tile shows on the home grid
    (either it isn't grouped at all, or it's the first/representative
    member of its family)."""
    return _FAMILY_OF.get(site_key, site_key) == site_key


def get_home_sites():
    """Ordered (key, title, tagline) for the home grid — the single
    source of truth plugin.py builds its tiles from. The tagline is the
    entry's "short" value when present, else its full "tagline".
    Registered sites missing from _HOME_SITE_ORDER are appended at the
    end (in a stable, defined order: registry insertion order), so a
    newly added extractor shows up even if you forget to order it.

    A site that is a non-representative member of a _SITE_FAMILIES group
    (e.g. "arabseed_liv") never gets its own tile here -- it rides on its
    representative's tile and is reached through get_family_members().

    A tile's title is the user's long-press "Rename" override
    (plugin_state._get_site_custom_name) when one is saved, else the
    registry's default title."""
    try:
        from plugin_state import _get_site_custom_name
    except Exception:
        def _get_site_custom_name(_k):
            return ""

    def _title_for(key, entry):
        return _get_site_custom_name(key) or entry["title"]

    ordered_keys = set(_HOME_SITE_ORDER)
    out = []
    for key in _HOME_SITE_ORDER:
        if not is_family_representative(key):
            continue
        entry = _SITE_REGISTRY.get(key)
        if entry:
            out.append((key, _title_for(key, entry), entry.get("short") or entry["tagline"]))
    # Append-tail for unlisted sites: registry insertion order (a stable,
    # defined order — the old code's membership scan made tail order
    # implicit and the `key in tuple` check O(n²)).
    for key, entry in _SITE_REGISTRY.items():
        if key not in ordered_keys and is_family_representative(key):
            out.append((key, _title_for(key, entry), entry.get("short") or entry["tagline"]))
    return out