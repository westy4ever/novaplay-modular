#!/usr/bin/env python3
"""NOVA Media Player -- Cloudflare WARP test for servers blocked on this line.

Creates a free WARP account (no sign-up), brings up a WireGuard interface
"wgnova" and routes ONLY the given addresses through it -- everything else
(live TV, other sites) keeps using the normal connection.

    python3 warp_test.py                 # set up + test s1.cdnmvs.online
    python3 warp_test.py --host other.example.com
    python3 warp_test.py --down          # remove the tunnel

Needs: opkg install kernel-module-wireguard wireguard-tools iproute2
"""
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone

IFACE = "wgnova"
STATE = "/etc/enigma2/novaplay_warp.json"
API = "https://api.cloudflareclient.com/v0a1922/reg"
HDRS = {"User-Agent": "okhttp/3.12.1", "CF-Client-Version": "a-6.3-1922",
        "Content-Type": "application/json; charset=UTF-8"}
DEFAULT_HOST = "s1.cdnmvs.online"


def sh(cmd, check=True, inp=None):
    r = subprocess.run(cmd, shell=True, input=inp, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError("{} -> {}".format(cmd, (r.stderr or r.stdout).strip()))
    return r.stdout.strip()


def api(method, url, body=None, token=None):
    h = dict(HDRS)
    if token:
        h["Authorization"] = "Bearer " + token
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode())


def register():
    if os.path.exists(STATE):
        with open(STATE) as f:
            st = json.load(f)
        print("WARP account: reusing", STATE)
        return st
    priv = sh("wg genkey")
    pub = sh("wg pubkey", inp=priv)
    print("WARP account: registering a free one ...")
    reg = api("POST", API, {
        "key": pub, "install_id": "", "fcm_token": "",
        "tos": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "model": "PC", "serial_number": "", "locale": "en_US"})
    rid, token = reg["id"], reg["token"]
    try:
        reg = api("PATCH", API + "/" + rid, {"warp_enabled": True}, token)
    except Exception as e:
        print("  (enable step: {} -- continuing)".format(e))
    cfg = reg.get("config") or {}
    peer = (cfg.get("peers") or [{}])[0]
    ep = (peer.get("endpoint") or {})
    host = ep.get("v4") or ep.get("host") or "162.159.192.1:2408"
    h, _, port = host.rpartition(":")
    if not port or port == "0":
        port = "2408"
    if not h or h.startswith("engage"):
        h = "162.159.192.1"
    st = {"id": rid, "token": token, "private_key": priv,
          "peer_public_key": peer.get("public_key"),
          "endpoint": "{}:{}".format(h, port),
          "address_v4": (cfg.get("interface") or {}).get("addresses", {}).get("v4", "172.16.0.2")}
    with open(STATE, "w") as f:
        json.dump(st, f)
    os.chmod(STATE, 0o600)
    print("WARP account: created")
    return st


def up(st):
    sh("modprobe wireguard", check=False)
    sh("ip link del {}".format(IFACE), check=False)
    sh("ip link add dev {} type wireguard".format(IFACE))
    kf = "/tmp/.{}.key".format(IFACE)
    with open(kf, "w") as f:
        f.write(st["private_key"])
    os.chmod(kf, 0o600)
    try:
        sh("wg set {} private-key {} peer {} allowed-ips 0.0.0.0/0 endpoint {} persistent-keepalive 25".format(
            IFACE, kf, st["peer_public_key"], st["endpoint"]))
    finally:
        os.remove(kf)
    sh("ip addr add {}/32 dev {}".format(st["address_v4"], IFACE))
    sh("ip link set {} mtu 1280 up".format(IFACE))
    print("Tunnel {}: up (endpoint {})".format(IFACE, st["endpoint"]))


def route(host):
    ips = sorted({i[4][0] for i in socket.getaddrinfo(host, 443, socket.AF_INET)})
    for ip in ips:
        sh("ip route replace {}/32 dev {}".format(ip, IFACE))
    print("Routing {} ({}) through WARP -- nothing else".format(host, ", ".join(ips)))
    return ips


def test(host):
    t = time.time()
    try:
        s = socket.create_connection((host, 443), timeout=12)
        s.close()
        print("TEST: {} answered in {:.1f} s  ->  WARP gets past the block".format(host, time.time() - t))
        return True
    except Exception as e:
        print("TEST: {} still unreachable ({})".format(host, e))
        hs = sh("wg show {} latest-handshakes".format(IFACE), check=False)
        print("      WireGuard handshake: {}".format(
            "none -- WARP itself may be blocked on this line" if not hs or hs.endswith("\t0") else "ok"))
        return False


def main():
    if "--down" in sys.argv:
        sh("ip link del {}".format(IFACE), check=False)
        print("Tunnel removed")
        return
    host = DEFAULT_HOST
    if "--host" in sys.argv:
        host = sys.argv[sys.argv.index("--host") + 1]
    for tool in ("wg", "ip"):
        if not sh("which " + tool, check=False):
            print("Missing '{}': opkg install kernel-module-wireguard wireguard-tools iproute2".format(tool))
            return
    try:
        st = register()
        up(st)
        route(host)
        time.sleep(2)
        test(host)
    except Exception as e:
        print("FAILED:", e)


if __name__ == "__main__":
    main()
