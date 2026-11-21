"""Grundtest von Puzzle: Server mit Immich-Attrappe, Puzzle speichern, Live-Link mit Passwort, Download und Musik."""
import base64
import io
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
from http.server import ThreadingHTTPServer

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "app"))
sys.path.insert(0, HERE)

import server  # noqa: E402
from fake_immich import FakeImmich  # noqa: E402


def photo_url(w=400, h=300):
    out = io.BytesIO()
    Image.new("RGB", (w, h), (200, 80, 40)).save(out, "JPEG")
    return "data:image/jpeg;base64," + base64.b64encode(out.getvalue()).decode()


class PuzzleServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fake = FakeImmich(count=20)
        port = cls.fake.start(0)
        cls.tmp = tempfile.mkdtemp()
        music = os.path.join(cls.tmp, "music", "Ruhig")
        os.makedirs(music)
        with open(os.path.join(music, "Abend.mp3"), "wb") as f:
            f.write(b"ID3" + b"\0" * 200)
        cls.app = server.App(f"http://127.0.0.1:{port}", "test-key", os.path.join(cls.tmp, "data"),
                             os.path.join(cls.tmp, "puzzles"), os.path.join(cls.tmp, "music"),
                             share_port=1, share_public_url="https://links.example.com/puzzles")
        cls.http = ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler(cls.app))
        cls.share = ThreadingHTTPServer(("127.0.0.1", 0), server.make_share_handler(cls.app))
        for s in (cls.http, cls.share):
            threading.Thread(target=s.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.http.server_address[1]}"
        cls.sbase = f"http://127.0.0.1:{cls.share.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        for s in (cls.http, cls.share):
            s.shutdown()
            s.server_close()
        cls.fake.stop()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def call(self, method, url, body=None, headers=None, raw=False):
        data = body if isinstance(body, bytes) or body is None else json.dumps(body).encode()
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

    def new_puzzle(self, **cfg):
        config = {"count": 48, "format": "3:2", "title": "Ferien", "text": "Hallo\nWelt", **cfg}
        status, p = self.call("POST", self.base + "/api/puzzles", {"config": config, "photo": photo_url(), "name": "Ferien"})
        self.assertEqual(status, 200, p)
        return p

    def test_page_and_sources(self):
        status, page = self.call("GET", self.base + "/", raw=True)
        self.assertEqual(status, 200)
        self.assertIn(b"puzzle-engine.js", page)
        self.assertNotIn(b"{{", page)
        status, src = self.call("GET", self.base + "/api/sources")
        self.assertEqual(status, 200)
        self.assertIn("albums", src)
        self.assertIn("people", src)
        self.assertIn("other", src["albums"][0])  # Reiter «Geteilt mit»
        status, font = self.call("GET", self.base + "/fonts/Roboto-Bold.woff", raw=True)
        self.assertEqual(status, 200)
        self.assertTrue(font.startswith(b"wOFF"))
        status, _ = self.call("GET", self.base + "/fonts/..%2Fserver.py", raw=True)
        self.assertEqual(status, 404)

    def test_config_is_cleaned(self):
        c = server.clean_config({"count": 24, "format": "1:1", "T": 30, "durMode": "music", "hold": 3, "tempo": .4,
                                 "text": "  a   b \r\n c ", "music": None, "extra": 1})
        self.assertEqual(c["count"], 24)
        self.assertEqual(c["format"], "1:1")
        self.assertEqual(c["durMode"], "own")  # ohne Musik keine Dauer «wie die Musik»
        self.assertEqual(c["text"], "a b\nc")
        self.assertNotIn("extra", c)
        self.assertEqual(c["hold"], 3)
        self.assertEqual((c["frameAt"], c["ramp"], c["textSize"], c["textPos"]), (40, 5, 100, "mc"))
        c = server.clean_config({"format": "4:3", "tempo": 1.6, "hold": 8, "frameAt": 20, "ramp": 60, "textSize": 150,
                                 "textFont": "luckiest", "textPos": "br", "textColor": "#ABCDEF"})
        self.assertEqual((c["format"], c["tempo"], c["hold"], c["frameAt"], c["ramp"]), ("4:3", 1.6, 8, 20, 60))
        self.assertEqual((c["textSize"], c["textFont"], c["textPos"], c["textColor"]), (150, "luckiest", "br", "#abcdef"))
        self.assertEqual(server.clean_config({"textPos": "bottom"})["textPos"], "bc")  # Puzzles bis 0.1.2
        for bad in ({"count": 7}, {"format": "5:4"}, {"T": 999}, {"hold": 1}, {"hold": 11}, {"tempo": 3},
                    {"frameAt": 10}, {"ramp": 61}, {"textSize": 300}, {"textFont": "x"}, {"textPos": "top-left"},
                    {"textColor": "red"}, {"textColor": "#12345"}, {"count": True}, {"music": "x"}):
            with self.assertRaises(ValueError, msg=bad):
                server.clean_config(bad)

    def test_save_list_and_files(self):
        p = self.new_puzzle()
        self.assertEqual(p["config"]["count"], 48)
        self.assertEqual(p["config"]["text"], "Hallo\nWelt")
        status, listing = self.call("GET", self.base + "/api/puzzles")
        self.assertIn(p["id"], [x["id"] for x in listing["puzzles"]])
        for name in ("photo.jpg", "thumb.jpg", "card.jpg"):
            status, body = self.call("GET", f"{self.base}/api/puzzles/{p['id']}/{name}", raw=True)
            self.assertEqual(status, 200, name)
            self.assertTrue(body.startswith(b"\xff\xd8"), name)
        status, p2 = self.call("POST", f"{self.base}/api/puzzles/{p['id']}", {"config": {**p["config"], "count": 200}})
        self.assertEqual(p2["config"]["count"], 200)
        status, _ = self.call("POST", self.base + "/api/puzzles", {"config": {"count": 24}})
        self.assertEqual(status, 400)  # neues Puzzle ohne Foto

    def test_live_link(self):
        p = self.new_puzzle()
        status, p = self.call("POST", f"{self.base}/api/puzzles/{p['id']}/link", {"download": False, "days": 30})
        self.assertEqual(status, 200, p)
        link = p["link"]
        self.assertTrue(link["url"].startswith("https://links.example.com/puzzles/"))
        path = link["url"].split("links.example.com", 1)[1]
        status, page = self.call("GET", self.sbase + path, raw=True)
        self.assertEqual(status, 200)
        self.assertIn(b'og:image', page)
        self.assertIn(b"Ferien", page)
        self.assertNotIn(b"{{", page)
        status, view = self.call("GET", self.sbase + path + "puzzle.json")
        self.assertEqual(view["config"]["count"], 48)
        self.assertFalse(view["download"])
        self.assertIsNone(view["meta"])
        self.assertNotIn("music", view["config"])
        status, _ = self.call("GET", self.sbase + path + "download", raw=True)
        self.assertEqual(status, 404)
        self.call("POST", f"{self.base}/api/puzzles/{p['id']}/link", {"download": True})
        status, body = self.call("GET", self.sbase + path + "download", raw=True)
        self.assertEqual(status, 200)
        # Passwort: ohne Freischaltung keine Daten mehr
        status, p = self.call("POST", f"{self.base}/api/puzzles/{p['id']}/link", {"password": "geheim123"})
        self.assertEqual(status, 200, p)
        self.assertTrue(p["link"]["protected"])
        status, _ = self.call("GET", self.sbase + path + "puzzle.json")
        self.assertEqual(status, 401)
        # Aus: Seite sagt «nicht aktiv»
        self.call("POST", f"{self.base}/api/puzzles/{p['id']}/link", {"password": None, "active": False})
        status, _ = self.call("GET", self.sbase + path + "puzzle.json")
        self.assertEqual(status, 403)
        status, p = self.call("DELETE", f"{self.base}/api/puzzles/{p['id']}/link")
        self.assertIsNone(p["link"])
        status, _ = self.call("GET", self.sbase + path + "puzzle.json")
        self.assertEqual(status, 404)

    def test_shares_box(self):
        """Box «Geteilte Puzzles»: Liste mit Vorschaubild, Titel und Gültigkeit ändern, Abgelaufene löschen"""
        a, b = self.new_puzzle(), self.new_puzzle()
        for p in (a, b):
            status, _ = self.call("POST", f"{self.base}/api/puzzles/{p['id']}/link", {"days": 30})
            self.assertEqual(status, 200)
        status, res = self.call("GET", self.base + "/api/shares")
        entry = next(s for s in res["shares"] if s["id"] == a["id"])
        self.assertEqual(entry["thumb"].split("?")[0], f"/api/puzzles/{a['id']}/thumb.jpg")
        status, body = self.call("GET", self.base + entry["thumb"], raw=True)
        self.assertEqual(status, 200)
        status, p = self.call("POST", f"{self.base}/api/puzzles/{a['id']}/link", {"title": "Für Oma", "days": "today"})
        self.assertEqual((p["link"]["title"] if "title" in p["link"] else None, p["link"]["days"]), ("Für Oma", "today"))
        status, p = self.call("POST", f"{self.base}/api/puzzles/{a['id']}/link", {"until": "2999-01-31"})
        self.assertEqual(p["link"]["days"], "until")
        # b läuft ab: «Abgelaufene löschen» entfernt nur dessen Link, das Puzzle bleibt
        with self.app.lock:
            self.app.puzzles[b["id"]]["online_link"]["expires"] = time.time() - 10
        status, res = self.call("DELETE", self.base + "/api/shares/expired")
        self.assertEqual(status, 200, res)
        ids = [s["id"] for s in res["shares"]]
        self.assertIn(a["id"], ids)
        self.assertNotIn(b["id"], ids)
        status, p = self.call("GET", f"{self.base}/api/puzzles/{b['id']}")
        self.assertEqual(status, 200)
        self.assertIsNone(p["link"])

    def test_share_url_setting(self):
        """Box «Einstellungen»: eigene Adresse der Live-Links gilt vor SHARE_PUBLIC_URL, leer = wieder die Vorgabe"""
        status, setup = self.call("GET", self.base + "/api/shares")
        self.assertEqual((setup["url"], setup["default"], setup["custom"]), ("https://links.example.com/puzzles", "https://links.example.com/puzzles", False))
        status, err = self.call("POST", self.base + "/api/shares/url", {"url": "ftp://x"})
        self.assertEqual((status, err.get("error_code")), (400, "shareUrlInvalid"), err)
        try:
            status, setup = self.call("POST", self.base + "/api/shares/url", {"url": "https://andere.example.org/spiele/"})
            self.assertEqual(status, 200, setup)
            self.assertEqual((setup["url"], setup["prefix"], setup["custom"]), ("https://andere.example.org/spiele", "/spiele", True))
            with open(os.path.join(self.tmp, "data", "settings.json"), encoding="utf-8") as f:
                self.assertEqual(json.load(f)["share_public_url"], "https://andere.example.org/spiele")
            p = self.new_puzzle()
            status, p = self.call("POST", f"{self.base}/api/puzzles/{p['id']}/link", {"days": 7})
            self.assertTrue(p["link"]["url"].startswith("https://andere.example.org/spiele/"), p["link"])
        finally:
            status, setup = self.call("POST", self.base + "/api/shares/url", {"url": ""})
        self.assertEqual((status, setup["url"], setup["custom"]), (200, "https://links.example.com/puzzles", False))

    def test_notice_page(self):
        """Entscheid 52: Hinweisseite mit Vorrang, Status, Sprache, Umami-Grund, ohne Inhalt und ohne Zählung."""
        page = {"Accept": "text/html"}
        en = {**page, "Accept-Language": "fr-CH, en;q=0.8, de;q=0.5"}
        for url in (self.sbase + "/", self.sbase + "/puzzles/", self.sbase + "/puzzles"):
            status, body = self.call("GET", url, headers=page, raw=True)
            self.assertEqual(status, 404, url)
            self.assertIn("Hier liegt kein Puzzle".encode(), body)
        status, body = self.call("GET", self.sbase + "/puzzles/", headers=en, raw=True)
        self.assertIn(b"There is no puzzle here", body)
        self.assertIn(b'<html lang="en">', body)
        status, body = self.call("GET", self.sbase + "/puzzles/?lang=de", headers=en, raw=True)
        self.assertIn("Hier liegt kein Puzzle".encode(), body)
        try:
            urllib.request.urlopen(urllib.request.Request(self.sbase + "/puzzles/", headers=page))
        except urllib.error.HTTPError as e:
            head = e.headers
        self.assertEqual((head["Cache-Control"], head["Referrer-Policy"]), ("no-store", "no-referrer"))
        self.assertIn("noindex", head["X-Robots-Tag"])
        self.assertIn("frame-ancestors 'none'", head["Content-Security-Policy"])
        status, body = self.call("GET", self.sbase + "/puzzles/" + "x" * 32 + "/", headers=page, raw=True)
        self.assertEqual(status, 404)
        self.assertIn(b"Dieser Link gilt nicht (mehr)", body)

        p = self.new_puzzle(title="Geheim")
        status, p = self.call("POST", f"{self.base}/api/puzzles/{p['id']}/link", {"days": 30})
        path = p["link"]["url"].split("links.example.com", 1)[1]
        app = self.app
        app.umami_links = ("https://umami.example.com", "site-1")
        try:
            def link(**change):
                with app.lock:
                    current = dict(app.puzzles[p["id"]]["online_link"])
                app._update(p["id"], online_link={**current, **change})

            def notice(status_wanted, text, grund, headers=page):
                status, body = self.call("GET", self.sbase + path, headers=headers, raw=True)
                self.assertEqual(status, status_wanted, text)
                self.assertIn(text.encode(), body)
                self.assertIn(f"grund: &quot;{grund}&quot;".encode(), body)
                self.assertNotIn(b"Geheim", body)
                self.assertNotIn(b"og:image", body)
                self.assertNotIn(path.encode(), body)  # Umami und Seite nennen den Code nicht
                # Nur die Funktionsform maskiert URL und Referrer, auch beim Ereignis (Design-Vorlage Kap. 7)
                calls = body.split(b"umami.track(")[1:]
                self.assertEqual(len(calls), 2)
                for call in calls:
                    self.assertTrue(call.startswith(b"function (p)"), call[:40])
                    self.assertIn(b"p.url = &quot;/puzzles/&quot;", call)
                    self.assertIn(b"p.referrer = ''", call)

            soon = time.time() + 3 * 86400
            link(valid_from=soon, active=False, expires=time.time() - 60)
            notice(410, "Dieser Link gilt nicht (mehr)", "abgelaufen")
            status, _ = self.call("GET", self.sbase + path + "puzzle.json")
            self.assertEqual(status, 410)
            link(expires=time.time() + 86400)
            notice(403, "Link nicht aktiv", "nicht-aktiv")
            notice(403, "Link not active", "nicht-aktiv", en)
            link(active=True)
            notice(403, "Dieser Link gilt noch nicht", "noch-nicht")
            notice(403, "This link is valid from", "noch-nicht", en)
            link(valid_from=None)
            self.call("POST", f"{self.base}/api/puzzles/{p['id']}/link", {"password": "geheim123"})
            notice(200, "Diese Seite ist mit einem Passwort geschützt.", "passwort")
            with app.lock:
                views = app.puzzles[p["id"]]["online_link"].get("views") or 0
            self.assertEqual(views, 0)
        finally:
            app.umami_links = None

    def test_music(self):
        status, lib = self.call("GET", self.base + "/api/music")
        self.assertEqual(status, 200)
        ids = [t["id"] for t in lib["tracks"]]
        self.assertIn("lib:Ruhig/Abend.mp3", ids)
        status, body = self.call("GET", self.base + "/api/music/" + urllib.parse.quote("lib:Ruhig/Abend.mp3", safe=""), raw=True)
        self.assertEqual(status, 200)
        status, _ = self.call("GET", self.base + "/api/music/" + urllib.parse.quote("lib:../../x.mp3", safe=""), raw=True)
        self.assertIn(status, (400, 404))
        wav = b"RIFF" + (36).to_bytes(4, "little") + b"WAVEfmt " + b"\0" * 24
        status, up = self.call("POST", self.base + "/api/music", wav,
                               {"Content-Type": "application/octet-stream", "X-Music-Upload": "1", "X-File-Name": "Mein%20Lied.wav"})
        self.assertEqual(status, 200, up)
        self.assertTrue(up["id"].startswith("up:"))
        p = self.new_puzzle(music={"id": up["id"], "name": "Mein Lied", "volume": .5, "fade": True}, durMode="own")
        self.assertEqual(p["config"]["music"]["id"], up["id"])
        status, _ = self.call("DELETE", self.base + "/api/music/" + urllib.parse.quote("lib:Ruhig/Abend.mp3", safe=""))
        self.assertEqual(status, 400)


if __name__ == "__main__":
    unittest.main()


class WithoutImmichTest(unittest.TestCase):
    """Ohne IMMICH_URL und IMMICH_API_KEY: nur Hochladen, Immich-Endpunkte sagen klar, dass Immich fehlt."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.app = server.App("", "", os.path.join(cls.tmp, "data"), os.path.join(cls.tmp, "puzzles"),
                             os.path.join(cls.tmp, "music"))
        cls.http = ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler(cls.app))
        threading.Thread(target=cls.http.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.http.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown()
        cls.http.server_close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    call = PuzzleServerTest.call

    def test_upload_only(self):
        self.assertIsNone(self.app.immich)
        status, v = self.call("GET", self.base + "/api/version")
        self.assertEqual((status, v["immich"]), (200, False))
        status, info = self.call("GET", self.base + "/api/immich")
        self.assertEqual((status, info), (200, {"ok": False, "configured": False}))
        status, err = self.call("GET", self.base + "/api/sources")
        self.assertEqual(status, 400)
        self.assertIn("noImmich", json.dumps(err))
        status, p = self.call("POST", self.base + "/api/puzzles",
                              {"config": {"count": 24, "metadata": True}, "photo": photo_url(), "name": "Hochgeladen",
                               "asset": "11111111-1111-1111-1111-111111111111"})
        self.assertEqual(status, 200, p)
        status, got = self.call("GET", self.base + f"/api/puzzles/{p['id']}")
        self.assertEqual(status, 200)
        self.assertIsNone(got["meta"])
        status, data = self.call("GET", self.base + f"/api/puzzles/{p['id']}/photo.jpg", raw=True)
        self.assertEqual(status, 200)
