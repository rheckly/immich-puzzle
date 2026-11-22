"""Immich Puzzle: ein Foto, das sich als Puzzle von selbst zusammensetzt, live im Browser (kein Video).

Editor (puzzle.html) und Empfängerseite (play.html) zeichnen mit derselben Engine (puzzle-engine.js). Der Server
speichert je Puzzle die Einstellungen, das zugeschnittene Foto und ein Vorschaubild, liefert die Musik (gemeinsamer
Ordner mit Reels und eigene Uploads) und die Live-Links auf einem zweiten Port (Muster aus Collage).
Login mit LOGIN_USER und LOGIN_PASSWORD (auth.py). Ohne LOGIN_PASSWORD ist alles offen.
"""
import base64
import hashlib
import hmac
import html
import io
import json
import math
import os
import re
import secrets
import shutil
import threading
import time
import traceback
import unicodedata
import urllib.parse
import uuid
from datetime import datetime, timedelta, timezone
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from PIL import Image, ImageOps

import auth as login
import family
from immich import Immich, ImmichError, check_id
from msg import Msg, fields, of

try:
    import mutagen  # Länge der Musikstücke; fehlt es, misst die Seite selbst
except ImportError:  # pragma: no cover
    mutagen = None

APP_DIR = os.path.dirname(os.path.abspath(__file__))
VERSION = "0.1.7"  # bei jedem Build hochzählen, zuoberst in app/CHANGELOG.md gleich
VERSION_DATE = "22.11.2026"


# ---------- Changelog für das Panel «Über» (Entscheid 43) ----------
# app/CHANGELOG.md: «## X.Y.Z» je Version, darunter ein Satz zur Version und die Einträge «- `TYP` Text».
# TYP ist NEW (neue Funktion), CHG (Änderung), FIX (Fehlerbehebung) oder LNG (Sprache und Texte). Ein Eintrag ohne
# Präfix (Hinweis) hat keinen Typ; Unterpunkte «  - …» gehören zum Eintrag darüber und haben nie einen Typ.
CHANGELOG_FILE = os.path.join(APP_DIR, "CHANGELOG.md")
CHANGE_TYPES = ("NEW", "CHG", "FIX", "LNG")
_changelog_cache = [None, None]  # (Stempel, Ergebnis)


def parse_changelog(text):
    """Die Versionen, neueste zuerst: [{"version", "label", "note", "changes": [{"type", "text", "sub"}]}]"""
    releases = []
    for line in text.splitlines():
        if line.startswith("## "):
            head = line[3:].strip()
            version, _, label = head.partition(" ")
            releases.append({"version": version, "label": label.strip(" ()").lstrip("–-").strip(), "note": "", "changes": []})
        elif not releases or not line.strip():
            continue
        elif line.startswith("- "):
            match = re.match(r"`(NEW|CHG|FIX|LNG)` (.*)", line[2:])
            entry = {"type": match.group(1), "text": match.group(2)} if match else {"type": None, "text": line[2:]}
            releases[-1]["changes"].append({**entry, "sub": []})
        elif re.match(r"\s+- ", line) and releases[-1]["changes"]:
            releases[-1]["changes"][-1]["sub"].append(line.strip()[2:])
        else:  # Satz zur Version, auch ein zweiter Absatz zwischen den Einträgen (z. B. 0.5.5)
            releases[-1]["note"] = (releases[-1]["note"] + " " + line.strip()).strip()
    return releases


def read_changelog(path=CHANGELOG_FILE):
    """parse_changelog der Datei, gemerkt bis sie sich ändert. Fehlt sie: leere Liste."""
    try:
        st = os.stat(path)
    except OSError:
        return []
    stamp = (path, st.st_mtime_ns, st.st_size)
    if _changelog_cache[0] != stamp:
        with open(path, encoding="utf-8") as f:
            _changelog_cache[:] = [stamp, parse_changelog(f.read())]
    return _changelog_cache[1]


BRAND_DIR = os.path.join(APP_DIR, "brand")  # Logos der Familienmarke, Dateien *-puzzle.*
BRAND_TYPES = {".svg": "image/svg+xml", ".png": "image/png"}


def brand_file(name):
    """Pfad einer Logo-Datei unter /brand/, nur bekannte Namen ohne Ordner"""
    if "/" in name or "\\" in name or os.path.splitext(name)[1] not in BRAND_TYPES:
        raise KeyError(name)
    path = os.path.join(BRAND_DIR, name)
    if not os.path.isfile(path):
        raise KeyError(name)
    return path


FAMILY_ID = "puzzle"  # Eigene id in family.json, wenn FAMILY_ID fehlt


def family_register():
    """Register der Familien-Dienste (Entscheid 21). family.py ist 1:1 aus Mosaik und nimmt ohne FAMILY_ID «mosaik» an"""
    register = family.Register.from_env()
    if not (os.environ.get("FAMILY_ID") or "").strip():
        register.own_id = FAMILY_ID
    return register


def text_files():
    """Die Sprachdateien texts.<sprache>.js (Mehrsprachigkeit, siehe i18n.js). Eine neue Datei gilt ohne Änderung hier."""
    return sorted(n for n in os.listdir(APP_DIR) if re.fullmatch(r"texts\.[a-z]{2}\.js", n))




# ---------- Live-Link: ein Puzzle zum Abspielen auf einem zweiten Port (wie in Collage) ----------
VIEW_DAYS = 365  # So viele Tage Aufrufe pro Tag behalten (Entscheid 37)


def count_day(days, now):
    """Ein Aufruf mehr am lokalen Kalendertag von now (TZ des Containers, Europe/Zurich). Ältere Tage als VIEW_DAYS
    fallen weg. Gespeichert werden nur Tag und Zahl, keine IP."""
    today = time.strftime("%Y-%m-%d", time.localtime(now))
    oldest = time.strftime("%Y-%m-%d", time.localtime(now - VIEW_DAYS * 86400))
    days = {d: int(n) for d, n in (days or {}).items() if d > oldest}
    days[today] = days.get(today, 0) + 1
    return days


SHARE_TOKEN = re.compile(r"^[A-Za-z0-9_-]{32}$")
SHARE_TITLE_MAX = 120  # Zeichen im Titel eines Live-Links
LINK_DAYS = ("today", 1, 7, 30, 60, 90, 365, 0)  # Gültigkeit (Entscheid 38): heute + N Tage, 0 = unbegrenzt


def day_end(day):
    """Ende eines Tages, 23:59:59 Lokalzeit (TZ des Containers), als Epoch."""
    return datetime(day.year, day.month, day.day, 23, 59, 59).timestamp()


def link_expiry(body, now=None):
    """Ablauf aus days ("today", 1, 7, 30, 60, 90, 365, 0) oder until (JJJJ-MM-TT, frühestens heute).
    Gibt (expires, days) zurück; jeder Ablauf endet am Tagesende, 0 = ohne Ablauf."""
    today = datetime.fromtimestamp(now if now is not None else time.time()).date()
    if body.get("until"):
        try:
            until = datetime.strptime(str(body["until"]), "%Y-%m-%d").date()
        except ValueError:
            until = None
        if not until or until < today:
            raise ValueError(Msg("unknownValidity", "Unbekannte Gültigkeit."))
        return day_end(until), "until"
    days = body.get("days")
    if days != "today":
        try:
            days = int(days or 0)
        except (TypeError, ValueError):
            days = -1
    if days not in LINK_DAYS:
        raise ValueError(Msg("unknownValidity", "Unbekannte Gültigkeit."))
    if days == 0:
        return None, 0
    return day_end(today + timedelta(days=0 if days == "today" else days)), days


def link_start(value, now=None):
    """Gültig ab (Entscheid 39): JJJJ-MM-TT, frühestens heute, gilt ab 00:00:00 Lokalzeit. Heute oder leer = sofort (None)."""
    if not value:
        return None
    today = datetime.fromtimestamp(now if now is not None else time.time()).date()
    try:
        start = datetime.strptime(str(value), "%Y-%m-%d").date()
    except ValueError:
        start = None
    if not start or start < today:
        raise ValueError(Msg("unknownStart", "Unbekanntes Startdatum: frühestens heute."))
    return None if start == today else datetime(start.year, start.month, start.day).timestamp()


class NotActive(KeyError):
    """Live-Link ausgeschaltet (Entscheid 48): Adresse bleibt, von aussen nur «Link nicht aktiv», kein Inhalt."""
    def __init__(self):
        super().__init__("link")


class Expired(KeyError):
    """Live-Link abgelaufen (Entscheid 52): 410, gleicher Text wie unbekannt, kein Inhalt."""
    def __init__(self):
        super().__init__("link")


class BaseAddress(KeyError):
    """Freigabe-Port ohne Code (Entscheid 52): 404 «Hier liegt kein Puzzle»."""
    def __init__(self):
        super().__init__("base")


class FieldError(ValueError):
    """Ungültiges Feld beim Ändern aus Smart Albums (Entscheid 48): 400 mit {"error", "field"}."""
    def __init__(self, field, message):
        super().__init__(message)
        self.field = field


# Was Smart Albums an einem Puzzle-Link ändern darf (Entscheid 48, Passwort ab Entscheid 49, Metadaten ab Entscheid 50)
SHARE_ACTIONS = ["expiry", "validFrom", "password", "download", "metadata", "off", "delete"]
PATCH_FIELDS = {"expires": "expiry", "validFrom": "validFrom", "password": "password", "download": "download",
                "metadata": "metadata", "active": "off"}

# Passwort für Live-Links (Entscheid 49): nur als Hash in share_passwords.json, nie in job.json oder einer Antwort
PW_MIN = 4
PW_ROUNDS = 200_000
PW_FAILS, PW_WINDOW, PW_BLOCK = 5, 600, 300  # 5 Fehlversuche in 10 Minuten: diese IP 5 Minuten gesperrt
UNLOCK_COOKIE, UNLOCK_DAYS = "puzzle_unlock", 30


class PasswordRequired(KeyError):
    """Geschützter Live-Link ohne Freischaltung (Entscheid 49): Abfrage statt Inhalt, nichts zählt."""
    def __init__(self):
        super().__init__("link")


def check_password(value, field="password"):
    """Neues Passwort: None entfernt es, sonst Text mit mindestens PW_MIN Zeichen. Sonst FieldError."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise FieldError(field, Msg("badPassword", "Das Passwort muss Text sein."))
    if len(value) < PW_MIN:
        raise FieldError(field, Msg("pwTooShort", "Das Passwort braucht mindestens {n} Zeichen.", n=PW_MIN))
    return value


def hash_password(pw, salt=None):
    salt = salt or secrets.token_bytes(16)
    return {"salt": salt.hex(), "hash": hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, PW_ROUNDS).hex()}


def parse_utc(value, field):
    """ISO 8601 (UTC, z. B. 2027-01-31T23:00:00Z) als Zeitstempel, None bleibt None. Sonst FieldError."""
    if value is None:
        return None
    when = None
    if isinstance(value, str):
        try:
            when = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            pass
    if when is None:
        raise FieldError(field, Msg("badDate", "Ungültiges Datum: ISO 8601 erwartet."))
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.timestamp()


def set_active(link, on):
    """Link ein- oder ausschalten (Entscheid 48). Gespeichert wird nur «aus», ältere Links gelten als aktiv."""
    if on:
        link.pop("active", None)
    else:
        link["active"] = False


def link_active(link):
    return (link or {}).get("active") is not False


def link_metadata(link):
    """Metadaten über den Link zeigen (Entscheid 50). Gespeichert wird nur «aus», ältere Links zeigen sie."""
    return (link or {}).get("metadata") is not False


def set_metadata(link, on):
    if on:
        link.pop("metadata", None)
    else:
        link["metadata"] = False


# JPEG-Segmente mit Metadaten: APP1 (EXIF mit GPS, XMP), APP13 (IPTC/Photoshop), COM. Das Farbprofil (APP2) bleibt
JPEG_META_MARKERS = (0xE1, 0xED, 0xFE)


def _jpeg_orientation(data):
    try:
        with Image.open(io.BytesIO(data)) as img:
            return img.getexif().get(0x0112, 1)
    except Exception:
        return 1


def strip_jpeg(data):
    """Metadaten aus einem JPEG streichen, ohne neu zu komprimieren. None, wenn der Aufbau unerwartet ist."""
    out, i = bytearray(data[:2]), 2
    while i + 4 <= len(data):
        if data[i] != 0xFF:
            return None
        marker = data[i + 1]
        if marker == 0xFF:  # Füllbyte
            i += 1
            continue
        if marker == 0xDA:  # Ab hier die Bilddaten
            out += data[i:]
            return bytes(out)
        if 0xD0 <= marker <= 0xD7 or marker == 0x01:
            out += data[i:i + 2]
            i += 2
            continue
        size = int.from_bytes(data[i + 2:i + 4], "big")
        if size < 2:
            return None
        if marker not in JPEG_META_MARKERS:
            out += data[i:i + 2 + size]
        i += 2 + size
    return None


def clean_image(data):
    """Bild ohne EXIF, GPS, XMP und IPTC (Entscheid 50). Ein JPEG verlustfrei, sonst mit Pillow neu gespeichert
    (gedreht nach der EXIF-Lage, die ja wegfällt). (Daten, Endung) oder None, wenn Pillow das Format nicht kennt."""
    if data[:2] == b"\xff\xd8" and _jpeg_orientation(data) == 1:
        clean = strip_jpeg(data)
        if clean:
            return clean, ".jpg"
    try:
        with Image.open(io.BytesIO(data)) as img:
            kind = img.format
            icc = img.info.get("icc_profile")
            lossless = bool(img.info.get("lossless"))
            img = ImageOps.exif_transpose(img)
            out = io.BytesIO()
            extra = {"icc_profile": icc} if icc else {}
            if kind == "PNG":
                img.save(out, "PNG", **extra)
                return out.getvalue(), ".png"
            if kind == "WEBP":
                img.save(out, "WEBP", lossless=lossless, quality=90, **extra)
                return out.getvalue(), ".webp"
            img.convert("RGB").save(out, "JPEG", quality=92, **extra)
            return out.getvalue(), ".jpg"
    except Exception:
        return None


class NotYetValid(KeyError):
    """Live-Link mit Startdatum in der Zukunft: von aussen nur die Hinweisseite, kein Inhalt."""
    def __init__(self, start):
        super().__init__("link")
        self.start = start


def start_text(start, lang):
    """Hinweis «gültig ab» in der Sprache des Besuchers: 24.12.2026 / 24 Dec 2026"""
    day = datetime.fromtimestamp(start)
    if lang == "en":
        return f"This link is valid from {day.day} {day.strftime('%b')} {day.year}."
    return f"Dieser Link ist ab {day:%d.%m.%Y} gültig."
VIEW_WINDOW = 30 * 60  # Dieselbe IP zählt auf einem Live-Link nur einmal in 30 Minuten
# Bots, Crawler und Link-Vorschauen der Messenger zählen nicht als Aufruf
VIEW_BOTS = re.compile(r"bot|crawl|spider|slurp|preview|facebookexternalhit|facebot|whatsapp|telegram|slack|discord|"
                       r"twitter|linkedin|skype|signal|viber|embedly|pinterest|vkshare|mastodon|bluesky|iframely|"
                       r"headless|lighthouse|curl|wget|python|go-http|java/|okhttp|axios|node-fetch|httpclient|"
                       r"scan|monitor|uptime|check", re.I)
SHARE_HEADERS = {"Referrer-Policy": "no-referrer", "X-Robots-Tag": "noindex, nofollow"}
CARD_MAX_BYTES = 200 * 1024  # WhatsApp zeigt grössere Vorschaubilder nicht an (Grenze etwa 300 KB)
CARD_TEXT = {"de": "Ein Foto-Puzzle, das sich von selbst zusammensetzt", "en": "A photo puzzle that puts itself together"}
# Hinweisseite geteilter Link (Entscheid 52, Design-Vorlage Kap. 7): Fall → Status, Umami-Grund, Titel / Satz / Hilfesatz.
# Vorrang: unbekannt 404 > abgelaufen 410 > nicht aktiv 403 > noch nicht gültig 403 > Passwort 200/401/429; Basis 404.
_GONE = {"de": ("Dieser Link gilt nicht (mehr)",
                "Der Link ist abgelaufen, wurde zurückgezogen oder ist nicht vollständig kopiert.",
                "Frag die Person, die ihn dir geschickt hat, nach einem neuen Link."),
         "en": ("This link is not valid (any more)", "The link has expired, was withdrawn or was not copied completely.",
                "Ask the person who sent it to you for a new link.")}
NOTICE = {
    "unknown": (404, "ungueltig", _GONE),
    "expired": (410, "abgelaufen", _GONE),
    "off": (403, "nicht-aktiv", {
        "de": ("Link nicht aktiv", "Dieser Link ist im Moment nicht aktiv.", "Frag die Person, die ihn dir geschickt hat."),
        "en": ("Link not active", "This link is not active at the moment.", "Ask the person who sent it to you.")}),
    "soon": (403, "noch-nicht", {  # Satz kommt aus start_text
        "de": ("Dieser Link gilt noch nicht", None, "Öffne ihn ab diesem Tag nochmals."),
        "en": ("This link is not valid yet", None, "Please open it again from that day on.")}),
    "base": (404, "basis", {
        "de": ("Hier liegt kein Puzzle", "Diese Adresse allein zeigt nichts an.", "Öffne den ganzen Link, den du bekommen hast."),
        "en": ("There is no puzzle here", "This address alone shows nothing.", "Open the full link you received.")}),
}
OTHER_LANG = {"de": ("en", "English"), "en": ("de", "Deutsch")}
SHARE_PW = {  # Passwort-Abfrage (Entscheid 49, Mockup hinweisseite-link.html): Titel, Satz, Feld, Knopf, Fehler, Sperre
    "de": ("Passwort", "Diese Seite ist mit einem Passwort geschützt.", "Passwort", "Öffnen", "Passwort falsch.",
           "Zu viele Versuche. Bitte in 5 Minuten wieder versuchen."),
    "en": ("Password", "This page is protected by a password.", "Password", "Open", "Wrong password.",
           "Too many attempts. Please try again in 5 minutes."),
}
SHARE_FOOT = {
    "de": "Hier werden Foto-Puzzles geteilt. Ein Puzzle öffnet sich nur mit dem persönlichen Link, den du bekommen hast.",
    "en": "Photo puzzles are shared here. A puzzle only opens with the personal link you received.",
}




def share_address(url):
    """SHARE_PUBLIC_URL → (Origin, Präfix der Links, Basis der Links). Der Pfad gilt als Präfix
    (https://links.example.com/puzzles → /puzzles/<Code>/). Ohne Adresse /puzzles/<Code>/ auf dem Freigabe-Port."""
    parts = urllib.parse.urlsplit((url or "").strip().rstrip("/"))
    origin = f"{parts.scheme}://{parts.netloc}" if parts.scheme in ("http", "https") and parts.netloc else ""
    prefix = (parts.path.rstrip("/") if origin else "") or "/puzzles"
    return origin, prefix, origin + prefix if origin else ""




def clean_title(title):
    """Titel eines Live-Links: eine Zeile, höchstens SHARE_TITLE_MAX Zeichen. Leer = Name des Puzzles."""
    return " ".join(str(title or "").split())[:SHARE_TITLE_MAX].strip()






def write_json(path, data):
    tmp = f"{path}.{uuid.uuid4().hex}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, path)



# ---------- Puzzle: Einstellungen, wie sie der Editor schickt ----------
PUZZLE_ID = re.compile(r"^[0-9a-f]{32}$")
COUNTS = (24, 48, 100, 200, 500)  # Anzahl Teile
FORMATS = {"photo": 0, "free": 0, "1:1": 1, "4:3": 4 / 3, "3:2": 3 / 2, "16:9": 16 / 9, "3:4": 3 / 4, "2:3": 2 / 3,
           "9:16": 9 / 16}  # Ausschnitt, «photo» = wie das Foto, «free» = frei gezogen (das Foto ist schon zugeschnitten)
STARTS = ("scatter", "heap", "mix")  # Startlage: verstreut, Haufen, gemischt
DUR_MODES = ("music", "own")  # Dauer wie die Musik (höchstens MAX_T) oder eigene Sekunden
HOLD_MIN, HOLD_MAX = 2, 10  # So lange bleibt das fertige Bild stehen (Sekunden, heranzoomen)
TEMPO_MAX = 2  # Stärke des Tempo-Bogens (0 = gleichmässig, 2 = sehr stark)
FRAME_AT = (15, 70, 40)  # Rahmen fertig nach … % der Zeit bis «fertig» (min, max, Vorgabe)
RAMP = (0, 60, 5)  # Schneller werden ab … % der Zeit
TEXT_SIZE = (50, 200, 100)  # Schriftgrösse in %
TEXT_MODES = ("in", "end")  # Text im Puzzle (in den Teilen) oder erscheint am Schluss
# Schriften: 8 aus dem System, 13 aus Collage (app/fonts, Lizenzen OFL/Apache daneben)
FONT_FILES = {"roboto": "Roboto-Bold", "sourceserif": "SourceSerifPro-Semibold", "amatic": "AmaticSC-Bold",
              "opensans": "OpenSans-Bold", "lato": "Lato-Bold", "montserrat": "Montserrat-Bold",
              "bebas": "BebasNeue-Regular", "anton": "Anton-Regular", "archivo": "ArchivoBlack-Regular",
              "lilita": "LilitaOne-Regular", "fredoka": "Fredoka-Bold", "chewy": "Chewy-Regular",
              "luckiest": "LuckiestGuy-Regular"}
TEXT_FONTS = ("hand", "script", "serif", "elegant", "sans", "bold", "mono", "fun") + tuple(FONT_FILES)
TEXT_POS = ("tl", "tc", "tr", "ml", "mc", "mr", "bl", "bc", "br")
OLD_TEXT_POS = {"top": "tc", "mid": "mc", "bottom": "bc"}  # bis 0.1.2
TEXT_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")  # 14 Farben im Editor oder eine eigene
MIN_T, MAX_T = 10, 60  # Sekunden
TEXT_MAX = 300
MAX_BODY = 16 * 1024 * 1024  # Foto als data:-URL im JSON
PHOTO_SIDE = 1600  # Längste Seite des gespeicherten Fotos
THUMB_SIDE = 360
CARD_SIZE = (1200, 630)
MUSIC_TYPES = {".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".aac": "audio/aac", ".wav": "audio/wav",
               ".ogg": "audio/ogg", ".flac": "audio/flac"}
MAX_MUSIC = 30 * 1024 * 1024
MUSIC_UPLOAD = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.()-]{0,120}$")


def bad_setting(field):
    return ValueError(Msg("badSetting", "Ungültige Einstellung «{field}».", field=field))


def clean_config(c):
    """Einstellungen eines Puzzles prüfen. Gibt ein neues dict zurück, nur mit bekannten Feldern."""
    if not isinstance(c, dict):
        raise ValueError(Msg("badConfig", "Ungültige Einstellungen."))

    def pick(key, allowed, default):
        value = c.get(key, default)
        if value not in allowed or isinstance(value, bool):
            raise bad_setting(key)
        return value

    def number(key, low, high, default):
        value = c.get(key, default)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
            raise bad_setting(key)
        return value

    if c.get("textPos") in OLD_TEXT_POS:
        c = {**c, "textPos": OLD_TEXT_POS[c["textPos"]]}
    text = c.get("text") or ""
    if not isinstance(text, str):
        raise bad_setting("text")
    text = "\n".join(" ".join(line.split()) for line in text.replace("\r", "").split("\n"))[:TEXT_MAX].rstrip("\n")  # Leerzeilen am Anfang bleiben, sie schieben den Schriftzug nach unten (Roger 21.11.2026)
    title = c.get("title") or ""
    if not isinstance(title, str):
        raise bad_setting("title")
    out = {
        "count": pick("count", COUNTS, 100), "format": pick("format", tuple(FORMATS), "photo"),
        "start": pick("start", STARTS, "scatter"), "rot": bool(c.get("rot")), "frame": bool(c.get("frame", True)),
        "ghost": bool(c.get("ghost")), "seed": int(number("seed", 0, 2 ** 31, 4711)),
        "tempo": round(float(number("tempo", 0, TEMPO_MAX, 0.5)), 3), "durMode": pick("durMode", DUR_MODES, "own"),
        "T": round(float(number("T", MIN_T, MAX_T, 45)), 2), "hold": int(number("hold", HOLD_MIN, HOLD_MAX, 5)),
        "frameAt": int(number("frameAt", *FRAME_AT)), "ramp": int(number("ramp", *RAMP)),
        "title": clean_title(title), "text": text, "textMode": pick("textMode", TEXT_MODES, "in"),
        "textFont": pick("textFont", TEXT_FONTS, "hand"), "textSize": int(number("textSize", *TEXT_SIZE)),
        "textPos": pick("textPos", TEXT_POS, "mc"), "textColor": text_color(c.get("textColor", "#ffffff")),
    }
    music = c.get("music")
    if music is None:
        out["music"] = None
    elif isinstance(music, dict) and isinstance(music.get("id"), str):
        out["music"] = {"id": music["id"][:300], "name": str(music.get("name") or "")[:120],
                        "volume": round(float(_unit(music.get("volume", 0.8))), 3),
                        "fade": bool(music.get("fade", True))}
    else:
        raise bad_setting("music")
    if out["durMode"] == "music" and not out["music"]:
        out["durMode"] = "own"
    return out


def text_color(value):
    if not isinstance(value, str) or not TEXT_COLOR.match(value):
        raise bad_setting("textColor")
    return value.lower()


def _unit(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
        raise bad_setting("volume")
    return value


def decode_photo(value):
    """data:image/…;base64,… vom Editor → JPEG ohne Metadaten, längste Seite höchstens PHOTO_SIDE, und das Bild."""
    if not isinstance(value, str) or not value.startswith("data:image/") or ";base64," not in value[:40]:
        raise ValueError(Msg("badPhoto", "Das Foto fehlt oder ist kein Bild."))
    try:
        data = base64.b64decode(value.split(",", 1)[1], validate=True)
        with Image.open(io.BytesIO(data)) as img:
            img = ImageOps.exif_transpose(img).convert("RGB")
    except Exception:
        raise ValueError(Msg("badPhoto", "Das Foto fehlt oder ist kein Bild."))
    img.thumbnail((PHOTO_SIDE, PHOTO_SIDE))
    out = io.BytesIO()
    img.save(out, "JPEG", quality=90)  # Neu gespeichert: kein EXIF, kein GPS
    return out.getvalue(), img


def cover(img, size):
    """Bild auf size zuschneiden (füllt ganz, Mitte bleibt)."""
    return ImageOps.fit(img, size, Image.LANCZOS)


def card_bytes(img):
    """Vorschaubild für Messenger: 1200 × 630, höchstens CARD_MAX_BYTES."""
    card = cover(img, CARD_SIZE)
    for quality in (82, 72, 62, 52, 42):
        out = io.BytesIO()
        card.save(out, "JPEG", quality=quality, optimize=True)
        if out.tell() <= CARD_MAX_BYTES:
            break
    return out.getvalue()


def thumb_bytes(img):
    small = img.copy()
    small.thumbnail((THUMB_SIDE, THUMB_SIDE))
    out = io.BytesIO()
    small.save(out, "JPEG", quality=80)
    return out.getvalue()


def music_kind(data):
    """Endung nach den ersten Bytes, None wenn es keine bekannte Musikdatei ist."""
    if data[:3] == b"ID3" or (len(data) > 1 and data[0] == 0xFF and data[1] & 0xE0 == 0xE0):
        return ".mp3"
    if data[4:8] == b"ftyp":
        return ".m4a"
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return ".wav"
    if data[:4] == b"OggS":
        return ".ogg"
    if data[:4] == b"fLaC":
        return ".flac"
    return None


def safe_music_name(name, ext):
    stem = os.path.splitext(os.path.basename(str(name or "")))[0]
    stem = unicodedata.normalize("NFKD", stem).encode("ascii", "ignore").decode()
    stem = re.sub(r"[^A-Za-z0-9 _.()-]+", "", stem).strip(" .-_")[:80] or "Musik"
    return stem + ext


def track_name(path):
    return os.path.splitext(os.path.basename(path))[0].replace("_", " ")


def puzzle_name(p):
    return p.get("name") or (p.get("config") or {}).get("title") or "Puzzle"


def share_title(p):
    return clean_title((p.get("online_link") or {}).get("title")) or puzzle_name(p)


class App:
    def __init__(self, immich_url, api_key, data_dir, puzzle_dir="", music_dir="/music", share_port=0,
                 share_public_url="", shares_api_key="", umami_url="", umami_id="", umami_links_id=""):
        self.data_dir = data_dir
        self.puzzle_dir = puzzle_dir or os.path.join(data_dir, "puzzles")  # Ein Ordner je Puzzle
        self.music_dir = music_dir  # Gemeinsame Musik mit Reels, nur lesen
        self.upload_music_dir = os.path.join(data_dir, "music")  # Eigene Musik, hier hochgeladen
        for folder in (self.data_dir, self.puzzle_dir, self.upload_music_dir):
            os.makedirs(folder, exist_ok=True)
        self.shares_api_key = (shares_api_key or "").strip()
        self.share_port = share_port
        self.share_default_url = (share_public_url or "").strip()
        self.settings_path = os.path.join(data_dir, "settings.json")  # Einstellungen aus der App, z. B. Adresse der Live-Links
        try:
            with open(self.settings_path, encoding="utf-8") as f:
                self.settings = json.load(f)
        except (OSError, ValueError):
            self.settings = {}
        if not isinstance(self.settings, dict):
            self.settings = {}
        self._apply_share_url()
        self.views_seen = {}
        umami_url, umami_id, umami_links_id = (umami_url or "").strip().rstrip("/"), (umami_id or "").strip(), (umami_links_id or "").strip()
        self.umami_tag = (f'<script defer src="{html.escape(umami_url)}/script.js" '
                          f'data-website-id="{html.escape(umami_id)}"></script>') if umami_url and umami_id else ""
        self.umami_links = (umami_url, umami_links_id) if umami_url and umami_links_id else None
        self.immich = Immich(immich_url, api_key) if immich_url and api_key else None  # ohne Immich nur Hochladen
        self.lock = threading.Lock()
        self.write_lock = threading.Lock()
        self.puzzles = {}
        self.durations = {}  # (Pfad, mtime) → Sekunden
        self._load()
        self.pw_path = os.path.join(data_dir, "share_passwords.json")
        self.pw_fails, self.pw_blocked = {}, {}
        self._prune_passwords()

    # ---------- Ablage ----------
    def _folder(self, pid):
        if not PUZZLE_ID.match(pid or ""):
            raise KeyError(pid)
        return os.path.join(self.puzzle_dir, pid)

    def _load(self):
        for name in os.listdir(self.puzzle_dir):
            path = os.path.join(self.puzzle_dir, name, "puzzle.json")
            if not PUZZLE_ID.match(name) or not os.path.isfile(path):
                continue
            try:
                with open(path, encoding="utf-8") as f:
                    self.puzzles[name] = json.load(f)
            except (OSError, ValueError):
                traceback.print_exc()

    def _puzzle(self, pid):
        with self.lock:
            p = self.puzzles.get(pid)
            if p is None:
                raise KeyError(pid)
            return json.loads(json.dumps(p))

    def _update(self, pid, **changes):
        with self.lock:
            p = {**self.puzzles[pid], **changes}
            if p.get("online_link") is None:
                p.pop("online_link", None)
            self.puzzles[pid] = p
        with self.write_lock:
            write_json(os.path.join(self._folder(pid), "puzzle.json"), p)
        return p

    def file(self, pid, name):
        if name not in ("photo.jpg", "thumb.jpg", "card.jpg"):
            raise KeyError(name)
        self._puzzle(pid)
        path = os.path.join(self._folder(pid), name)
        if not os.path.isfile(path):
            raise KeyError(name)
        return path

    def _entry(self, p, now=None):
        now = time.time() if now is None else now
        cfg = p.get("config") or {}
        return {"id": p["id"], "name": puzzle_name(p), "created": p.get("created"), "updated": p.get("updated"),
                "by": p.get("by") or "", "count": cfg.get("count"), "T": cfg.get("T"), "format": cfg.get("format"),
                "thumb": f"/api/puzzles/{p['id']}/thumb.jpg?v={int(p.get('updated') or 0)}",
                "link": self._share_entry(p, now) if (p.get("online_link") or {}).get("token") else None}

    def list_puzzles(self):
        with self.lock:
            items = [json.loads(json.dumps(p)) for p in self.puzzles.values()]
        now = time.time()
        items.sort(key=lambda p: p.get("updated") or 0, reverse=True)
        return {"puzzles": [self._entry(p, now) for p in items], **self.share_setup()}

    def get_puzzle(self, pid):
        p = self._puzzle(pid)
        return {**self._entry(p), "config": p.get("config"), "asset": p.get("asset"), "meta": p.get("meta"),
                "photo": f"/api/puzzles/{pid}/photo.jpg?v={int(p.get('updated') or 0)}"}

    def need_immich(self):
        """Die Immich-Verbindung, oder ein Fehler, wenn IMMICH_URL und IMMICH_API_KEY fehlen."""
        if not self.immich:
            raise ValueError(Msg("noImmich", "Immich ist nicht eingerichtet (IMMICH_URL und IMMICH_API_KEY)."))
        return self.immich

    def _meta_of(self, asset):
        """Datum und Ort aus Immich, für die Empfängerseite (nur mit Schalter «Metadaten»)."""
        if not self.immich:
            return None
        try:
            info = self.immich.asset(asset) or {}
        except ImmichError:
            return None
        exif = info.get("exifInfo") or {}
        place = ", ".join(x for x in (exif.get("city"), exif.get("country")) if x)
        date = (info.get("localDateTime") or exif.get("dateTimeOriginal") or "")[:10]
        return {"date": date, "place": place} if date or place else None

    def save_puzzle(self, body, pid=None, by=""):
        """Neues Puzzle (pid None, Foto nötig) oder ändern (Foto nur, wenn es neu ist)."""
        config = clean_config(body.get("config"))
        if config["music"]:
            self.music_path(config["music"]["id"])  # Gibt es die Musik?
        photo = body.get("photo")
        if pid is None and not photo:
            raise ValueError(Msg("badPhoto", "Das Foto fehlt oder ist kein Bild."))
        asset = body.get("asset")
        if asset is not None:
            asset = check_id(asset)
        now = time.time()
        if pid is None:
            pid = uuid.uuid4().hex
            old = {"id": pid, "created": now, "by": by}
        else:
            old = self._puzzle(pid)
        folder = self._folder(pid)
        os.makedirs(folder, exist_ok=True)
        changes = {"config": config, "updated": now, "name": clean_title(body.get("name")) or config["title"]}
        if photo:
            data, img = decode_photo(photo)
            with self.write_lock:
                for name, content in (("photo.jpg", data), ("thumb.jpg", thumb_bytes(img)), ("card.jpg", card_bytes(img))):
                    tmp = os.path.join(folder, f"{name}.{uuid.uuid4().hex}.tmp")
                    with open(tmp, "wb") as f:
                        f.write(content)
                    os.replace(tmp, os.path.join(folder, name))
            changes["asset"] = asset
            changes["meta"] = self._meta_of(asset) if asset else None
        with self.lock:
            self.puzzles[pid] = {**old, **self.puzzles.get(pid, {})}
        self._update(pid, **changes)
        return self.get_puzzle(pid)

    def delete_puzzle(self, pid):
        folder = self._folder(pid)
        with self.lock:
            if pid not in self.puzzles:
                raise KeyError(pid)
            del self.puzzles[pid]
        shutil.rmtree(folder, ignore_errors=True)
        self._prune_passwords()
        return {"ok": True}

    # ---------- Musik: gemeinsamer Ordner (Reels) und eigene Uploads ----------
    def _tags(self, path):
        """Länge, Titel und Interpret einer Musikdatei (mutagen), zwischengespeichert bis die Datei ändert"""
        if not mutagen:
            return {}
        try:
            stamp = (path, os.path.getmtime(path))
        except OSError:
            return {}
        if stamp not in self.durations:
            out = {}
            try:
                info = mutagen.File(path, easy=True)
                if info and info.info:
                    out["duration"] = round(info.info.length, 1)
                for key in ("title", "artist"):
                    value = (info.get(key) if info and info.tags is not None else None) or []
                    value = " ".join(str(value[0]).split())[:120] if value else ""
                    if value:
                        out[key] = value
            except Exception:
                pass
            self.durations[stamp] = out
        return self.durations[stamp]

    def _duration(self, path):
        return self._tags(path).get("duration")

    def _track(self, tid, name, group, path, own):
        tags = self._tags(path)
        return {"id": tid, "name": track_name(name), "group": group, "duration": tags.get("duration"), "own": own,
                "title": tags.get("title") or "", "artist": tags.get("artist") or ""}

    def music_list(self):
        tracks = []
        root = self.music_dir
        if root and os.path.isdir(root):
            for folder, dirs, files in os.walk(root):
                dirs[:] = sorted(d for d in dirs if not d.startswith((".", "@", "#")))
                for name in files:
                    if os.path.splitext(name)[1].lower() in MUSIC_TYPES and not name.startswith("."):
                        path = os.path.join(folder, name)
                        rel = os.path.relpath(path, root).replace(os.sep, "/")
                        group = os.path.dirname(rel)
                        tracks.append(self._track("lib:" + rel, name, group, path, False))
        for name in sorted(os.listdir(self.upload_music_dir)):
            if os.path.splitext(name)[1].lower() in MUSIC_TYPES and MUSIC_UPLOAD.match(name):
                tracks.append(self._track("up:" + name, name, "", os.path.join(self.upload_music_dir, name), True))
        tracks.sort(key=lambda t: (not t["own"], t["group"].lower(), t["name"].lower()))
        return {"tracks": tracks, "library": bool(root and os.path.isdir(root))}

    def music_path(self, mid):
        """Datei einer Musik-ID, sonst KeyError. Nie ausserhalb der beiden Ordner."""
        mid = str(mid or "")
        if mid.startswith("lib:") and self.music_dir:
            root = os.path.realpath(self.music_dir)
            path = os.path.realpath(os.path.join(root, mid[4:]))
            if not path.startswith(root + os.sep):
                raise KeyError(mid)
        elif mid.startswith("up:") and MUSIC_UPLOAD.match(mid[3:]):
            path = os.path.join(self.upload_music_dir, mid[3:])
        else:
            raise KeyError(mid)
        if os.path.splitext(path)[1].lower() not in MUSIC_TYPES or not os.path.isfile(path):
            raise KeyError(mid)
        return path

    def add_music(self, data, name):
        ext = music_kind(data)
        if not ext:
            raise ValueError(Msg("badMusic", "Das ist keine Musikdatei (MP3, M4A, WAV, OGG oder FLAC)."))
        wanted = os.path.splitext(str(name or ""))[1].lower()
        ext = wanted if wanted in MUSIC_TYPES and (wanted in (".m4a", ".aac") or ext == wanted) else ext
        file = safe_music_name(name, ext)
        with self.write_lock:
            stem, n = os.path.splitext(file)[0], 2
            while os.path.exists(os.path.join(self.upload_music_dir, file)):
                file, n = f"{stem} ({n}){ext}", n + 1
            with open(os.path.join(self.upload_music_dir, file), "wb") as f:
                f.write(data)
        path = os.path.join(self.upload_music_dir, file)
        return {"id": "up:" + file, "name": track_name(file), "group": "", "duration": self._duration(path), "own": True}

    def delete_music(self, mid):
        if not str(mid).startswith("up:"):
            raise ValueError(Msg("musicReadOnly", "Musik aus dem gemeinsamen Ordner lässt sich hier nicht löschen."))
        os.remove(self.music_path(mid))
        return {"ok": True}

    # ---------- Live-Links (Muster aus Collage, Entscheide 38, 39, 48, 49, 50) ----------
    def online_link(self, pid, body):
        if not self.share_port:
            raise ValueError(Msg("shareNotSetUp", "Live-Links sind nicht eingerichtet: SHARE_PORT fehlt."))
        p = self._puzzle(pid)
        old = p.get("online_link") or {}
        link = dict(old or {"token": secrets.token_urlsafe(24), "created": time.time(), "expires": None, "days": 0})
        if "download" in body or "token" not in old:
            link["download"] = bool(body.get("download"))
        if "metadata" in body:
            if not isinstance(body["metadata"], bool):
                raise ValueError(Msg("notBool", "«{field}» muss true oder false sein.", field="metadata"))
            set_metadata(link, body["metadata"])
        if "title" in body:
            title = clean_title(body.get("title"))
            if title and title != puzzle_name(p):
                link["title"] = title
            else:
                link.pop("title", None)
        if "active" in body:
            if not isinstance(body["active"], bool):
                raise ValueError(Msg("badActive", "«Aktiv» muss ein- oder ausgeschaltet sein."))
            set_active(link, body["active"])
        if "days" in body or "until" in body:
            link["expires"], link["days"] = link_expiry(body)
        password = ...
        if "password" in body:
            password = check_password(body["password"])
        if "from" in body:
            link["valid_from"] = link_start(body.get("from"))
        if link.get("valid_from") and link.get("expires") and link["expires"] < link["valid_from"]:
            raise ValueError(Msg("endBeforeStart", "Der Link würde ablaufen, bevor er gilt."))
        if password is not ...:
            self._set_password(link["token"], password)
            if password is None:
                link.pop("protected", None)
            else:
                link["protected"] = True
        self._update(pid, online_link=link)
        return self.get_puzzle(pid)

    def revoke_online_link(self, pid):
        self._puzzle(pid)
        self._update(pid, online_link=None)
        self._prune_passwords()
        return self.get_puzzle(pid)

    def _passwords(self):
        try:
            with open(self.pw_path, encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _set_password(self, token, pw):
        with self.write_lock:
            data = self._passwords()
            if pw is None:
                data.pop(token, None)
            else:
                data[token] = hash_password(pw)
            write_json(self.pw_path, data)

    def _prune_passwords(self):
        with self.lock:
            tokens = {(p.get("online_link") or {}).get("token") for p in self.puzzles.values()}
        with self.write_lock:
            data = self._passwords()
            keep = {t: v for t, v in data.items() if t in tokens}
            if keep != data:
                write_json(self.pw_path, keep)

    def _unlock_secret(self):
        path = os.path.join(self.data_dir, "share_secret")
        with self.write_lock:
            try:
                with open(path, encoding="utf-8") as f:
                    value = f.read().strip()
                if value:
                    return value.encode()
            except OSError:
                pass
            value = secrets.token_hex(32)
            with open(path, "w", encoding="utf-8") as f:
                f.write(value)
            return value.encode()

    def unlock_value(self, token):
        rec = self._passwords().get(token)
        if not rec:
            return None
        return hmac.new(self._unlock_secret(), f"{token}\0{rec['hash']}".encode(), hashlib.sha256).hexdigest()[:40]

    def share_locked(self, token, cookie):
        pid = self.shared_puzzle(token)
        if not (self._puzzle(pid).get("online_link") or {}).get("protected"):
            return False
        want = self.unlock_value(token)
        return not (want and cookie and hmac.compare_digest(want.encode(), cookie.encode()))

    def password_ok(self, token, pw):
        rec = self._passwords().get(token)
        if not rec or not isinstance(pw, str):
            return False
        got = hash_password(pw, bytes.fromhex(rec["salt"]))["hash"]
        return hmac.compare_digest(got.encode(), rec["hash"].encode())

    def pw_blocked_until(self, ip, now=None):
        now = time.time() if now is None else now
        with self.lock:
            until = self.pw_blocked.get(ip, 0)
            if until and until <= now:
                del self.pw_blocked[ip]
                return 0
            return until

    def note_pw_fail(self, ip, now=None):
        now = time.time() if now is None else now
        with self.lock:
            fails = [t for t in self.pw_fails.get(ip, []) if t > now - PW_WINDOW] + [now]
            if len(fails) >= PW_FAILS:
                self.pw_fails.pop(ip, None)
                self.pw_blocked[ip] = now + PW_BLOCK
                print(f"Live-Links: {ip} für {PW_BLOCK // 60} Min gesperrt ({PW_FAILS} falsche Passwörter)", flush=True)
                return True
            self.pw_fails[ip] = fails
            return False

    def _by_token(self, token):
        if not SHARE_TOKEN.match(token or ""):
            raise KeyError("link")
        with self.lock:
            found = [pid for pid, p in self.puzzles.items()
                     if secrets.compare_digest(((p.get("online_link") or {}).get("token") or "").encode(), token.encode())]
        if not found:
            raise KeyError("link")
        return found[0]

    def revoke_share(self, token, by="Smart Albums"):
        pid = self._by_token(token)
        self._update(pid, online_link=None)
        self._prune_passwords()
        print(f"Live-Link von {pid} widerrufen durch {by}", flush=True)

    def shared_puzzle(self, token):
        """Das Puzzle zu einem Live-Link, sonst KeyError (unbekannt) oder eine Unterklasse in der Reihenfolge
        von Entscheid 52: abgelaufen (Expired) > nicht aktiv (NotActive) > noch nicht gültig (NotYetValid)."""
        pid = self._by_token(token)
        link = self._puzzle(pid).get("online_link") or {}
        expires, start = link.get("expires"), link.get("valid_from")
        if expires and time.time() > expires:
            raise Expired()
        if not link_active(link):
            raise NotActive()
        if start and time.time() < start:
            raise NotYetValid(start)
        return pid

    def shared_view(self, pid):
        """Was die Empfängerseite bekommt: Einstellungen ohne Musik-Pfad, Metadaten nur mit Schalter."""
        p = self._puzzle(pid)
        link = p.get("online_link") or {}
        cfg = dict(p.get("config") or {})
        music = cfg.pop("music", None)
        out = {"title": share_title(p), "by": p.get("by") or "", "config": cfg,
               "music": {"volume": music.get("volume", .8), "fade": music.get("fade", True)} if music else None,
               "download": bool(link.get("download")), "meta": None}
        if link_metadata(link):
            out["meta"] = p.get("meta")
        return out

    def shared_music(self, pid):
        music = (self._puzzle(pid).get("config") or {}).get("music")
        if not music:
            raise KeyError("music")
        return self.music_path(music["id"])

    def count_view(self, pid, ip, agent, now=None):
        now = time.time() if now is None else now
        if not agent or VIEW_BOTS.search(agent):
            return False
        with self.lock:
            link = (self.puzzles.get(pid) or {}).get("online_link")
            if not link or not link.get("token"):
                return False
            seen = self.views_seen
            for key in [k for k, at in seen.items() if now - at >= VIEW_WINDOW]:
                del seen[key]
            key = (link["token"], ip)
            if key in seen:
                return False
            seen[key] = now
            link = {**link, "views": int(link.get("views") or 0) + 1, "last_viewed_at": now,
                    "view_days": count_day(link.get("view_days"), now)}
        self._update(pid, online_link=link)
        return True

    def share_path(self, token):
        return f"{self.share_prefix}/{token}/"

    def _share_entry(self, p, now):
        link = p["online_link"]
        expires = link.get("expires")
        path = self.share_path(link["token"])
        return {"id": p["id"], "title": share_title(p), "name": puzzle_name(p),
                "own_title": clean_title(link.get("title")), "created_at": link.get("created"), "expires_at": expires,
                "expired": bool(expires and now > expires), "days": link.get("days"),
                "valid_from": link.get("valid_from"), "pending": bool(link.get("valid_from") and now < link["valid_from"]),
                "views": int(link.get("views") or 0), "last_viewed_at": link.get("last_viewed_at"),
                "url": self.share_origin + path if self.share_origin else "", "path": path,
                "thumb": f"/api/puzzles/{p['id']}/thumb.jpg?v={int(p.get('updated') or 0)}",
                "download": bool(link.get("download")), "active": link_active(link),
                "metadata": link_metadata(link), "protected": bool(link.get("protected"))}

    def _apply_share_url(self):
        own = str(self.settings.get("share_public_url") or "").strip()
        self.share_origin, self.share_prefix, self.share_public_url = share_address(own or self.share_default_url)

    def share_setup(self):
        """Ob Live-Links gehen und unter welcher Adresse. default: Vorgabe aus dem Stack, custom: eigene Adresse gespeichert"""
        return {"enabled": bool(self.share_port), "url": self.share_public_url, "prefix": self.share_prefix,
                "default": share_address(self.share_default_url)[2],
                "custom": bool(str(self.settings.get("share_public_url") or "").strip())}

    def set_share_url(self, url):
        """Adresse der Live-Links in der App setzen (Box «Einstellungen», wie Collage). Leer = wieder die Vorgabe
        SHARE_PUBLIC_URL aus dem Stack. Der Pfad der Adresse ist das Präfix der Links; ohne Pfad gilt /puzzles."""
        url = str(url or "").strip()
        if url:
            parts = urllib.parse.urlsplit(url)
            if parts.scheme not in ("http", "https") or not parts.netloc or parts.query or parts.fragment or len(url) > 300:
                raise ValueError(Msg("shareUrlInvalid", "Die Adresse muss mit https:// beginnen, zum Beispiel "
                                                        "https://links.example.com/puzzles."))
            url = url.rstrip("/")
        with self.write_lock:
            settings = {**self.settings, "share_public_url": url}
            if not url:
                settings.pop("share_public_url")
            write_json(self.settings_path, settings)
            self.settings = settings
            self._apply_share_url()
        return self.share_setup()

    def list_shares(self):
        now = time.time()
        with self.lock:
            items = [json.loads(json.dumps(p)) for p in self.puzzles.values() if (p.get("online_link") or {}).get("token")]
        shares = [self._share_entry(p, now) for p in items]
        shares.sort(key=lambda s: s["created_at"] or 0, reverse=True)
        return {"shares": shares, **self.share_setup()}

    def purge_expired_links(self):
        """Box «Geteilte Puzzles»: alle abgelaufenen Live-Links löschen, die Puzzles selbst bleiben (wie Collage)"""
        now = time.time()
        with self.lock:
            ids = [p["id"] for p in self.puzzles.values()
                   if (p.get("online_link") or {}).get("expires") and now > p["online_link"]["expires"]]
        for pid in ids:
            self._update(pid, online_link=None)
        if ids:
            self._prune_passwords()
        return self.list_shares()

    def share_umami_tag(self, grund=None):
        """Umami für Freigabe- und Hinweisseiten (UMAMI_LINKS_ID, Tag der Marke): URL nur mit Präfix, leerer Referrer.
        Hinweisseiten (Entscheid 52) melden dazu das Ereignis «Hinweis» mit dem Grund."""
        if not self.umami_links:
            return ""
        url, site = self.umami_links
        masked = html.escape(json.dumps(self.share_prefix.rstrip("/") + "/"))
        # Auch das Ereignis mit maskierter URL, sonst ginge der Code mit
        event = (f" umami.track(function (p) {{ p.url = {masked}; p.referrer = ''; p.name = 'Hinweis'; "
                 f"p.data = {{ grund: {html.escape(json.dumps(grund))} }}; return p; }});") if grund else ""
        return (f'<script defer src="{html.escape(url)}/script.js" data-website-id="{html.escape(site)}" '
                f'data-tag="{html.escape(FAMILY_ID)}" data-auto-track="false" '
                f'onload="umami.track(function (p) {{ p.url = {masked}; p.referrer = \'\'; return p; }});{event}"></script>')

    def hint_csp(self):
        """CSP der Hinweisseite (wie Collage): nur eigene Logos, Stil und Skript inline, Umami nur mit UMAMI_LINKS_ID."""
        umami = ""
        if self.umami_links:
            parts = urllib.parse.urlsplit(self.umami_links[0])
            umami = f" {parts.scheme}://{parts.netloc}" if parts.scheme and parts.netloc else ""
        connect = umami.strip() or "'none'"
        return (f"default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'{umami}; "
                f"connect-src {connect}; form-action 'self'; base-uri 'none'; frame-ancestors 'none'")

    @staticmethod
    def view_days(link, now=None):
        now = time.time() if now is None else now
        oldest = time.strftime("%Y-%m-%d", time.localtime(now - VIEW_DAYS * 86400))
        left = int(link.get("views") or 0)
        days = []
        for day, n in sorted((link.get("view_days") or {}).items(), reverse=True):
            if day <= oldest:
                break
            n = min(int(n or 0), left)
            if n > 0:
                days.append({"date": day, "views": n})
                left -= n
        return days

    def internal_shares(self, app_url):
        """GET /api/internal/shares für «Shared Links» in Smart Albums (docs/shares-api.md dort)."""
        with self.lock:
            items = [json.loads(json.dumps(p)) for p in self.puzzles.values() if (p.get("online_link") or {}).get("token")]
        items.sort(key=lambda p: p["online_link"].get("created") or 0, reverse=True)
        return {"source": FAMILY_ID, "version": VERSION, "shares": [self._internal_entry(p, app_url) for p in items]}

    def _internal_entry(self, p, app_url):
        iso = lambda t: datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if t else None
        link = p["online_link"]
        url = self.share_origin + self.share_path(link["token"]) if self.share_origin else ""
        return {"id": link["token"], "title": share_title(p), "url": url, "item": puzzle_name(p),
                "person": None, "created": iso(link.get("created")),
                "validFrom": iso(link.get("valid_from")), "expires": iso(link.get("expires")),
                "protected": bool(link.get("protected")), "views": int(link.get("views") or 0), "viewDays": self.view_days(link),
                "preview": url + "card.jpg" if url else None,
                "open": f"{app_url}/?id={urllib.parse.quote(p['id'])}", "revocable": True,
                "actions": list(SHARE_ACTIONS), "active": link_active(link), "download": bool(link.get("download")),
                "metadata": link_metadata(link)}

    def patch_share(self, token, body, app_url, by="Smart Albums"):
        if not isinstance(body, dict) or not body:
            raise FieldError(None, Msg("emptyChange", "Nichts zu ändern."))
        for field in body:
            if PATCH_FIELDS.get(field) not in SHARE_ACTIONS:
                raise FieldError(field, Msg("fieldNotChangeable", "«{field}» lässt sich hier nicht ändern.", field=field))
        for field in ("download", "metadata", "active"):
            if field in body and not isinstance(body[field], bool):
                raise FieldError(field, Msg("notBool", "«{field}» muss true oder false sein.", field=field))
        expires = parse_utc(body["expires"], "expires") if "expires" in body else None
        start = parse_utc(body["validFrom"], "validFrom") if "validFrom" in body else None
        password = check_password(body["password"]) if "password" in body else None
        pid = self._by_token(token)
        link = dict(self._puzzle(pid)["online_link"])
        if "expires" in body:
            link["expires"], link["days"] = (expires, "until") if expires else (None, 0)
        if "validFrom" in body:
            link["valid_from"] = start
        new_start, new_end = link.get("valid_from"), link.get("expires")
        if new_start and new_end and new_end < new_start:
            raise FieldError("expires" if "expires" in body else "validFrom",
                             Msg("endBeforeStart", "Der Link würde ablaufen, bevor er gilt."))
        if "download" in body:
            link["download"] = body["download"]
        if "metadata" in body:
            set_metadata(link, body["metadata"])
        if "active" in body:
            set_active(link, body["active"])
        if "password" in body:
            self._set_password(link["token"], password)
            if password is None:
                link.pop("protected", None)
            else:
                link["protected"] = True
        self._update(pid, online_link=link)
        print(f"Live-Link von {pid} geändert durch {by}: {', '.join(sorted(body))}", flush=True)
        return self._internal_entry(self._puzzle(pid), app_url)


def image_type(data):
    return "image/webp" if data[:4] == b"RIFF" else "image/jpeg"


def make_handler(app, auth=None, register=None):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):
            pass

        def _send(self, status, body, ctype, extra=None):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            for key, value in {"Cache-Control": "no-store", **(extra or {})}.items():
                for one in (value if isinstance(value, list) else [value]):
                    self.send_header(key, one)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, data, status=200):
            self._send(status, json.dumps(data).encode(), "application/json")

        def _brand(self, name):
            path = brand_file(name)
            ctype = BRAND_TYPES[os.path.splitext(name)[1]]
            extra = {"Cache-Control": "public, max-age=86400"}
            if ctype == "image/svg+xml":
                extra["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'"
            self._file(path, ctype, extra)

        def _font(self, name):
            """Schriften aus app/fonts, nur die bekannten WOFF-Dateien"""
            if name not in {f + ".woff" for f in FONT_FILES.values()}:
                raise KeyError(name)
            self._file(os.path.join(APP_DIR, "fonts", name), "font/woff", {"Cache-Control": "public, max-age=604800"})

        def _base(self):
            scheme = "https" if login.is_https(self.client_address[0], self.headers) else "http"
            return f"{scheme}://{self.headers.get('Host', '')}"

        def _app_card(self):
            base = html.escape(self._base())
            return "\n".join([
                '<meta property="og:type" content="website">',
                '<meta property="og:site_name" content="Puzzle">',
                '<meta property="og:title" content="Puzzle">',
                f'<meta property="og:description" content="{html.escape(family.CLAIMS.get(FAMILY_ID, ""))}">',
                f'<meta property="og:image" content="{base}/brand/og-puzzle.png">',
                '<meta property="og:image:type" content="image/png">',
                '<meta property="og:image:width" content="1200">',
                '<meta property="og:image:height" content="630">',
                '<meta name="twitter:card" content="summary_large_image">',
            ])

        def _file(self, path, ctype, extra=None):
            """Datei senden, mit Range (Safari auf dem iPhone spielt Musik nur so)."""
            size = os.path.getsize(path)
            start, end, status = 0, size - 1, 200
            match = re.fullmatch(r"bytes=(\d*)-(\d*)", (self.headers.get("Range") or "").strip())
            if match and size and (match.group(1) or match.group(2)):
                if match.group(1):
                    start = int(match.group(1))
                    end = min(int(match.group(2)), size - 1) if match.group(2) else size - 1
                else:
                    start = max(0, size - int(match.group(2)))
                if start > end or start >= size:
                    self._send(416, b"", "text/plain", {"Content-Range": f"bytes */{size}"})
                    return
                status = 206
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(end - start + 1))
            self.send_header("Accept-Ranges", "bytes")
            if status == 206:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.send_header("X-Content-Type-Options", "nosniff")
            for key, value in {"Cache-Control": "no-store", **(extra or {})}.items():
                self.send_header(key, value)
            self.end_headers()
            if self.command == "HEAD":
                return
            with open(path, "rb") as f:
                f.seek(start)
                left = end - start + 1
                while left > 0:
                    chunk = f.read(min(left, 1024 * 1024))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)

        def _read(self, limit=1024 * 1024):
            length = int(self.headers.get("Content-Length") or 0)
            if length > limit:
                self.close_connection = True
                raise ValueError(Msg("fileTooBig", "Die Datei ist zu gross."))
            return self.rfile.read(length)

        def _body(self, limit=1024 * 1024):
            if not (self.headers.get("Content-Type") or "").startswith("application/json"):
                self._read(limit)
                raise ValueError(Msg("jsonExpected", "JSON erwartet"))
            try:
                data = json.loads(self._read(limit) or b"{}")
            except json.JSONDecodeError:
                raise ValueError(Msg("invalidJson", "Ungültiges JSON"))
            if not isinstance(data, dict):
                raise ValueError(Msg("invalidJson", "Ungültiges JSON"))
            return data

        def _route(self, handler):
            try:
                parts = self.path.split("?")[0].strip("/").split("/")
                if parts == ["api", "internal", "shares"] and self.command in ("GET", "HEAD"):
                    self._internal_shares()
                    return
                if len(parts) == 4 and parts[:3] == ["api", "internal", "shares"] and self.command == "DELETE":
                    self._internal_revoke(parts[3])
                    return
                if len(parts) == 4 and parts[:3] == ["api", "internal", "shares"] and self.command == "PATCH":
                    self._internal_patch(parts[3])
                    return
                if auth and auth.enabled and not self._public(parts) and not self._user():
                    self._deny(parts)
                    return
                handler(parts)
            except FieldError as e:
                self._json({**fields("error", of(e)), "field": e.field}, 400)
            except ValueError as e:
                self._json(fields("error", of(e)), 400)
            except (KeyError, FileNotFoundError):
                self._json(fields("error", Msg("notFound", "Nicht gefunden")), 404)
            except ImmichError as e:
                self._json(fields("error", Msg("immich", "Immich: {detail}", **fields("detail", of(e)))), 502)
            except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
                self.close_connection = True
            except Exception:
                traceback.print_exc()
                self._json(fields("error", Msg("internal", "Interner Fehler")), 500)

        def _internal_key(self):
            if not app.shares_api_key:
                raise KeyError(self.path)
            given = self.headers.get("Authorization") or ""
            token = given[7:].strip() if given[:7].lower() == "bearer " else ""
            if not hmac.compare_digest(token.encode(), app.shares_api_key.encode()):
                self._json(fields("error", Msg("unauthorized", "Schlüssel fehlt oder stimmt nicht.")), 401)
                return False
            return True

        def _internal_revoke(self, token):
            if not self._internal_key():
                return
            self._read()
            app.revoke_share(urllib.parse.unquote(token))
            self._send(204, b"", "text/plain")

        def _app_url(self):
            own = next((s["url"] for s in family.load_register(register.path) if s["id"] == register.own_id), "") if register else ""
            return own or self._base()

        def _internal_shares(self):
            if not self._internal_key():
                return
            self._json(app.internal_shares(self._app_url()))

        def _internal_patch(self, token):
            if not self._internal_key():
                return
            try:
                body = json.loads(self._read() or b"null")
            except ValueError:
                raise FieldError(None, Msg("invalidJson", "Ungültiges JSON"))
            self._json(app.patch_share(urllib.parse.unquote(token), body, self._app_url()))

        # ---------- Login ----------
        def _public(self, parts):
            return (parts in (["health"], ["login"], ["favicon.ico"], ["favicon.svg"], ["i18n.js"])
                    or (len(parts) == 1 and parts[0] in text_files()) or (len(parts) == 2 and parts[0] == "brand")
                    or parts[:2] == ["api", "auth"])

        def _user(self):
            return auth.session_user(self.headers.get("Cookie"))

        def _by(self):
            if auth and auth.enabled:
                return auth.name or self._user() or ""
            return ""

        def _deny(self, parts):
            if self.command == "GET" and parts[0] != "api":
                target = "/login?next=" + urllib.parse.quote(login.safe_next(self.path), safe="")
                self._send(303, b"", "text/plain", {"Location": target})
                return
            if int(self.headers.get("Content-Length") or 0) <= 1024 * 1024:
                self._read()
            else:
                self.close_connection = True
            self._json(fields("error", Msg("loginRequired", "Bitte zuerst anmelden.")), 401)

        def _cookie(self, value, max_age=None):
            secure = "; Secure" if login.is_https(self.client_address[0], self.headers) else ""
            age = f"; Max-Age={max_age}" if max_age is not None else ""
            return f"{login.COOKIE}={value}; Path=/; HttpOnly; SameSite=Lax{age}{secure}"

        def _family_cookie(self, value, max_age=None):
            https = login.is_https(self.client_address[0], self.headers)
            return auth.family.header(value, self.headers.get("Host", ""), https, max_age)

        def _i18n(self, root="/"):
            return "".join(f'<script src="{root}{name}?v={VERSION}"></script>\n' for name in text_files() + ["i18n.js"])

        def _login_page(self):
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            target = login.safe_next(query.get("next", ["/"])[0])
            if not (auth and auth.enabled) or self._user():
                self._send(303, b"", "text/plain", {"Location": target})
                return
            with open(os.path.join(APP_DIR, "login.html"), encoding="utf-8") as f:
                page = f.read().replace("{{VERSION}}", VERSION).replace("{{OG}}", self._app_card())
            page = page.replace("{{I18N}}", self._i18n())
            self._send(200, page.encode(), "text/html; charset=utf-8", {"X-Frame-Options": "DENY"})

        def _too_many(self, wait):
            body = {**fields("error", Msg("tooManyAttempts", "Zu viele Anmeldeversuche. Bitte in {n} Minuten wieder versuchen.",
                                          n=math.ceil(wait / 60))), "retry_after": wait}
            self._send(429, json.dumps(body).encode(), "application/json", {"Retry-After": str(wait)})

        def _auth_post(self, action):
            if action == "logout":
                self._read()
                cookies = [self._cookie("", 0)]
                if auth and auth.enabled and auth.family.enabled:
                    cookies.append(self._family_cookie("", 0))
                self._send(200, b'{"ok": true}', "application/json", {"Set-Cookie": cookies})
            elif action == "login":
                if not (auth and auth.enabled):
                    self._read()
                    self._json({"ok": True, "enabled": False})
                    return
                ip = login.client_ip(self.client_address[0], self.headers)
                wait = auth.wait_seconds(ip)
                if wait:
                    self._read()
                    self._too_many(wait)
                    return
                data = self._body()
                if not auth.check(data.get("username"), data.get("password")):
                    wait = auth.failed(ip)
                    print(f"Login fehlgeschlagen von {ip}" + (", gesperrt" if wait else ""), flush=True)
                    if wait:
                        self._too_many(wait)
                    else:
                        self._json(fields("error", Msg("loginWrong", "Benutzername oder Passwort stimmt nicht.")), 401)
                    return
                auth.succeeded(ip)
                max_age = login.REMEMBER_DAYS * 86400 if data.get("remember") else None
                cookies = [self._cookie(auth.make_cookie(), max_age)]
                if auth.family.enabled:
                    cookies.append(self._family_cookie(auth.family.make_value(auth.user, login.REMEMBER_DAYS), max_age))
                self._send(200, json.dumps({"ok": True, "user": auth.user}).encode(), "application/json", {"Set-Cookie": cookies})
            else:
                raise KeyError(self.path)

        def do_GET(self):
            self._route(self._get)

        def do_HEAD(self):
            self._route(self._get)

        def do_POST(self):
            self._route(self._post)

        def do_DELETE(self):
            self._route(self._delete)

        def do_PATCH(self):
            self._route(self._no_patch)

        def _no_patch(self, parts):
            raise KeyError(self.path)

        def _script(self, name):
            self._file(os.path.join(APP_DIR, name), "text/css; charset=utf-8" if name.endswith(".css") else "text/javascript; charset=utf-8")

        def _get(self, parts):
            if parts == ["health"]:
                self._json({"ok": True, "version": VERSION})
            elif parts == ["login"]:
                self._login_page()
            elif parts in (["favicon.svg"], ["favicon.ico"]):
                self._brand("icon-puzzle.svg")
            elif len(parts) == 2 and parts[0] == "brand":
                self._brand(parts[1])
            elif len(parts) == 2 and parts[0] == "fonts":
                self._font(parts[1])
            elif parts == ["api", "auth", "session"]:
                enabled = bool(auth and auth.enabled)
                user = self._user() if enabled else None
                self._json({"enabled": enabled, "user": user, "name": (auth.name or None) if user and user == auth.user else None})
            elif parts == ["api", "family"]:
                self._json(register.view() if register else {"self": None, "services": [], "openMode": family.OPEN_MODE_DEFAULT})
            elif parts == [""]:
                with open(os.path.join(APP_DIR, "puzzle.html"), encoding="utf-8") as f:
                    page = f.read().replace("{{UMAMI}}", app.umami_tag).replace("{{VERSION}}", VERSION).replace("{{OG}}", self._app_card())
                self._send(200, page.replace("{{I18N}}", self._i18n()).encode(), "text/html; charset=utf-8")
            elif parts in (["common.css"], ["common.js"], ["profile.js"], ["i18n.js"], ["puzzle-engine.js"]) \
                    or (len(parts) == 1 and parts[0] in text_files()):
                self._script(parts[0])
            elif parts == ["api", "version"]:
                day, month, year = VERSION_DATE.split(".")
                self._json({"version": VERSION, "date": f"{year}-{month}-{day}", "immich": bool(app.immich)})
            elif parts == ["api", "changelog"]:
                self._json({"types": list(CHANGE_TYPES), "releases": read_changelog()})
            elif parts == ["api", "immich"]:
                if not app.immich:
                    self._json({"ok": False, "configured": False})
                    return
                try:
                    self._json({"ok": True, "version": app.immich.server_version()})
                except ImmichError as e:
                    self._json({"ok": False, **fields("error", of(e))})
            elif parts == ["api", "sources"]:  # Alben und Personen für die Wahl des Motivs
                self._json({"albums": app.need_immich().albums(), "people": app.immich.people()})
            elif len(parts) == 4 and parts[:2] == ["api", "assets"] and parts[3] in ("thumb", "preview"):
                data = app.need_immich().image(check_id(parts[2]), "thumbnail" if parts[3] == "thumb" else "preview")
                self._send(200, data, image_type(data), {"Cache-Control": "private, max-age=3600"})
            elif parts == ["api", "music"]:
                self._json(app.music_list())
            elif len(parts) == 3 and parts[:2] == ["api", "music"]:
                path = app.music_path(urllib.parse.unquote(parts[2]))
                self._file(path, MUSIC_TYPES[os.path.splitext(path)[1].lower()], {"Cache-Control": "private, max-age=3600"})
            elif parts == ["api", "puzzles"]:
                self._json(app.list_puzzles())
            elif len(parts) == 3 and parts[:2] == ["api", "puzzles"]:
                self._json(app.get_puzzle(parts[2]))
            elif len(parts) == 4 and parts[:2] == ["api", "puzzles"] and parts[3] in ("photo.jpg", "thumb.jpg", "card.jpg"):
                self._file(app.file(parts[2], parts[3]), "image/jpeg", {"Cache-Control": "private, max-age=86400"})
            elif parts == ["api", "shares"]:
                self._json(app.list_shares())
            else:
                raise KeyError(self.path)

        def _post(self, parts):
            if len(parts) == 3 and parts[:2] == ["api", "auth"]:
                self._auth_post(parts[2])
            elif parts == ["api", "photos"]:
                body = self._body()
                albums = [check_id(a) for a in body.get("albums") or []][:20]
                people = [check_id(p) for p in body.get("people") or []][:20]
                if not albums and not people:
                    raise ValueError(Msg("noSource", "Bitte ein Album oder eine Person wählen."))
                self._json({"photos": app.need_immich().photo_list(albums, people, limit=500)})
            elif parts == ["api", "music"]:
                if self.headers.get("X-Music-Upload") != "1":
                    self._read(MAX_MUSIC)
                    raise ValueError(Msg("invalidUpload", "Ungültiger Upload"))
                self._json(app.add_music(self._read(MAX_MUSIC), urllib.parse.unquote(self.headers.get("X-File-Name") or "")))
            elif parts == ["api", "puzzles"]:
                self._json(app.save_puzzle(self._body(MAX_BODY), None, self._by()))
            elif len(parts) == 3 and parts[:2] == ["api", "puzzles"]:
                self._json(app.save_puzzle(self._body(MAX_BODY), parts[2]))
            elif len(parts) == 4 and parts[:2] == ["api", "puzzles"] and parts[3] == "link":
                self._json(app.online_link(parts[2], self._body()))
            elif parts == ["api", "shares", "url"]:  # Einstellung «Adresse der Live-Links»
                self._json(app.set_share_url(self._body().get("url")))
            else:
                self._read()
                raise KeyError(self.path)

        def _delete(self, parts):
            self._read()
            if len(parts) == 3 and parts[:2] == ["api", "puzzles"]:
                self._json(app.delete_puzzle(parts[2]))
            elif len(parts) == 4 and parts[:2] == ["api", "puzzles"] and parts[3] == "link":
                self._json(app.revoke_online_link(parts[2]))
            elif parts == ["api", "shares", "expired"]:
                self._json(app.purge_expired_links())
            elif len(parts) == 3 and parts[:2] == ["api", "music"]:
                self._json(app.delete_music(urllib.parse.unquote(parts[2])))
            else:
                raise KeyError(self.path)

    return Handler


def serve(app, host, port, auth=None, register=None):
    return ThreadingHTTPServer((host, port), make_handler(app, auth, register))


def make_share_handler(app):
    """Der Freigabe-Port: nur die Empfängerseite geteilter Puzzles. Alles andere, auch die ganze API, gibt 404."""
    class ShareHandler(make_handler(app)):
        def _send(self, status, body, ctype, extra=None):
            super()._send(status, body, ctype, {**SHARE_HEADERS, **(extra or {})})

        def _file(self, path, ctype, extra=None):
            super()._file(path, ctype, {**SHARE_HEADERS, **(extra or {})})

        def _lang(self):
            """Sprache der Hinweisseite (Entscheid 52): ?lang= aus dem Sprachlink, sonst der erste Eintrag in
            Accept-Language, der mit de oder en beginnt, sonst Deutsch."""
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get("lang") or [""]
            if query[0] in ("de", "en"):
                return query[0]
            for part in (self.headers.get("Accept-Language") or "").split(","):
                tag = part.split(";")[0].strip().lower()
                if tag[:2] in ("de", "en"):
                    return tag[:2]
            return "de"

        def _pw_page(self, status, error=None):
            lang = self._lang()
            heading, text, field, go, bad, lock = SHARE_PW[lang]
            box = {"bad": bad, "lock": lock}.get(error)
            wait = max(1, int(app.pw_blocked_until(self._ip()) - time.time())) if error == "lock" else 0
            countdown = (f'<script>(() => {{ const b = document.querySelector(".go"), t = b.textContent; let s = {wait};'
                         ' const tick = () => { if (s <= 0) { b.disabled = false; b.textContent = t; return; }'
                         ' b.textContent = `${t} (${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")})`; s--;'
                         ' setTimeout(tick, 1000); }; tick(); })();</script>') if wait else ""
            form = (f'<p class="s">{text}</p><form method="post" autocomplete="off">'
                    f'<input type="password" name="pw" required autofocus aria-label="{field}" placeholder="{field}"'
                    + (' class="bad" aria-invalid="true"' if error == "bad" else "") + ">"
                    + (f'<div class="errbox" role="alert">{box}</div>' if box else "")
                    + f'<button class="go"{" disabled" if error == "lock" else ""}>{go}</button></form>{countdown}')
            extra = {"Retry-After": str(wait or PW_BLOCK)} if status == 429 else None
            self._card(lang, heading, form, status, "passwort", extra)

        def _page(self, case, start=None):
            """Hinweisseite ohne Inhalt für einen Fall aus NOTICE."""
            lang = self._lang()
            status, grund, texts = NOTICE[case]
            heading, sentence, hint = texts[lang]
            if start:
                sentence = start_text(start, lang)
            self._card(lang, heading, f'<p class="s">{html.escape(sentence)}</p><p class="h">{html.escape(hint)}</p>',
                       status, grund)

        def _card(self, lang, heading, text, status, grund, extra=None):
            other, other_name = OTHER_LANG[lang]
            with open(os.path.join(APP_DIR, "share.html"), encoding="utf-8") as f:
                page = f.read().replace("{{HEADING}}", html.escape(heading)).replace("{{TEXT}}", text)
            page = (page.replace('<html lang="de">', f'<html lang="{lang}">').replace("{{FOOT}}", SHARE_FOOT[lang])
                    .replace("{{OTHER}}", f'<a href="?lang={other}" hreflang="{other}" lang="{other}">{other_name}</a>')
                    .replace("{{UMAMI}}", app.share_umami_tag(grund)).replace("{{ROOT}}", app.share_prefix + "/"))
            self._send(status, page.encode(), "text/html; charset=utf-8", {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
                                                                            "X-Robots-Tag": "noindex, nofollow",
                                                                            "Content-Security-Policy": app.hint_csp(), **(extra or {})})

        def _route(self, handler):
            """Fehler des Freigabe-Ports: Seiten bekommen die Hinweisseite, JSON, Bilder und Musik denselben Status."""
            html_wanted = self.command in ("GET", "HEAD", "POST") and "text/html" in (self.headers.get("Accept") or "")
            try:
                handler(self.path.split("?")[0].strip("/").split("/"))
            except NotYetValid as early:
                if html_wanted:
                    self._page("soon", early.start)
                else:
                    self._json(fields("error", Msg("notYetValid", "Dieser Link gilt noch nicht.")), 403)
            except PasswordRequired:
                self._json(fields("error", Msg("passwordRequired", "Dieser Link ist mit einem Passwort geschützt.")), 401)
            except NotActive:
                if html_wanted:
                    self._page("off")
                else:
                    self._json(fields("error", Msg("linkOff", "Dieser Link ist nicht aktiv.")), 403)
            except Expired:
                if html_wanted:
                    self._page("expired")
                else:
                    self._json(fields("error", Msg("linkExpired", "Dieser Link ist abgelaufen.")), 410)
            except BaseAddress:
                if html_wanted:
                    self._page("base")
                else:
                    self._json(fields("error", Msg("notFound", "Nicht gefunden")), 404)
            except (ValueError, KeyError, FileNotFoundError):
                if html_wanted:
                    self._page("unknown")
                else:
                    self._json(fields("error", Msg("notFound", "Nicht gefunden")), 404)
            except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
                self.close_connection = True
            except Exception:
                traceback.print_exc()
                self._json(fields("error", Msg("internal", "Interner Fehler")), 500)

        def _get(self, parts):
            prefix = app.share_prefix.strip("/").split("/")
            if parts[:len(prefix)] != prefix:
                if not self._static(parts):
                    raise KeyError(self.path)
                return
            rest = parts[len(prefix):]
            if self._static(rest):
                return
            pid = app.shared_puzzle(rest[0])
            if app.share_locked(rest[0], self._unlock_cookie()):
                if rest[1:] not in ([], [""]):
                    raise PasswordRequired()
                if not self.path.split("?")[0].endswith("/"):
                    self._send(301, b"", "text/plain", {"Location": self.path.split("?")[0] + "/"})
                elif app.pw_blocked_until(self._ip()):
                    self._pw_page(429, "lock")
                else:
                    self._pw_page(200)
                return
            self._shared(pid, rest[0], rest[1:])

        def _shared(self, pid, token, rest):
            if rest in ([], [""]):
                if not self.path.split("?")[0].endswith("/"):
                    self._send(301, b"", "text/plain", {"Location": self.path.split("?")[0] + "/"})
                    return
                self._play_page(pid, token)
                if self.command == "GET":
                    self._count_view(pid)
            elif rest == ["puzzle.json"]:
                self._json(app.shared_view(pid))
            elif rest in (["photo.jpg"], ["card.jpg"]):
                self._file(app.file(pid, rest[0]), "image/jpeg", {"Cache-Control": "private, max-age=3600"})
            elif rest == ["music"]:
                path = app.shared_music(pid)
                self._file(path, MUSIC_TYPES[os.path.splitext(path)[1].lower()], {"Cache-Control": "private, max-age=3600"})
            elif rest == ["download"]:
                view = app.shared_view(pid)
                if not view["download"]:
                    raise KeyError("download")
                name = " ".join(re.sub(r"[^A-Za-z0-9 _-]+", " ", unicodedata.normalize("NFKD", view["title"])
                                       .encode("ascii", "ignore").decode()).split()) or "Puzzle"
                self._file(app.file(pid, "photo.jpg"), "image/jpeg",
                           {"Content-Disposition": f'attachment; filename="{name}.jpg"'})
            else:
                raise KeyError(self.path)

        def _play_page(self, pid, token):
            view = app.shared_view(pid)
            origin = app.share_origin or self._base()
            url = origin + app.share_path(token)
            lang = self._lang()
            title = html.escape(view["title"])
            og = "\n".join([
                '<meta property="og:type" content="website">',
                '<meta property="og:site_name" content="Puzzle">',
                f'<meta property="og:title" content="{title}">',
                f'<meta property="og:description" content="{html.escape(CARD_TEXT[lang])}">',
                f'<meta property="og:url" content="{html.escape(url)}">',
                f'<meta property="og:image" content="{html.escape(url)}card.jpg">',
                '<meta property="og:image:type" content="image/jpeg">',
                '<meta property="og:image:width" content="1200">',
                '<meta property="og:image:height" content="630">',
                '<meta name="twitter:card" content="summary_large_image">',
            ])
            root = app.share_prefix + "/"
            with open(os.path.join(APP_DIR, "play.html"), encoding="utf-8") as f:
                page = f.read()
            page = (page.replace("{{TITLE}}", title).replace("{{OG}}", og).replace("{{ROOT}}", root)
                    .replace("{{I18N}}", self._i18n(root)).replace("{{VERSION}}", VERSION)
                    .replace("{{UMAMI}}", app.share_umami_tag()).replace('<html lang="de">', f'<html lang="{lang}">'))
            self._send(200, page.encode(), "text/html; charset=utf-8")

        def _static(self, parts):
            if parts in ([], [""]):
                raise BaseAddress()  # Basisadresse ohne Code: 404 (Entscheid 52)
            elif parts in (["favicon.svg"], ["favicon.ico"]):
                self._brand("icon-puzzle.svg")
            elif len(parts) == 2 and parts[0] == "brand":
                self._brand(parts[1])
            elif len(parts) == 2 and parts[0] == "fonts":
                self._font(parts[1])
            elif parts == ["robots.txt"]:
                self._send(200, f"User-agent: *\nAllow: {app.share_prefix}/\nDisallow: /\n".encode(),
                           "text/plain; charset=utf-8")
            elif parts in (["i18n.js"], ["puzzle-engine.js"]) or (len(parts) == 1 and parts[0] in text_files()):
                self._script(parts[0])
            else:
                return False
            return True

        def _count_view(self, pid):
            purpose = " ".join(self.headers.get(h) or "" for h in ("Sec-Purpose", "Purpose", "X-Purpose", "X-Moz")).lower()
            if "prefetch" in purpose or "preview" in purpose:
                return
            try:
                app.count_view(pid, self._ip(), self.headers.get("User-Agent") or "")
            except Exception:
                traceback.print_exc()

        def _ip(self):
            return login.client_ip(self.client_address[0], self.headers)

        def _unlock_cookie(self):
            try:
                morsel = SimpleCookie(self.headers.get("Cookie") or "").get(UNLOCK_COOKIE)
            except Exception:
                return None
            return morsel.value if morsel else None

        def _post(self, parts):
            """Nur das Passwort-Formular an der Wurzel eines geschützten Links (Entscheid 49)."""
            body = self._read(4096)
            prefix = app.share_prefix.strip("/").split("/")
            rest = parts[len(prefix):] if parts[:len(prefix)] == prefix else []
            if len(rest) != 1:
                raise KeyError(self.path)
            token = rest[0]
            app.shared_puzzle(token)
            if not app.share_locked(token, None):
                raise KeyError(self.path)
            ip = self._ip()
            if app.pw_blocked_until(ip):
                self._pw_page(429, "lock")
                return
            pw = (urllib.parse.parse_qs(body.decode("utf-8", "replace")).get("pw") or [""])[0]
            if app.password_ok(token, pw):
                path = app.share_path(token)
                cookie = (f"{UNLOCK_COOKIE}={app.unlock_value(token)}; Path={path.rstrip('/')}; "
                          f"Max-Age={UNLOCK_DAYS * 86400}; HttpOnly; SameSite=Lax")
                if login.is_https(self.client_address[0], self.headers):
                    cookie += "; Secure"
                self._send(303, b"", "text/plain", {"Location": path, "Set-Cookie": cookie})
            elif app.note_pw_fail(ip):
                self._pw_page(429, "lock")
            else:
                self._pw_page(401, "bad")

        def _delete(self, parts):
            self._read()
            raise KeyError(self.path)

    return ShareHandler


def serve_share(app, host, port):
    return ThreadingHTTPServer((host, port), make_share_handler(app))


def main():
    url, key = os.environ.get("IMMICH_URL", "").strip(), os.environ.get("IMMICH_API_KEY", "").strip()
    if bool(url) != bool(key):
        raise SystemExit("IMMICH_URL und IMMICH_API_KEY gehören zusammen: beide setzen oder beide leer lassen.")
    app = App(url, key, os.environ.get("DATA_DIR", "data"), os.environ.get("PUZZLE_DIR", ""),
              os.environ.get("MUSIC_DIR", "/music"),
              share_port=int(os.environ.get("SHARE_PORT") or 0), share_public_url=os.environ.get("SHARE_PUBLIC_URL", ""),
              shares_api_key=os.environ.get("SHARES_API_KEY", ""), umami_url=os.environ.get("UMAMI_URL", ""),
              umami_id=os.environ.get("UMAMI_WEBSITE_ID", ""), umami_links_id=os.environ.get("UMAMI_LINKS_ID", ""))
    host, port = os.environ.get("HOST", "0.0.0.0"), int(os.environ.get("PORT", "8080"))
    auth = login.Auth.from_env(app.data_dir)
    print(f"immich-puzzle {VERSION} auf {host}:{port}, Ablage {app.puzzle_dir}, Musik {app.music_dir}"
          + ("" if app.immich else ", ohne Immich (nur Hochladen)"), flush=True)
    print(f"Login aktiv für {auth.user}" if auth.enabled else "Login aus (LOGIN_PASSWORD fehlt)", flush=True)
    register = family_register()
    view = register.view()
    print(f"Familie: {register.path} ({len(view['services'])} Dienste, öffnen: {view['openMode']}), Familien-Login "
          + ("an" if auth.enabled and auth.family.enabled else "aus"), flush=True)
    if app.share_port:
        print(f"Live-Links auf {host}:{app.share_port}, Präfix {app.share_prefix}", flush=True)
        threading.Thread(target=serve_share(app, host, app.share_port).serve_forever, daemon=True).start()
    serve(app, host, port, auth, register).serve_forever()


if __name__ == "__main__":
    main()
