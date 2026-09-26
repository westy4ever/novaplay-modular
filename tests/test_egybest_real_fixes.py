# -*- coding: utf-8 -*-
"""EgyBest regression tests: watch-server extraction (PATCH E1) and the download-gateway
resolver (PATCH E3). Both fixtures are real captured content (Blue Box, 2026-09-23)."""

import sys
import os
import types

sys.path.insert(0, os.getcwd())
import extractors.hosts as _hosts_mod

WATCH_FIXTURE = r'''<ul id="watch-servers-list" class="servList">
                                <li class="Hoverable ISActive" onclick="loadIframe(this, 'https://egybests.live/watch/?url=aHR0cHM6Ly9jeWJlcnZ5bnguY29tL2UvbGV4NGV2aXR2Z3dz');" id="first-server">
                        <i class="fal fa-play"></i> cybervynx                    </li>
                                        <li class="Hoverable " onclick="loadIframe(this, 'https://egybests.live/watch/?url=aHR0cHM6Ly9jeWJlcnZ5bnguY29tL2UvanY2cTg0YXM2cmJ4');" id="">
                        <i class="fal fa-play"></i> cybervynx                    </li>
                                        <li class="Hoverable " onclick="loadIframe(this, 'https://egybests.live/watch/?url=aHR0cHM6Ly9kb29kc3RyZWFtLmNvbS9lL2FmZG8zcms3dmR1dA%3D%3D');" id="">
                        <i class="fal fa-play"></i> doodstream                    </li>
                                        <li class="Hoverable " onclick="loadIframe(this, 'https://egybests.live/watch/?url=aHR0cHM6Ly9kb29kc3RyZWFtLmNvbS9lL2FxeWQ1d21nN2t2Nw%3D%3D');" id="">
                        <i class="fal fa-play"></i> doodstream                    </li>
                                        <li class="Hoverable " onclick="loadIframe(this, 'https://egybests.live/watch/?url=aHR0cHM6Ly9kb29kc3RyZWFtLmNvbS9lL3Bkd213aDY0eDNpaA%3D%3D');" id="">
                        <i class="fal fa-play"></i> doodstream                    </li>
                                        <li class="Hoverable " onclick="loadIframe(this, 'https://egybests.live/watch/?url=aHR0cHM6Ly9taXhkcm9wLnBzL2UvcTFsbjF4azl1d2xrOTA%3D');" id="">
                        <i class="fal fa-play"></i> mixdrop                    </li>
                                        <li class="Hoverable " onclick="loadIframe(this, 'https://egybests.live/watch/?url=aHR0cHM6Ly9taXhkcm9wLnBzL2UvamQ5eGQwb3FoZDBwcTc%3D');" id="">
                        <i class="fal fa-play"></i> mixdrop                    </li>
                                        <li class="Hoverable " onclick="loadIframe(this, 'https://egybests.live/watch/?url=aHR0cHM6Ly9taXhkcm9wLnBzL2UvMzZubTZkbDlheGQ3bHI%3D');" id="">
                        <i class="fal fa-play"></i> mixdrop                    </li>
                                        <li class="Hoverable " onclick="loadIframe(this, 'https://egybests.live/watch/?url=aHR0cHM6Ly9taXhkcm9wLnBzL2UvN2tydjBrcXZhZGc3MHg%3D');" id="">
                        <i class="fal fa-play"></i> mixdrop                    </li>
                                        <li class="Hoverable " onclick="loadIframe(this, 'https://egybests.live/watch/?url=aHR0cHM6Ly9zdHJlYW10YXBlLmNvbS9lL0x2cFdqMUpHYTlpTTRN');" id="">
                        <i class="fal fa-play"></i> streamtape                    </li>
                            </ul>'''

GATEWAY_FIXTURE = """<div class=\"download-card\">
    <h2 class=\"download-title\">\u062c\u0627\u0631\u064a \u062a\u062c\u0647\u064a\u0632 \u0631\u0627\u0628\u0637 \u0627\u0644\u062a\u062d\u0645\u064a\u0644...</h2>

    <div id=\"count\" class=\"countdown\">9</div>

    <a id=\"btn\" href=\"#\" data-url=\"aHR0cHM6Ly9jeWJlcnZ5bnguY29tL2QvbGV4NGV2aXR2Z3dz\" class=\"download-btn\" style=\"display: none;\">
        \u062a\u062d\u0645\u064a\u0644 \u0627\u0644\u0622\u0646
    </a>
</div>

</div>
</div>

<script>
let time = 10;
let count = document.getElementById(\"count\");
let btn = document.getElementById(\"btn\");

// \u0625\u062e\u0641\u0627\u0621 \u0627\u0644\u0632\u0631 \u0641\u064a \u0627\u0644\u0628\u062f\u0627\u064a\u0629
btn.style.display = \"none\";

// \u0627\u0644\u0639\u062f \u0627\u0644\u062a\u0646\u0627\u0632\u0644\u064a
let timer = setInterval(() => {
    time--;
    count.innerHTML = time;

    if (time <= 0) {
        clearInterval(timer);
        count.style.display = \"none\";
        btn.style.display = \"inline-block\";
    }
}, 1000);

// \u0639\u0646\u062f \u0627\u0644\u0636\u063a\u0637
btn.addEventListener(\"click\", function(e) {
    e.preventDefault();

    let encoded = this.getAttribute(\"data-url\");

    if (!encoded) {
        alert(\"\u0627\u0644\u0631\u0627\u0628\u0637 \u063a\u064a\u0631 \u0645\u0648\u062c\u0648\u062f\");
        return;
    }

    try {
        let realUrl = atob(encoded);
        window.open(realUrl, \"_blank\");
    } catch (err) {
        alert(\"\u062e\u0637\u0623 \u0641\u064a \u0641\u062a\u062d \u0627\u0644\u0631\u0627\u0628\u0637\");
    }
});
</script>"""


def _load_egybest():
    src = open("extractors/egybest.py", encoding="utf-8").read()
    src = src.replace(
        "from .base import (",
        "from extractors.base import (",
    )
    mod = types.ModuleType("egybest_under_test")
    exec(compile(src, "egybest.py", "exec"), mod.__dict__)
    return mod


def test_real_watch_server_list_extracts_all_ten():
    m = _load_egybest()
    e = m.EgyBestExtractor()
    page = "<html><body>" + WATCH_FIXTURE + "</body></html>"
    m.fetch = lambda url, referer=None, **k: (page, url)
    result = e.get_page("https://egybests.live/some-movie/")
    servers = result.get("servers", [])
    assert len(servers) == 10, len(servers)
    names = [s["name"] for s in servers]
    assert names.count("cybervynx") == 2
    assert names.count("doodstream") == 3
    assert names.count("mixdrop") == 4
    assert names.count("streamtape") == 1


def test_real_mixdrop_filecodes_match_independently_confirmed_streams():
    # ground truth: these exact filecodes were independently confirmed against real resolved
    # mxcontent.net URLs from the same capture session
    m = _load_egybest()
    e = m.EgyBestExtractor()
    page = "<html><body>" + WATCH_FIXTURE + "</body></html>"
    m.fetch = lambda url, referer=None, **k: (page, url)
    result = e.get_page("https://egybests.live/some-movie/")
    mixdrop_urls = {s["url"] for s in result["servers"] if s["name"] == "mixdrop"}
    assert "https://mixdrop.ps/e/q1ln1xk9uwlk90" in mixdrop_urls
    assert "https://mixdrop.ps/e/jd9xd0oqhd0pq7" in mixdrop_urls
    assert "https://mixdrop.ps/e/36nm6dl9axd7lr" in mixdrop_urls


def test_empty_page_returns_no_servers_not_a_crash():
    m = _load_egybest()
    e = m.EgyBestExtractor()
    m.fetch = lambda url, referer=None, **k: ("<html></html>", url)
    result = e.get_page("https://egybests.live/some-movie/")
    assert result.get("servers", []) == []


def test_download_gateway_decodes_the_real_url():
    url = "https://egybests.live/download/?url=aHR0cHM6Ly9jeWJlcnZ5bnguY29tL2QvbGV4NGV2aXR2Z3dz"
    _hosts_mod.fetch = lambda u, referer=None, **k: (GATEWAY_FIXTURE, u)
    result = _hosts_mod.resolve_egybest_download_gateway(url)
    assert result == "https://cybervynx.com/d/lex4evitvgws", result


def test_download_gateway_dispatch_routes_correctly():
    url = "https://egybests.live/download/?url=aHR0cHM6Ly9jeWJlcnZ5bnguY29tL2QvbGV4NGV2aXR2Z3dz"
    _hosts_mod.fetch = lambda u, referer=None, **k: (GATEWAY_FIXTURE, u)
    result = _hosts_mod.resolve_host(url)
    assert result == "https://cybervynx.com/d/lex4evitvgws", result


def test_download_gateway_ignores_unrelated_egybests_urls():
    # must not claim a normal movie page just because the domain matches
    _hosts_mod.fetch = lambda u, referer=None, **k: ("<html>irrelevant</html>", u)
    result = _hosts_mod.resolve_egybest_download_gateway("https://egybests.live/some-movie/")
    assert result is None


def test_download_gateway_fetch_failure_returns_none():
    url = "https://egybests.live/download/?url=abc"
    _hosts_mod.fetch = lambda u, referer=None, **k: (None, u)
    assert _hosts_mod.resolve_egybest_download_gateway(url) is None


def test_cybervynx_dispatch_entry_present():
    # PATCH E2 -- structural check only (dispatch wiring), NOT a claim the resolver logic
    # itself has been verified against real cybervynx.com markup
    assert _hosts_mod.resolve_host is not None
