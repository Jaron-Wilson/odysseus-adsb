"""The mod under the stand-in core (odysseus_mod.devcore), as Odysseus runs
it: through the proxy with a signed identity, its settings in the core, the
tools, the card, and the import of the core's old adsb.json.
Run from the mod folder: python -m pytest -q"""
import json
import os
import sys
import urllib.error
import urllib.request

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from odysseus_mod.devcore import DevCore  # noqa: E402
from tests.receiver_fixtures import FakeReceiver  # noqa: E402


def get(url, method="GET", body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)


@pytest.fixture(scope="module")
def rx():
    r = FakeReceiver()
    yield r
    r.close()


@pytest.fixture(scope="module")
def core(tmp_path_factory, rx):
    # The state folder is laid out like an Odysseus data dir (<data>/mods/adsb),
    # with the core's old adsb.json next to mods/, so the import offer shows.
    data = tmp_path_factory.mktemp("odysseus-data")
    (data / "adsb.json").write_text(json.dumps({"url": rx.url}))
    state = data / "mods" / "adsb"
    state.mkdir(parents=True)
    c = DevCore(ROOT, port=0, user="alice", state_dir=str(state))
    c.serve()
    c.start_mod()
    yield c
    c.close()


def mod(core, path="/", **kw):
    return get(f"{core.url}/mods/{core.mod_id}{path}", **kw)


def test_health_and_unsigned_requests(core):
    assert get(f"http://127.0.0.1:{core.mod_port}/health")[0] == 200
    assert get(f"http://127.0.0.1:{core.mod_port}/")[0] == 401
    assert get(f"http://127.0.0.1:{core.mod_port}/api/status")[0] == 401


def test_the_manifest(core):
    m = core.manifest
    assert m["id"] == "adsb" and m["permissions"] == ["network.lan"]
    tab = m["ui"]["tabs"][0]
    assert tab["replaces"] == "adsb" and tab["section"] == "system" and tab["icon"] == "adsb"
    assert m["runtime"]["start"] == "lazy" and m["runtime"]["idle_stop_minutes"] == 10
    assert set(m["tags"]) == {"radio", "hardware", "monitoring"}


def test_unconfigured_first(core):
    code, body, _ = mod(core, "/api/status")
    s = json.loads(body)
    assert code == 200 and s["configured"] is False
    assert [k["label"] for k in s["links"]] == ["adsb.lol map", "adsb.fi map", "Flys Down"]
    code, body, _ = mod(core, "/")
    assert code == 200 and b"api/status" in body and b"ADS-B receiver" in body
    assert b"No receiver set" in mod(core, "/cards/status")[1]


def test_the_old_core_setting_is_offered_and_imported(core, rx):
    d = json.loads(mod(core, "/api/settings")[1])
    assert d["legacy"] == {"found": True, "url": rx.url} and d["settings"]["url"] == ""
    code, body, _ = mod(core, "/api/import", method="POST", body={"legacy": True})
    assert code == 200 and json.loads(body)["settings"]["url"] == rx.url
    saved = json.loads(open(os.path.join(core.state_dir, "settings.json")).read())
    assert saved["url"] == rx.url and saved["imported_from"] == "odysseus adsb.json"


def test_status_page_data_card_and_links(core, rx):
    s = json.loads(mod(core, "/api/status")[1])
    assert s["ok"] and s["now"] == {"aircraft": 3, "with_position": 1, "messages_per_s": 25.0}
    assert s["links"][0] == {"label": "Open the map in a new tab", "url": rx.url, "note": "tar1090 on the receiver"}
    card = mod(core, "/cards/status")[1]
    assert b">3</span> aircraft" in card and b"25.0 messages/s" in card


def test_settings_save_and_refuse(core, rx):
    assert mod(core, "/settings")[0] == 200
    code, body, _ = mod(core, "/api/settings", method="PUT", body={"url": "ftp://pi/"})
    assert code == 400 and "http" in json.loads(body)["error"]
    code, body, _ = mod(core, "/api/settings", method="PUT",
                        body={"url": rx.url + "data/aircraft.json", "show_network_links": False})
    assert code == 200 and json.loads(body)["settings"] == {
        "url": rx.url, "feeder_url": "", "show_network_links": False, "imported_from": "odysseus adsb.json"}
    s = json.loads(mod(core, "/api/status")[1])
    assert [k["label"] for k in s["links"]] == ["Open the map in a new tab"]


def test_paste_import_and_export(core, rx):
    code, body, _ = mod(core, "/api/import", method="POST", body={"text": "not json"})
    assert code == 400
    code, body, headers = mod(core, "/api/export")
    assert code == 200 and "attachment" in headers.get("Content-Disposition", "")
    exp = json.loads(body)
    assert exp["url"] == rx.url
    code, body, _ = mod(core, "/api/import", method="POST", body={"text": json.dumps(exp)})
    assert code == 200 and json.loads(body)["settings"]["imported_from"] == "pasted"


def test_tools(core):
    names = sorted(t["function"]["name"] for t in core.list_tools())
    assert names == ["adsb_aircraft", "adsb_status"]
    assert all(t["x-odysseus"]["read_only"] for t in core.list_tools())
    out = core.call_tool("adsb_status", {})
    assert "is receiving" in out["output"] and out["data"]["now"]["aircraft"] == 3
    assert "/#mod/adsb/receiver" in out["output"]
    out = core.call_tool("adsb_aircraft", {"with_position": True})
    assert out["output"].startswith("1 aircraft") and "PDT5995" in out["output"]
    out = core.call_tool("adsb_aircraft", {"query": "e145"})
    assert [p["hex"] for p in out["data"]["aircraft"]] == ["abc123"]
    out = core.call_tool("adsb_aircraft", {"limit": "lots"})
    assert out["output"].startswith("3 aircraft")


def test_manifest_has_a_tutorial_written_for_this_mod(core):
    from odysseus_mod.devcore import tutorial_problems
    assert tutorial_problems(core.manifest, ROOT) == []
    tut = core.manifest["tutorial"]
    assert 3 <= len(tut["steps"]) <= 8
    text = " ".join(s["title"] + " " + s["body"] for s in tut["steps"]).lower()
    for feature in ("map", "settings", "import", "flys down", "adsb.im", "agent"):
        assert feature in text, feature
    assert any((s.get("action") or {}).get("settings") for s in tut["steps"])
