# -*- coding: utf-8 -*-
"""PATCH 103 regression tests. The two page bodies are the real ones fetched on the box on 2026-09-21."""

from extractors import net

# streamwish.to/e/g9fmmcgby8m6 - 819 bytes, the only "cloudflare" is the Web Analytics beacon URL
STREAMWISH_LOADER = r'''<!DOCTYPE html>

<head>
    <title>Loading...</title>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <link href="/style.css" rel="stylesheet" type="text/css" />
</head>

<body>
    <div class="loading-container">
        <div class="spinner"></div>
        <div class="loading-text">Page is loading, please wait...</div>
    </div>
    <script src="/main.js?v=1.1.9"></script>
<script type="module" src="https://static.cloudflareinsights.com/beacon.min.js/v31edd6df95cf4e85bb4c19e7a9bdbcba1788362987495" integrity="sha512-iIg7k2xntmwu6/uSb5tpc/hySgZc4eoL31yB29W6tJFo2akwjPWcEqnCEdJvGexCL0KEQwVYv5BlowfhVz26hg==" data-cf-beacon='{"version":"2024.11.0","token":"e265417d76f2475eb0557f852d3dc178","r":1,"spa":2}' crossorigin="anonymous"></script>
</body>

</html>
'''

# byseraguci.com/e/d5c2ss1vuxo9 - a 1972-byte React app shell with the same beacon
BYSE_SHELL = r'''<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <title>Byse Frontend</title>
    <script>
      (function () { try { var path = window.location.pathname; if (path.indexOf('/e/') !== 0) { return; } } catch (error) {} })();
    </script>
    <script type="module" crossorigin src="/assets/index-DocunfmE.js"></script>
    <link rel="stylesheet" crossorigin href="/assets/index-CS9Rwwms.css">
  </head>
  <body>
    <div id="root"></div>
  <script type="module" src="https://static.cloudflareinsights.com/beacon.min.js/v31edd6df95cf4e85bb4c19e7a9bdbcba1788362987495" integrity="sha512-iIg7k2xntmwu6/uSb5tpc/hySgZc4eoL31yB29W6tJFo2akwjPWcEqnCEdJvGexCL0KEQwVYv5BlowfhVz26hg==" data-cf-beacon='{"version":"2024.11.0","token":"72888248f3624380b824a0874ae9e0ad","r":1,"spa":2}' crossorigin="anonymous"></script>
</body>
</html>
'''

CHALLENGE_JUST_A_MOMENT = ('<!DOCTYPE html><html><head><title>Just a moment...</title></head><body>'
                           '<div id="challenge-error-text">Enable JavaScript and cookies to continue</div>'
                           '<script src="/cdn-cgi/challenge-platform/h/b/orchestrate/chl_page/v1"></script></body></html>')
CHALLENGE_TURNSTILE = ('<html><head><title>Verify</title></head><body><div class="cf-turnstile" data-sitekey="x"></div>'
                       '<script src="https://challenges.cloudflare.com/turnstile/v0/api.js"></script></body></html>')
BLOCK_PAGE = ('<html><head><title>Attention Required! | Cloudflare</title></head><body>'
              '<h1>Sorry, you have been blocked</h1><p>Cloudflare Ray ID: <strong>8a1b2c3d4e5f</strong></p></body></html>')


def test_ordinary_small_pages_with_the_analytics_beacon_are_not_challenges():
    assert len(STREAMWISH_LOADER) < 5000 and len(BYSE_SHELL) < 5000
    assert net._is_cloudflare_challenge(STREAMWISH_LOADER) is False
    assert net._is_cloudflare_challenge(BYSE_SHELL) is False
    assert net._is_cloudflare_challenge_raw(STREAMWISH_LOADER) is True     # documents the old behaviour


def test_real_challenges_and_block_pages_are_still_detected():
    assert net._is_cloudflare_challenge(CHALLENGE_JUST_A_MOMENT)
    assert net._is_cloudflare_challenge(CHALLENGE_TURNSTILE)              # challenges.cloudflare.com is NOT benign
    assert net._is_cloudflare_challenge(BLOCK_PAGE)


def test_a_challenge_hidden_behind_a_beacon_is_still_detected():
    page = CHALLENGE_JUST_A_MOMENT.replace("</body>", STREAMWISH_LOADER.split("<body>")[1].split("</body>")[0] + "</body>")
    assert "cloudflareinsights" in page and net._is_cloudflare_challenge(page)


def test_empty_and_none_input():
    assert not net._is_cloudflare_challenge("")
    assert not net._is_cloudflare_challenge(None)
