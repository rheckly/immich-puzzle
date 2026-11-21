"""Familie der Immich-Zusatzdienste (Design-Entscheid 21 vom 16.11.2026): Register und Familien-Login.

Register: eine Datei family.json (FAMILY_FILE, Standard /family/family.json), read-only in jeden Container gemountet.
Inhalt ist eine Liste von {"id", "name", "claim", "url", "new_path", "enabled"}. Fehlt die Datei oder ist sie kaputt, gibt es
keinen Umschalter und keinen Fehler. Ungültige Einträge werden einzeln übersprungen.

Familien-Login: nach dem Login setzt jeder Dienst zusätzlich das Cookie family_session auf der Elterndomain
(FAMILY_COOKIE_DOMAIN, z.B. .example.com). Inhalt:

    v1.<Benutzer, URL-kodiert>.<Ablauf in Unix-Sekunden>.<HMAC-SHA256 hex>

Die Signatur ist HMAC-SHA256 mit dem Schlüssel FAMILY_SECRET (UTF-8) über den Text "v1.<Benutzer>.<Ablauf>", also alles vor
dem letzten Punkt. Jeder Dienst der Familie mit demselben FAMILY_SECRET nimmt das Cookie an. FAMILY_SSO=off oder ein
fehlendes/zu kurzes FAMILY_SECRET schaltet den Familien-Login für diesen Dienst aus (heutiges Verhalten).
Anmeldesperre (Entscheid 33): FamilyLockout zählt Fehlversuche pro IP gemeinsam für alle Dienste in
/family-state/login-fails.json (FAMILY_STATE_DIR); nach 5 Fehlversuchen 5 Minuten gesperrt. Format: docs/family-lockout.md.
Einstellungen (Entscheid 44): FamilySettings liest /family-state/family-settings.json ({"version": 1, "openMode": …}), wie
«Andere Dienste» öffnen (same, tab, window). Alle Dienste lesen, nur Smart Albums schreibt. Format: docs/family-settings.md.
Nur Standardbibliothek, damit die anderen Dienste den Teil 1:1 übernehmen können.
"""
import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets
import threading
import time
import urllib.parse

COOKIE = "family_session"
VERSION = "v1"
MIN_SECRET = 16
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
# Englischer Claim unter dem Namen (Entscheid 41, namen.md Kap. 1), nie übersetzt. Gilt, wenn family.json kein "claim" hat
CLAIMS = {
    "mosaik": "Photo mosaics from your Immich library",
    "collage": "Photo collages from your Immich library",
    "puzzle": "Photo puzzles from your Immich library",
    "timelapse": "Face timelapses from your Immich library",
    "reels": "Short films from your Immich library",
    "smart": "Share your moments",
}


# ---------- Register ----------

def _entry(raw):
    """Ein gültiger Eintrag oder None"""
    if not isinstance(raw, dict):
        return None
    sid, name, url = raw.get("id"), raw.get("name"), raw.get("url")
    new_path = raw.get("new_path", "/")
    if not (isinstance(sid, str) and ID_RE.match(sid) and isinstance(name, str) and name.strip()):
        return None
    if not (isinstance(url, str) and re.match(r"^https?://[^/\s]+", url)):
        return None
    if not isinstance(new_path, str) or not new_path.startswith("/") or new_path.startswith("//"):
        return None
    if raw.get("enabled", True) is not True:
        return None
    claim = raw.get("claim")
    claim = claim.strip() if isinstance(claim, str) and claim.strip() else CLAIMS.get(sid, "")
    return {"id": sid, "name": name.strip(), "claim": claim, "url": url.rstrip("/"), "new_path": new_path}


def load_register(path):
    """Die aktiven Dienste aus family.json, in der Reihenfolge der Datei. Fehlt oder kaputt: leere Liste."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    if isinstance(data, dict):  # Auch {"services": [...]} zulassen
        data = data.get("services")
    if not isinstance(data, list):
        return []
    seen, out = set(), []
    for raw in data:
        entry = _entry(raw)
        if entry and entry["id"] not in seen:
            seen.add(entry["id"])
            out.append(entry)
    return out


class Register:
    def __init__(self, path, own_id, settings=None):
        self.path = path
        self.own_id = own_id
        self.settings = settings  # FamilySettings oder None (dann gilt OPEN_MODE_DEFAULT)

    @classmethod
    def from_env(cls):
        return cls(os.environ.get("FAMILY_FILE", "/family/family.json"), (os.environ.get("FAMILY_ID") or "mosaik").strip(),
                   FamilySettings.from_env())

    def view(self):
        """Für GET /api/family. Kein Umschalter, wenn ausser dem eigenen Dienst keiner aktiv ist.
        openMode (Entscheid 44): wie das Frontend die anderen Dienste öffnet, aus family-settings.json."""
        services = load_register(self.path)
        if not any(s["id"] != self.own_id for s in services):
            services = []
        mode = self.settings.read()["openMode"] if self.settings else OPEN_MODE_DEFAULT
        return {"self": self.own_id, "services": services, "openMode": mode}


# ---------- Familien-Einstellungen (Entscheid 44) ----------
# Eine Datei für alle Dienste und alle Benutzer im State-Ordner (wie login-fails.json). Format: docs/family-settings.md.

SETTINGS_FILE = "family-settings.json"
SETTINGS_VERSION = 1
OPEN_MODES = ("same", "tab", "window")  # gleiches Fenster, neuer Tab, neues Fenster
OPEN_MODE_DEFAULT = "same"


def _parse_settings(raw):
    """Die gültigen Einstellungen aus dem Dateiinhalt (bytes) oder None, wenn er unlesbar ist oder eine andere Version hat.
    Ein unbekannter openMode gilt als Standard; weitere Felder bleiben erhalten (für spätere Einstellungen)."""
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("version") != SETTINGS_VERSION:
        return None
    if data.get("openMode") not in OPEN_MODES:
        data["openMode"] = OPEN_MODE_DEFAULT
    return data


class FamilySettings:
    """Liest und schreibt STATE_DIR/family-settings.json. read() fällt nie: fehlen Ordner, Datei oder ein gültiger Wert,
    gilt der Standard. Gelesen wird nur, wenn sich die Datei geändert hat (mtime, Grösse, Inode)."""

    def __init__(self, state_dir="", log=print):
        self.dir = state_dir or ""
        self.path = os.path.join(self.dir, SETTINGS_FILE) if self.dir else ""
        self.log = log
        self._lock = threading.Lock()
        self._cache = None  # (Stempel, Einstellungen)
        self._broken_warned = False

    @classmethod
    def from_env(cls, log=print):
        return cls(os.environ.get("FAMILY_STATE_DIR", "/family-state"), log)

    @staticmethod
    def default():
        return {"version": SETTINGS_VERSION, "openMode": OPEN_MODE_DEFAULT}

    def read(self):
        """Die Einstellungen als neues dict, mindestens {"version": 1, "openMode": "same"|"tab"|"window"}."""
        if not self.path:
            return self.default()
        try:
            st = os.stat(self.path)
        except OSError:  # Datei oder Mount fehlt
            return self.default()
        stamp = (st.st_mtime_ns, st.st_size, st.st_ino)
        with self._lock:
            if self._cache and self._cache[0] == stamp:
                return dict(self._cache[1])
        try:
            with open(self.path, "rb") as f:
                raw = f.read()
        except OSError:
            return self.default()
        data = _parse_settings(raw)
        if data is None:
            if not self._broken_warned:
                self._broken_warned = True
                self.log(f"Familien-Einstellungen: {self.path} ist unlesbar, es gilt der Standard")
            data = self.default()
        with self._lock:
            self._cache = (stamp, data)
        return dict(data)

    def write(self, open_mode):
        """Nur Smart Albums. Setzt openMode, behält weitere Felder, schreibt atomar (temp + rename). Gibt read() zurück.
        ValueError bei unbekanntem Wert, OSError wenn der Ordner fehlt oder nicht beschreibbar ist."""
        if open_mode not in OPEN_MODES:
            raise ValueError(f"openMode muss {', '.join(OPEN_MODES)} sein")
        if not self.dir or not os.path.isdir(self.dir):
            raise FileNotFoundError(f"Ordner {self.dir or '(leer)'} fehlt")
        current = None
        try:
            with open(self.path, "rb") as f:
                current = _parse_settings(f.read())
        except FileNotFoundError:
            pass
        data = dict(current or {})
        data.update({"version": SETTINGS_VERSION, "openMode": open_mode})
        body = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        tmp = os.path.join(self.dir, f"{SETTINGS_FILE}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
        try:
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
            with os.fdopen(fd, "wb") as f:
                f.write(body.encode("utf-8"))
                f.flush()
                os.fsync(f.fileno())
                _chmod(f.fileno())
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise
        with self._lock:
            self._cache = None
        return self.read()


# ---------- Familien-Login ----------

def _sign(secret, payload):
    return hmac.new(secret, payload.encode(), hashlib.sha256).hexdigest()


class FamilyLogin:
    def __init__(self, secret, domain="", sso=True):
        secret = (secret or "").strip()
        self.enabled = bool(sso) and len(secret) >= MIN_SECRET
        self.secret = secret.encode() if self.enabled else b""
        self.domain = (domain or "").strip()

    @classmethod
    def from_env(cls):
        sso = (os.environ.get("FAMILY_SSO") or "on").strip().lower() not in ("off", "0", "false", "no", "aus")
        return cls(os.environ.get("FAMILY_SECRET", ""), os.environ.get("FAMILY_COOKIE_DOMAIN", ""), sso)

    def make_value(self, user, days):
        expires = int(time.time()) + int(days * 86400)
        payload = f"{VERSION}.{urllib.parse.quote(user, safe='')}.{expires}"
        return f"{payload}.{_sign(self.secret, payload)}"

    def user_from_value(self, value):
        """Der Benutzer aus einem Cookie-Wert, sonst None (falsch signiert, abgelaufen, aus)."""
        if not self.enabled or not value or value.count(".") != 3:
            return None
        version, quoted, expires, sig = value.split(".")
        if version != VERSION or not expires.isdigit():
            return None
        if not hmac.compare_digest(sig.encode(), _sign(self.secret, f"{version}.{quoted}.{expires}").encode()):
            return None
        if int(expires) < time.time():
            return None
        user = urllib.parse.unquote(quoted)
        return user or None

    def session_user(self, cookie_header):
        for part in (cookie_header or "").split(";"):
            name, _, val = part.strip().partition("=")
            if name == COOKIE:
                user = self.user_from_value(val)
                if user:
                    return user
        return None

    def cookie_domain(self, host):
        """Die Domain fürs Cookie, wenn die Anfrage darunter liegt, sonst "" (Cookie nur für diesen Host)."""
        domain = self.domain.lstrip(".").lower()
        host = (host or "").split(":")[0].strip().lower()
        if domain and (host == domain or host.endswith("." + domain)):
            return "." + domain
        return ""

    def header(self, value, host, https, max_age=None):
        """Set-Cookie für family_session; value "" mit max_age 0 löscht es."""
        domain = self.cookie_domain(host)
        parts = [f"{COOKIE}={value}", "Path=/"]
        if domain:
            parts.append(f"Domain={domain}")
        parts += ["HttpOnly", "SameSite=Lax"]
        if max_age is not None:
            parts.append(f"Max-Age={max_age}")
        if https or domain:
            parts.append("Secure")
        return "; ".join(parts)


# ---------- Anmeldesperre (Entscheid 33) ----------
# Gemeinsam über alle Dienste der Familie gezählt, pro IP, in STATE_DIR/login-fails.json. Das Format steht für sich in
# docs/family-lockout.md (Face-Timelapse baut es in Rust nach): bei einer Änderung hier die Spezifikation mitziehen.

LOCK_FAILS = 5  # Nach so vielen Fehlversuchen ...
LOCK_SECONDS = 5 * 60  # ... ist die Anmeldung so lange gesperrt, ab dem letzten Fehlversuch
LOCK_FILE = "login-fails.json"
LOCK_LOCKFILE = "login-fails.lock"
LOCK_KEY_PREFIX = "login-fails.v1:"
LOCK_VERSION = 1
_KEY_RE = re.compile(r"^[0-9a-f]{64}$")


def canonical_ip(ip):
    """IPv4 punktiert ohne führende Nullen, IPv6 ausgeschrieben (8 Gruppen à 4 Hex-Ziffern, klein). IPv4 in IPv6
    (::ffff:a.b.c.d) gilt als IPv4, eine Zone (%eth0) fällt weg. Keine gültige Adresse: der Text ohne Leerraum am Rand."""
    text = str(ip or "").strip()
    try:
        addr = ipaddress.ip_address(text.split("%")[0])
    except ValueError:
        return text
    mapped = getattr(addr, "ipv4_mapped", None)
    return (mapped or addr).exploded


TRUSTED_PROXIES = ""  # keine Vorgabe; der Reverse Proxy kommt in die Variable TRUSTED_PROXIES (kommagetrennt)


def trusted_proxies(value=None):
    """Die Adressen, deren X-Real-IP und X-Forwarded-For gelten: aus TRUSTED_PROXIES (kommagetrennt), ohne Variable
    keine. Kanonisch (siehe canonical_ip); ungültige Einträge fallen weg. Leer gesetzt: kein Proxy gilt."""
    if value is None:
        value = os.environ.get("TRUSTED_PROXIES", TRUSTED_PROXIES)
    return frozenset(canonical_ip(p) for p in str(value).split(",") if _is_ip(p))


def _is_ip(text):
    try:
        ipaddress.ip_address(str(text or "").strip().split("%")[0])
    except ValueError:
        return False
    return True


def client_ip(peer, headers, trusted=None):
    """Die IP des Browsers, gleich für alle Dienste der Familie. Kommt die Verbindung von einem vertrauenswürdigen
    Proxy (trusted_proxies), gilt X-Real-IP, ersatzweise der letzte Eintrag in X-Forwarded-For (den der Proxy selbst
    anhängt). Von jeder anderen Adresse zählt die IP der Verbindung und die Kopfzeilen werden nicht beachtet, damit
    niemand im Netz eine fremde IP vortäuschen kann. headers braucht nur .get(name)."""
    peer = str(peer or "").strip()
    if trusted is None:
        trusted = trusted_proxies()
    if not _is_ip(peer) or canonical_ip(peer) not in trusted:
        return peer
    real = (headers.get("X-Real-IP") or "").strip()
    if _is_ip(real):
        return real
    last = (headers.get("X-Forwarded-For") or "").split(",")[-1].strip()
    if _is_ip(last):
        return last
    return peer


def _prune(times, now):
    """Gültige Fehlversuche einer IP: nur ganze Sekunden, aufsteigend, höchstens LOCK_FAILS. Ist der neueste älter als
    LOCK_SECONDS, fällt alles weg; sonst fallen die weg, die mehr als LOCK_SECONDS vor dem neuesten liegen. So hält eine
    Sperre volle LOCK_SECONDS ab dem 5. Fehlversuch, auch wenn der erste schon länger zurückliegt."""
    times = sorted(t for t in times if type(t) is int)
    if not times or times[-1] <= now - LOCK_SECONDS:
        return []
    newest = times[-1]
    return [t for t in times if t > newest - LOCK_SECONDS][-LOCK_FAILS:]


def _retry(times, now):
    """Sekunden bis zum nächsten erlaubten Versuch, 0 = frei."""
    times = _prune(times, now)
    if len(times) < LOCK_FAILS:
        return 0
    return max(1, times[-1] + LOCK_SECONDS - now)


class FamilyLockout:
    """Sperre nach LOCK_FAILS Fehlversuchen für LOCK_SECONDS, pro IP. Gemeinsam über die Datei im State-Ordner, wenn der
    Familien-Login an ist und der Ordner beschreibbar ist; sonst im Speicher dieses Dienstes nach derselben Regel."""

    def __init__(self, secret, state_dir="", sso=True, log=print):
        secret = (secret or "").strip()
        self.log = log
        self._mem = {}
        self._lock = threading.Lock()
        self._warned = False
        self._broken_warned = False
        self.secret = secret.encode()
        self.dir = state_dir or ""
        self.path = os.path.join(self.dir, LOCK_FILE) if self.dir else ""
        reason = ""
        if not sso:
            reason = "FAMILY_SSO ist aus"
        elif len(secret) < MIN_SECRET:
            reason = "FAMILY_SECRET fehlt oder ist zu kurz"
        elif not self.dir or not os.path.isdir(self.dir):
            reason = f"Ordner {self.dir or '(leer)'} fehlt"
        elif not os.access(self.dir, os.W_OK):
            reason = f"Ordner {self.dir} ist nicht beschreibbar"
        self.shared = not reason
        if reason:
            self.log(f"Anmeldesperre: {reason}, zähle Fehlversuche nur in diesem Dienst")

    @classmethod
    def from_env(cls, log=print):
        sso = (os.environ.get("FAMILY_SSO") or "on").strip().lower() not in ("off", "0", "false", "no", "aus")
        state = os.environ.get("FAMILY_STATE_DIR", "/family-state")
        return cls(os.environ.get("FAMILY_SECRET", ""), state, sso, log)

    def key(self, ip):
        """Schlüssel in der Datei: HMAC-SHA256 (Schlüssel FAMILY_SECRET) über «login-fails.v1:<IP>», hex klein."""
        return hmac.new(self.secret, (LOCK_KEY_PREFIX + canonical_ip(ip)).encode(), hashlib.sha256).hexdigest()

    # ---------- Öffentlich ----------

    def retry_after(self, ip, now=None):
        """0, wenn die IP versuchen darf, sonst die Sekunden bis zum nächsten Versuch (mindestens 1)."""
        now = int(time.time()) if now is None else int(now)
        shared = 0
        if self.shared:
            try:
                shared = _retry(self._read().get(self.key(ip), []), now)
            except OSError as e:
                self._fallback(e)
        with self._lock:  # Auch was im Rückfall nur hier gezählt wurde
            return max(shared, _retry(self._mem.get(canonical_ip(ip), []), now))

    def failed(self, ip, now=None):
        now = int(time.time()) if now is None else int(now)
        if self.shared:
            key = self.key(ip)
            try:
                self._update(lambda fails: fails.__setitem__(key, list(fails.get(key, [])) + [now]), now)
                return
            except OSError as e:
                self._fallback(e)
        with self._lock:
            ip = canonical_ip(ip)
            self._mem[ip] = _prune(self._mem.get(ip, []) + [now], now)

    def succeeded(self, ip, now=None):
        now = int(time.time()) if now is None else int(now)
        if self.shared:
            key = self.key(ip)
            try:
                self._update(lambda fails: fails.pop(key, None), now)
            except OSError as e:
                self._fallback(e)
        with self._lock:
            self._mem.pop(canonical_ip(ip), None)

    # ---------- Datei ----------

    def _fallback(self, error):
        if not self._warned:
            self._warned = True
            self.log(f"Anmeldesperre: {self.path} geht nicht ({error}), zähle in diesem Dienst")

    def _read(self):
        """Die gültigen Einträge der Datei. Fehlt sie, ist sie leer oder kaputt: keine Einträge."""
        try:
            with open(self.path, "rb") as f:
                raw = f.read()
        except FileNotFoundError:
            return {}
        try:
            data = json.loads(raw.decode("utf-8")) if raw.strip() else {}
        except (UnicodeDecodeError, ValueError):
            data = None
        if not isinstance(data, dict) or data.get("version") != LOCK_VERSION or not isinstance(data.get("fails"), dict):
            if raw.strip() and not self._broken_warned:
                self._broken_warned = True
                self.log(f"Anmeldesperre: {self.path} ist unlesbar, gilt als leer und wird beim nächsten Schreiben ersetzt")
            return {}
        return {k: v for k, v in data["fails"].items() if isinstance(k, str) and _KEY_RE.match(k) and isinstance(v, list)}

    def _update(self, change, now):
        """Unter der Sperrdatei lesen, ändern, abgelaufene Einträge entfernen, atomar ersetzen."""
        fd = os.open(os.path.join(self.dir, LOCK_LOCKFILE), os.O_RDWR | os.O_CREAT, 0o666)
        try:
            _chmod(fd)
            _lock_file(fd)
            try:
                fails = self._read()
                change(fails)
                fails = {k: t for k, t in ((k, _prune(v, now)) for k, v in fails.items()) if t}
                body = json.dumps({"version": LOCK_VERSION, "fails": fails}, sort_keys=True, separators=(",", ":"))
                tmp = os.path.join(self.dir, f"{LOCK_FILE}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
                try:
                    tfd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
                    with os.fdopen(tfd, "wb") as f:
                        f.write(body.encode("utf-8"))
                        f.flush()
                        os.fsync(f.fileno())
                        _chmod(f.fileno())
                    os.replace(tmp, self.path)
                except BaseException:
                    try:
                        os.remove(tmp)
                    except OSError:
                        pass
                    raise
            finally:
                _unlock_file(fd)
        finally:
            os.close(fd)


def _chmod(fd):
    """0666, damit Dienste mit anderer UID die Dateien auch schreiben können (umask ausser Kraft)."""
    try:
        os.fchmod(fd, 0o666)
    except (AttributeError, OSError):
        pass


try:
    import fcntl

    def _lock_file(fd, wait=2.0):
        """flock exklusiv, höchstens wait Sekunden warten, sonst OSError (dann zählt der Dienst lokal)."""
        end = time.monotonic() + wait
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return
            except BlockingIOError:
                if time.monotonic() >= end:
                    raise TimeoutError("Sperrdatei seit 2 s belegt")
                time.sleep(0.02)

    def _unlock_file(fd):
        fcntl.flock(fd, fcntl.LOCK_UN)
except ImportError:  # Windows, nur für Tests auf dem PC
    import msvcrt

    def _lock_file(fd):
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_LOCK, 1)

    def _unlock_file(fd):
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
