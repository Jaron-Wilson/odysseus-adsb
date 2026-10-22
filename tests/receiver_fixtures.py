"""A receiver's two files, and a tiny fake receiver serving them."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STATS = {
    "gain_db": 43.9,
    "last1min": {"start": 1000.0, "end": 1060.0, "messages": 1500, "position_count_total": 120,
                 "max_distance": 92600.0,
                 "local": {"accepted": [1400, 90], "signal": -18.2, "noise": -33.1, "peak_signal": -2.9}},
    "last15min": {"start": 160.0, "end": 1060.0, "messages": 21000, "position_count_total": 1800,
                  "max_distance": 94877.0, "local": {}},
    "total": {"start": 0.0, "end": 367200.0, "messages": 2758242, "position_count_total": 308185,
              "max_distance": 94877.0, "local": {}},
}
AIRCRAFT = {"now": 1060.0, "aircraft": [
    {"hex": "a1b2c3", "seen": 30.0},                                          # heard, no position
    {"hex": "abc123", "flight": "PDT5995 ", "t": "E145", "r": "N123", "alt_baro": 21000,
     "gs": 380.2, "lat": 1, "lon": 2, "seen_pos": 0.4, "seen": 0.1, "rssi": -9.5, "messages": 410},
    {"hex": "def456", "alt_baro": "ground", "lat": 1, "lon": 2, "seen_pos": 200, "seen": 2.0},  # stale position
    {"flight": "NOHEX"},                                                      # dropped
]}


class FakeReceiver:
    """tar1090's data files on a real port. `status` makes it fail; `calls`
    records the paths asked for."""

    def __init__(self, status=200, redirect=None):
        self.status = status
        self.redirect = redirect
        self.calls = []
        rx = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                rx.calls.append(self.path)
                if rx.redirect:
                    self.send_response(302)
                    self.send_header("Location", rx.redirect)
                    self.end_headers()
                    return
                body = {"/data/stats.json": STATS, "/data/aircraft.json": AIRCRAFT}.get(self.path)
                if body is None or rx.status != 200:
                    self.send_response(404 if body is None else rx.status)
                    self.end_headers()
                    return
                raw = json.dumps(body).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
