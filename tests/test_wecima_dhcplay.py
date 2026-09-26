# -*- coding: utf-8 -*-
"""PATCH 113 regression test: resolve_dhcplay. VIBUXER_FIXTURE is the real P.A.C.K.E.R.-packed
player script from a real wecima.cx capture (vibuxer.com/gn3l5skg35lv, 2026-09-22)."""

import extractors.hosts as h

VIBUXER_FIXTURE = """<html><body><script>eval(function(p,a,c,k,e,d){while(c--)if(k[c])p=p.replace(new RegExp('\\\\b'+c.toString(a)+'\\\\b','g'),k[c]);return p}('b 6q=[];b n={\"1d\":\"1j://6z.dm-dk.1r/1d/6y/6x/6w/6u.dj?t=di&s=36&e=dh&f=2l&dg=3e&i=0.4&df=4v&de=3e&dd=3e&dc=db\",\"y\":\"1j://6z.da.d9/3e/y/6y/6x/6w/6u.d8\"};1i(\"d7\").d6({d5:\"1\",d4:[{1k:n.1a||n.y||n.1d,2o:\"2n\"}],d3:\"1j://6t.1r/d2.6s\",d1:\"4c%\",d0:\"4c%\",cz:\"cy\",cx:\"cw.85\",cv:\\'cu\\',ct:{cs:{3c:\"#3d\",cr:\"#3d\"},cq:{cp:\"#3d\"},co:{3c:\"#3d\"}},cn:\"r\",p:[{1k:\"/dl?2b=cm&1u=cl&ck=1j://6t.1r/cj.6s\",ci:\"ch\"}],4l:{cg:1,cf:\\'#6r\\',ce:\\'#6r\\',cd:\"cc\",cb:0,ca:\\'4c\\',},\"c9\":{\"c8\":\"6o\",\"c7\":\"c6\"},\\'c5\\':{\"c4\":\"c3\"},c2:\"c1\",c0:\"1j://bz.1r\",by:{},bx:r,4k:[0.25,0.5,0.75,1,1.25,1.5,2]});b 49,4b;b bw=0,bv=0,bu=0;b g=1i();b 38=0,28=0,bt=0,o=0;$.bs({br:{\\'bq-bp\\':\\'bo-bn\\'}});g.1c(\\'6p\\',j(x){a(5>0&&x.1n>=5&&4b!=1){4b=1;$(\\'26.bm\\').bl(\\'bk\\')}b 4a=0;6q.bj(15=>{a(15.6p<=x.1n&&15.6h==0){a(15.6m==\\'6o\\'){a(15.1s.6l(\\'1j://\\')){g.6n(15.1s)}16{b 3b=2q 6k().6j(15.1s,\"3c/35\");15.1s=\"2k:bi/bh;bg,\"+bf(be(4q(3b.6i.3a)));g.6n(15.1s)}}16 a(15.6m==\\'bd\\'){bc(4a,15.1s)}16{b 1h=15.1s.bb();b 1e=2j.6d(\\'1e\\');a(1h.6l(\\'1j://\\')){1e.3q=1h;1e.ba=r}16{b 3b=2q 6k().6j(1h,\"3c/35\");1h=3b.6i.3a;b 23=1h.23(/<1e[^>]*>([\\\\s\\\\b9]*?)<\\\\/1e>/i);a(23){1e.3a=23[1]}16{1e.3a=1h}}2j.66.65(1e)}15.6h=1}4a++});a(x.1n>=o+5||x.1n<o){o=x.1n;1l.b8(\\'1z\\',b7.b6(o),{b5:60*60*24*7})}a(x.b4){39=x.1n-38;a(39>5)39=1;28+=39}38=x.1n;2u.2t(28);a(28>=60){$.b3(\\'1j://b2.b1.1r/dl\\',{2b:\\'b0\\',45:\\'2l-42-40-36-3z\\',az:3u(28),ay:2l,ax:\\'2p\\'},j(){},\"aw\");28=0}});g.1c(\\'1y\\',j(x){38=x.1n});g.1c(\\'3s\\',j(x){6f(x)});g.1c(\\'av\\',j(){$(\\'26.6e\\').au();1l.at(\\'1z\\')});g.1c(\\'as\\',j(x){});j ar(29,6g,2z){b 37=2q 55();37.aq(37.ap()+(2z*ao));2j.an=29+\"=\"+6g+\"; am=\"+37.al()+\"; ak=.aj.1r; 2h=/; ai=ah; ag\"}j 6f(x){$(\\'26.6e\\').3x();$(\\'#af\\').3x();a(49)1t;49=1;3y=0;ae 2i=2j.6d(\\'1e\\');2i.3q=\\'1j://ad.ac.1r/6c/ab/aa.6c\\';2i.a9=()=>{$.20(\\'/dl?2b=6b&6a=2p&45=2l-42-40-36-3z&69=&68=&3y=1&1a=1\\',j(2k){$(\\'#67\\').35(2k)})};2i.a8=()=>{$.20(\\'/dl?2b=6b&6a=2p&45=2l-42-40-36-3z&69=&68=&3y=0&1a=1\\',j(2k){$(\\'#67\\').35(2k)})};2j.66.65(2i);b o=1l.20(\\'1z\\');a(o>0){1i().1y(o)}}j a7(){b p=g.3g(64);2u.2t(p);a(p.1u>1){4f(i=0;i<p.1u;i++){a(p[i].29==64){2u.2t(\\'!!=\\'+i);g.4d(i)}}}}g.1c(\\'4p\\',j(){1i().3n(\\'<18 5p=\"5o://5n.5m.5l/5k/18\" 5j=\"k-18-1q k-18-1q-a6\" 5i=\"0 0 33 33\" 5h=\"q\"><2h d=\"m 25.a5,57.a4 v a3.3 c 0.a2,2.a1 2.a0,4.9z 4.8,4.8 h 62.7 v -19.3 h -48.2 v -96.4 63 9y.9x v 19.3 c 0,5.3 3.6,7.2 8,4.3 l 41.8,-27.9 c 2.9w,-1.9v 4.9u,-5.9t 2.7,-8 -0.9s,-1.9r -1.9q,-2.9p -2.7,-2.7 l -41.8,-27.9 c -4.4,-2.9 -8,-1 -8,4.3 v 19.3 63 30.9o c -2.9n,0.9m -4.9l,2.9k -4.9,4.9 z m 9j.9i,73.9h c -3.61,-6.5z -10.5y,-10.5x -17.7,-10.6 -7.5w,0.5v -13.5u,4.5t -17.7,10.6 -8.34,14.5s -8.34,32.5r 0,46.3 3.61,6.5z 10.5y,10.5x 17.7,10.6 7.5w,-0.5v 13.5u,-4.5t 17.7,-10.6 8.34,-14.5s 8.34,-32.5r 0,-46.3 z m -17.7,47.2 c -7.8,0 -14.4,-11 -14.4,-24.1 0,-13.1 6.6,-24.1 14.4,-24.1 7.8,0 14.4,11 14.4,24.1 0,13.1 -6.5,24.1 -14.4,24.1 z m -47.9g,9.9f v -51 l -4.8,4.8 -6.8,-6.8 13,-12.9e c 3.9d,-3.9c 8.9b,-0.9a 8.2,3.4 v 62.99 z\"></2h></18>\\',\"98 10 2z\",j(){1i().1y(1i().5b()+10)},\"5q\");$(\"26[5a=5q]\").58().56(\\'.k-1q-2x\\');1i().3n(\\'<18 5p=\"5o://5n.5m.5l/5k/18\" 5j=\"k-18-1q k-18-1q-2x\" 5i=\"0 0 33 33\" 5h=\"q\"><2h d=\"97.2,94.93.1m,21.1m,0,0,0-17.7-10.6,21.1m,21.1m,0,0,0-17.7,10.6,44.31,44.31,0,0,0,0,46.3,21.1m,21.1m,0,0,0,17.7,10.6,21.1m,21.1m,0,0,0,17.7-10.6,44.31,44.31,0,0,0,0-46.92-17.7,47.2c-7.8,0-14.4-11-14.4-24.91.6-24.1,14.4-24.1,14.4,11,14.4,24.90.4,5g.8z,95.5,5g.8y-43.4,9.7v-8x-4.8,4.8-6.8-6.8,13-8w.8,4.8,0,0,1,8.2,3.8v.7l-9.6-.8u-8t.8s.8r.5f,4.5f,0,0,1-4.8,4.8q.6v-19.8p.2v-96.8o.8n.8m,5.3-3.6,7.2-8,4.3l-41.8-27.8l.5e,6.5e,0,0,1-2.7-8,5.5d,5.5d,0,0,1,2.7-2.8k.8-27.8j.4-2.9,8-1,8,4.8i.8h.8g.5c,4.5c,0,0,1,8f.1,57.8e\"></2h></18>\\',\"8d 10 2z\",j(){b 2y=1i().5b()-10;a(2y<0)2y=0;1i().1y(2y)},\"59\");$(\"26[5a=59]\").58().56(\\'.k-1q-2x\\');$(\"26.k-1q-2x\").3x()});b 2w=0;b 3w=0;b 2s=q;b 3r=q;g.1c(\\'8c\\',j(1o){b 3v=55.8b();a(3v-3w<8a){2w++;a(2w>3){1t q}}16{2w=0}3w=3v;b 1g=1o.1h||0;b 2g=(1o.89||\\'\\').88();b 22=q;a(3t 1g===\\'87\\'){1g=3u(1g,10)}a(1g>=4z&&1g<=4y){22=r}16 a(1g>=52&&1g<=50){22=r}16 a(2g.1p(\\'86\\')!==-1||2g.1p(\\'84\\')!==-1){22=r}16 a(2g.23(/54+(d{6})/)){b 53=2g.23(/54+(d{6})/);b 2f=3u(53[1],10);a((2f>=52&&2f<=50)||(2f>=4z&&2f<=4y)){22=r}}a(!22){1t q}4s{b 1b=g.4o().1k;b 4w=g.83().p||[];b 1x=82;b 2d=q,2r=q;a(n.1a&&1b){2d=(1b===n.1a)||(1b.1p(\\'/4x/\\')===0&&n.1a.1p(\\'/4x/\\')===0)||(1b.1p(\\'1a\\')!==-1)}a(n.y&&1b&&!2d){2r=(1b===n.y)||(1b.1p(n.y)!==-1)||(1b.1p(\\'y\\')!==-1)}a(2d){1x=n.y||n.1d}16 a(2r){1x=n.1d}a(1x&&!2s){2s=r;g.3o([{1k:1x,2o:\\'2n\\',p:4w}]);b 2e=q;b 81=1l.20(\\'1z\\');g.4u(\\'80\\',j(){a(!2e&&3t 1l!==\\'4t\\'){2e=r;2a(j(){b o=1l.20(\\'1z\\');a(o&&o>0){g.1y(o)}},4v)}});g.4u(\\'3s\\',j(){a(!2e&&3t 1l!==\\'4t\\'){2e=r;2a(j(){b o=1l.20(\\'1z\\');2u.2t(\\'o 7z 7y\\',o);a(o&&o>0){g.1y(o)}},7x)}});2a(j(){g.3s()},7w);2a(j(){2s=q},7u);1t r}a(!1x&&!3r){3r=r;b 4r=2d?\\'1a\\':(2r?\\'y\\':\\'1d\\');4s{(2q 7t()).3q=(\\'/dl?2b=7s&1k=2p&7r=\\'+4r+\\'&1h=\\'+4q(7q(1g).7p(0,12)))}3p(e){}}}3p(7o){}});g.1c(\\'4p\\',j(){b 1b=g.4o().1k;a(n.1a&&1b===n.1a){7n(n.1a,{7m:\\'7k\\'}).7j(4n=>{a(!4n.7i&&(n.y||n.1d)){g.4m();g.3o([{1k:n.y||n.1d,2o:\"2n\"}]);}}).3p(()=>{a(n.y||n.1d){g.4m();g.3o([{1k:n.y||n.1d,2o:\"2n\"}]);}})}});g.1c(\"1f\",j(1o){b p=g.3g();a(p.1u<2)1t;$(\\'.k-w-7h-7g\\').7f(j(){$(\\'#k-w-u-1f\\').3j(\\'k-w-u-2m\\');$(\\'.k-u-1f\\').1w(\\'1v-3k\\',\\'q\\')});g.3n(\"/7e/7d.18\",\"7c 7b\",j(){$(\\'.k-4j\\').7a(\\'k-w-4i\\');$(\\'.k-w-4l, .k-w-4k\\').1w(\\'1v-3m\\',\\'q\\');a($(\\'.k-4j\\').79(\\'k-w-4i\\')){$(\\'.k-u-1f\\').1w(\\'1v-3m\\',\\'r\\');$(\\'.k-u-1f\\').1w(\\'1v-3k\\',\\'r\\');$(\\'.k-w-u-78\\').3j(\\'k-w-u-2m\\');$(\\'.k-w-u-1f\\').77(\\'k-w-u-2m\\')}16{$(\\'.k-u-1f\\').1w(\\'1v-3m\\',\\'q\\');$(\\'.k-u-1f\\').1w(\\'1v-3k\\',\\'q\\');$(\\'.k-w-u-1f\\').3j(\\'k-w-u-2m\\')}},\"76\");g.1c(\"74\",j(1o){3i.72(\\'3h\\',1o.p[1o.71].29)});a(3i.4h(\\'3h\\')){2a(\"4g(3i.4h(\\'3h\\'));\",70)}});b 3f;j 4g(4e){b p=g.3g();a(p.1u>1){4f(i=0;i<p.1u;i++){a(p[i].29==4e){a(i==3f){1t}3f=i;g.4d(i)}}}}',36,491,'||||||||||if|var|||||player|||function|jw|||links|lastt|tracks|false|true|||submenu||settings||hls3|||||||item|else||svg||hls4|currentFile|on|hls2|script|audioTracks|errorCode|code|jwplayer|https|file|ls|589|position|event|indexOf|icon|com|link|return|length|aria|attr|newFile|seek|ttgn3l5skg35lv|get||shouldSwitch|match|||div||tott|name|setTimeout|op||isHLS4|seekDone|codeFromMessage|errorMessage|path|ggima|document|data|75026437|active|hls|type|gn3l5skg35lv|new|isHLS3|switchedLink|log|console||errorCount|rewind|tt|sec||769||240|60009|html|1790104210|date|prevt|dt|textContent|doc|text|1db0ef|GvRy31Y4fe02|current_audio|getAudioTracks|default_audio|localStorage|removeClass|expanded||checked|addButton|load|catch|src|beaconSent|play|typeof|parseInt|currentTime|lastErrorTime|hide|adb|4af10147c94c89a9ec326347cb99b3a8|164||197|||hash||||vvplay|itads|vvad|100|setCurrentAudioTrack|audio_name|for|audio_set|getItem|open|controls|playbackRates|captions|stop|res|getPlaylistItem|ready|encodeURIComponent|lvl|try|undefined|once|500|currentTracks|stream|233999|232000|202999||202000|codeMatch|Errors|Date|insertAfter||detach|ff00|button|getPosition|974|887|013|867|178|focusable|viewBox|class|2000|org|w3|www|http|xmlns|ff11|06475|23525|29374|97928|30317|31579|29683|38421|30626||72072||H|track_name|appendChild|body|fviews|referer|embed|file_code|view|js|createElement|video_ad|doPlay|value|loaded|documentElement|parseFromString|DOMParser|startsWith|xtype|playAd|vast|time|uas|FFFFFF|jpg|huntrexus|master||gn3l5skg35lv_o|15005|01|cwfuommwagyrfcg6|300|currentTrack|setItem||audioTrackChanged||dualSound|addClass|quality|hasClass|toggleClass|Track|Audio|dualy|images|mousedown|buttons|topbar|ok|then|HEAD||method|fetch|err|slice|String|level|hlserr|Image|10000||200|800|done|change|firstFrame|lastt1|null|getConfig|networkError||fragLoadError|string|toString|message|3000|now|error|Rewind|778Z|214|2A4|3H209|3v19|9c4|7l41|9a6|3c0|1v19|4H79|3h48|8H146|3a4|2v125|130|1Zm162|4v62|13a4|51l|278Zm|278|1S103|1s6|3Zm|078a21|131|||M113|Forward|69999|88605|21053|03598|02543|99999|72863|77056|04577|422413|163|210431|860275|03972|689569|893957|124979|52502|174985|57502|04363|13843|480087|93574|99396|160|76396|164107|63589|03604|125|778|993957|rewind2|set_audio_track|onload|onerror|ima3|sdkloader|googleapis|imasdk|const|over_player_msg|Secure|None|SameSite|vibuxer|domain|toGMTString|expires|cookie|1000|getTime|setTime|createCookieSec|pause|remove|show|complete|jsonp|file_real|file_id|ss|view4|dohaxe|logs|post|viewable|ttl|round|Math|set|S|async|trim|pickDirect|direct|unescape|btoa|base64|xml|application|forEach|slow|fadeIn|video_ad_fadein|cache|no|Cache|Content|headers|ajaxSetup|v2done|pop3done|vastdone2|vastdone1|playbackRateControls|cast|streamhg|aboutlink|StreamHG|abouttext|Original|1328|qualityLabels|insecure|vpaidmode|client|advertising|fontOpacity|backgroundOpacity|Tahoma|fontFamily|backgroundColor|color|userFontScale|thumbnails|kind|gn3l5skg35lv0000|url|6008|get_slides|androidhls|menus|progress|timeslider|icons|controlbar|skin|auto|preload|6007|duration|uniform|stretching|height|width|gn3l5skg35lv_xt|image|sources|debug|setup|vplayer|txt|sbs|virtualanalytics|24863|asn|p2|p1|sp|srv|129600|bd5HFm8ptk6Lzjn0QJ6danloN_pcsf35o_xDrnNMXfs|m3u8|centaurus||cdn'.split('|')))
</script></body></html>"""

REAL_STREAM_URL = ("https://cwfuommwagyrfcg6.cdn-centaurus.com/hls2/01/15005/gn3l5skg35lv_o/"
                    "master.m3u8?t=bd5HFm8ptk6Lzjn0QJ6danloN_pcsf35o_xDrnNMXfs&s=1790104210"
                    "&e=129600&f=75026437&srv=GvRy31Y4fe02&i=0.4&sp=500&p1=GvRy31Y4fe02"
                    "&p2=GvRy31Y4fe02&asn=24863")


def _patched(fake):
    class Ctx(object):
        def __enter__(self):
            self.saved = h.fetch
            h.fetch = fake
        def __exit__(self, *a):
            h.fetch = self.saved
    return Ctx()


def test_direct_redirect_dhcplay_lands_on_vibuxer():
    # fetch() already followed the redirect at the HTTP level -- the common case
    with _patched(lambda url, referer=None, **k: (VIBUXER_FIXTURE, "https://vibuxer.com/gn3l5skg35lv")):
        result = h.resolve_dhcplay("https://dhcplay.com/gn3l5skg35lv")
    assert result == REAL_STREAM_URL, result


def test_meta_refresh_to_vibuxer_is_followed():
    calls = []
    def fake(url, referer=None, **k):
        calls.append(url)
        if "dhcplay.com" in url:
            return '<html><meta http-equiv="refresh" content="0; url=https://vibuxer.com/gn3l5skg35lv"></html>', url
        return VIBUXER_FIXTURE, url
    with _patched(fake):
        result = h.resolve_dhcplay("https://dhcplay.com/gn3l5skg35lv")
    assert result == REAL_STREAM_URL, result
    assert any("dhcplay.com" in c for c in calls) and any("vibuxer.com" in c for c in calls)


def test_embedded_vibuxer_url_in_html_is_found():
    def fake(url, referer=None, **k):
        if "dhcplay.com" in url:
            return '<html>some page mentioning https://vibuxer.com/gn3l5skg35lv somewhere</html>', url
        return VIBUXER_FIXTURE, url
    with _patched(fake):
        result = h.resolve_dhcplay("https://dhcplay.com/gn3l5skg35lv")
    assert result == REAL_STREAM_URL, result


def test_no_vibuxer_reference_returns_none_not_a_crash():
    with _patched(lambda url, referer=None, **k: ("<html>nothing relevant here</html>", url)):
        result = h.resolve_dhcplay("https://dhcplay.com/somecode")
    assert result is None


def test_fetch_failure_returns_none():
    with _patched(lambda url, referer=None, **k: (None, url)):
        assert h.resolve_dhcplay("https://dhcplay.com/x") is None
