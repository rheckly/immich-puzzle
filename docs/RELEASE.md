# Versionen und Releases

## Nummern

Semantische Versionen `MAJOR.MINOR.PATCH`, solange wir unter 1.0 sind:

- **MINOR** (0.2.0, 0.3.0 …) für eine neue Funktion oder einen Auftrag aus der Planung (z.B. Live-Link teilen).
- **PATCH** (0.2.1 …) für Korrekturen, kleine Verbesserungen, Texte, Betrieb.
- 1.0.0, wenn Roger es festlegt.

Die Nummer steht an genau zwei Stellen, die immer gleich sein müssen (das Deploy-Skript und der Release-Workflow prüfen es):

- `app/server.py`: `VERSION` und `VERSION_DATE` (TT.MM.JJJJ)
- `app/CHANGELOG.md`: oberster Abschnitt `## X.Y.Z`

Eine neue Version beginnt mit der ersten Änderung danach: Nummer hochzählen, neuer Abschnitt oben im CHANGELOG. Weitere Änderungen derselben Runde kommen in denselben Abschnitt, bis sie released ist.

## Ablauf

| Schritt | Wer | Was |
|---|---|---|
| 1. Bauen und testen | Claude | Code, Tests (`.venv/Scripts/python -m unittest discover -s tests`), CHANGELOG, README |
| 2. Testversion | Claude (Skill `puzzle-build`) | `bash scripts/deploy-nas.sh`: kopiert `app/` aufs NAS, startet `immich-puzzle` neu, prüft die Version |
| 3. Abnahme | Roger | auf https://puzzle.immich.example.com ausprobieren |
| 4. «committen» | Claude auf Rogers Wort | Commit auf `main`, Push, NAS-Stand mit `--check` bestätigen |
| 5. Release | Claude auf Rogers Wort | `git tag vX.Y.Z && git push origin vX.Y.Z` |
| 6. Nachführen | Claude | Notion-Task «Erledigt», Homelab-Doku (Skill `homelab-doku`), wenn Betrieb, Ports, Stack oder Variablen ändern |

Der Tag startet `.github/workflows/release.yml`: Tests, Prüfung Tag = `VERSION` = CHANGELOG, Image `ghcr.io/rheckly/immich-puzzle:X.Y.Z` und `:latest`, GitHub-Release mit dem CHANGELOG-Abschnitt als Text. Von Hand gestartet baut der Workflow nur das Image `:dev`.

## Betrieb

Auf dem NAS läuft im Alltag `python:3.12-slim` mit dem gemounteten Ordner `app/` (siehe `compose.yaml`). Das Image ist der feste Stand jedes Releases, zum Zurückgehen oder für einen späteren Wechsel auf «nur Image». Ist das Paket auf ghcr.io öffentlich, braucht `docker pull` kein Token; solange es privat ist, eines mit `read:packages`.

## SSH-Zugang deploy

Der Neustart läuft über den eingeschränkten Benutzer `deploy` (Schlüssel `~/.ssh/nas_deploy`), der nur `puzzle-stop|start|restart|status` darf. Einrichten oder nach einem DSM-Update reparieren, als Admin auf dem NAS:

```
sudo sh <Docker-Ordner>/immich-tools/deploy/setup-deploy.sh
```

Das Skript (`scripts/setup-deploy.sh`) enthält alle Dienste mit Deploy-Zugang (face-timelapse, Reels, Smart Albums, Mosaik, Events, Collage, Puzzle). Auf dem NAS gibt es nur noch die eine Kopie unter `immich-tools/deploy/` (seit 17.11.2026, siehe `immich_smart_albums/docs/compose-vereinheitlichung.md`); sie muss gleich sein wie `scripts/setup-deploy.sh`.
