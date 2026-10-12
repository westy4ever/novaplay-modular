# -*- coding: utf-8 -*-
"""NovaPlay - server-link ranking.

Tracks, per streaming title, which server HOSTS have actually played
successfully, so _sort_servers() can push a confirmed-working host (and
its same-host "variant" quality links) to the top of that title's server
list. Confirming a second, different host appends it AFTER the first --
it never replaces an earlier confirmed host. A host that fails to play
for a given title is sunk to the bottom of that title's list (unless it
already proved it works for that same title, in which case one flaky
failure is ignored).

Separately, a GLOBAL per-host success/fail tally (_host_stats) is kept
across ALL titles and used only as a tie-breaker for servers that have
no title-specific history yet.

No Enigma2/UI dependency -- safe to call from any thread.
"""
import threading
from urllib.parse import urlparse

_lock = threading.RLock()
_loaded = False

_host_stats = {}       # host -> [ok, fail]              (global, cross-title)
_title_working = {}    # title_key -> [host, host, ...]  (ordered, first-confirmed-first)
_title_failed = {}     # title_key -> [host, host, ...]  (this title only)

_MAX_HOSTS = 300
_MAX_TITLES = 500


def _ensure_loaded():
    global _loaded, _host_stats, _title_working, _title_failed
    if _loaded:
        return
    try:
        from plugin_state import _get_config
        _host_stats = _get_config("server_host_stats", {}) or {}
    except Exception:
        _host_stats = {}
    try:
        from plugin_state import _get_config
        _title_working = _get_config("server_title_working", {}) or {}
    except Exception:
        _title_working = {}
    try:
        from plugin_state import _get_config
        _title_failed = _get_config("server_title_failed", {}) or {}
    except Exception:
        _title_failed = {}
    _loaded = True


def _persist_hosts():
    try:
        from plugin_state import _set_config
        _set_config("server_host_stats", dict(_host_stats))
    except Exception:
        pass


def _persist_working():
    try:
        from plugin_state import _set_config
        _set_config("server_title_working", dict(_title_working))
    except Exception:
        pass


def _persist_failed():
    try:
        from plugin_state import _set_config
        _set_config("server_title_failed", dict(_title_failed))
    except Exception:
        pass


def _host_of(url):
    try:
        host = (urlparse(url or "").netloc or "").lower()
    except Exception:
        return ""
    if host.startswith("www."):
        host = host[4:]
    return host


def _host_path_of(url):
    """[server-rank-wrapper-fix] Per-title identity key: host + path.

    Several sites (ArabSeed confirmed, likely others) hand out EVERY
    server for a title as the same gateway/wrapper host -- e.g. every
    one of ArabSeed's 5 servers is a `govid.live/play/=<token>` link,
    with the real destination (voe.sx, uqload.vc, vidara.to, ...) only
    revealed after a network round-trip inside extract_stream(). Using
    bare host() as the per-title identity collapses all of those
    distinct servers into one indistinguishable "govid.live" bucket,
    so a confirmed-working server can never be told apart from the
    four that failed next to it -- it just never reaches the top of
    the list again.

    The wrapper token in the path is embedded statically per episode
    (same data-link on every page load), so host+path stays just as
    stable across revisits as host alone, while actually telling the
    servers apart. Global, cross-title host_score()/record_host_result()
    intentionally keep using plain host() -- that's a real-host
    reliability signal and collapsing wrapper hosts there is fine (even
    desired, since "govid.live" itself isn't informative on its own).
    """
    try:
        p = urlparse(url or "")
        host = (p.netloc or "").lower()
        if host.startswith("www."):
            host = host[4:]
        if not host:
            return ""
        return host + (p.path or "")
    except Exception:
        return ""


def _prune_titles(d):
    # dict preserves insertion order in py3 -- drop the oldest first.
    while len(d) > _MAX_TITLES:
        try:
            oldest = next(iter(d))
        except StopIteration:
            break
        d.pop(oldest, None)


# ---------------------------------------------------------------- global ----

def record_host_result(url, success):
    host = _host_of(url)
    if not host:
        return
    with _lock:
        _ensure_loaded()
        row = _host_stats.get(host) or [0, 0]
        if success:
            row[0] += 1
        else:
            row[1] += 1
        _host_stats[host] = row
        if len(_host_stats) > _MAX_HOSTS:
            worst = min(_host_stats, key=lambda k: sum(_host_stats[k]))
            if worst != host:
                _host_stats.pop(worst, None)
        _persist_hosts()


def host_score(url):
    host = _host_of(url)
    if not host:
        return 0.5
    with _lock:
        _ensure_loaded()
        ok, fail = _host_stats.get(host, (0, 0))
    prior = 2.0
    return (ok + prior * 0.5) / (ok + fail + prior)


# ----------------------------------------------------------- per-title -----

def record_title_working(title_key, url):
    """Mark url's per-title identity (host+path -- see _host_path_of)
    as confirmed-working for this exact title. Hosts are kept in
    first-confirmed order -- a new host is appended after the existing
    ones, never replacing them. Also clears the host from this title's
    failed list (a working result outweighs a past fail)."""
    if not title_key:
        return
    host = _host_path_of(url)
    if not host:
        return
    with _lock:
        _ensure_loaded()
        hosts = list(_title_working.get(title_key) or [])
        if host not in hosts:
            hosts.append(host)
            _title_working[title_key] = hosts
            _prune_titles(_title_working)
            _persist_working()
        failed = _title_failed.get(title_key) or []
        if host in failed:
            _title_failed[title_key] = [h for h in failed if h != host]
            _persist_failed()


def get_title_working(title_key):
    """Ordered list of hosts confirmed working for this title (first
    confirmed first). Empty list if none yet."""
    if not title_key:
        return []
    with _lock:
        _ensure_loaded()
        return list(_title_working.get(title_key) or [])


def record_title_failed(title_key, url):
    """Mark url's per-title identity (host+path -- see _host_path_of)
    as having failed to play for this exact title. No-op if that
    identity already proved it works for this same title -- one flaky
    failure shouldn't bury an otherwise-working server."""
    if not title_key:
        return
    host = _host_path_of(url)
    if not host:
        return
    with _lock:
        _ensure_loaded()
        if host in (_title_working.get(title_key) or []):
            return
        failed = list(_title_failed.get(title_key) or [])
        if host not in failed:
            failed.append(host)
            _title_failed[title_key] = failed
            _prune_titles(_title_failed)
            _persist_failed()


def get_title_failed(title_key):
    """Set of hosts that failed to play for this exact title."""
    if not title_key:
        return set()
    with _lock:
        _ensure_loaded()
        return set(_title_failed.get(title_key) or [])
