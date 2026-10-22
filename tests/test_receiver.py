"""The receiver logic (adsb_mod/receiver.py) and the settings and migration
(adsb_mod/settings.py, the import/export CLI), without a core."""
import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from adsb_mod import receiver, settings as cfg  # noqa: E402
from tests.receiver_fixtures import AIRCRAFT, STATS, FakeReceiver  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_cache():
    receiver.clear_cache()
    yield
    receiver.clear_cache()


# ── The address ─────────────────────────────────────────────────────────

def test_the_address_is_normalized_to_the_maps_base():
    assert receiver.normalize_url(" https://pi.ts.net ") == "https://pi.ts.net/"
    assert receiver.normalize_url("http://100.1.2.3:8080/tar1090") == "http://100.1.2.3:8080/tar1090/"
    assert receiver.normalize_url("https://pi.ts.net/data/aircraft.json") == "https://pi.ts.net/"
    assert receiver.normalize_url("") == "" and receiver.normalize_url(None) == ""


@pytest.mark.parametrize("bad", ["ftp://pi/", "javascript:alert(1)", "pi.ts.net", "https://pi/?x=1",
                                 "https://pi/#map", "https://u:p@pi/", "https:///", "https://pi/\"><script>",
                                 "https://pi/a b", 5])
def test_odd_addresses_are_refused(bad):
    with pytest.raises(ValueError):
        receiver.normalize_url(bad)


def test_the_feeder_page_is_guessed_from_an_adsb_im_map():
    assert receiver.feeder_page("http://adsb-feeder:8080/") == "http://adsb-feeder/"
    assert receiver.feeder_page("https://pi.ts.net/") == ""
    assert receiver.feeder_page("") == ""


# ── The summary ─────────────────────────────────────────────────────────

def test_the_summary_counts_what_the_receiver_hears():
    s = receiver.summarize(STATS, AIRCRAFT, now=5.0)
    assert s["now"] == {"aircraft": 3, "with_position": 1, "messages_per_s": 25.0}
    assert s["total"]["messages"] == 2758242 and s["total"]["positions"] == 308185
    assert s["total"]["max_range_nm"] == 51.2 and s["last1min"]["max_range_nm"] == 50.0
    assert s["last1min"]["decoded"] == 1490 and s["last1min"]["peak_dbfs"] == -2.9
    assert s["gain_db"] == 43.9
    first = s["aircraft"][0]
    assert first["flight"] == "PDT5995" and first["type"] == "E145" and first["has_position"]
    assert [p["hex"] for p in s["aircraft"]] == ["abc123", "def456", "a1b2c3"]
    assert s["aircraft"][1]["alt"] == "ground" and not s["aircraft"][1]["has_position"]


def test_junk_from_the_receiver_does_not_break_the_summary():
    s = receiver.summarize({"last1min": "x", "total": None}, {"aircraft": "nope"}, now=1.0)
    assert s["now"]["aircraft"] == 0 and s["aircraft"] == []
    assert receiver.summarize([], [], now=1.0)["now"]["aircraft"] == 0


def test_the_agent_text():
    s = dict(configured=True, ok=True, url="https://pi/", **receiver.summarize(STATS, AIRCRAFT))
    text = receiver.describe(s)
    assert "is receiving" in text and "3 (1 with a position)" in text and "25.0 messages/s" in text
    assert "51.2 NM" in text and "gain 43.9 dB" in text
    planes = receiver.describe_aircraft(s["aircraft"])
    assert planes.splitlines()[0].startswith("- PDT5995, E145, 21,000 ft, 380 kt, position")
    assert "on the ground" in planes
    assert "not set up" in receiver.describe({"configured": False}) or "No ADS-B" in receiver.describe(
        {"configured": False})
    assert "not answering: boom" in receiver.describe({"configured": True, "ok": False, "url": "u",
                                                       "error": "boom"})


# ── Fetching (a real HTTP receiver on loopback) ─────────────────────────

def test_status_fetches_both_files_and_caches():
    rx = FakeReceiver()
    try:
        s = receiver.fetch_status(rx.url)
        assert s["ok"] and s["configured"] and s["url"] == rx.url and s["now"]["aircraft"] == 3
        assert rx.calls == ["/data/stats.json", "/data/aircraft.json"]
        receiver.fetch_status(rx.url)
        assert len(rx.calls) == 2, "served from the cache"
    finally:
        rx.close()


def test_an_unreachable_receiver_is_a_status_not_an_error():
    assert receiver.fetch_status("") == {"configured": False}
    rx = FakeReceiver(status=502)
    try:
        s = receiver.fetch_status(rx.url)
        assert s == {"configured": True, "url": rx.url, "ok": False, "error": "The receiver answered HTTP 502"}
    finally:
        rx.close()
    receiver.clear_cache()
    s = receiver.fetch_status("http://127.0.0.1:9/")
    assert not s["ok"] and s["error"].startswith("Could not reach the receiver")


def test_redirects_are_not_followed():
    rx = FakeReceiver(redirect="http://169.254.169.254/latest/meta-data/")
    try:
        s = receiver.fetch_status(rx.url)
        assert not s["ok"] and "HTTP 302" in s["error"]
    finally:
        rx.close()


# ── Settings and migration ──────────────────────────────────────────────

class MemCore:
    def __init__(self, values=None):
        self.values = dict(values or {})

    def settings(self):
        return dict(self.values)

    def save_settings(self, v):
        self.values = dict(v)


def test_settings_are_checked_and_links_follow_them():
    core = MemCore()
    assert cfg.load(core) == cfg.DEFAULTS
    s = cfg.save(core, {"url": "http://adsb-feeder:8080", "junk": 1})
    assert s["url"] == "http://adsb-feeder:8080/" and "junk" not in core.values
    labels = [k["label"] for k in cfg.links(s)]
    assert labels[:2] == ["Open the map in a new tab", "Feeder settings (adsb.im)"]
    assert "Flys Down" in labels and any("adsb.lol" in k for k in labels)
    assert cfg.links(cfg.save(core, {"show_network_links": False}))[-1]["label"] == "Feeder settings (adsb.im)"
    with pytest.raises(ValueError):
        cfg.save(core, {"url": "ftp://x/"})
    assert cfg.load(MemCore({"url": "javascript:alert(1)"}))["url"] == "", "a broken saved value counts as unset"


@pytest.mark.parametrize("text, url", [
    ('{"url": "https://adsb-feeder.example.ts.net"}', "https://adsb-feeder.example.ts.net/"),    # core adsb.json
    ('{"format": "odysseus-adsb/1", "url": "x", "settings": {"url": "http://pi:8080/"}}', "http://pi:8080/"),
    ("http://adsb-feeder:8080/data/aircraft.json", "http://adsb-feeder:8080/"),
    ('{"url": ""}', ""),
])
def test_import_reads_the_old_file_an_export_or_an_address(text, url):
    core = MemCore({"show_network_links": False})
    out = cfg.import_json(core, text)
    assert out["url"] == url and out["show_network_links"] is False and out["imported_from"] == "pasted"


@pytest.mark.parametrize("text", ["", "nope", "[1]", '{"other": 1}', '{"url": 5}', '{"url": "ftp://x"}'])
def test_bad_imports_are_refused(text):
    with pytest.raises(ValueError):
        cfg.parse_import(text)


def test_export_imports_again_and_reads_like_the_old_file():
    s = cfg.clean({"url": "http://pi:8080/", "show_network_links": False})
    exp = cfg.export(s)
    assert exp["url"] == "http://pi:8080/" and exp["format"] == cfg.EXPORT_FORMAT
    assert cfg.parse_import(json.dumps(exp))["show_network_links"] is False


def test_the_old_core_file_is_found_next_to_the_mods_folder(tmp_path):
    data = tmp_path / "mods" / "adsb" / "data"
    data.mkdir(parents=True)
    assert cfg.read_legacy(str(data)) == (None, "")
    (tmp_path / "adsb.json").write_text('{"url": "https://pi.ts.net"}')
    assert cfg.read_legacy(str(data)) == (str(tmp_path / "adsb.json"), "https://pi.ts.net/")
    (tmp_path / "adsb.json").write_text('{"url": "https://pi.ts.net; script-src *"}')
    assert cfg.read_legacy(str(data))[1] == ""
    other = tmp_path / "elsewhere" / "data"
    other.mkdir(parents=True)
    assert cfg.read_legacy(str(other)) == (None, "")


def _cli(*args):
    return subprocess.run([sys.executable, "-m", "adsb_mod", *args], cwd=ROOT, capture_output=True, text=True,
                          timeout=30)


def test_cli_import_and_export(tmp_path):
    old = tmp_path / "adsb.json"
    old.write_text('{"url": "https://adsb-feeder.example.ts.net"}')
    into = tmp_path / "mods" / "adsb" / "settings.json"
    r = _cli("import", str(old), "--into", str(into), "--dry-run")
    assert r.returncode == 0 and not into.exists() and "adsb-feeder" in r.stdout
    r = _cli("import", str(old), "--into", str(into))
    assert r.returncode == 0, r.stderr
    saved = json.loads(into.read_text())
    assert saved["url"] == "https://adsb-feeder.example.ts.net/" and saved["imported_from"] == "adsb.json"
    assert oct(into.stat().st_mode & 0o777) == "0o600"
    assert json.loads(old.read_text()) == {"url": "https://adsb-feeder.example.ts.net"}, "the old file is untouched"
    r = _cli("export", str(into))
    assert r.returncode == 0 and json.loads(r.stdout)["url"] == "https://adsb-feeder.example.ts.net/"
    bad = tmp_path / "bad.json"
    bad.write_text('{"url": "ftp://x"}')
    assert _cli("import", str(bad), "--into", str(into)).returncode == 1
