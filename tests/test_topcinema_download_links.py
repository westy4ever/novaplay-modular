# -*- coding: utf-8 -*-
"""topcinema download-links regression test. DOWNLOAD_ITEMS_FIXTURE is the real download-items
block from a real topcinema.io capture (Super Troopers 3, 2026-09-23) -- 8 real providers,
confirming both the block-matching and the (href-before-class) attribute order in the real
markup. Deliberately embedded as a RAW triple-quoted string to avoid the exact class of bug
this patch itself went through: a plain (non-raw) triple-quoted string containing \\b would
have that word-boundary escape silently turned into a real backspace byte by Python's own
string-escape processing before the fixture ever reaches a regex."""

import sys
import types


def _load_topcinema():
    src = open("extractors/topcinema.py", encoding="utf-8").read()
    src = src.replace(
        "from .base import BaseExtractor, fetch, urljoin, log, resolve_iframe_chain",
        "from extractors.base import BaseExtractor, fetch, urljoin, log, resolve_iframe_chain",
    )
    src = src.replace(
        "from .referers import get_referer",
        "from extractors.referers import get_referer",
    )
    mod = types.ModuleType("topcinema_under_test")
    exec(compile(src, "topcinema.py", "exec"), mod.__dict__)
    return mod


_mod = _load_topcinema()

DOWNLOAD_ITEMS_FIXTURE = r'''<ul class="download-items">
																	<li>
										<a target="_NEW" rel="nofollow" href="https://updown.cam/g7k98lvrm9t2" class="downloadsLink green">
											<i class="fas fa-download"></i>
											<div class="text">
												<span>UpDown</span>
												<p>1080p</p>
											</div>
										</a>
									</li>
																	<li>
										<a target="_NEW" rel="nofollow" href="https://bowfile.com/5xxzX" class="downloadsLink ">
											<i class="fas fa-download"></i>
											<div class="text">
												<span>BowFile</span>
												<p>1080p</p>
											</div>
										</a>
									</li>
																	<li>
										<a target="_NEW" rel="nofollow" href="https://down.mdiaload.com/92appkh1ed9t" class="downloadsLink ">
											<i class="fas fa-download"></i>
											<div class="text">
												<span>Mdiaload</span>
												<p>1080p</p>
											</div>
										</a>
									</li>
																	<li>
										<a target="_NEW" rel="nofollow" href="https://nitroflare.com/view/E39E48E85F13FDE" class="downloadsLink ">
											<i class="fas fa-download"></i>
											<div class="text">
												<span>Nitroflare</span>
												<p>1080p</p>
											</div>
										</a>
									</li>
																	<li>
										<a target="_NEW" rel="nofollow" href="https://1fichier.com/?b600ep82sb924cjn2aa1" class="downloadsLink ">
											<i class="fas fa-download"></i>
											<div class="text">
												<span>1Fichier</span>
												<p>1080p</p>
											</div>
										</a>
									</li>
																	<li>
										<a target="_NEW" rel="nofollow" href="https://rapidgator.net/file/a9457a8e0faf479d106604293f5be0b0" class="downloadsLink ">
											<i class="fas fa-download"></i>
											<div class="text">
												<span>Rapidgator</span>
												<p>1080p</p>
											</div>
										</a>
									</li>
																	<li>
										<a target="_NEW" rel="nofollow" href="https://koramaup.com/n3pu" class="downloadsLink ">
											<i class="fas fa-download"></i>
											<div class="text">
												<span>Koramaup</span>
												<p>1080p</p>
											</div>
										</a>
									</li>
																	<li>
										<a target="_NEW" rel="nofollow" href="https://1cloudfile.com/2c84A" class="downloadsLink ">
											<i class="fas fa-download"></i>
											<div class="text">
												<span>CloudFile</span>
												<p>1080p</p>
											</div>
										</a>
									</li>
															</ul>'''

REAL_PROVIDERS = {
    "UpDown": "https://updown.cam/g7k98lvrm9t2",
    "BowFile": "https://bowfile.com/5xxzX",
    "Mdiaload": "https://down.mdiaload.com/92appkh1ed9t",
    "Nitroflare": "https://nitroflare.com/view/E39E48E85F13FDE",
    "1Fichier": "https://1fichier.com/?b600ep82sb924cjn2aa1",
    "Rapidgator": "https://rapidgator.net/file/a9457a8e0faf479d106604293f5be0b0",
    "Koramaup": "https://koramaup.com/n3pu",
    "CloudFile": "https://1cloudfile.com/2c84A",
}


def _ex():
    return _mod.TopCinemaExtractor()


def test_all_eight_real_providers_extracted_correctly():
    e = _ex()
    downloads = e._extract_download_links(DOWNLOAD_ITEMS_FIXTURE)
    assert len(downloads) == 8, downloads
    got = {d["quality"]: d["url"] for d in downloads}
    assert got == REAL_PROVIDERS, got


def test_resolution_label_is_1080p_for_every_entry():
    e = _ex()
    downloads = e._extract_download_links(DOWNLOAD_ITEMS_FIXTURE)
    assert all(d["resolution"] == "1080p" for d in downloads)


def test_word_boundary_regex_does_not_regress_to_a_backspace_byte():
    # the exact bug this patch went through: a plain (non-raw) triple-quoted string in the
    # apply script silently turned \b into a real 0x08 backspace byte before it ever reached
    # topcinema.py, which made the per-item loop match nothing despite looking correct on
    # visual inspection. Checking for the byte's ABSENCE is simpler and safer than trying to
    # match the correct escape sequence with a string literal of its own.
    data = open("extractors/topcinema.py", "rb").read()
    assert b"\x08" not in data, "found a raw backspace byte in topcinema.py -- \\b escaping regressed"


def test_empty_and_missing_block_return_empty_not_a_crash():
    e = _ex()
    assert e._extract_download_links("") == []
    assert e._extract_download_links(None) == []
    assert e._extract_download_links("<html>no download list here</html>") == []
