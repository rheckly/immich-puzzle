"""Puzzle (App): Meldungen über ntfy (0.1.18). Ein falscher ntfy-Server fängt die Meldungen auf."""
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "app"))
sys.path.insert(0, HERE)

import kiosk  # noqa: E402
import server  # noqa: E402
from notify import Notifier, RunMarker  # noqa: E402
from test_puzzle import photo_url  # noqa: E402

GOT = []


class FakeNtfy(BaseHTTPRequestHandler):
    status = 200

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
        GOT.append({"path": self.path, "auth": self.headers.get("Authorization"), **body})
        self.send_response(FakeNtfy.status)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *a):
        pass


def wait_for(n, timeout=3):
    end = time.time() + timeout
    while len(GOT) < n and time.time() < end:
        time.sleep(0.02)
    return GOT


class AppNotifyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ntfy = ThreadingHTTPServer(("127.0.0.1", 0), FakeNtfy)
        threading.Thread(target=cls.ntfy.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.ntfy.server_address[1]}"
        cls.tmp = tempfile.mkdtemp()
        cls.app = server.App("", "", os.path.join(cls.tmp, "data"), os.path.join(cls.tmp, "puzzles"),
                             os.path.join(cls.tmp, "music"), share_port=1,
                             notifier=Notifier(cls.url, "puzzle", "tk_app"))
        cls.http = ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler(cls.app))
        threading.Thread(target=cls.http.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.http.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        for s in (cls.ntfy, cls.http):
            s.shutdown()
            s.server_close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        GOT.clear()
        FakeNtfy.status = 200
        self.app._set_setting("notify", None)

    def call(self, method, path, body=None):
        req = urllib.request.Request(self.base + path, data=None if body is None else json.dumps(body).encode(),
                                     method=method, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def test_settings_and_test(self):
        status, res = self.call("GET", "/api/puzzles")
        self.assertEqual(status, 200)
        n = res["notify"]
        self.assertEqual((n["errors"], n["summary"], n["time"]), (True, True, "08:00"))
        self.assertEqual((n["ntfy"]["ready"], n["ntfy"]["topic"], n["ntfy"]["token"]), (True, "puzzle", True))
        self.assertNotIn("tk_app", json.dumps(res))
        status, n = self.call("POST", "/api/settings/notify", {"summary": False, "time": "6:05"})
        self.assertEqual(status, 200, n)
        self.assertEqual((n["summary"], n["errors"], n["time"]), (False, True, "06:05"))
        for bad in ({"time": "24:00"}, {"time": "x"}, {"errors": 1}):
            self.assertEqual(self.call("POST", "/api/settings/notify", bad)[0], 400, bad)
        status, n = self.call("POST", "/api/settings/notify/test", {})
        self.assertEqual(status, 200, n)
        self.assertTrue(n["sent"]["ok"])
        msg = wait_for(1)[0]
        self.assertEqual((msg["path"], msg["topic"], msg["auth"]), ("/", "puzzle", "Bearer tk_app"))
        self.assertIn("Testmeldung", msg["title"])
        FakeNtfy.status = 401
        status, err = self.call("POST", "/api/settings/notify/test", {})
        self.assertEqual(status, 400, err)
        self.assertIn("401", err["error"])
        self.assertIn("notifySet", [e["what"] for e in self.app.protocol_entries()["entries"]])

    def test_not_configured(self):
        app = server.App("", "", os.path.join(self.tmp, "d2"), os.path.join(self.tmp, "p2"), os.path.join(self.tmp, "music"))
        self.assertFalse(app.alert_view()["ntfy"]["ready"])
        with self.assertRaises(ValueError) as e:
            app.alert_test()
        self.assertEqual(e.exception.args[0].code, "ntfyOff")
        self.assertFalse(app.alert("errors", "x", "y"))
        self.assertFalse(app.alert_due())

    def test_kiosk_hides_app_box(self):
        k = kiosk.KioskApp(os.path.join(self.tmp, "k"), os.path.join(self.tmp, "music"), enabled=True)
        self.assertIsNone(k.app_settings()["notify"])

    def test_summary(self):
        pid = self.app.save_puzzle({"name": "Gruss an Oma", "config": {"count": 24}, "photo": photo_url()})["id"]
        self.app.online_link(pid, {})
        token = self.app._puzzle(pid)["online_link"]["token"]
        self.assertTrue(self.app.count_view(pid, "10.0.0.1", "Mozilla/5.0"))
        self.assertTrue(self.app.count_view(pid, "10.0.0.2", "Mozilla/5.0"))
        self.app.set_like(pid, "visitor-aaaa", True)
        self.app.set_like(pid, "visitor-bbbb", True)
        self.app.set_like(pid, "visitor-bbbb", False)  # zurückgenommen: bleibt als gesetzt gezählt
        link = self.app._puzzle(pid)["online_link"]
        today = time.strftime("%Y-%m-%d")
        self.assertEqual(link["like_days"], {today: 2})
        self.assertNotIn("visitor-aaaa", json.dumps(link))
        n = self.app.day_numbers(today)
        self.assertEqual((n["created"], n["links"], n["views"], n["likes"]), (1, 1, 2, 2))
        self.assertEqual(n["top"], ("Gruss an Oma", 2))
        # Zahlen von gestern in die Angaben schreiben, wie sie nach einem Tag dastünden
        yday = time.strftime("%Y-%m-%d", time.localtime(time.time() - 86400))
        self.app._update(pid, online_link={**link, "view_days": {yday: 5}, "like_days": {yday: 3}})
        at8 = datetime.now().replace(hour=8, minute=0, second=0).timestamp()
        self.assertFalse(self.app.alert_due(at8 - 60))   # vor 08:00
        self.assertTrue(self.app.alert_due(at8 + 60))
        self.assertFalse(self.app.alert_due(at8 + 120))  # heute schon gesendet
        msg = wait_for(1)[0]
        self.assertEqual(msg["title"], "Puzzle: Tageszusammenfassung")
        self.assertIn("5 Aufrufe, 3 Likes; meistgeschaut: «Gruss an Oma» (5)", msg["message"])
        self.assertIn("Stand: ", msg["message"])
        self.assertNotIn("Heute bis jetzt", msg["message"])
        self.assertNotIn(token, msg["message"])
        status, res = self.call("POST", "/api/settings/notify/summary", {})
        self.assertEqual(status, 200, res)
        self.assertIn("Heute bis jetzt", wait_for(2)[1]["message"])
        self.app.set_alert_settings({"summary": False})
        self.app._set_setting("notify", {**self.app.settings["notify"], "sentDay": ""})
        self.assertFalse(self.app.alert_due(at8 + 60))

    def test_crash_and_switch(self):
        marker = RunMarker(os.path.join(self.tmp, "run.json"), server.VERSION)
        self.app.run_marker = marker
        self.assertEqual(self.app.run_start(), {})  # erster Start: nichts zu melden
        marker.alive()
        time.sleep(0.2)
        self.assertEqual(GOT, [])
        old = self.app.run_start()  # Start, ohne dass der letzte Lauf sauber endete
        self.assertTrue(old.get("started"))
        self.assertIn("Absturz", wait_for(1)[0]["title"])
        marker.end()
        self.assertFalse(os.path.exists(marker.path))
        self.app.set_alert_settings({"errors": False})
        marker.start()
        self.app.run_start()
        time.sleep(0.2)
        self.assertEqual(len(GOT), 1)


if __name__ == "__main__":
    unittest.main()
