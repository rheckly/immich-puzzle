# Changelog Puzzle

## 0.1.7 – 2026-11-22
- `CHG` `.env.example` läuft ohne Anpassung (nur Hochladen): `IMMICH_URL` ist leer, die Beispieladresse steht im Kommentar
- `CHG` README: Abschnitt «Try it on your computer» mit Docker Desktop in vier Schritten (Dateien holen, `.env`, Start, Entfernen), auch für Windows (PowerShell); geteilte Puzzles unter `http://localhost:8081/puzzles/<Code>/`

## 0.1.6 – 2026-11-21
- `FIX` Handy: Schritte öffnen als ganzer Bildschirm unter der Kopfzeile, auch wenn die Seite vorher nach unten gescrollt war (vorher schaute die Seite oberhalb durch, es wirkte wie ein Popup); beim Schliessen steht die Seite wieder an der alten Stelle
- `NEW` Schritte 2–9: unten «‹ Vorheriger», «Nächster ›» und «Fertig»; «Motiv bearbeiten» behält «Abbrechen» und «Übernehmen»; auf dem Handy nur « und » als kurze Knöpfe, «Fertig» bekommt den Platz
- `FIX` Handy: «8 Vorschau» steht mit gewähltem Motiv nicht mehr als Kasten auf der Startseite, sondern öffnet wie alle Schritte als ganzer Bildschirm
- `FIX` iPhone: Seite wackelt nicht mehr seitwärts; Vorschau-Panel scrollt statt Tisch und Diagramm zusammenzustauchen; offener Schritt: Kopfzeile bleibt oben, der Hintergrund steht still und scrollt nicht mehr mit
- `CHG` Handy: Schritte gleiten seitwärts – tiefer und «Nächster» von rechts herein, Schliessen und «Vorheriger» nach rechts hinaus
- `NEW` Puzzle geht auch ohne Immich: Bleiben `IMMICH_URL` und `IMMICH_API_KEY` leer, zeigt der Schritt Motiv nur «Hochladen», «Über» meldet Immich als «Nicht eingerichtet»

## 0.1.5 – 2026-11-21
- `FIX` Schriftzug im fertigen Puzzle genau wie in der Vorschau: Leerzeilen bleiben (auch die am Anfang, der Server schnitt sie beim Speichern ab), lange Zeilen werden umgebrochen statt die ganze Schrift zu verkleinern, Rand 6 % der Breite
- `CHG` Repo bereit für die Öffentlichkeit: README auf Englisch mit Schnellstart (Immich-Schlüssel und Rechte, `.env`, `docker compose up -d`), Reverse Proxy, Sicherheitshinweis und allen Einstellungen; Demo-Film und Bildschirmfoto oben
  - `compose.yaml` allgemein: Image `ghcr.io/rheckly/immich-puzzle:latest`, Ordner `./data` und `./music`, keine festen IPs, Pfade, Benutzer oder Umami-IDs; dazu `.env.example`
  - Lizenz AGPL-3.0 (`LICENSE`); die bisherige Betriebsanleitung für das NAS steht deutsch in `docs/NAS.de.md`, Domains und Adressen als Platzhalter
- `CHG` `TRUSTED_PROXIES` hat keine Vorgabe mehr im Code; die IP des Reverse Proxy kommt in die Variable (ohne sie zählen nur die direkten Absender)
- `CHG` «Andere Dienste» zeigt einen Hinweis, wenn ausser Puzzle kein Dienst eingerichtet ist

## 0.1.4 – 2026-11-21
- `CHG` Dauer und Tempo: Der Regler «Rahmen fertig nach» erscheint nur, wenn im Schritt Puzzle «Zuerst den Rahmen» an ist (sonst wirkt er nicht)
- `CHG` Geteiltes Puzzle: Der Titel steht beim Abspielen von Anfang an oben (vorher erst nach dem letzten Teil)
- `FIX` Geteiltes Puzzle: Der Lautsprecher oben rechts schaltet die Musik jetzt wirklich stumm, auch auf iPhone und iPad
- `NEW` Geteiltes Puzzle: Knopf für das Tempo 1×, 2×, 4× oben rechts; die Musik läuft dabei in normalem Tempo weiter
- `FIX` Panels (z.B. Teilen) enden auch dann unten bündig, wenn die Kopfzeile ihre Höhe ändert (Pfad «Puzzle › Teilen», Umbruch)
- `CHG` Musik in der Vorschau spielt auch bei 2×, 3× und 5× weiter, in normalem Tempo; nur das Puzzle läuft schneller
- `CHG` Die Vorschau füllt auf dem Computer die ganze restliche Bildschirmbreite; das Puzzle wird so gross wie möglich, ohne dass man im Panel scrollen muss
- `FIX` Panelhöhe stimmt auch, wenn die Seite beim Öffnen nach unten gescrollt war (vorher wurde das Panel zu hoch)
- `CHG` Geöffnete Panels laufen auf dem Computer beim Scrollen mit und schliessen unten bündig mit der Startspalte ab, wie bei Collage
- `CHG` Die Vorschau steht nur noch, solange kein Schritt-Panel offen ist; öffnet man einen Schritt, ersetzt er die Vorschau, beim Schliessen kommt sie zurück
- `CHG` Ablauf in der Vorschau als Pfeilkette auf einer Zeile: grün erledigt, rot noch offen, der nächste Schritt unterstrichen; ein Klick öffnet den Schritt. Auf dem Handy lässt sich die Kette seitlich schieben
- `CHG` Die grünen Häkchen links in den Schritt-Boxen sind weg, den Stand zeigt die Pfeilkette in der Vorschau
- `CHG` Geöffnete Panels sind auf dem Computer bildschirmhoch, auch bei wenig Inhalt; «Fertig» steht unten, längere Inhalte scrollen im Panel
- `FIX` Fusszeile der Panels (z.B. «Abbrechen / Als Motiv übernehmen» bei den Fotos eines Albums) bleibt sauber unten stehen, mit Hintergrund und Trennlinie; keine Fotos mehr unterhalb der Knöpfe

## 0.1.3 – 2026-11-21
- `NEW` Neue Oberfläche nach dem abgenommenen Mockup: links die Schritte 1–9 mit Häkchen und Kurzfassung, rechts daneben die offenen Panels (auf dem Handy als eigene Seite), Pfad oben zum Zurückspringen, Esc schliesst
  - Willkommensseite, solange noch kein Motiv gewählt ist: Zahlen zu Puzzles, aktiven Links, Aufrufen und eigener Musik, dazu die letzten Änderungen
  - Box «Name» zuoberst; der Name ist auch der Titel der Website, ausser der Link hat einen eigenen Titel
  - Vorschau mit Ablauf der Schritte (Klick springt zum Schritt) und dem Titel, wie ihn der Empfänger sieht
- `NEW` Motiv aus Immich: Alben, Personen und mit dir geteilte Alben als sortierbare Tabelle mit Suche, daneben die Fotos zum Auswählen; auch Link oder ID direkt eingeben
- `NEW` Motiv bearbeiten: neun Formate (Foto, Frei, 1:1, 4:3, 3:2, 16:9, 3:4, 2:3, 9:16), drehen, zoomen, Ausschnitt verschieben, bei «Frei» an den Ecken ziehen
- `NEW` Dauer und Tempo mit Kurve: wann der Rahmen und das Puzzle fertig sind, Spitze und Schnitt pro Abschnitt; Regler für Rahmen-Anteil und «schneller werden ab»
- `NEW` Schriftzug mit Vorschau auf dem Motiv: 21 Schriften, Grösse 50–200 %, neun Positionen, 14 Farben und eigene Farbe
- `NEW` Website-Titel pro Link, beim Teilen und in den Link-Details änderbar (leer = Name des Puzzles)
- `CHG` Speichern geschieht automatisch kurz nach jeder Änderung; der Knopf «Speichern» entfällt
- `CHG` Fertiges Bild zeigen jetzt 2 bis 10 s, Tempo-Bogen bis 200 %

## 0.1.2 – 2026-11-21
- `NEW` Startboxen wie bei Collage: «Was möchtest du heute erstellen?» mit «Neues Puzzle» und «Etwas anderes» (öffnet rechts «Andere Dienste» der Familie)
  - Box «Bisherige Puzzles» mit «N Puzzles, zuletzt …»; «>» öffnet rechts die Liste mit Suche, ein Klick auf ein Puzzle daneben das Detail-Panel
  - Detail-Panel wie bei Reels: oben die Vorschau zum Abspielen (mit Musik), darunter Bearbeiten, «Nur Einstellungen übernehmen» (neues Puzzle mit denselben Einstellungen, Motiv, Titel und Schriftzug neu) und Löschen
  - Darunter «Erstellt aus»: Länge, Erstellt, Geändert, Teile, Format, Start, Musik, Titel, Live-Link mit Status, Gültig bis, Aufrufe, Adresse und Sprung zu «Geteilte Puzzles»
- `NEW` Zoom am Schluss: Ist das Puzzle fertig, zoomt das fertige Bild langsam auf Bildschirmgrösse, in der Vorschau und auf der Seite des Empfängers
- `NEW` Box «Geteilte Puzzles» wie bei Collage: alle Live-Links mit Vorschaubild, Gültigkeit und Aufrufen, Filter Aktiv/Abgelaufen/Alle, «Abgelaufene löschen»
  - Pro Link: Adresse kopieren, Schalter Aktiv, Öffnen, Gültigkeit ab heute ändern (auch bis Datum), Löschen
  - «Details ›» öffnet daneben ein Panel mit allen Einstellungen: Aktiv, Passwort, Download, Metadaten, Gültigkeit, Titel ändern, Puzzle bearbeiten, Link löschen
- `NEW` Box «Einstellungen» mit dem Abschnitt Live-Links: Adresse der Live-Links ansehen und in der App ändern (gilt vor SHARE_PUBLIC_URL aus dem Stack, «Vorgabe verwenden» setzt zurück)
- `CHG` «Über» wie bei Collage: Box mit «>», das Panel öffnet sich rechts statt der Vorschau (Version, Immich-Verbindung, Box «Changelog»), der Changelog daneben mit Release-Auswahl und Filtern NEW/CHG/FIX/LNG

## 0.1.1 – 2026-11-20
- `CHG` Hinweisseite geteilter Link wie bei Collage abgesichert: Content-Security-Policy (nur eigene Bilder, Umami nur mit UMAMI_LINKS_ID, kein Einbetten), dazu Referrer-Policy no-referrer und X-Robots-Tag noindex als Header

## 0.1.0 – 2026-11-20
- `NEW` Erste Version: Ein Foto aus Immich oder hochgeladen setzt sich Teil für Teil zum Puzzle zusammen, live im Browser (kein Video)
  - Format wie das Foto, 3:2, 1:1 oder 9:16; 24, 48, 100, 200 oder 500 Teile; Start verstreut, als Haufen oder gemischt
  - Schalter für verdrehte Teile, Rahmen zuerst und blasse Vorlage; «Neu mischen» und Tempo-Bogen
  - Musik wie bei Reels (gemeinsamer Musikordner, Suche, Anhören, «Keine Musik», Lautstärke, Ausblenden, eigene Musik hochladen); Dauer wie die Musik (höchstens 1:00) oder 10 bis 60 Sekunden
  - Schriftzug mehrzeilig, im Puzzle oder am Schluss, mit Schrift, Position und Farbe; Titel und Haltezeit am Schluss
  - Live-Link unter links.example.com/puzzles/… mit Passwort, Download, Metadaten, Gültigkeit und Aktiv-Schalter, Vorschaukarte für WhatsApp und Senden per WhatsApp
  - Empfängerseite dunkel mit grossem ▶, am Schluss «Nochmals», «Ton» und «Foto herunterladen»
  - Hinweisseite geteilter Link wie bei allen Marken (Entscheid 52): unbekannt 404, abgelaufen 410, nicht aktiv und noch nicht gültig 403, Passwort 200/401/429, Basisadresse 404; Deutsch oder Englisch nach Browser mit Link zur anderen Sprache, Hell/Dunkel nach System, nichts vom Inhalt, kein Aufruf gezählt, Umami-Ereignis «Hinweis» mit Grund
