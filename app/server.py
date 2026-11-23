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
import random
import re
import secrets
import shutil
import signal
import sys
import threading
import time
import traceback
import unicodedata
import urllib.parse
import uuid
import warnings
from datetime import datetime, timedelta, timezone
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from PIL import Image, ImageOps

import auth as login
import family
import render
from immich import Immich, ImmichError, check_id
from msg import Msg, fields, of
from notify import Notifier, RunMarker, clean_time

try:
    import mutagen  # Länge der Musikstücke; fehlt es, misst die Seite selbst
except ImportError:  # pragma: no cover
    mutagen = None

APP_DIR = os.path.dirname(os.path.abspath(__file__))
VERSION = "0.1.19"  # bei jedem Build hochzählen, zuoberst in app/CHANGELOG.md gleich
VERSION_DATE = "23.11.2026"


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
BRAND_TYPES = {".svg": "image/svg+xml", ".png": "image/png", ".jpg": "image/jpeg"}


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
ALERT_KINDS = ("errors", "summary")  # Meldungen über ntfy (0.1.18): je Art ein Schalter, Werkseinstellung an
ALERT_TIME = "08:00"  # Tageszusammenfassung für den Vortag
HEARTBEAT = 60  # Sekunden: Takt der Meldeschleife (Lebenszeichen in run.json, Tageszusammenfassung)
WEEKDAYS = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")


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


def day_start(ts):
    """Beginn des Tages eines Zeitstempels, 00:00:00 Lokalzeit, als Epoch."""
    d = datetime.fromtimestamp(ts).date()
    return datetime(d.year, d.month, d.day).timestamp()


def link_from(link):
    """Gültig ab eines Links (Roger 22.11.2026: jeder Link hat ein Datum, Standard heute);
    ältere Links ohne Datum gelten ab dem Tag, an dem sie erstellt wurden."""
    return link.get("valid_from") or (day_start(link["created"]) if link.get("created") else None)


def link_expiry(body, now=None, start=None):
    """Ablauf aus days ("today", 1, 7, 30, 60, 90, 365, 0) oder until (JJJJ-MM-TT, frühestens heute).
    Die Tage zählen ab «Gültig ab» (start), frühestens ab heute (Roger 22.11.2026).
    Gibt (expires, days) zurück; jeder Ablauf endet am Tagesende, 0 = ohne Ablauf."""
    today = datetime.fromtimestamp(now if now is not None else time.time()).date()
    base = max(today, datetime.fromtimestamp(start).date()) if start else today
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
    return day_end(base + timedelta(days=0 if days == "today" else days)), days


def link_start(value, now=None):
    """Gültig ab (Entscheid 39): JJJJ-MM-TT, frühestens heute, gilt ab 00:00:00 Lokalzeit.
    Leer = heute; seit 0.1.8 immer als Datum gespeichert (Roger 22.11.2026)."""
    today = datetime.fromtimestamp(now if now is not None else time.time()).date()
    if not value:
        return datetime(today.year, today.month, today.day).timestamp()
    try:
        start = datetime.strptime(str(value), "%Y-%m-%d").date()
    except ValueError:
        start = None
    if not start or start < today:
        raise ValueError(Msg("unknownStart", "Unbekanntes Startdatum: frühestens heute."))
    return datetime(start.year, start.month, start.day).timestamp()


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


class TooLarge(ValueError):
    """Anfrage grösser als erlaubt: 413 (Sicherheitsprüfung Kiosk 0.1.9)."""


class FieldError(ValueError):
    """Ungültiges Feld beim Ändern aus Smart Albums (Entscheid 48): 400 mit {"error", "field"}."""
    def __init__(self, field, message):
        super().__init__(message)
        self.field = field


# Was Smart Albums an einem Puzzle-Link ändern darf (Entscheid 48, Passwort ab Entscheid 49, Metadaten ab Entscheid 50).
# Ab 0.1.17: download = Foto, video = Video (getrennt), share = «Teilen erlauben» (Vorgabe aus, noch ohne Anzeige)
SHARE_ACTIONS = ["expiry", "validFrom", "password", "download", "video", "share", "metadata", "off", "delete"]
PATCH_FIELDS = {"expires": "expiry", "validFrom": "validFrom", "password": "password", "download": "download",
                "video": "video", "share": "share", "metadata": "metadata", "active": "off"}
LINK_FLAGS = ("download", "video", "share")  # Schalter am Link, Vorgabe aus

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
    """Link ein- oder ausschalten (Entscheid 48). Gespeichert wird nur «aus», ältere Links gelten als aktiv.
    off_since: seit wann aus (Shares-API offSince), bleibt beim erneuten Ausschalten, fällt beim Einschalten weg."""
    if on:
        link.pop("active", None)
        link.pop("off_since", None)
    else:
        if link.get("active") is not False:
            link["off_since"] = time.time()
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


# Protokoll (Vorlage Smart Albums): wann, wer, was, in protocol.jsonl, die letzten PROTOCOL_KEEP Einträge.
# Ein Eintrag hat einen Code (what) und Werte (vars), den Text macht die Oberfläche in ihrer Sprache (log.<what>).
# Wer: role admin (angemeldet, who = Name), smart (Smart Albums über die Shares-API), visitor (Live-Link), system.
PROTOCOL_KEEP = 2000
PROTOCOL_MERGE = 10 * 60  # Automatisches Speichern: dasselbe Puzzle, derselbe Wer, innert 10 Minuten = ein Eintrag
_actor = threading.local()


def set_actor(role, who=""):
    _actor.role, _actor.who = role, str(who or "")[:80]


def actor():
    return getattr(_actor, "role", "system"), getattr(_actor, "who", "")


def link_changes(old, new, password_set=...):
    """Was sich an einem Live-Link geändert hat, als Codes fürs Protokoll (log.f.<code>)."""
    out = []
    flags = [(k, lambda l, k=k: bool(l.get(k))) for k in LINK_FLAGS]
    for key, flag in (*flags, ("metadata", link_metadata), ("active", link_active)):
        if flag(old) != flag(new):
            out.append(f"{key}:{'on' if flag(new) else 'off'}")
    if password_set is not ...:
        out.append("password:" + ("removed" if password_set is None else "set"))
    if link_from(old) != link_from(new):
        out.append("validFrom")
    if old.get("expires") != new.get("expires"):
        out.append("expires")
    if old.get("title") != new.get("title"):
        out.append("title")
    return out


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
VISITOR_ID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")  # Zufalls-Kennung des Besuchers aus dem Browser (Like, 0.1.8)
LIKES_MAX = 10000  # Obergrenze gespeicherter Like-Kennungen pro Link
# Bots, Crawler und Link-Vorschauen der Messenger zählen nicht als Aufruf
VIEW_BOTS = re.compile(r"bot|crawl|spider|slurp|preview|facebookexternalhit|facebot|whatsapp|telegram|slack|discord|"
                       r"twitter|linkedin|skype|signal|viber|embedly|pinterest|vkshare|mastodon|bluesky|iframely|"
                       r"headless|lighthouse|curl|wget|python|go-http|java/|okhttp|axios|node-fetch|httpclient|"
                       r"scan|monitor|uptime|check", re.I)
SHARE_HEADERS = {"Referrer-Policy": "no-referrer", "X-Robots-Tag": "noindex, nofollow"}


def with_nonce(page, nonce):
    """Alle <script>-Tags der Seite mit der Nonce versehen (Seiten mit strenger CSP)."""
    return page.replace("<script", f'<script nonce="{nonce}"')


def page_csp(nonce, umami=""):
    """Strenge CSP für Gäste-Editor und Abspielseite (Sicherheitsprüfung Kiosk 0.1.9): Skripte nur eigene und mit
    Nonce, dazu Umami; keine Einbettung in fremde Seiten."""
    extra = f" {umami}" if umami else ""
    return (f"default-src 'self'; script-src 'self' 'nonce-{nonce}'{extra}; style-src 'self' 'unsafe-inline'; "
            f"img-src 'self' data: blob:; media-src 'self' blob:; font-src 'self'; connect-src 'self'{extra}; "
            f"object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")
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
EDGES = ("classic", "round")  # Form der Nasen (0.1.15): organisch (Standard, Form bis 0.1.14) oder klassisch (gerade bis zur Nase); fehlt = organisch
DUR_MODES = ("music", "own")  # Dauer wie die Musik (höchstens MAX_T) oder eigene Sekunden
HOLD_MIN, HOLD_MAX = 2, 10  # So lange bleibt das fertige Bild stehen (Sekunden, heranzoomen)
TEMPO_MAX = 2  # Stärke des Tempo-Bogens (0 = gleichmässig, 2 = sehr stark)
FRAME_AT = (15, 70, 40)  # Rahmen fertig nach … % der Zeit bis «fertig» (min, max, Vorgabe)
RAMP = (0, 60, 5)  # Schneller werden ab … % der Zeit
LEAD = (0, 10, 1.5)  # Start nach … Sekunden (0.1.8); fehlt bei älteren Puzzles, dann gilt der bisherige Start
TEXT_SIZE = (50, 200, 100)  # Schriftgrösse in %
GHOST_PCT = (5, 60, 15)  # Deckkraft des Motivs beim Start in % (E1, 0.1.8)
TEXT_ARC = (-100, 100, 0)  # Bogen des Schriftzugs, 0 = gerade, + nach oben gewölbt (C1, 0.1.8)
TEXT_ROT = (-45, 45, 0)  # Drehung des Schriftzugs in Grad (C2, 0.1.8)
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
# Schutz vor Dekompressionsbomben (Sicherheitsprüfung Kiosk 0.1.9): höchstens 40 MP, nur JPEG/PNG/WEBP,
# höchstens zwei Fotos gleichzeitig dekodieren (Speicher und CPU)
MAX_PIXELS = 40_000_000
Image.MAX_IMAGE_PIXELS = MAX_PIXELS
warnings.simplefilter("error", Image.DecompressionBombWarning)
PHOTO_FORMATS = ("JPEG", "PNG", "WEBP")
DECODE_SLOTS = threading.BoundedSemaphore(2)
THUMB_SIDE = 360
CARD_SIZE = (1200, 630)
# Ab 0.1.8 zeigt das Vorschaubild das Puzzle (Teile, drei fehlen noch); ältere card.jpg werden beim Abruf neu erstellt
CARD_PUZZLE_SINCE = datetime(2026, 11, 22, 12, tzinfo=timezone.utc).timestamp()
MUSIC_TYPES = {".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".aac": "audio/aac", ".wav": "audio/wav",
               ".ogg": "audio/ogg", ".flac": "audio/flac"}
MAX_MUSIC = 30 * 1024 * 1024
MUSIC_UPLOAD = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.()-]{0,120}$")


def bad_setting(field):
    return ValueError(Msg("badSetting", "Ungültige Einstellung «{field}».", field=field))


STEP_CHOICE = ("crop", "music", "puzzle", "dur", "text", "view")  # Schritte, die man selber einstellen oder beim Standard lassen kann
FOR_MAX, NOTE_MAX = 120, 2000


def clean_steps(steps):
    """Welche Schritte selber eingestellt werden (Panel «Puzzle Details»). Fehlt ein Schritt, gilt «selber»."""
    if steps is None:
        return None
    if not isinstance(steps, dict):
        raise bad_setting("steps")
    return {k: bool(steps.get(k, True)) for k in STEP_CHOICE}


def clean_details(body):
    """Für wen / Anlass, interne Notiz und Schrittwahl eines Puzzles. Nur Felder, die im Body stehen."""
    out = {}
    if "forWhom" in body:
        out["for"] = " ".join(str(body.get("forWhom") or "").split())[:FOR_MAX]
    if "note" in body:
        note = body.get("note") or ""
        if not isinstance(note, str):
            raise bad_setting("note")
        out["note"] = note.replace("\r", "")[:NOTE_MAX].strip()
    if "steps" in body:
        out["steps"] = clean_steps(body.get("steps"))
    return out


def clean_defaults(d):
    """Standard für neue Puzzles: Einstellungen ohne Seed, Titel und Text des Schriftzugs, dazu die Schrittwahl."""
    if not isinstance(d, dict):
        raise bad_setting("defaults")
    config = clean_config(d.get("config") or {})
    for key in ("seed", "title", "text"):
        config.pop(key, None)
    if "edges" not in (d.get("config") or {}):
        config.pop("edges", None)  # Standard von vor 0.1.15 ohne «edges»: neue Puzzles nehmen die Form des Editors (organisch)
    return {"config": config, "steps": clean_steps(d.get("steps") or {})}


def clean_url(url, field):
    """Eine http(s)-Adresse ohne Query und Fragment, höchstens 300 Zeichen. Leer bleibt leer."""
    url = str(url or "").strip()
    if not url:
        return ""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc or parts.query or parts.fragment or len(url) > 300:
        raise ValueError(Msg("urlInvalid", "Die Adresse muss mit https:// beginnen, zum Beispiel https://example.com."))
    return url.rstrip("/")


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

    # Position: alte Werte (bis 0.1.2) umwandeln, leer oder null gilt als Mitte (0.1.9)
    for key in ("textPos", "greetPos"):
        if key in c and not c[key]:
            c = {**c, key: "mc"}
        elif c.get(key) in OLD_TEXT_POS:
            c = {**c, key: OLD_TEXT_POS[c[key]]}
    def clean_text(key):
        value = c.get(key) or ""
        if not isinstance(value, str):
            raise bad_setting(key)
        # Leerzeilen am Anfang bleiben, sie schieben den Schriftzug nach unten (Roger 21.11.2026)
        return "\n".join(" ".join(line.split()) for line in value.replace("\r", "").split("\n"))[:TEXT_MAX].rstrip("\n")

    text = clean_text("text")
    title = c.get("title") or ""
    if not isinstance(title, str):
        raise bad_setting("title")
    out = {
        "count": pick("count", COUNTS, 100), "format": pick("format", tuple(FORMATS), "photo"),
        "start": pick("start", STARTS, "scatter"), "edges": pick("edges", EDGES, "round"), "rot": bool(c.get("rot")), "frame": bool(c.get("frame", True)),
        "ghost": bool(c.get("ghost")), "ghostPct": int(number("ghostPct", *GHOST_PCT)), "seed": int(number("seed", 0, 2 ** 31, 4711)),
        "tempo": round(float(number("tempo", 0, TEMPO_MAX, 0.5)), 3), "durMode": pick("durMode", DUR_MODES, "own"),
        "T": round(float(number("T", MIN_T, MAX_T, 45)), 2), "hold": int(number("hold", HOLD_MIN, HOLD_MAX, 5)),
        "frameAt": int(number("frameAt", *FRAME_AT)), "ramp": int(number("ramp", *RAMP)),
        "title": clean_title(title), "text": text, "textMode": pick("textMode", TEXT_MODES, "in"),
        "textFont": pick("textFont", TEXT_FONTS, "hand"), "textSize": int(number("textSize", *TEXT_SIZE)),
        "textPos": pick("textPos", TEXT_POS, "mc"), "textColor": text_color(c.get("textColor", "#ffffff")),
        "textArc": int(number("textArc", *TEXT_ARC)), "textRot": int(number("textRot", *TEXT_ROT)),
        # Grussbotschaft auf dem leeren Tisch (C3, 0.1.8): gleiche Einstellungen wie der Schriftzug auf dem fertigen Puzzle
        "greet": clean_text("greet"), "greetFont": pick("greetFont", TEXT_FONTS, "hand"),
        "greetSize": int(number("greetSize", *TEXT_SIZE)), "greetPos": pick("greetPos", TEXT_POS, "mc"),
        "greetColor": text_color(c.get("greetColor", "#ffffff"), "greetColor"),
        "greetArc": int(number("greetArc", *TEXT_ARC)), "greetRot": int(number("greetRot", *TEXT_ROT)),
    }
    if c.get("lead") is not None:
        out["lead"] = round(float(number("lead", *LEAD)), 2)
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


def text_color(value, key="textColor"):
    if not isinstance(value, str) or not TEXT_COLOR.match(value):
        raise bad_setting(key)
    return value.lower()


def _unit(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
        raise bad_setting("volume")
    return value


def open_photo(data, max_pixels=None):
    """Bild öffnen, nur JPEG/PNG/WEBP, Grösse sofort prüfen (vor dem Dekodieren). ValueError, wenn zu gross."""
    img = Image.open(io.BytesIO(data), formats=PHOTO_FORMATS)
    if img.width * img.height > (max_pixels or MAX_PIXELS):
        img.close()
        raise ValueError("too many pixels")
    return img


def decode_photo(value):
    """data:image/…;base64,… vom Editor → JPEG ohne Metadaten, längste Seite höchstens PHOTO_SIDE, und das Bild."""
    if not isinstance(value, str) or not value.startswith("data:image/") or ";base64," not in value[:40]:
        raise ValueError(Msg("badPhoto", "Das Foto fehlt oder ist kein Bild."))
    try:
        data = base64.b64decode(value.split(",", 1)[1], validate=True)
        with DECODE_SLOTS, open_photo(data) as img:
            if img.format == "JPEG":
                img.draft("RGB", (PHOTO_SIDE, PHOTO_SIDE))  # JPEG gleich verkleinert dekodieren
            img = ImageOps.exif_transpose(img).convert("RGB")
            img.thumbnail((PHOTO_SIDE, PHOTO_SIDE))
    except Exception:
        raise ValueError(Msg("badPhoto", "Das Foto fehlt oder ist kein Bild."))
    out = io.BytesIO()
    img.save(out, "JPEG", quality=90)  # Neu gespeichert: kein EXIF, kein GPS
    return out.getvalue(), img


def cover(img, size):
    """Bild auf size zuschneiden (füllt ganz, Mitte bleibt)."""
    return ImageOps.fit(img, size, Image.LANCZOS)


# Nasen wie EDGE_SEGS in puzzle-engine.js: gerade bis u = a, Bézier-Stücke, gerade ab u = b
EDGE_SEGS = {
    "round": (0, 1, (((.30, 0), (.40, .02), (.38, .10)), ((.30, .20), (.36, .30), (.50, .30)),
                     ((.64, .30), (.70, .20), (.62, .10)), ((.60, .02), (.70, 0), (1, 0)))),
    "classic": (.37, .63, (((.37, .035), (.40, .05), (.39, .09)), ((.30, .14), (.31, .28), (.50, .28)),
                           ((.69, .28), (.70, .14), (.61, .09)), ((.60, .05), (.63, .035), (.63, 0)))),
}


def _piece_outline(x, y, cw, ch, sides, edges="round"):
    """Umriss eines Puzzleteils als Punktliste, gleiche Nasen wie edgePath in puzzle-engine.js."""
    start, end, segs = EDGE_SEGS.get(edges, EDGE_SEGS["round"])
    tab = min(cw, ch)
    x0, y0, x1, y1 = x * cw, y * ch, (x + 1) * cw, (y + 1) * ch
    corners = ((x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0))
    pts = [corners[0]]
    for i, s in enumerate(sides):
        (ax, ay), (bx, by) = corners[i], corners[i + 1]
        dx, dy = bx - ax, by - ay
        if not s:
            pts.append((bx, by))
            continue
        length = math.hypot(dx, dy)
        ox, oy = dy / length * tab * s, -dx / length * tab * s
        p = lambda u, v: (ax + u * dx + v * ox, ay + u * dy + v * oy)
        if start:
            pts.append(p(start, 0))
        cur = pts[-1]
        for a, b, c in segs:
            a, b, c = p(*a), p(*b), p(*c)
            for k in range(1, 9):
                t = k / 8
                pts.append(tuple((1 - t) ** 3 * cur[j] + 3 * (1 - t) ** 2 * t * a[j] + 3 * (1 - t) * t * t * b[j] + t ** 3 * c[j]
                                 for j in range(2)))
            cur = c
        if end < 1:
            pts.append((bx, by))
    return pts


def puzzle_card(card, seed=0, edges="round"):
    """Vorschaubild als Puzzle: Teile-Linien über dem Foto, rechts unten fehlen noch drei Teile (Roger 22.11.2026)."""
    from PIL import ImageDraw
    rnd = random.Random(seed)
    w, h = card.size
    rows = 4
    cols = max(2, round(rows * w / h))
    cw, ch = w / cols, h / rows
    hs = [[rnd.choice((1, -1)) for _ in range(cols + 1)] for _ in range(rows + 1)]
    vs = [[rnd.choice((1, -1)) for _ in range(cols + 1)] for _ in range(rows + 1)]

    def sides(x, y):
        return (0 if y == 0 else -hs[y][x], 0 if x == cols - 1 else vs[y][x + 1],
                0 if y == rows - 1 else hs[y + 1][x], 0 if x == 0 else -vs[y][x])
    missing = {(cols - 2, rows - 1), (cols - 1, rows - 1), (cols - 1, rows - 2)}
    lines = Image.new("RGBA", card.size, (0, 0, 0, 0))
    g = ImageDraw.Draw(lines)
    for y in range(rows):
        for x in range(cols):
            if (x, y) in missing:
                g.polygon(_piece_outline(x, y, cw, ch, sides(x, y), edges), fill=(35, 39, 42, 255))
    for y in range(rows):
        for x in range(cols):
            if (x, y) in missing:
                continue
            pts = _piece_outline(x, y, cw, ch, sides(x, y), edges)
            g.line(pts, fill=(255, 255, 255, 80), width=4, joint="curve")
            g.line(pts, fill=(0, 0, 0, 120), width=1)
    return Image.alpha_composite(card.convert("RGBA"), lines).convert("RGB")


def card_bytes(img, seed=0, edges="round"):
    """Vorschaubild für Messenger: 1200 × 630 als Puzzle, höchstens CARD_MAX_BYTES."""
    return card_jpeg(puzzle_card(cover(img, CARD_SIZE), seed, edges))


def decode_card(value):
    """Startbild vom Editor (data:image/…, 1200 × 630) als Vorschaubild, neu gespeichert. None, wenn es keines ist."""
    if not isinstance(value, str) or not value.startswith("data:image/") or ";base64," not in value[:40]:
        return None
    try:
        data = base64.b64decode(value.split(",", 1)[1], validate=True)
        with DECODE_SLOTS, open_photo(data, 4 * CARD_SIZE[0] * CARD_SIZE[1]) as img:
            card = img.convert("RGB")
    except Exception:
        return None
    return card_jpeg(card if card.size == CARD_SIZE else cover(card, CARD_SIZE))


def card_jpeg(card):
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


def download_name(title):
    """Dateiname zum Herunterladen: nur ASCII, sonst «Puzzle»."""
    return " ".join(re.sub(r"[^A-Za-z0-9 _-]+", " ", unicodedata.normalize("NFKD", title or "")
                           .encode("ascii", "ignore").decode()).split()) or "Puzzle"


def share_title(p):
    return clean_title((p.get("online_link") or {}).get("title")) or puzzle_name(p)


class App:
    def __init__(self, immich_url, api_key, data_dir, puzzle_dir="", music_dir="/music", share_port=0,
                 share_public_url="", shares_api_key="", umami_url="", umami_id="", umami_links_id="", notifier=None):
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
        self.notifier = notifier or Notifier()  # ntfy (0.1.18), ohne NTFY_URL/NTFY_TOPIC aus
        self.run_marker = RunMarker(os.path.join(data_dir, "run.json"), VERSION)  # Absturz erkennen
        umami_url, umami_id, umami_links_id = (umami_url or "").strip().rstrip("/"), (umami_id or "").strip(), (umami_links_id or "").strip()
        # Editor: ein Aufruf pro Laden mit Adresse «/puzzle-editor» (ohne Puzzle-ID und Panel); automatische Zählung aus, sonst zählte
        # jeder Panel-Wechsel (#P3 …) und jedes history.replaceState als eigener Aufruf. Skriptname hp.js gegen Werbeblocker.
        self.umami_tag = (f'<script defer src="{html.escape(umami_url)}/hp.js" data-website-id="{html.escape(umami_id)}" '
                          f'data-auto-track="false" id="umamiApp"></script>'
                          f'<script>document.getElementById("umamiApp").addEventListener("load", function () {{ '
                          f'umami.track(function (p) {{ p.url = "/puzzle-editor"; return p; }}); }});</script>') if umami_url and umami_id else ""
        self.umami_links = (umami_url, umami_links_id) if umami_url and umami_links_id else None
        parts = urllib.parse.urlsplit(umami_url)
        self.umami_origin = f"{parts.scheme}://{parts.netloc}" if parts.scheme in ("http", "https") and parts.netloc else ""
        self.immich = Immich(immich_url, api_key) if immich_url and api_key else None  # ohne Immich nur Hochladen
        self.lock = threading.Lock()
        self.write_lock = threading.Lock()
        self.puzzles = {}
        self.durations = {}  # (Pfad, mtime) → Sekunden
        self._load()
        # Video-Download (0.1.17): Aufträge für den Render-Container (render/), Austausch nur über data/render
        self.videos = render.Videos(data_dir, music_dir, self.upload_music_dir)
        self.eager_photos = True  # Foto mit Schrift gleich beim Teilen erstellen (_photo_soon)
        self.videos.prune(list(self.puzzles))
        self.pw_path = os.path.join(data_dir, "share_passwords.json")
        self.pw_fails, self.pw_blocked = {}, {}
        self._prune_passwords()
        self.log_path = os.path.join(data_dir, "protocol.jsonl")
        self.log_lock = threading.Lock()
        self.log = []
        try:
            with open(self.log_path, encoding="utf-8") as f:
                for line in f.readlines()[-PROTOCOL_KEEP:]:
                    try:
                        self.log.append(json.loads(line))
                    except ValueError:
                        pass
        except OSError:
            pass

    # ---------- Protokoll ----------
    def protocol(self, what, item="", merge=False, pid=None, **values):
        """Eintrag ins Protokoll. merge: gleicher Code, gleiches Puzzle, gleicher Wer innert PROTOCOL_MERGE ersetzt
        den letzten Eintrag (automatisches Speichern im Editor; pid statt Name, der Name kann sich ändern). Fehler beim Schreiben halten nichts auf."""
        role, who = actor()
        now = datetime.now().astimezone()
        e = {"when": now.isoformat(timespec="seconds"), "role": role, "who": who, "what": what}
        if item:
            e["item"] = str(item)[:120]
        if pid:
            e["id"] = pid
        if values:
            e["vars"] = values
        try:
            with self.log_lock:
                last = self.log[-1] if self.log else None
                same = merge and last and all(last.get(k) == e.get(k) for k in ("what", "id", "role", "who"))
                if same and (now - datetime.fromisoformat(last["when"])).total_seconds() < PROTOCOL_MERGE:
                    self.log[-1] = e
                    rewrite = True
                else:
                    self.log.append(e)
                    rewrite = len(self.log) > PROTOCOL_KEEP + 200
                    if rewrite:
                        self.log = self.log[-PROTOCOL_KEEP:]
                if rewrite:
                    tmp = self.log_path + ".tmp"
                    with open(tmp, "w", encoding="utf-8") as f:
                        f.writelines(json.dumps(x, ensure_ascii=False) + "\n" for x in self.log)
                    os.replace(tmp, self.log_path)
                else:
                    with open(self.log_path, "a", encoding="utf-8") as f:
                        f.write(json.dumps(e, ensure_ascii=False) + "\n")
        except (OSError, ValueError) as ex:
            print(f"Protokoll: {ex}", flush=True)

    def protocol_entries(self):
        with self.log_lock:
            return {"entries": list(reversed(self.log)), "keep": PROTOCOL_KEEP}

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
        self.video_state(pid)
        self._photo_soon(pid, p)
        return p

    def _photo_soon(self, pid, p):
        """Foto mit Schrift gleich nach dem Teilen bzw. Einschalten von Download erstellen (im Hintergrund),
        nicht erst beim ersten Herunterladen. Unverändert = nichts zu tun (Datei mit gleichem Hash liegt schon da)."""
        link = p.get("online_link") or {}
        if not (link.get("download") and link_active(link)) or not getattr(self, "eager_photos", False):
            return
        def run():
            try:
                self.download_photo(pid, self.shared_view(pid))
            except Exception as e:  # noqa: BLE001 – beim Herunterladen wird es nochmals versucht
                print(f"Foto mit Schrift für {pid}: {e}", flush=True)
        threading.Thread(target=run, daemon=True).start()

    def file(self, pid, name):
        if name not in ("photo.jpg", "thumb.jpg", "card.jpg"):
            raise KeyError(name)
        self._puzzle(pid)
        path = os.path.join(self._folder(pid), name)
        if not os.path.isfile(path):
            raise KeyError(name)
        if name == "card.jpg" and os.path.getmtime(path) < CARD_PUZZLE_SINCE:
            self._renew_card(pid, path)
        return path

    def _renew_card(self, pid, path):
        """Vorschaubild von vor 0.1.8 (nur Foto) als Puzzle neu erstellen."""
        edges = (self._puzzle(pid).get("config") or {}).get("edges", "round")
        try:
            with Image.open(os.path.join(self._folder(pid), "photo.jpg")) as img:
                data = card_bytes(img.convert("RGB"), pid, edges)
        except OSError:
            return
        with self.write_lock:
            tmp = f"{path}.{uuid.uuid4().hex}.tmp"
            with open(tmp, "wb") as f:
                f.write(data)
            os.replace(tmp, path)

    def _entry(self, p, now=None):
        now = time.time() if now is None else now
        cfg = p.get("config") or {}
        return {"id": p["id"], "name": puzzle_name(p), "created": p.get("created"), "updated": p.get("updated"),
                "by": p.get("by") or "", "forWhom": p.get("for") or "", "count": cfg.get("count"), "T": cfg.get("T"), "format": cfg.get("format"),
                "thumb": f"/api/puzzles/{p['id']}/thumb.jpg?v={int(p.get('updated') or 0)}",
                "link": self._share_entry(p, now) if (p.get("online_link") or {}).get("token") else None}

    def list_puzzles(self):
        with self.lock:
            items = [json.loads(json.dumps(p)) for p in self.puzzles.values()]
        now = time.time()
        items.sort(key=lambda p: p.get("updated") or 0, reverse=True)
        return {"puzzles": [self._entry(p, now) for p in items], **self.share_setup(), **self.app_settings()}

    def get_puzzle(self, pid):
        p = self._puzzle(pid)
        return {**self._entry(p), "config": p.get("config"), "asset": p.get("asset"), "meta": p.get("meta"),
                "note": p.get("note") or "", "steps": p.get("steps"),
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
        changes = {"config": config, "updated": now, "name": clean_title(body.get("name")) or config["title"],
                   **clean_details(body)}
        card = decode_card(body.get("card"))  # Startbild aus dem Editor (0.1.8), sonst macht der Server eines
        files = []
        if photo:
            data, img = decode_photo(photo)
            files = [("photo.jpg", data), ("thumb.jpg", thumb_bytes(img)), ("card.jpg", card or card_bytes(img, pid, config["edges"]))]
        elif card:
            files = [("card.jpg", card)]
        if files:
            with self.write_lock:
                for name, content in files:
                    tmp = os.path.join(folder, f"{name}.{uuid.uuid4().hex}.tmp")
                    with open(tmp, "wb") as f:
                        f.write(content)
                    os.replace(tmp, os.path.join(folder, name))
        if photo:
            changes["asset"] = asset
            changes["meta"] = self._meta_of(asset) if asset else None
        with self.lock:
            new = pid not in self.puzzles
            self.puzzles[pid] = {**old, **self.puzzles.get(pid, {})}
        self._update(pid, **changes)
        self.protocol("puzzleNew" if new else "puzzleEdit", changes["name"], merge=not new, pid=pid)
        return self.get_puzzle(pid)

    def delete_puzzle(self, pid):
        folder = self._folder(pid)
        with self.lock:
            if pid not in self.puzzles:
                raise KeyError(pid)
            name = puzzle_name(self.puzzles.pop(pid))
        shutil.rmtree(folder, ignore_errors=True)
        self.videos.remove(pid)
        self._prune_passwords()
        self.protocol("puzzleDelete", name)
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
        self.protocol("musicAdd", track_name(file))
        return {"id": "up:" + file, "name": track_name(file), "group": "", "duration": self._duration(path), "own": True}

    def delete_music(self, mid):
        if not str(mid).startswith("up:"):
            raise ValueError(Msg("musicReadOnly", "Musik aus dem gemeinsamen Ordner lässt sich hier nicht löschen."))
        os.remove(self.music_path(mid))
        self.protocol("musicDelete", track_name(str(mid)[3:]))
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
        for key in ("video", "share"):  # Video getrennt vom Foto (0.1.17); Kiosk-Gäste haben nur Video
            if key in body:
                link[key] = bool(body.get(key))
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
        if "from" in body or not link.get("valid_from"):
            link["valid_from"] = link_start(body.get("from")) if "from" in body else link_from(link) or link_start(None)
        if "days" in body or "until" in body:
            link["expires"], link["days"] = link_expiry(body, start=link["valid_from"])
        elif "from" in body and link.get("days") not in (None, 0, "until"):  # neues «ab»: Dauer zählt ab dort
            link["expires"], link["days"] = link_expiry({"days": link["days"]}, start=link["valid_from"])
        password = ...
        if "password" in body:
            password = check_password(body["password"])
        if link.get("valid_from") and link.get("expires") and link["expires"] < link["valid_from"]:
            raise ValueError(Msg("endBeforeStart", "Der Link würde ablaufen, bevor er gilt."))
        if password is not ...:
            self._set_password(link["token"], password)
            if password is None:
                link.pop("protected", None)
            else:
                link["protected"] = True
        self._update(pid, online_link=link)
        if "token" not in old:
            self.protocol("linkNew", puzzle_name(p))
        elif changes := link_changes(old, link, password):
            self.protocol("linkEdit", puzzle_name(p), changes=changes)
        return self.get_puzzle(pid)

    def revoke_online_link(self, pid):
        p = self._puzzle(pid)
        self._update(pid, online_link=None)
        self._prune_passwords()
        if (p.get("online_link") or {}).get("token"):
            self.protocol("linkDelete", puzzle_name(p))
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
        name = puzzle_name(self._puzzle(pid))
        self._update(pid, online_link=None)
        self._prune_passwords()
        self.protocol("linkDelete", name)
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

    def shared_view(self, pid, visitor=None):
        """Was die Empfängerseite bekommt: Einstellungen ohne Musik-Pfad, Metadaten nur mit Schalter."""
        p = self._puzzle(pid)
        link = p.get("online_link") or {}
        cfg = dict(p.get("config") or {})
        music = cfg.pop("music", None)
        out = {"title": share_title(p), "by": p.get("by") or "", "config": cfg,
               "music": {"volume": music.get("volume", .8), "fade": music.get("fade", True)} if music else None,
               "download": bool(link.get("download")), "meta": None,
               "year": datetime.fromtimestamp(p.get("created") or time.time()).year,
               "madeWith": self.settings.get("made_with_url") or "",
               "madeWithText": self.settings.get("made_with_text") or "",
               "copyright": self.copyright(),
               "likes": len(link.get("likers") or []),
               "liked": bool(visitor and self._liker(link, visitor) in (link.get("likers") or []))}
        if link_metadata(link):
            out["meta"] = p.get("meta")
        return out

    def video_state(self, pid, view=None):
        """Video zum Puzzle: None (kein Knopf), "pending" (wird erstellt …) oder "ready". Schreibt oder löscht dabei den
        Auftrag für den Render-Container. Titel, Unterzeile und Fusszeile wie auf der Empfängerseite (auch Kiosk)."""
        try:
            return self.videos.state(pid, self._puzzle(pid), self._folder(pid), self._render_info(pid, view))
        except (KeyError, OSError, ValueError):
            return None  # Video ist ein Zusatz: nie das Speichern oder die Empfängerseite stören

    def _render_info(self, pid, view=None):
        view = view or self.shared_view(pid)
        return {"title": view["title"], "meta": view.get("meta"), "kiosk": bool(view.get("kiosk")),
                "made_with_text": view.get("madeWithText") or (view.get("madeWith") or "").replace("https://", "").replace("http://", ""),
                "copyright": view.get("copyright") or "", "year": view.get("year"), "lang": "de"}

    def download_photo(self, pid, view):
        """Foto-Download (0.1.17): mit Titel oben und Fusszeile unten wie im Video. Fällt nur bei einem Fehler auf das
        Foto ohne Schrift zurück, damit der Download nie ganz ausfällt."""
        try:
            path = self.videos.photo(pid, self._puzzle(pid), self._folder(pid), self._render_info(pid, view))
            if path:
                return path
        except Exception as e:  # noqa: BLE001 – Schrift ist ein Zusatz
            print(f"Foto mit Schrift für {pid}: {e}", flush=True)
        return self.file(pid, "photo.jpg")

    def video_settings(self):
        return self.videos.overview()

    def set_video(self, on):
        """Hauptschalter Video-Download (Admin): aus = keine neuen Aufträge, Knopf weg, fertige Videos gelöscht."""
        self.videos.set_enabled(bool(on))
        with self.lock:
            ids = [pid for pid, p in self.puzzles.items() if (p.get("online_link") or {}).get("token")]
        for pid in ids:
            self.video_state(pid)
        self.protocol("videoOn" if on else "videoOff", "")
        return self.video_settings()

    def shared_music(self, pid):
        music = (self._puzzle(pid).get("config") or {}).get("music")
        if not music:
            raise KeyError("music")
        return self.music_path(music["id"])

    @staticmethod
    def _liker(link, visitor):
        """Gespeichert wird nur ein Hash aus Link und Besucher-Kennung, keine IP (Like, 0.1.8)."""
        return hashlib.sha256(f"{link.get('token')}:{visitor}".encode()).hexdigest()[:16]

    def set_like(self, pid, visitor, on):
        """Ein Like pro Besucher (Roger 22.11.2026), wieder zurücknehmbar. Gibt Zahl und eigenen Stand zurück."""
        if not isinstance(visitor, str) or not VISITOR_ID.match(visitor):
            raise ValueError("visitor")  # Freigabe-Port: 404
        on = bool(on)
        with self.lock:
            link = (self.puzzles.get(pid) or {}).get("online_link")
            if not link or not link.get("token"):
                raise KeyError(pid)
            likers = list(link.get("likers") or [])
            key = self._liker(link, visitor)
            changed = (key in likers) != on
            if changed:
                if on:
                    likers = (likers + [key])[-LIKES_MAX:]
                else:
                    likers.remove(key)
                link = {**link, "likers": likers}
                if on:  # gesetzte Likes pro Tag für die Tageszusammenfassung (0.1.18), nur Tag und Zahl
                    link["like_days"] = count_day(link.get("like_days"), time.time())
        if changed:  # nur in den Angaben des Links, nicht im Protokoll und nicht in der Shares-API (Roger 22.11.2026)
            self._update(pid, online_link=link)
        return {"likes": len(likers), "liked": on}

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
            name = puzzle_name(self.puzzles[pid])
        self._update(pid, online_link=link)
        self.protocol("linkView", name)
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
                "valid_from": link_from(link), "pending": bool(link.get("valid_from") and now < link["valid_from"]),
                "views": int(link.get("views") or 0), "last_viewed_at": link.get("last_viewed_at"),
                "likes": len(link.get("likers") or []),
                "view_days": self.view_days(link, now),  # Zugriffs-Statistik in der App (0.1.8)
                "url": self.share_origin + path if self.share_origin else "", "path": path,
                "thumb": f"/api/puzzles/{p['id']}/thumb.jpg?v={int(p.get('updated') or 0)}",
                "download": bool(link.get("download")), "active": link_active(link), "video": bool(link.get("video")),
                "share": bool(link.get("share")), "metadata": link_metadata(link), "protected": bool(link.get("protected"))}

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
        self.protocol("shareUrl", url=url)
        return self.share_setup()

    def app_settings(self):
        """Standard für neue Puzzles (None = Werkseinstellung) und «Erstellt mit»-Adresse der Empfängerseite."""
        return {"defaults": self.settings.get("defaults"), "madeWith": self.settings.get("made_with_url") or "",
                "madeWithText": self.settings.get("made_with_text") or "", "copyright": self.copyright(),
                "video": self.videos.overview(), "notify": self.alert_view()}

    def copyright(self):
        """Zweite Zeile der Fusszeile («© Jahr by …»): fest, nur über den Eintrag «copyright» in settings.json, nicht in der App änderbar."""
        return " ".join(str(self.settings.get("copyright") or "").split())[:120]

    def _set_setting(self, key, value):
        with self.write_lock:
            settings = {**self.settings, key: value}
            if value in (None, ""):
                settings.pop(key)
            write_json(self.settings_path, settings)
            self.settings = settings

    def set_defaults(self, defaults):
        """Standard für neue Puzzles setzen (Einstellungen oder «Als Standard übernehmen»). None = Werkseinstellung."""
        value = None if defaults is None else clean_defaults(defaults)
        self._set_setting("defaults", value)
        self.protocol("defaultsSet" if value else "defaultsReset")
        return self.app_settings()

    def set_made_with(self, url, text=None):
        """Erste Zeile unten auf der Empfängerseite: «Erstellt mit Text»; die Adresse macht den Text zum Link."""
        url = clean_url(url, "madeWith")
        text = " ".join(str(text or "").split())[:120]
        self._set_setting("made_with_url", url)
        self._set_setting("made_with_text", text)
        self.protocol("madeWith", url=" · ".join(x for x in (text, url) if x) or "–")
        return self.app_settings()

    # ---------- Meldungen über ntfy (0.1.18) ----------
    def alert_settings(self):
        saved = self.settings.get("notify") if isinstance(self.settings.get("notify"), dict) else {}
        out = {k: saved.get(k) is not False for k in ALERT_KINDS}
        out["time"] = saved.get("time") or ALERT_TIME
        out["sentDay"] = saved.get("sentDay") or ""
        return out

    def alert_view(self):
        """Für Einstellungen › Meldungen; der Kiosk überschreibt das mit None (eigene Box)."""
        return {**self.alert_settings(), "ntfy": self.notifier.view()}

    def set_alert_settings(self, body):
        cur = self.alert_settings()
        for k in ALERT_KINDS:
            if k in body:
                if not isinstance(body[k], bool):
                    raise ValueError(Msg("notBool", "«{field}» muss true oder false sein.", field=k))
                cur[k] = body[k]
        if "time" in body:
            cur["time"] = clean_time(body["time"])
        self._set_setting("notify", cur)
        self.protocol("notifySet")
        return self.alert_view()

    def alert(self, kind, title, message, prio=3, tags=()):
        """Meldung einer Art (errors, summary), wenn ihr Schalter an ist; nie blockierend."""
        if not self.alert_settings().get(kind) or not self.notifier.ready():
            return False
        self.notifier.send(title, message, prio, tags)
        return True

    def _alert_sent(self, title, message, tags):
        if not self.notifier.ready():
            raise ValueError(Msg("ntfyOff", "ntfy ist nicht eingerichtet: im Stack NTFY_URL und NTFY_TOPIC setzen."))
        res = self.notifier.send(title, message, 2, tags, wait=True)
        if not res.get("ok"):
            raise ValueError(Msg("ntfyFail", "{error}", error=res.get("error") or "?"))
        return {**self.alert_view(), "sent": res}

    def alert_test(self):
        return self._alert_sent("Puzzle: Testmeldung", "Die Meldungen von Puzzle kommen an.", ["white_check_mark"])

    def day_numbers(self, day):
        """Zahlen eines Tages (YYYY-MM-DD, Lokalzeit) aus den Puzzles: neu, neue Links, Aufrufe, gesetzte Likes, meistgeschaut.
        Gelöschte Puzzles zählen nicht mehr mit; Likes pro Tag gibt es erst ab 0.1.18."""
        start = datetime.strptime(day, "%Y-%m-%d").timestamp()
        end = start + 86400
        with self.lock:
            items = [json.loads(json.dumps(p)) for p in self.puzzles.values()]
        out = {"created": 0, "links": 0, "views": 0, "likes": 0, "top": None}
        best = 0
        for p in items:
            if start <= float(p.get("created") or 0) < end:
                out["created"] += 1
            link = p.get("online_link") or {}
            if not link.get("token"):
                continue
            if start <= float(link.get("created") or 0) < end:
                out["links"] += 1
            views = int((link.get("view_days") or {}).get(day) or 0)
            out["views"] += views
            out["likes"] += int((link.get("like_days") or {}).get(day) or 0)
            if views > best:
                best, out["top"] = views, (share_title(p), views)
        return out

    def alert_summary_text(self, day, today=False):
        d = datetime.strptime(day, "%Y-%m-%d")

        def line(n):
            text = f"{n['created']} neue Puzzles, {n['links']} neue Links, {n['views']} Aufrufe, {n['likes']} Likes"
            if n["top"]:
                text += f"; meistgeschaut: «{n['top'][0]}» ({n['top'][1]})"
            return text
        lines = [f"{WEEKDAYS[d.weekday()]} {d:%d.%m.%Y}: " + line(self.day_numbers(day))]
        if today:
            lines.append("Heute bis jetzt: " + line(self.day_numbers(time.strftime("%Y-%m-%d"))))
        now = time.time()
        with self.lock:
            items = list(self.puzzles.values())
        links = [p["online_link"] for p in items if (p.get("online_link") or {}).get("token")]
        live = [lk for lk in links if link_active(lk) and not (lk.get("expires") and now > lk["expires"])]
        views = sum(int(lk.get("views") or 0) for lk in links)
        likes = sum(len(lk.get("likers") or []) for lk in links)
        lines.append(f"Stand: {len(items)} Puzzles, {len(live)} aktive Links, insgesamt {views} Aufrufe und {likes} Likes")
        return "\n".join(lines)

    def alert_summary(self, now=None, manual=False):
        """Tageszusammenfassung für den Vortag. manual (Knopf «Jetzt senden»): sofort, mit heute bis jetzt, wartet auf ntfy."""
        now = time.time() if now is None else now
        day = (datetime.fromtimestamp(now) - timedelta(days=1)).strftime("%Y-%m-%d")
        text = self.alert_summary_text(day, today=manual)
        if manual:
            return self._alert_sent("Puzzle: Zusammenfassung", text, ["bar_chart"])
        return self.notifier.send("Puzzle: Tageszusammenfassung", text, 2, ["bar_chart"])

    def alert_due(self, now=None):
        """Einmal pro Tag ab der eingestellten Uhrzeit (auch nachgeholt, wenn Puzzle um diese Zeit nicht lief)."""
        now = time.time() if now is None else now
        cfg = self.alert_settings()
        local = datetime.fromtimestamp(now)
        today = local.strftime("%Y-%m-%d")
        if not cfg["summary"] or not self.notifier.ready() or cfg["sentDay"] == today or local.strftime("%H:%M") < cfg["time"]:
            return False
        saved = self.settings.get("notify") if isinstance(self.settings.get("notify"), dict) else {}
        self._set_setting("notify", {**saved, "sentDay": today})
        self.alert_summary(now)
        return True

    def run_start(self):
        """Beim Start: endete der letzte Lauf nicht sauber, Meldung «Neustart nach Absturz»."""
        old = self.run_marker.start()
        if old:
            self.alert("errors", "Puzzle: Neustart nach Absturz",
                       f"Puzzle lief seit {old.get('started', '?')} (Version {old.get('version', '?')}) und endete nicht "
                       f"sauber; zuletzt aktiv {old.get('alive', '?')}. Jetzt läuft {VERSION}.", 4, ["rotating_light"])
        return old

    def start_alerts(self):
        def beat():
            while True:
                try:
                    self.run_marker.alive()
                    self.alert_due()
                except Exception:  # noqa: BLE001
                    traceback.print_exc()
                time.sleep(HEARTBEAT)
        threading.Thread(target=beat, daemon=True).start()

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
            self.protocol("linkDelete", puzzle_name(self._puzzle(pid)))
        if ids:
            self._prune_passwords()
        return self.list_shares()

    def share_umami_tag(self, grund=None):
        """Umami für Freigabe- und Hinweisseiten (UMAMI_LINKS_ID, Tag der Marke): Adresse fest «/puzzle-shared-link» (ohne Code), leerer Referrer.
        Hinweisseiten (Entscheid 52) melden dazu das Ereignis «Hinweis» mit dem Grund."""
        if not self.umami_links:
            return ""
        url, site = self.umami_links

        def js(value):  # JSON im <script>: kein </script> möglich
            return json.dumps(value).replace("<", "\\u003c")
        masked = js("/puzzle-shared-link")  # fester Name statt Präfix, in Umami lesbar
        # Auch das Ereignis mit maskierter URL, sonst ginge der Code mit
        event = (f" umami.track(function (p) {{ p.url = {masked}; p.referrer = ''; p.name = 'Hinweis'; "
                 f"p.data = {{ grund: {js(grund)} }}; return p; }});") if grund else ""
        # Kein onload-Attribut (strenge CSP mit Nonce): eigenes Skript wartet auf das Laden von Umami
        return (f'<script defer src="{html.escape(url)}/hp.js" data-website-id="{html.escape(site)}" '
                f'data-tag="{html.escape(FAMILY_ID)}" data-auto-track="false" id="umamiJs"></script>'
                f'<script>document.getElementById("umamiJs").addEventListener("load", function () {{ '
                f"umami.track(function (p) {{ p.url = {masked}; p.referrer = ''; return p; }});{event} }});</script>")

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
        now = time.time()
        # Vorschaubild nur, wenn der Link abrufbar ist (wie Mosaik): sonst sperrt die Seite card.jpg und Smart Albums lädt vergeblich (0.1.8)
        live = (not link.get("protected") and link_active(link) and not (link.get("expires") and now > link["expires"])
                and not (link.get("valid_from") and now < link["valid_from"]))
        return {"id": link["token"], "title": share_title(p), "url": url, "item": puzzle_name(p),
                "person": None, "created": iso(link.get("created")),
                "validFrom": iso(link_from(link)), "expires": iso(link.get("expires")),
                "protected": bool(link.get("protected")), "views": int(link.get("views") or 0), "viewDays": self.view_days(link),
                "preview": url + "card.jpg" if url and live else None,
                "open": f"{app_url}/?id={urllib.parse.quote(p['id'])}", "revocable": True,
                "actions": list(SHARE_ACTIONS), "active": link_active(link), "download": bool(link.get("download")),
                "video": bool(link.get("video")), "share": bool(link.get("share")), "metadata": link_metadata(link),
                "offSince": None if link_active(link) else iso(link.get("off_since"))}

    def patch_share(self, token, body, app_url, by="Smart Albums"):
        if not isinstance(body, dict) or not body:
            raise FieldError(None, Msg("emptyChange", "Nichts zu ändern."))
        for field in body:
            if PATCH_FIELDS.get(field) not in SHARE_ACTIONS:
                raise FieldError(field, Msg("fieldNotChangeable", "«{field}» lässt sich hier nicht ändern.", field=field))
        for field in (*LINK_FLAGS, "metadata", "active"):
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
            link["valid_from"] = start or link_start(None)  # leer = ab heute, nie ohne Datum
        new_start, new_end = link.get("valid_from"), link.get("expires")
        if new_start and new_end and new_end < new_start:
            raise FieldError("expires" if "expires" in body else "validFrom",
                             Msg("endBeforeStart", "Der Link würde ablaufen, bevor er gilt."))
        for field in LINK_FLAGS:
            if field in body:
                link[field] = body[field]
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
        old = self._puzzle(pid)
        self._update(pid, online_link=link)
        changes = link_changes(old["online_link"], link, password if "password" in body else ...)
        if changes:
            self.protocol("linkEdit", puzzle_name(old), changes=changes)
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

        def _length(self, limit=1024 * 1024):
            """Content-Length prüfen, bevor etwas gelesen wird: fehlt = 0, negativ oder unlesbar = Fehler."""
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = -1
            if length < 0:
                self.close_connection = True
                raise ValueError(Msg("badLength", "Ungültige Anfrage."))
            if length > limit:
                self.close_connection = True
                raise TooLarge(Msg("fileTooBig", "Die Datei ist zu gross."))
            return length

        def _read(self, limit=1024 * 1024):
            return self.rfile.read(self._length(limit))

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
                set_actor("smart", "Smart Albums") if parts[:2] == ["api", "internal"] else set_actor("admin", self._by())
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
            except TooLarge as e:
                self._json(fields("error", of(e)), 413)
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
                if auth and auth.enabled and self._user():
                    app.protocol("logout")
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
                    set_actor("admin", str(data.get("username") or ""))
                    app.protocol("loginFail" + ("Block" if wait else ""))
                    if wait:
                        self._too_many(wait)
                    else:
                        self._json(fields("error", Msg("loginWrong", "Benutzername oder Passwort stimmt nicht.")), 401)
                    return
                auth.succeeded(ip)
                set_actor("admin", auth.name or auth.user)
                app.protocol("login")
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

        def _mode_tags(self):
            return ""  # Kiosk-Modus (kiosk.py) hängt hier eigene Dateien an

        def _head_tags(self):
            return ""  # Kiosk-Modus: früh im <head>, bevor die Seite gezeichnet wird

        def _strict_page(self):
            return False  # Kiosk-Gäste: strenge CSP mit Nonce für den Editor

        def _secure_page(self, page):
            """Seite mit Nonce und Headern: CSP, keine Einbettung, HSTS hinter https."""
            nonce = secrets.token_urlsafe(16)
            headers = {"Content-Security-Policy": page_csp(nonce, app.umami_origin), "X-Frame-Options": "DENY"}
            if login.is_https(self.client_address[0], self.headers):
                headers["Strict-Transport-Security"] = "max-age=31536000"
            return with_nonce(page, nonce), headers

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
                    page = f.read().replace("{{UMAMI}}", app.umami_tag).replace("{{VERSION}}", VERSION).replace("{{OG}}", self._app_card()).replace("{{MODE}}", self._mode_tags()).replace("</head>", self._head_tags() + "</head>", 1)
                page, headers = page.replace("{{I18N}}", self._i18n()), None
                if self._strict_page():
                    page, headers = self._secure_page(page)
                self._send(200, page.encode(), "text/html; charset=utf-8", headers)
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
            elif parts == ["api", "protocol"]:
                self._json(app.protocol_entries())
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
            elif parts == ["api", "settings", "defaults"]:  # Standard für neue Puzzles
                self._json(app.set_defaults(self._body().get("defaults")))
            elif parts == ["api", "settings", "video"]:  # Hauptschalter Video-Download (0.1.17)
                self._json(app.set_video(self._body().get("enabled")))
            elif parts == ["api", "settings", "notify"]:  # Meldungen über ntfy (0.1.18)
                self._json(app.set_alert_settings(self._body()))
            elif parts == ["api", "settings", "notify", "test"]:
                self._read()
                self._json(app.alert_test())
            elif parts == ["api", "settings", "notify", "summary"]:
                self._read()
                self._json(app.alert_summary(manual=True))
            elif parts == ["api", "settings", "made-with"]:  # «Erstellt mit …» auf der Empfängerseite
                self._json(app.set_made_with(**{k: v for k, v in self._body().items() if k in ("url", "text")}))
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
            set_actor("visitor")
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
                view = app.shared_view(pid, self.headers.get("X-Visitor"))
                view["video"] = app.video_state(pid, view)
                self._json(view)
            elif rest == ["video"]:  # MP4 vom Render-Container (0.1.17), nur wenn fertig und erlaubt
                view = app.shared_view(pid)
                if app.video_state(pid, view) != "ready" or not app.videos.file(pid):
                    raise KeyError("video")
                self._file(app.videos.file(pid), "video/mp4",
                           {"Content-Disposition": f'attachment; filename="{download_name(view["title"])}.mp4"'})
            elif rest in (["photo.jpg"], ["card.jpg"]):
                self._file(app.file(pid, rest[0]), "image/jpeg", {"Cache-Control": "private, max-age=3600"})
            elif rest == ["music"]:
                path = app.shared_music(pid)
                self._file(path, MUSIC_TYPES[os.path.splitext(path)[1].lower()], {"Cache-Control": "private, max-age=3600"})
            elif rest == ["download"]:
                view = app.shared_view(pid)
                if not view["download"]:
                    raise KeyError("download")
                self._file(app.download_photo(pid, view), "image/jpeg",
                           {"Content-Disposition": f'attachment; filename="{download_name(view["title"])}.jpg"'})
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
            page, headers = self._secure_page(page)
            self._send(200, page.encode(), "text/html; charset=utf-8", headers)

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
            """Passwort-Formular an der Wurzel eines geschützten Links (Entscheid 49) und der Like-Knopf (0.1.8)."""
            body = self._read(4096)
            prefix = app.share_prefix.strip("/").split("/")
            rest = parts[len(prefix):] if parts[:len(prefix)] == prefix else []
            if len(rest) == 2 and rest[1] == "like":
                pid = app.shared_puzzle(rest[0])
                if app.share_locked(rest[0], self._unlock_cookie()):
                    raise PasswordRequired()
                try:
                    data = json.loads(body or b"{}")
                except ValueError:
                    data = None
                if not isinstance(data, dict):
                    raise ValueError("body")
                self._json(app.set_like(pid, self.headers.get("X-Visitor"), data.get("on", True)))
                return
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
            name = puzzle_name(app._puzzle(app._by_token(token)))
            if app.password_ok(token, pw):
                app.protocol("linkUnlock", name)
                path = app.share_path(token)
                cookie = (f"{UNLOCK_COOKIE}={app.unlock_value(token)}; Path={path.rstrip('/')}; "
                          f"Max-Age={UNLOCK_DAYS * 86400}; HttpOnly; SameSite=Lax")
                if login.is_https(self.client_address[0], self.headers):
                    cookie += "; Secure"
                self._send(303, b"", "text/plain", {"Location": path, "Set-Cookie": cookie})
            elif app.note_pw_fail(ip):
                app.protocol("linkPwBlock", name)
                self._pw_page(429, "lock")
            else:
                app.protocol("linkPwWrong", name)
                self._pw_page(401, "bad")

        def _delete(self, parts):
            self._read()
            raise KeyError(self.path)

    return ShareHandler


def serve_share(app, host, port):
    return ThreadingHTTPServer((host, port), make_share_handler(app))


def main():
    if (os.environ.get("PUZZLE_MODE") or "").strip().lower() == "kiosk":  # Kiosk für Gäste (docs/konzept-kiosk.md)
        import kiosk
        kiosk.main()
        return
    url, key = os.environ.get("IMMICH_URL", "").strip(), os.environ.get("IMMICH_API_KEY", "").strip()
    if bool(url) != bool(key):
        raise SystemExit("IMMICH_URL und IMMICH_API_KEY gehören zusammen: beide setzen oder beide leer lassen.")
    app = App(url, key, os.environ.get("DATA_DIR", "data"), os.environ.get("PUZZLE_DIR", ""),
              os.environ.get("MUSIC_DIR", "/music"),
              share_port=int(os.environ.get("SHARE_PORT") or 0), share_public_url=os.environ.get("SHARE_PUBLIC_URL", ""),
              shares_api_key=os.environ.get("SHARES_API_KEY", ""), umami_url=os.environ.get("UMAMI_URL", ""),
              umami_id=os.environ.get("UMAMI_WEBSITE_ID", ""), umami_links_id=os.environ.get("UMAMI_LINKS_ID", ""),
              notifier=Notifier.from_env())
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
    print(app.notifier.describe(), flush=True)
    app.run_start()

    def stop(signum, frame):  # docker stop / Redeploy: sauberes Ende, kein «Neustart nach Absturz»
        app.run_marker.end()
        sys.exit(0)
    signal.signal(signal.SIGTERM, stop)
    app.start_alerts()
    try:
        serve(app, host, port, auth, register).serve_forever()
    except KeyboardInterrupt:  # Strg+C lokal: auch sauber; jede andere Ausnahme lässt run.json liegen (= Absturz)
        app.run_marker.end()


if __name__ == "__main__":
    main()
