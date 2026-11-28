"""Collage als Motiv (Entscheid 66): Puzzle liest fertige Collagen aus Collage, nur Server zu Server mit SHARES_API_KEY.
Ein Fake-Collage nach docs/collage-motiv-api.md (Smart Albums) steht für Collage."""
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "app"), os.path.join(ROOT, "tests")]

import family  # noqa: E402
import kiosk  # noqa: E402
import server  # noqa: E402
from collage_source import CollageError, CollageSource  # noqa: E402

KEY = "test-shares-key"


def jpeg(w=40, h=30):
    out = io.BytesIO()
    Image.new("RGB", (w, h), (20, 120, 200)).save(out, "JPEG")
    return out.getvalue()


class FakeCollage(BaseHTTPRequestHandler):
    calls = []
    mode = "ok"

    def do_GET(self):
        FakeCollage.calls.append((self.path, self.headers.get("Authorization")))
        if self.headers.get("Authorization") != f"Bearer {KEY}" or FakeCollage.mode == "denied":
            return self._send(401, b'{"error": "unauthorized"}', "application/json")
        if FakeCollage.mode == "missing":
            return self._send(404, b"{}", "application/json")
        path = self.path.split("?")[0]
        if path == "/api/internal/collages":
            body = {"source": "collage", "version": "0.9.1", "collages": [
                {"id": "old1", "name": "Frühling", "created_at": 1760000000, "updated_at": 1760000000, "photos": 12,
                 "template": "Raster", "format": "A4 hoch", "width": 2480, "height": 3508,
                 "thumb": "/api/internal/collages/old1/thumb?v=1", "image": "/api/internal/collages/old1/image?v=1"},
                {"id": "new2", "name": "Sommer 2026", "created_at": 1764000000, "updated_at": 1764003600, "photos": 24,
                 "template": "", "format": "A3 quer", "width": 4961, "height": 3508},
                {"id": "../evil", "name": "x"}, "kaputt",
            ]}
            return self._send(200, json.dumps(body).encode(), "application/json")
        if path in ("/api/internal/collages/new2/thumb", "/api/internal/collages/new2/image"):
            return self._send(200, jpeg(), "image/jpeg")
        if path == "/api/internal/collages/html/image":
            return self._send(200, b"<html>", "text/html")
        self._send(404, b"{}", "application/json")

    def _send(self, status, body, ctype):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def register(folder, services):
    path = os.path.join(folder, "family.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(services, f)
    return family.Register(path, "puzzle")


class CollageSourceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.http = ThreadingHTTPServer(("127.0.0.1", 0), FakeCollage)
        threading.Thread(target=cls.http.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.http.server_address[1]}/"
        cls.tmp = tempfile.mkdtemp()
        cls.reg = register(cls.tmp, [{"id": "puzzle", "name": "Puzzle", "url": "http://127.0.0.1:1/"},
                                     {"id": "collage", "name": "Collage", "url": cls.url}])

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown()
        cls.http.server_close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        FakeCollage.mode = "ok"
        FakeCollage.calls = []

    def test_off_without_key_or_service(self):
        self.assertFalse(CollageSource(self.reg, "").enabled())
        self.assertFalse(CollageSource(None, KEY).enabled())
        other = register(tempfile.mkdtemp(dir=self.tmp), [{"id": "collage", "name": "C", "url": self.url, "enabled": False}])
        self.assertFalse(CollageSource(other, KEY).enabled())
        self.assertEqual(CollageSource(self.reg, "").view(), {"enabled": False, "collages": []})
        self.assertEqual(FakeCollage.calls, [])

    def test_list(self):
        src = CollageSource(self.reg, KEY)
        self.assertTrue(src.enabled())
        view = src.view()
        self.assertTrue(view["enabled"])
        self.assertEqual(view["new"], self.url.rstrip("/") + "/?new=1")
        self.assertEqual([c["id"] for c in view["collages"]], ["new2", "old1"])  # neueste zuerst, kaputte weg
        c = view["collages"][0]
        self.assertEqual(c["thumb"], "/api/collages/new2/thumb?v=1764003600")  # über Puzzle, nie direkt
        self.assertEqual(c["image"], "/api/collages/new2/image?v=1764003600")
        self.assertEqual(c["open"], self.url.rstrip("/") + "/?collage=new2")
        self.assertEqual((c["photos"], c["format"], c["width"]), (24, "A3 quer", 4961))
        self.assertEqual(FakeCollage.calls[0], ("/api/internal/collages", f"Bearer {KEY}"))

    def test_errors_become_hints(self):
        self.assertEqual(CollageSource(self.reg, "wrong").view()["error"], "denied")
        FakeCollage.mode = "missing"
        self.assertEqual(CollageSource(self.reg, KEY).view()["error"], "missing")
        down = register(tempfile.mkdtemp(dir=self.tmp), [{"id": "collage", "name": "C", "url": "http://127.0.0.1:1/"}])
        view = CollageSource(down, KEY, timeout=2).view()
        self.assertEqual((view["error"], view["collages"]), ("down", []))

    def test_file(self):
        src = CollageSource(self.reg, KEY)
        data, ctype = src.file("new2", "image")
        self.assertEqual(ctype, "image/jpeg")
        self.assertTrue(data.startswith(b"\xff\xd8"))
        with self.assertRaises(ValueError):
            src.file("../x", "image")
        with self.assertRaises(ValueError):
            src.file("new2", "raw")
        with self.assertRaises(CollageError) as e:
            src.file("html", "image")  # nur Bilder weiterreichen
        self.assertEqual(e.exception.code, "bad")
        with self.assertRaises(CollageError) as e:
            src.file("nope", "thumb")
        self.assertEqual(e.exception.code, "missing")


class CollageRoutesTest(unittest.TestCase):
    """Admin von Puzzle: /api/collages… mit Collage; Kiosk: nichts davon (Roger 25.11.2026)."""

    @classmethod
    def setUpClass(cls):
        cls.fake = ThreadingHTTPServer(("127.0.0.1", 0), FakeCollage)
        threading.Thread(target=cls.fake.serve_forever, daemon=True).start()
        cls.tmp = tempfile.mkdtemp()
        reg = register(cls.tmp, [{"id": "collage", "name": "Collage", "url": f"http://127.0.0.1:{cls.fake.server_address[1]}/"}])
        cls.app = server.App("", "", os.path.join(cls.tmp, "data"), os.path.join(cls.tmp, "puzzles"),
                             os.path.join(cls.tmp, "music"), share_port=1, shares_api_key=KEY)
        cls.kapp = kiosk.KioskApp(os.path.join(cls.tmp, "kdata"), os.path.join(cls.tmp, "music"), share_port=1)
        cls.servers = [ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler(cls.app, None, reg)),
                       ThreadingHTTPServer(("127.0.0.1", 0), kiosk.make_guest_handler(cls.kapp)),
                       ThreadingHTTPServer(("127.0.0.1", 0), kiosk.make_admin_handler(cls.kapp, None))]
        for s in cls.servers:
            threading.Thread(target=s.serve_forever, daemon=True).start()
        cls.base, cls.guest, cls.kadmin = (f"http://127.0.0.1:{s.server_address[1]}" for s in cls.servers)

    @classmethod
    def tearDownClass(cls):
        for s in cls.servers + [cls.fake]:
            s.shutdown()
            s.server_close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def get(self, url):
        try:
            with urllib.request.urlopen(url) as r:
                return r.status, r.headers.get("Content-Type"), r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.headers.get("Content-Type"), e.read()

    def setUp(self):
        FakeCollage.mode = "ok"

    def test_admin(self):
        status, _, body = self.get(self.base + "/api/version")
        self.assertTrue(json.loads(body)["collage"])
        status, _, body = self.get(self.base + "/api/collages")
        data = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual(data["collages"][0]["id"], "new2")
        self.assertNotIn(KEY, body.decode())  # Schlüssel nie an den Browser
        status, ctype, body = self.get(self.base + "/api/collages/new2/image?v=1")
        self.assertEqual((status, ctype), (200, "image/jpeg"))
        self.assertEqual(self.get(self.base + "/api/collages/nope/thumb")[0], 404)
        self.assertEqual(self.get(self.base + "/api/collages/nope/image")[0], 404)  # gerade neu gezeichnet oder weg
        self.assertEqual(self.get(self.base + "/api/collages/a.b/thumb")[0], 400)

    def test_admin_collage_down(self):
        FakeCollage.mode = "denied"
        status, _, body = self.get(self.base + "/api/collages")
        self.assertEqual((status, json.loads(body)["error"]), (200, "denied"))
        self.assertEqual(self.get(self.base + "/api/collages/new2/image")[0], 502)

    def test_kiosk_has_no_collage(self):
        for base in (self.guest, self.kadmin):
            status, _, body = self.get(base + "/api/version")
            self.assertFalse(json.loads(body).get("collage"))
            self.assertEqual(self.get(base + "/api/collages")[0], 404)
            self.assertEqual(self.get(base + "/api/collages/new2/image")[0], 404)


if __name__ == "__main__":
    unittest.main()
