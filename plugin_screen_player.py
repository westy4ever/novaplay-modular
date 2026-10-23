# -*- coding: utf-8 -*-
"""NovaPlay — playback screen + service orchestration.

MODULAR EXTRACTION of NovaPlaySimplePlayer plus _play /
_build_remote_play_candidates / service capture-restore. Full feature
set with ALL session patches baked in:

  * tracker via novaplay_tracker module attributes
  * Fix Y: _next_open_timer NOT stopped in __stop
  * Fix Z: double evEOF does not exit from under a visible card
  * OSD poster: 132x198 floating ABOVE the panel
  * auto-next card: portrait 100x150 with fallback chain
  * number keys 0-9 = proportional seeks
  * record: decodes the proxy URL back to the upstream stream
  * [PATCH 29] eAVSwitch aspect probes with restore-on-exit
  * [PATCH 32] per-site "candidate that worked" memory
  * [PATCH 33] verifySeek trusts the demuxer's PTS
  * [PATCH 65-B3] per-title quality preference
  * [PATCH 65-A3] next-episode countdown from config

[player-anim]
  * OSD slide-in/out (eased, exponential smoothing)
  * Seek-arrow flash overlay
  * Auto-next card slide-in/out from the right
  * Quality-switch wipe (mask the service restart)
  * Aspect-bar slide-in on 4:3 / 14:9 cycle
  * Studio-panel drop-down
  * Sync-offset toast on the right edge

[player-ux]
  * state glyph ▶/⏸/⏳/⚠ beside elapsed
  * watched at 90%
  * "✓ Saved at hh:mm" toast on exit
  * hold-to-accelerate seek
  * MENU = skip hint 3s into an extraction
  * retry dialog when all candidates fail
  * near-end exit prompt (next / mark / save / cancel)
  * disk-space guard before recording

[split 2026-10-23] the class is assembled from parts (no behaviour change):
  player_osd.py        PlayerOSD
  player_switch.py     PlayerSwitch
  player_fallback.py   PlayerFallback
  player_autonext.py   PlayerAutoNext
  player_controls.py   PlayerControls
  player_menus.py      PlayerMenus
  player_subs.py       PlayerSubs
  player_base.py       imports and module-level helpers
"""
from player_base import *  # noqa: F401,F403
from player_osd import PlayerOSD
from player_switch import PlayerSwitch
from player_fallback import PlayerFallback
from player_autonext import PlayerAutoNext
from player_controls import PlayerControls
from player_menus import PlayerMenus
from player_subs import PlayerSubs


class NovaPlaySimplePlayer(PlayerOSD, PlayerSwitch, PlayerFallback, PlayerAutoNext, PlayerControls, PlayerMenus, PlayerSubs, Screen):
    skin = scale_skin_xml("""
    <screen name="NovaPlaySimplePlayer" position="0,0" size="1920,1080" flags="wfNoBorder" backgroundColor="transparent">
        <widget name="aspect_bar_l" position="0,0" size="1,1080" backgroundColor="#000000" zPosition="5" />
        <widget name="aspect_bar_r" position="1919,0" size="1,1080" backgroundColor="#000000" zPosition="5" />
        <widget name="recBlink" position="1660,42" size="190,54" font="Regular;36" halign="center" valign="center" foregroundColor="#00FF0000" backgroundColor="#AA000000" transparent="0" zPosition="95" cornerRadius="16" />
        <!-- [osd-ui 2026-10-23] three rows inside the TV safe area (ends at y 1000):
             title / episode / video / subtitles / clock -- seek bar with elapsed and
             remaining + "Ends 23:14" -- colour keys + one help line -->
        <widget name="overlayEdge"  position="158,798" size="1604,204" backgroundColor="#262E3B" cornerRadius="22" zPosition="9" />
        <widget name="posterEdge"   position="182,504" size="192,282" backgroundColor="#262E3B" cornerRadius="14" zPosition="11" />
        <widget name="overlay_bg"   position="160,800" size="1600,200" backgroundColor="#121822" cornerRadius="20" zPosition="10" />
        <widget name="osdPosterBox" position="184,506" size="188,278" backgroundColor="#121822" cornerRadius="12" zPosition="12" transparent="0" />
        <widget name="osdPoster" position="188,510" size="180,270" zPosition="12" alphatest="blend" scale="1" />
        <widget name="osd_title"    position="184,812" size="576,40" font="Regular;30" foregroundColor="#F2C230" transparent="1" zPosition="12" halign="left" valign="center" />
        <widget name="osd_episode"  position="770,812" size="180,40" font="Regular;24" foregroundColor="#F2C230" transparent="1" zPosition="12" halign="left" valign="center" />
        <widget name="osd_videinfo" position="960,812" size="260,40" font="Regular;22" foregroundColor="#A3ADBB" transparent="1" zPosition="12" halign="left" valign="center" />
        <widget name="osd_subdetail" position="1230,812" size="370,40" font="Regular;20" foregroundColor="#A3ADBB" transparent="1" zPosition="12" halign="left" valign="center" />
        <widget name="osd_clock"    position="1610,812" size="130,40" font="Regular;28" foregroundColor="#FFFFFF" transparent="1" zPosition="12" halign="right" valign="center" />
        <widget name="seekFrame" position="182,870" size="1556,10" zPosition="11" backgroundColor="#2A3240" cornerRadius="5" />
        <widget name="seekbar" position="184,872" size="1552,6" zPosition="12" backgroundColor="#1C2333" foregroundColor="#F2C230" cornerRadius="3" />
        <widget name="seekMarker" position="184,866" size="18,18" zPosition="13" backgroundColor="#00FFFFFF" cornerRadius="9" />
        <widget name="osd_elapsed"  position="184,890" size="330,40" font="Regular;28" foregroundColor="#E5D1A6" transparent="1" zPosition="12" valign="center" />
        <widget name="osd_state"    position="520,890" size="60,40" font="Regular;30" foregroundColor="#39D98A" transparent="1" zPosition="12" halign="center" valign="center" />
        <widget name="status"       position="590,890" size="640,40" font="Regular;24" foregroundColor="#FFFFFF" transparent="1" zPosition="12" halign="center" valign="center" />
        <widget name="osd_durtext"  position="1240,890" size="500,40" font="Regular;26" foregroundColor="#FFFFFF" transparent="1" zPosition="12" halign="right" valign="center" />
        <widget name="osd_divider"  position="184,936" size="1552,1" backgroundColor="#2A3240" zPosition="11" />
        <widget name="keyRed"    position="184,948" size="205,38" font="Regular;23" halign="center" valign="center" foregroundColor="#F0F6FC" backgroundColor="#681418" transparent="0" cornerRadius="10" zPosition="12" />
        <widget name="keyGreen"  position="399,948" size="205,38" font="Regular;23" halign="center" valign="center" foregroundColor="#F0F6FC" backgroundColor="#18521F" transparent="0" cornerRadius="10" zPosition="12" />
        <widget name="keyYellow" position="614,948" size="205,38" font="Regular;23" halign="center" valign="center" foregroundColor="#F0F6FC" backgroundColor="#695B0C" transparent="0" cornerRadius="10" zPosition="12" />
        <widget name="keyBlue"   position="829,948" size="205,38" font="Regular;23" halign="center" valign="center" foregroundColor="#F0F6FC" backgroundColor="#141763" transparent="0" cornerRadius="10" zPosition="12" />
        <widget name="osd_keys"     position="1050,948" size="690,38" font="Regular;22" foregroundColor="#A3ADBB" transparent="1" zPosition="12" halign="right" valign="center" />
        <widget name="seekBubble"   position="184,818" size="150,40" font="Bold;24" foregroundColor="#0C1017" backgroundColor="#F2C230" cornerRadius="10" halign="center" valign="center" transparent="0" zPosition="14" />
        <widget name="pauseBadge"   position="900,480" size="120,120" font="Regular;52" foregroundColor="#FFFFFF" backgroundColor="#400C1017" cornerRadius="60" halign="center" valign="center" transparent="0" zPosition="91" />
        <!-- old OSD parts, no longer shown (kept so nothing that still sets them breaks) -->
        <widget name="osd_shadow" position="-300,-300" size="1,1" font="Regular;10" transparent="1" zPosition="0" />
        <widget name="osd_topline" position="-300,-300" size="1,1" font="Regular;10" transparent="1" zPosition="0" />
        <widget name="osd_titlebar" position="-300,-300" size="1,1" font="Regular;10" transparent="1" zPosition="0" />
        <widget name="osd_subinfo" position="-300,-300" size="1,1" font="Regular;10" transparent="1" zPosition="0" />
        <widget name="progBack" position="-300,-300" size="1,1" font="Regular;10" transparent="1" zPosition="0" />
        <widget name="prog_bar" position="-300,-300" size="1,1" font="Regular;10" transparent="1" zPosition="0" />
        <widget name="osd_hints" position="-300,-300" size="1,1" font="Regular;10" transparent="1" zPosition="0" />
        <widget name="osd_keybar" position="-300,-300" size="1,1" font="Regular;10" transparent="1" zPosition="0" />
        <widget name="osd_botline" position="-300,-300" size="1,1" font="Regular;10" transparent="1" zPosition="0" />
        <widget name="osd_jump" position="-300,-300" size="1,1" font="Regular;10" transparent="1" zPosition="0" />
        <widget name="autoNextPanel" position="1110,92" size="750,255" zPosition="20" backgroundColor="#20000000" transparent="0" cornerRadius="24" />
        <widget name="autoNextAccent" position="1110,92" size="750,7" zPosition="21" backgroundColor="#F2C230" transparent="0" />
        <widget name="autoNextPoster" position="1140,105" size="100,150" zPosition="22" alphatest="blend" scale="1" />
        <widget name="autoNextTitle" position="1370,122" size="455,40" font="Regular;30" halign="left" foregroundColor="#F0F6FC" backgroundColor="transparent" zPosition="22" transparent="1" />
        <widget name="autoNextEpisode" position="1370,170" size="455,54" font="Regular;27" halign="left" foregroundColor="#F2C230" backgroundColor="transparent" zPosition="22" transparent="1" />
        <widget name="autoNextText" position="1370,235" size="455,32" font="Regular;24" halign="left" foregroundColor="#E8E8E8" backgroundColor="transparent" zPosition="22" transparent="1" />
        <widget name="autoNextProgress" position="1370,282" size="455,16" zPosition="22" cornerRadius="8" backgroundColor="#1C2333" foregroundColor="#F2C230" />
        <widget name="autoNextHelp" position="1370,310" size="455,24" font="Regular;20" halign="left" foregroundColor="#CFCFCF" backgroundColor="transparent" zPosition="22" transparent="1" />
        <widget name="seekFlash"    position="660,430" size="600,140" font="Regular;64" foregroundColor="#F0F6FC" backgroundColor="#E60D1117" transparent="0" cornerRadius="20" halign="center" valign="center" zPosition="90" />
        <widget name="qualityWipe"  position="0,0"   size="1920,0"  backgroundColor="#F2C230" transparent="0" zPosition="89" />
        <widget name="syncToast"    position="1550,760" size="280,64" font="Regular;34" foregroundColor="#0C1017" backgroundColor="#F2C230" transparent="0" cornerRadius="14" halign="center" valign="center" zPosition="88" />
        <widget name="subBg1"   position="660,812" size="600,100" zPosition="7" backgroundColor="#80000000" transparent="0" cornerRadius="14" />
        <widget name="subBg2"   position="660,916" size="600,100" zPosition="7" backgroundColor="#80000000" transparent="0" cornerRadius="14" />
        <widget name="subLine1" position="210,812" size="1500,100" font="Regular;38" halign="center" valign="center" foregroundColor="#00FFFFFF" backgroundColor="transparent" zPosition="8" transparent="1" />
        <widget name="subLine2" position="210,916" size="1500,100" font="Regular;38" halign="center" valign="center" foregroundColor="#00FFFFFF" backgroundColor="transparent" zPosition="8" transparent="1" />
        <widget name="subShadow0_0" position="210,812" size="1500,100" font="Regular;38" halign="center" valign="center" foregroundColor="#00000000" backgroundColor="transparent" zPosition="7" transparent="1" />
        <widget name="subShadow0_1" position="210,812" size="1500,100" font="Regular;38" halign="center" valign="center" foregroundColor="#00000000" backgroundColor="transparent" zPosition="7" transparent="1" />
        <widget name="subShadow0_2" position="210,812" size="1500,100" font="Regular;38" halign="center" valign="center" foregroundColor="#00000000" backgroundColor="transparent" zPosition="7" transparent="1" />
        <widget name="subShadow0_3" position="210,812" size="1500,100" font="Regular;38" halign="center" valign="center" foregroundColor="#00000000" backgroundColor="transparent" zPosition="7" transparent="1" />
        <widget name="subShadow0_4" position="210,812" size="1500,100" font="Regular;38" halign="center" valign="center" foregroundColor="#00000000" backgroundColor="transparent" zPosition="7" transparent="1" />
        <widget name="subShadow0_5" position="210,812" size="1500,100" font="Regular;38" halign="center" valign="center" foregroundColor="#00000000" backgroundColor="transparent" zPosition="7" transparent="1" />
        <widget name="subShadow0_6" position="210,812" size="1500,100" font="Regular;38" halign="center" valign="center" foregroundColor="#00000000" backgroundColor="transparent" zPosition="7" transparent="1" />
        <widget name="subShadow0_7" position="210,812" size="1500,100" font="Regular;38" halign="center" valign="center" foregroundColor="#00000000" backgroundColor="transparent" zPosition="7" transparent="1" />
        <widget name="subShadow1_0" position="210,916" size="1500,100" font="Regular;38" halign="center" valign="center" foregroundColor="#00000000" backgroundColor="transparent" zPosition="7" transparent="1" />
        <widget name="subShadow1_1" position="210,916" size="1500,100" font="Regular;38" halign="center" valign="center" foregroundColor="#00000000" backgroundColor="transparent" zPosition="7" transparent="1" />
        <widget name="subShadow1_2" position="210,916" size="1500,100" font="Regular;38" halign="center" valign="center" foregroundColor="#00000000" backgroundColor="transparent" zPosition="7" transparent="1" />
        <widget name="subShadow1_3" position="210,916" size="1500,100" font="Regular;38" halign="center" valign="center" foregroundColor="#00000000" backgroundColor="transparent" zPosition="7" transparent="1" />
        <widget name="subShadow1_4" position="210,916" size="1500,100" font="Regular;38" halign="center" valign="center" foregroundColor="#00000000" backgroundColor="transparent" zPosition="7" transparent="1" />
        <widget name="subShadow1_5" position="210,916" size="1500,100" font="Regular;38" halign="center" valign="center" foregroundColor="#00000000" backgroundColor="transparent" zPosition="7" transparent="1" />
        <widget name="subShadow1_6" position="210,916" size="1500,100" font="Regular;38" halign="center" valign="center" foregroundColor="#00000000" backgroundColor="transparent" zPosition="7" transparent="1" />
        <widget name="subShadow1_7" position="210,916" size="1500,100" font="Regular;38" halign="center" valign="center" foregroundColor="#00000000" backgroundColor="transparent" zPosition="7" transparent="1" />
        <widget name="studioPanel" position="0,36" size="1920,262" zPosition="85" backgroundColor="#0A0E14" transparent="0" />
        <widget name="studioAccent" position="0,36" size="1920,8" zPosition="86" backgroundColor="#F2C230" transparent="0" />
        <widget name="studioTitle" position="45,52" size="480,34" font="Regular;30" foregroundColor="#F2C230" transparent="1" zPosition="87" />
        <widget name="studioTab0" position="560,48" size="210,40" font="Regular;24" halign="center" valign="center" foregroundColor="#8B949E" backgroundColor="#171718" transparent="0" cornerRadius="20" zPosition="87" />
        <widget name="studioTab1" position="790,48" size="210,40" font="Regular;24" halign="center" valign="center" foregroundColor="#8B949E" backgroundColor="#171718" transparent="0" cornerRadius="20" zPosition="87" />
        <widget name="studioTab2" position="1020,48" size="210,40" font="Regular;24" halign="center" valign="center" foregroundColor="#8B949E" backgroundColor="#171718" transparent="0" cornerRadius="20" zPosition="87" />
        <widget name="studioTab3" position="1250,48" size="210,40" font="Regular;24" halign="center" valign="center" foregroundColor="#8B949E" backgroundColor="#171718" transparent="0" cornerRadius="20" zPosition="87" />
        <widget name="studioStatus" position="1490,54" size="380,32" font="Regular;26" halign="right" foregroundColor="#8B949E" transparent="1" zPosition="87" />
        <widget name="studioHelp" position="45,252" size="1830,30" font="Regular;22" halign="center" foregroundColor="#8B949E" transparent="1" zPosition="87" />
        <widget name="studioCard0" position="60,100" size="340,82" font="Regular;26" halign="center" valign="center" foregroundColor="#D0D0D0" backgroundColor="#171718" transparent="0" cornerRadius="16" zPosition="87" />
        <widget name="studioCard1" position="425,100" size="340,82" font="Regular;26" halign="center" valign="center" foregroundColor="#D0D0D0" backgroundColor="#171718" transparent="0" cornerRadius="16" zPosition="87" />
        <widget name="studioCard2" position="790,100" size="340,82" font="Regular;26" halign="center" valign="center" foregroundColor="#D0D0D0" backgroundColor="#171718" transparent="0" cornerRadius="16" zPosition="87" />
        <widget name="studioCard3" position="1155,100" size="340,82" font="Regular;26" halign="center" valign="center" foregroundColor="#D0D0D0" backgroundColor="#171718" transparent="0" cornerRadius="16" zPosition="87" />
        <widget name="studioCard4" position="1520,100" size="340,82" font="Regular;26" halign="center" valign="center" foregroundColor="#D0D0D0" backgroundColor="#171718" transparent="0" cornerRadius="16" zPosition="87" />
        <widget name="studioPreviewBg" position="460,192" size="1000,1" zPosition="86" backgroundColor="#0A0E14" transparent="0" />
        <widget name="studioPreview" position="45,194" size="1830,50" font="Regular;28" halign="center" valign="center" foregroundColor="#F0F6FC" backgroundColor="transparent" zPosition="88" transparent="1" />
        <widget name="safeTop" position="0,90" size="1920,2" backgroundColor="#F2C230" zPosition="86" transparent="0" />
        <widget name="safeBottom" position="0,990" size="1920,2" backgroundColor="#F2C230" zPosition="86" transparent="0" />
        <widget name="safeLeft" position="96,90" size="2,900" backgroundColor="#F2C230" zPosition="86" transparent="0" />
        <widget name="safeRight" position="1822,90" size="2,900" backgroundColor="#F2C230" zPosition="86" transparent="0" />
    </screen>
    """)

    _OSD_WIDGETS = [
        "overlayEdge", "overlay_bg", "posterEdge", "osdPosterBox", "osdPoster",
        "osd_title", "osd_episode", "osd_videinfo", "osd_subdetail", "osd_clock",
        "seekFrame", "seekbar", "seekMarker",
        "osd_elapsed", "osd_state", "status", "osd_durtext",
        "osd_divider", "keyRed", "keyGreen", "keyYellow", "keyBlue", "osd_keys",
    ]
    # [osd-ui] parts of the old layout: always hidden
    _OSD_RETIRED = ("osd_shadow", "osd_topline", "osd_titlebar", "osd_subinfo", "progBack",
                    "prog_bar", "osd_hints", "osd_keybar", "osd_botline", "osd_jump")

    _OSD_DESIGN_Y = {
        "overlayEdge": 798, "overlay_bg": 800, "posterEdge": 504, "osdPosterBox": 506, "osdPoster": 510,
        "osd_title": 812, "osd_episode": 812, "osd_videinfo": 812, "osd_subdetail": 812, "osd_clock": 812,
        "seekFrame": 870, "seekbar": 872, "seekMarker": 866,
        "osd_elapsed": 890, "osd_state": 890, "status": 890, "osd_durtext": 890,
        "osd_divider": 936, "keyRed": 948, "keyGreen": 948, "keyYellow": 948, "keyBlue": 948,
        "osd_keys": 948,
    }
    _OSD_SLIDE_OFFSET = 500

    _AUTONEXT_WIDGETS = ("autoNextPanel","autoNextAccent","autoNextPoster","autoNextTitle",
                          "autoNextEpisode","autoNextText","autoNextProgress","autoNextHelp")
    _AUTONEXT_DESIGN_X = {
        "autoNextPanel": 1110, "autoNextAccent": 1110,
        "autoNextPoster": 1140, "autoNextTitle": 1370,
        "autoNextEpisode": 1370, "autoNextText": 1370,
        "autoNextProgress": 1370, "autoNextHelp": 1370,
    }
    _AUTONEXT_SLIDE_OFFSET = 850

    _STUDIO_KEYS = ("studioPanel","studioAccent","studioTitle","studioStatus",
                    "studioHelp","studioCard0","studioCard1","studioCard2",
                    "studioCard3","studioCard4",
                    "studioTab0","studioTab1","studioTab2","studioTab3",
                    "studioPreviewBg","studioPreview")
    _STUDIO_TABS = ("studioTab0","studioTab1","studioTab2","studioTab3")
    _STUDIO_SLOTS_X = (60, 425, 790, 1155, 1520)
    # [studio-ui] settings grouped into 4 tabs (1-4 switch tabs)
    _STUDIO_GROUPS = (
        (u"TEXT", ("size", "font", "color", "spacing", "autowrap")),
        (u"LOOK", ("outline", "ocolor", "bg", "bg_alpha")),
        (u"POSITION", ("offset_y", "align", "cuepos")),
        (u"SYNC", ("delay", "pickline", "presets", "reset", "remove")),
    )
    _STUDIO_HINTS = {
        "size": u"▲ bigger   ▼ smaller",
        "font": u"OK — choose a font",
        "color": u"▲ ▼ change the text colour",
        "spacing": u"▲ ▼ space between the two lines",
        "autowrap": u"▲ ▼ on / off — split long lines automatically",
        "outline": u"▲ ▼ on / off — dark edge around the letters",
        "ocolor": u"▲ ▼ change the edge colour",
        "bg": u"▲ ▼ on / off — box behind the text",
        "bg_alpha": u"▲ more see-through   ▼ more solid",
        "offset_y": u"▲ ▼ move the subtitle up / down",
        "align": u"▲ ▼ top / bottom / centre of the screen",
        "cuepos": u"▲ ▼ on / off — use positions written in the subtitle file",
        "delay": u"▲ +250 ms   ▼ −250 ms   (text too early → +, too late → −)",
        "pickline": u"OK — pick the line you hear now to sync",
        "presets": u"OK — save / load styles A, B, C",
        "reset": u"OK — back to the default style",
        "remove": u"OK — turn the subtitles off",
    }
    _STUDIO_CARDS = ("studioCard0","studioCard1","studioCard2","studioCard3","studioCard4")
    _STUDIO_DESIGN_Y = {
        "studioPanel": 36, "studioAccent": 36,
        "studioTitle": 52, "studioStatus": 54, "studioHelp": 252,
        "studioCard0": 100, "studioCard1": 100, "studioCard2": 100,
        "studioCard3": 100, "studioCard4": 100,
        "studioTab0": 48, "studioTab1": 48, "studioTab2": 48, "studioTab3": 48,
        "studioPreviewBg": 192, "studioPreview": 194,
    }
    _STUDIO_SLIDE_PX = 320

    _AUTONEXT_SECONDS_DEFAULT = 10
    _ASPECT_MODES = [("Full", 0), ("14:9", 120), ("4:3", 240)]
    _AV_PROBE_MODES = [
        ("System 4:3 — probe v0", 0),
        ("System 16:9 — probe v1", 1),
        ("System 16:10 — probe v2", 2),
        ("System probe v3", 3),
        ("System probe v4", 4),
        ("System probe v5", 5),
    ]

    _OSD_ANIM_MS       = 20
    _OSD_ANIM_EASE     = 0.35
    _AUTONEXT_ANIM_MS  = 20
    _AUTONEXT_ANIM_EASE= 0.35
    _SEEK_FLASH_MS     = 800
    _EXTRACT_HINT_MS   = 3000
    _QUALITY_WIPE_MS   = 25
    _QUALITY_WIPE_FRAMES = 10

    def __init__(self, session, title, candidates, previous_service=None, resume_pos=0, item_url="",
                 next_episode=None, on_next=None, poster_url="", quality_variants=None, server_url="",
                 is_preview=False, servers=None, site="", server_name=""):
        Screen.__init__(self, session)
        # [log-tags] every log line of this playback carries this id
        try:
            import random
            self._play_id = "P" + "".join(random.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(3))
            set_play_id(self._play_id)
            my_log("playback start: {} ({} methods to try)".format(title, len(candidates or [])))
        except Exception:
            pass
        self["overlay_bg"]   = Label("")
        self["status"]       = Label("Playing...")
        self["osd_shadow"]   = Label("")
        self["osd_titlebar"] = Label("")
        self["osd_title"]    = Label("")
        self["osd_durtext"]  = Label("")
        self["osd_topline"]  = Label("")
        self["seekbar"]      = ProgressBar()
        self["prog_bar"]     = Label("")
        self["osd_elapsed"]  = Label("")
        self["osd_state"]    = Label("")
        self["osd_hints"]    = Label("")
        self["osd_divider"]  = Label("")
        self["osd_keybar"]   = Label("")
        self["osd_keys"]     = Label("")
        self["osd_botline"]  = Label("")
        self["aspect_bar_l"] = Label("")
        self["aspect_bar_r"] = Label("")
        self["osdPosterBox"] = Label("")
        self["osdPoster"]    = Pixmap()
        self["osd_videinfo"] = Label("")
        self["osd_subinfo"] = Label("")
        self["osd_clock"] = Label("")
        self["recBlink"]     = Label("● REC")
        # [POLISH v1] seek-bar polish + colour key chips + second info row
        self["seekFrame"]    = Label("")
        self["seekMarker"]   = Label("")
        self["progBack"]     = Label("")
        self["keyRed"]       = Label(u"Next server ⏭")
        self["keyGreen"]     = Label(u"Skip intro")
        self["keyYellow"]    = Label(u"Quality · Server")
        self["keyBlue"]      = Label(u"Audio · Subs")
        self["osd_subdetail"] = Label("")
        self["osd_episode"]  = Label("")
        self["osd_jump"]     = Label("")
        self["seekFlash"]    = Label("")
        self["seekFlash"].hide()
        self["overlayEdge"]  = Label("")     # [osd-ui] thin outline around panel / poster
        self["posterEdge"]   = Label("")
        self["seekBubble"]   = Label("")      # [osd-ui] target time above the marker
        self["seekBubble"].hide()
        self["pauseBadge"]   = Label(u"\u275a\u275a")   # [osd-ui] big pause sign
        self["pauseBadge"].hide()
        self["qualityWipe"]  = Label("")
        self["qualityWipe"].hide()
        self["syncToast"]    = Label("")
        self["syncToast"].hide()
        for k in self._AUTONEXT_WIDGETS:
            if k == "autoNextProgress":
                self[k] = ProgressBar()
            elif "Poster" in k:
                self[k] = Pixmap()
            else:
                self[k] = Label("")
        self["subBg1"] = Label("")
        self["subBg2"] = Label("")
        self["subLine1"] = Label("")
        self["subLine2"] = Label("")
        for _li in range(2):
            for _di in range(8):
                self["subShadow%d_%d" % (_li, _di)] = Label("")
        self["studioPanel"] = Label("")
        self["studioAccent"] = Label("")
        self["studioTitle"] = Label("Subtitle Studio")
        self["studioStatus"] = Label("")
        self["studioHelp"] = Label(u"◀ ▶ setting   ▲ ▼ change   1 2 3 4 tab   OK menu   EXIT close")
        for _i in range(4):
            self["studioTab%d" % _i] = Label("")
        for c in self._STUDIO_CARDS:
            self[c] = Label("")
        self["studioPreviewBg"] = Label("")
        self["studioPreview"] = Label("")
        self["safeTop"] = Label("")
        self["safeBottom"] = Label("")
        self["safeLeft"] = Label("")
        self["safeRight"] = Label("")

        self._raw_title = (title or "").strip()

        _raw = self._raw_title
        _qtag_m = re.search(r'\s*(\[\d+p\])\s*$', _raw)
        _qtag = _qtag_m.group(1) if _qtag_m else ""
        _bare = _raw[:_qtag_m.start()].strip() if _qtag_m else _raw
        if len(_bare) > 34:
            _bare = _bare[:32].rstrip() + u"\u2026"
        self.title = (_bare + " " + _qtag).strip() if _qtag else _bare
        self.candidates = candidates or []
        self.previous_service = _copy_service_ref(previous_service)
        self.sref = None
        self._play_confirmed = False
        self._candidate_idx = -1
        self._candidate_start_ts = 0
        self._candidate_uses_proxy = False
        self._candidate_label = ""
        self._handoff = False
        self._restored_previous = False
        self._resume_pos = int(resume_pos or 0)
        self._item_url  = item_url or ""
        # [server-rank] host of the ORIGINAL server/embed link this stream
        # came from -- credited/blamed once playback is actually confirmed
        # or exhausted, not merely extracted.
        self._server_url = server_url or ""
        # [panel] the title's server list, for switching servers inside the player
        self._servers = [sv for sv in (servers or []) if isinstance(sv, dict) and sv.get("url")
                         and not str(sv.get("url")).startswith("magnet:")]
        self._site = site or ""
        self._server_name = server_name or next(
            (sv.get("name", "") for sv in self._servers if sv.get("url") == self._server_url), "")
        self._speed = 1
        self._closed_flag = False
        self._switch_kind = "quality"
        self._server_prev = None
        # [detail-trailer] a trailer has nothing to mark watched, save a
        # position for, or queue a next episode after -- so the near-end
        # exit prompt in _pl_onCancelKey (meant for "you're about to finish
        # a real episode, what do you want to do?") is just noise here.
        # Opt-in only: default False keeps the prompt exactly as-is for
        # every other caller/stream.
        self._is_preview = bool(is_preview)
        try:
            self.candidates = self._reorderCandidatesByPref(self.candidates)
        except Exception:
            pass
        self._poster_url = poster_url or ""
        self._quality_variants = self._normalizeQualityVariants(quality_variants)
        self._quality_current_url = (candidates[0][1] if candidates else "")
        _qlbl = ""
        try:
            from extractors.base import _label_quality_variant
            _qlbl = _label_quality_variant(self._quality_current_url) or ""
        except Exception:
            _qlbl = ""
        self._quality_current_label = _qlbl or (_qtag.strip("[]") if _qtag else "AUTO")
        self._quality_dead_urls = set()
        self._quality_switch_target_url = ""
        self._quality_prev_url = ""
        self._quality_prev_label = ""
        self._quality_prev_candidates = []
        self._quality_switch_in_flight = False
        self._quality_pending_pref = ""
        self._poster_painted = False
        self._poster_final = False
        self._poster_requested = False
        self._osd_video_info = ""
        self._next_episode = next_episode
        self._on_next = on_next
        self._next_prompt_enabled = str(_get_config("next_episode_prompt", "true")).lower() == "true"
        self._skip_restore = False
        self._next_open_timer = None
        self._eof_prompted = False
        self._sleep_timer = None
        self._sleep_minutes = 0
        self._aspect_idx = 0
        self._pic_extra = None
        self._pic_extra_list = []
        self._pic_pep_rows = []
        self._saved_av_aspect = None
        self._av_aspect_dirty = False
        try:
            from Components.config import config
            self._saved_av_aspect = int(config.av.aspect.value)
        except Exception:
            pass
        self._record_task = None
        self._rec_blink_on = True
        self._autonext_active = False
        self._autonext_nxt = None
        self._autonext_secs = 0
        self._autonext_poster_url = ""
        self._autonext_poster_polls = 0
        self._studioOverlayActive = False
        self._studioIdx = 0
        self._studioWin = 0
        self._studioItems = []
        self._studioChoiceKey = ""

        # [player-anim] OSD slide state
        self._osd_slide_p = 0.0
        self._osd_slide_target = 0.0
        self._osd_slide_timer = eTimer()
        self._osd_slide_timer.callback.append(self._osdSlideTick)

        # [player-anim] auto-next slide state
        self._autonext_slide_p = 0.0
        self._autonext_slide_target = 0.0
        self._autonext_slide_timer = eTimer()
        self._autonext_slide_timer.callback.append(self._autonextSlideTick)

        # [player-anim] seek flash
        self._seek_flash_timer = eTimer()
        self._seek_flash_timer.callback.append(self._hideSeekFlash)
        self._pause_badge_timer = eTimer()
        self._pause_badge_timer.callback.append(self._hidePauseBadge)

        # [player-anim] quality wipe
        self._quality_wipe_frame = 0
        self._quality_wipe_target = 0
        self._quality_wipe_cb = None
        self._quality_wipe_timer = eTimer()
        self._quality_wipe_timer.callback.append(self._qualityWipeTick)

        # [player-ux] extraction skip hint
        self._skip_hint_timer = eTimer()
        self._skip_hint_timer.callback.append(self._showSkipHint)
        self._skip_hint_shown = False

        # [player-anim] aspect-bar slide state
        self._aspect_anim_from = 0
        self._aspect_anim_to = 0
        self._aspect_anim_p = 1.0
        self._aspect_anim_timer = eTimer()
        self._aspect_anim_timer.callback.append(self._aspectAnimTick)

        # [player-anim] studio-panel slide state
        self._studio_slide_p = 1.0
        self._studio_slide_target = 1.0
        self._studio_slide_timer = eTimer()
        self._studio_slide_timer.callback.append(self._studioSlideTick)

        # [player-anim] sync toast
        self._sync_toast_x_from = 1550
        self._sync_toast_x_target = 1550
        self._sync_toast_p = 0.0
        self._sync_toast_frame = 0
        self._sync_toast_slide_timer = eTimer()
        self._sync_toast_slide_timer.callback.append(self._syncToastSlideTick)
        self._sync_toast_timer = eTimer()
        self._sync_toast_timer.callback.append(self._hideSyncToast)

        # [player-ux] hold-to-accelerate seek
        self._hold_dir = 0
        self._last_seek_step = 10   # [POLISH v1] last seek step, shown in osd_jump
        self._hold_count = 0
        self._hold_last_t = 0.0

        self._seek_timer = eTimer()
        self._seek_timer.callback.append(self._pl_doSeek)
        self._seek_retry_count = 0
        self._seek_verify_timer = eTimer()
        self._seek_verify_timer.callback.append(self._pl_verifySeek)
        self._hide_timer = eTimer()
        self._hide_timer.callback.append(self._pl_autoHideOSD)
        self._osd_update_timer = eTimer()
        self._osd_update_timer.callback.append(self._pl_updateOSD)
        self._osd_visible = False
        self._total_secs  = 0
        self._osd_auto_hide_secs = 4
        self._paused = False
        self._paused_elapsed = 0
        self._force_confirmation_timer = eTimer()
        self._force_confirmation_timer.callback.append(self._pl_forceConfirm)
        self._retry_timer = eTimer()
        self._retry_timer.callback.append(self._pl_onTimeout)
        self._autonext_timer = eTimer()
        self._autonext_timer.callback.append(self._pl_autonextTick)
        self._studioTimer = eTimer()
        self._studioTimer.callback.append(self._pl_studioTick)
        self._rec_blink_timer = eTimer()
        self._rec_blink_timer.callback.append(self._pl_tickRecBlink)
        self._stall_last_pts = -1
        self._stall_count = 0
        self._stall_fail_count = 0
        self._stall_recovering = False
        self._stall_recover_target = 0
        self._stall_timer = eTimer()
        self._stall_timer.callback.append(self._pl_stallWatchdog)
        self._stall_kick_timer = eTimer()
        self._stall_kick_timer.callback.append(self._pl_stallKickBack)
        self._autosub_gen = 0
        self._watched_at_90_fired = False

        for k in self._STUDIO_KEYS + self._AUTONEXT_WIDGETS:
            try: self[k].hide()
            except Exception: pass
        for k in ("safeTop","safeBottom","safeLeft","safeRight",
                  "aspect_bar_l","aspect_bar_r","recBlink",
                  "subBg1","subBg2","subLine1","subLine2") + \
                 tuple("subShadow%d_%d" % (li, di) for li in range(2) for di in range(8)):
            try: self[k].hide()
            except Exception: pass

        try:
            from novaplay_substudio import STUDIO
            STUDIO.bind(self)
        except Exception:
            pass

        self["actions"] = ActionMap(["OkCancelActions", "MediaPlayerActions", "NumberActions", "InfobarSeekActions", "DirectionActions", "ColorActions", "InfobarSubtitleActions", "InfobarSubtitleSelectionActions", "InfobarAudioSelectionActions", "MenuActions", "InfoBarMenuActions", "ButtonSetupActions", "InfoActions"], {
            "cancel":           self._pl_onCancelKey,
            "stop":             self._pl_onExit,
            "ok":               self._pl_onOkKey,
            "playpauseService": self._pl_togglePause,
            "right":            self._pl_navRightKey,
            "left":             self._pl_navLeftKey,
            "up":               self._pl_navUpKey,
            "down":             self._pl_navDownKey,
            "seekFwd":          self._pl_navSeekFwdKey,
            "seekBack":         self._pl_navSeekBackKey,
            "0": lambda: self._pl_seekPct(0),
            "1": lambda: self._pl_seekPct(10),
            "2": lambda: self._pl_seekPct(20),
            "3": lambda: self._pl_seekPct(30),
            "4": lambda: self._pl_seekPct(40),
            "5": lambda: self._pl_seekPct(50),
            "6": lambda: self._pl_seekPct(60),
            "7": lambda: self._pl_seekPct(70),
            "8": lambda: self._pl_seekPct(80),
            "9": lambda: self._pl_seekPct(90),
            "seekdef:1": lambda: self._pl_seekPct(10),
            "seekdef:3": lambda: self._pl_seekPct(30),
            "seekdef:4": lambda: self._pl_seekPct(40),
            "seekdef:6": lambda: self._pl_seekPct(60),
            "seekdef:7": lambda: self._pl_seekPct(70),
            "seekdef:9": lambda: self._pl_seekPct(90),
            "green":            self._pl_greenKey,
            "red":              self._pl_nextServer,
            "blue":             self._subsPanel,
            "yellow":           self._pl_panelMenu,
            "subtitles":        self._subsPanel,
            "subtitleSelection": self._subsPanel,
            "audioSelection":   self._pl_audioSelect,
            "menu":             self._playerMenu,
            "showMenu":         self._playerMenu,
            "mainMenu":         self._playerMenu,
            "info":             self._pl_streamInfo,
        }, -1)
        # [studio-ui] no CH+/CH- binding: in many keymaps ◀ ▶ also send the
        # zap actions, which made one press move a setting AND switch tab.
        eventmap = {
            iPlayableService.evTuneFailed: self._pl_onFailed,
            iPlayableService.evEOF: self._pl_onEOF,
        }
        ev_video = getattr(iPlayableService, "evVideoSizeChanged", None)
        if ev_video is not None:
            eventmap[ev_video] = self._pl_onConfirmed
        self._events = ServiceEventTracker(screen=self, eventmap=eventmap)
        self.onLayoutFinish.append(self._pl_initOSD)
        self.onLayoutFinish.append(self._pl_playNext)
        self.onClose.append(self._pl_stop)
        # Transmission on demand: torrents pause while something plays (they
        # share the HDD with playback and swap), resumed after exit.
        threading.Thread(target=_tr_call, args=("pause_all",), daemon=True).start()


def _play(session, url, title, resume_pos=0, item_url="",
          next_episode=None, on_next=None, poster_url="",
          quality_variants=None, server_url="", servers=None, site="", server_name=""):
    try:
        svc_url = str(url).strip()
        is_remote = svc_url.startswith("http://") or svc_url.startswith("https://")
        previous_service = _capture_previous_service(session)
        if is_remote:
            session.open(NovaPlaySimplePlayer, title,
                         _build_remote_play_candidates(svc_url), previous_service,
                         resume_pos=resume_pos, item_url=item_url,
                         next_episode=next_episode, on_next=on_next,
                         poster_url=poster_url,
                         quality_variants=quality_variants,
                         server_url=server_url, servers=servers, site=site,
                         server_name=server_name)
            return
        sref = eServiceReference(4097, 0, svc_url)
        if sys.version_info[0] == 3:
            sref.setName(str(title))
        else:
            sref.setName(title.encode("utf-8", "ignore"))
        try:
            from Screens.InfoBar import MoviePlayer
            callback = lambda *args: _restore_previous_service(session, previous_service)
            try:
                session.openWithCallback(callback, MoviePlayer, sref, askBeforeLeaving=False)
            except TypeError:
                session.openWithCallback(callback, MoviePlayer, sref)
        except Exception as e:
            my_log("[PLAY_INFOBAR_FALLBACK] " + str(e))
            session.open(NovaPlaySimplePlayer, title,
                         _build_remote_play_candidates(svc_url), previous_service,
                         resume_pos=resume_pos, item_url=item_url,
                         next_episode=next_episode, on_next=on_next,
                         poster_url=poster_url,
                         server_url=server_url)
    except Exception as e:
        my_log("[PLAY_ERROR] " + str(e))                                