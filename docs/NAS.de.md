# Betrieb auf einem Synology-NAS (Deutsch)

Beispiel aus dem Betrieb des Autors: Puzzle läuft neben weiteren Immich-Diensten (Smart Albums, Mosaik, Collage,
Reels, Face-Timelapse) auf einem Synology-NAS, verwaltet mit Dockhand, hinter Nginx Proxy Manager (NPM). Für eine
gewöhnliche Installation reicht das [README](../README.md) mit `compose.yaml`.

Domains und Adressen sind hier Platzhalter: `puzzle.immich.example.com` (App), `links.immich.example.com` (Live-Links),
`<NAS-IP>` und `<NPM-IP>`.

## Lokal ausprobieren, ohne echtes Immich

```bash
python -m venv .venv && .venv/Scripts/python -m pip install -r app/requirements.txt
```

```bash
.venv/Scripts/python tests/fake_immich.py 2299 200
```

```bash
cd app && IMMICH_URL=http://127.0.0.1:2299 IMMICH_API_KEY=test-key DATA_DIR=../data PUZZLE_DIR=../data/puzzles MUSIC_DIR=../data/music-lib SHARE_PORT=8113 SHARE_PUBLIC_URL=http://127.0.0.1:8113/puzzles PORT=8112 ../.venv/Scripts/python server.py
```

Danach http://127.0.0.1:8112 öffnen. Die Attrappe liefert nur farbige Testbilder. Musik: MP3, M4A, OGG oder WAV in
`data/music-lib/<Ordner>/` legen oder in der App hochladen.

## Testversion und Release

`bash scripts/deploy-nas.sh` bringt den Arbeitsstand aufs NAS (Tests, Kopie, Neustart, Versionsprüfung). Das Skript
bleibt lokal (`.git/info/exclude`), ebenso die NAS-Fassung der Compose-Datei (`compose.nas.yaml`). Ein Release ist
ein Tag `vX.Y.Z` auf `main`: `.github/workflows/release.yml` prüft Version und CHANGELOG und baut beide Images.

## Auf dem NAS

Das NAS braucht kein Git und kein eigenes Image. Der Stack startet `python:3.12-slim` mit dem gemounteten Ordner
`app/`; der Code wird kopiert (`cp`, danach `cmp -s`; kein robocopy aus Git Bash). Pillow und mutagen landen beim
ersten Start in `data/lib`.

### Ordner

| Ordner | Zweck |
|---|---|
| `<docker>/immich-puzzle/app` | Code (nur lesen) |
| `<docker>/immich-puzzle/data` | Einstellungen, Puzzles, hochgeladene Musik, Python-Pakete |
| `<fotos>/puzzles` | Fertige Bilder der Puzzles (`PUZZLE_DIR`) |
| `<docker>/immich-tools/music` | Gemeinsame Musik aller Dienste (nur lesen) |
| `<docker>/immich-tools/family` | Register `family.json` und `state/` (Anmeldesperre der Familie) |

Alle Ordner gehören einem eigenen Dienstbenutzer (`user:` im Stack), wie bei den übrigen Diensten.

### Stack in Dockhand

Stack `immich-puzzle` mit dem Inhalt von `compose.nas.yaml` (lokal, nicht im Repo). Gegenüber `compose.yaml`:
`python:3.12-slim` mit gemountetem Code, fester Benutzer, externes Netz `immich` (`IMMICH_URL=http://immich_server:2283`),
Ports nur auf der NAS-IP, `TRUSTED_PROXIES` = IP von NPM, Familie und Umami gesetzt.

Geheimnisse nur als Stack-Variablen, nie in der Compose-Datei:

- `IMMICH_API_KEY`: Schlüssel mit Lesen von Alben, Personen und Bildern
- `LOGIN_USER`, `LOGIN_PASSWORD`, optional `LOGIN_NAME`
- `FAMILY_SECRET`: derselbe Wert wie bei den übrigen Diensten
- `SHARES_API_KEY`: Schlüssel für die Übersicht der Links in Smart Albums

Ports: `<NAS-IP>:8112` (App) und `<NAS-IP>:8113` (Links).

### Adressen

- App: `https://puzzle.immich.example.com` (NPM, zuerst Zertifikat, dann Force SSL, danach `http://` → 301 prüfen)
- Links: `https://links.immich.example.com/puzzles/<code>` (Block `/puzzles/` im Host `links.immich`, siehe
  [LIVE-LINKS.md](LIVE-LINKS.md))

### Neustart

Ein eingeschränkter SSH-Benutzer `deploy` darf nur `puzzle-stop|start|restart|status` (Präfix `puzzle-` im Skript
`setup-deploy.sh` auf dem NAS, als root ausführen).
