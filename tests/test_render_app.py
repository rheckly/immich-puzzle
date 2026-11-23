"""Video-Download (0.1.17) in server.py: Auftrag beim Link mit Download, Stand in puzzle.json, Route «video»,
Hauptschalter, Löschen mit dem Puzzle."""
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

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "app"))
sys.path.insert(0, HERE)

import kiosk  # noqa: E402
import server  # noqa: E402
from test_puzzle import photo_url  # noqa: E402


class RenderAppTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.tmp, "music"))
        self.app = server.App("", "", os.path.join(self.tmp, "data"), os.path.join(self.tmp, "puzzles"),
                              os.path.join(self.tmp, "music"), share_port=1)
        self.http = ThreadingHTTPServer(("127.0.0.1", 0), server.make_share_handler(self.app))
        threading.Thread(target=self.http.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.http.server_address[1]}"
        p = self.app.save_puzzle({"config": {"count": 24}, "photo": photo_url(), "name": "Sommer"}, None)
        self.pid = p["id"]
        self.queued = os.path.join(self.app.videos.queue, f"{self.pid}.json")

    def tearDown(self):
        self.http.shutdown()
        self.http.server_close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def get(self, rest):
        token = self.app._puzzle(self.pid)["online_link"]["token"]
        try:
            with urllib.request.urlopen(self.base + self.app.share_path(token) + rest) as r:
                return r.status, r.read(), r.headers
        except urllib.error.HTTPError as e:
            return e.code, e.read(), e.headers

    def finish(self):
        """Spielt den Renderer."""
        with open(self.queued, encoding="utf-8") as f:
            job = json.load(f)
        with open(os.path.join(self.app.videos.videos, f"{self.pid}.mp4"), "wb") as f:
            f.write(b"\0\0\0\x18ftypmp42")
        with open(os.path.join(self.app.videos.videos, f"{self.pid}.json"), "w") as f:
            json.dump({"hash": job["hash"], "state": "ready"}, f)
        os.remove(self.queued)
        return job

    def test_flow(self):
        self.app.online_link(self.pid, {"download": False})
        self.assertFalse(os.path.exists(self.queued))  # Hauptschalter aus (Standard)
        self.assertEqual(self.app.set_video(True)["enabled"], True)
        self.assertFalse(os.path.exists(self.queued))  # Download aus
        self.app.eager_photos = False
        self.app.online_link(self.pid, {"download": True})
        self.assertFalse(os.path.exists(self.queued))  # nur Foto: kein Video (getrennt seit 0.1.17)
        status, body, _ = self.get("puzzle.json")
        self.assertIsNone(json.loads(body)["video"])
        self.app.online_link(self.pid, {"video": True})
        self.assertTrue(os.path.exists(self.queued))
        status, body, _ = self.get("puzzle.json")
        self.assertEqual(json.loads(body)["video"], "pending")
        self.assertEqual(self.get("video")[0], 404)
        job = self.finish()
        self.assertEqual(job["title"], "Sommer")
        status, body, _ = self.get("puzzle.json")
        self.assertEqual(json.loads(body)["video"], "ready")
        status, body, headers = self.get("video")
        self.assertEqual((status, headers["Content-Type"]), (200, "video/mp4"))
        self.assertIn('filename="Sommer.mp4"', headers["Content-Disposition"])
        # Video-Schalter aus: Video weg
        self.app.online_link(self.pid, {"video": False})
        self.assertFalse(os.path.exists(os.path.join(self.app.videos.videos, f"{self.pid}.mp4")))
        self.assertEqual(self.get("video")[0], 404)

    def test_photo_download_with_text(self):
        from PIL import Image
        self.app.online_link(self.pid, {"download": False})
        status, _, _ = self.get("download")
        self.assertEqual(status, 404)  # Download aus
        self.app.online_link(self.pid, {"download": True})  # Hauptschalter aus: Foto trotzdem
        # gleich beim Einschalten im Hintergrund erstellt, nicht erst beim Herunterladen
        for _ in range(100):
            if [n for n in os.listdir(self.app.videos.videos) if n.endswith(".jpg")]:
                break
            time.sleep(.05)
        made = [n for n in os.listdir(self.app.videos.videos) if ".photo-" in n and n.endswith(".jpg")]
        self.assertEqual(len(made), 1)
        self.assertFalse(os.path.exists(self.queued))  # kein Video ohne Video-Schalter
        status, body, headers = self.get("download")
        self.assertEqual((status, headers["Content-Type"]), (200, "image/jpeg"))
        self.assertIn('filename="Sommer.jpg"', headers["Content-Disposition"])
        import io
        out = Image.open(io.BytesIO(body))
        with Image.open(os.path.join(self.app._folder(self.pid), "photo.jpg")) as src:
            self.assertEqual(out.width, src.width)
            self.assertGreater(out.height, src.height)  # Balken oben und unten
        cached = [n for n in os.listdir(self.app.videos.videos) if ".photo-" in n]
        self.assertEqual(cached, made)  # dieselbe Datei, nicht nochmals erstellt
        self.app.delete_puzzle(self.pid)
        self.assertFalse([n for n in os.listdir(self.app.videos.videos) if ".photo-" in n])

    def test_switch_off_and_delete(self):
        self.app.set_video(True)
        self.app.online_link(self.pid, {"video": True})
        self.finish()
        self.app.set_video(False)
        self.assertIsNone(self.app.videos.file(self.pid))
        self.assertFalse(self.app.app_settings()["video"]["enabled"])
        self.app.set_video(True)
        self.assertTrue(os.path.exists(self.queued))
        self.app.delete_puzzle(self.pid)
        self.assertFalse(os.path.exists(self.queued))

    def test_orphans_pruned_at_start(self):
        orphan = os.path.join(self.app.videos.videos, "0123456789abcdef.mp4")
        with open(orphan, "wb") as f:
            f.write(b"x")
        server.App("", "", os.path.join(self.tmp, "data"), os.path.join(self.tmp, "puzzles"), os.path.join(self.tmp, "music"))
        self.assertFalse(os.path.exists(orphan))


class RenderKioskTest(unittest.TestCase):
    """Kiosk-Gäste: «Video herunterladen erlauben» ohne Foto-Download, Gäste sehen nur an/aus, Aufräumjob."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.tmp, "music"))
        self.app = kiosk.KioskApp(os.path.join(self.tmp, "data"), os.path.join(self.tmp, "music"), share_port=1, enabled=True)
        self.app.set_video(True)
        p = self.app.guest_create({"config": {"count": 24}, "photo": photo_url(), "name": "Gast"}, "10.0.0.1")
        self.pid = p["id"]
        self.queued = os.path.join(self.app.videos.queue, f"{self.pid}.json")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_guest_video(self):
        link = self.app.guest_link(self.pid, {})["link"]
        self.assertEqual((link["download"], link["video"]), (False, False))
        self.assertFalse(os.path.exists(self.queued))
        link = self.app.guest_link(self.pid, {"video": True, "download": True})["link"]
        self.assertEqual((link["download"], link["video"]), (False, True))  # Foto bleibt gesperrt
        with open(self.queued, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["foot"], ["Erstellt mit Puzzle"])
        self.assertIs(self.app.status()["video"], True)  # Gäste: nur an/aus, keine Zahlen
        self.app.guest_link(self.pid, {"video": False})
        self.assertFalse(os.path.exists(self.queued))

    def test_clean_prunes(self):
        orphan = os.path.join(self.app.videos.videos, "0123456789abcdef.mp4")
        with open(orphan, "wb") as f:
            f.write(b"x")
        self.assertEqual(self.app.clean()["videos"], 1)
        self.assertFalse(os.path.exists(orphan))


if __name__ == "__main__":
    unittest.main()
