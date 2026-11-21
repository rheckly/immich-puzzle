"""Schlanker Client für die Immich-API (Such-API ab Immich 3.2: filter + cursor)."""
import json
import os
import re
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone

from msg import Msg

UUID_RE = re.compile(r"^[0-9a-fA-F-]{36}$")
SEARCH_BASE = {"trashedAt": {"eq": None}, "visibility": {"in": ["timeline", "archive"]}}


# Immich meldet ein gelöschtes Foto je nach Aufruf mit 400 ("Not found or no access") oder 404
GONE_STATUS = (400, 404)


class ImmichError(Exception):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


def check_id(value):
    if not isinstance(value, str) or not UUID_RE.match(value):
        raise ValueError(Msg("invalidId", "Ungültige Immich-ID"))
    return value


class Immich:
    def __init__(self, url, key, timeout=120):
        self.url = url.rstrip("/")
        self.key = key
        self.timeout = timeout

    def api(self, method, path, body=None, raw=False, timeout=None):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.url + "/api" + path, data=data, method=method)
        req.add_header("x-api-key", self.key)
        req.add_header("Accept", "*/*" if raw else "application/json")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as r:
                content = r.read()
        except urllib.error.HTTPError as e:
            raise ImmichError(f"{method} {path}: HTTP {e.code}", e.code)
        except (urllib.error.URLError, OSError) as e:
            raise ImmichError(f"{method} {path}: {getattr(e, 'reason', e)}")
        if raw:
            return content
        return json.loads(content) if content else None

    def server_version(self):
        # Version des Immich-Servers, z. B. "v1.135.3". Kurze Wartezeit, das Panel «Über» soll nicht hängen
        v = self.api("GET", "/server/version", timeout=5) or {}
        return f"v{v.get('major', 0)}.{v.get('minor', 0)}.{v.get('patch', 0)}"

    def albums(self):
        """Eigene und geteilte Alben. «other»: von jemand anderem mit mir geteilt (Reiter «Geteilt mit»)"""
        try:
            me = (self.api("GET", "/users/me") or {}).get("id")
        except Exception:
            me = None
        seen = {}
        for query in ("", "?shared=true"):
            for a in self.api("GET", "/albums" + query):
                owner = a.get("ownerId") or (a.get("owner") or {}).get("id")
                seen[a["id"]] = {"id": a["id"], "name": a.get("albumName") or "(ohne Name)",
                                 "count": a.get("assetCount", 0),
                                 "start": a.get("startDate"), "end": a.get("endDate"),
                                 "other": bool(me and owner and owner != me),
                                 "owner": (a.get("owner") or {}).get("name") or ""}
        return sorted(seen.values(), key=lambda a: a["name"].lower())

    def people(self):
        people, page = [], 1
        while True:
            r = self.api("GET", f"/people?withHidden=false&size=500&page={page}")
            people += [{"id": p["id"], "name": p["name"]} for p in r.get("people", []) if p.get("name")]
            if not r.get("hasNextPage"):
                break
            page += 1
        return sorted(people, key=lambda p: p["name"].lower())

    def _search(self, extra, on_page=None):
        ids, cursor = set(), None
        while True:
            body = {"filter": {**SEARCH_BASE, **extra}, "size": 1000, "withExif": False}
            if cursor:
                body["cursor"] = cursor
            assets = self.api("POST", "/search/metadata", body).get("assets", {})
            page = {a["id"] for a in assets.get("items", []) if a.get("type", "IMAGE") == "IMAGE"}
            ids |= page
            if on_page:
                on_page(page)
            cursor = assets.get("nextCursor")
            if not cursor:
                return ids

    def photo_list(self, albums=(), people=(), limit=3000):
        """Fotos zur Auswahl des Motivs mit Datum, Favorit und Grösse. Höchstens limit Stück, neueste zuerst."""
        found = {}
        filters = ([{"albumIds": {"any": list(albums)}}] if albums else []) + \
                  ([{"personIds": {"any": list(people)}}] if people else [])
        for extra in filters:
            cursor = None
            while len(found) < limit:
                body = {"filter": {**SEARCH_BASE, **extra}, "size": 1000, "withExif": False}
                if cursor:
                    body["cursor"] = cursor
                assets = self.api("POST", "/search/metadata", body).get("assets", {})
                for a in assets.get("items", []):
                    if a.get("type", "IMAGE") == "IMAGE":
                        found[a["id"]] = {"id": a["id"], "fav": bool(a.get("isFavorite")),
                                          "date": (a.get("localDateTime") or a.get("fileCreatedAt") or "")[:19],
                                          "w": a.get("width") or 0, "h": a.get("height") or 0}
                cursor = assets.get("nextCursor")
                if not cursor:
                    break
        return sorted(found.values(), key=lambda p: p["date"], reverse=True)[:limit]

    def faces(self, asset_id):
        """Gesichter, die Immich auf dem Foto kennt. Leer, wenn der Schlüssel das nicht darf."""
        try:
            return self.api("GET", f"/faces?id={check_id(asset_id)}") or []
        except ImmichError as e:
            if e.status in (*GONE_STATUS, 401, 403):
                return []
            raise

    def person_count(self, person_id):
        return self.api("GET", f"/people/{check_id(person_id)}/statistics").get("assets", 0)

    def person_thumbnail(self, person_id):
        return self.api("GET", f"/people/{check_id(person_id)}/thumbnail", raw=True)

    def asset_ids(self, albums=(), people=(), on_page=None):
        """Fotos aus den gewählten Alben ODER mit den gewählten Personen; ohne Auswahl alle Fotos.
        on_page bekommt die IDs jeder geladenen Seite."""
        if not albums and not people:
            return self._search({}, on_page)
        ids = set()
        if albums:
            ids |= self._search({"albumIds": {"any": list(albums)}}, on_page)
        if people:
            ids |= self._search({"personIds": {"any": list(people)}}, on_page)
        return ids

    def original_path(self, asset_id):
        """Pfad der Originaldatei aus Sicht von Immich. None, wenn das Foto nicht mehr da ist."""
        try:
            return self.api("GET", f"/assets/{check_id(asset_id)}").get("originalPath") or None
        except ImmichError as e:
            if e.status in GONE_STATUS:
                return None
            raise

    def asset(self, asset_id):
        """Alle Angaben von Immich zu einem Foto. None, wenn es nicht mehr da ist."""
        try:
            return self.api("GET", f"/assets/{check_id(asset_id)}")
        except ImmichError as e:
            if e.status in GONE_STATUS:
                return None
            raise

    def album_names(self, asset_id):
        """Die Namen der Alben, in denen dieses Foto liegt."""
        try:
            albums = self.api("GET", f"/albums?assetId={check_id(asset_id)}")
        except ImmichError as e:
            if e.status in GONE_STATUS:
                return []
            raise
        return sorted(a.get("albumName") or "(ohne Name)" for a in albums)

    def tag_id(self, name, create=False):
        """ID des Tags mit diesem Namen. Ohne create: None, wenn es ihn noch nicht gibt."""
        if create:
            return self.api("PUT", "/tags", {"tags": [name]})[0]["id"]
        return next((t["id"] for t in self.api("GET", "/tags") if t.get("value") == name), None)

    def tagged_ids(self, tag_id):
        return self._search({"tagIds": {"any": [tag_id]}})

    def has_tag(self, asset_id, name):
        try:
            asset = self.api("GET", f"/assets/{check_id(asset_id)}")
        except ImmichError as e:
            if e.status in GONE_STATUS:
                return False
            raise
        return any(t.get("value") == name for t in asset.get("tags") or [])

    def _tag_assets(self, method, tag_id, asset_id, harmless):
        results = self.api(method, f"/tags/{check_id(tag_id)}/assets", {"ids": [check_id(asset_id)]})
        for r in results:
            if not r.get("success") and r.get("error") != harmless:
                raise ImmichError(Msg("tagNotChanged", "Immich hat den Tag nicht geändert: {error}", error=r.get("error")))

    def add_tag(self, tag_id, asset_id):
        self._tag_assets("PUT", tag_id, asset_id, "duplicate")

    def remove_tag(self, tag_id, asset_id):
        self._tag_assets("DELETE", tag_id, asset_id, "not_found")

    def upload_file(self, path, filename):
        """Lädt eine Datei als neues Foto hoch. Eine bereits vorhandene gleiche Datei meldet Immich als "duplicate"."""
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        fields = {"fileCreatedAt": stamp, "fileModifiedAt": stamp, "filename": filename}
        try:
            return self._upload(path, filename, fields)
        except ImmichError as e:
            if e.status != 400:
                raise
        # Ältere Immich-Versionen verlangen zusätzlich Angaben zum Gerät
        return self._upload(path, filename, {**fields, "deviceAssetId": f"{filename}-{stamp}", "deviceId": "immich-mosaik"})

    def _upload(self, path, filename, fields):
        boundary = uuid.uuid4().hex
        head = "".join(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n' for k, v in fields.items())
        head += (f'--{boundary}\r\nContent-Disposition: form-data; name="assetData"; filename="{filename}"\r\n'
                 "Content-Type: image/jpeg\r\n\r\n")
        head, tail = head.encode(), f"\r\n--{boundary}--\r\n".encode()

        def parts():  # Das Bild kann sehr gross sein, darum nicht als Ganzes in den Speicher
            yield head
            with open(path, "rb") as f:
                while chunk := f.read(1024 * 1024):
                    yield chunk
            yield tail

        req = urllib.request.Request(self.url + "/api/assets", data=parts(), method="POST")
        req.add_header("x-api-key", self.key)
        req.add_header("Accept", "application/json")
        req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
        req.add_header("Content-Length", str(len(head) + os.path.getsize(path) + len(tail)))
        try:
            with urllib.request.urlopen(req, timeout=600) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            raise ImmichError(f"POST /assets: HTTP {e.code}", e.code)
        except (urllib.error.URLError, OSError) as e:
            raise ImmichError(f"POST /assets: {getattr(e, 'reason', e)}")

    def create_album(self, name):
        return self.api("POST", "/albums", {"albumName": name})["id"]

    def rename_album(self, album_id, name):
        self.api("PATCH", f"/albums/{check_id(album_id)}", {"albumName": name})

    def show_first(self, album_id, asset_id):
        """Neuestes Foto zuoberst und dieses Bild als Titelbild. Immich sortiert Alben nur nach dem Aufnahmedatum."""
        self.api("PATCH", f"/albums/{check_id(album_id)}", {"order": "desc", "albumThumbnailAssetId": check_id(asset_id)})

    def delete_album(self, album_id):
        """Löscht das Album, die Fotos bleiben. Immich entfernt dabei auch die Links darauf."""
        try:
            self.api("DELETE", f"/albums/{check_id(album_id)}")
        except ImmichError as e:
            if e.status not in GONE_STATUS:
                raise

    def remove_from_album(self, album_id, ids):
        try:
            self.api("DELETE", f"/albums/{check_id(album_id)}/assets", {"ids": list(ids)})
        except ImmichError as e:
            if e.status not in GONE_STATUS:
                raise

    def assets_under(self, prefix):
        """Fotos, deren Originalpfad so beginnt, als {Pfad: ID}."""
        found, cursor = {}, None
        while True:
            body = {"filter": {"originalPath": {"startsWith": prefix}, "trashedAt": {"eq": None},
                               "visibility": {"in": ["timeline", "archive", "hidden"]}}, "size": 1000, "withExif": False}
            if cursor:
                body["cursor"] = cursor
            assets = self.api("POST", "/search/metadata", body).get("assets", {})
            found.update({a["originalPath"]: a["id"] for a in assets.get("items", []) if a.get("originalPath", "").startswith(prefix)})
            cursor = assets.get("nextCursor")
            if not cursor:
                return found

    def add_to_album(self, album_id, ids):
        """Verknüpft Fotos mit einem Album, kopiert nichts. Gibt zurück, wie viele Fotos Immich nicht mehr hat."""
        ids, missing = list(ids), 0
        for start in range(0, len(ids), 1000):
            part = ids[start:start + 1000]
            for r in self.api("PUT", f"/albums/{check_id(album_id)}/assets", {"ids": part}):
                if r.get("success") or r.get("error") == "duplicate":
                    continue
                if r.get("error") != "not_found":
                    raise ImmichError(Msg("notAddedToAlbum", "Immich hat das Foto nicht ins Album gelegt: {error}", error=r.get("error")),
                                      403 if r.get("error") == "no_permission" else None)
                missing += 1
        return missing

    def original(self, asset_id):
        """Die Originaldatei. None, wenn das Foto nicht mehr da ist."""
        try:
            return self.api("GET", f"/assets/{check_id(asset_id)}/original", raw=True)
        except ImmichError as e:
            if e.status in GONE_STATUS:
                return None
            raise

    def image(self, asset_id, size):
        return self.api("GET", f"/assets/{check_id(asset_id)}/thumbnail?size={size}", raw=True)
