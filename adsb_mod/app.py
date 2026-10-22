"""ADS-B Receiver: an Odysseus mod for an adsb.im (tar1090/readsb) feeder.

The tab shows what the receiver hears now (aircraft, positions, message
rate, range, signal, and the aircraft list, refreshed every 5 seconds) beside
the receiver's own tar1090 map, with links to the feeder's setup page, the
aggregators it feeds and Flys Down. A home card has the headline numbers, and
two agent tools answer "is my receiver working?" and "what planes are
overhead?".

Run it without Odysseus:   python -m odysseus_mod.devcore
Run it on its own:         MOD_PASSWORD=... python -m adsb_mod
Migrate the core's setting: python -m adsb_mod import <data dir>/adsb.json --into <settings.json>
"""
from __future__ import annotations

import json

from odysseus_mod import CoreClient, ModApp, Response, html, json_response

from adsb_mod import receiver, settings as cfg

MOD_ID = "adsb"
app = ModApp(mod_id=MOD_ID)

STYLE = """
:root{color-scheme:dark}
*{box-sizing:border-box}
body{font:13.5px system-ui,-apple-system,"Segoe UI",sans-serif;margin:0;background:#141414;color:#ddd}
a{color:#d97a4a;text-decoration:none}a:hover{text-decoration:underline}
button,input{font:inherit;color:inherit}
button{padding:5px 12px;border-radius:7px;border:1px solid #3a3a3a;background:#222;cursor:pointer}
button:hover{background:#2a2a2a}
input[type=url],input[type=text],textarea{width:100%;padding:6px 8px;border-radius:7px;border:1px solid #3a3a3a;
  background:#1b1b1b;font-size:13px}
.muted{opacity:.6}.small{font-size:12px}
"""

PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>ADS-B receiver</title>
<style>__STYLE__
html,body{height:100%}
.wrap{display:flex;height:100%}
.side{width:360px;flex:0 0 360px;display:flex;flex-direction:column;min-height:0;border-right:1px solid #262626}
.state{padding:12px 14px 8px;opacity:.85}
.state::before{content:"";display:inline-block;width:8px;height:8px;border-radius:50%;background:#777;margin-right:7px}
.state.good::before{background:#3fb950}.state.bad{opacity:1}.state.bad::before{background:#e5534b}
.tiles{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:6px;padding:0 12px 10px}
.tiles:empty{display:none}
.tile{padding:8px 10px;border-radius:9px;background:#1c1c1c;border:1px solid #262626;min-width:0}
.tile .v{font-size:17px;font-weight:600;font-variant-numeric:tabular-nums}
.tile .l{font-size:11.5px;opacity:.65}.tile .s{font-size:11px;opacity:.5;margin-top:1px}
.list{flex:1;min-height:0;overflow:auto;padding:0 6px}
table{width:100%;border-collapse:collapse;font-size:12.5px;font-variant-numeric:tabular-nums}
th{position:sticky;top:0;background:#141414;text-align:left;font-weight:500;opacity:.7;padding:4px 6px}
td{padding:4px 6px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:110px}
.n{text-align:right}tr.nopos td{opacity:.5}
.empty{padding:14px 10px;opacity:.6}
.links{padding:10px 14px 12px;border-top:1px solid #262626;display:flex;flex-direction:column;gap:4px;font-size:12.5px}
.links a{width:fit-content}
.map{flex:1;min-width:0;min-height:0;display:flex;flex-direction:column}
.map iframe{flex:1;width:100%;border:0;background:#1b1b1b}
.map .empty{margin:auto;max-width:420px;text-align:center}
.note{padding:6px 12px;font-size:12px;background:#2a2118;border-bottom:1px solid #3a2c1d}
@media (max-width:768px){.wrap{flex-direction:column;height:auto}.side{width:100%;flex:none;border-right:0}
  .list{max-height:40vh}.map{height:70vh;flex:none;border-top:1px solid #262626}}
</style></head><body>
<div class="wrap">
  <div class="side">
    <div class="state" id="state">Loading...</div>
    <div class="tiles" id="tiles"></div>
    <div class="list" id="list"></div>
    <div class="links" id="links"></div>
  </div>
  <div class="map" id="map"><div class="empty">Loading...</div></div>
</div>
<script>
// Relative URLs only: the page is served by Odysseus under /mods/adsb/~<token>/.
const POLL_MS = 5000;
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => (
  {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt = (n, d = 0) => (n === null || n === undefined) ? '-'
  : Number(n).toLocaleString([], {maximumFractionDigits: d, minimumFractionDigits: d});
const span = (s) => { if (!s) return ''; const h = Math.floor(s / 3600);
  return h >= 48 ? `${Math.floor(h / 24)} days` : h >= 1 ? `${h} h` : `${Math.max(1, Math.round(s / 60))} min`; };
const tile = (l, v, s = '') => `<div class="tile"><div class="v">${esc(v)}</div><div class="l">${esc(l)}</div>${
  s ? `<div class="s">${esc(s)}</div>` : ''}</div>`;
let mapUrl = null;

function paintLinks(links) {
  $('links').innerHTML = (links || []).map((k) =>
    `<a href="${esc(k.url)}" target="_blank" rel="noopener noreferrer" title="${esc(k.note || '')}">${esc(k.label)}</a>`
  ).join('') + '<a href="settings">Settings</a>';
}

function paintMap(url) {
  if (url === mapUrl) return;
  mapUrl = url;
  const box = $('map');
  if (!url) {
    box.innerHTML = '<div class="empty">The receiver\\'s map shows here once its address is set. '
      + '<a href="settings">Open settings</a></div>';
    return;
  }
  // An https Odysseus can't show an http map inside it (mixed content).
  const mixed = location.protocol === 'https:' && url.startsWith('http://');
  box.innerHTML = (mixed ? `<div class="note">Your browser blocks an http:// map inside this https page.
    <a href="${esc(url)}" target="_blank" rel="noopener noreferrer">Open it in a new tab</a>, or serve the
    receiver over https (for example with Tailscale Serve on the Pi).</div>` : '');
  const f = document.createElement('iframe');
  f.title = 'Receiver map';
  f.referrerPolicy = 'no-referrer';
  f.src = url;
  box.appendChild(f);
}

function paint(s) {
  const head = $('state');
  paintLinks(s.links);
  paintMap(s.configured ? s.url : '');
  if (!s.configured) {
    head.className = 'state';
    head.innerHTML = 'No receiver set. <a href="settings">Add its address</a>.';
    $('tiles').innerHTML = ''; $('list').innerHTML = '';
    return;
  }
  if (!s.ok) {
    head.className = 'state bad';
    head.textContent = s.error || 'The receiver is not answering.';
    return;  // the last numbers stay on screen; the line above says they are stale
  }
  head.className = 'state good';
  head.textContent = 'Receiving' + (s.gain_db !== null && s.gain_db !== undefined ? ` · gain ${fmt(s.gain_db, 1)} dB` : '');
  const t = s.total || {}, m1 = s.last1min || {}, m15 = s.last15min || {};
  $('tiles').innerHTML = [
    tile('aircraft now', fmt(s.now?.aircraft), `${fmt(s.now?.with_position)} with a position`),
    tile('messages / s', fmt(s.now?.messages_per_s, 1)),
    tile('range, 15 min', m15.max_range_nm ? `${fmt(m15.max_range_nm, 1)} NM` : '-',
      t.max_range_nm ? `best ${fmt(t.max_range_nm, 1)} NM` : ''),
    tile('signal', m1.signal_dbfs !== null && m1.signal_dbfs !== undefined ? `${fmt(m1.signal_dbfs, 1)} dBFS` : '-',
      m1.peak_dbfs !== null && m1.peak_dbfs !== undefined ? `peak ${fmt(m1.peak_dbfs, 1)}` : ''),
    tile('messages total', fmt(t.messages), t.seconds ? `in ${span(t.seconds)}` : ''),
    tile('positions total', fmt(t.positions)),
  ].join('');
  const planes = s.aircraft || [];
  $('list').innerHTML = planes.length ? `<table><thead><tr><th>Flight</th><th>Type</th><th class="n">Alt ft</th>
    <th class="n">Speed kt</th><th class="n">Seen</th></tr></thead><tbody>${planes.map((p) => `
    <tr class="${p.has_position ? '' : 'nopos'}" title="${esc(p.hex)}${p.registration ? ' · ' + esc(p.registration) : ''}">
      <td>${esc(p.flight || p.registration || p.hex)}</td><td>${esc(p.type || '')}</td>
      <td class="n">${p.alt === 'ground' ? 'ground' : fmt(p.alt)}</td><td class="n">${fmt(p.gs)}</td>
      <td class="n">${p.seen !== null && p.seen !== undefined ? fmt(p.seen) + ' s' : '-'}</td></tr>`).join('')}
    </tbody></table>` : '<div class="empty">Nothing heard right now.</div>';
}

async function refresh() {
  if (document.visibilityState !== 'visible') return;
  try {
    const r = await fetch('api/status');
    const d = await r.json();
    if (!r.ok) throw new Error(d.error || `HTTP ${r.status}`);
    paint(d);
  } catch (e) {
    $('state').className = 'state bad';
    $('state').textContent = e.message;
  }
}
refresh();
setInterval(refresh, POLL_MS);
document.addEventListener('visibilitychange', refresh);
</script></body></html>""".replace("__STYLE__", STYLE)

SETTINGS = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>ADS-B receiver settings</title>
<style>__STYLE__
body{padding:20px;max-width:640px}
h1{font-size:17px;margin:0 0 14px}h2{font-size:14px;margin:22px 0 8px}
label{display:block;margin:12px 0 4px;opacity:.75}
.row{display:flex;gap:8px;align-items:center;margin-top:12px}
.msg{margin-top:10px;min-height:1.2em}.msg.err{color:#e5534b}.msg.ok{color:#3fb950}
.box{padding:10px 12px;border:1px solid #3a2c1d;background:#2a2118;border-radius:8px;margin:10px 0}
textarea{height:70px;font-family:ui-monospace,monospace;font-size:12px}
</style></head><body>
<h1>ADS-B receiver settings</h1>
<form id="f">
  <label for="url">Receiver address (its tar1090 map)</label>
  <input id="url" type="url" placeholder="http://adsb-feeder:8080/ or https://adsb-feeder.your-tailnet.ts.net/"
    autocomplete="off" spellcheck="false">
  <div class="small muted">An adsb.im feeder serves its map on port 8080. A pasted .../data/aircraft.json works too.</div>
  <label for="feeder">Feeder settings page (optional)</label>
  <input id="feeder" type="url" placeholder="Guessed from the address: the same host without :8080"
    autocomplete="off" spellcheck="false">
  <div class="row"><input id="net" type="checkbox" style="width:auto"> <label for="net" style="margin:0;opacity:1">
    Show links to adsb.lol, adsb.fi and Flys Down</label></div>
  <div class="row"><button type="submit">Save</button> <a href="./">Back to the receiver</a></div>
  <div class="msg" id="msg"></div>
</form>

<h2>Move from the built-in page</h2>
<div id="legacy"></div>
<div class="small muted">Odysseus used to keep the receiver in <code>adsb.json</code> in its data folder.
Paste that file (or an export from here, or just the address) and press Import.</div>
<textarea id="paste" placeholder='{"url": "https://adsb-feeder.your-tailnet.ts.net/"}'></textarea>
<div class="row"><button type="button" id="imp">Import</button>
  <a href="api/export" download="odysseus-adsb-settings.json">Export these settings</a></div>
<div class="msg" id="imsg"></div>
<script>
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => (
  {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function say(id, text, ok) { $(id).textContent = text; $(id).className = 'msg ' + (ok ? 'ok' : 'err'); }
function fill(s) { $('url').value = s.url || ''; $('feeder').value = s.feeder_url || '';
  $('net').checked = s.show_network_links !== false; }
async function call(path, method, body) {
  const r = await fetch(path, {method, headers: {'Content-Type': 'application/json'},
    body: body === undefined ? undefined : JSON.stringify(body)});
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(d.error || `HTTP ${r.status}`);
  return d;
}
async function load() {
  const d = await call('api/settings', 'GET');
  fill(d.settings);
  const box = $('legacy');
  if (d.legacy && d.legacy.url && d.legacy.url !== d.settings.url) {
    box.innerHTML = `<div class="box">Found the old Odysseus setting: <code>${esc(d.legacy.url)}</code>
      <button type="button" id="leg">Use it</button></div>`;
    $('leg').onclick = async () => {
      try { const r = await call('api/import', 'POST', {legacy: true}); fill(r.settings);
        box.innerHTML = ''; say('imsg', 'Imported the old setting.', true);
      } catch (e) { say('imsg', e.message, false); }
    };
  } else box.innerHTML = '';
}
$('f').onsubmit = async (ev) => {
  ev.preventDefault();
  try {
    const d = await call('api/settings', 'PUT', {url: $('url').value, feeder_url: $('feeder').value,
      show_network_links: $('net').checked});
    fill(d.settings); say('msg', d.settings.url ? 'Saved.' : 'Saved. No receiver is set.', true);
  } catch (e) { say('msg', e.message, false); }
};
$('imp').onclick = async () => {
  try { const d = await call('api/import', 'POST', {text: $('paste').value}); fill(d.settings);
    $('paste').value = ''; say('imsg', 'Imported: ' + (d.settings.url || 'no address'), true); load();
  } catch (e) { say('imsg', e.message, false); }
};
load().catch((e) => say('msg', e.message, false));
</script></body></html>""".replace("__STYLE__", STYLE)

CARD = """<!doctype html><meta charset="utf-8"><style>__STYLE__
body{background:transparent;margin:10px 12px}.big{font-size:22px;font-weight:600;font-variant-numeric:tabular-nums}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px;background:__DOT__}</style>
<body>__BODY__</body>""".replace("__STYLE__", STYLE)


def _status() -> dict:
    s = cfg.load(app.core)
    out = dict(receiver.fetch_status(s["url"]))
    out["links"] = cfg.links(s)
    return out


@app.get("/")
def home(req):
    return html(PAGE)


@app.get("/api/status")
def api_status(req):
    return json_response(_status())


@app.get("/cards/status")
def card(req):
    s = _status()
    if not s.get("configured"):
        body, dot = "<span class='dot'></span>No receiver set. Open the tab's settings.", "#777"
    elif not s.get("ok"):
        body, dot = f"<span class='dot'></span>{_esc(s.get('error') or 'Not answering')}", "#e5534b"
    else:
        now = s.get("now") or {}
        mps = now.get("messages_per_s")
        body = (f"<span class='dot'></span><span class='big'>{now.get('aircraft', 0)}</span> aircraft, "
                f"{now.get('with_position', 0)} with a position"
                f"<div class='muted small'>{mps if mps is not None else '-'} messages/s</div>")
        dot = "#3fb950"
    return html(CARD.replace("__DOT__", dot).replace("__BODY__", body))


def _esc(s: str) -> str:
    import html as h
    return h.escape(str(s))


@app.get("/settings")
def settings_page(req):
    return html(SETTINGS)


def _settings_view() -> dict:
    s = cfg.load(app.core)
    path, legacy_url = cfg.read_legacy()
    return {"settings": s, "legacy": {"found": bool(path), "url": legacy_url}}


@app.get("/api/settings")
def api_settings(req):
    return json_response(_settings_view())


def _save(req):
    body = req.json()
    if not isinstance(body, dict):
        return json_response({"error": "Send a JSON object"}, 400)
    try:
        cfg.save(app.core, body)
    except ValueError as e:
        return json_response({"error": str(e)}, 400)
    return json_response(_settings_view())


app.route("PUT", "/api/settings")(_save)
app.route("POST", "/api/settings")(_save)


@app.post("/api/import")
def api_import(req):
    body = req.json()
    if not isinstance(body, dict):
        return json_response({"error": "Send a JSON object"}, 400)
    try:
        if body.get("legacy") is True:
            path, url = cfg.read_legacy()
            if not url:
                return json_response({"error": "No old Odysseus setting was found"}, 404)
            cfg.import_json(app.core, json.dumps({"url": url}), source="odysseus adsb.json")
        else:
            cfg.import_json(app.core, str(body.get("text") or ""), source="pasted")
    except ValueError as e:
        return json_response({"error": str(e)}, 400)
    return json_response(_settings_view())


@app.get("/api/export")
def api_export(req):
    data = json.dumps(cfg.export(cfg.load(app.core)), indent=2).encode()
    return Response(data, 200, "application/json", headers=[
        ("Content-Disposition", 'attachment; filename="odysseus-adsb-settings.json"')])


# ── Agent tools ─────────────────────────────────────────────────────────────

@app.tool("adsb_status",
          "The ADS-B receiver's state: whether it is receiving, aircraft heard now and with a position, "
          "messages per second, range, signal and gain.",
          keywords=["receiver", "feeder", "tar1090", "readsb", "ads-b"], read_only=True)
def status_tool(req, args):
    s = _status()
    data = {k: s.get(k) for k in ("configured", "ok", "url", "error", "now", "last15min", "gain_db")}
    return {"output": receiver.describe(s) + f"\nOpen the receiver: {CoreClient.link(MOD_ID, 'receiver')}",
            "data": data}


@app.tool("adsb_aircraft",
          "The aircraft the ADS-B receiver hears right now: flight or registration, type, altitude, speed, "
          "and whether it has a position. Optionally only those with a position, or matching a flight, "
          "registration, hex code or type.",
          {"query": {"type": "string", "description": "Part of a flight number, registration, hex or type"},
           "with_position": {"type": "boolean", "description": "Only aircraft with a recent position"},
           "limit": {"type": "integer", "description": "At most this many (default 25)"}},
          keywords=["planes", "aircraft", "flights", "overhead", "flying"], read_only=True)
def aircraft_tool(req, args):
    s = _status()
    if not s.get("configured") or not s.get("ok"):
        return {"output": receiver.describe(s)}
    planes = list(s.get("aircraft") or [])
    q = str(args.get("query") or "").strip().lower()
    if q:
        planes = [p for p in planes if any(q in str(p.get(k) or "").lower()
                                           for k in ("flight", "registration", "hex", "type"))]
    if args.get("with_position"):
        planes = [p for p in planes if p.get("has_position")]
    try:
        limit = max(1, min(int(args.get("limit") or 25), receiver.MAX_AIRCRAFT))
    except (TypeError, ValueError):
        limit = 25
    head = f"{len(planes)} aircraft" + (f" matching {q!r}" if q else "") + ":\n"
    return {"output": head + receiver.describe_aircraft(planes, limit), "data": {"aircraft": planes[:limit]}}


def main():
    app.run()
