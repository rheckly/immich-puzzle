"""Kiosk-Modus (PUZZLE_MODE=kiosk, Konzept docs/konzept-kiosk.md): Gäste machen ohne Login ein Puzzle aus einem
hochgeladenen Foto und teilen es. Gleiches Image wie die App, eigener Container und eigener Datenordner.

- Port 8080 (PORT): Gäste-Editor und Gäste-API /api/kiosk/…, sonst nichts (keine Admin-Routen)
- Port 8081 (SHARE_PORT): Live-Links /p/<code>/ wie in der App, ohne Like, Fusszeile «Mach dein eigenes Puzzle»
- Port 8082 (ADMIN_PORT): die normale App mit Login als Admin (Moderation, Protokoll, Einstellungen) plus Box «Kiosk»

Jedes Gast-Puzzle hat einen Bearbeitungsschlüssel; gespeichert wird nur dessen SHA-256 (edit_hash).
Ein Aufräumjob löscht Entwürfe ohne Link nach KIOSK_DRAFT_HOURS und Puzzles mit abgelaufenem Link.
Der Kiosk-Code steht nur in dieser Datei und in kiosk.js / kiosk.css / kiosk-admin.js, damit er sich später sauber trennen lässt.

Meldungen über ntfy (0.1.13): Adresse, Topic und Token nur aus dem Stack (NTFY_URL, NTFY_TOPIC, NTFY_TOKEN), nie aus dem
Admin (kein Ziel von aussen wählbar, Token nie in Antworten). Im Admin je Meldungsart ein Schalter und die Uhrzeit der
Tageszusammenfassung (settings.json «kiosk_notify»).
"""
import hashlib
import hmac
import html
import ipaddress
import json
import os
import re
import secrets
import shutil
import signal
import sys
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

import auth as login
import server as core
from msg import Msg, fields, of
from notify import Notifier  # noqa: F401  (kiosk.Notifier bleibt für Tests und main)

STORAGE_CACHE = 60      # Sekunden, so lange gilt die gezählte Belegung des Speichers
PHOTO_BODY = 1024 * 1024  # ab dieser Grösse bringt eine Änderung ein Foto: Budget prüfen, bevor gelesen wird. Darunter
                          # (Einstellungen + Startbild 1200×630, auch über 256 KB) wird gelesen; ein kleines Foto zählt dann in guest_save
GONE_DAYS = 30           # so lange zeigt ein gelöschter Link «abgelaufen» (410) statt «unbekannt» (404)
STATS_DAYS = 90          # Tage in kiosk-stats.json
JOB_KEEP = 50            # Läufe im Verlauf des Aufräumjobs
TITLE_DEFAULT = "Ein Puzzle-Gruss für dich"
KIOSK_DAYS = ("today", 1, 7)
PRIVACY_DEFAULT = (
    "Du lädst hier ein Foto hoch und machst daraus ein Puzzle. Gespeichert werden nur das Foto (verkleinert), "
    "deine Einstellungen und der Link. Wir speichern keine Namen, keine E-Mail-Adressen und keine IP-Adressen; "
    "gegen Missbrauch zählen wir nur kurz und anonym, wie viele Puzzles von einem Gerät kommen.\n\n"
    "Ein Puzzle ohne Link löschen wir nach einem Tag, ein Puzzle mit Link sobald der Link abläuft (spätestens nach 7 Tagen). "
    "Mit dem Bearbeitungslink kannst du dein Puzzle jederzeit selbst löschen.\n\n"
    "Lade nur Fotos hoch, die du teilen darfst.")
STORAGE_WARN = 0.8       # ab diesem Anteil von KIOSK_MAX_STORAGE_MB meldet ntfy «Speicher knapp»
NOTIFY_KINDS = ("limits", "errors", "summary")
NOTIFY_TIME = "08:00"    # Tageszusammenfassung für den Vortag
NOTIFY_PAUSE = {"full": 6 * 3600, "storage": 6 * 3600, "device": 3600}  # Entprellen: höchstens eine Meldung pro Pause
HEARTBEAT = 60           # Sekunden: Takt der Meldeschleife (Lebenszeichen in kiosk-run.json, Tageszusammenfassung)
WEEKDAYS = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")


def now_iso(when=None):
    return datetime.fromtimestamp(time.time() if when is None else when).astimezone().isoformat(timespec="seconds")


def env_int(name, default):
    try:
        return int(os.environ.get(name) or default)
    except ValueError:
        return default


def env_bool(name, default):
    value = (os.environ.get(name) or "").strip().lower()
    return default if not value else value in ("1", "true", "yes", "ja", "on")


def key_hash(key):
    return hashlib.sha256(str(key or "").encode()).hexdigest()


class Limits:
    def __init__(self, **kw):
        self.photo_mb = kw.get("photo_mb", env_int("KIOSK_MAX_PHOTO_MB", 16))
        self.per_ip_hour = kw.get("per_ip_hour", env_int("KIOSK_MAX_PER_IP_HOUR", 10))
        self.max_puzzles = kw.get("max_puzzles", env_int("KIOSK_MAX_PUZZLES", 500))
        self.storage_mb = kw.get("storage_mb", env_int("KIOSK_MAX_STORAGE_MB", 2048))
        self.draft_hours = kw.get("draft_hours", env_int("KIOSK_DRAFT_HOURS", 24))
        self.clean_minutes = kw.get("clean_minutes", env_int("KIOSK_CLEAN_MINUTES", 15))
        self.per_ip_active = kw.get("per_ip_active", env_int("KIOSK_MAX_ACTIVE_PER_IP", 20))

    def view(self):
        return dict(vars(self))


class KioskApp(core.App):
    """Die App über DATA_DIR/kiosk, ohne Immich, ohne Shares-API, ohne eigene Musik der Gäste."""

    def __init__(self, data_dir, music_dir="/music", share_port=0, share_public_url="", umami_url="", umami_id="",
                 umami_links_id="", limits=None, enabled=None, ntfy=None):
        super().__init__("", "", data_dir, os.path.join(data_dir, "kiosk"), music_dir, share_port=share_port,
                         share_public_url=share_public_url, shares_api_key="", umami_url=umami_url, umami_id=umami_id,
                         umami_links_id=umami_links_id)
        self.limits = limits or Limits()
        self.enabled_default = env_bool("KIOSK_ENABLED", True) if enabled is None else enabled
        self.stats_path = os.path.join(data_dir, "kiosk-stats.json")
        self.gone_path = os.path.join(data_dir, "kiosk-gone.json")
        self.job_path = os.path.join(data_dir, "kiosk-job.json")
        self.stats = self._read(self.stats_path, {})
        self.gone = self._read(self.gone_path, {})
        self.job = self._read(self.job_path, {"runs": []})
        self.kiosk_lock = threading.Lock()
        self.ip_hits = {}  # Hash der IP mit Tagessalz → Zeitpunkte der letzten Stunde, nur im Speicher
        self.ip_puzzles = {}  # Hash der IP mit Tagessalz → Puzzles von heute, nur im Speicher
        self._storage = (0.0, 0)  # (Zeitpunkt, Bytes): storage_bytes() höchstens alle STORAGE_CACHE Sekunden zählen
        self.salt = (time.strftime("%Y-%m-%d"), secrets.token_bytes(16))
        self.ntfy = ntfy or Notifier()
        self.run_path = os.path.join(data_dir, "kiosk-run.json")  # da, solange der Kiosk läuft; beim sauberen Ende weg
        self.notified = {}   # Schlüssel → Zeitpunkt der letzten Meldung (Entprellen, nur im Speicher)
        self.level = {"full": False, "storage": False}  # gemeldeter Zustand; neue Meldung erst nach Rückkehr unter die Grenze
        self.device_quiet = 0  # Geräte an der Grenze, seit der letzten Meldung nicht gemeldet
        self.job_failed = False

    @staticmethod
    def _read(path, default):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, type(default)) else default
        except (OSError, ValueError):
            return default

    # ---------- Einstellungen ----------
    def kiosk_enabled(self):
        value = self.settings.get("kiosk_enabled")
        return self.enabled_default if value is None else bool(value)

    def kiosk_settings(self):
        return {"enabled": self.kiosk_enabled(), "contact": self.settings.get("kiosk_contact") or "",
                "privacy": self.settings.get("kiosk_privacy") or "", "privacyDefault": PRIVACY_DEFAULT,
                "title": self.settings.get("kiosk_title") or "", "titleDefault": TITLE_DEFAULT}

    def set_kiosk_settings(self, body):
        if "enabled" in body:
            if not isinstance(body["enabled"], bool):
                raise ValueError(Msg("notBool", "«{field}» muss true oder false sein.", field="enabled"))
            self._set_setting("kiosk_enabled", body["enabled"])
            self.protocol("kioskOn" if body["enabled"] else "kioskOff")
        for key, field, size in (("contact", "kiosk_contact", 200), ("privacy", "kiosk_privacy", 6000), ("title", "kiosk_title", 120)):
            if key in body:
                value = str(body[key] or "").strip()[:size]
                if key != "privacy":
                    value = " ".join(value.split())
                self._set_setting(field, value)
                self.protocol("kioskSetting", field=key)
        return self.kiosk_settings()

    def title_default(self):
        return self.settings.get("kiosk_title") or TITLE_DEFAULT

    # ---------- Zahlen ----------
    def count(self, what, n=1):
        day = time.strftime("%Y-%m-%d")
        with self.kiosk_lock:
            d = self.stats.setdefault(day, {})
            d[what] = int(d.get(what) or 0) + n
            for old in sorted(self.stats)[:-STATS_DAYS]:
                del self.stats[old]
            core.write_json(self.stats_path, self.stats)

    def storage_bytes(self, fresh=False):
        """Belegter Speicher der Gäste-Puzzles, zwischengespeichert für STORAGE_CACHE Sekunden."""
        when, total = self._storage
        if not fresh and time.time() - when < STORAGE_CACHE:
            return total
        total = 0
        for folder, _, files in os.walk(self.puzzle_dir):
            for name in files:
                try:
                    total += os.path.getsize(os.path.join(folder, name))
                except OSError:
                    pass
        self._storage = (time.time(), total)
        return total

    def status(self):
        """Für die Startseite der Gäste: offen, Pause (im Admin aus) oder voll (Anzahl oder Speicher)."""
        if not self.kiosk_enabled():
            state = "paused"
        elif (self.limits.max_puzzles and len(self.puzzles) >= self.limits.max_puzzles) or \
                (self.limits.storage_mb and self.storage_bytes() >= self.limits.storage_mb * 1024 * 1024):
            state = "full"
        else:
            state = "open"
        music = any(not x["own"] for x in self.music_list()["tracks"])
        return {"status": state, "limits": {"photoMb": self.limits.photo_mb, "perHour": self.limits.per_ip_hour,
                                            "days": list(KIOSK_DAYS)},
                "music": music, "share": bool(self.share_port), "titleDefault": self.title_default(),
                "contact": self.settings.get("kiosk_contact") or "", **self.guest_settings()}

    def guest_settings(self):
        """App-Einstellungen für Gäste: vom Video-Download nur, ob er an ist (keine Zahlen aus dem Admin)."""
        return {**self.app_settings(), "video": self.videos.enabled()}

    def admin_view(self):
        now = time.time()
        with self.lock:
            items = list(self.puzzles.values())
        links = [p for p in items if (p.get("online_link") or {}).get("token")]
        live = [p for p in links if not (p["online_link"].get("expires") and now > p["online_link"]["expires"])]
        return {**self.kiosk_settings(), "status": self.status()["status"], "limits": self.limits.view(),
                "puzzles": len(items), "drafts": len(items) - len(links), "links": len(live),
                "storageMb": round(self.storage_bytes(fresh=True) / 1024 / 1024, 1), "gone": len(self.gone),
                "stats": [{"day": d, **self.stats[d]} for d in sorted(self.stats, reverse=True)[:30]],
                "job": self.job, "notify": {**self.notify_settings(), "ntfy": self.ntfy.view()}}

    # ---------- Meldungen über ntfy ----------
    def alert_view(self):
        """Der Kiosk meldet über seine eigene Box (Einstellungen → Kiosk), nicht über die der App."""
        return None

    def notify_settings(self):
        saved = self.settings.get("kiosk_notify") if isinstance(self.settings.get("kiosk_notify"), dict) else {}
        out = {k: saved.get(k) is not False for k in NOTIFY_KINDS}  # Werkseinstellung: alles an
        out["time"] = saved.get("time") or NOTIFY_TIME
        out["sentDay"] = saved.get("sentDay") or ""
        return out

    def set_notify_settings(self, body):
        cur = self.notify_settings()
        for k in NOTIFY_KINDS:
            if k in body:
                if not isinstance(body[k], bool):
                    raise ValueError(Msg("notBool", "«{field}» muss true oder false sein.", field=k))
                cur[k] = body[k]
        if "time" in body:
            value = str(body["time"] or "").strip()
            m = re.fullmatch(r"(\d{1,2}):(\d{2})", value)
            if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
                raise ValueError(Msg("kioskNotifyTime", "Uhrzeit als HH:MM angeben, z. B. 08:00."))
            cur["time"] = f"{int(m.group(1)):02d}:{m.group(2)}"
        self._set_setting("kiosk_notify", cur)
        self.protocol("kioskSetting", field="notify")
        return self.notify_settings()

    def notify(self, kind, title, message, prio=3, tags=(), key=None, now=None):
        """Meldung einer Art (limits, errors, summary), wenn ihr Schalter an ist. key: entprellt nach NOTIFY_PAUSE."""
        if not self.notify_settings().get(kind) or not self.ntfy.ready():
            return False
        now = time.time() if now is None else now
        if key:
            with self.kiosk_lock:
                if now - self.notified.get(key, 0) < NOTIFY_PAUSE.get(key, 3600):
                    return False
                self.notified[key] = now
        self.ntfy.send(title, message, prio, tags)
        return True

    def _need_ntfy(self):
        if not self.ntfy.ready():
            raise ValueError(Msg("kioskNtfyOff", "ntfy ist nicht eingerichtet: im Stack NTFY_URL und NTFY_TOPIC setzen."))

    def _sent(self, res):
        if not res.get("ok"):
            raise ValueError(Msg("kioskNtfyFail", "{error}", error=res.get("error") or "?"))
        return res

    def notify_test(self):
        self._need_ntfy()
        return self._sent(self.ntfy.send("Puzzle-Kiosk: Testmeldung", "Die Meldungen vom Puzzle-Kiosk kommen an.",
                                         2, ["white_check_mark"], wait=True))

    def watch_levels(self, now=None):
        """Kiosk voll (Anzahl oder Speicher) und Speicher knapp: Meldung beim Überschreiten, erst nach Rückkehr wieder."""
        mb = self.storage_bytes() / 1024 / 1024
        n = len(self.puzzles)
        L = self.limits
        full_count = bool(L.max_puzzles and n >= L.max_puzzles)
        full_storage = bool(L.storage_mb and mb >= L.storage_mb)
        low = bool(L.storage_mb and mb >= L.storage_mb * STORAGE_WARN)
        state = {"full": full_count or full_storage, "storage": low and not full_storage}
        for key, on in state.items():
            was, self.level[key] = self.level[key], on
            if not on or was:
                continue
            if key == "full":
                why = (f"{n} von {L.max_puzzles} Puzzles" if full_count else f"Speicher {mb:.0f} von {L.storage_mb} MB")
                self.notify("limits", "Puzzle-Kiosk ist voll", f"{why}. Gäste können keine neuen Puzzles machen, bis der "
                            "Aufräum-Job Platz schafft (oder im Admin aufräumen).", 4, ["no_entry"], key="full", now=now)
            else:
                self.notify("limits", "Puzzle-Kiosk: Speicher knapp", f"Belegt {mb:.0f} von {L.storage_mb} MB "
                            f"({mb / L.storage_mb:.0%}), {n} Puzzles.", 3, ["warning"], key="storage", now=now)
        return state

    def device_limit(self, code, now=None):
        """Ein Gerät an der Grenze (Stundenbudget oder aktive Puzzles); höchstens eine Meldung pro Stunde, ohne IP."""
        with self.kiosk_lock:
            self.device_quiet += 1
            n = self.device_quiet
        if code == "kioskRate":
            what = f"hat sein Stundenbudget aufgebraucht ({self.limits.per_ip_hour} Puzzles oder Fotowechsel pro Stunde)"
        else:
            what = f"hat schon {self.limits.per_ip_active} Puzzles"
        more = f" Abgelehnte Anfragen seit der letzten Meldung: {n}." if n > 1 else ""
        if self.notify("limits", "Puzzle-Kiosk: Gerät an der Grenze", f"Ein Gerät {what}.{more}", 3, ["hourglass"],
                       key="device", now=now):
            with self.kiosk_lock:
                self.device_quiet = 0

    def summary_text(self, day, today=False):
        """Zahlen eines Tages aus kiosk-stats.json, dazu der heutige Stand von Puzzles, Links und Speicher."""
        s = self.stats.get(day) or {}
        d = datetime.strptime(day, "%Y-%m-%d")
        lines = [f"{WEEKDAYS[d.weekday()]} {d:%d.%m.%Y}: {int(s.get('created') or 0)} neue Puzzles, "
                 f"{int(s.get('links') or 0)} Links, {int(s.get('views') or 0)} Aufrufe, "
                 f"{int(s.get('rejected') or 0)} abgelehnt"]
        if today:
            t = self.stats.get(time.strftime("%Y-%m-%d")) or {}
            lines.append(f"Heute bis jetzt: {int(t.get('created') or 0)} neue Puzzles, {int(t.get('links') or 0)} Links, "
                         f"{int(t.get('views') or 0)} Aufrufe, {int(t.get('rejected') or 0)} abgelehnt")
        v = self.admin_view()
        L = self.limits
        storage = f"{v['storageMb']} MB" + (f" von {L.storage_mb} MB ({v['storageMb'] / L.storage_mb:.0%})" if L.storage_mb else "")
        lines.append(f"Stand: {v['puzzles']} Puzzles, davon {v['links']} mit gültigem Link, Speicher {storage}"
                     + ("" if v["enabled"] else ", Kiosk pausiert"))
        return "\n".join(lines)

    def send_summary(self, now=None, manual=False):
        """Tageszusammenfassung für den Vortag. manual (Knopf «Jetzt senden»): sofort, mit heute bis jetzt, wartet auf ntfy."""
        now = time.time() if now is None else now
        day = (datetime.fromtimestamp(now) - timedelta(days=1)).strftime("%Y-%m-%d")
        text = self.summary_text(day, today=manual)
        if manual:
            self._need_ntfy()
            return self._sent(self.ntfy.send("Puzzle-Kiosk: Zusammenfassung", text, 2, ["bar_chart"], wait=True))
        return self.ntfy.send("Puzzle-Kiosk: Tageszusammenfassung", text, 2, ["bar_chart"])

    def summary_due(self, now=None):
        """Einmal pro Tag ab der eingestellten Uhrzeit (auch nachgeholt, wenn der Kiosk um diese Zeit nicht lief)."""
        now = time.time() if now is None else now
        cfg = self.notify_settings()
        local = datetime.fromtimestamp(now)
        today = local.strftime("%Y-%m-%d")
        if not cfg["summary"] or not self.ntfy.ready() or cfg["sentDay"] == today or local.strftime("%H:%M") < cfg["time"]:
            return False
        saved = self.settings.get("kiosk_notify") if isinstance(self.settings.get("kiosk_notify"), dict) else {}
        self._set_setting("kiosk_notify", {**saved, "sentDay": today})
        self.send_summary(now)
        return True

    # ---------- Lauf: Absturz erkennen ----------
    def run_start(self):
        """Beim Start: liegt kiosk-run.json noch da, endete der letzte Lauf nicht sauber (Absturz, Speichergrenze, Strom)."""
        old = self._read(self.run_path, {})
        if old:
            self.notify("errors", "Puzzle-Kiosk: Neustart nach Absturz",
                        f"Der Kiosk lief seit {old.get('started', '?')} (Version {old.get('version', '?')}) und endete nicht "
                        f"sauber; zuletzt aktiv {old.get('alive', '?')}. Jetzt läuft {core.VERSION}.", 4, ["rotating_light"])
        self._run = {"started": now_iso(), "version": core.VERSION, "alive": now_iso()}
        core.write_json(self.run_path, self._run)
        return old

    def run_alive(self):
        if getattr(self, "_run", None):
            self._run["alive"] = now_iso()
            core.write_json(self.run_path, self._run)

    def run_end(self):
        try:
            os.remove(self.run_path)
        except OSError:
            pass

    # ---------- Grenzen pro Gerät ----------
    @staticmethod
    def _ip_net(ip):
        """IPv4 einzeln, IPv6 pro /64 (ein Anschluss hat meist ein ganzes /64, sonst wäre jede Adresse ein neues Gerät)."""
        try:
            addr = ipaddress.ip_address(str(ip).split("%")[0])
        except ValueError:
            return str(ip)
        if addr.version == 6 and addr.ipv4_mapped:
            return str(addr.ipv4_mapped)
        if addr.version == 6:
            return str(ipaddress.ip_network(f"{addr}/64", strict=False))
        return str(addr)

    def _ip_key(self, ip):
        day = time.strftime("%Y-%m-%d")
        if self.salt[0] != day:
            self.salt = (day, secrets.token_bytes(16))
            self.ip_hits = {}
            self.ip_puzzles = {}
        return hmac.new(self.salt[1], self._ip_net(ip).encode(), hashlib.sha256).hexdigest()[:24]

    def _check_active(self, ip):
        """Höchstens per_ip_active Puzzles pro Gerät gleichzeitig (gezählt ab heute, solange sie bestehen)."""
        if not self.limits.per_ip_active:
            return
        key = self._ip_key(ip)
        with self.kiosk_lock:
            alive = [pid for pid in self.ip_puzzles.get(key, []) if pid in self.puzzles]
            self.ip_puzzles[key] = alive
        if len(alive) >= self.limits.per_ip_active:
            raise LimitError(Msg("kioskActive", "Von diesem Gerät gibt es schon viele Puzzles. Lösche eines oder "
                                                "versuche es später nochmals."))

    def _remember(self, ip, pid):
        key = self._ip_key(ip)
        with self.kiosk_lock:
            self.ip_puzzles.setdefault(key, []).append(pid)

    def guest_gate(self, ip, new=True):
        """Vor dem Lesen eines Fotos: Pause oder voll, aktive Puzzles des Geräts (nur neue), Stundenbudget.
        Neue Puzzles und Fotowechsel zählen ins selbe Budget."""
        state = self.status()["status"]
        try:
            if state != "open":
                raise LimitError(Msg("kioskPaused", "Im Moment können keine neuen Puzzles gemacht werden.") if state == "paused"
                                 else Msg("kioskFull", "Der Speicher ist voll. Bitte später nochmals versuchen."))
            if new:
                self._check_active(ip)
            self.check_rate(ip)
        except LimitError as e:
            self.count("rejected")
            code = getattr(of(e), "code", "")
            if code == "kioskFull":
                self.watch_levels()
            elif code in ("kioskRate", "kioskActive"):
                self.device_limit(code)
            raise

    def check_rate(self, ip, now=None):
        now = time.time() if now is None else now
        key = self._ip_key(ip)
        with self.kiosk_lock:
            hits = [t for t in self.ip_hits.get(key, []) if now - t < 3600]
            if self.limits.per_ip_hour and len(hits) >= self.limits.per_ip_hour:  # 0 = ohne Grenze
                self.ip_hits[key] = hits
                raise LimitError(Msg("kioskRate", "Von diesem Gerät kamen in der letzten Stunde schon viele Puzzles. "
                                                  "Bitte später nochmals versuchen."))
            self.ip_hits[key] = hits + [now]

    # ---------- Gäste-Puzzles ----------
    def guest_create(self, body, ip, checked=False):
        if not checked:  # der Gäste-Port prüft schon vor dem Lesen (guest_gate)
            self.guest_gate(ip)
        body = {k: v for k, v in body.items() if k in ("name", "config", "photo", "card")}
        body["config"] = {**(body.get("config") or {}), "music": self._guest_music(body)}
        p = self.save_puzzle(body, None, "Gast")
        self._remember(ip, p["id"])
        key = secrets.token_urlsafe(24)
        self._update(p["id"], edit_hash=key_hash(key))
        self.count("created")
        self.watch_levels()
        return {**p, "key": key}

    def _guest_music(self, body):
        """Nur Musik aus der Bibliothek, keine eigenen Uploads."""
        music = (body.get("config") or {}).get("music")
        if isinstance(music, dict) and str(music.get("id") or "").startswith("lib:"):
            return music
        return None

    def check_key(self, pid, key):
        p = self._puzzle(pid)
        if not key or not hmac.compare_digest(p.get("edit_hash") or "", key_hash(key)):
            raise KeyError(pid)  # wie unbekannt: verrät nicht, ob es das Puzzle gibt
        return p

    def guest_save(self, pid, body, ip="", checked=False):
        """Änderung eines Gasts: bei Pause nichts, ein neues Foto zählt wie ein neues Puzzle ins Budget."""
        if not self.kiosk_enabled():
            self.count("rejected")
            raise LimitError(Msg("kioskPaused", "Im Moment können keine neuen Puzzles gemacht werden."))
        if body.get("photo") and not checked:
            self.guest_gate(ip, new=False)
        body = {k: v for k, v in body.items() if k in ("name", "config", "photo", "card")}
        body["config"] = {**(body.get("config") or {}), "music": self._guest_music(body)}
        return self.save_puzzle(body, pid)

    def guest_link(self, pid, body):
        """Link der Gäste: nur ab heute, gültig heute, bis morgen oder 7 Tage; Titel mit Vorgabe. Foto-Download bleibt
        gesperrt, «Download erlauben (Video)» dürfen Gäste selber wählen (0.1.17)."""
        p = self._puzzle(pid)
        new = not (p.get("online_link") or {}).get("token")
        out = {"download": False, "metadata": False}
        if "video" in body or new:
            out["video"] = body.get("video") is True
        if "days" in body or new:
            days = body.get("days")
            out["days"] = days if days in KIOSK_DAYS else 7
        if "title" in body:
            out["title"] = body.get("title") or self.title_default()
        elif new:
            out["title"] = self.title_default()
        if new:
            out["from"] = None
            out["active"] = True
        result = self.online_link(pid, out)
        if new:
            self.count("links")
        return result

    def count_view(self, pid, ip, agent, now=None):
        counted = super().count_view(pid, ip, agent, now)
        if counted:
            self.count("views")
        return counted

    def shared_view(self, pid, visitor=None):
        view = super().shared_view(pid, None)
        view.update({"kiosk": True, "likes": 0, "liked": False, "copyright": "",
                     "madeWithText": "", "madeWith": self.settings.get("made_with_url") or ""})
        return view

    def set_like(self, pid, visitor, on):
        raise KeyError(pid)

    def shared_puzzle(self, token):
        try:
            return super().shared_puzzle(token)
        except core.Expired:
            raise
        except KeyError:
            if token in self.gone:
                raise core.Expired()
            raise

    # ---------- Aufräumjob ----------
    def _forget(self, pid, why):
        p = self.puzzles.get(pid) or {}
        token = (p.get("online_link") or {}).get("token")
        if token:
            with self.kiosk_lock:
                self.gone[token] = time.time()
        core.set_actor("job")
        try:
            self.delete_puzzle(pid)
        except KeyError:
            pass

    def clean(self, now=None, by="Job"):
        now = time.time() if now is None else now
        done = {"drafts": 0, "expired": 0, "orphans": 0, "tmp": 0, "videos": 0}
        with self.lock:
            items = list(self.puzzles.values())
        for p in items:
            link = p.get("online_link") or {}
            if link.get("token"):
                if link.get("expires") and now > link["expires"]:
                    self._forget(p["id"], "expired")
                    done["expired"] += 1
            elif now - (p.get("created") or now) > self.limits.draft_hours * 3600:
                self._forget(p["id"], "draft")
                done["drafts"] += 1
        for name in os.listdir(self.puzzle_dir):
            path = os.path.join(self.puzzle_dir, name)
            if os.path.isdir(path):
                if name not in self.puzzles and now - os.path.getmtime(path) > 3600:
                    shutil.rmtree(path, ignore_errors=True)
                    done["orphans"] += 1
                    continue
                for f in os.listdir(path):
                    fp = os.path.join(path, f)
                    if f.endswith(".tmp") and now - os.path.getmtime(fp) > 3600:
                        try:
                            os.remove(fp)
                            done["tmp"] += 1
                        except OSError:
                            pass
        with self.lock:
            pids = list(self.puzzles)
        done["videos"] = self.videos.prune(pids)  # Videos ohne Puzzle (z. B. nach Absturz beim Löschen)
        with self.kiosk_lock:
            self.gone = {k: v for k, v in self.gone.items() if now - v < GONE_DAYS * 86400}
            core.write_json(self.gone_path, self.gone)
            run = {"when": datetime.fromtimestamp(now).astimezone().isoformat(timespec="seconds"), "by": by, **done}
            self.job = {"runs": ([run] + self.job.get("runs", []))[:JOB_KEEP], "every": self.limits.clean_minutes}
            core.write_json(self.job_path, self.job)
        self._storage = (0.0, 0)
        if any(done.values()):
            core.set_actor("job")
            self.protocol("kioskClean", **{k: str(v) for k, v in done.items()})
        return run

    def purge_recent(self, hours, by="Admin"):
        """Notbremse im Admin: alle Gäste-Puzzles der letzten hours Stunden löschen (Links melden danach 410)."""
        if isinstance(hours, bool) or not isinstance(hours, (int, float)) or not 0 < hours <= 24 * 30:
            raise ValueError(Msg("kioskPurgeHours", "Stunden zwischen 1 und 720 angeben."))
        since = time.time() - hours * 3600
        with self.lock:
            pids = [p["id"] for p in self.puzzles.values() if (p.get("created") or 0) >= since]
        for pid in pids:
            self._forget(pid, "purge")
        self._storage = (0.0, 0)
        core.set_actor("admin", by)
        self.protocol("kioskPurge", hours=str(hours), n=str(len(pids)))
        return len(pids)

    def run_job(self):
        """Ein Lauf des Aufräumjobs aus der Schleife: Fehler melden (einmal, bis er wieder läuft), danach Grenzen prüfen."""
        try:
            self.clean()
        except Exception as e:  # noqa: BLE001 – der Job läuft weiter, Roger bekommt eine Meldung
            traceback.print_exc()
            if not self.job_failed:
                self.notify("errors", "Puzzle-Kiosk: Aufräum-Job gescheitert",
                            f"{type(e).__name__}: {e}"[:300] + f"\nNächster Versuch in {self.limits.clean_minutes} Minuten.",
                            4, ["warning"])
            self.job_failed = True
            return False
        if self.job_failed:
            self.notify("errors", "Puzzle-Kiosk: Aufräum-Job wieder in Ordnung", "Der letzte Lauf ging durch.", 2,
                        ["white_check_mark"])
        self.job_failed = False
        self.watch_levels()
        return True

    def start_job(self):
        def loop():
            while True:
                self.run_job()
                time.sleep(max(1, self.limits.clean_minutes) * 60)

        def beat():
            while True:
                try:
                    self.run_alive()
                    self.summary_due()
                except Exception:  # noqa: BLE001
                    traceback.print_exc()
                time.sleep(HEARTBEAT)
        threading.Thread(target=loop, daemon=True).start()
        threading.Thread(target=beat, daemon=True).start()


class LimitError(ValueError):
    """Grenze erreicht: 429 statt 400."""


def mode_tags(kind):
    """Zusätzliche Dateien in puzzle.html: Kiosk-Gäste oder Kiosk-Admin."""
    v = core.VERSION
    if kind == "guest":
        return (f'<link rel="stylesheet" href="/kiosk.css?v={v}">\n'
                f'<script>window.PUZZLE_MODE = "kiosk";</script><script src="/kiosk.js?v={v}"></script>\n')
    return (f'<link rel="stylesheet" href="/kiosk.css?v={v}">\n'
            f'<script>window.PUZZLE_MODE = "kiosk-admin";</script><script src="/kiosk-admin.js?v={v}"></script>\n')


KIOSK_FILES = ("kiosk.js", "kiosk.css", "kiosk-admin.js")
# Suchmaschinen (Checkliste öffentliche Website): Startseite und Datenschutz sind öffentlich, alles Persönliche nicht.
# Englisch, weil die Seite ohne gespeicherte Wahl die Sprache des Browsers nimmt (Googlebot: Englisch); kiosk.js
# setzt den Titel danach in der gewählten Sprache. Eine URL für beide Sprachen, darum kein hreflang.
SEO_TITLE = "Puzzle · Turn your photo into a puzzle"
SEO_DESCRIPTION = ("Upload a photo, add music and a greeting, and watch it put itself back together as a puzzle. "
                   "Share it by link or QR code, no account needed.")
PRIVACY_DESCRIPTION = "Datenschutz beim Puzzle: was von deinem Foto gespeichert wird, keine Namen und keine IP-Adressen, und wann alles gelöscht wird."


def make_guest_handler(app):
    """Port 8080: nur der Gäste-Editor. Alles, was nicht ausdrücklich erlaubt ist, gibt 404."""
    Base = core.make_handler(app)

    class GuestHandler(Base):
        def _route(self, handler):
            super()._route(self._guarded(handler))

        def _guarded(self, handler):
            def run(parts):
                core.set_actor("guest")
                try:
                    handler(parts)
                except LimitError as e:
                    self._json(fields("error", of(e)), 429)
            return run

        def do_PATCH(self):
            self._route(self._post)

        def _key(self):
            return self.headers.get("X-Edit-Key") or ""

        def _mode_tags(self):
            return mode_tags("guest")

        def _strict_page(self):
            return True

        def _title(self):
            return SEO_TITLE

        def _head_tags(self):
            # Bearbeitungslinks (/edit/<id>) sind persönlich: nicht indexieren; die Startseite mit Beschreibung und canonical
            if self.path.split("?")[0].rstrip("/") == "":
                seo = (f'<meta name="description" content="{html.escape(SEO_DESCRIPTION)}">'
                       f'<link rel="canonical" href="{html.escape(self._base())}/">')
            else:
                seo = '<meta name="robots" content="noindex, nofollow">'
            # Seite verborgen, bis kiosk.js den Status kennt (sonst blitzt bei Pause die App auf)
            return seo + '<script>document.documentElement.classList.add("kWait")</script><style>html.kWait body{visibility:hidden}</style>'

        def _robots(self):
            lines = ["User-agent: *", "Allow: /", "Disallow: /edit/", "Disallow: /api/", "Disallow: /login", "Disallow: /admin"]
            if app.share_prefix and app.share_prefix != "/":
                lines.append(f"Disallow: {app.share_prefix}/")  # Spiel-Links, falls sie über denselben Host laufen
            lines.append(f"Sitemap: {self._base()}/sitemap.xml")
            self._send(200, ("\n".join(lines) + "\n").encode(), "text/plain; charset=utf-8")

        def _sitemap(self):
            base = html.escape(self._base())
            urls = "".join(f"<url><loc>{base}{p}</loc></url>" for p in ("/", "/datenschutz"))
            page = f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>\n'
            self._send(200, page.encode(), "application/xml; charset=utf-8")

        def _get(self, parts):
            if parts in (["health"], ["favicon.svg"], ["favicon.ico"]) or parts[:1] in (["brand"], ["fonts"]):
                super()._get(parts)
            elif parts == [""] or (len(parts) == 2 and parts[0] == "edit" and core.PUZZLE_ID.match(parts[1])):
                super()._get([""])
            elif parts in (["datenschutz"], ["privacy"]):
                self._privacy()
            elif parts == ["robots.txt"]:
                self._robots()
            elif parts == ["sitemap.xml"]:
                self._sitemap()
            elif parts in (["common.css"], ["common.js"], ["profile.js"], ["i18n.js"], ["puzzle-engine.js"], ["qr.js"], ["kiosk.js"], ["kiosk.css"]) \
                    or (len(parts) == 1 and parts[0] in core.text_files()):
                self._script(parts[0])
            elif parts == ["api", "version"]:
                day, month, year = core.VERSION_DATE.split(".")
                self._json({"version": core.VERSION, "date": f"{year}-{month}-{day}", "built": core.BUILT, "immich": False, "kiosk": True})
            elif parts == ["api", "auth", "session"]:
                self._json({"enabled": False, "user": None, "name": None})
            elif parts == ["api", "family"]:
                self._json({"self": None, "services": [], "openMode": "tab"})
            elif parts == ["api", "kiosk"]:
                self._json(app.status())
            elif parts == ["api", "music"]:
                music = app.music_list()
                self._json({**music, "tracks": [x for x in music["tracks"] if not x["own"]]})
            elif len(parts) == 3 and parts[:2] == ["api", "music"] and urllib.parse.unquote(parts[2]).startswith("lib:"):
                super()._get(parts)
            elif parts == ["api", "puzzles"]:  # keine Liste für Gäste
                self._json({"puzzles": [], **app.share_setup(), **app.guest_settings()})
            elif parts == ["api", "shares"]:
                self._json({"shares": [], **app.share_setup()})
            elif len(parts) == 4 and parts[:2] == ["api", "puzzles"] and parts[3] in ("photo.jpg", "thumb.jpg", "card.jpg"):
                super()._get(parts)  # Bilder über die zufällige Kennung, wie ein Link
            elif len(parts) == 4 and parts[:3] == ["api", "kiosk", "puzzles"]:
                app.check_key(parts[3], self._key())
                self._json(app.get_puzzle(parts[3]))
            else:
                raise KeyError(self.path)

        def _read(self, limit=1024 * 1024):
            data = super()._read(limit)
            self._unread = False
            return data

        def _post(self, parts):
            # Abgelehnt, bevor der Body gelesen ist: kleine Bodies leeren (Verbindung bleibt brauchbar), grosse nicht lesen
            self._unread = True
            try:
                self._guest_post(parts)
            finally:
                if self._unread:
                    try:
                        length = int(self.headers.get("Content-Length") or 0)
                    except ValueError:
                        length = -1
                    if 0 <= length <= PHOTO_BODY:
                        self.rfile.read(length)
                    else:
                        self.close_connection = True

        def _guest_post(self, parts):
            limit = app.limits.photo_mb * 1024 * 1024 * 14 // 10 + 1024 * 1024  # Foto als data:-URL (+40 %)
            ip = core.login.client_ip(self.client_address[0], self.headers)
            if parts == ["api", "kiosk", "puzzles"] and self.command == "POST":
                self._length(limit)
                app.guest_gate(ip)
                self._json(app.guest_create(self._body(limit), ip, checked=True))
            elif len(parts) == 4 and parts[:3] == ["api", "kiosk", "puzzles"]:
                app.check_key(parts[3], self._key())
                photo = self._length(limit) > PHOTO_BODY  # gross = mit Foto: Budget vor dem Lesen
                if photo:
                    app.guest_gate(ip, new=False)
                self._json(app.guest_save(parts[3], self._body(limit), ip, checked=photo))
            elif len(parts) == 5 and parts[:3] == ["api", "kiosk", "puzzles"] and parts[4] == "link":
                app.check_key(parts[3], self._key())
                self._json(app.guest_link(parts[3], self._body()))
            else:
                self._read(limit)
                raise KeyError(self.path)

        def _delete(self, parts):
            self._read()
            if len(parts) == 4 and parts[:3] == ["api", "kiosk", "puzzles"]:
                app.check_key(parts[3], self._key())
                self._json(app.delete_puzzle(parts[3]))
            elif len(parts) == 5 and parts[:3] == ["api", "kiosk", "puzzles"] and parts[4] == "link":
                p = app.check_key(parts[3], self._key())
                token = (p.get("online_link") or {}).get("token")
                if token:
                    with app.kiosk_lock:
                        app.gone[token] = time.time()
                self._json(app.revoke_online_link(parts[3]))
            else:
                raise KeyError(self.path)

        def _privacy(self):
            text = app.settings.get("kiosk_privacy") or PRIVACY_DEFAULT
            contact = app.settings.get("kiosk_contact") or ""
            paras = "".join(f"<p>{html.escape(x).replace(chr(10), '<br>')}</p>" for x in text.split("\n\n") if x.strip())
            if contact:
                paras += f'<h2>Kontakt</h2><p>{html.escape(contact)}</p>'
            page = (f'<!DOCTYPE html><html lang="de"><head><meta charset="utf-8">'
                    f'<meta name="viewport" content="width=device-width, initial-scale=1"><title>Datenschutz · Puzzle</title>'
                    f'<meta name="description" content="{html.escape(PRIVACY_DESCRIPTION)}">'
                    f'<link rel="canonical" href="{html.escape(self._base())}/datenschutz">'
                    f'<link rel="icon" type="image/svg+xml" href="/favicon.svg"><link rel="stylesheet" href="/common.css?v={core.VERSION}">'
                    f'<link rel="stylesheet" href="/kiosk.css?v={core.VERSION}"></head>'
                    f'<body class="kPrivacy"><main><p><a href="/">‹ Zurück zum Puzzle</a></p><h1>Datenschutz</h1>{paras}</main></body></html>')
            self._send(200, page.encode(), "text/html; charset=utf-8")

    return GuestHandler


def make_admin_handler(app, auth):
    """Port 8082: die App mit Login, dazu /api/kiosk/admin (Box «Kiosk»)."""
    Base = core.make_handler(app, auth, None)

    class AdminHandler(Base):
        def _mode_tags(self):
            return mode_tags("admin")

        def _get(self, parts):
            if parts in (["kiosk.css"], ["kiosk-admin.js"]):
                self._script(parts[0])
            elif parts == ["api", "kiosk", "admin"]:
                self._json(app.admin_view())
            elif parts == ["api", "family"]:
                self._json({"self": None, "services": [], "openMode": "tab"})
            elif parts[:2] == ["api", "collages"]:  # Collage als Motiv nicht im Kiosk (Roger 25.11.2026)
                raise KeyError(self.path)
            else:
                super()._get(parts)

        def _post(self, parts):
            if parts == ["api", "kiosk", "admin", "settings"]:
                app.set_kiosk_settings(self._body(64 * 1024))
                self._json(app.admin_view())
            elif parts == ["api", "kiosk", "admin", "purge"]:
                n = app.purge_recent(self._body().get("hours"), self._by() or "Admin")
                self._json({**app.admin_view(), "purged": n})
            elif parts == ["api", "kiosk", "admin", "job"]:
                self._read()
                app.clean(by=self._by() or "Admin")
                self._json(app.admin_view())
            elif parts == ["api", "kiosk", "admin", "notify"]:
                app.set_notify_settings(self._body())
                self._json(app.admin_view())
            elif parts == ["api", "kiosk", "admin", "notify", "test"]:
                self._read()
                sent = app.notify_test()
                self._json({**app.admin_view(), "sent": sent})
            elif parts == ["api", "kiosk", "admin", "notify", "summary"]:
                self._read()
                sent = app.send_summary(manual=True)
                self._json({**app.admin_view(), "sent": sent})
            else:
                super()._post(parts)

    return AdminHandler


def main():
    """Start im Kiosk-Modus (aus server.main, wenn PUZZLE_MODE=kiosk)."""
    for name in ("IMMICH_URL", "IMMICH_API_KEY", "FAMILY_FILE", "SHARES_API_KEY"):
        if (os.environ.get(name) or "").strip():
            print(f"Kiosk: {name} wird im Kiosk-Modus nicht verwendet.", flush=True)
    app = KioskApp(os.environ.get("DATA_DIR", "data"), os.environ.get("MUSIC_DIR", "/music"),
                   share_port=int(os.environ.get("SHARE_PORT") or 0), share_public_url=os.environ.get("SHARE_PUBLIC_URL", ""),
                   umami_url=os.environ.get("UMAMI_URL", ""), umami_id=os.environ.get("UMAMI_WEBSITE_ID", ""),
                   umami_links_id=os.environ.get("UMAMI_LINKS_ID", ""),
                   ntfy=Notifier(os.environ.get("NTFY_URL", ""), os.environ.get("NTFY_TOPIC", ""), os.environ.get("NTFY_TOKEN", "")))
    host, port = os.environ.get("HOST", "0.0.0.0"), int(os.environ.get("PORT", "8080"))
    admin_port = int(os.environ.get("ADMIN_PORT") or 8082)
    auth = login.Auth.from_env(app.data_dir)
    print(f"immich-puzzle {core.VERSION} im Kiosk-Modus: Gäste auf {host}:{port}, Ablage {app.puzzle_dir}, "
          f"Grenzen {app.limits.view()}, Kiosk {'an' if app.kiosk_enabled() else 'aus'}", flush=True)
    if auth.enabled:
        print(f"Admin auf {host}:{admin_port}, Login für {auth.user}", flush=True)
        server = core.ThreadingHTTPServer((host, admin_port), make_admin_handler(app, auth))
        threading.Thread(target=server.serve_forever, daemon=True).start()
    else:
        print("Admin-Port startet nicht: LOGIN_PASSWORD fehlt.", flush=True)
    if app.share_port:
        print(f"Live-Links auf {host}:{app.share_port}, Präfix {app.share_prefix}", flush=True)
        threading.Thread(target=core.serve_share(app, host, app.share_port).serve_forever, daemon=True).start()
    n = app.ntfy
    print(f"ntfy: {n.base}/{n.topic}" + (" mit Token" if n.token else " ohne Token") if n.ready()
          else "ntfy: aus (NTFY_URL/NTFY_TOPIC fehlen)", flush=True)
    app.run_start()

    def stop(signum, frame):  # docker stop / Redeploy: sauberes Ende, kein «Neustart nach Absturz»
        app.run_end()
        sys.exit(0)
    signal.signal(signal.SIGTERM, stop)
    app.start_job()
    print(f"Job: Aufräumen alle {app.limits.clean_minutes} Minuten", flush=True)
    try:
        core.ThreadingHTTPServer((host, port), make_guest_handler(app)).serve_forever()
    except KeyboardInterrupt:  # Strg+C lokal: auch sauber; jede andere Ausnahme lässt kiosk-run.json liegen (= Absturz)
        app.run_end()
