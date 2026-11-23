"""Kiosk: Meldungen über ntfy (0.1.13). Ein falscher ntfy-Server fängt die Meldungen auf."""
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


class NotifyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ntfy = ThreadingHTTPServer(("127.0.0.1", 0), FakeNtfy)
        threading.Thread(target=cls.ntfy.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.ntfy.server_address[1]}"
        cls.tmp = tempfile.mkdtemp()
        os.makedirs(os.path.join(cls.tmp, "music"))
        limits = kiosk.Limits(photo_mb=2, per_ip_hour=1000, max_puzzles=1000, storage_mb=1000, draft_hours=24,
                              clean_minutes=15, per_ip_active=20)
        cls.app = kiosk.KioskApp(os.path.join(cls.tmp, "data"), os.path.join(cls.tmp, "music"), limits=limits,
                                 enabled=True, ntfy=kiosk.Notifier(cls.url, "puzzle-kiosk", "tk_test"))
        cls.admin = ThreadingHTTPServer(("127.0.0.1", 0), kiosk.make_admin_handler(cls.app, None))
        threading.Thread(target=cls.admin.serve_forever, daemon=True).start()
        cls.abase = f"http://127.0.0.1:{cls.admin.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        for s in (cls.ntfy, cls.admin):
            s.shutdown()
            s.server_close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        GOT.clear()
        FakeNtfy.status = 200
        self.app._set_setting("kiosk_notify", None)
        self.app.notified = {}
        self.app.level = {"full": False, "storage": False}
        self.app.device_quiet = 0
        self.app.job_failed = False
        self.app.ip_hits = {}
        self.app.ip_puzzles = {}
        self.app.limits.per_ip_hour = 1000
        self.app.limits.max_puzzles = 1000
        self.app.limits.storage_mb = 1000

    def call(self, method, path, body=None):
        req = urllib.request.Request(self.abase + path, data=None if body is None else json.dumps(body).encode(),
                                     method=method, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def test_notifier_config(self):
        n = kiosk.Notifier("https://ntfy.example.ch/puzzle-kiosk", "", "x")
        self.assertEqual((n.base, n.topic, n.ready()), ("https://ntfy.example.ch", "puzzle-kiosk", True))
        self.assertFalse(kiosk.Notifier("ftp://ntfy.example.ch", "t").ready())
        self.assertFalse(kiosk.Notifier("https://ntfy.example.ch", "a/b").ready())
        self.assertFalse(kiosk.Notifier("", "").ready())
        view = kiosk.Notifier("https://ntfy.example.ch", "t", "tk_secret").view()
        self.assertNotIn("tk_secret", json.dumps(view))
        self.assertTrue(view["token"])

    def test_admin_settings_and_test(self):
        status, view = self.call("GET", "/api/kiosk/admin")
        self.assertEqual(status, 200)
        n = view["notify"]
        self.assertEqual((n["limits"], n["errors"], n["summary"], n["time"]), (True, True, True, "08:00"))
        self.assertEqual((n["ntfy"]["ready"], n["ntfy"]["topic"], n["ntfy"]["token"]), (True, "puzzle-kiosk", True))
        self.assertNotIn("tk_test", json.dumps(view))
        status, view = self.call("POST", "/api/kiosk/admin/notify", {"summary": False, "time": "7:30"})
        self.assertEqual(status, 200, view)
        self.assertEqual((view["notify"]["summary"], view["notify"]["time"], view["notify"]["limits"]), (False, "07:30", True))
        for bad in ({"time": "25:00"}, {"time": "abc"}, {"errors": "ja"}):
            self.assertEqual(self.call("POST", "/api/kiosk/admin/notify", bad)[0], 400, bad)
        status, view = self.call("POST", "/api/kiosk/admin/notify/test", {})
        self.assertEqual(status, 200, view)
        self.assertTrue(view["sent"]["ok"])
        msg = wait_for(1)[0]
        self.assertEqual((msg["path"], msg["topic"], msg["auth"]), ("/", "puzzle-kiosk", "Bearer tk_test"))
        self.assertIn("Testmeldung", msg["title"])
        FakeNtfy.status = 403
        status, err = self.call("POST", "/api/kiosk/admin/notify/test", {})
        self.assertEqual(status, 400, err)
        self.assertIn("403", err["error"])
        self.assertFalse(self.call("GET", "/api/kiosk/admin")[1]["notify"]["ntfy"]["last"]["ok"])

    def test_not_configured(self):
        app = kiosk.KioskApp(os.path.join(self.tmp, "data2"), os.path.join(self.tmp, "music"), enabled=True)
        with self.assertRaises(ValueError) as e:
            app.notify_test()
        self.assertEqual(e.exception.args[0].code, "kioskNtfyOff")
        self.assertFalse(app.notify("errors", "x", "y"))

    def test_switch_off(self):
        self.app.set_notify_settings({"errors": False})
        self.assertFalse(self.app.notify("errors", "x", "y"))
        self.assertTrue(self.app.notify("limits", "x", "y"))
        self.assertEqual(len(wait_for(1)), 1)

    def test_device_limit_debounced(self):
        self.app.limits.per_ip_hour = 1
        for _ in range(4):
            try:
                self.app.guest_gate("10.0.0.9")
            except kiosk.LimitError:
                pass
        got = wait_for(1)
        time.sleep(0.2)
        self.assertEqual(len(got), 1, got)
        self.assertIn("Stundenbudget", got[0]["message"])
        self.assertNotIn("10.0.0.9", json.dumps(got))
        # nach einer Stunde wieder, mit Anzahl der Ablehnungen dazwischen
        self.app.notified["device"] -= 3601
        with self.assertRaises(kiosk.LimitError):
            self.app.guest_gate("10.0.0.9")
        self.assertIn("seit der letzten Meldung: 3", wait_for(2)[1]["message"])

    def test_full_and_storage(self):
        self.app.limits.max_puzzles = len(self.app.puzzles) + 1
        self.app.guest_create({"name": "a", "photo": photo_url()}, "10.0.0.1")
        with self.assertRaises(kiosk.LimitError):
            self.app.guest_gate("10.0.0.2")
        got = wait_for(1)
        time.sleep(0.2)
        self.assertEqual([g["title"] for g in got], ["Puzzle-Kiosk ist voll"])
        # bleibt voll: keine zweite Meldung; unter die Grenze und wieder voll nach der Pause: neue Meldung
        self.app.watch_levels()
        self.app.limits.max_puzzles = 1000
        self.app.watch_levels()
        self.app.limits.max_puzzles = len(self.app.puzzles)
        self.app.notified["full"] -= 6 * 3600 + 1
        self.app.watch_levels()
        self.assertEqual(len(wait_for(2)), 2)
        # Speicher knapp ab 80 %
        GOT.clear()
        self.app.limits.max_puzzles = 1000
        mb = self.app.storage_bytes(fresh=True) / 1024 / 1024
        self.app.limits.storage_mb = mb / 0.85
        self.assertEqual(self.app.watch_levels(), {"full": False, "storage": True})
        self.assertIn("Speicher knapp", wait_for(1)[0]["title"])

    def test_job_failure(self):
        orig = self.app.clean
        self.app.clean = lambda *a, **k: (_ for _ in ()).throw(OSError("Platte voll"))
        try:
            self.assertFalse(self.app.run_job())
            self.assertFalse(self.app.run_job())
        finally:
            self.app.clean = orig
        self.assertTrue(self.app.run_job())
        got = wait_for(2)
        time.sleep(0.2)
        # im Hintergrund gesendet: Reihenfolge beim falschen ntfy nicht sicher
        self.assertEqual(sorted(g["title"] for g in got), ["Puzzle-Kiosk: Aufräum-Job gescheitert",
                                                           "Puzzle-Kiosk: Aufräum-Job wieder in Ordnung"], got)
        self.assertIn("Platte voll", next(g["message"] for g in got if "gescheitert" in g["title"]))

    def test_crash_detection(self):
        self.app.run_end()
        self.assertEqual(self.app.run_start(), {})
        self.app.run_alive()
        self.assertTrue(os.path.exists(self.app.run_path))
        time.sleep(0.2)
        self.assertEqual(GOT, [])
        old = self.app.run_start()  # wie ein Start, ohne dass der letzte Lauf sauber endete
        self.assertTrue(old.get("started"))
        self.assertIn("Absturz", wait_for(1)[0]["title"])
        self.app.run_end()
        self.assertFalse(os.path.exists(self.app.run_path))

    def test_summary(self):
        day = time.strftime("%Y-%m-%d", time.localtime(time.time() - 86400))
        self.app.stats[day] = {"created": 3, "links": 2, "views": 7, "rejected": 1}
        at8 = datetime.now().replace(hour=8, minute=0, second=0).timestamp()
        self.assertFalse(self.app.summary_due(at8 - 60))   # vor 08:00
        self.assertTrue(self.app.summary_due(at8 + 60))
        self.assertFalse(self.app.summary_due(at8 + 120))  # heute schon gesendet
        msg = wait_for(1)[0]
        self.assertIn("3 neue Puzzles, 2 Links, 7 Aufrufe, 1 abgelehnt", msg["message"])
        self.assertIn("Speicher", msg["message"])
        self.assertNotIn("Heute bis jetzt", msg["message"])
        status, view = self.call("POST", "/api/kiosk/admin/notify/summary", {})
        self.assertEqual(status, 200, view)
        self.assertIn("Heute bis jetzt", wait_for(2)[1]["message"])
        self.app.set_notify_settings({"summary": False})
        self.app._set_setting("kiosk_notify", {**self.app.settings["kiosk_notify"], "sentDay": ""})
        self.assertFalse(self.app.summary_due(at8 + 60))


if __name__ == "__main__":
    unittest.main()
