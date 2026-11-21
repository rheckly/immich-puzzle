"""Attrappe von Immich 3.x für Tests und Vorführung: erzeugt farbige Testfotos, keine echten Bilder.

Eigenständig starten: python tests/fake_immich.py [Port] [Anzahl Fotos]
"""
import colorsys
import io
import json
import os
import random
import sys
import threading
import uuid
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from PIL import Image, ImageDraw


class FakeImmich:
    def __init__(self, count=300, page_size=100):
        self.page_size = page_size
        self.requests = {"thumbnail": 0, "preview": 0, "fullsize": 0}
        self.lock = threading.Lock()
        rng = random.Random(7)
        self.assets = []
        for i in range(count):
            self.assets.append({"id": str(uuid.UUID(int=rng.getrandbits(128), version=4)), "type": "IMAGE",
                                "hue": rng.random(), "sat": rng.uniform(0.1, 1), "val": rng.uniform(0.1, 1),
                                "seed": i, "portrait": rng.random() < 0.25})
        self.video = {"id": str(uuid.UUID(int=rng.getrandbits(128), version=4)), "type": "VIDEO"}
        self.gone = set()
        self.tags = {}  # Name -> {"id": ..., "assets": set()}
        self.denied = set()  # Rechte, die dem API-Schlüssel fehlen
        self.uploads = []  # Hochgeladene Dateien: {"id", "fields", "body"}
        self.album_assets = {}  # Album-ID -> Liste der hinzugefügten Fotos
        self.new_albums = {}  # Album-ID -> Name
        self.needs_device = False  # Wie ältere Immich-Versionen beim Hochladen
        self.library_dir = None  # Ordner der External Library, aus Sicht von Immich /mnt/lib
        self.library_assets = {}  # Pfad in Immich -> Asset-ID
        self.scans = 0
        self.scan_works = True
        self.patched = {}  # Album-ID -> geänderte Werte
        self.deleted_albums = []
        self.real_originals = False  # True: Originale sind echte JPEG-Bilder, sonst nur ein kurzer Text
        self.faces_denied = False
        third = count // 3
        self.album_id = str(uuid.UUID(int=1, version=4))
        self.person_id = str(uuid.UUID(int=2, version=4))
        self.album = {a["id"] for a in self.assets[:third]}
        self.person = {a["id"] for a in self.assets[third:2 * third]}

    def picture(self, asset, long_side):
        w, h = (long_side, long_side * 3 // 4)
        if asset["portrait"]:
            w, h = h, w
        base = tuple(int(c * 255) for c in colorsys.hsv_to_rgb(asset["hue"], asset["sat"], asset["val"]))
        img = Image.new("RGB", (w, h), base)
        draw = ImageDraw.Draw(img)
        rng = random.Random(asset["seed"])
        for _ in range(5):  # etwas Struktur, damit die Kacheln wie Fotos wirken
            shade = tuple(max(0, min(255, c + rng.randint(-45, 45))) for c in base)
            x, y, r = rng.random() * w, rng.random() * h, w * (0.1 + rng.random() * 0.23)  # in jeder Grösse gleich
            draw.ellipse((x - r, y - r, x + r, y + r), fill=shade)
        return img

    def info(self, asset):
        """Wie in den Suchergebnissen von Immich: Datum, Favorit und Grösse."""
        w, h = (4000, 3000) if not asset["portrait"] else (3000, 4000)
        taken = datetime(2019, 1, 1, 10, 0) + timedelta(days=asset["seed"] * 5)
        return {"id": asset["id"], "type": "IMAGE", "isFavorite": asset["seed"] % 7 == 0, "width": w, "height": h,
                "localDateTime": taken.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                "fileCreatedAt": taken.strftime("%Y-%m-%dT%H:%M:%S.000Z")}

    def faces(self, asset):
        """Ein Gesicht oben rechts bei jedem zweiten Foto, sonst keines."""
        if asset["seed"] % 2:
            return []
        w, h = (4000, 3000) if not asset["portrait"] else (3000, 4000)
        return [{"id": str(uuid.uuid4()), "imageWidth": w, "imageHeight": h, "boundingBoxX1": int(w * 0.7),
                 "boundingBoxY1": int(h * 0.1), "boundingBoxX2": int(w * 0.85), "boundingBoxY2": int(h * 0.3)}]

    def scan(self):
        self.scans += 1
        if not self.library_dir or not self.scan_works:
            return
        seen = set()
        for folder, dirs, files in os.walk(self.library_dir):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for name in files:
                if not name.startswith(".") and name.endswith(".jpg"):
                    rel = os.path.relpath(os.path.join(folder, name), self.library_dir).replace(os.sep, "/")
                    seen.add(f"/mnt/lib/{rel}")
        for path in seen - set(self.library_assets):
            self.library_assets[path] = str(uuid.uuid4())
        for path in set(self.library_assets) - seen:
            del self.library_assets[path]

    def search(self, body):
        flt = body.get("filter", {})
        if flt.get("originalPath"):
            prefix = flt["originalPath"]["startsWith"]
            return {"assets": {"items": [{"id": a, "originalPath": p, "type": "IMAGE"} for p, a in self.library_assets.items()
                                         if p.startswith(prefix)], "nextCursor": None}}
        items = [self.info(a) for a in self.assets]
        if flt.get("tagIds"):
            wanted = flt["tagIds"]["any"]
            tagged = {a for t in self.tags.values() if t["id"] in wanted for a in t["assets"]}
            return {"assets": {"items": [i for i in items if i["id"] in tagged], "nextCursor": None}}
        if flt.get("albumIds"):
            items = [i for i in items if i["id"] in self.album]
        if flt.get("personIds"):
            items = [i for i in items if i["id"] in self.person]
        if not flt.get("albumIds") and not flt.get("personIds"):
            items.append({"id": self.video["id"], "type": "VIDEO"})
        start = int(body.get("cursor") or 0)
        end = start + self.page_size
        return {"assets": {"items": items[start:end], "nextCursor": str(end) if end < len(items) else None}}

    def tag(self, name, assets=()):
        """Tag anlegen und Fotos damit versehen."""
        tag = self.tags.setdefault(name, {"id": str(uuid.uuid4()), "assets": set()})
        tag["assets"].update(assets)
        return tag

    def start(self, port=0):
        fake = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def _send(self, status, body, ctype="application/json"):
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _json(self, data):
                self._send(200, json.dumps(data).encode())

            def do_POST(self):
                raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                if self.path == "/api/assets":
                    if "asset.upload" in fake.denied:
                        return self._send(403, b"{}")
                    if fake.needs_device and b'name="deviceId"' not in raw:
                        return self._send(400, b"{}")
                    asset = str(uuid.uuid4())
                    fake.uploads.append({"id": asset, "body": raw})
                    return self._json({"id": asset, "status": "created"})
                body = json.loads(raw or b"{}")
                if self.path.startswith("/api/libraries/") and self.path.endswith("/scan"):
                    if "library.update" in fake.denied:
                        return self._send(403, b"{}")
                    fake.scan()
                    return self._send(204, b"")
                if self.path == "/api/albums":
                    if "album.create" in fake.denied:
                        return self._send(403, b"{}")
                    album = str(uuid.uuid4())
                    fake.new_albums[album] = body["albumName"]
                    return self._json({"id": album, "albumName": body["albumName"]})
                if self.path == "/api/search/metadata":
                    self._json(fake.search(body))
                else:
                    self._send(404, b"{}")

            def _tag_write(self, add):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                parts = self.path.strip("/").split("/")
                if parts[:2] == ["api", "albums"] and len(parts) == 4 and parts[3] == "assets":
                    known = {a["id"] for a in fake.assets} | {u["id"] for u in fake.uploads} | set(fake.library_assets.values())
                    added = fake.album_assets.setdefault(parts[2], [])
                    results = []
                    for asset in body["ids"]:
                        if asset in fake.gone or asset not in known:
                            results.append({"id": asset, "success": False, "error": "not_found"})
                        elif asset in added:
                            results.append({"id": asset, "success": False, "error": "duplicate"})
                        else:
                            added.append(asset)
                            results.append({"id": asset, "success": True})
                    return self._json(results)
                if parts[:2] == ["api", "tags"] and len(parts) == 2:
                    if "tag.create" in fake.denied:
                        return self._send(403, b"{}")
                    return self._json([{"id": fake.tag(n)["id"], "value": n} for n in body["tags"]])
                if parts[:2] == ["api", "tags"] and len(parts) == 4 and parts[3] == "assets":
                    if "tag.asset" in fake.denied:
                        return self._send(403, b"{}")
                    tag = next((t for t in fake.tags.values() if t["id"] == parts[2]), None)
                    if not tag:
                        return self._send(400, b"{}")
                    known = ({a["id"] for a in fake.assets} | {u["id"] for u in fake.uploads} | set(fake.library_assets.values())) - fake.gone
                    results = []
                    for asset in body["ids"]:
                        if asset not in known or (not add and asset not in tag["assets"]):
                            results.append({"id": asset, "success": False, "error": "not_found"})
                        elif add and asset in tag["assets"]:
                            results.append({"id": asset, "success": False, "error": "duplicate"})
                        else:
                            (tag["assets"].add if add else tag["assets"].discard)(asset)
                            results.append({"id": asset, "success": True})
                    return self._json(results)
                self._send(404, b"{}")

            def do_PUT(self):
                self._tag_write(True)

            def do_PATCH(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                parts = self.path.strip("/").split("/")
                if "album.update" in fake.denied:
                    return self._send(403, b"{}")
                fake.patched.setdefault(parts[2], {}).update(body)
                if "albumName" in body:
                    fake.new_albums[parts[2]] = body["albumName"]
                self._json({"id": parts[2]})

            def do_DELETE(self):
                parts = self.path.strip("/").split("/")
                if parts[:2] == ["api", "albums"] and len(parts) == 3:
                    self.rfile.read(int(self.headers.get("Content-Length") or 0))
                    if "album.delete" in fake.denied:
                        return self._send(403, b"{}")
                    if parts[2] not in fake.new_albums:
                        return self._send(400, b"{}")
                    del fake.new_albums[parts[2]]
                    fake.album_assets.pop(parts[2], None)
                    fake.deleted_albums.append(parts[2])
                    return self._send(204, b"")
                if parts[:2] == ["api", "albums"] and len(parts) == 4 and parts[3] == "assets":
                    body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                    added = fake.album_assets.get(parts[2], [])
                    for asset in body["ids"]:
                        if asset in added:
                            added.remove(asset)
                    return self._json([{"id": a, "success": True} for a in body["ids"]])
                self._tag_write(False)

            def do_GET(self):
                path, _, query = self.path.partition("?")
                if self.headers.get("x-api-key") != "test-key":
                    return self._send(401, b"{}")
                if path == "/api/server/version":
                    return self._json({"major": 1, "minor": 135, "patch": 3})
                if path == "/api/tags":
                    if "tag.read" in fake.denied:
                        return self._send(403, b"{}")
                    return self._json([{"id": t["id"], "name": n, "value": n} for n, t in fake.tags.items()])
                if path == "/api/albums" and "assetId=" in query:
                    asset = query.split("assetId=")[-1]
                    if asset in fake.gone:
                        return self._send(400, b'{"message":"Not found or no asset.read access"}')
                    return self._json([{"id": fake.album_id, "albumName": "Testalbum"}] if asset in fake.album else [])
                if path == "/api/albums":
                    shared = "shared" in query
                    return self._json([] if shared else [{"id": fake.album_id, "albumName": "Testalbum",
                                                          "assetCount": len(fake.album),
                                                          "startDate": "2019-03-02T10:00:00.000Z",
                                                          "endDate": "2024-08-20T10:00:00.000Z"}]
                                      + [{"id": a, "albumName": n, "assetCount": len(fake.album_assets.get(a, []))}
                                         for a, n in fake.new_albums.items()])
                if path == "/api/people":
                    return self._json({"people": [{"id": fake.person_id, "name": "Testperson"},
                                                  {"id": str(uuid.UUID(int=3, version=4)), "name": ""}],
                                       "hasNextPage": False})
                parts = path.strip("/").split("/")
                if len(parts) == 4 and parts[:2] == ["api", "people"] and parts[3] == "statistics":
                    if "person.statistics" in fake.denied:
                        return self._send(403, b"{}")
                    return self._json({"assets": 7 if parts[2] == fake.person_id else 0})
                if len(parts) == 4 and parts[:2] == ["api", "people"] and parts[3] == "thumbnail":
                    return self._send(200, b"\xff\xd8gesicht", "image/jpeg")
                if len(parts) == 3 and parts[:2] == ["api", "assets"]:
                    asset = next((a for a in fake.assets if a["id"] == parts[2]), None)
                    if not asset or asset["id"] in fake.gone:  # Antwort wie beim echten Immich
                        return self._send(400, b'{"message":"Not found or no asset.read access"}')
                    return self._json({"id": asset["id"], "originalPath": f"/usr/src/app/upload/Fotos/{asset['seed'] % 3}/bild-{asset['seed']}.jpg",
                                       "originalFileName": f"bild-{asset['seed']}.jpg", "localDateTime": fake.info(asset)["localDateTime"],
                                       "exifInfo": {"make": "Testkamera", "model": "T1", "city": "Bern", "country": "Schweiz",
                                                    "exifImageWidth": 4000, "exifImageHeight": 3000, "fileSizeInByte": 2500000},
                                       "people": [{"name": "Testperson"}, {"name": ""}],
                                       "tags": [{"id": t["id"], "value": n} for n, t in fake.tags.items()
                                                if asset["id"] in t["assets"]]})
                if path == "/api/faces":
                    if fake.faces_denied:
                        return self._send(403, b"{}")
                    asset = next((a for a in fake.assets if a["id"] == query.split("id=")[-1]), None)
                    if not asset or asset["id"] in fake.gone:
                        return self._send(400, b"{}")
                    return self._json(fake.faces(asset))
                if len(parts) == 4 and parts[:2] == ["api", "assets"] and parts[3] == "original":
                    if "asset.download" in fake.denied:
                        return self._send(403, b"{}")
                    if parts[2] in fake.gone:
                        return self._send(400, b"{}")
                    asset = next((a for a in fake.assets if a["id"] == parts[2]), None)
                    if fake.real_originals and asset:
                        out = io.BytesIO()
                        fake.picture(asset, 2000).save(out, "JPEG", quality=85)
                        return self._send(200, out.getvalue(), "image/jpeg")
                    return self._send(200, b"ORIGINAL-" + parts[2].encode(), "image/jpeg")
                if len(parts) == 4 and parts[:2] == ["api", "assets"] and parts[3] == "thumbnail":
                    size = query.split("size=")[-1]
                    asset = next((a for a in fake.assets if a["id"] == parts[2]), None)
                    if not asset or asset["id"] in fake.gone:
                        return self._send(400, b'{"message":"Not found or no asset.view access"}')
                    with fake.lock:
                        fake.requests[size] += 1
                    out = io.BytesIO()
                    if size == "thumbnail":
                        fake.picture(asset, 330).save(out, "WEBP", quality=80)
                        ctype = "image/webp"
                    else:
                        fake.picture(asset, 1440).save(out, "JPEG", quality=85)
                        ctype = "image/jpeg"
                    return self._send(200, out.getvalue(), ctype)
                self._send(404, b"{}")

        self.server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self.server.server_address[1]

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


if __name__ == "__main__":
    fake = FakeImmich(count=int(sys.argv[2]) if len(sys.argv) > 2 else 1500)
    fake.real_originals = True  # Für die Vorführung: Puzzles brauchen echte Vorschaubilder
    print("Attrappe auf Port", fake.start(int(sys.argv[1]) if len(sys.argv) > 1 else 2283), flush=True)
    threading.Event().wait()
