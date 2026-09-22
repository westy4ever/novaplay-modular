# -*- coding: utf-8 -*-
"""
novaplay_diagnostics.py — self-test screen (StreamProxy diagnostics pattern)
==============================================================================
Checks: dependencies (curl_cffi/brotli), API keys, network/DNS, per-site
base-domain resolution, download disk space, local proxy stats.

Fast checks run first and fill the list; slow site probes (each may
resolve mirrors over the network) update the list as they complete.
The whole run happens on a worker thread; rows appear via
callInMainThread. EXIT closes any time (the thread is a daemon and
rows arriving after close are harmless).

py2/py3 safe (no f-strings).

Hook (from your plugin.py settings screen):
    from novaplay_diagnostics import NovaDiagnosticsScreen
    self.session.open(NovaDiagnosticsScreen)
"""

import os
import threading

try:
    import urllib.request as _urlreq
except ImportError:
    import urllib2 as _urlreq

from Screens.Screen import Screen
from Components.ActionMap import ActionMap
from Components.Label import Label
from Components.MenuList import MenuList

from novaplay_thread import callInMainThread
from plugin_gridlist import scale_skin_xml


def _disk_free_mb(path):
    try:
        st = os.statvfs(path)          # py2+py3 (shutil.disk_usage is py3-only)
        return (st.f_bavail * st.f_frsize) / (1024.0 * 1024.0)
    except Exception:
        return -1


def _net_ok():
    try:
        r = _urlreq.urlopen("https://www.google.com/generate_204", timeout=6)
        code = r.getcode()
        r.close()
        return code in (200, 204)
    except Exception:
        return False


def run_diagnostics(on_row, on_done):
    """Runs all checks. on_row(name, status) fires per check; on_done()
    at the end. Intended to be called from a WORKER thread."""
    def add(name, fn):
        try:
            ok = fn()
            if ok is None:
                status = "SKIP"
            else:
                status = "OK" if ok else "FAIL"
        except Exception as e:
            status = "ERROR: %s" % str(e)[:60]
        on_row(name, status)

    import extractors.net as _n  
    import novaplay_proxy as _np
    from plugin_state import _get_config

    add("curl_cffi (Cloudflare bypass)", lambda: _n._CURL_CFFI_OK)
    add("brotli decompression", lambda: _n.brotli is not None)
    add("external browser proxy configured", lambda: bool(_n._BROWSER_PROXY_URL))
    add("network / DNS", _net_ok)
    add("TMDB API key", lambda: bool(str(_get_config("tmdb_api_key", "")).strip()))
    add("SubSource API key (optional)", lambda: bool(str(_get_config("subsource_api_key", "")).strip()))
    add("OpenSubtitles API key (optional)", lambda: bool(str(_get_config("opensubtitles_api_key", "")).strip()))

    try:
        from plugin_downloads import download_dir
        add("download disk free > 500 MB",
            lambda: _disk_free_mb(download_dir()) > 500)
    except Exception:
        pass

    stats = _np.get_stats() if _np else {}
    add("local proxy started", lambda: bool(_np and _np._PROXY_STARTED))
    on_row("proxy stats", "req=%s rewrites=%s cache_hits=%s errors=%s" % (
        stats.get("requests", 0), stats.get("manifests_rewritten", 0),
        stats.get("cache_hits", 0), stats.get("upstream_errors", 0)))

    # Slow part: live site base resolution (each may probe mirror lists).
    # Extractors without a _get_base() method report SKIP, not FAIL.
    try:
        from extractors import get_extractor
        for site in ("egydead", "wecima", "fasel", "akwam"):
            def probe(s=site):
                ex = get_extractor(s)
                base_fn = getattr(ex, "_get_base", None)
                if base_fn is None:
                    return None
                return bool(base_fn())
            add("site base: %s" % site, probe)
    except Exception:
        pass
    on_done()


class NovaDiagnosticsScreen(Screen):
    skin = scale_skin_xml("""
    <screen name="NovaDiagnosticsScreen" position="center,center" size="1500,860" title="Diagnostics" flags="wfNoBorder">
        <eLabel position="0,0" size="1500,860" backgroundColor="#0D1117" zPosition="0" />
        <eLabel position="0,0" size="1500,80" backgroundColor="#161B22" zPosition="1" />
        <widget name="header" position="40,18" size="1420,44" font="Regular;32" foregroundColor="#00E5FF" transparent="1" zPosition="2" />
        <widget name="list" position="30,100" size="1440,680" scrollbarMode="showOnDemand" zPosition="2" />
        <widget name="hint" position="40,800" size="1420,36" font="Regular;24" foregroundColor="#8B949E" transparent="1" zPosition="2" />
    </screen>""")

    def __init__(self, session):
        Screen.__init__(self, session)
        self.skinName = ["NovaDiagnosticsScreen"]
        self.rows = []
        self["header"] = Label("Diagnostics — running…")
        self["hint"] = Label("EXIT = close (site probes are slow)")
        self["list"] = MenuList([])
        self["actions"] = ActionMap(["OkCancelActions"], {
            "ok": self._noop,
            "cancel": self.close,
        }, -1)
        threading.Thread(target=self._run, daemon=True).start()

    def _noop(self):
        pass

    def _run(self):
        def on_row(name, status):
            callInMainThread(self._add_row, "%s:  %s" % (name, status))
        run_diagnostics(on_row, lambda: callInMainThread(self._finish))

    def _add_row(self, text):
        self.rows.append((text,))
        self["list"].setList(self.rows)

    def _finish(self):
        self["header"].setText("Diagnostics — done (%d checks)" % len(self.rows))