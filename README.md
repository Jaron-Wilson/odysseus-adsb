# ADS-B Receiver

An [Odysseus](https://github.com/Jaron-Wilson/odysseus) mod for your own ADS-B feeder: an
[adsb.im](https://adsb.im/) Raspberry Pi, or any receiver running tar1090 and readsb.

It used to be the built-in **System > ADS-B receiver** page. Since Odysseus moved it out of the
core, `#adsb` links and saved Workspace tabs open this mod's tab (it declares
`"replaces": "adsb"`); without the mod they show a "This feature is now a mod" card.

## What it does

- **The ADS-B receiver tab** (System group, right after Devices): what the receiver decodes right
  now (aircraft heard and how many have a position, messages per second, range over the last 15
  minutes and the best ever, signal, peak and gain, totals since the decoder started) and the
  list of aircraft (flight, type, altitude, speed, last seen). It refreshes every 5 seconds while
  the tab is open and stops when it is hidden.
- **The live map**: the receiver's own tar1090 map beside the numbers, and **Open the map in a new
  tab** for the full map.
- **Links**: the feeder's adsb.im page (guessed from the map address: an adsb.im image serves the
  map on `:8080` and its setup page on the same host's default port; set it yourself in
  settings), the adsb.lol and adsb.fi maps the feeder sends to, and
  [Flys Down](https://flysdown.jaronwilson.dev/), the live ADS-B and AIS map built on the same
  network feed (its Filters, Advanced, "Use your own receiver" can read this receiver too). The
  aggregator links can be turned off.
- **A Home card**: aircraft now, with a position, messages per second.
- **Agent tools** (read only, offered when a message is about ADS-B, aircraft, planes, flights,
  the feeder or tar1090):
  - `adsb_status`: is the receiver working, and what it hears.
  - `adsb_aircraft`: the aircraft heard now, optionally only those with a position or matching a
    flight, registration, hex code or type.
- **A five-step tutorial**, shown the first time someone opens the tab (**Tutorial** in the tab's
  header replays it).

The status is fetched by the mod's server (tar1090 sends no CORS header on `stats.json`), cached
for 5 seconds, never following redirects and never through a proxy from the environment. The mod
is lazy: it starts when the tab, the card or a tool needs it and stops after 10 idle minutes, so it
costs nothing while nobody looks.

### The map inside the tab

Mod pages run in a sandboxed frame, and a frame inside it (the tar1090 map) inherits that sandbox:
it runs with an opaque origin. tar1090 copes (it falls back to an in-memory store when
`localStorage` is unavailable, and readsb's `aircraft.json` allows any origin), but map settings
you change inside the tab are not kept. **Open the map in a new tab** opens it as an ordinary page.

An Odysseus served over https (for example behind Tailscale Serve) can only show an https receiver
in the tab: browsers block an `http://` frame in an https page. The page says so and links to the
map in a new tab. To embed it, serve the receiver over https too (for example `tailscale serve` on
the Pi).

## Permissions

| Permission | Why |
|---|---|
| `network.lan` | It reads the receiver on your network or tailnet (informational for a process mod) |

Its settings live in the core (no permission needed). It sends no notifications and no events.

## Settings

| Setting | Meaning |
|---|---|
| Receiver address | The tar1090 map, like `http://<pi>:8080/` (an adsb.im feeder) or `https://<name>.<tailnet>.ts.net/`. A pasted `.../data/aircraft.json` is cut back to the map. |
| Feeder settings page | Optional; guessed from the address when it ends in `:8080` |
| Show links to adsb.lol, adsb.fi and Flys Down | On by default |

Anyone who may use the mod can change them; Odysseus makes a new mod admin only (Settings > Mods >
Who can use it), as the built-in page was.

## Moving from the built-in page

The built-in page kept the receiver in `<Odysseus data dir>/adsb.json`, as `{"url": "..."}` (or
read `ODYSSEUS_ADSB_URL`). Odysseus does not delete that file, so going back to an older Odysseus
still works. Three ways to bring it over, all of which only read the old file:

1. **In the mod's settings.** When the mod's data folder is the usual
   `<data dir>/mods/adsb/data`, the settings page finds the old `adsb.json` and offers its address
   under "Move from the built-in page": press **Use it**.
2. **Paste it.** Paste the file's contents (or just the address) into the box and press **Import**.
3. **From a shell**, with Odysseus running or stopped (the core reads the mod's settings file on
   every request):

   ```
   cd <data dir>/mods/adsb/src
   python3 -m adsb_mod import <data dir>/adsb.json --into <data dir>/mods/adsb/settings.json --dry-run
   python3 -m adsb_mod import <data dir>/adsb.json --into <data dir>/mods/adsb/settings.json
   ```

   The import keeps the mod's other settings and writes the file with mode 0600. If you used
   `ODYSSEUS_ADSB_URL` instead of the file, paste that address in settings.

**Export**: **Export these settings** on the settings page downloads
`odysseus-adsb-settings.json`, or from a shell
`python3 -m adsb_mod export <data dir>/mods/adsb/settings.json`. The export has the address at its
top level too, so it reads like the old `adsb.json`, and imports here again.

## Install in Odysseus

Settings > Mods > Browse (the repository has the `odysseus-mod` topic; a private repository shows
only with your GitHub token), **Details**, **Review and install**. Or Settings > Mods > Install and
paste `git@github.com:Jaron-Wilson/odysseus-adsb.git`. Approve the permission, then open
System > ADS-B receiver and set the address (or import it, above).

## Develop

Python 3.10 or newer, standard library only.

```
python -m pytest -q                  # tests, under the stand-in core, with a fake receiver
python -m odysseus_mod.devcore       # mock Odysseus at http://127.0.0.1:7900/
MOD_PASSWORD=secret python -m adsb_mod   # standalone with its own login (port 8765)
```

`CLAUDE.md` holds the mod contract, and the `new-mod` skill adds features from a description.

## License

MIT
