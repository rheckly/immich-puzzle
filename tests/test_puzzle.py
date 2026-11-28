"""Grundtest von Puzzle: Server mit Immich-Attrappe, Puzzle speichern, Live-Link mit Passwort, Download und Musik."""
import base64
import datetime
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
        for v in (None, "", "mid"):  # leer, null oder alt: Mitte, auch bei der Grussbotschaft (0.1.9)
            c = server.clean_config({"textPos": v, "greetPos": v})
            self.assertEqual((c["textPos"], c["greetPos"]), ("mc", "mc"))
        with self.assertRaises(ValueError):
            server.clean_config({"greetPos": "xx"})
        self.assertEqual(server.clean_config({})["ghostPct"], 15)  # Deckkraft des Motivs beim Start (E1)
        self.assertEqual(server.clean_config({"ghost": True, "ghostPct": 40})["ghostPct"], 40)
        # Bogen und Drehen (C1, C2), Grussbotschaft auf dem leeren Tisch (C3)
        c = server.clean_config({})
        self.assertEqual((c["textArc"], c["textRot"], c["greet"], c["greetFont"], c["greetPos"], c["greetColor"]), (0, 0, "", "hand", "mc", "#ffffff"))
        c = server.clean_config({"greet": "Hallo  Welt", "greetArc": -40, "greetRot": 30, "textArc": 100, "textRot": -45, "greetColor": "#ff8800"})
        self.assertEqual((c["greet"], c["greetArc"], c["greetRot"], c["textArc"], c["textRot"], c["greetColor"]), ("Hallo Welt", -40, 30, 100, -45, "#ff8800"))
        for bad in ({"count": 7}, {"format": "5:4"}, {"T": 999}, {"hold": 1}, {"hold": 11}, {"tempo": 3},
                    {"frameAt": 10}, {"ramp": 61}, {"textSize": 300}, {"textFont": "x"}, {"textPos": "top-left"},
                    {"textColor": "red"}, {"textColor": "#12345"}, {"count": True}, {"music": "x"}, {"ghostPct": 4}, {"ghostPct": 61},
                    {"textArc": 101}, {"textRot": -46}, {"greetArc": -101}, {"greetRot": 46}, {"greet": 5}, {"greetColor": "gold"},
                    {"greetFont": "x"}, {"greetSize": 20}):
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

    def test_card_from_editor(self):
        """Das Startbild aus dem Editor wird das Vorschaubild; Unbrauchbares wird übergangen."""
        p = self.new_puzzle()
        card = lambda: Image.open(io.BytesIO(self.call("GET", f"{self.base}/api/puzzles/{p['id']}/card.jpg", raw=True)[1]))
        status, _ = self.call("POST", f"{self.base}/api/puzzles/{p['id']}", {"config": p["config"], "card": photo_url(1200, 630).replace("jpeg", "png", 1)})
        self.assertEqual(status, 200)
        img = card()
        self.assertEqual(img.size, (1200, 630))
        self.assertGreater(img.convert("RGB").getpixel((5, 5))[0], 150)  # die Farbe des gesendeten Bildes, kein Server-Puzzle
        status, _ = self.call("POST", f"{self.base}/api/puzzles/{p['id']}", {"config": p["config"], "card": photo_url(600, 600)})
        self.assertEqual(card().size, (1200, 630))  # auf das Vorschauformat zugeschnitten
        for bad in ("x", "data:image/png;base64,AAAA", 5):
            self.assertIsNone(server.decode_card(bad), bad)
        status, _ = self.call("POST", f"{self.base}/api/puzzles/{p['id']}", {"config": p["config"], "card": "kaputt"})
        self.assertEqual(status, 200)

    def test_set_active_off_since(self):
        link = {}
        server.set_active(link, False)
        first = link["off_since"]
        self.assertIs(link["active"], False)
        server.set_active(link, False)  # erneut aus: «seit» bleibt
        self.assertEqual(link["off_since"], first)
        server.set_active(link, True)
        self.assertEqual(link, {})

    def test_protocol(self):
        """Protokoll (0.1.8): erstellen, ändern (zusammengefasst), Link neu/geändert/aufgerufen/gelöscht, Smart Albums"""
        start = len(self.app.log)
        p = self.new_puzzle(title="Protokoll")
        pid = p["id"]
        for title in ("A", "B"):  # zweimal speichern = ein Eintrag «geändert»
            self.call("POST", f"{self.base}/api/puzzles/{pid}", {"config": {"count": 48, "format": "3:2", "title": title}})
        _, linked = self.call("POST", f"{self.base}/api/puzzles/{pid}/link", {"days": 30})
        self.call("POST", f"{self.base}/api/puzzles/{pid}/link", {"download": True})
        path = linked["link"]["url"].split("links.example.com", 1)[1]
        views = lambda: sum(e["what"] == "linkView" for e in self.app.log)
        before = views()
        self.call("GET", self.sbase + path, raw=True)
        for _ in range(100):  # der Aufruf zählt erst nach der Antwort (eigener Thread des Servers)
            if views() > before:
                break
            time.sleep(.02)
        server.set_actor("smart", "Smart Albums")
        try:
            self.app.patch_share(self.app.puzzles[pid]["online_link"]["token"], {"active": False}, "https://app")
        finally:
            server.set_actor("system")
        self.call("DELETE", f"{self.base}/api/puzzles/{pid}/link")
        status, log = self.call("GET", self.base + "/api/protocol")
        self.assertEqual(status, 200, log)
        self.assertEqual(log["keep"], 2000)
        mine = list(reversed(log["entries"]))[start:]
        whats = [e["what"] for e in mine]
        self.assertEqual(whats, ["puzzleNew", "puzzleEdit", "linkNew", "linkEdit", "linkView", "linkEdit", "linkDelete"], whats)
        self.assertEqual(mine[-4]["vars"]["changes"], ["download:on"])
        self.assertEqual(mine[-2]["vars"]["changes"], ["active:off"])
        self.assertEqual((mine[-2]["role"], mine[-2]["who"]), ("smart", "Smart Albums"))
        self.assertEqual(mine[-3]["role"], "visitor")
        self.assertEqual(mine[-1]["role"], "admin")
        with open(os.path.join(self.tmp, "data", "protocol.jsonl"), encoding="utf-8") as f:
            self.assertEqual(len(f.read().splitlines()), len(log["entries"]))

    def test_live_link(self):
        p = self.new_puzzle()
        status, p = self.call("POST", f"{self.base}/api/puzzles/{p['id']}/link", {"download": False, "days": 30})
        self.assertEqual(status, 200, p)
        link = p["link"]
        self.assertTrue(link["url"].startswith("https://links.example.com/puzzles/"))
        # D2: jeder Link hat ein «Gültig ab» (Standard heute), «bis» zählt ab diesem Tag
        today = datetime.date.today()
        self.assertEqual(datetime.date.fromtimestamp(link["valid_from"]), today)
        self.assertEqual(datetime.date.fromtimestamp(link["expires_at"]), today + datetime.timedelta(days=30))
        start = today + datetime.timedelta(days=10)
        _, moved = self.call("POST", f"{self.base}/api/puzzles/{p['id']}/link", {"from": start.isoformat()})
        self.assertEqual(datetime.date.fromtimestamp(moved["link"]["expires_at"]), start + datetime.timedelta(days=30))
        self.call("POST", f"{self.base}/api/puzzles/{p['id']}/link", {"from": today.isoformat()})
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

    def test_shares_view_days(self):
        """Zugriffs-Statistik: die Link-Liste liefert die Tageswerte, neueste zuerst, höchstens das Total (0.1.8)"""
        p = self.new_puzzle()
        self.call("POST", f"{self.base}/api/puzzles/{p['id']}/link", {"days": 30})
        today = time.strftime("%Y-%m-%d")
        old = time.strftime("%Y-%m-%d", time.localtime(time.time() - 400 * 86400))
        with self.app.lock:
            link = self.app.puzzles[p["id"]]["online_link"]
            link["views"] = 7
            link["view_days"] = {"2026-01-02": 2, today: 3, old: 9}
        status, res = self.call("GET", self.base + "/api/shares")
        entry = next(s for s in res["shares"] if s["id"] == p["id"])
        days = entry["view_days"]
        self.assertEqual(entry["views"], 7)
        self.assertEqual(days[0], {"date": today, "views": 3})
        self.assertNotIn(old, [d["date"] for d in days])  # älter als ein Jahr fällt weg
        self.assertLessEqual(sum(d["views"] for d in days), 7)

    def test_like(self):
        """Like auf der Seite der Empfänger: ein Like pro Besucher, zurücknehmbar, Zahl für alle (0.1.8)"""
        p = self.new_puzzle()
        status, p = self.call("POST", f"{self.base}/api/puzzles/{p['id']}/link", {"days": 30})
        url = self.sbase + p["link"]["path"]
        a, b = {"X-Visitor": "aaaaaaaaaaaa"}, {"X-Visitor": "bbbbbbbbbbbb"}
        self.assertEqual(self.call("POST", url + "like", {"on": True}, a), (200, {"likes": 1, "liked": True}))
        self.assertEqual(self.call("POST", url + "like", {"on": True}, a)[1], {"likes": 1, "liked": True})  # nur einmal
        self.assertEqual(self.call("POST", url + "like", {"on": True}, b)[1], {"likes": 2, "liked": True})
        status, view = self.call("GET", url + "puzzle.json", headers=a)
        self.assertEqual((view["likes"], view["liked"]), (2, True))
        status, view = self.call("GET", url + "puzzle.json")
        self.assertEqual((view["likes"], view["liked"]), (2, False))
        self.assertEqual(self.call("POST", url + "like", {"on": False}, a)[1], {"likes": 1, "liked": False})
        self.assertEqual(self.call("POST", url + "like", {"on": True}, {"X-Visitor": "x"})[0], 404)  # ungültige Kennung
        stored = self.app.puzzles[p["id"]]["online_link"]["likers"]
        self.assertNotIn("bbbbbbbbbbbb", json.dumps(stored))  # nur Hash
        status, res = self.call("GET", self.base + "/api/shares")
        self.assertEqual(next(s for s in res["shares"] if s["id"] == p["id"])["likes"], 1)
        entry = self.app._internal_entry(self.app.puzzles[p["id"]], "https://app")  # Shares-API (Entscheide 78, 79)
        self.assertEqual(entry["likes"], 1)
        self.assertEqual([d["likes"] for d in entry["likeDays"]], [2])  # gesetzte Likes, Zurücknehmen zieht nicht ab
        self.assertNotIn("likers", json.dumps(entry))
        log = [e for e in self.app.protocol_entries()["entries"] if e["what"] in ("likeOn", "likeOff")]  # Entscheid 78
        self.assertEqual([e["what"] for e in log], ["likeOff", "likeOn", "likeOn"])
        self.assertTrue(all(e["role"] == "visitor" and e.get("item") == "Ferien" for e in log))
        self.assertNotIn("bbbbbbbbbbbb", json.dumps(log))  # keine Kennung im Protokoll
        self.app.patch_share(self.app.puzzles[p["id"]]["online_link"]["token"], {"active": False}, "https://app")
        self.assertEqual(self.call("POST", url + "like", {"on": True}, a)[0], 403)  # ausgeschaltet

    def test_shares_api_flags(self):
        """Shares-API (0.1.17): download = Foto, video und share getrennt, Standard aus, Smart darf alle drei ändern"""
        p = self.new_puzzle()
        self.call("POST", f"{self.base}/api/puzzles/{p['id']}/link", {"days": 30})
        token = self.app.puzzles[p["id"]]["online_link"]["token"]
        entry = self.app._internal_entry(self.app.puzzles[p["id"]], "https://app")
        self.assertEqual((entry["download"], entry["video"], entry["share"]), (False, False, False))
        self.assertTrue({"download", "video", "share"} <= set(entry["actions"]))
        entry = self.app.patch_share(token, {"video": True, "share": True}, "https://app")
        self.assertEqual((entry["download"], entry["video"], entry["share"]), (False, True, True))
        with self.assertRaises(server.FieldError) as err:
            self.app.patch_share(token, {"share": "ja"}, "https://app")
        self.assertEqual(err.exception.field, "share")
        status, res = self.call("GET", self.base + "/api/shares")
        mine = next(s for s in res["shares"] if s["id"] == p["id"])
        self.assertEqual((mine["video"], mine["share"]), (True, True))
        status, res = self.call("POST", f"{self.base}/api/puzzles/{p['id']}/link", {"share": False})
        self.assertFalse(self.app.puzzles[p["id"]]["online_link"]["share"])

    def test_shares_api_preview(self):
        """Shares-API: Vorschaubild nur bei abrufbaren Links, sonst preview None (wie Mosaik, 0.1.8)"""
        p = self.new_puzzle()
        self.call("POST", f"{self.base}/api/puzzles/{p['id']}/link", {"days": 30})
        entry = lambda: self.app._internal_entry(self.app.puzzles[p["id"]], "https://app")
        with self.app.lock:
            link = self.app.puzzles[p["id"]]["online_link"]
            self.assertTrue(entry()["preview"].endswith("card.jpg"))
            for change in ({"protected": True}, {"active": False}, {"expires": time.time() - 10},
                           {"valid_from": time.time() + 86400}):
                saved = dict(link); link.update(change)
                self.assertIsNone(entry()["preview"], change)
                link.clear(); link.update(saved)
            self.assertTrue(entry()["preview"].endswith("card.jpg"))

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

    def test_details_defaults_made_with(self):
        """Puzzle Details, Standard für neue Puzzles und «Erstellt mit» (0.1.8)"""
        p = self.new_puzzle()
        pid = p["id"]
        steps = {"crop": False, "music": True, "puzzle": False, "dur": True, "text": False, "view": False}
        status, p = self.call("POST", f"{self.base}/api/puzzles/{pid}",
                              {"config": p["config"], "forWhom":"  Grosi   80 ", "note": "nur intern\r\n", "steps": steps})
        self.assertEqual(status, 200, p)
        status, p = self.call("GET", f"{self.base}/api/puzzles/{pid}")
        self.assertEqual((p["note"], p["steps"]), ("nur intern", steps))
        status, listing = self.call("GET", self.base + "/api/puzzles")
        self.assertEqual(next(x for x in listing["puzzles"] if x["id"] == pid)["forWhom"], "Grosi 80")
        status, err = self.call("POST", f"{self.base}/api/puzzles/{pid}", {"config": p["config"], "steps": "alle"})
        self.assertEqual(status, 400, err)
        # Die Notiz kommt nie zu den Empfängern
        self.call("POST", f"{self.base}/api/puzzles/{pid}/link", {"days": 7})
        view = self.app.shared_view(pid)
        self.assertNotIn("note", json.dumps(view))
        self.assertNotIn("nur intern", json.dumps(view))
        self.assertEqual(view["year"], datetime.date.today().year)
        try:
            status, res = self.call("POST", self.base + "/api/settings/defaults",
                                    {"defaults": {"config": {"count": 24, "format": "1:1", "title": "X", "text": "Hallo"},
                                                  "steps": {"music": False}}})
            self.assertEqual(status, 200, res)
            d = res["defaults"]
            self.assertEqual((d["config"]["count"], d["config"]["format"]), (24, "1:1"))
            for key in ("title", "text", "seed"):
                self.assertNotIn(key, d["config"])
            self.assertEqual(d["steps"], {"crop": True, "music": False, "puzzle": True, "dur": True, "text": True, "view": True})
            status, listing = self.call("GET", self.base + "/api/puzzles")
            self.assertEqual(listing["defaults"], d)
            status, res = self.call("POST", self.base + "/api/settings/defaults", {"defaults": None})
            self.assertIsNone(res["defaults"])
            status, err = self.call("POST", self.base + "/api/settings/made-with", {"url": "javascript:alert(1)"})
            self.assertEqual((status, err.get("error_code")), (400, "urlInvalid"), err)
            status, res = self.call("POST", self.base + "/api/settings/made-with", {"url": "https://puzzle.example.org/"})
            self.assertEqual((status, res["madeWith"]), (200, "https://puzzle.example.org"), res)
            self.assertEqual(self.app.shared_view(pid)["madeWith"], "https://puzzle.example.org")
            status, res = self.call("POST", self.base + "/api/settings/made-with", {"url": "https://puzzle.example.org", "text": "  Example  /  Jane Doe "})
            self.assertEqual((status, res["madeWithText"], res["madeWith"]), (200, "Example / Jane Doe", "https://puzzle.example.org"), res)
            self.assertEqual(self.app.shared_view(pid)["madeWithText"], "Example / Jane Doe")
            # Copyright: fest aus settings.json, keine API zum Ändern
            self.assertEqual(self.app.shared_view(pid)["copyright"], "")
            self.app.settings = {**self.app.settings, "copyright": "  example.org /  Jane Doe "}
            self.assertEqual((self.app.shared_view(pid)["copyright"], self.app.app_settings()["copyright"]), ("example.org / Jane Doe",) * 2)
            status, res = self.call("POST", self.base + "/api/settings/made-with", {"url": "", "text": "", "copyright": "x"})
            self.assertEqual(res["copyright"], "example.org / Jane Doe")
            self.app.settings.pop("copyright")
        finally:
            self.call("POST", self.base + "/api/settings/defaults", {"defaults": None})
            status, res = self.call("POST", self.base + "/api/settings/made-with", {"url": "", "text": ""})
        self.assertEqual((res["madeWith"], res["madeWithText"]), ("", ""))
        whats = [e["what"] for e in self.app.log[-4:]]
        self.assertIn("madeWith", whats)
        self.assertIn("defaultsReset", whats)

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
                self.assertIn(f'grund: "{grund}"'.encode(), body)
                self.assertNotIn(b"Geheim", body)
                self.assertNotIn(b"og:image", body)
                self.assertNotIn(path.encode(), body)  # Umami und Seite nennen den Code nicht
                # Nur die Funktionsform maskiert URL und Referrer, auch beim Ereignis (Design-Vorlage Kap. 7)
                calls = body.split(b"umami.track(")[1:]
                self.assertEqual(len(calls), 2)
                for call in calls:
                    self.assertTrue(call.startswith(b"function (p)"), call[:40])
                    self.assertIn(b'p.url = "/puzzle-shared-link"', call)
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

    def test_umami_editor(self):
        # Editor: hp.js, keine automatische Zählung, genau ein Aufruf mit Adresse «/» (ohne Puzzle-ID und Panel)
        tmp = tempfile.mkdtemp()
        try:
            other = server.App("http://127.0.0.1:1", "test-key", os.path.join(tmp, "data"), os.path.join(tmp, "puzzles"), os.path.join(tmp, "music"),
                               umami_url="https://umami.example.com/", umami_id="site-9")
            tag = other.umami_tag
            self.assertIn('src="https://umami.example.com/hp.js"', tag)
            self.assertIn('data-auto-track="false"', tag)
            calls = tag.split("umami.track(")[1:]
            self.assertEqual(len(calls), 1)
            self.assertTrue(calls[0].startswith('function (p) { p.url = "/puzzle-editor";'), calls[0][:40])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_whole_box_opens_pane(self):
        """0.1.14: Klick auf die ganze Box wirkt wie [>]; das [>] bleibt der einzige Knopf im Kopf (keine verschachtelten Buttons)."""
        import re
        status, page = self.call("GET", self.base + "/", raw=True)
        self.assertEqual(status, 200)
        page = page.decode("utf-8")
        heads = re.findall(r'<div class="boxHead">(.*?)</div>', page)
        self.assertGreaterEqual(len(heads), 15)  # Menü links (13) und die zwei Grussbotschaft-Boxen in P5
        for head in heads:
            self.assertEqual(head.count("<button"), 1, head)
            self.assertIn('class="paneBtn"', head)
            self.assertNotIn("<button", head.split("</h2>")[0])  # Titel ohne Knopf darin
        self.assertIn("function boxLead(box)", page)
        for sel in (":scope > .boxHead > .paneBtn", ":scope > .clBoxHead > .paneBtn", ":scope .shareTitle > .paneBtn"):
            self.assertIn(sel, page)  # Boxen links und in P5, Änderungen unter «Über», Karten in «Geteilte Puzzles»
        self.assertIn("a,button,input,label,select,textarea,summary,[contenteditable]", page)  # Bedienelemente behalten ihren Klick
        self.assertIn('e.target.closest("section.box, .clBox, .shareRow")', page)
        self.assertIn("lead.click();", page)  # nochmals Klick schliesst wie beim [>]
        self.assertIn("section.box:has(> .boxHead > .paneBtn)", page)  # cursor:pointer und Hover
        # Kiosk-Admin baut die Box «Kiosk» im selben Muster (section.box > div.boxHead > button.paneBtn)
        with open(os.path.join(HERE, "..", "app", "kiosk-admin.js"), encoding="utf-8") as f:
            ka = f.read()
        self.assertIn('mk("section", "box"), head = mk("div", "boxHead")', ka)
        self.assertIn('mk("button", "paneBtn", ">")', ka)

    def test_classic_edges(self):
        """0.1.15: klassische Teile (gerade bis zur Nase) wählbar; Standard bleibt organisch, auch ohne «edges»."""
        self.assertEqual(server.clean_config({})["edges"], "round")
        self.assertEqual(server.clean_config({"edges": "classic"})["edges"], "classic")
        with self.assertRaises(ValueError):
            server.clean_config({"edges": "zigzag"})
        # Standard von vor 0.1.15 ohne «edges»: neue Puzzles nehmen die Form aus dem Editor
        self.assertNotIn("edges", server.clean_defaults({"config": {"count": 48}})["config"])
        self.assertEqual(server.clean_defaults({"config": {"edges": "round"}})["config"]["edges"], "round")
        # Umriss: Randteil aussen gerade, klassische Kante gerade bis u = .37, dann Nase nach aussen
        top = server._piece_outline(0, 0, 100, 100, (0, 1, 0, 0), "classic")
        self.assertEqual(top[:2], [(0, 0), (100, 0)])  # obere Kante ohne Nase: gerade
        right = [p for p in top if p[0] > 100]
        self.assertTrue(right and max(p[0] for p in right) > 125)  # Nase rechts ragt rund 28 % hinaus
        self.assertIn((100, 37.0), top)  # gerades Stück bis zum Hals
        self.assertEqual(len(server._piece_outline(1, 1, 100, 100, (1, -1, 1, -1), "round")), 1 + 4 * 32)
        card = server.puzzle_card(Image.new("RGB", server.CARD_SIZE, (120, 160, 200)), 7, "classic")
        self.assertEqual(card.size, server.CARD_SIZE)
        # Editor und Engine kennen beide Formen; Umschalter im Panel «Puzzle», Standard für neue Puzzles klassisch
        status, page = self.call("GET", self.base + "/", raw=True)
        page = page.decode("utf-8")
        self.assertIn('id="edgePick"', page)
        self.assertIn('edges: "round", rot: false', page)  # FACTORY: organisch
        self.assertIn('start: "scatter", edges: "round", seed', page)  # Editor-Startwert
        self.assertLess(page.index('data-e="round"'), page.index('data-e="classic"'))  # Organisch zuerst
        self.assertNotIn('edges: "classic"', page)
        self.assertIn('st.edges = c.edges || "round"', page)
        with open(os.path.join(HERE, "..", "app", "puzzle-engine.js"), encoding="utf-8") as f:
            eng = f.read()
        self.assertIn("classic: { a: .37, b: .63", eng)  # gleiche Zahlen wie EDGE_SEGS in server.py
        self.assertEqual(server.EDGE_SEGS["classic"][:2], (.37, .63))

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


class ServerTimeZoneTest(unittest.TestCase):
    """Entscheid 84: Die Seite bekommt die Zeitzone des Containers, damit Ablaufdaten in derselben Zone erscheinen."""

    def test_tz_meta(self):
        old = os.environ.get("TZ")
        try:
            os.environ["TZ"] = "Europe/Zurich"
            self.assertEqual(server.tz_meta(), '<meta name="tz" content="Europe/Zurich">')
            os.environ["TZ"] = '"><script>'
            self.assertEqual(server.tz_meta(), "")
            os.environ.pop("TZ")
            self.assertEqual(server.tz_meta(), "")
        finally:
            if old is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = old


class StatsTest(unittest.TestCase):
    """Statistik (0.1.30, Entscheide 86-93): /api/stats nur mit Login; works, storage und Downloads in der Shares-API."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        data = os.path.join(cls.tmp, "data")
        cls.app = server.App("", "", data, os.path.join(cls.tmp, "puzzles"), os.path.join(cls.tmp, "music"),
                             share_port=1, share_public_url="https://links.example.com/puzzles")
        cls.app.eager_photos = False  # Foto mit Schrift erst beim Download, nicht nebenher (sonst wechselt die Datei beim Lesen)
        cls.auth = server.login.Auth("admin", "pw-test-123", data)
        cls.http = ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler(cls.app, auth=cls.auth))
        cls.share = ThreadingHTTPServer(("127.0.0.1", 0), server.make_share_handler(cls.app))
        for s in (cls.http, cls.share):
            threading.Thread(target=s.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.http.server_address[1]}"
        cls.sbase = f"http://127.0.0.1:{cls.share.server_address[1]}"
        cls.cookie = {"Cookie": f"{server.login.COOKIE}={cls.auth.make_cookie()}"}

    @classmethod
    def tearDownClass(cls):
        for s in (cls.http, cls.share):
            s.shutdown()
            s.server_close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    call = PuzzleServerTest.call

    def new_puzzle(self, name):
        status, p = self.call("POST", self.base + "/api/puzzles", {"config": {"count": 24, "title": name}, "photo": photo_url(),
                                                                   "name": name}, self.cookie)
        self.assertEqual(status, 200, p)
        return p

    def test_stats_needs_login(self):
        status, body = self.call("GET", self.base + "/api/stats")
        self.assertEqual(status, 401)
        self.assertIsInstance(body, dict)
        status, body = self.call("GET", self.base + "/api/stats", headers=self.cookie)
        self.assertEqual(status, 200, body)
        self.assertEqual(body["keep"], server.VIEW_DAYS)
        self.assertEqual(body["today"], time.strftime("%Y-%m-%d"))
        for key in ("works", "storage", "shares", "source", "version"):
            self.assertIn(key, body)

    def test_works_storage_downloads(self):
        old = self.new_puzzle("Ohne Link")
        p = self.new_puzzle("Mit Link")
        status, p = self.call("POST", f"{self.base}/api/puzzles/{p['id']}/link", {"days": 30, "download": True}, self.cookie)
        self.assertEqual(status, 200, p)
        path = p["link"]["url"].split("links.example.com", 1)[1]
        for _ in range(2):  # gleicher Besucher: zählt einmal
            status, _ = self.call("GET", self.sbase + path + "download", raw=True)
            self.assertEqual(status, 200)
        status, _ = self.call("GET", self.sbase + path + "download", headers={"User-Agent": "WhatsApp/2.23"}, raw=True)
        self.assertEqual(status, 200)  # Bot: geliefert, aber nicht gezählt
        req = urllib.request.Request(self.sbase + path + "download", method="HEAD", headers={"User-Agent": "Mozilla/5.0 other"})
        urllib.request.urlopen(req).close()  # HEAD zählt nicht
        self.app._storage = None  # Zwischenspeicher leeren, sonst gilt der Stand vom ersten Test bis zu einer Stunde
        status, body = self.call("GET", self.base + "/api/stats", headers=self.cookie)
        self.assertEqual(status, 200, body)
        ids = [w["id"] for w in body["works"]]
        self.assertLess(ids.index(p["id"]), ids.index(old["id"]))  # neueste zuerst, auch ohne Link
        work = body["works"][ids.index(p["id"])]
        self.assertEqual(work["title"], "Mit Link")
        self.assertFalse(work["video"])
        self.assertGreater(work["bytes"], 0)
        self.assertTrue(work["created"].endswith("Z"))
        share = next(s for s in body["shares"] if s["work"] == p["id"])
        self.assertEqual(share["downloads"], 1)
        self.assertEqual(share["downloadDays"], [{"date": time.strftime("%Y-%m-%d"), "downloads": 1}])
        st = body["storage"]
        self.assertEqual(set(st), {"bytes", "files", "measured"})
        self.assertGreaterEqual(st["bytes"], sum(w.get("bytes", 0) for w in body["works"]))
        self.assertGreater(st["files"], 0)
        self.assertNotIn(self.tmp.replace("\\", "/"), json.dumps(body).replace("\\\\", "/"))  # keine Pfade
        # Zwischenspeicher: eine neue Datei zählt erst nach einer Stunde (oder fresh)
        with open(os.path.join(self.tmp, "data", "extra.bin"), "wb") as f:
            f.write(b"x" * 5000)
        self.assertEqual(self.app.storage_scan()[0]["bytes"], st["bytes"])
        self.assertEqual(self.app.storage_scan(fresh=True)[0]["bytes"], st["bytes"] + 5000)
        # Weiterer Besucher nach Ablauf des Fensters zählt wieder, gespeichert ohne IP
        self.assertTrue(self.app.count_download(p["id"], "10.0.0.9", "Mozilla/5.0", now=time.time() + server.VIEW_WINDOW + 1))
        link = self.app.puzzles[p["id"]]["online_link"]
        self.assertEqual(link["downloads"], 2)
        self.assertNotIn("10.0.0.9", json.dumps(link))


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

