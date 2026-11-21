"""Einfacher Login mit einem Benutzer, gleich wie bei Face-Timelapse und Reels.

Name und Passwort kommen aus LOGIN_USER und LOGIN_PASSWORD, der Anzeigename für den Profil-Button aus LOGIN_NAME (optional). Ohne LOGIN_PASSWORD ist der Login aus und alles läuft wie
bisher. Die Sitzung ist ein signiertes Cookie (HMAC-SHA256). Der Schlüssel wird beim ersten Start zufällig erzeugt und liegt
in DATA_DIR/login-secret (Rechte 600). Im signierten Inhalt steckt ein Fingerprint von Name und Passwort: Ändert sich eins
davon, gelten alte Cookies nicht mehr.

Gegen Raten (Entscheid 33): nach 5 Fehlversuchen pro IP ist die Anmeldung 5 Minuten gesperrt, ab dem letzten Fehlversuch.
Gezählt wird mit family.FamilyLockout gemeinsam für alle Dienste der Familie (/family-state/login-fails.json), ohne den
Ordner oder mit FAMILY_SSO=off nur in diesem Dienst nach derselben Regel. Die Client-IP bestimmt family.client_ip:
X-Real-IP bzw. X-Forwarded-For gelten nur von einem Proxy aus TRUSTED_PROXIES (ohne Vorgabe).
"""
import hashlib
import hmac
import ipaddress
import os
import secrets
import time
import urllib.parse

import family as fam

COOKIE = "mosaik_session"
REMEMBER_DAYS = 30
MAX_FAILS = fam.LOCK_FAILS
LOCK = fam.LOCK_SECONDS


def safe_next(target):
    """Nur ein relativer Pfad auf dieser Seite, sonst «/». Kein Open Redirect über //host oder /\\host."""
    target = str(target or "")
    if (not target.startswith("/") or target.startswith("//") or target.startswith("/\\")
            or any(ord(c) < 32 or c == "\\" for c in target)):
        return "/"
    return target


class Auth:
    def __init__(self, user, password, data_dir, name="", family=None, lockout=None):
        self.user = user or "admin"
        self.name = (name or "").strip()  # Nur für die Anzeige, nicht im Fingerprint
        self.password = password or ""
        self.enabled = bool(self.password)
        self.data_dir = data_dir
        self.family = family or fam.FamilyLogin("", sso=False)  # Familien-Login (Entscheid 21), nur mit eigenem Login
        # Sperre nach Fehlversuchen (Entscheid 33); ohne Angabe nur im Speicher dieses Dienstes
        self.lockout = lockout or fam.FamilyLockout("", sso=False, log=lambda *_: None)
        self._key = self._load_key() if self.enabled else b""
        # Ändert sich Name oder Passwort, ändert sich der Fingerprint und alte Cookies gelten nicht mehr
        self._print = hashlib.sha256(f"{self.user}\0{self.password}".encode()).hexdigest()[:16]

    @classmethod
    def from_env(cls, data_dir):
        return cls(os.environ.get("LOGIN_USER", "").strip(), os.environ.get("LOGIN_PASSWORD", ""), data_dir,
                   os.environ.get("LOGIN_NAME", ""), fam.FamilyLogin.from_env(),
                   fam.FamilyLockout.from_env(lambda text: print(text, flush=True)))

    def _load_key(self):
        path = os.path.join(self.data_dir, "login-secret")
        try:
            with open(path, encoding="ascii") as f:
                key = f.read().strip()
            if len(key) >= 32:
                return key.encode()
        except FileNotFoundError:
            pass
        os.makedirs(self.data_dir, exist_ok=True)
        key = secrets.token_hex(32)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="ascii") as f:
            f.write(key)
        try:
            os.chmod(path, 0o600)  # Auch wenn die Datei schon da war
        except OSError:
            pass
        return key.encode()

    # ---------- Prüfen ----------

    def check(self, user, password):
        """Name und Passwort in konstanter Zeit, beide immer verglichen."""
        user_ok = hmac.compare_digest(str(user or "").encode(), self.user.encode())
        password_ok = hmac.compare_digest(str(password or "").encode(), self.password.encode())
        return user_ok and password_ok

    def _sign(self, payload):
        return hmac.new(self._key, f"{payload}|{self._print}".encode(), hashlib.sha256).hexdigest()

    def make_cookie(self):
        expires = int(time.time()) + REMEMBER_DAYS * 86400
        payload = f"{urllib.parse.quote(self.user, safe='')}.{expires}"
        return f"{payload}.{self._sign(payload)}"

    def session_user(self, cookie_header):
        """Der angemeldete Benutzer: aus dem eigenen Cookie, sonst aus dem Familien-Cookie, sonst None."""
        if not self.enabled:
            return None
        return self._local_user(cookie_header) or self.family.session_user(cookie_header)

    def _local_user(self, cookie_header):
        if not self.enabled:
            return None
        value = None
        for part in (cookie_header or "").split(";"):
            name, _, val = part.strip().partition("=")
            if name == COOKIE:
                value = val
        if not value or value.count(".") != 2:
            return None
        quoted, expires, sig = value.split(".")
        payload = f"{quoted}.{expires}"
        if not hmac.compare_digest(sig, self._sign(payload)):
            return None
        if not expires.isdigit() or int(expires) < time.time():
            return None
        user = urllib.parse.unquote(quoted)
        return user if user == self.user else None

    # ---------- Bremse gegen Raten ----------

    def wait_seconds(self, ip, now=None):
        """0, wenn die IP versuchen darf, sonst die Sekunden bis zum nächsten Versuch (für Retry-After)."""
        return self.lockout.retry_after(ip, now)

    def failed(self, ip, now=None):
        """Zählt einen Fehlversuch. Gibt die Sperre in Sekunden zurück, wenn die IP jetzt gesperrt ist, sonst 0."""
        self.lockout.failed(ip, now)
        return self.lockout.retry_after(ip, now)

    def succeeded(self, ip, now=None):
        self.lockout.succeeded(ip, now)


def from_proxy(socket_ip):
    try:
        addr = ipaddress.ip_address(socket_ip)
    except ValueError:
        return False
    return addr.is_loopback or addr.is_private


def client_ip(socket_ip, headers):
    """Die IP des Browsers nach der Regel der Familie (family.client_ip): X-Real-IP oder der letzte Eintrag in
    X-Forwarded-For nur von einem vertrauenswürdigen Proxy (TRUSTED_PROXIES, ohne Vorgabe)."""
    return fam.client_ip(socket_ip, headers)


def is_https(socket_ip, headers):
    return from_proxy(socket_ip) and (headers.get("X-Forwarded-Proto") or "").split(",")[0].strip().lower() == "https"
