"""Video-Download (MP4) geteilter Puzzles: die App-Seite (Roger 23.11.2026, Auftrag «MP4-Download»).

Gerendert wird im eigenen Container `render/` (Node + Skia + ffmpeg, ohne Netzwerk). App und Renderer tauschen sich nur
über Dateien in `<data>/render` aus:
  queue/<pid>.json    Auftrag (schreibt nur die App): Hash, Titel, Unterzeile, Fusszeile, Einstellungen, Musik
  videos/<pid>.mp4    fertiges Video (schreibt der Renderer)
  videos/<pid>.json   Stand vom Renderer: {hash, state: queued|running|ready|error, progress, tries}
  control.json        {enabled}: Hauptschalter aus dem Admin
Ein Video gibt es nur, wenn der Link existiert, aktiv und nicht abgelaufen ist, «Download erlauben (Video)»
an ist (Admin und Kiosk, getrennt vom Foto-Schalter «download») und der Hauptschalter an ist. Sonst löscht die App Auftrag und Video.
Das Modul kennt server.py nicht (kein Import im Kreis): Aufrufer geben Puzzle-Daten und App-Angaben mit.
"""
import hashlib
import json
import os
import re
import threading
import time
import uuid

PID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
QUEUE_MAX = 50  # mehr Aufträge gleichzeitig nimmt die App nicht an
STORAGE_MB = int(os.environ.get("RENDER_MAX_STORAGE_MB") or 2048)  # alle Videos zusammen
TEXTS_DIR = os.path.dirname(os.path.abspath(__file__))
_texts = {}


def text(lang, key):
    """Ein Text aus texts.<lang>.js (dieselben Texte wie die Empfängerseite, nicht doppelt gepflegt)."""
    lang = lang if lang in ("de", "en") else "de"
    if lang not in _texts:
        try:
            with open(os.path.join(TEXTS_DIR, f"texts.{lang}.js"), encoding="utf-8") as f:
                src = f.read()
        except OSError:
            src = ""
        _texts[lang] = {k: json.loads(f'"{v}"') for k, v in re.findall(r'^\s*"([\w.]+)":\s*"((?:[^"\\]|\\.)*)"', src, re.M)}
    return _texts[lang].get(key, "")


def fmt_date(value, lang):
    """Datum wie fmtDate() mit dateStyle medium: de-CH «23.11.2026», en «Nov 23, 2026»."""
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", str(value or ""))
    if not m:
        return ""
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if lang == "en":
        return f"{'Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec'.split()[mo - 1]} {d}, {y}" if 1 <= mo <= 12 else ""
    return f"{d:02d}.{mo:02d}.{y}"


def _write(path, data):
    tmp = f"{path}.{uuid.uuid4().hex}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, path)


def _read(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _rm(path):
    try:
        os.remove(path)
    except OSError:
        pass


def link_allows(link, now=None):
    """Erlaubt der Link ein Video? Aktiv, nicht abgelaufen, Schalter «Download erlauben (Video)» an.
    Ab 0.1.17 getrennt vom Foto («download»): sonst entstünden Videos für Links, die nur das Foto teilen."""
    link = link or {}
    if not link.get("token") or link.get("active") is False:
        return False
    if link.get("expires") and (now or time.time()) > link["expires"]:
        return False
    return bool(link.get("video"))


FONT = os.path.join(TEXTS_DIR, "fonts", "OpenSans-Bold.woff")  # wie «Overlay Sans» im Video
BAND = (22, 25, 27)  # Hintergrund der Empfängerseite und des Videos (#16191b)


# Schriftzug auf dem Motiv (Panel «Grussbotschaft auf dem fertigen Puzzle»): dieselben Schriften wie puzzle-engine.js.
# Die Systemschriften des Browsers gibt es auf dem Server nicht: freie Ersatzschriften wie im Renderer (render.js).
MOTIF_FONTS = {"roboto": "Roboto-Bold", "sourceserif": "SourceSerifPro-Semibold", "amatic": "AmaticSC-Bold",
               "opensans": "OpenSans-Bold", "lato": "Lato-Bold", "montserrat": "Montserrat-Bold", "bebas": "BebasNeue-Regular",
               "anton": "Anton-Regular", "archivo": "ArchivoBlack-Regular", "lilita": "LilitaOne-Regular",
               "fredoka": "Fredoka-Bold", "chewy": "Chewy-Regular", "luckiest": "LuckiestGuy-Regular"}
MOTIF_ALIAS = {"hand": "chewy", "script": "amatic", "serif": "sourceserif", "elegant": "sourceserif", "sans": "opensans",
               "bold": "anton", "mono": "roboto", "fun": "chewy"}
TEXT_KEYS = ("text", "textMode", "textFont", "textSize", "textPos", "textColor", "textArc", "textRot")


def _hex(c, default=(255, 255, 255)):
    m = re.match(r"^#([0-9a-fA-F]{6})$", str(c or ""))
    return tuple(int(m.group(1)[i:i + 2], 16) for i in (0, 2, 4)) if m else default


def draw_motif_text(img, cfg):
    """Schriftzug wie drawText() in puzzle-engine.js aufs Foto (RGB, wird verändert): Umbruch, Grösse, Lage (9 Felder),
    Farbe mit Schatten, Bogen und Drehung. Nur in den Modi «Im Puzzle» und «Erscheint am Schluss»; dann steht er
    auf dem fertigen Puzzle und gehört aufs Foto."""
    import math
    from PIL import Image, ImageDraw, ImageFilter, ImageFont
    cfg = cfg or {}
    if cfg.get("textMode") not in ("in", "end") or not str(cfg.get("text") or "").strip():
        return img
    W, H = img.size
    raw = [re.sub(r"\s+", " ", x).strip() for x in re.sub(r"\r?\n$", "", str(cfg["text"])).splitlines()]
    pad = W * .06
    max_w, max_h = W - 2 * pad, H - 2 * pad
    key = cfg.get("textFont") or "hand"
    path = os.path.join(TEXTS_DIR, "fonts", MOTIF_FONTS[MOTIF_ALIAS.get(key, key) if key not in MOTIF_FONTS else key] + ".woff")

    def num(k, d):
        try:
            v = float(cfg.get(k))
            return v if math.isfinite(v) else d
        except (TypeError, ValueError):
            return d

    def wrap(fs):
        f = ImageFont.truetype(path, max(4, round(fs)))
        fits = lambda t: f.getlength(t) <= max_w  # noqa: E731
        lines = []
        for line in raw:
            if not line:
                lines.append("")
                continue
            cur = ""
            for w in line.split(" "):
                t = f"{cur} {w}" if cur else w
                if fits(t):
                    cur = t
                    continue
                if cur:
                    lines.append(cur)
                    cur = ""
                while not fits(w):  # Wort länger als die Zeile: irgendwo trennen
                    k = len(w) - 1
                    while k > 1 and not fits(w[:k]):
                        k -= 1
                    lines.append(w[:k])
                    w = w[k:]
                cur = w
            lines.append(cur)
        return f, lines

    fs = W / 16 * (num("textSize", 100) or 100) / 100
    f, lines = wrap(fs)
    for _ in range(30):
        if fs * 1.15 * len(lines) <= max_h:
            break
        fs *= .92
        f, lines = wrap(fs)
    pos = {"top": "tc", "mid": "mc", "bottom": "bc"}.get(cfg.get("textPos"), cfg.get("textPos"))
    pos = pos if pos in ("tl", "tc", "tr", "ml", "mc", "mr", "bl", "bc", "br") else "mc"
    v, h = pos
    lh = fs * 1.15
    tot = lh * len(lines)
    y0 = pad if v == "t" else H - pad - tot if v == "b" else (H - tot) / 2
    x = pad if h == "l" else W - pad if h == "r" else W / 2
    arc = max(-1.0, min(1.0, num("textArc", 0) / 100))
    rot = max(-45.0, min(45.0, num("textRot", 0)))
    color = _hex(cfg.get("textColor"))
    lw = [f.getlength(t) for t in lines]
    lcx = lambda i: x + lw[i] / 2 if h == "l" else x - lw[i] / 2 if h == "r" else x  # noqa: E731

    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    g = ImageDraw.Draw(layer)
    if abs(arc) < .01:
        anchor = {"l": "la", "r": "ra"}.get(h, "ma")
        for i, t in enumerate(lines):
            g.text((x, y0 + i * lh + fs * .05), t, font=f, fill=color, anchor=anchor)
    else:  # jede Zeile als weicher Bogen, + nach oben gewölbt, - nach unten
        R, up = max_w / (abs(arc) * math.pi * .9), arc > 0
        size = round(fs * 2)
        for i, t in enumerate(lines):
            ly = y0 + i * lh + fs * .6
            cx = lcx(i)
            cy = ly + R if up else ly - R
            s = -lw[i] / 2
            for ch in t:
                w = f.getlength(ch)
                a = (s + w / 2) / R
                s += w
                tile = Image.new("RGBA", (size, size), (0, 0, 0, 0))
                ImageDraw.Draw(tile).text((size / 2, size / 2), ch, font=f, fill=color, anchor="mm")
                tile = tile.rotate(-math.degrees(a if up else -a), resample=Image.BICUBIC)
                px, py = cx + R * math.sin(a), (cy - R * math.cos(a)) if up else (cy + R * math.cos(a))
                layer.paste(tile, (round(px - size / 2), round(py - size / 2)), tile)
    if rot:  # um die Mitte des Schriftzugs drehen (Canvas: positiv = im Uhrzeigersinn)
        bw = max(lw) if lw else 0
        center = (x + bw / 2 if h == "l" else x - bw / 2 if h == "r" else x, y0 + tot / 2)
        layer = layer.rotate(-rot, center=center, resample=Image.BICUBIC)
    # Schatten wie im Canvas: helle Farben dunkel hinterlegen, dunkle (#0…–#3…) hell; Unschärfe 15 %, 4 % nach unten
    dark = bool(re.match(r"^#[0-3]", str(cfg.get("textColor") or ""), re.I))
    alpha = layer.getchannel("A")
    shadow = Image.new("RGBA", (W, H), (255, 255, 255, 0) if dark else (0, 0, 0, 0))
    shadow.putalpha(alpha.point(lambda a: a * 7 // 10))
    shadow = shadow.transform((W, H), Image.AFFINE, (1, 0, 0, 0, 1, -fs * .04)).filter(ImageFilter.GaussianBlur(fs * .15 / 2))
    out = img.convert("RGBA")
    out.alpha_composite(shadow)
    out.alpha_composite(layer)
    img.paste(out.convert("RGB"))
    return img


def photo_with_text(src, title, sub, foot, out, cfg=None):
    """Foto-Download (0.1.17): das Foto in voller Grösse mit dem Schriftzug auf dem Motiv (wie auf dem fertigen Puzzle),
    darüber ein dunkler Balken mit Titel und Unterzeile, darunter einer mit der Fusszeile, gleich gesetzt wie im Video.
    Schreibt ein JPEG nach out."""
    from PIL import Image, ImageDraw, ImageFont, ImageOps
    with Image.open(src) as im:
        img = ImageOps.exif_transpose(im).convert("RGB")
    draw_motif_text(img, cfg)
    W, H = img.size
    u = min(W, H) / 1080

    def font(px):
        size = max(8, round(px * u))
        try:
            main = ImageFont.truetype(FONT, size)
        except OSError:
            main = None
        return main, ImageFont.load_default(size)  # Ersatz für Zeichen, die der Teilsatz nicht hat (·, ©)

    def runs(s, f):
        """Text in Stücke je Schrift: Zeichen ohne Glyphe in der Hauptschrift kommen aus der Ersatzschrift."""
        main, alt = f
        if main is None:
            return [(s, alt)]
        notdef = main.getmask("￿").getbbox()
        out = []
        for c in s:
            ff = alt if not c.isspace() and main.getmask(c).getbbox() in (None, notdef) else main
            if out and out[-1][1] is ff:
                out[-1] = (out[-1][0] + c, ff)
            else:
                out.append((c, ff))
        return out

    def width(g, s, f):
        return sum(g.textlength(t, font=ff) for t, ff in runs(s, f))

    def fit(g, s, f, maxw):
        s = str(s)
        if width(g, s, f) <= maxw:
            return s
        while len(s) > 1 and width(g, s + "…", f) > maxw:
            s = s[:-1]
        return s + "…"

    def draw(g, y, s, f, color, anchor):
        x = W / 2 - width(g, s, f) / 2
        for t, ff in runs(s, f):
            g.text((x, y), t, font=ff, fill=color, anchor="l" + anchor)
            x += g.textlength(t, font=ff)

    top = [(title, font(44), (238, 238, 238), 54)] if title else []
    if title and sub:
        top.append((sub, font(22), (204, 204, 204), 30))
    lines = [x for x in foot or [] if x]
    th = round((30 + sum(t[3] for t in top) + 10) * u) if top else 0
    bh = round((14 + 26 * len(lines) + 4) * u) if lines else 0
    canvas = Image.new("RGB", (W, H + th + bh), BAND)
    canvas.paste(img, (0, th))
    g = ImageDraw.Draw(canvas)
    y = 30 * u
    for s, f, color, step in top:
        draw(g, y, fit(g, s, f, W * .9), f, color, "a")
        y += step * u
    f = font(18)
    fy = H + th + bh - 14 * u
    for s in reversed(lines):
        draw(g, fy, fit(g, s, f, W * .94), f, (170, 170, 170), "d")
        fy -= 26 * u
    tmp = f"{out}.{uuid.uuid4().hex}.tmp"
    canvas.save(tmp, "JPEG", quality=92, optimize=True)
    os.replace(tmp, out)


class Videos:
    def __init__(self, data_dir, music_dir, upload_music_dir):
        self.root = os.path.join(data_dir, "render")
        self.queue = os.path.join(self.root, "queue")
        self.videos = os.path.join(self.root, "videos")
        self.control = os.path.join(self.root, "control.json")
        self.music_dir, self.upload_music_dir = music_dir, upload_music_dir
        self.photo_lock = threading.Lock()
        for folder in (self.root, self.queue, self.videos):
            os.makedirs(folder, exist_ok=True)

    # ---------- Hauptschalter ----------
    def enabled(self):
        return (_read(self.control) or {}).get("enabled") is True  # Standard aus: erst einschalten, wenn der Container läuft

    def set_enabled(self, on):
        _write(self.control, {"enabled": bool(on)})
        return self.enabled()

    def renderer_alive(self):
        """Herzschlag vom Renderer in den letzten 5 Minuten (für die Admin-Box)."""
        try:
            return time.time() - os.path.getmtime(os.path.join(self.root, "heartbeat")) < 300
        except OSError:
            return False

    # ---------- Auftrag ----------
    def _music(self, cfg):
        """Musik als {src, path, volume, fade, tail}; path relativ zum Musikordner. Unbekannte oder fehlende Musik: keine."""
        m = cfg.get("music")
        if not isinstance(m, dict):
            return None, ""
        mid = str(m.get("id") or "")
        if mid.startswith("lib:") and self.music_dir:
            src, rel, root = "lib", mid[4:], self.music_dir
        elif mid.startswith("up:"):
            src, rel, root = "up", mid[3:], self.upload_music_dir
        else:
            return None, ""
        base = os.path.realpath(root)
        full = os.path.realpath(os.path.join(base, rel))
        if not full.startswith(base + os.sep) or not os.path.isfile(full):
            return None, ""
        st = os.stat(full)
        tail = bool(m.get("tail", True))  # «Musik nach dem Puzzle weiterlaufen lassen»
        return ({"src": src, "path": rel.replace("\\", "/"), "volume": m.get("volume", .8), "fade": m.get("fade", True),
                 "tail": tail},
                f"{mid}:{st.st_size}:{int(st.st_mtime)}" + (":tail" if tail else ""))

    def job(self, pid, p, folder, info):
        """Auftrag zu einem Puzzle. info: title, by, meta (oder None), kiosk, made_with_text, copyright, year, lang."""
        lang = info.get("lang") or "de"
        cfg = dict(p.get("config") or {})
        music, music_key = self._music(cfg)
        cfg.pop("music", None)
        sub = [text(lang, "play.by")]
        meta = info.get("meta")
        if meta:
            sub.append(" · ".join(x for x in (fmt_date(meta.get("date"), lang), meta.get("place") or "") if x))
        made = text(lang, "play.madeWith")
        if info.get("kiosk"):
            foot = [f"{made} Puzzle"]
        else:
            foot = [f"{made} {info['made_with_text']}" if info.get("made_with_text") else "",
                    f"© {info.get('year')} by {info['copyright']}" if info.get("copyright") else ""]
        job = {"pid": pid, "title": info.get("title") or "Puzzle", "sub": " · ".join(x for x in sub if x),
               "foot": [x for x in foot if x], "config": cfg, "music": music}
        try:
            st = os.stat(os.path.join(folder, "photo.jpg"))
            photo = f"{st.st_size}:{int(st.st_mtime)}"
        except OSError:
            return None
        body = json.dumps({k: job[k] for k in ("title", "sub", "foot", "config")}, sort_keys=True, ensure_ascii=False)
        job["hash"] = hashlib.sha256(f"{body}|{photo}|{music_key}".encode()).hexdigest()[:24]
        return job

    def _files(self, pid):
        return (os.path.join(self.queue, f"{pid}.json"), os.path.join(self.videos, f"{pid}.mp4"),
                os.path.join(self.videos, f"{pid}.json"))

    def sync(self, pid, p, folder, info):
        """Nach jeder Änderung am Puzzle: Auftrag schreiben, wenn ein Video erlaubt ist und es noch keins mit diesem
        Stand gibt; sonst alles löschen. Gibt den Stand für die Empfängerseite zurück."""
        if not PID.match(pid or ""):
            return None
        if not (self.enabled() and link_allows(p.get("online_link"))):
            self.remove(pid)
            return None
        job = self.job(pid, p, folder, info)
        if not job:
            self.remove(pid)
            return None
        queued, video, status = self._files(pid)
        old = _read(status) or {}
        if old.get("hash") == job["hash"] and old.get("state") == "ready" and os.path.isfile(video):
            _rm(queued)
            return "ready"
        if (_read(queued) or {}).get("hash") == job["hash"]:
            return "pending"
        if os.path.isfile(video) and old.get("hash") != job["hash"]:
            _rm(video)  # alter Stand: nicht mehr herunterladen lassen
        if not os.path.isfile(queued) and (self._queued() >= QUEUE_MAX or self.usage() >= STORAGE_MB * 1024 * 1024):
            return None  # voll: kein Knopf, beim nächsten Speichern nochmals versuchen
        _write(queued, job)
        return "pending"

    def _queued(self):
        try:
            return sum(1 for n in os.listdir(self.queue) if n.endswith(".json"))
        except OSError:
            return 0

    def usage(self):
        total = 0
        try:
            for n in os.listdir(self.videos):
                if n.endswith(".mp4"):
                    try:
                        total += os.path.getsize(os.path.join(self.videos, n))
                    except OSError:
                        pass
        except OSError:
            pass
        return total

    # ---------- Empfängerseite ----------
    def state(self, pid, p, folder, info):
        """None (kein Knopf), "pending" (wird erstellt …) oder "ready"; erzeugt auch fehlende Aufträge (z. B. nach
        dem Einschalten des Hauptschalters)."""
        return self.sync(pid, p, folder, info)

    def photo(self, pid, p, folder, info):
        """Foto-Download mit Titel, Fusszeile und Schriftzug auf dem Motiv; bleibt als videos/<pid>.photo-<hash>.jpg liegen,
        bis sich Titel, Fusszeile, Schriftzug oder Foto ändern (dann neu) oder das Puzzle verschwindet (remove/prune). Unabhängig vom Video und vom Hauptschalter."""
        if not PID.match(pid or ""):
            return None
        job = self.job(pid, p, folder, info)
        if not job:
            return None
        src = os.path.join(folder, "photo.jpg")
        st = os.stat(src)
        motif = {k: job["config"].get(k) for k in TEXT_KEYS}
        key = json.dumps([job["title"], job["sub"], job["foot"], motif, st.st_size, st.st_mtime_ns], ensure_ascii=False, sort_keys=True)
        out = os.path.join(self.videos, f"{pid}.photo-{hashlib.sha256(key.encode()).hexdigest()[:24]}.jpg")
        with self.photo_lock:  # beim Teilen im Hintergrund und beim Herunterladen gleichzeitig: nur einmal erstellen
            if not os.path.isfile(out):
                self._rm_photos(pid)
                photo_with_text(src, job["title"], job["sub"], job["foot"], out, motif)
        return out

    def _rm_photos(self, pid):
        try:
            names = os.listdir(self.videos)
        except OSError:
            return
        for n in names:
            if n.startswith(f"{pid}.photo-") and n.endswith(".jpg"):
                _rm(os.path.join(self.videos, n))

    def file(self, pid):
        """Pfad des fertigen Videos, sonst None. Nur nach state() == "ready" aufrufen."""
        _, video, status = self._files(pid)
        return video if PID.match(pid or "") and (_read(status) or {}).get("state") == "ready" and os.path.isfile(video) else None

    # ---------- Löschen ----------
    def remove(self, pid):
        if not PID.match(pid or ""):
            return
        for path in self._files(pid):
            _rm(path)
        self._rm_photos(pid)

    def prune(self, pids):
        """Aufräumen: Aufträge und Videos ohne Puzzle löschen, liegengebliebene .tmp-Dateien auch. Gibt die Anzahl zurück."""
        keep, gone = set(pids), 0
        for folder in (self.queue, self.videos):
            try:
                names = os.listdir(folder)
            except OSError:
                continue
            for n in names:
                pid = n.split(".", 1)[0]
                if pid not in keep or (n.endswith(".tmp") and time.time() - os.path.getmtime(os.path.join(folder, n)) > 3600):
                    _rm(os.path.join(folder, n))
                    gone += n.endswith(".mp4")
        return gone

    def overview(self):
        """Für die Admin-Box: Schalter, Renderer läuft?, Anzahl wartend/fertig, Speicher."""
        videos = 0
        try:
            videos = sum(1 for n in os.listdir(self.videos) if n.endswith(".mp4"))
        except OSError:
            pass
        return {"enabled": self.enabled(), "alive": self.renderer_alive(), "queued": self._queued(), "videos": videos,
                "mb": round(self.usage() / 1024 / 1024, 1), "maxMb": STORAGE_MB}
