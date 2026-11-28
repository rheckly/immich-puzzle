"""Familie der Immich-Dienste (app/family.py byte-gleich aus Mosaik): Status und Version je Dienst (Entscheide 53, 65).
Tests aus immich-mosaik/tests/test_family.py übernommen."""
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "app"), os.path.join(ROOT, "tests")]

import family  # noqa: E402


def write(folder, data, raw=False):
    path = os.path.join(folder, "family.json")
    with open(path, "w", encoding="utf-8") as f:
        f.write(data if raw else json.dumps(data))
    return path


def entry(sid, **more):
    return {"id": sid, "name": f"Immich {sid.title()}", "url": f"https://{sid}.immich.example.com/", "new_path": "/", **more}



class VersionsTest(unittest.TestCase):
    """Entscheid 53: Version je Dienst aus GET <url>/health, im Hintergrund, 60 s im Speicher"""

    @classmethod
    def setUpClass(cls):
        import http.server

        class Health(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                answers = {"/ok/health": (200, b'{"ok": true, "version": "0.7.4"}'), "/none/health": (200, b'{"ok": true}'),
                           "/bad/health": (200, b'{"ok": true, "version": "<b>1</b>"}'), "/html/health": (200, b"<html>"),
                           "/err/health": (500, b'{"version": "9.9.9"}')}
                if self.path == "/slow/health":
                    time.sleep(3)
                status, body = answers.get(self.path, (404, b""))
                try:
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(body)
                except OSError:
                    pass

            def log_message(self, *args):
                pass

        cls.http = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Health)
        cls.base = f"http://127.0.0.1:{cls.http.server_address[1]}"
        threading.Thread(target=cls.http.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown()
        cls.http.server_close()

    def test_fetch_health(self):
        # Entscheid 65: erreichbar ist 200 mit gültigem JSON, auch ohne oder mit ungültiger Version
        self.assertEqual(family.fetch_health(self.base + "/ok"), (True, "0.7.4"))
        self.assertEqual(family.fetch_health(self.base + "/none"), (True, None))
        self.assertEqual(family.fetch_health(self.base + "/bad"), (True, None))
        for path in ("/html", "/err", "/missing"):
            self.assertEqual(family.fetch_health(self.base + path), (False, None), path)
        self.assertEqual(family.fetch_health("http://127.0.0.1:1"), (False, None))

    def wait(self, versions, urls):
        for _ in range(100):
            known, pending = versions.get(urls)
            if not pending:
                return known
            time.sleep(0.05)
        self.fail("Abfrage läuft noch")

    def test_reachable(self):
        answers = {"a": (True, "1.0.0"), "b": (True, None), "c": (False, None)}
        versions = family.Versions(lambda url: answers[url], ttl=60)
        self.assertEqual(versions.reachable(["a", "b", "c"]), {})  # Noch keine Prüfung, kein Aufruf
        versions.get(["a", "b", "c"])
        self.assertEqual(self.wait(versions, ["a", "b", "c"]), {"a": "1.0.0"})
        self.assertEqual(versions.reachable(["a", "b", "c", "d"]), {"a": True, "b": True, "c": False})

    def test_register_view(self):
        folder = tempfile.mkdtemp()
        try:
            path = write(folder, [entry("mosaik"), {"id": "collage", "name": "Immich Collage", "url": self.base + "/ok"},
                                  {"id": "reels", "name": "Immich Reels", "url": self.base + "/none"},
                                  {"id": "puzzle", "name": "Immich Puzzle", "url": self.base + "/err"}])
            reg = family.Register(path, "mosaik", own_version="0.5.19", versions=family.Versions())
            first = reg.view()
            self.assertTrue(first["versionsPending"])
            self.assertEqual(first["services"][0]["version"], "0.5.19")  # Eigene sofort, ohne HTTP
            # Prüfung läuft. Der lokale Health-Server kann schon geantwortet haben, bevor view() reachable() liest:
            # dann steht dort bereits das Endergebnis, nie etwas anderes (gleiche Fassung wie Collage)
            for got, final in zip([s.get("status") for s in first["services"]], ["ok", "ok", "ok", "down"]):
                self.assertIn(got, (None, final))
            self.assertEqual(first["services"][0]["status"], "ok")  # Eigener Dienst sofort
            self.wait(reg.versions, [self.base + "/ok", self.base + "/none", self.base + "/err"])
            view = reg.view()
            self.assertNotIn("versionsPending", view)
            self.assertEqual([s.get("version") for s in view["services"]], ["0.5.19", "0.7.4", None, None])
            self.assertEqual([s.get("status") for s in view["services"]], ["ok", "ok", "ok", "down"])
            plain = family.Register(path, "mosaik").view()["services"]
            self.assertNotIn("version", plain[1])  # Ohne Versions keine
            self.assertEqual([s.get("status") for s in plain], ["ok", None, None, None])
        finally:
            shutil.rmtree(folder, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
