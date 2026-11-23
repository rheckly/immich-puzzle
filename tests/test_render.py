"""Video-Download (MP4): Aufträge, Stand, Hauptschalter und Aufräumen in app/render.py (Renderer selbst: render/)."""
import json
import os
import shutil
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "app"))

import render  # noqa: E402

PID = "c23904cd09a14d46bc4aa5efec628e83"
INFO = {"title": "Ferien", "meta": {"date": "2026-07-14T10:00:00", "place": "Bern"}, "kiosk": False,
        "made_with_text": "example.com", "copyright": "Roger", "year": 2026, "lang": "de"}


class RenderTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.music = os.path.join(self.tmp, "lib")
        self.up = os.path.join(self.tmp, "data", "music")
        os.makedirs(os.path.join(self.music, "Test"))
        os.makedirs(self.up)
        with open(os.path.join(self.music, "Test", "Ton.wav"), "wb") as f:
            f.write(b"RIFF")
        self.folder = os.path.join(self.tmp, "data", "puzzles", PID)
        os.makedirs(self.folder)
        with open(os.path.join(self.folder, "photo.jpg"), "wb") as f:
            f.write(b"jpg")
        self.v = render.Videos(os.path.join(self.tmp, "data"), self.music, self.up)
        self.v.set_enabled(True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def puzzle(self, **link):
        return {"config": {"count": 100, "T": 20, "music": {"id": "lib:Test/Ton.wav", "volume": .5, "fade": True}},
                "online_link": {"token": "t" * 20, "video": True, **link}}

    def queued(self):
        return os.path.join(self.v.queue, f"{PID}.json")

    def finish(self, job):
        """Spielt den Renderer: Video und Stand «ready» schreiben, Auftrag weg."""
        with open(os.path.join(self.v.videos, f"{PID}.mp4"), "wb") as f:
            f.write(b"\0" * 1000)
        with open(os.path.join(self.v.videos, f"{PID}.json"), "w") as f:
            json.dump({"hash": job["hash"], "state": "ready"}, f)
        os.remove(self.queued())

    def test_job_contents(self):
        self.assertEqual(self.v.sync(PID, self.puzzle(), self.folder, INFO), "pending")
        with open(self.queued(), encoding="utf-8") as f:
            job = json.load(f)
        self.assertEqual(job["title"], "Ferien")
        self.assertEqual(job["sub"], "Ein Puzzle-Gruss für dich · 14.07.2026 · Bern")
        self.assertEqual(job["foot"], ["Erstellt mit example.com", "© 2026 by Roger"])
        self.assertEqual(job["music"], {"src": "lib", "path": "Test/Ton.wav", "volume": .5, "fade": True})
        self.assertNotIn("music", job["config"])
        kiosk = self.v.job(PID, self.puzzle(), self.folder, {**INFO, "kiosk": True, "meta": None, "lang": "en"})
        self.assertEqual(kiosk["foot"], ["Made with Puzzle"])
        self.assertEqual(kiosk["sub"], "A puzzle greeting for you")

    def test_music_outside_folder_ignored(self):
        p = self.puzzle()
        p["config"]["music"]["id"] = "lib:../../etc/passwd"
        self.assertIsNone(self.v.job(PID, p, self.folder, INFO)["music"])
        p["config"]["music"]["id"] = "up:fehlt.mp3"
        self.assertIsNone(self.v.job(PID, p, self.folder, INFO)["music"])

    def test_ready_and_change(self):
        self.v.sync(PID, self.puzzle(), self.folder, INFO)
        with open(self.queued(), encoding="utf-8") as f:
            job = json.load(f)
        self.finish(job)
        self.assertEqual(self.v.sync(PID, self.puzzle(), self.folder, INFO), "ready")
        self.assertTrue(self.v.file(PID))
        # Titel geändert: altes Video weg, neuer Auftrag
        self.assertEqual(self.v.sync(PID, self.puzzle(), self.folder, {**INFO, "title": "Neu"}), "pending")
        self.assertIsNone(self.v.file(PID))
        self.assertTrue(os.path.isfile(self.queued()))

    def test_not_allowed_removes(self):
        self.v.sync(PID, self.puzzle(), self.folder, INFO)
        for p in (self.puzzle(video=False), self.puzzle(video=False, download=True), self.puzzle(active=False), self.puzzle(expires=time.time() - 1),
                  {"config": {}}):
            self.v.sync(PID, self.puzzle(), self.folder, INFO)
            self.assertIsNone(self.v.sync(PID, p, self.folder, INFO))
            self.assertFalse(os.path.exists(self.queued()))
        # nur der Video-Schalter zählt (seit 0.1.17 getrennt vom Foto)
        self.assertEqual(self.v.sync(PID, self.puzzle(download=False, video=True), self.folder, INFO), "pending")

    def test_master_switch(self):
        self.v.set_enabled(False)
        self.assertIsNone(self.v.sync(PID, self.puzzle(), self.folder, INFO))
        self.assertFalse(os.path.exists(self.queued()))
        self.v.set_enabled(True)
        self.assertEqual(self.v.state(PID, self.puzzle(), self.folder, INFO), "pending")

    def test_default_off(self):
        v = render.Videos(os.path.join(self.tmp, "other"), self.music, self.up)
        self.assertFalse(v.enabled())

    def test_queue_limit(self):
        for i in range(render.QUEUE_MAX):
            with open(os.path.join(self.v.queue, f"x{i:09d}.json"), "w") as f:
                f.write("{}")
        self.assertIsNone(self.v.sync(PID, self.puzzle(), self.folder, INFO))

    def test_bad_pid(self):
        self.assertIsNone(self.v.sync("../x", self.puzzle(), self.folder, INFO))
        self.assertIsNone(self.v.file("../x"))

    def test_prune(self):
        self.v.sync(PID, self.puzzle(), self.folder, INFO)
        with open(self.queued(), encoding="utf-8") as f:
            job = json.load(f)
        self.finish(job)
        orphan = os.path.join(self.v.videos, "deadbeefdeadbeef.mp4")
        with open(orphan, "wb") as f:
            f.write(b"x")
        self.assertEqual(self.v.prune([PID]), 1)
        self.assertFalse(os.path.exists(orphan))
        self.assertTrue(self.v.file(PID))
        self.assertEqual(self.v.overview()["videos"], 1)
        self.v.remove(PID)
        self.assertIsNone(self.v.file(PID))


class MotifTextTest(unittest.TestCase):
    """Schriftzug auf dem Motiv im Foto-Download, wie drawText() in puzzle-engine.js."""
    def image(self):
        from PIL import Image
        return Image.new("RGB", (800, 450), (40, 80, 160))

    def changed(self, img, box):
        from PIL import ImageChops
        return ImageChops.difference(img.crop(box), self.image().crop(box)).getbbox() is not None

    def test_modes(self):
        cfg = {"text": "Gute\nBesserung!", "textFont": "archivo", "textSize": 70, "textPos": "tc", "textColor": "#e53935"}
        for mode in ("in", "end"):
            img = render.draw_motif_text(self.image(), {**cfg, "textMode": mode})
            self.assertTrue(self.changed(img, (0, 0, 800, 225)), mode)  # oben Mitte
            self.assertFalse(self.changed(img, (0, 330, 800, 450)), mode)
        for off in ({**cfg, "textMode": "none"}, {**cfg, "textMode": "in", "text": "  "}, {}):
            self.assertFalse(self.changed(render.draw_motif_text(self.image(), off), (0, 0, 800, 450)))

    def test_position_arc_rotation_and_fallback_font(self):
        img = render.draw_motif_text(self.image(), {"text": "Hallo", "textMode": "in", "textFont": "hand", "textPos": "br",
                                                    "textArc": 60, "textRot": -20, "textColor": "#111111"})
        self.assertTrue(self.changed(img, (400, 225, 800, 450)))
        self.assertFalse(self.changed(img, (0, 0, 300, 200)))

    def test_photo_cache_follows_text(self):
        from PIL import Image
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        folder = os.path.join(tmp, "puzzles", PID)
        os.makedirs(folder)
        self.image().save(os.path.join(folder, "photo.jpg"))
        v = render.Videos(os.path.join(tmp, "data"), tmp, tmp)
        p = {"config": {"count": 24, "text": "Hallo", "textMode": "in"}}
        first = v.photo(PID, p, folder, INFO)
        self.assertEqual(v.photo(PID, p, folder, INFO), first)
        p["config"]["text"] = "Tschüss"
        second = v.photo(PID, p, folder, INFO)
        self.assertNotEqual(second, first)
        self.assertFalse(os.path.exists(first))
        p["config"]["T"] = 40  # Dauer, Musik usw. ändern das Foto nicht
        self.assertEqual(v.photo(PID, p, folder, INFO), second)
        with Image.open(second) as im:
            self.assertEqual(im.width, 800)


if __name__ == "__main__":
    unittest.main()
