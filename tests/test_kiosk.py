"""Kiosk-Modus (0.1.9): Gäste-Port mit Whitelist, Bearbeitungsschlüssel, Grenzen, Link-Vorgaben, Aufräumjob,
Datenschutzseite, abgelaufene Links (410) und Admin-Ansicht."""
import base64
import http.client
import io
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "app"))
sys.path.insert(0, HERE)

import kiosk  # noqa: E402
import server  # noqa: E402
from PIL import Image  # noqa: E402
from test_puzzle import photo_url  # noqa: E402


def data_url(img, fmt, mime):
    out = io.BytesIO()
    img.save(out, fmt)
    return f"data:image/{mime};base64," + base64.b64encode(out.getvalue()).decode()


class KioskTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        os.makedirs(os.path.join(cls.tmp, "music"))
        limits = kiosk.Limits(photo_mb=2, per_ip_hour=1000, max_puzzles=1000, storage_mb=1000, draft_hours=24, clean_minutes=15)
        cls.app = kiosk.KioskApp(os.path.join(cls.tmp, "data"), os.path.join(cls.tmp, "music"), share_port=1,
                                 share_public_url="https://links.example.com/puzzles", limits=limits, enabled=True)
        cls.guest = ThreadingHTTPServer(("127.0.0.1", 0), kiosk.make_guest_handler(cls.app))
        cls.admin = ThreadingHTTPServer(("127.0.0.1", 0), kiosk.make_admin_handler(cls.app, None))
        cls.share = ThreadingHTTPServer(("127.0.0.1", 0), server.make_share_handler(cls.app))
        for s in (cls.guest, cls.admin, cls.share):
            threading.Thread(target=s.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.guest.server_address[1]}"
        cls.abase = f"http://127.0.0.1:{cls.admin.server_address[1]}"
        cls.sbase = f"http://127.0.0.1:{cls.share.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        for s in (cls.guest, cls.admin, cls.share):
            s.shutdown()
            s.server_close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        self.app.limits.per_ip_hour = 1000
        self.app.limits.per_ip_active = 20
        self.app.ip_hits = {}
        self.app.ip_puzzles = {}
        self.app._set_setting("kiosk_enabled", True)

    def call(self, method, url, body=None, headers=None, raw=False):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json",
                                                                             "User-Agent": "Mozilla/5.0 test", **(headers or {})})
        try:
            with urllib.request.urlopen(req) as res:
                payload = res.read()
                return res.status, (payload if raw else json.loads(payload or b"{}"))
        except urllib.error.HTTPError as e:
            payload = e.read()
            try:
                return e.code, json.loads(payload)
            except ValueError:
                return e.code, payload

    def create(self, **cfg):
        config = {"count": 48, "format": "3:2", "title": "Gruss", **cfg}
        status, p = self.call("POST", self.base + "/api/kiosk/puzzles", {"config": config, "photo": photo_url(), "name": "Gruss"})
        self.assertEqual(status, 200, p)
        self.assertTrue(p.get("key"))
        return p

    def test_whitelist(self):
        status, page = self.call("GET", self.base + "/", raw=True)
        self.assertEqual(status, 200)
        self.assertIn(b"kiosk.js", page)
        self.assertNotIn(b"kiosk-admin.js", page)
        self.assertNotIn(b"{{", page)
        for path in ("/api/protocol", "/api/settings", "/api/sources", "/api/puzzles/abc/link", "/login", "/api/kiosk/admin"):
            status, _ = self.call("GET", self.base + path, raw=True)
            self.assertEqual(status, 404, path)
        status, lst = self.call("GET", self.base + "/api/puzzles")
        self.assertEqual(lst["puzzles"], [])
        status, st = self.call("GET", self.base + "/api/kiosk")
        self.assertEqual((st["status"], st["titleDefault"], st["limits"]["photoMb"]), ("open", kiosk.TITLE_DEFAULT, 2))

    def test_key_and_link(self):
        p = self.create()
        pid, key = p["id"], p["key"]
        self.assertNotIn(key, json.dumps(self.app.puzzles[pid]))
        status, _ = self.call("GET", f"{self.base}/api/kiosk/puzzles/{pid}")
        self.assertEqual(status, 404)
        status, _ = self.call("GET", f"{self.base}/api/kiosk/puzzles/{pid}", headers={"X-Edit-Key": "falsch"})
        self.assertEqual(status, 404)
        hdr = {"X-Edit-Key": key}
        status, got = self.call("GET", f"{self.base}/api/kiosk/puzzles/{pid}", headers=hdr)
        self.assertEqual(status, 200, got)
        status, saved = self.call("POST", f"{self.base}/api/kiosk/puzzles/{pid}",
                                  {"config": {"count": 48, "format": "3:2", "title": "Neu", "music": {"id": "own:x"}}}, hdr)
        self.assertEqual(status, 200, saved)
        self.assertIsNone(self.app.puzzles[pid]["config"].get("music"))
        # Link: Vorgaben 7 Tage, Titel-Vorgabe, kein Download, keine Metadaten; unbekannte Dauer → 7
        status, linked = self.call("POST", f"{self.base}/api/kiosk/puzzles/{pid}/link", {"days": 30}, hdr)
        self.assertEqual(status, 200, linked)
        link = linked["link"]
        self.assertEqual(link["days"], 7)
        self.assertFalse(link["download"])
        self.assertEqual(self.app.puzzles[pid]["online_link"].get("title"), kiosk.TITLE_DEFAULT)
        status, linked = self.call("POST", f"{self.base}/api/kiosk/puzzles/{pid}/link", {"title": "Für Anna", "days": 1}, hdr)
        self.assertEqual((linked["link"]["days"], self.app.puzzles[pid]["online_link"]["title"]), (1, "Für Anna"))
        path = link["url"].split("links.example.com", 1)[1]
        status, view = self.call("GET", self.sbase + path + "puzzle.json")
        self.assertEqual(status, 200, view)
        self.assertTrue(view["kiosk"])
        self.assertEqual(view["likes"], 0)
        # Löschen nur mit Schlüssel; danach meldet der Link 410
        status, _ = self.call("DELETE", f"{self.base}/api/kiosk/puzzles/{pid}")
        self.assertEqual(status, 404)
        status, _ = self.call("DELETE", f"{self.base}/api/kiosk/puzzles/{pid}/link", headers=hdr)
        self.assertEqual(status, 200)
        status, _ = self.call("GET", self.sbase + path + "puzzle.json")
        self.assertEqual(status, 410)
        status, _ = self.call("DELETE", f"{self.base}/api/kiosk/puzzles/{pid}", headers=hdr)
        self.assertEqual(status, 200)
        self.assertNotIn(pid, self.app.puzzles)

    def test_limits(self):
        self.app.limits.per_ip_hour = 1
        self.create()
        status, err = self.call("POST", self.base + "/api/kiosk/puzzles", {"config": {"count": 48}, "photo": photo_url()})
        self.assertEqual(status, 429, err)
        self.app.limits.per_ip_hour = 0  # 0 = ohne Grenze
        self.app.limits.max_puzzles = 0
        self.create()
        self.app.limits.max_puzzles = 1000
        self.app.limits.per_ip_hour = 1000
        self.app._set_setting("kiosk_enabled", False)
        status, st = self.call("GET", self.base + "/api/kiosk")
        self.assertEqual(st["status"], "paused")
        status, err = self.call("POST", self.base + "/api/kiosk/puzzles", {"config": {"count": 48}, "photo": photo_url()})
        self.assertEqual(status, 429, err)
        self.app._set_setting("kiosk_enabled", True)
        big = "data:image/jpeg;base64," + "A" * (4 * 1024 * 1024 + 10)
        before = len(self.app.puzzles)
        try:  # zu gross: der Server antwortet 413 und schliesst, Windows meldet das teils als Abbruch
            status, _ = self.call("POST", self.base + "/api/kiosk/puzzles", {"config": {"count": 48}, "photo": big})
            self.assertGreaterEqual(status, 400)
        except (ConnectionError, urllib.error.URLError):
            pass
        self.assertEqual(len(self.app.puzzles), before)
        self.assertGreaterEqual(self.app.stats[time.strftime("%Y-%m-%d")].get("rejected", 0), 2)

    def test_clean(self):
        draft = self.create()
        linked = self.create()
        self.app.online_link(linked["id"], {"days": 1})
        token = self.app.puzzles[linked["id"]]["online_link"]["token"]
        orphan = os.path.join(self.app.puzzle_dir, "verwaist")
        os.makedirs(orphan)
        old = time.time() - 7200
        os.utime(orphan, (old, old))
        run = self.app.clean(now=time.time() + 3 * 86400, by="Test")
        self.assertGreaterEqual(run["drafts"], 1)
        self.assertGreaterEqual(run["expired"], 1)
        self.assertGreaterEqual(run["orphans"], 1)
        for p in (draft, linked):
            self.assertNotIn(p["id"], self.app.puzzles)
        self.assertIn(token, self.app.gone)
        self.assertEqual(self.app.job["runs"][0]["by"], "Test")

    def test_search_engines(self):
        """Checkliste öffentliche Website: robots, Sitemap, Titel, Beschreibung, canonical, noindex für Persönliches."""
        status, robots = self.call("GET", self.base + "/robots.txt", raw=True)
        self.assertEqual(status, 200)
        robots = robots.decode()
        for line in ("Allow: /", "Disallow: /edit/", "Disallow: /api/", "Disallow: /login", "Disallow: /puzzles/",
                     f"Sitemap: {self.base}/sitemap.xml"):
            self.assertIn(line + "\n", robots)
        status, sitemap = self.call("GET", self.base + "/sitemap.xml", raw=True)
        self.assertEqual(status, 200)
        self.assertEqual(re.findall(rb"<loc>(.*?)</loc>", sitemap), [f"{self.base}/".encode(), f"{self.base}/datenschutz".encode()])
        status, page = self.call("GET", self.base + "/", raw=True)
        self.assertIn(b"<title>Puzzle \xc2\xb7 Turn your photo into a puzzle</title>", page)
        desc = re.search(rb'<meta name="description" content="([^"]+)">', page).group(1)
        self.assertTrue(50 <= len(desc.decode()) <= 160)
        self.assertIn(f'<link rel="canonical" href="{self.base}/">'.encode(), page)
        self.assertNotIn(b"noindex", page)
        status, page = self.call("GET", self.base + "/edit/" + "a" * 32, raw=True)
        self.assertEqual(status, 200)
        self.assertIn(b'<meta name="robots" content="noindex, nofollow">', page)
        self.assertNotIn(b'rel="canonical"', page)
        status, page = self.call("GET", self.base + "/privacy", raw=True)
        self.assertIn(f'<link rel="canonical" href="{self.base}/datenschutz">'.encode(), page)
        self.assertIn(b'<meta name="description"', page)
        status, page = self.call("GET", self.abase + "/", raw=True)
        self.assertIn(b"<title>Puzzle</title>", page)  # Admin unverändert
        self.assertNotIn(b'name="description"', page)

    def test_privacy(self):
        self.app._set_setting("kiosk_contact", "kiosk@example.com")
        status, page = self.call("GET", self.base + "/datenschutz", raw=True)
        self.assertEqual(status, 200)
        self.assertIn(b"kiosk@example.com", page)
        self.assertIn("Lade nur Fotos hoch".encode(), page)
        self.app._set_setting("kiosk_privacy", "Eigener <Text>")
        status, page = self.call("GET", self.base + "/privacy", raw=True)
        self.assertIn(b"Eigener &lt;Text&gt;", page)
        self.app._set_setting("kiosk_privacy", "")

    def test_admin(self):
        status, page = self.call("GET", self.abase + "/", raw=True)
        self.assertEqual(status, 200)
        self.assertIn(b"kiosk-admin.js", page)
        status, view = self.call("GET", self.abase + "/api/kiosk/admin")
        self.assertEqual(status, 200, view)
        for key in ("enabled", "status", "limits", "puzzles", "drafts", "links", "storageMb", "stats", "job", "privacyDefault"):
            self.assertIn(key, view)
        status, view = self.call("POST", self.abase + "/api/kiosk/admin/settings",
                                 {"enabled": False, "contact": "  a@b.ch ", "title": "Hallo  du"})
        self.assertEqual(status, 200, view)
        self.assertEqual((view["enabled"], view["status"], view["contact"], view["title"]), (False, "paused", "a@b.ch", "Hallo du"))
        status, st = self.call("GET", self.base + "/api/kiosk")
        self.assertEqual((st["status"], st["titleDefault"]), ("paused", "Hallo du"))
        status, err = self.call("POST", self.abase + "/api/kiosk/admin/settings", {"enabled": "ja"})
        self.assertEqual(status, 400, err)
        status, view = self.call("POST", self.abase + "/api/kiosk/admin/job", {})
        self.assertEqual(status, 200, view)
        self.assertTrue(view["job"]["runs"])
        self.call("POST", self.abase + "/api/kiosk/admin/settings", {"enabled": True, "title": "", "contact": ""})

    # ---------- Sicherheitsprüfung 0.1.9 ----------
    def raw_post(self, path, length, body=b""):
        conn = http.client.HTTPConnection("127.0.0.1", self.guest.server_address[1], timeout=10)
        try:
            conn.putrequest("POST", path)
            conn.putheader("Content-Type", "application/json")
            conn.putheader("Content-Length", length)
            conn.endheaders()
            if body:
                conn.send(body)
            return conn.getresponse().status
        finally:
            conn.close()

    def test_photo_limits(self):
        """Befund 1: nur JPEG/PNG/WEBP, höchstens 40 MP, vor dem Dekodieren geprüft."""
        server.decode_photo(photo_url())
        server.decode_photo(data_url(Image.new("RGB", (50, 40)), "PNG", "png"))
        huge = data_url(Image.new("1", (8000, 6000)), "PNG", "png")  # 48 MP, als PNG nur wenige KB
        for bad in (huge, data_url(Image.new("RGB", (50, 40)), "GIF", "gif"), data_url(Image.new("RGB", (50, 40)), "BMP", "bmp"),
                    "data:image/png;base64,bm9jaCBrZWluIEJpbGQ=", "kein Bild"):
            with self.assertRaises(ValueError):
                server.decode_photo(bad)
        status, err = self.call("POST", self.base + "/api/kiosk/puzzles", {"config": {"count": 48}, "photo": huge})
        self.assertEqual(status, 400, err)

    def test_content_length(self):
        """Befund 3: negative oder unlesbare Länge 400, zu gross 413, ohne den Body zu lesen."""
        before = len(self.app.puzzles)
        self.assertEqual(self.raw_post("/api/kiosk/puzzles", "-5"), 400)
        self.assertEqual(self.raw_post("/api/kiosk/puzzles", "abc"), 400)
        self.assertEqual(self.raw_post("/api/kiosk/puzzles", str(500 * 1024 * 1024)), 413)
        self.assertEqual(len(self.app.puzzles), before)

    def test_csv_cell(self):
        """Befund 5: Formeln in der CSV-Ausgabe entschärft (Regex in puzzle.html)."""
        with open(os.path.join(HERE, "..", "app", "puzzle.html"), encoding="utf-8") as f:
            page = f.read()
        self.assertIn(r"""const cell = x => { if (/^[=+\-@\t\r]/.test(x)) x = "'" + x;""", page)
        formula = re.compile(r"^[=+\-@\t\r]")
        for x in ("=HYPERLINK(1)", "+1", "-1", "@SUM(A1)", "\tx", "\rx"):
            self.assertTrue(formula.match(x), x)
        self.assertFalse(formula.match("Anna"))

    def test_headers(self):
        """Befund 6: Gäste-Editor und Abspielseite mit CSP und Nonce, keine Einbettung."""
        for url in (self.base + "/",):
            with urllib.request.urlopen(url) as res:
                csp, page = res.headers["Content-Security-Policy"], res.read().decode()
                self.assertEqual(res.headers["X-Frame-Options"], "DENY")
            nonce = re.search(r"'nonce-([^']+)'", csp).group(1)
            self.assertIn("frame-ancestors 'none'", csp)
            self.assertNotIn("unsafe-inline", csp.split("script-src", 1)[1].split(";", 1)[0])
            scripts = re.findall(r"<script[^>]*>", page)
            self.assertTrue(scripts)
            for tag in scripts:
                self.assertIn(f'nonce="{nonce}"', tag)
        with urllib.request.urlopen(self.base + "/") as res:
            self.assertNotIn(nonce, res.headers["Content-Security-Policy"])  # jede Antwort eine neue Nonce
        p = self.create()
        link = self.call("POST", f"{self.base}/api/kiosk/puzzles/{p['id']}/link", {}, {"X-Edit-Key": p["key"]})[1]["link"]
        path = link["url"].split("links.example.com", 1)[1]
        with urllib.request.urlopen(self.sbase + path) as res:
            self.assertIn("'nonce-", res.headers["Content-Security-Policy"])
            self.assertEqual(res.headers["X-Frame-Options"], "DENY")
        self.assertEqual(server.with_nonce('<script src="a.js"></script>', "N"), '<script nonce="N" src="a.js"></script>')

    def test_gates(self):
        """Befund 2/4: Fotowechsel zählt ins Budget, aktive Puzzles pro Gerät, IPv6 pro /64, Notbremse."""
        p = self.create()
        hdr = {"X-Edit-Key": p["key"]}
        self.app.limits.per_ip_hour = 2
        self.app.ip_hits = {}
        url = f"{self.base}/api/kiosk/puzzles/{p['id']}"
        for _ in range(2):
            status, err = self.call("POST", url, {"config": {"count": 48}, "photo": photo_url(1200, 900)}, hdr)
            self.assertEqual(status, 200, err)
        status, err = self.call("POST", url, {"config": {"count": 48}, "photo": photo_url(1200, 900)}, hdr)
        self.assertEqual(status, 429, err)
        status, err = self.call("POST", url, {"config": {"count": 48, "title": "ohne Foto"}}, hdr)
        self.assertEqual(status, 200, err)  # ohne Foto kein Budget
        buf = io.BytesIO(); Image.effect_noise((1200, 630), 90).convert("RGB").save(buf, "JPEG", quality=85)
        card = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
        self.assertGreater(len(card), 256 * 1024)
        status, err = self.call("POST", url, {"config": {"count": 48, "title": "Startbild"}, "card": card}, hdr)
        self.assertEqual(status, 200, err)  # grosses Startbild beim Autospeichern ist kein Fotowechsel (Handy, 22.11.2026)
        self.app.limits.per_ip_hour = 1000
        self.app.limits.per_ip_active = 2
        self.create()
        status, err = self.call("POST", self.base + "/api/kiosk/puzzles", {"config": {"count": 48}, "photo": photo_url()})
        self.assertEqual(status, 429, err)
        self.app.limits.per_ip_active = 20
        net = kiosk.KioskApp._ip_net
        self.assertEqual(net("2001:db8:1:2:aaaa::1"), net("2001:db8:1:2:bbbb::9"))
        self.assertNotEqual(net("2001:db8:1:2::1"), net("2001:db8:1:3::1"))
        self.assertEqual(net("::ffff:192.0.2.7"), "192.0.2.7")
        self.assertNotEqual(net("192.0.2.7"), net("192.0.2.8"))
        # Notbremse: alles der letzten Stunde weg, auch mit Link (410)
        link = self.call("POST", f"{url}/link", {}, hdr)[1]["link"]
        status, err = self.call("POST", self.abase + "/api/kiosk/admin/purge", {"hours": 0})
        self.assertEqual(status, 400, err)
        status, view = self.call("POST", self.abase + "/api/kiosk/admin/purge", {"hours": 1})
        self.assertEqual(status, 200, view)
        self.assertGreaterEqual(view["purged"], 2)
        self.assertEqual(self.app.puzzles, {})
        status, _ = self.call("GET", self.sbase + link["url"].split("links.example.com", 1)[1] + "puzzle.json")
        self.assertEqual(status, 410)
        status, _ = self.call("POST", self.base + "/api/kiosk/admin/purge", {"hours": 1})
        self.assertEqual(status, 404)  # nicht auf dem Gäste-Port


if __name__ == "__main__":
    unittest.main()
