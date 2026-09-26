# -*- coding: utf-8 -*-
"""PATCH 110 regression test: EgyDead's page info box. INFO_BOX_FIXTURE is the real "LeftBox"
block from a real page capture (Minerva Academy, episode 1, 2026-09-22)."""

import extractors.egydead as eg

INFO_BOX_FIXTURE = """<div class=\"LeftBox\">
					<ul>
													<li>
								<span>\u0627\u0644\u0642\u0633\u0645 : </span>
																	<a href=\"https://tv10.egydead.live/series-category/english-series/\">\u0645\u0633\u0644\u0633\u0644\u0627\u062a \u0627\u062c\u0646\u0628\u064a</a>
															</li>
																			<li>
								<span>\u0627\u0644\u0646\u0648\u0639 : </span>
																	<a href=\"https://tv10.egydead.live/type/thriller/\">\u0627\u062b\u0627\u0631\u0629</a>
																	<a href=\"https://tv10.egydead.live/type/drama/\">\u062f\u0631\u0627\u0645\u0627</a>
																	<a href=\"https://tv10.egydead.live/type/romance/\">\u0631\u0648\u0645\u0627\u0646\u0633\u064a</a>
																	<a href=\"https://tv10.egydead.live/type/mystery/\">\u063a\u0645\u0648\u0636</a>
															</li>
																																											<li>
								<span>\u0627\u0644\u0644\u063a\u0647 : </span>
																	<a href=\"https://tv10.egydead.live/language/%d8%a7%d9%84%d8%a7%d9%8a%d8%b7%d8%a7%d9%84%d9%8a%d8%a9/\">\u0627\u0644\u0625\u064a\u0637\u0627\u0644\u064a\u0629</a>
															</li>
																			<li>
								<span>\u0627\u0644\u0628\u0644\u062f : </span>
																	<a href=\"https://tv10.egydead.live/country/%d8%a5%d9%8a%d8%b7%d8%a7%d9%84%d9%8a%d8%a7/\">\u0625\u064a\u0637\u0627\u0644\u064a\u0627</a>
															</li>
																			<li>
								<span>\u0627\u0644\u0633\u0646\u0647 : </span>
																	<a href=\"https://tv10.egydead.live/year/2026/\">2026</a>
															</li>
																									<li>
								<span>\u0627\u0644\u0642\u0646\u0627\u0647 : </span>
																	<a href=\"https://tv10.egydead.live/channel/netflix/\">Netflix</a>
															</li>
																																											<li>
								<span>\u0645\u062f\u0647 \u0627\u0644\u0639\u0631\u0636 : </span>
								<a href=\"javascript:void(0)\">55 \u062f\u0642\u064a\u0642\u0629</a>
							</li>
											</ul>
				</div>"""

NO_BOX_PAGE = "<html><body>nothing here</body></html>"


def _ex():
    e = eg.EgyDeadExtractor()
    e._resolved_base = "https://tv10.egydead.live/"
    return e


def test_real_info_box_parses_every_present_field_correctly():
    e = _ex()
    info = e._parse_info_box(INFO_BOX_FIXTURE)
    assert info == {
        "category": "\u0645\u0633\u0644\u0633\u0644\u0627\u062a \u0627\u062c\u0646\u0628\u064a",
        "genres": "\u0627\u062b\u0627\u0631\u0629, \u062f\u0631\u0627\u0645\u0627, \u0631\u0648\u0645\u0627\u0646\u0633\u064a, \u063a\u0645\u0648\u0636",
        "language": "\u0627\u0644\u0625\u064a\u0637\u0627\u0644\u064a\u0629",
        "country": "\u0625\u064a\u0637\u0627\u0644\u064a\u0627",
        "year": "2026",
        "channel": "Netflix",
        "runtime": "55 \u062f\u0642\u064a\u0642\u0629",
    }, info


def test_fields_absent_from_the_page_are_not_invented():
    e = _ex()
    info = e._parse_info_box(INFO_BOX_FIXTURE)
    assert "quality" not in info          # this page genuinely has no quality field


def test_page_with_no_info_box_returns_empty():
    e = _ex()
    assert e._parse_info_box(NO_BOX_PAGE) == {}
    assert e._parse_info_box("") == {}
    assert e._parse_info_box(None) == {}


def test_get_page_prefers_info_box_year_and_fills_the_new_fields():
    e = _ex()
    page = "<html><body>" + INFO_BOX_FIXTURE + "</body></html>"
    e._fetch = lambda u, post_data=None: (page, u)
    r = e.get_page("https://tv10.egydead.live/episode/minerva-academy-s01e01/")
    assert r["year"] == "2026"
    assert r["genres"] == "\u0627\u062b\u0627\u0631\u0629, \u062f\u0631\u0627\u0645\u0627, \u0631\u0648\u0645\u0627\u0646\u0633\u064a, \u063a\u0645\u0648\u0636"
    assert r["country"] == "\u0625\u064a\u0637\u0627\u0644\u064a\u0627"
    assert r["channel"] == "Netflix"
    assert r["runtime"] == "55 \u062f\u0642\u064a\u0642\u0629"
    assert "quality" not in r
