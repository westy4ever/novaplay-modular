# NovaPlay Media Center

Enigma2 plugin for Arabic, YTS and torrent streaming (formerly "Advanced Arabic Player").

## Install or update

On the box (SSH/Telnet as `root`):

```
wget -qO /tmp/install_novaplay.sh https://raw.githubusercontent.com/westy4ever/novaplay-modular/main/install_novaplay.sh && sh /tmp/install_novaplay.sh
```

No internet on the box? Download the ZIP from GitHub (**Code > Download ZIP**), copy it and
`install_novaplay.sh` to `/tmp` on the box with FileZilla or WinSCP, then run:

```
sh /tmp/install_novaplay.sh /tmp/novaplay-modular-main.zip
```

The installer backs up the current version, keeps your `api_keys.conf`, removes the old
`AdvancedArabicPlayer` plugin if present (backed up first), checks the files, and restarts Enigma2.
NovaPlay then appears in the plugin menu. Settings, favourites, history and watch positions from
the old plugin are carried over automatically on first start.

| What | Where |
| --- | --- |
| Plugin | `/usr/lib/enigma2/python/Plugins/Extensions/NovaPlay` |
| Settings | `/etc/enigma2/novaplay_state.json` |
| Log | `/tmp/novaplay.log` |
| Cache, downloads, backups | `/media/hdd/NovaPlay/` |

## Tests

```
cd /usr/lib/enigma2/python/Plugins/Extensions/NovaPlay
python3 run_site_test.py --all
```

Full guide: [docs/SITE_TEST_GUIDE.md](docs/SITE_TEST_GUIDE.md) — commands, how to read the report, what to do for each result.

To capture a site for a new resolver, use [Stream Recorder Pro](https://github.com/westy4ever/Stream-Recorder-Pro-12) — see its [guide](https://github.com/westy4ever/Stream-Recorder-Pro-12/blob/main/docs/GUIDE.md).
