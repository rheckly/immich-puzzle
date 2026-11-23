"""Meldungen über ntfy für App und Kiosk (App ab 0.1.18, Kiosk ab 0.1.13).

Adresse, Topic und Token kommen nur aus dem Stack (NTFY_URL, NTFY_TOPIC, NTFY_TOKEN), nie aus dem Admin: kein Ziel von
aussen wählbar, der Token steht in keiner Antwort. Ohne NTFY_URL/NTFY_TOPIC sind die Meldungen einfach aus.
"""
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

from msg import Msg


def now_iso(when=None):
    return datetime.fromtimestamp(time.time() if when is None else when).astimezone().isoformat(timespec="seconds")


def clean_time(value):
    """Uhrzeit der Tageszusammenfassung als HH:MM (7:30 → 07:30)."""
    value = str(value or "").strip()
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", value)
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        raise ValueError(Msg("notifyTime", "Uhrzeit als HH:MM angeben, z. B. 08:00."))
    return f"{int(m.group(1)):02d}:{m.group(2)}"


class Notifier:
    """Schickt Meldungen als JSON an die Wurzel von NTFY_URL (wie Smart Albums), Token als Bearer.
    NTFY_URL darf das Topic auch selbst tragen (https://ntfy.example.ch/puzzle-kiosk), dann darf NTFY_TOPIC leer sein."""

    def __init__(self, url="", topic="", token="", opener=None):
        url, topic = (url or "").strip(), (topic or "").strip().strip("/")
        p = urllib.parse.urlsplit(url)
        if p.scheme in ("http", "https") and p.netloc:
            if not topic and p.path.strip("/") and "/" not in p.path.strip("/"):
                topic = p.path.strip("/")
            self.base = f"{p.scheme}://{p.netloc}"
        else:
            self.base = ""
            if url:
                print("ntfy: NTFY_URL muss mit http:// oder https:// beginnen, Meldungen aus.", flush=True)
        self.topic = topic if re.fullmatch(r"[\w-]{1,64}", topic or "") else ""
        self.token = (token or "").strip()
        self.opener = opener or urllib.request.urlopen
        self.last = None

    @classmethod
    def from_env(cls):
        return cls(os.environ.get("NTFY_URL", ""), os.environ.get("NTFY_TOPIC", ""), os.environ.get("NTFY_TOKEN", ""))

    def ready(self):
        return bool(self.base and self.topic)

    def view(self):
        return {"ready": self.ready(), "url": self.base, "topic": self.topic, "token": bool(self.token), "last": self.last}

    def describe(self):
        """Eine Zeile fürs Startlog, ohne Token."""
        if not self.ready():
            return "ntfy: aus (NTFY_URL/NTFY_TOPIC fehlen)"
        return f"ntfy: {self.base}/{self.topic}" + (" mit Token" if self.token else " ohne Token")

    def send(self, title, message, prio=3, tags=(), wait=False):
        """wait=False: im Hintergrund, nie blockierend. Gibt {"ok", "when", "title"[, "error"]} zurück (ohne wait sofort ok)."""
        if not self.ready():
            return {"ok": False, "error": "ntfy ist nicht eingerichtet"}
        body = json.dumps({"topic": self.topic, "title": title, "message": message, "priority": prio,
                           "tags": list(tags)}).encode()
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token

        def run():
            req = urllib.request.Request(self.base + "/", data=body, method="POST", headers=headers)
            try:
                with self.opener(req, timeout=10) as r:
                    r.read()
                res = {"ok": True, "when": now_iso(), "title": title}
            except urllib.error.HTTPError as e:
                res = {"ok": False, "when": now_iso(), "title": title, "error": f"ntfy antwortet {e.code} {e.reason}"}
            except (OSError, ValueError) as e:
                res = {"ok": False, "when": now_iso(), "title": title, "error": f"ntfy nicht erreichbar ({getattr(e, 'reason', None) or e})"[:160]}
            self.last = res
            if not res["ok"]:
                print(f"ntfy: {res['error']} ({title})", flush=True)
            return res
        if wait:
            return run()
        threading.Thread(target=run, daemon=True).start()
        return {"ok": True}


class RunMarker:
    """Datei, die da ist, solange der Dienst läuft, und beim sauberen Ende verschwindet. Liegt sie beim Start noch da,
    endete der letzte Lauf nicht sauber (Absturz, Speichergrenze, Strom)."""

    def __init__(self, path, version):
        self.path, self.version, self.run = path, version, None

    def start(self):
        """Gibt den alten Lauf zurück ({} beim sauberen letzten Ende) und schreibt den neuen."""
        try:
            with open(self.path, encoding="utf-8") as f:
                old = json.load(f)
        except (OSError, ValueError):
            old = {}
        self.run = {"started": now_iso(), "version": self.version, "alive": now_iso()}
        self._write()
        return old if isinstance(old, dict) else {}

    def alive(self):
        if self.run:
            self.run["alive"] = now_iso()
            self._write()

    def end(self):
        try:
            os.remove(self.path)
        except OSError:
            pass

    def _write(self):
        tmp = self.path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.run, f)
            os.replace(tmp, self.path)
        except OSError as e:
            print(f"Laufdatei: {e}", flush=True)
