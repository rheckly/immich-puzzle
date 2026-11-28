"""Collage als Motiv (Entscheid 66, docs/collage-motiv-api.md in Smart Albums).

Puzzle liest fertige Collagen aus Collage, nur von Server zu Server mit SHARES_API_KEY.
Der Browser bekommt den Schlüssel nie, er spricht nur mit /api/collages… von Puzzle.
Die Adresse von Collage steht in family.json (Dienst «collage», aktiv). Nur lesen, nie ändern.
Nur im Admin von Puzzle, nicht im Kiosk (Roger 25.11.2026).
"""
import json
import re
import urllib.error
import urllib.parse
import urllib.request

import family

COLLAGE_ID = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
MAX_IMAGE = 80 * 1024 * 1024  # fertiges Bild, z. B. A3 als PNG
MAX_LIST = 4 * 1024 * 1024
TIMEOUT = 15
IMAGE_TYPES = ("image/jpeg", "image/png", "image/webp")


class CollageError(Exception):
    """code: «down» (nicht erreichbar), «denied» (401/403), «missing» (404), «bad» (Antwort unbrauchbar)."""

    def __init__(self, code, detail=""):
        super().__init__(detail or code)
        self.code = code


def check_collage_id(value):
    value = str(value or "")
    if not COLLAGE_ID.match(value):
        raise ValueError("bad collage id")
    return value


def _text(value, limit=200):
    return str(value)[:limit] if isinstance(value, (str, int, float)) and not isinstance(value, bool) else ""


def _number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0 else None


class CollageSource:
    def __init__(self, register, key, timeout=TIMEOUT):
        self.register = register
        self.key = (key or "").strip()
        self.timeout = timeout

    def url(self):
        """Adresse von Collage aus family.json, leer, wenn Collage fehlt oder inaktiv ist."""
        if not self.register:
            return ""
        for s in family.load_register(self.register.path):
            if s["id"] == "collage" and s.get("url"):
                return s["url"].rstrip("/")
        return ""

    def enabled(self):
        return bool(self.key and self.url())

    def _get(self, path, limit):
        base = self.url()
        if not self.key or not base:
            raise CollageError("off")
        req = urllib.request.Request(base + path, headers={"Authorization": f"Bearer {self.key}", "Accept": "*/*"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                data = r.read(limit + 1)
                ctype = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        except urllib.error.HTTPError as e:
            raise CollageError("denied" if e.code in (401, 403) else "missing" if e.code == 404 else "down", f"HTTP {e.code}")
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise CollageError("down", str(e))
        if len(data) > limit:
            raise CollageError("bad", "too large")
        return data, ctype

    def collages(self):
        """Fertige Collagen für das Panel «Collage wählen», neueste zuerst; Bilder über Puzzle."""
        base = self.url()
        data, _ = self._get("/api/internal/collages", MAX_LIST)
        try:
            raw = json.loads(data.decode("utf-8")).get("collages")
        except (ValueError, AttributeError):
            raise CollageError("bad", "no json")
        if not isinstance(raw, list):
            raise CollageError("bad", "no list")
        out = []
        for c in raw:
            if not isinstance(c, dict) or not COLLAGE_ID.match(str(c.get("id") or "")):
                continue
            cid, ver = c["id"], int(_number(c.get("updated_at")) or 0)
            out.append({
                "id": cid, "name": _text(c.get("name")), "created": _number(c.get("created_at")),
                "updated": _number(c.get("updated_at")), "photos": _number(c.get("photos")),
                "template": _text(c.get("template")), "format": _text(c.get("format")),
                "width": _number(c.get("width")), "height": _number(c.get("height")),
                "thumb": f"/api/collages/{cid}/thumb?v={ver}", "image": f"/api/collages/{cid}/image?v={ver}",
                "open": f"{base}/?collage={urllib.parse.quote(cid)}",
            })
        out.sort(key=lambda x: x["updated"] or 0, reverse=True)
        return out

    def view(self):
        """Antwort für GET /api/collages: nie ein Fehlerstatus, damit das Panel einen Hinweis zeigen kann."""
        base = self.url()
        if not self.key or not base:
            return {"enabled": False, "collages": []}
        out = {"enabled": True, "new": f"{base}/?new=1", "collages": []}
        try:
            out["collages"] = self.collages()
        except CollageError as e:
            out["error"] = e.code
        return out

    def file(self, cid, kind):
        """Vorschaubild (thumb) oder fertiges Bild (image) einer Collage: (Bytes, Content-Type)."""
        cid = check_collage_id(cid)
        if kind not in ("thumb", "image"):
            raise ValueError("bad kind")
        data, ctype = self._get(f"/api/internal/collages/{cid}/{kind}", MAX_IMAGE)
        if ctype not in IMAGE_TYPES:
            raise CollageError("bad", f"type {ctype}")
        return data, ctype
