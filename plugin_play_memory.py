# -*- coding: utf-8 -*-
"""NovaPlay - remembers which way of playing works for each video server.

The player tries a list of methods (4097 direct, 4097 + proxy, 5002 exteplayer3,
...) until one plays. Which one works depends on the server that delivers the
video (moon.zenoak.top needs the proxy, a plain TS host plays direct), so the
outcome of every attempt is kept per stream host:

  * a method that played on this host before goes first (most recent first);
  * methods never tried on this host keep their normal order;
  * a method that never played anywhere and failed 5+ times goes near the end;
  * a method that failed 2+ times on this host and never played there goes last.

Nothing is ever dropped, so a server that changes still gets every method.
No Enigma2 dependency -- safe from any thread.
"""
import threading
import time
from urllib.parse import urlparse, parse_qs

_lock = threading.RLock()
_loaded = False
_hosts = {}     # host -> {label: [ok, fail, last_ok_ts]}
_global = {}    # label -> [ok, fail]
_MAX_HOSTS = 400
_CFG_HOSTS = "play_method_hosts"
_CFG_GLOBAL = "play_method_global"


def _norm_label(label):
    return str(label or "").strip()


def stream_host(svc_url):
    """Host that really serves the video, also for proxied / legacy-proxied URLs."""
    try:
        u = str(svc_url or "").split("|", 1)[0].split("#", 1)[0]
        p = urlparse(u)
        if p.hostname in ("127.0.0.1", "localhost"):
            q = parse_qs(p.query or "")
            if q.get("url"):
                return stream_host(q["url"][0])
            inner = (p.path or "").lstrip("/")
            if "://" in inner:
                return stream_host(inner)
        host = (p.hostname or "").lower()
        return host[4:] if host.startswith("www.") else host
    except Exception:
        return ""


def _ensure_loaded():
    global _loaded, _hosts, _global
    if _loaded:
        return
    try:
        from plugin_state import _get_config
        _hosts = _get_config(_CFG_HOSTS, {}) or {}
        _global = _get_config(_CFG_GLOBAL, {}) or {}
    except Exception:
        _hosts, _global = {}, {}
    _loaded = True


def _persist():
    try:
        from plugin_state import _set_config
        _set_config(_CFG_HOSTS, dict(_hosts))
        _set_config(_CFG_GLOBAL, dict(_global))
    except Exception:
        pass


def record(svc_url, label, ok):
    """Note how one attempt went."""
    host = stream_host(svc_url)
    label = _norm_label(label)
    if not host or not label:
        return
    with _lock:
        _ensure_loaded()
        h = _hosts.setdefault(host, {})
        st = h.setdefault(label, [0, 0, 0])
        while len(st) < 3:
            st.append(0)
        g = _global.setdefault(label, [0, 0])
        if ok:
            st[0] += 1
            st[2] = int(time.time())
            g[0] += 1
        else:
            st[1] += 1
            g[1] += 1
        # keep counts small so the picture can change over time
        if st[0] + st[1] > 40:
            st[0], st[1] = (st[0] + 1) // 2, (st[1] + 1) // 2
        if g[0] + g[1] > 200:
            g[0], g[1] = (g[0] + 1) // 2, (g[1] + 1) // 2
        if len(_hosts) > _MAX_HOSTS:
            oldest = sorted(_hosts, key=lambda k: max([v[2] for v in _hosts[k].values()] or [0]))
            for k in oldest[:len(_hosts) - _MAX_HOSTS]:
                _hosts.pop(k, None)
        _persist()


def order(cands, svc_url=None):
    """Re-order player candidates [(type, url, label, uses_proxy), ...]."""
    if not cands:
        return cands
    host = stream_host(svc_url or (cands[0][1] if cands else ""))
    with _lock:
        _ensure_loaded()
        h = dict(_hosts.get(host, {})) if host else {}
        g = dict(_global)

    def tier(c):
        label = _norm_label(c[2] if len(c) > 2 else "")
        st = h.get(label)
        if st and st[0] > 0:
            return (0, -(st[2] if len(st) > 2 else 0))
        if st and st[1] >= 2:
            return (3, 0)
        gs = g.get(label)
        if gs and gs[0] == 0 and gs[1] >= 5:
            return (2, 0)
        return (1, 0)
    indexed = list(enumerate(cands))
    indexed.sort(key=lambda ic: (tier(ic[1]), ic[0]))
    return [c for _, c in indexed]


def describe(svc_url):
    """Short text for the log: what is known about this host."""
    host = stream_host(svc_url)
    with _lock:
        _ensure_loaded()
        h = _hosts.get(host, {})
        parts = ["{} {}/{}".format(k, v[0], v[1]) for k, v in h.items()]
    return "{}: {}".format(host, ", ".join(parts) if parts else "no history")
