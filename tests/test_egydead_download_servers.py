# -*- coding: utf-8 -*-
"""PATCH 109 regression test: EgyDead's real download-servers list. DL_FIXTURE is the actual
downloadMaster block from a real page capture (Minerva Academy S01E08, 2026-09-22) -- note the
site's own CSS class name typo, "donwload-servers-list", preserved exactly as captured."""

import extractors.egydead as eg

DL_FIXTURE = """<div class=\"downloadMaster\">
			<div class=\"container\">
				<h1 class=\"TitleMaster\">
					<span>
						<em>\u0633\u064a\u0631\u0641\u0631\u0627\u062a \u0627\u0644\u062a\u062d\u0645\u064a\u0644</em>
					</span>
					<a class=\"bactToSingle\" href=\"https://tv10.egydead.live/episode/minerva-academy-s01e08/\">\u0631\u062c\u0648\u0639 \u0644\u0644\u0645\u0648\u0636\u0648\u0639 \u0627\u0644\u0627\u0635\u0644\u0649</a>
					<div class=\"clr\"></div>
				</h1>
				<ul class=\"donwload-servers-list\">
                                                                                                            <li>
                                    <span class=\"ser-name\">MegaMax</span>
                                    <div class=\"server-info\">
                                        <em>1080p</em>
                                    </div>
                                    <a onclick=\"downloadCount(335766,'li.downloadCount a');$(this).removeAttr('onclick')\" target=\"_blank\" class=\"ser-link\" href=\"https://megamax.me/download/OynO56jwZXp87\">\u062d\u0645\u0644 \u0627\u0644\u0627\u0646</a>
                                </li>                                                                                                                                                                                                               <div class=\"clr\"></div>                  
			</ul>
				
			</div>
		</div>"""

LANDING_PAGE_NO_DOWNLOADS = "<html><body><div class=\"videoWatch\">no downloadMaster here</div></body></html>"


def _ex():
    e = eg.EgyDeadExtractor()
    e._resolved_base = "https://tv10.egydead.live/"
    return e


def test_real_download_block_parses_correctly():
    e = _ex()
    out = e._parse_download_servers(DL_FIXTURE)
    assert out == [{
        "resolution": "1080p",
        "size": "",
        "quality": "MegaMax",
        "url": "https://megamax.me/download/OynO56jwZXp87",
    }], out


def test_page_with_no_download_block_returns_empty():
    e = _ex()
    assert e._parse_download_servers(LANDING_PAGE_NO_DOWNLOADS) == []
    assert e._parse_download_servers("") == []
    assert e._parse_download_servers(None) == []


def test_get_page_uses_the_real_list_when_present():
    e = _ex()
    page = "<html><body>" + DL_FIXTURE + "</body></html>"
    e._fetch = lambda u, post_data=None: (page, u)
    r = e.get_page("https://tv10.egydead.live/episode/minerva-academy-s01e08/")
    assert r["downloads"] == [{
        "resolution": "1080p", "size": "", "quality": "MegaMax",
        "url": "https://megamax.me/download/OynO56jwZXp87",
    }]


def test_get_page_falls_back_to_megamax_mirrors_when_no_real_list():
    e = _ex()
    # a page with watch servers but NO downloadMaster block at all
    li = ("<ul class=\"serversList\"><li data-link=\"https://example.com/embed/x\">"
          "<span><p>ExampleHost</p></span></li></ul>")
    page = "<html><body>" + li + "</body></html>"
    e._fetch = lambda u, post_data=None: (page, u)
    e._extract_watch_servers = lambda html, url: [{"name": "ExampleHost", "url": "https://example.com/embed/x", "type": "embed", "quality": ""}]
    e._pull_downloads = lambda servers: [{"resolution": "fallback", "size": "", "quality": "X", "url": "y"}]
    r = e.get_page("https://tv10.egydead.live/episode/no-download-list/")
    assert r["downloads"] == [{"resolution": "fallback", "size": "", "quality": "X", "url": "y"}]
