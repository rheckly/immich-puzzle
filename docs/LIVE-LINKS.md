# Live-Links

Ein gespeichertes Puzzle lässt sich als **Live-Link** teilen: `https://links.immich.example.com/puzzles/<Code>/`. Der
Empfänger sieht eine dunkle Seite mit grossem ▶; nach dem Tippen setzt sich das Puzzle live auf dem Canvas zusammen,
mit Musik. Am Schluss: «Nochmals», «Ton» und, wenn erlaubt, «Foto herunterladen».

## Freigabe-Port

Die App (Port 8080, auf dem NAS 8112) bleibt hinter dem Login. Live-Links laufen über einen zweiten Port im selben Container (8081, auf dem NAS 8113), der
**nur** geteilte Puzzles kennt:

- `/puzzles/<Code>/` die Empfängerseite (`app/play.html`) mit Open-Graph-Angaben (`og:image` = `card.jpg`, Titel)
- `/puzzles/<Code>/puzzle.json`, `photo.jpg`, `card.jpg`, `music` (mit Range), `download` (nur mit «Download erlauben»)
- Metadaten (Entscheid 50): Datum und Ort nur mit «Metadaten zeigen»
- `robots.txt`, `i18n.js`, `puzzle-engine.js`, Texte und `brand/`
- Alles andere, auch die ganze API, gibt 404

## Hinweisseite (Entscheid 52)

Darf ein Link nichts zeigen, kommt die gemeinsame Hinweisseite aller Marken (`app/share.html`, Design-Vorlage Kap. 7):
Karte wie die Login-Karte, Hell/Dunkel nach System, Deutsch oder Englisch nach `Accept-Language` (erster Eintrag mit
`de` oder `en`, sonst Deutsch), `?lang=de|en` vom Sprachlink unter der Karte. Der erste zutreffende Fall gilt:

| Fall | Status | Umami-Grund |
|---|---|---|
| Unbekannt, gelöscht, unvollständig | 404 | `ungueltig` |
| Abgelaufen | 410 | `abgelaufen` |
| Nicht aktiv (Entscheid 48) | 403 | `nicht-aktiv` |
| Noch nicht gültig (Entscheid 39) | 403 | `noch-nicht` |
| Passwort (Entscheid 49), falsch, gesperrt | 200, 401, 429 | `passwort` |
| Basisadresse `/puzzles/` ohne Code | 404 | `basis` |

`puzzle.json`, Bilder, Musik und Download bekommen denselben Status ohne Seite (gesperrt mit Passwort: 401). Die Seite
zeigt nichts vom Puzzle, keine og:-Vorschau und zählt keinen Aufruf. Mit `UMAMI_URL` und `UMAMI_LINKS_ID` meldet sie
sich mit Tag `puzzle`, URL `/puzzles/` (nie der Code), leerem Referrer und dem Ereignis `Hinweis` mit `grund`.

## NPM-Block für links.immich.example.com

In den **Advanced-Tab** des Hosts `links.immich.example.com`, unter die bestehenden Blöcke (unter `/collages/`), keine
Custom Location und **nicht** in immich.example.com:

```nginx
# Puzzle: Live-Links /puzzles/<Code>/ (Freigabe-Port 8113)
location ^~ /puzzles/ {
    proxy_pass http://<NAS-IP>:8113;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    sub_filter '</head>' '</head>';   # hebt das Umami-/og-Einfügen der Server-Ebene auf
}
```

> **Achtung:** Ein Fehler im Advanced-Tab setzt den ganzen Host offline. Vor dem Einfügen mit dem Live-Text vergleichen.
> Nach dem Speichern sofort prüfen:
>
> 1. Host-Status in NPM «Online»
> 2. `https://links.immich.example.com/s/<Slug>` (Smart Albums)
> 3. `https://links.immich.example.com/collages/<Code>/` (Collage)
> 4. `https://links.immich.example.com/puzzles/` (Hinweisseite «Hier liegt kein Puzzle», Status 404)

Der Block hat keinen Regex; `^~` beendet die Suche, die Regex-Blöcke von Smart Albums werden für `/puzzles/…` nicht geprüft.
