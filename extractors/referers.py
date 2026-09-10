# -*- coding: utf-8 -*-
"""One referer table for the whole scraping layer.

Consolidates four drifted copies (fetch PATH A curl_cffi chain, fetch
PATH B urllib chain, extract_stream's direct-URL branch, and
per-resolver hardcodes). When a host's referer changes, change it HERE.

Merge rules: PATH B (most complete) wins; streamruby's egydead probe
list stays inside the resolver (it's probe logic, not a default);
token-bearing CDNs are None = self-referer.
"""

from urllib.parse import urlparse

_REFERER_TABLE = {
    # site domains → their canonical referer
    "wecima":        "https://wecima.click/",
    "wecima.cx":     "https://wecima.cx/",
    "mycima":        "https://wecima.click/",
    "arabseed":      "https://arabseeds.cam/",
    "topcinema":     "https://topcinemaa.top/",
    "shaheed":       "https://shahid4u.solar/",
    "shahid4u":      "https://shahid4u.solar/",
    "fasel":         "https://faselhd.rip/",
    "faselhdx":      "https://web5106x.faselhdx.bid/",
    "web596x":       "https://web5106x.faselhdx.bid/",
    "web5106x":      "https://web5106x.faselhdx.bid/",
    "govid":         "https://faselhd.rip/",
    "datahowa":      "https://faselhd.rip/",
    "scdns":         "https://web5106x.faselhdx.bid/",
    "akwam":         "https://akwam.com.co/",
    "akoam":         "https://akwam.com.co/",
    "egydead":       None,   # special: self-referer (scheme://host/)
    "downet":        "https://akwam.com.co/",
    "streamwish":    "https://streamwish.to/",
    "wishfast":      "https://streamwish.to/",
    "filemoon":      "https://filemoon.sx/",
    "lulustream":    "https://lulustream.com/",
    "luluvdo":       "https://lulustream.com/",
    "ok.ru":         "https://ok.ru/",
    "vidguard":      "https://vidguard.to/",
    "vgfplay":       "https://vidguard.to/",
    "filelion":      "https://filelions.to/",
    "vidhide":       "https://filelions.to/",
    "streamhide":    "https://filelions.to/",
    "fastvid":       "https://fastvid.cam/",
    "rpmvip":        "https://shaaheid4u.rpmvip.com/",
    "upn.one":       "https://shiid4u.upn.one/",
    "upshare":       "https://shiid4u.upn.one/",
    "savefiles":     "https://savefiles.com/",
    "mxcontent":     "https://wecima.cx/",
    "delucloud":     "https://delucloud.xyz/",
    "tnmr":          "https://wecima.cx/",
    "netrocdn":      "https://moviesapi.to/",
    "shows.st":      "https://shows.st/",
    "hakunaymatata": "https://moviepire.co/",
    "moviepire":     "https://moviepire.co/",
    "polarcandy":    "https://peakstorm.top/",
    "peakstorm":     "https://peakstorm.top/",
    "xpass":         "https://play.xpass.top/",
    "1x2":           "https://play.xpass.top/",
    "nextgencloudfabric":       "https://nextgencloudfabric.com/",
    "scalablecontentengine":    "https://nextgencloudfabric.com/",
    "cloudorchestranova":       "https://cloudorchestranova.com/",
    "panoplypalaver":           "https://cloudorchestranova.com/",
    "1shows":        "https://viduki.net/",
    "viduki":        "https://viduki.net/",
    "streamrk":      "https://streamrk.site/",
    "vaplayer":      "https://vaplayer.ru/",
    "superflixapi":  "https://superflixapi.sbs/",
    "zxcstream":     "https://zxcstream.xyz/",
    "braflix":       "https://braflix.win/",
    "voe":           "https://voe.sx/",
    "masukestin":    "https://masukestin.com/",
    "hgcloud":       "https://hgcloud.to/",
    "dramiyos-cdn":  None,   # token-bearing CDNs: self-referer
    "cdn-centaurus": None,
    "vidaraa":       "https://vidaraa.cc/",
    "dood":          None,   # pass_md5 flow sets its own
    "mixdrop":       "https://mixdrop.to/",
    "miixdrop":      "https://mixdrop.to/",
    "earnvids":      None,
    "streamruby":    None,   # resolver carries its egydead referer list
}


def get_referer(url, default_self=True):
    """Referer for a URL: longest matching table key wins (so
    'web5106x.faselhdx.bid' hits the 'web5106x' entry, not 'faselhdx').
    None entries = self-referer. default_self=False returns "" instead."""
    try:
        host = urlparse(url).netloc.lower()
    except Exception:
        host = str(url or "").lower()
    best_key = ""
    best_val = ""
    for frag, ref in _REFERER_TABLE.items():
        if frag in host:
            if len(frag) > len(best_key):
                best_key = frag
                best_val = ref
    if best_key:
        if best_val is None:
            if default_self:
                try:
                    parts = urlparse(url)
                    return "{}://{}/".format(parts.scheme or "https", parts.netloc)
                except Exception:
                    return ""
            return ""
        return best_val
    if default_self:
        try:
            parts = urlparse(url)
            if parts.scheme and parts.netloc:
                return "{}://{}/".format(parts.scheme, parts.netloc)
        except Exception:
            pass
    return ""