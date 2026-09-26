# -*- coding: utf-8 -*-
"""EgyDead / MegaMax (PATCH 99) regression tests. The fixtures are REAL responses captured from
tv10.egydead.live + eg.megamax.cam on 2026-09-20 (MegaMax Inertia page, two HLS master playlists)."""

import os
import sys
import html as _html
import subprocess

import extractors.egydead as eg
from extractors.hosts import _parse_hls_master_variants, _quality_bucket

MM_JSON = r'''{"component":"files\/mirror\/video","props":{"errors":{},"streams":{"status":"success","msg":"OK","token":"20c90450a93f797e18b8031fa03d8c5e","data":[{"hashId":"2TMLV5DHApZJI","label":"1036p (source)","resolution":"1920x1036","size":3918172206,"mirrors":[{"driver":"streamruby","link":"https:\/\/stmruby.com\/embed-ctqrvn2plqkk.html","uploaded_at":"2026-09-20 13:09:33","checked_at":null},{"driver":"streamhg","link":"\/\/streamwish.to\/e\/g9fmmcgby8m6","uploaded_at":"2026-09-20 13:06:14","checked_at":null},{"driver":"uqload","link":"\/\/uqload.vc\/embed-tscgogjtxdq5.html","uploaded_at":"2026-09-20 13:12:59","checked_at":null},{"driver":"mixdrop","link":"\/\/mixdrop.ag\/e\/pjk1dnm1i6g9qo","uploaded_at":"2026-09-20 13:09:07","checked_at":null},{"driver":"byse","link":"\/\/byseraguci.com\/e\/d5c2ss1vuxo9","uploaded_at":"2026-09-20 13:06:57","checked_at":null},{"driver":"doodstream","link":"\/\/doodstream.com\/e\/4yf8ri1gcej8","uploaded_at":"2026-09-20 13:10:09","checked_at":null},{"driver":"vidara","link":"\/\/vidaraa.cc\/e\/7syr7xvKW6Ld1","uploaded_at":"2026-09-20 13:09:06","checked_at":null},{"driver":"earnvids","link":"\/\/vidhidevip.com\/v\/flc6nvdpwllr","uploaded_at":"2026-09-20 13:09:01","checked_at":null},{"driver":"voe","link":"\/\/voe.sx\/e\/0eqlw6fhwfeg","uploaded_at":"2026-09-20 13:09:41","checked_at":null}]},{"hashId":"Gv99AQuQiFpdv","label":"720p","resolution":"1280x720","size":1384583398,"mirrors":[{"driver":"streamhg","link":"\/\/streamwish.to\/e\/5tpx25730oza","uploaded_at":"2026-09-20 13:38:17","checked_at":null},{"driver":"mixdrop","link":"\/\/mixdrop.ag\/e\/z1p6m0jmbgmq4ll","uploaded_at":"2026-09-20 13:38:22","checked_at":null},{"driver":"byse","link":"\/\/byseraguci.com\/e\/n4lku69irav7","uploaded_at":"2026-09-20 13:45:06","checked_at":null},{"driver":"doodstream","link":"\/\/doodstream.com\/e\/7tl5wrpglamp","uploaded_at":"2026-09-20 13:38:29","checked_at":null},{"driver":"vidara","link":"\/\/vidaraa.cc\/e\/o9RfraU5PcYIH","uploaded_at":"2026-09-20 13:37:49","checked_at":null},{"driver":"earnvids","link":"\/\/vidhidevip.com\/v\/k0j78ns1txkc","uploaded_at":"2026-09-20 13:38:23","checked_at":null},{"driver":"voe","link":"\/\/voe.sx\/e\/8zb2ganqlhxg","uploaded_at":"2026-09-20 13:38:25","checked_at":null}]},{"hashId":"zMd6opTcjIbZu","label":"480p","resolution":"854x480","size":665740972,"mirrors":[{"driver":"streamhg","link":"\/\/streamwish.to\/e\/kfbzytfq688p","uploaded_at":"2026-09-20 13:36:07","checked_at":null},{"driver":"mixdrop","link":"\/\/mixdrop.ag\/e\/l76k43xxfqdd0x1","uploaded_at":"2026-09-20 13:35:59","checked_at":null},{"driver":"byse","link":"\/\/byseraguci.com\/e\/21ejvf7283pa","uploaded_at":"2026-09-20 13:36:32","checked_at":null},{"driver":"doodstream","link":"\/\/doodstream.com\/e\/yuz2z5w58p11","uploaded_at":"2026-09-20 13:36:19","checked_at":null},{"driver":"vidara","link":"\/\/vidaraa.cc\/e\/Wx0mOEfV5sYCa","uploaded_at":"2026-09-20 13:36:12","checked_at":null},{"driver":"earnvids","link":"\/\/vidhidevip.com\/v\/kjksclh54ddp","uploaded_at":"2026-09-20 13:36:09","checked_at":null},{"driver":"voe","link":"\/\/voe.sx\/e\/eh6anbenvihw","uploaded_at":"2026-09-20 13:36:11","checked_at":null}]}]}},"url":"\/iframe\/NDPBlLci0sk8E","version":"a601a2d0d16b8ae7121ceb1fd46c1f5a","sharedProps":["errors"]}'''
MASTER_STREAMRUBY = r'''#EXTM3U
#EXT-X-STREAM-INF:PROGRAM-ID=1,BANDWIDTH=373845,RESOLUTION=668x360,FRAME-RATE=60.000,CODECS="avc1.64001f,mp4a.40.2"
https://ucxipzwkyhms02.streamruby.net/hls2/01/00494/ctqrvn2plqkk_l/index-v1-a1.m3u8?t=FOWxHAy3QePrCUGEAg-sdV2FtC4GiOKMwHwBUwt8K3A&s=1789931690&e=32400&v=1843193628&i=197.164&sp=0
#EXT-X-STREAM-INF:PROGRAM-ID=1,BANDWIDTH=606147,RESOLUTION=888x480,FRAME-RATE=60.000,CODECS="avc1.64001f,mp4a.40.2"
https://ucxipzwkyhms02.streamruby.net/hls2/01/00494/ctqrvn2plqkk_n/index-v1-a1.m3u8?t=FOWxHAy3QePrCUGEAg-sdV2FtC4GiOKMwHwBUwt8K3A&s=1789931690&e=32400&v=1843193628&i=197.164&sp=0
#EXT-X-STREAM-INF:PROGRAM-ID=1,BANDWIDTH=1227421,RESOLUTION=1336x720,FRAME-RATE=60.000,CODECS="avc1.640028,mp4a.40.2"
https://ucxipzwkyhms02.streamruby.net/hls2/01/00494/ctqrvn2plqkk_h/index-v1-a1.m3u8?t=FOWxHAy3QePrCUGEAg-sdV2FtC4GiOKMwHwBUwt8K3A&s=1789931690&e=32400&v=1843193628&i=197.164&sp=0
#EXT-X-STREAM-INF:PROGRAM-ID=1,BANDWIDTH=3957554,RESOLUTION=1920x1036,FRAME-RATE=23.974,CODECS="avc1.640028,mp4a.40.2"
https://ucxipzwkyhms02.streamruby.net/hls2/01/00494/ctqrvn2plqkk_o/index-v1-a1.m3u8?t=FOWxHAy3QePrCUGEAg-sdV2FtC4GiOKMwHwBUwt8K3A&s=1789931690&e=32400&v=1843193628&i=197.164&sp=0

#EXT-X-I-FRAME-STREAM-INF:BANDWIDTH=19865,RESOLUTION=668x360,CODECS="avc1.64001f",URI="https://ucxipzwkyhms02.streamruby.net/hls2/01/00494/ctqrvn2plqkk_l/iframes-v1-a1.m3u8?t=FOWxHAy3QePrCUGEAg-sdV2FtC4GiOKMwHwBUwt8K3A&s=1789931690&e=32400&v=1843193628&i=197.164&sp=0"
#EXT-X-I-FRAME-STREAM-INF:BANDWIDTH=32231,RESOLUTION=888x480,CODECS="avc1.64001f",URI="https://ucxipzwkyhms02.streamruby.net/hls2/01/00494/ctqrvn2plqkk_n/iframes-v1-a1.m3u8?t=FOWxHAy3QePrCUGEAg-sdV2FtC4GiOKMwHwBUwt8K3A&s=1789931690&e=32400&v=1843193628&i=197.164&sp=0"
#EXT-X-I-FRAME-STREAM-INF:BANDWIDTH=61625,RESOLUTION=1336x720,CODECS="avc1.640028",URI="https://ucxipzwkyhms02.streamruby.net/hls2/01/00494/ctqrvn2plqkk_h/iframes-v1-a1.m3u8?t=FOWxHAy3QePrCUGEAg-sdV2FtC4GiOKMwHwBUwt8K3A&s=1789931690&e=32400&v=1843193628&i=197.164&sp=0"
#EXT-X-I-FRAME-STREAM-INF:BANDWIDTH=215189,RESOLUTION=1920x1036,CODECS="avc1.640028",URI="https://ucxipzwkyhms02.streamruby.net/hls2/01/00494/ctqrvn2plqkk_o/iframes-v1-a1.m3u8?t=FOWxHAy3QePrCUGEAg-sdV2FtC4GiOKMwHwBUwt8K3A&s=1789931690&e=32400&v=1843193628&i=197.164&sp=0"
'''
MASTER_STREAMWISH_TXT = r'''#EXTM3U
#EXT-X-STREAM-INF:PROGRAM-ID=1,BANDWIDTH=541321,RESOLUTION=888x480,FRAME-RATE=23.974,CODECS="avc1.64001f,mp4a.40.2"
index-f1-v1-a1.txt
#EXT-X-STREAM-INF:PROGRAM-ID=1,BANDWIDTH=3957554,RESOLUTION=1920x1036,FRAME-RATE=23.974,CODECS="avc1.640028,mp4a.40.2"
index-f2-v1-a1.txt

#EXT-X-I-FRAME-STREAM-INF:BANDWIDTH=36484,RESOLUTION=888x480,CODECS="avc1.64001f",URI="iframes-f1-v1-a1.txt"
#EXT-X-I-FRAME-STREAM-INF:BANDWIDTH=215189,RESOLUTION=1920x1036,CODECS="avc1.640028",URI="iframes-f2-v1-a1.txt"
'''
WATCH_LI = '''<ul class="serversList"><li data-link="https://megamax.me/iframe/NDPBlLci0sk8E" class="active"><span><p>MegaMax</p></span></li></ul>'''
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _wrap(js):
    return '<div id="app" data-page="%s"></div>' % _html.escape(js, quote=True)


def _ex():
    e = eg.EgyDeadExtractor()
    e._resolved_base = "https://tv10.egydead.live/"
    return e


def test_inertia_page_all_forms():
    e = _ex()
    for txt in (MM_JSON, _wrap(MM_JSON), "<script>var p = %s;</script>" % MM_JSON):
        assert e._parse_inertia_page(txt)["component"] == "files/mirror/video"
    assert e._parse_inertia_page("<html>x</html>") is None
    assert e._parse_inertia_page("") is None


def test_megamax_servers_quality_x_mirror():
    e = _ex()
    e._fetch = lambda u, referer=None, post_data=None: (_wrap(MM_JSON), u)
    s = e._megamax_servers("https://megamax.me/iframe/NDPBlLci0sk8E")
    assert len(s) == 23
    assert s[0]["name"] == "StreamRuby (1080p, 3.65 GB)"
    assert s[0]["url"] == "https://stmruby.com/embed-ctqrvn2plqkk.html"
    assert all(x["url"].startswith("https://") for x in s)
    assert any(x["name"] == "MixDrop (480p, 634.9 MB)" for x in s)


def test_get_page_returns_servers_and_downloads():
    e = _ex()
    page = "<html><body>" + WATCH_LI + "</body></html>"
    e._fetch = lambda u, referer=None, post_data=None: ((page if "michael" in u else _wrap(MM_JSON)), u)
    r = e.get_page("https://tv10.egydead.live/michael-2026-1080p-bluray/")
    assert len(r["servers"]) == 23 and len(r["downloads"]) == 23
    assert all("_dl" not in s for s in r["servers"])
    assert set(r["downloads"][0]) == {"resolution", "size", "quality", "url"}


def test_unreadable_megamax_keeps_the_single_server():
    e = _ex()
    page = "<html><body>" + WATCH_LI + "</body></html>"
    e._fetch = lambda u, referer=None, post_data=None: ((page if "michael" in u else "<html>challenge</html>"), u)
    r = e.get_page("https://tv10.egydead.live/michael-2026-1080p-bluray/")
    assert [s["name"] for s in r["servers"]] == ["MegaMax"] and r["downloads"] == []


def test_master_labels_use_width_and_best_first():
    assert _quality_bucket(1920, 1036) == "1080p"      # letterboxed 1080p (was labelled 720p / '1036p')
    assert _quality_bucket(1336, 720) == "720p"
    assert _quality_bucket(854, 460) == "480p"
    assert _quality_bucket(668, 360) == "360p"
    e = _ex()
    u = "https://x.streamruby.net/hls2/01/00494/ctqrvn2plqkk_,l,n,h,o,.urlset/master.m3u8"
    a = _parse_hls_master_variants(u, MASTER_STREAMRUBY)
    b = e._parse_master_playlist(u, MASTER_STREAMRUBY)
    assert [l for l, _ in a] == [l for l, _ in b] == ["1080p", "720p", "480p", "360p"]
    assert "_o/" in a[0][1] and "iframes" not in "".join(x for _, x in a)
    w = "https://cdn.example/hls3/01/master.txt"
    assert [l for l, _ in _parse_hls_master_variants(w, MASTER_STREAMWISH_TXT)] == ["1080p", "480p"]


def test_dedicated_resolvers_are_bound_in_a_fresh_process():
    # a fresh interpreter: an already-imported module in this process would hide the bug
    code = ("from extractors import egydead as e; print(all(getattr(e, n) is not None for n in "
            "('resolve_streamruby','resolve_mixdrop','resolve_doodstream','resolve_streamwish',"
            "'resolve_voe','resolve_vidguard','resolve_byselapuix','resolve_govid')))")
    out = subprocess.check_output([sys.executable, "-c", code], cwd=HERE, stderr=subprocess.DEVNULL)
    assert out.decode().strip().splitlines()[-1] == "True"
