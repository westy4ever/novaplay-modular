# -*- coding: utf-8 -*-
"""onlyflix.py regression tests: bogus related-movie "servers" (PATCH O1), TV series page
misclassified as episode (PATCH O2), and allSeasonsData using const instead of var (PATCH O3).
All fixtures are real captured content (I Was a Stranger / Lanterns, 2026-09-24)."""

import sys
import os

sys.path.insert(0, os.getcwd())
import extractors.onlyflix as _ofx_mod

SERVERS_FIXTURE = r'''<ul class="nav nav-tabs player-server-tabs player-server-tabs--async mt-3 mb-4" role="tablist"><li class="nav-item" role="presentation"><button type="button" class="nav-link player-tab-btn" data-player-url="https://share.cdnm.ink/embed/imdb/tt21272942">Server 1</button></li><li class="nav-item" role="presentation"><button type="button" class="nav-link player-tab-btn" data-player-url="https://vidapi.xyz/embed/movie/tt21272942">Server 2</button></li><li class="nav-item" role="presentation"><button type="button" class="nav-link player-tab-btn" data-player-url="https://sv2.nontongo.stream/soap/movie/tt21272942">Server 3</button></li><li class="nav-item" role="presentation"><button type="button" class="nav-link player-tab-btn active" data-player-url="https://vidfast.vc/movie/tt21272942">Server 4</button></li></ul>
<article class="of-title-card-v2" data-of-title-card-v2="" data-ofa-post-id="36805" data-ofa-kind="movies" data-title="Light It Up" data-url="https://onlyflix.to/light-it-up/" data-backdrop="https://onlyflix.to/wp-content/uploads/onlyflix-backdrops/36805_singlepage_mobile.jpg?t=1786037030" data-year="1999" data-rating="6" data-rating-tier="ok" data-runtime="1 h 39 min" data-genres="Drama, Thriller" data-overview="A group of teens are bent on improving the run-down conditions of their high-school.">
            <a class="of-title-card-v2__link" href="https://onlyflix.to/light-it-up/" aria-label="Light It Up">
                <span class="of-title-card-v2__media">
                    <img class="of-title-card-v2__poster" src="https://onlyflix.to/wp-content/uploads/posters/36805_medium.jpg?v=1764101056" alt="Light It Up" width="300" height="450" loading="lazy" decoding="async" style="">
                                            <span class="of-title-card-v2__rating of-title-card-v2__rating--ok" style="--of-rating-progress:60%">
                            <svg class="of-title-card-v2__rating-ring" viewBox="0 0 44 44" aria-hidden="true">
                                <circle class="of-title-card-v2__rating-track" cx="22" cy="22" r="18.5" pathLength="100"></circle>
                                <circle class="of-title-card-v2__rating-progress" cx="22" cy="22" r="18.5" pathLength="100" stroke-dasharray="60 100"></circle>
                            </svg>
                            <span class="of-title-card-v2__rating-content">
                                <span>6.0</span>
                            </span>
                        </span>
                                        <span class="of-title-card-v2__play" aria-hidden="true">
                        <span class="of-title-card-v2__play-ripple"></span>
                                                <svg viewBox="0 0 64 64" focusable="false" aria-hidden="true">
                            <defs>
                                <linearGradient id="of-card-play-gradient-36805" x1="12" y1="9" x2="53" y2="55" gradientUnits="userSpaceOnUse">
                                    <stop offset="0" stop-color="#23e9e8"></stop>
                                    <stop offset=".52" stop-color="#147cff"></stop>
                                    <stop offset="1" stop-color="#ef37e8"></stop>
                                </linearGradient>
                            </defs>
                            <path fill="url(#of-card-play-gradient-36805)" d="M20.4 8.8C15.8 6.1 10 9.4 10 14.7v34.6c0 5.3 5.8 8.6 10.4 5.9l29.9-17.3c4.6-2.6 4.6-9.2 0-11.8L20.4 8.8Z"></path>
                        </svg>
                    </span>
                </span>
                <span class="of-title-card-v2__body">
                    <span class="of-title-card-v2__title">Light It Up</span>
                                            <span class="of-title-card-v2__meta-line">
                            <span class="of-title-card-v2__genres">Drama, Thriller</span>                            <i aria-hidden="true"></i>                            <span class="of-title-card-v2__year">1999</span>                        </span>
                                    </span>
            </a>
									<div class="ofa-card-actions" data-ofa-card-actions="" data-ofa-post-id="36805" data-ofa-type="movie" data-ofa-state-known="1" data-ofa-card-bound="1" data-ofa-counts-skin="3">
			<button type="button" class="ofa-title-action ofa-title-action--favorite" data-ofa-card-favorite="" aria-pressed="false" aria-label="Add to Favorites" title="Add to Favorites" data-ofa-public-count="0" data-ofa-count-type="favorite" data-ofa-press-bound="1"><span class="ofa-title-action__icon" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78L12 21.23l8.84-8.84a5.5 5.5 0 0 0 0-7.78Z"></path></svg></span><small class="ofa-title-action__count" data-ofa-action-count="" aria-hidden="true">0</small></button>
					</div>			        </article>'''

TYPE_FIXTURE = r'''<body class="wp-singular tvshows-template-default single single-tvshows postid-1547757 wp-theme-Onlyflix20 ofa-action-effect-1 ofa-folder-skin-1 ofa-header-skin-1 ofa-counts-skin-3 ofa-title-layout-1 of-title-cards-v2 of-single-page-backdrop-active dark-mode ofa-login-active of-mobile-header-paused of-drawer-open of-drawer-is-open of-ui-locked" style="overflow: hidden;">
<div class="player-frame player-frame--lazy player-frame--async mb-3 player-frame--loaded" data-player-ajax-url="https://onlyflix.to/wp-admin/admin-ajax.php" data-player-nonce="ee003eeb3f" data-player-post-id="1548186" data-player-content-type="episode" data-player-no-servers-text="No available servers right now." data-player-direct="0" data-of-player-initialized="1">
<script type="application/ld+json">
{
    "@context": "https://schema.org",
    "@type": "TVSeries",
    "name": "Lanterns",
    "url": "https://onlyflix.to/series/lanterns/",
    "description": "Two intergalactic cops, new recruit John Stewart and Lantern legend Hal Jordan, are drawn into a dark, earth-based mystery as they investigate a murder in the American heartland.",
    "image": "https://image.tmdb.org/t/p/w500/rb94rKVIzLyfWufIN7WqLvadBDH.jpg",
    "startDate": "2026-08-16",
    "endDate": "2026-08-16",
    "genre": [
        "Action &amp; Adventure",
        "Drama",
        "Mystery",
        "Sci-Fi &amp; Fantasy"
    ],
    "countryOfOrigin": [
        {
            "@type": "Country",
            "name": "United States of America"
        }
    ],
    "numberOfSeasons": 1,
    "numberOfEpisodes": 8,
    "identifier": {
        "@type": "PropertyValue",
        "propertyID": "IMDb",
        "value": "tt26545992",
        "url": "https://www.imdb.com/title/tt26545992/"
    },
    "aggregateRating": {
        "@type": "AggregateRating",
        "ratingValue": "7.8",
        "bestRating": "10",
        "worstRating": "1",
        "ratingCount": "37447"
    },
    "director": {
        "@type": "Person",
        "name": "Chris Mundy"
    },
    "actor": [
        {
            "@type": "Person",
            "name": "Aaron Pierre"
        },
        {
            "@type": "Person",
            "name": "Garret Dillahunt"
        },
        {
            "@type": "Person",
            "name": "Kelly Macdonald"
        },
        {
            "@type": "Person",
            "name": "Kyle Chandler"
        },
        {
            "@type": "Person",
            "name": "Poorna Jagannathan"
        }
    ],
    "publisher": {
        "@type": "Organization",
        "name": "Onlyflix",
        "url": "https://onlyflix.to/"
    }
}
</script>'''

EPISODES_FIXTURE = r'''<script>
    document.addEventListener('DOMContentLoaded', function () {
        const seasonSelect = document.getElementById('seasonSelect');
        const episodesContainer = document.getElementById('episodes-container');

        const allSeasonsData = {"1":{"1":{"id":1547758,"title":"Lanterns &#8211; Season 1 Episode 1: Pilot","permalink":"https:\/\/onlyflix.to\/episodes\/lanterns-season-1-episode-1-pilot\/","season":1,"episode":1},"2":{"id":1547815,"title":"Lanterns &#8211; Season 1 Episode 2: Episode 2","permalink":"https:\/\/onlyflix.to\/episodes\/lanterns-season-1-episode-2-episode-2\/","season":1,"episode":2},"3":{"id":1547893,"title":"Lanterns &#8211; Season 1 Episode 3: Episode 3","permalink":"https:\/\/onlyflix.to\/episodes\/lanterns-season-1-episode-3-episode-3\/","season":1,"episode":3},"4":{"id":1547983,"title":"Lanterns &#8211; Season 1 Episode 4: The Weenie","permalink":"https:\/\/onlyflix.to\/episodes\/lanterns-season-1-episode-4-the-weenie\/","season":1,"episode":4},"5":{"id":1548117,"title":"Lanterns &#8211; Season 1 Episode 5: Lights Out","permalink":"https:\/\/onlyflix.to\/episodes\/lanterns-season-1-episode-5-lights-out\/","season":1,"episode":5},"6":{"id":1548186,"title":"Lanterns &#8211; Season 1 Episode 6: Bad Optics","permalink":"https:\/\/onlyflix.to\/episodes\/lanterns-season-1-episode-6-bad-optics\/","season":1,"episode":6}}};

        if (seasonSelect && episodesContainer) {
            seasonSelect.addEventListener('change', function () {
                showSeasonEpisodes(this.value);
            });
        }

        function showSeasonEpisodes(seasonNum) {
            if (!allSeasonsData[seasonNum]) return;

            const episodes = allSeasonsData[seasonNum];
            let html = '<div class="row episode-list-row" id="season-' + seasonNum + '-episodes">';

            Object.keys(episodes).forEach(episodeNum => {
                const episode = episodes[episodeNum];
                const episodeTitle = episode.title ? episode.title : 'Episode ' + episodeNum;
                html += `
                    <div class="col-lg-3 col-md-4 col-sm-6 mb-3 episode-list-col">
                        <div class="card episode-card-glass">
                            <div class="card-body episode-card-body">
                                <div class="episode-card-kicker">Episode ${episodeNum}</div>
                                <h6 class="episode-card-title">${episodeTitle}</h6>
                                <a href="${episode.permalink}" class="episode-watch-link">Watch</a>
                            </div>
                        </div>
                    </div>
                `;
            });

            html += '</div>';
            episodesContainer.innerHTML = html;
        }

        // Recently Viewed functionality
        initializeRecentlyViewed();

        // Arrows + drag-to-scroll for carousels
        initMcsScrolls();
    });

    function initMcsScrolls() {
        document.querySelectorAll('.mcs-wrap').forEach(wrap => {
            const scroll = wrap.querySelector('.mcs-scroll');
            if (!scroll) return;
            const leftBtn  = wrap.querySelector('.mcs-arrow-left');
            const rightBtn = wrap.querySelector('.mcs-arrow-right');
            if (leftBtn)  leftBtn.addEventListener('click',  () => scroll.scrollBy({left: -480, behavior: 'smooth'}));
            if (rightBtn) rightBtn.addEventListener('click', () => scroll.scrollBy({left:  480, behavior: 'smooth'}));
        });
        document.querySelectorAll('.mcs-scroll').forEach(scroll => {
            let isDown = false, startX, scrollLeft;
            scroll.addEventListener('mousedown', e => {
                isDown = true; scroll.classList.add('is-dragging');
                startX = e.pageX - scroll.offsetLeft;
                scrollLeft = scroll.scrollLeft;
            });
            scroll.addEventListener('mouseleave', () => { isDown = false; scroll.classList.remove('is-dragging'); });
            scroll.addEventListener('mouseup',    () => { isDown = false; scroll.classList.remove('is-dragging'); });
            scroll.addEventListener('mousemove',  e => {
                if (!isDown) return;
                e.preventDefault();
                const x = e.pageX - scroll.offsetLeft;
                scroll.scrollLeft = scrollLeft - (x - startX) * 1.5;
            });
        });
    }

function initializeRecentlyViewed() {
    const container = document.getElementById('recently-viewed-container');
    const block = document.getElementById('recently-viewed-block');

    if (!container || !block) return;

    const currentItem = {
        id: 1547757,
        type: 'tvshows',
        title: 'Lanterns',
        url: 'https://onlyflix.to/series/lanterns/',
        poster: 'https://onlyflix.to/wp-content/uploads/posters/1547757_small.jpg?t=1786837785',
        year: '2026',
        rating: '6.5'
    };

    try {
        let items = JSON.parse(localStorage.getItem('mcs_recently_viewed') || '[]');

        items = items.filter(item => String(item.id) !== String(currentItem.id));
        items.unshift(currentItem);
        items = items.slice(0, 12);

        localStorage.setItem('mcs_recently_viewed', JSON.stringify(items));

        const visibleItems = items.filter(item => String(item.id) !== String(currentItem.id)).slice(0, 12);

        renderRecentlyViewed(visibleItems);
    } catch (e) {
        block.style.display = 'none';
    }
}

function renderRecentlyViewed(items) {
    const container = document.getElementById('recently-viewed-container');
    const block = document.getElementById('recently-viewed-block');

    if (!container || !block) return;

    if (!items || !items.length) {
        block.style.display = 'none';
        container.innerHTML = '';
        return;
    }

    block.style.display = '';

    let html = '';

    items.forEach(item => {
        const title = item.title ? item.title.replace(/"/g, '&quot;') : '';
        const poster = item.poster ? item.poster : 'https://onlyflix.to/wp-content/themes/Onlyflix%202.0/assets/images/noimg.png';

        html += `
            <div class="recent-card">
                <a href="${item.url}" class="recent-card__link">
                    <div class="recent-card__poster-wrap">
                        <img
                            src="${poster}"
                            alt="${title}"
                            class="recent-card__poster"
                            loading="lazy"
                            onerror="this.src='https://onlyflix.to/wp-content/themes/Onlyflix%202.0/assets/images/noimg.png'">

                        <div class="recent-card__overlay">
                            <div class="recent-card__title">${item.title || ''}</div>
                            <div class="recent-card__meta">
                                <span class="recent-card__year">${item.year || ''}</span>
                                ${item.rating ? `<span class="recent-card__rating">IMDb: ${item.rating}</span>` : ''}
                            </div>
                        </div>
                    </div>
                </a>
            </div>
        `;
    });

    container.innerHTML = html;
}


</script>'''


def _ex():
    return _ofx_mod.OnlyFlixExtractor()


def test_real_servers_found_without_bogus_related_movie_entries():
    e = _ex()
    servers = e._extract_servers_from_html(SERVERS_FIXTURE, "https://onlyflix.to/i-was-a-stranger/")
    urls = [s["url"] for s in servers]
    assert len(servers) == 4, servers
    assert "https://share.cdnm.ink/embed/imdb/tt21272942" in urls
    assert "https://vidapi.xyz/embed/movie/tt21272942" in urls
    assert "https://sv2.nontongo.stream/soap/movie/tt21272942" in urls
    assert "https://vidfast.vc/movie/tt21272942" in urls
    assert not any("light-it-up" in u for u in urls), urls


def test_series_overview_page_not_misclassified_as_episode():
    e = _ex()
    page = "<html>" + TYPE_FIXTURE + "</html>"
    _ofx_mod.fetch = lambda url, referer=None, **k: (page, url)
    result = e.get_page("https://onlyflix.to/series/lanterns/")
    assert result["type"] == "series", result["type"]


def test_real_episode_page_still_correctly_detected():
    # the URL-based /episodes/ check must still correctly identify a real episode page
    e = _ex()
    page = "<html><body></body></html>"
    _ofx_mod.fetch = lambda url, referer=None, **k: (page, url)
    result = e.get_page("https://onlyflix.to/episodes/lanterns-season-1-episode-1-pilot/")
    assert result["type"] == "episode", result["type"]


def test_allseasonsdata_found_with_const_declaration():
    e = _ex()
    episodes = e._extract_tv_episodes_json(EPISODES_FIXTURE)
    assert len(episodes) == 6, episodes
    titles = [ep["title"] for ep in episodes]
    assert any("Pilot" in t for t in titles), titles
    first = [ep for ep in episodes if ep["episode"] == 1][0]
    assert first["season"] == 1
    assert first["url"] == "https://onlyflix.to/episodes/lanterns-season-1-episode-1-pilot/"


def test_empty_html_returns_no_servers_or_episodes_not_a_crash():
    e = _ex()
    assert e._extract_servers_from_html("", "https://onlyflix.to/x/") == []
    assert e._extract_tv_episodes_json("") == []


BEST_RAIL_FIXTURE = """<div class=\"ofb-grid\" data-ofb-grid=\"\"><article class=\"ofb-card\"><div class=\"ofb-card-poster\"><a class=\"ofb-card-link\" href=\"https://onlyflix.to/i-was-a-stranger/\" aria-label=\"I Was a Stranger\"></a><img src=\"https://onlyflix.to/wp-content/uploads/posters/1487813_medium.jpg?t=1776150138\" alt=\"I Was a Stranger\" loading=\"lazy\" decoding=\"async\"><span class=\"ofb-card-shade\" aria-hidden=\"true\"></span><button type=\"button\" class=\"ofb-card-play\" data-of-open-card-player=\"\" data-post-id=\"1487813\" data-kind=\"movies\" data-content-type=\"movie\" data-title=\"I Was a Stranger\" data-poster-url=\"https://onlyflix.to/wp-content/uploads/posters/1487813_medium.jpg?t=1776150138\" data-player-ajax-url=\"https://onlyflix.to/wp-admin/admin-ajax.php\" data-player-nonce=\"ee003eeb3f\" data-no-episodes-text=\"No episodes available for this series.\" data-no-servers-text=\"No available servers right now.\" data-tv-episode-ajax=\"https://onlyflix.to/wp-admin/admin-ajax.php\" data-tv-episode-nonce=\"ee003eeb3f\" data-tv-episode-action=\"mcs_home_hero_tv_episodes\" aria-label=\"Play I Was a Stranger\"><svg class=\"ofb-card-play__icon\" viewBox=\"0 0 42 42\" aria-hidden=\"true\"><circle cx=\"21\" cy=\"21\" r=\"20\"></circle><polygon points=\"17,13 30,21 17,29\"></polygon></svg></button><span class=\"of-media-card__rank-badge ofb-rank-badge\" aria-hidden=\"true\"><span class=\"of-media-card__rank-number\">1</span><span class=\"of-media-card__rank-star of-media-card__rank-star--1\"><svg viewBox=\"0 0 784.11 815.53\" aria-hidden=\"true\"><path d=\"M392.05 0c-20.9,210.08 -184.06,378.41 -392.05,407.78 207.96,29.37 371.12,197.68 392.05,407.74 20.93,-210.06 184.09,-378.37 392.05,-407.74 -207.98,-29.38 -371.16,-197.69 -392.06,-407.78z\"></path></svg></span><span class=\"of-media-card__rank-star of-media-card__rank-star--2\"><svg viewBox=\"0 0 784.11 815.53\" aria-hidden=\"true\"><path d=\"M392.05 0c-20.9,210.08 -184.06,378.41 -392.05,407.78 207.96,29.37 371.12,197.68 392.05,407.74 20.93,-210.06 184.09,-378.37 392.05,-407.74 -207.98,-29.38 -371.16,-197.69 -392.06,-407.78z\"></path></svg></span><span class=\"of-media-card__rank-star of-media-card__rank-star--3\"><svg viewBox=\"0 0 784.11 815.53\" aria-hidden=\"true\"><path d=\"M392.05 0c-20.9,210.08 -184.06,378.41 -392.05,407.78 207.96,29.37 371.12,197.68 392.05,407.74 20.93,-210.06 184.09,-378.37 392.05,-407.74 -207.98,-29.38 -371.16,-197.69 -392.06,-407.78z\"></path></svg></span><span class=\"of-media-card__rank-star of-media-card__rank-star--4\"><svg viewBox=\"0 0 784.11 815.53\" aria-hidden=\"true\"><path d=\"M392.05 0c-20.9,210.08 -184.06,378.41 -392.05,407.78 207.96,29.37 371.12,197.68 392.05,407.74 20.93,-210.06 184.09,-378.37 392.05,-407.74 -207.98,-29.38 -371.16,-197.69 -392.06,-407.78z\"></path></svg></span><span class=\"of-media-card__rank-star of-media-card__rank-star--5\"><svg viewBox=\"0 0 784.11 815.53\" aria-hidden=\"true\"><path d=\"M392.05 0c-20.9,210.08 -184.06,378.41 -392.05,407.78 207.96,29.37 371.12,197.68 392.05,407.74 20.93,-210.06 184.09,-378.37 392.05,-407.74 -207.98,-29.38 -371.16,-197.69 -392.06,-407.78z\"></path></svg></span></span><span class=\"ofb-card-rating ofb-rating--excellent\">\u2605 9.3</span><span class=\"ofb-card-hover\"><span class=\"ofb-card-hover__title\">I Was a Stranger</span><span class=\"ofb-card-hover__meta\"><span class=\"ofb-card-hover__rating ofb-rating--excellent\">\u2605 9.3</span><span>1h 43m</span><span>2026</span></span><span class=\"ofb-card-hover__genres\">Drama, Thriller</span></span></div></article><article class=\"ofb-card\"><div class=\"ofb-card-poster\"><a class=\"ofb-card-link\" href=\"https://onlyflix.to/project-hail-mary/\" aria-label=\"Project Hail Mary\"></a><img src=\"https://onlyflix.to/wp-content/uploads/posters/1487293_medium.jpg?t=1778240562\" alt=\"Project Hail Mary\" loading=\"lazy\" decoding=\"async\"><span class=\"ofb-card-shade\" aria-hidden=\"true\"></span><button type=\"button\" class=\"ofb-card-play\" data-of-open-card-player=\"\" data-post-id=\"1487293\" data-kind=\"movies\" data-content-type=\"movie\" data-title=\"Project Hail Mary\" data-poster-url=\"https://onlyflix.to/wp-content/uploads/posters/1487293_medium.jpg?t=1778240562\" data-player-ajax-url=\"https://onlyflix.to/wp-admin/admin-ajax.php\" data-player-nonce=\"ee003eeb3f\" data-no-episodes-text=\"No episodes available for this series.\" data-no-servers-text=\"No available servers right now.\" data-tv-episode-ajax=\"https://onlyflix.to/wp-admin/admin-ajax.php\" data-tv-episode-nonce=\"ee003eeb3f\" data-tv-episode-action=\"mcs_home_hero_tv_episodes\" aria-label=\"Play Project Hail Mary\"><svg class=\"ofb-card-play__icon\" viewBox=\"0 0 42 42\" aria-hidden=\"true\"><circle cx=\"21\" cy=\"21\" r=\"20\"></circle><polygon points=\"17,13 30,21 17,29\"></polygon></svg></button><span class=\"of-media-card__rank-badge ofb-rank-badge\" aria-hidden=\"true\"><span class=\"of-media-card__rank-number\">2</span><span class=\"of-media-card__rank-star of-media-card__rank-star--1\"><svg viewBox=\"0 0 784.11 815.53\" aria-hidden=\"true\"><path d=\"M392.05 0c-20.9,210.08 -184.06,378.41 -392.05,407.78 207.96,29.37 371.12,197.68 392.05,407.74 20.93,-210.06 184.09,-378.37 392.05,-407.74 -207.98,-29.38 -371.16,-197.69 -392.06,-407.78z\"></path></svg></span><span class=\"of-media-card__rank-star of-media-card__rank-star--2\"><svg viewBox=\"0 0 784.11 815.53\" aria-hidden=\"true\"><path d=\"M392.05 0c-20.9,210.08 -184.06,378.41 -392.05,407.78 207.96,29.37 371.12,197.68 392.05,407.74 20.93,-210.06 184.09,-378.37 392.05,-407.74 -207.98,-29.38 -371.16,-197.69 -392.06,-407.78z\"></path></svg></span><span class=\"of-media-card__rank-star of-media-card__rank-star--3\"><svg viewBox=\"0 0 784.11 815.53\" aria-hidden=\"true\"><path d=\"M392.05 0c-20.9,210.08 -184.06,378.41 -392.05,407.78 207.96,29.37 371.12,197.68 392.05,407.74 20.93,-210.06 184.09,-378.37 392.05,-407.74 -207.98,-29.38 -371.16,-197.69 -392.06,-407.78z\"></path></svg></span><span class=\"of-media-card__rank-star of-media-card__rank-star--4\"><svg viewBox=\"0 0 784.11 815.53\" aria-hidden=\"true\"><path d=\"M392.05 0c-20.9,210.08 -184.06,378.41 -392.05,407.78 207.96,29.37 371.12,197.68 392.05,407.74 20.93,-210.06 184.09,-378.37 392.05,-407.74 -207.98,-29.38 -371.16,-197.69 -392.06,-407.78z\"></path></svg></span><span class=\"of-media-card__rank-star of-media-card__rank-star--5\"><svg viewBox=\"0 0 784.11 815.53\" aria-hidden=\"true\"><path d=\"M392.05 0c-20.9,210.08 -184.06,378.41 -392.05,407.78 207.96,29.37 371.12,197.68 392.05,407.74 20.93,-210.06 184.09,-378.37 392.05,-407.74 -207.98,-29.38 -371.16,-197.69 -392.06,-407.78z\"></path></svg></span></span><span class=\"ofb-card-rating ofb-rating--excellent\">\u2605 8.3</span><span class=\"ofb-card-hover\"><span class=\"ofb-card-hover__title\">Project Hail Mary</span><span class=\"ofb-card-hover__meta\"><span class=\"ofb-card-hover__rating ofb-rating--excellent\">\u2605 8.3</span><span>2h 36m</span><span>2026</span></span><span class=\"ofb-card-hover__genres\">Adventure, Mystery, Science Fiction</span></span></div></article></div>"""


def test_best_of_year_rail_correctly_parsed():
    e = _ex()
    items = e._extract_cards_best_rail(BEST_RAIL_FIXTURE)
    assert len(items) == 2, items
    titles = [it["title"] for it in items]
    assert "I Was a Stranger" in titles, titles
    assert "Project Hail Mary" in titles, titles
    stranger = [it for it in items if it["title"] == "I Was a Stranger"][0]
    assert stranger["rating"] == "9.3", stranger
    assert stranger["runtime"] == "1h 43m", stranger
    assert stranger["year"] == "2026", stranger
    assert stranger["genres"] == "Drama, Thriller", stranger
    assert stranger["type"] == "movie", stranger


def test_extract_cards_falls_through_to_best_rail_when_others_empty():
    e = _ex()
    result = e._extract_cards(BEST_RAIL_FIXTURE)
    assert len(result) == 2, result


def test_coming_soon_removed_from_categories():
    e = _ex()
    cats = e.get_categories()
    titles = [c.get("title", "") for c in cats]
    assert not any("\u0642\u0631\u064a\u0628\u0627\u064b" in t for t in titles), titles
    assert any("\u0627\u0644\u062a\u0637\u0628\u064a\u0642\u0627\u062a" in t for t in titles), titles
