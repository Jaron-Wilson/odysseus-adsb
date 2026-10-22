"""The receiver: its address, and a status summary built from its own JSON.

An adsb.im feeder (or any tar1090/readsb receiver) serves data/stats.json and
data/aircraft.json next to its tar1090 map. The mod's server fetches them
(tar1090 sends no CORS header on stats.json, and the mod page runs in a
sandbox with an opaque origin), turns them into the summary the page, the
home card and the agent tools show, and keeps it for CACHE_S seconds so an
open tab, a card and a tool call do not each poll the Pi.

Ported from the Odysseus core (src/adsb.py before the ADS-B page became this
mod). Standard library only.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, Optional
from urllib.parse import urlsplit

FETCH_TIMEOUT_S = 4.0
CACHE_S = 5.0
MAX_AIRCRAFT = 60
M_PER_NM = 1852.0

_lock = threading.Lock()
_cache: Dict[str, Any] = {"at": 0.0, "url": None, "status": None}


# ── The address ─────────────────────────────────────────────────────────────

def normalize_url(url: Any) -> str:
    """The receiver's base address, always ending in "/" (tar1090's map).

    "" stays "" (no receiver). A pasted .../data/aircraft.json keeps the
    map's base. Raises ValueError for anything that is not a plain http(s)
    address."""
    if url is None:
        return ""
    if not isinstance(url, str):
        raise ValueError("The receiver address has to be text")
    url = url.strip()
    if not url:
        return ""
    if len(url) > 500:
        raise ValueError("The receiver address is too long")
    if any(c in url for c in " \"'<>\\\r\n\t"):
        raise ValueError("The receiver address has characters a URL can't have")
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("The receiver address has to be an http:// or https:// URL")
    if parts.query or parts.fragment:
        raise ValueError("The receiver address can't have a query or a fragment")
    if parts.username or parts.password:
        raise ValueError("The receiver address can't carry a username or password")
    path = parts.path or "/"
    # Accept a pasted .../data/aircraft.json and keep the map's base.
    if path.endswith(".json"):
        path = path.rsplit("/data/", 1)[0] + "/" if "/data/" in path else "/"
    if not path.endswith("/"):
        path += "/"
    return f"{parts.scheme}://{parts.netloc}{path}"


def feeder_page(url: str) -> str:
    """Where the adsb.im feeder's own web page probably is, from the map's
    address: an adsb.im image serves tar1090 on :8080 and its setup page on
    the default port of the same host. "" when that can't be told."""
    if not url:
        return ""
    parts = urlsplit(url)
    if parts.port == 8080 and parts.hostname:
        host = parts.hostname if ":" not in parts.hostname else f"[{parts.hostname}]"
        return f"{parts.scheme}://{host}/"
    return ""


# ── The summary ─────────────────────────────────────────────────────────────

def _num(v) -> Optional[float]:
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _period(stats: Dict, key: str) -> Dict:
    p = stats.get(key) or {}
    if not isinstance(p, dict):
        p = {}
    local = p.get("local") or {}
    if not isinstance(local, dict):
        local = {}
    start, end = _num(p.get("start")), _num(p.get("end"))
    accepted = local.get("accepted") or []
    return {
        "messages": _num(p.get("messages")),
        "positions": _num(p.get("position_count_total")),
        "seconds": (end - start) if start is not None and end is not None else None,
        "decoded": sum(a for a in accepted if _num(a) is not None) if isinstance(accepted, list) else None,
        "peak_dbfs": _num(local.get("peak_signal")),
        "signal_dbfs": _num(local.get("signal")),
        "noise_dbfs": _num(local.get("noise")),
        "max_range_nm": round(p["max_distance"] / M_PER_NM, 1) if _num(p.get("max_distance")) else None,
        "start": start,
    }


def summarize(stats: Dict, aircraft_json: Dict, now: Optional[float] = None) -> Dict:
    """The receiver's two files -> what the page shows. Pure, for the tests.

    Only aircraft with a position heard in the last minute count as "with a
    position": readsb keeps a contact in aircraft.json for a while after its
    last report."""
    now = now if now is not None else time.time()
    stats = stats if isinstance(stats, dict) else {}
    aircraft_json = aircraft_json if isinstance(aircraft_json, dict) else {}
    rows = aircraft_json.get("aircraft") or []
    planes = []
    for a in rows if isinstance(rows, list) else []:
        if not isinstance(a, dict) or not a.get("hex"):
            continue
        seen_pos = _num(a.get("seen_pos"))
        alt = a.get("alt_baro")
        planes.append({
            "hex": str(a["hex"])[:16],
            "flight": str(a.get("flight") or "").strip()[:16] or None,
            "registration": str(a.get("r"))[:16] if a.get("r") else None,
            "type": str(a.get("t"))[:16] if a.get("t") else None,
            "alt": "ground" if alt == "ground" else _num(alt),
            "gs": _num(a.get("gs")),
            "track": _num(a.get("track")),
            "rssi": _num(a.get("rssi")),
            "seen": _num(a.get("seen")),
            "seen_pos": seen_pos,
            "has_position": "lat" in a and seen_pos is not None and seen_pos <= 60,
            "messages": _num(a.get("messages")),
        })
    planes.sort(key=lambda p: (not p["has_position"], p["seen"] if p["seen"] is not None else 1e9))
    last1 = _period(stats, "last1min")
    return {
        "now": {
            "aircraft": len(planes),
            "with_position": sum(1 for p in planes if p["has_position"]),
            "messages_per_s": round(last1["messages"] / 60, 1) if last1["messages"] is not None else None,
        },
        "last1min": last1,
        "last15min": _period(stats, "last15min"),
        "total": _period(stats, "total"),
        "gain_db": _num(stats.get("gain_db")),
        "aircraft": planes[:MAX_AIRCRAFT],
        "receiver_time": _num(aircraft_json.get("now")),
        "fetched_at": now,
    }


# ── Fetching ────────────────────────────────────────────────────────────────

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):   # a redirect is an answer, never followed
        return None


# No proxies from the environment and no redirects: the receiver is asked directly.
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())


class FetchError(Exception):
    pass


def _get_json(url: str) -> Dict:
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "odysseus-adsb"})
    try:
        with _opener.open(req, timeout=FETCH_TIMEOUT_S) as r:
            raw = r.read(8 * 1024 * 1024)
    except urllib.error.HTTPError as e:
        raise FetchError(f"The receiver answered HTTP {e.code}")
    except (TimeoutError, OSError) as e:
        reason = getattr(e, "reason", e)
        if isinstance(reason, TimeoutError) or "timed out" in str(reason):
            raise FetchError(f"The receiver did not answer within {FETCH_TIMEOUT_S:g} s")
        raise FetchError(f"Could not reach the receiver ({type(reason).__name__})")
    try:
        data = json.loads(raw)
    except ValueError:
        raise FetchError("The receiver did not answer with JSON")
    return data if isinstance(data, dict) else {}


def clear_cache() -> None:
    with _lock:
        _cache.update(at=0.0, url=None, status=None)


def fetch_status(url: str, get: Callable[[str], Dict] = _get_json) -> Dict:
    """The receiver's status, cached for CACHE_S. Never raises: an unreachable
    receiver is a status to show, not an error."""
    if not url:
        return {"configured": False}
    with _lock:
        if _cache["url"] == url and _cache["status"] is not None and time.time() - _cache["at"] < CACHE_S:
            return _cache["status"]
    out: Dict[str, Any] = {"configured": True, "url": url}
    try:
        stats = get(url + "data/stats.json")
        planes = get(url + "data/aircraft.json")
        out.update(ok=True, **summarize(stats, planes))
    except FetchError as e:
        out.update(ok=False, error=str(e))
    except Exception as e:   # anything odd from the receiver is still just a status
        out.update(ok=False, error=f"Could not read the receiver ({type(e).__name__})")
    with _lock:
        _cache.update(at=time.time(), url=url, status=out)
    return out


# ── Text for the agent ──────────────────────────────────────────────────────

def _fmt(n, digits=0) -> str:
    if n is None:
        return "-"
    return f"{n:,.{digits}f}"


def describe(s: Dict) -> str:
    """The status as a few lines for the model."""
    if not s.get("configured"):
        return "No ADS-B receiver is set up yet. Set its address in the ADS-B receiver mod's settings."
    if not s.get("ok"):
        return f"The ADS-B receiver at {s.get('url')} is not answering: {s.get('error')}."
    now = s.get("now") or {}
    m15 = s.get("last15min") or {}
    m1 = s.get("last1min") or {}
    t = s.get("total") or {}
    lines = [
        f"The ADS-B receiver at {s.get('url')} is receiving.",
        f"Aircraft now: {_fmt(now.get('aircraft'))} ({_fmt(now.get('with_position'))} with a position), "
        f"{_fmt(now.get('messages_per_s'), 1)} messages/s.",
    ]
    if m15.get("max_range_nm") is not None:
        lines.append(f"Range in the last 15 min: {_fmt(m15['max_range_nm'], 1)} NM"
                     + (f" (best {_fmt(t['max_range_nm'], 1)} NM)." if t.get("max_range_nm") else "."))
    if m1.get("signal_dbfs") is not None:
        lines.append(f"Signal {_fmt(m1['signal_dbfs'], 1)} dBFS, noise {_fmt(m1.get('noise_dbfs'), 1)} dBFS"
                     + (f", gain {_fmt(s['gain_db'], 1)} dB." if s.get("gain_db") is not None else "."))
    if t.get("messages") is not None:
        lines.append(f"Messages since the decoder started: {_fmt(t['messages'])}.")
    return "\n".join(lines)


def describe_aircraft(planes, limit: int = 25) -> str:
    if not planes:
        return "The receiver hears no aircraft right now."
    rows = []
    for p in planes[:limit]:
        name = p.get("flight") or p.get("registration") or p.get("hex")
        alt = "on the ground" if p.get("alt") == "ground" else (
            f"{_fmt(p['alt'])} ft" if p.get("alt") is not None else "altitude unknown")
        bits = [name, p.get("type") or "", alt]
        if p.get("gs") is not None:
            bits.append(f"{_fmt(p['gs'])} kt")
        bits.append("position" if p.get("has_position") else "no position")
        if p.get("seen") is not None:
            bits.append(f"seen {_fmt(p['seen'])} s ago")
        rows.append("- " + ", ".join(b for b in bits if b))
    more = f"\n(+{len(planes) - limit} more)" if len(planes) > limit else ""
    return "\n".join(rows) + more
