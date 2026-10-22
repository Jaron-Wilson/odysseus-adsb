"""The mod's settings, and moving them in and out.

Settings (kept by the core, GET/PUT /api/mods/v1/settings; standalone in
MOD_DATA_DIR/settings.json):

    url                  the receiver's tar1090 map, like http://adsb-feeder:8080/
    feeder_url           the adsb.im feeder's own page ("" = guess it from url)
    show_network_links   links to adsb.lol, adsb.fi and Flys Down (default on)

Migration from the Odysseus core, where this page used to be built in: the
core kept the receiver in <data dir>/adsb.json as {"url": "..."}. import_json
takes that file's text (or this mod's own export), and legacy_file finds it
next to the mods folder so the settings page can offer it. Nothing is ever
read or written there without the user pressing Import; the core's file is
only read, never changed.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional, Tuple

from adsb_mod import receiver

DEFAULTS: Dict[str, Any] = {"url": "", "feeder_url": "", "show_network_links": True}
EXPORT_FORMAT = "odysseus-adsb/1"

# Public pages that go with a feeder. adsb.im feeds both aggregators; Flys Down
# (flysdown.jaronwilson.dev) is a live map built on the same network feed.
NETWORK_LINKS = [
    {"label": "adsb.lol map", "url": "https://adsb.lol/",
     "note": "The aggregator this feeder sends to. Its feeder check (api.adsb.lol/0/me) only works from the Pi."},
    {"label": "adsb.fi map", "url": "https://globe.adsb.fi/",
     "note": "The other aggregator. Its feeder check is api.adsb.fi/v1/myip, also from the Pi."},
    {"label": "Flys Down", "url": "https://flysdown.jaronwilson.dev/",
     "note": "Live ADS-B and AIS map with geofence alerts. Filters, Advanced, \"Use your own receiver\" adds this one."},
]


def clean(raw: Any) -> Dict[str, Any]:
    """Settings as stored, checked. Raises ValueError for a bad address."""
    raw = raw if isinstance(raw, dict) else {}
    out = dict(DEFAULTS)
    out["url"] = receiver.normalize_url(raw.get("url") or "")
    out["feeder_url"] = receiver.normalize_url(raw.get("feeder_url") or "")
    if "show_network_links" in raw:
        out["show_network_links"] = bool(raw.get("show_network_links"))
    if isinstance(raw.get("imported_from"), str):
        out["imported_from"] = raw["imported_from"][:200]
    return out


def load(core) -> Dict[str, Any]:
    """The saved settings, with defaults. A broken saved value counts as unset."""
    raw = core.settings() or {}
    try:
        return clean(raw)
    except ValueError:
        out = dict(DEFAULTS)
        out["show_network_links"] = bool(raw.get("show_network_links", True))
        return out


def save(core, values: Dict[str, Any]) -> Dict[str, Any]:
    current = load(core)
    merged = dict(current)
    for k in ("url", "feeder_url", "show_network_links"):
        if k in values:
            merged[k] = values[k]
    out = clean(merged)
    core.save_settings(out)
    receiver.clear_cache()
    return out


def links(s: Dict[str, Any]) -> list:
    out = []
    if s.get("url"):
        out.append({"label": "Open the map in a new tab", "url": s["url"], "note": "tar1090 on the receiver"})
    feeder = s.get("feeder_url") or receiver.feeder_page(s.get("url") or "")
    if feeder:
        out.append({"label": "Feeder settings (adsb.im)", "url": feeder,
                    "note": "The Pi's own page: SDR, aggregators, MLAT, updates"})
    if s.get("show_network_links", True):
        out += NETWORK_LINKS
    return out


# ── Migration ───────────────────────────────────────────────────────────────

def parse_import(text: str) -> Dict[str, Any]:
    """The settings in an old core adsb.json ({"url": ...}) or this mod's
    export ({"format": "odysseus-adsb/1", "settings": {...}}). A bare URL
    works too. Raises ValueError."""
    text = (text or "").strip()
    if not text:
        raise ValueError("Paste the contents of adsb.json, an export from this mod, or the address")
    if len(text) > 64 * 1024:
        raise ValueError("That is too large to be adsb.json")
    if text.startswith(("http://", "https://")):
        return {"url": receiver.normalize_url(text)}
    try:
        data = json.loads(text)
    except ValueError:
        raise ValueError("That is not JSON like {\"url\": \"https://...\"}")
    if not isinstance(data, dict):
        raise ValueError("Expected a JSON object like {\"url\": \"https://...\"}")
    if isinstance(data.get("settings"), dict):
        data = data["settings"]
    if not isinstance(data.get("url", ""), str):
        raise ValueError("`url` has to be text")
    values = {k: data[k] for k in ("url", "feeder_url", "show_network_links") if k in data}
    if "url" not in values:
        raise ValueError("There is no `url` in it")
    # Only what the file holds, checked: the rest of the settings stay as they are.
    checked = clean(values)
    return {k: checked[k] for k in values}


def import_json(core, text: str, source: str = "pasted") -> Dict[str, Any]:
    values = parse_import(text)
    current = load(core)
    current.update(values)
    current["imported_from"] = source[:200]
    out = clean(current)
    core.save_settings(out)
    receiver.clear_cache()
    return out


def export(s: Dict[str, Any]) -> Dict[str, Any]:
    """The settings in a file that imports here again. `url` is also at the
    top, so the file reads like the core's old adsb.json too."""
    keep = {k: s.get(k, DEFAULTS[k]) for k in DEFAULTS}
    return {"format": EXPORT_FORMAT, "url": keep["url"], "settings": keep}


def legacy_file(data_dir: Optional[str] = None) -> Optional[str]:
    """The core's old <data dir>/adsb.json, when this mod's data folder is the
    usual <data dir>/mods/<id>/data and the file is there."""
    data_dir = os.path.abspath(data_dir or os.environ.get("MOD_DATA_DIR") or "")
    if not data_dir or os.path.basename(data_dir) != "data":
        return None
    mods = os.path.dirname(os.path.dirname(data_dir))
    if os.path.basename(mods) != "mods":
        return None
    p = os.path.join(os.path.dirname(mods), "adsb.json")
    return p if os.path.isfile(p) else None


def read_legacy(data_dir: Optional[str] = None) -> Tuple[Optional[str], str]:
    """(path, receiver address) from the core's old file, or (None, "")."""
    p = legacy_file(data_dir)
    if not p:
        return None, ""
    try:
        if os.path.getsize(p) > 64 * 1024:
            return p, ""
        with open(p, "r", encoding="utf-8") as f:
            return p, parse_import(f.read())["url"]
    except (OSError, ValueError):
        return p, ""
