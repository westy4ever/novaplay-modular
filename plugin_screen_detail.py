# -*- coding: utf-8 -*-
"""NovaPlay — Detail screen (servers / episodes / qualities / chain).
MODULAR EXTRACTION of NovaPlayDetail with:
  * episode chain + auto-server (next-episode support, _playNextEpisode)
  * health hooks on load/extract
  * quality-choices kept populated (variants → Back → servers → grid)
  * watchdog race fix (_done re-check in the timeout callback)
  * OSD poster seeding (P1/P2) for the player
  * magnet prompt + TorrServer + download engine flows
  * [PATCH 46-R2] poster badges: red year box (top-left) + gold-star
    rating box (top-right) — universal TMDB rating source
  * [PATCH 65-B3] per-title quality preference: when a stored quality
    matches an available variant, auto-play it instead of showing the
    quality menu. Saved by plugin_screen_player when the user picks a
    quality from the in-player menu.
  * [detail-reveal] staggered slide-in on screen open: poster from the
    left, info panel from the right, plot + menu from the bottom, each
    eased. Skipped for chained-episode opens (episode_index >= 0).
  * [detail-stagger] menu rows appear one (or a few) at a time on load
    and on quality-choices swap — makes even a slow response feel live.
  * [detail-accent] gold accent bar parked beside the selected server.
    [PATCH 112] For long lists the bar tracks the real visible-window
    row index (native eListbox behaviour) instead of sliding
    proportionally — and never overshoots the menu box on the last item.
  * [detail-progress] indeterminate cyan bar sweeping under the menu
    box while a stream is being extracted, hidden when it resolves.

[split 2026-10-23] the class is assembled from parts (no behaviour change):
  detail_header.py     DetailHeader
  detail_band.py       DetailBand
  detail_list.py       DetailList
  detail_torrent.py    DetailTorrent
  detail_play.py       DetailPlay
  detail_trailer.py    DetailTrailer
  detail_downloads.py  DetailDownloads
  detail_base.py       imports, skins and module-level helpers
"""
from detail_base import *  # noqa: F401,F403
from detail_header import DetailHeader
from detail_band import DetailBand
from detail_list import DetailList
from detail_torrent import DetailTorrent
from detail_play import DetailPlay
from detail_trailer import DetailTrailer
from detail_downloads import DetailDownloads


class NovaPlayDetail(DetailHeader, DetailBand, DetailList, DetailTorrent, DetailPlay, DetailTrailer, DetailDownloads, Screen):
    # [panel 2026-10-21] three columns like the other panel screens:
    # poster + facts | servers / episodes | backdrop + plot, header with the
    # site logo, title, details and clock, colour-key bar at the bottom.
    skin = """
    <screen name="NovaPlayDetail" position="center,center" size="1920,1080" flags="wfNoBorder" backgroundColor="#0C1017">
        <widget name="bg" position="0,0" size="1920,1080" backgroundColor="#0C1017" zPosition="1" />
        <widget name="siteLogo" position="40,40" size="180,64" zPosition="3" alphatest="blend" scale="1" />
        <widget name="title" position="240,28" size="1120,56" font="Bold;40" valign="center" foregroundColor="#F2C230" transparent="1" zPosition="4" />
        <widget name="metaCert" position="240,86" size="80,30" font="Bold;19" halign="center" valign="center" foregroundColor="#FFFFFF" backgroundColor="#1A2130" borderWidth="2" borderColor="#A3ADBB" cornerRadius="6" transparent="0" zPosition="4" />
        <widget name="meta" position="240,86" size="1120,32" font="Regular;22" valign="center" foregroundColor="#A3ADBB" transparent="1" zPosition="4" />
        <widget name="proxy_warning" position="240,118" size="1120,28" font="Regular;22" foregroundColor="#FF6B6B" transparent="1" zPosition="4" halign="left" />
        <widget name="clock_time" position="1380,22" size="500,62" font="Bold;54" halign="right" foregroundColor="#FFFFFF" transparent="1" zPosition="4" />
        <widget name="clock_date" position="1280,90" size="600,30" font="Regular;22" halign="right" foregroundColor="#A3ADBB" transparent="1" zPosition="4" />

        <eLabel position="40,150" size="360,810" backgroundColor="#121822" cornerRadius="16" zPosition="2" />
        <eLabel position="62,172" size="316,470" backgroundColor="#161C26" cornerRadius="10" zPosition="3" />
        <widget name="poster" position="62,172" size="316,470" zPosition="4" alphatest="blend" />
        <widget name="posterYear" position="70,180" size="86,34" font="Bold;22" foregroundColor="#0C1017" backgroundColor="#F2C230" transparent="0" zPosition="5" halign="center" valign="center" cornerRadius="8" />
        <widget name="posterRating" position="282,180" size="88,34" font="Bold;22" foregroundColor="#F2C230" backgroundColor="#0C1017" transparent="0" zPosition="5" halign="center" valign="center" cornerRadius="8" />
        <widget name="posStrip" position="62,598" size="316,44" backgroundColor="#0C1017" zPosition="6" />
        <widget name="posStatus" position="74,598" size="250,40" font="Bold;19" valign="center" foregroundColor="#FFFFFF" backgroundColor="#0C1017" transparent="1" zPosition="7" />
        <widget name="posFav" position="328,598" size="44,40" font="Regular;26" halign="center" valign="center" foregroundColor="#F2C230" backgroundColor="#0C1017" transparent="1" zPosition="7" />
        <widget name="posBarBg" position="62,638" size="316,4" backgroundColor="#3A4352" zPosition="7" />
        <widget name="posBar" position="62,638" size="1,4" backgroundColor="#30A46C" zPosition="8" />
        <widget name="source" position="62,662" size="316,280" font="Regular;22" foregroundColor="#C9D0DA" transparent="1" halign="left" zPosition="4" />

        <widget name="section" position="430,152" size="950,48" font="Bold;30" foregroundColor="#FFFFFF" transparent="1" zPosition="4" />
        <widget name="torLead" position="-400,-100" size="200,40" font="Bold;26" valign="center" foregroundColor="#FFFFFF" transparent="1" zPosition="6" />
        <widget name="tq0" position="-400,-100" size="120,40" font="Bold;22" halign="center" valign="center" foregroundColor="#D6DCE5" backgroundColor="#2A3342" transparent="0" cornerRadius="20" zPosition="6" />
        <widget name="tq1" position="-400,-100" size="120,40" font="Bold;22" halign="center" valign="center" foregroundColor="#D6DCE5" backgroundColor="#2A3342" transparent="0" cornerRadius="20" zPosition="6" />
        <widget name="tq2" position="-400,-100" size="120,40" font="Bold;22" halign="center" valign="center" foregroundColor="#D6DCE5" backgroundColor="#2A3342" transparent="0" cornerRadius="20" zPosition="6" />
        <widget name="tq3" position="-400,-100" size="120,40" font="Bold;22" halign="center" valign="center" foregroundColor="#D6DCE5" backgroundColor="#2A3342" transparent="0" cornerRadius="20" zPosition="6" />
        <widget name="tq4" position="-400,-100" size="120,40" font="Bold;22" halign="center" valign="center" foregroundColor="#D6DCE5" backgroundColor="#2A3342" transparent="0" cornerRadius="20" zPosition="6" />
        <widget name="torHint" position="-400,-100" size="200,40" font="Regular;20" valign="center" foregroundColor="#8C96A5" transparent="1" zPosition="6" />
        <widget name="menu_box" position="430,210" size="950,750" backgroundColor="#161C26" cornerRadius="14" zPosition="2" />
        <widget name="menu" position="440,222" size="930,720" zPosition="4"
                scrollbarMode="showNever"
                enableWrapAround="1"
                transparent="1"
                backgroundColor="#161C26"
                backgroundColorSelected="#2B3648" />
        <widget name="menuAccent"   position="434,222"  size="5,60"  backgroundColor="#F2C230" transparent="0" cornerRadius="2" zPosition="5" />
        <widget name="menuProgress" position="440,946" size="180,5" backgroundColor="#F2C230" transparent="0" cornerRadius="2" zPosition="5" />

        <eLabel position="1410,150" size="470,810" backgroundColor="#121822" cornerRadius="16" zPosition="1" />
        <widget name="backdrop" position="1430,170" size="430,242" backgroundColor="#161C26" cornerRadius="12" zPosition="2" />
        <widget name="backdropImage" position="1430,170" size="430,242" zPosition="3" alphatest="blend" />
        <widget name="plot_title" position="1430,428" size="430,32" font="Bold;22" foregroundColor="#F2C230" transparent="1" halign="left" zPosition="4" />
        <widget name="tagline" position="1430,462" size="430,56" font="Regular;20" foregroundColor="#A3ADBB" transparent="1" halign="left" valign="top" zPosition="4" />
        <widget name="plot" position="1430,466" size="430,476" font="Regular;23" foregroundColor="#D6DCE5" transparent="1" halign="left" valign="top" zPosition="4" />

        <eLabel position="40,976" size="1840,76" backgroundColor="#121822" cornerRadius="16" zPosition="2" />
        <eLabel position="72,1003" size="22,22" backgroundColor="#E5484D" cornerRadius="11" zPosition="3" />
        <widget name="key_red"    position="106,994" size="220,40" font="Regular;26" valign="center" foregroundColor="#FFFFFF" transparent="1" zPosition="4" />
        <widget name="dotGreen" position="332,1003" size="22,22" backgroundColor="#30A46C" cornerRadius="11" zPosition="3" />
        <widget name="key_green"  position="366,994" size="180,40" font="Regular;26" valign="center" foregroundColor="#FFFFFF" transparent="1" zPosition="4" />
        <eLabel position="552,1003" size="22,22" backgroundColor="#F2C230" cornerRadius="11" zPosition="3" />
        <widget name="key_yellow" position="586,994" size="230,40" font="Regular;26" valign="center" foregroundColor="#FFFFFF" transparent="1" zPosition="4" />
        <widget name="dotBlue" position="822,1003" size="22,22" backgroundColor="#3E7BFA" cornerRadius="11" zPosition="3" />
        <widget name="key_blue"   position="856,994" size="240,40" font="Regular;26" valign="center" foregroundColor="#FFFFFF" transparent="1" zPosition="4" />
        <widget name="status"     position="1110,994" size="740,40" font="Regular;22" valign="center" foregroundColor="#C9D0DA" transparent="1" halign="right" zPosition="4" />
    </screen>
    """

    # [detail-reveal] design positions for every widget that participates
    # in the entry animation. Each group slides from an offset toward its
    # design position with an ease-out cubic curve. Groups have staggered
    # start frames so the reveal cascades.
    # (name, ((widget_key, design_x, design_y), ...), off_x, off_y, delay, duration)
    _REVEAL_GROUPS = (
        ("poster", (
            ("poster", 62, 172),
            ("posterYear", 70, 180),
            ("posterRating", 282, 180),
            ("source", 62, 662),
            ("posStrip", 62, 598),
            ("posStatus", 74, 598),
            ("posFav", 328, 598),
            ("posBarBg", 62, 638),
            ("posBar", 62, 638),
        ), -360, 0, 0, 8),
        ("info", (
            ("title", 240, 28),
            ("metaCert", 240, 86),
            ("meta", 240, 86),
            ("proxy_warning", 240, 118),
        ), 220, 0, 2, 8),
        ("menu", (
            ("menu_box", 430, 210),
            ("section", 430, 152),
            ("menu", 440, 222),
        ), 0, 320, 3, 8),
        ("plot", (
            ("backdrop", 1430, 170),
            ("backdropImage", 1430, 170),
            ("plot_title", 1430, 428),
            ("tagline", 1430, 462),
            ("plot", 1430, 466),
        ), 360, 0, 4, 8),
    )

    _REVEAL_GROUPS_MODERN = (
        ("poster", (
            ("poster", 52, 152),
            ("posterYear", 62, 162),
            ("posterRating", 272, 162),
            ("posStrip", 52, 578),
            ("posStatus", 64, 578),
            ("posFav", 318, 578),
            ("posBarBg", 52, 618),
            ("posBar", 52, 618),
        ), -400, 0, 0, 8),
        ("info", (
            ("title", 420, 138),
            ("tagline", 420, 208),
            ("metaCert", 420, 242),
            ("meta", 420, 242),
            ("source", 420, 280),
            ("proxy_warning", 1320, 140),
            ("plot_title", 420, 548),
            ("plot", 420, 576),
        ), 260, 0, 2, 8),
        ("band", (
            ("menu_box", 40, 692),
            ("section", 70, 702),
            ("svPos", 1230, 706),
        ), 0, 300, 3, 8),
    )

    # [detail-accent] geometry of the menu widget, used to compute where
    # the accent bar should sit relative to the selected row.
    _MENU_X = 440
    _MENU_Y = 222
    _MENU_W = 930
    # [PATCH 115] confirmed live from screen recording: 4 server rows are
    # actually visible on screen at once (e.g. "fastved / firestream /
    # luluvdoo / rpmvip" all shown together), not 3 -- 320 // 95 was
    # computing visible_rows=3, so the accent bar pinned itself one row
    # too early and could never reach the real 4th row. 380 // 95 = 4,
    # matching the real screen.
    _MENU_H = 720
    _MENU_ROW_H = 95

    # [detail-progress] geometry of the indeterminate bar
    _PROG_X0 = 440
    _PROG_Y  = 946
    _PROG_W  = 180
    _PROG_H  = 5
    _PROG_SPAN = 930 - 180   # bar travels this far before reversing
    _PROG_STEP = 40

    def __init__(self, session, item, site="egydead", m_type="movie",
                 episode_chain=None, episode_index=-1, auto_server_idx=None, resume=False):
        # [theme] Modern: backdrop header + server cards in a bottom band
        self._modern = _get_config("screen_style", "classic") == "modern"
        if self._modern:
            self.skin = _DETAIL_SKIN_MODERN
            self.skinName = "NovaPlayDetailModern"
            self._REVEAL_GROUPS = self._REVEAL_GROUPS_MODERN
            self._PROG_X0 = _SV_X0
            self._PROG_Y = 936
            self._PROG_H = 6
            self._PROG_SPAN = SV_CARDS * _SV_W + (SV_CARDS - 1) * _SV_GAP - self._PROG_W
        else:
            self.skin = NovaPlayDetail.skin.format(plugin_path=PLUGIN_PATH).replace("</screen>", TOAST_XML + "</screen>")
        self._sv_items = []
        self._sv_pop_key = None
        self._sv_pop_slot = -1
        self._sv_pop_f = 0
        self._sv_slide = 0
        self._svPopT = eTimer()
        self._svPopT.callback.append(self._svPopTick)
        Screen.__init__(self, session)
        self.session = session
        self._item   = item
        self._site   = site
        self._m_type = m_type
        self._data   = None
        self._servers = []
        self._episodes = []
        self._tmp_posters = []
        self._poster_loaded = False
        self._raw_title = ""
        self._closed = False
        self._proxy_warning_shown = False

        self._extract_lock = threading.Lock()
        self._extract_token = 0
        self._extracting = False
        self._quality_choices = []

        self._episode_chain = list(episode_chain) if episode_chain else []
        self._episode_index = int(episode_index if episode_index is not None else -1)
        self._auto_server_idx = auto_server_idx
        self._last_server_idx = None
        # [continue] opened from Continue Watching: start the last-used
        # server and resume without the yes/no question
        self._resume_direct = bool(resume)
        self._close_on_return = False
        if self._episode_chain and 0 <= self._episode_index < len(self._episode_chain) - 1:
            self._next_episode = self._episode_chain[self._episode_index + 1]
        else:
            self._next_episode = None
        self._auto_menu_timer = None
        self._osd_poster = (item.get("poster") or item.get("image") or "")

        # [detail-reveal] animation state
        self._reveal_timer = eTimer()
        self._reveal_timer.callback.append(self._revealTick)
        self._reveal_frame = 0
        self._reveal_running = False

        # [detail-stagger] menu rows appearing one step at a time
        self._stagger_timer = eTimer()
        self._stagger_timer.callback.append(self._staggerTick)
        self._stagger_items = []
        self._stagger_idx = 0
        self._stagger_step = 1

        # [detail-accent] tracks the last item count so the accent bar can
        # hide itself when the menu is empty
        self._menu_item_count = 0

        # [detail-progress] indeterminate bar state
        self._progress_timer = eTimer()
        self._progress_timer.callback.append(self._progressTick)
        self._progress_x = self._PROG_X0
        self._progress_dir = 1
        self._progress_active = False

        self["bg"]     = Label("")
        self["backdrop"] = Label("")
        self["backdropImage"] = Pixmap()
        self["menu_box"] = Label("")
        self["poster"] = Pixmap()
        self["posterYear"] = Label("")
        self["posterRating"] = Label("")
        self["title"]  = Label(item.get("title", ""))
        self["meta"]   = Label("")
        self["source"] = Label("")
        self["proxy_warning"] = Label("")
        # [hdr-ui] tagline, age-rating chip, poster resume/favourite strip
        self["tagline"] = Label("")
        self["metaCert"] = Label("")
        for _k in ("posStrip", "posStatus", "posFav", "posBarBg", "posBar"):
            self[_k] = Label("")
        self._tagline = ""
        self._rt_min = 0
        self._hdr_pos = {}
        self.onLayoutFinish.append(self._hdrInit)
        self.onShown.append(self._paintPosterStatus)
        self["plot_title"] = Label("Plot")
        self["plot"]   = Label("")
        self["section"] = Label("Preparing...")
        self["torLead"] = Label("")
        self["torHint"] = Label("")
        for _i in range(5):
            self["tq%d" % _i] = Label("")
        self._tq_geo = {}
        self._tq_pop = -1
        self._tq_f = 0
        self._tqT = eTimer()
        self._tqT.callback.append(self._tqTick)
        self["menu"]   = StreamList(width=930)
        self["siteLogo"] = Pixmap()
        self["clock_time"] = Label("")
        self["clock_date"] = Label("")
        self["dotGreen"] = Label("")
        self["dotBlue"] = Label("")
        self._clockTimer = eTimer()
        self._clockTimer.callback.append(self._tickClock)
        self["menuAccent"]   = Label("")
        self["menuProgress"] = Label("")
        self["menuAccent"].hide()
        self["menuProgress"].hide()
        if self._modern:
            for _i in range(CAST_N):
                self["castImg%d" % _i] = Pixmap()
                self["castInit%d" % _i] = Label("")
                self["castName%d" % _i] = Label("")
            self._cast_token = None
            self["svPos"] = Label("")
            self["svLeft"] = Label(u"\u2039")
            self["svRight"] = Label(u"\u203a")
            for _i in range(SV_CARDS):
                for _n in ("svEdge", "svBg", "svNum", "svQ", "svName", "svInfo"):
                    self["%s%d" % (_n, _i)] = Label("")
        self["key_red"] = Label("Favorites")
        self["key_yellow"] = Label("Update TMDb")
        self["key_blue"] = Label("Download")
        self["key_green"] = Label("")
        self["status"] = Label("Loading details...")
        toast_init(self)

        self._downloads = []
        self._active_download_task = None

        # [detail-accent] hook the selection-changed pipeline. If Enigma2
        # wires StreamList.selectionChanged (it does for eListbox parents),
        # this fires on every arrow-key move; otherwise the arrow-key
        # handlers below also call _updateMenuAccent explicitly, so it's
        # covered either way.
        try:
            self["menu"].onSelectionChanged.append(self._updateMenuAccent)
        except Exception:
            pass

        self.picLoad = ePicLoad()
        self.picLoad.PictureData.get().append(self._paintPoster)

        # [detail-tmdb] second, independent native-decode pipeline for the
        # blurred backdrop image -- kept fully separate from self.picLoad
        # (the sharp poster) so the two decodes never race or overwrite
        # each other's PictureData callback.
        self.backdropPicLoad = ePicLoad()
        self.backdropPicLoad.PictureData.get().append(self._paintBackdrop)

        self["actions"] = ActionMap(["OkCancelActions", "ColorActions", "DirectionActions"], {
            "ok":     self._onOk,
            "cancel": self._onCancel,
            "red":    self._toggleFavorite,
            "yellow": self._onYellow,
            "blue":   self._openDownloads,
            "green":  self._playTrailer,
            "up":     self._navMenuUp,
            "down":   self._navMenuDown,
            "left":   self._navLeft,
            "right":  self._navRight,
        }, -1)
        if self._modern:
            self.onLayoutFinish.append(self._svInit)
        # [2026-10-21] CH+ / CH- step the torrent quality filter (like YTS's tabs)
        self["torFilterActions"] = ActionMap(["ChannelSelectBaseActions"], {
            "nextBouquet": lambda: self._stepTorrentFilter(1),
            "prevBouquet": lambda: self._stepTorrentFilter(-1),
        }, -1)

        self.onLayoutFinish.append(self._load)
        self.onExecBegin.append(self._refreshPoster)
        self.onExecBegin.append(self._maybeCloseAfterResume)
        self.onClose.append(self._onCloseCleanup)

    # ── [detail-accent] navigation helpers ─────────────────────────────
    # [theme] Modern: the cards run left/right, so ◀ ▶ step one card and
    # ▲ ▼ jump a whole row of cards; Classic keeps ▲ ▼ rows, ◀ ▶ pages.
    def _navMenuUp(self):
        if self._modern:
            return self._svStep(-SV_CARDS)
        try:
            self["menu"].up()
        except Exception:
            pass
        self._updateMenuAccent()

    def _navMenuDown(self):
        if self._modern:
            return self._svStep(SV_CARDS)
        try:
            self["menu"].down()
        except Exception:
            pass
        self._updateMenuAccent()

    def _navLeft(self):
        if self._modern:
            return self._svStep(-1)
        self["menu"].pageUp()

    def _navRight(self):
        if self._modern:
            return self._svStep(1)
        self["menu"].pageDown()
