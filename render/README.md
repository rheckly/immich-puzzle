# Puzzle-Renderer (MP4-Download)

Macht aus einem geteilten Puzzle ein MP4: dieselbe `app/puzzle-engine.js` wie im Browser, hier in Node mit Skia
(`@napi-rs/canvas`), Bild für Bild an ffmpeg (H.264 + AAC). Kein Browser, kein Chromium.

- Läuft als eigener Dienst **im selben Stack** wie die App (`compose.kiosk.yaml` bzw. `compose.yaml`), damit jeder
  Renderer nur die Daten seiner App sieht.
- **Kein Netzwerk** (`network_mode: none`): kein Port, kein Internet, kein LAN. Austausch nur über Dateien in
  `data/render` (siehe Kopf von `render.js` und `app/render.py`).
- Ein Video nach dem anderen, ältester Auftrag zuerst, 60 s nach der letzten Änderung (entprellt).
- Video: 30 fps, kurze Seite 1080 px, quer 1920×1080 oder hoch 1080×1920 nach dem Foto; Titel oben, Fusszeile unten.
- Gehärtet: read_only, tmpfs /tmp, cap_drop ALL, no-new-privileges, 1 GB RAM, cpu_shares 256 (App hat Vorrang).

## Bauen (aus dem Repo-Ordner)

```
docker build -f render/Dockerfile -t immich-puzzle-render .
```

## Einstellungen (Umgebung)

| Variable | Standard | Bedeutung |
|---|---|---|
| `RENDER_SIZE` | 1080 | kurze Seite in Pixeln |
| `RENDER_FPS` | 30 | Bilder pro Sekunde |
| `RENDER_DEBOUNCE_SECONDS` | 60 | Ruhezeit nach der letzten Änderung |
| `RENDER_TIMEOUT_MINUTES` | 15 | Abbruch pro Video |
| `RENDER_TRIES` | 2 | Versuche, dann Stand «error» |

Test ohne Warteschlange: `node render.js --once <pid> <job.json> <out.mp4>`.
