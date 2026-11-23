## 0.1.19 – 2026-11-23
- `CHG` README und Vorlagen für das Release nachgeführt: Video-Download (Render-Dienst, `RENDER_SIZE`), Meldungen über ntfy, klassische Teile, Kiosk in drei Schritten, `PUID`/`PGID` und Kiosk-Ports in der Tabelle aller Einstellungen; «Es wird kein Video gerendert» in der Einleitung korrigiert
  - `compose.kiosk.yaml`: Kiosk und Renderer auf dieselbe Version 0.1.19 (vorher Kiosk 0.1.9, Renderer 0.1.17); `.env.example` mit `NTFY_*` und `RENDER_SIZE`
  - `docs/RELEASE.md` (interner Ablauf mit NAS-Zugang) nicht mehr im Repo, bleibt nur lokal

## 0.1.18 – 2026-11-23
- `NEW` Meldungen aufs Handy über ntfy auch für Puzzle (wie im Kiosk): Tageszusammenfassung des Vortags (neue Puzzles und Links, Aufrufe, gesetzte Likes, meistgeschautes Puzzle, dazu der Stand) und Meldung beim Start nach einem Absturz
  - Neu unter Einstellungen › «Meldungen aufs Handy (ntfy)»: je Art ein Schalter, Uhrzeit der Zusammenfassung (Standard 08:00), «Testmeldung senden» und «Zusammenfassung jetzt senden»; der Kiosk-Admin zeigt weiter nur seine eigene Kiosk-Box
  - Adresse, Topic und Token nur im Stack (`NTFY_URL`, `NTFY_TOPIC`, `NTFY_TOKEN`), nie im Admin; ohne `NTFY_URL` bleiben die Meldungen aus. Ohne IP-Adressen und ohne Namen der Besucher
  - Likes pro Tag: ein gesetztes Like zählt neu auch am Tag (nur Tag und Zahl, wie die Aufrufe); ältere Likes haben kein Datum und fehlen deshalb in den Tageszahlen

## 0.1.17 – 2026-11-23
- `NEW` Video herunterladen: Empfänger eines geteilten Puzzles können es als MP4 herunterladen (1080p, quer 1920×1080 oder hoch 1080×1920 nach dem Foto, mit Titel, Fusszeile und Musik wie auf der Seite)
  - Gerendert wird im neuen Container `immich-puzzle-render` im selben Stack: ohne Netzwerk (kein Port, kein Internet), gehärtet wie der Kiosk, ein Video nach dem anderen, ohne Browser (dieselbe Puzzle-Engine mit Skia und ffmpeg)
  - Ein Video gibt es nur bei aktivem Link mit «Download erlauben (Video)» und eingeschaltetem Hauptschalter; es entsteht gleich nach dem Einschalten (nach 60 s Ruhe), nicht erst beim Öffnen des Links. Puzzle geändert: wird neu erstellt
  - Empfängerseite: Knopf «Video herunterladen», solange es entsteht «Video wird erstellt …»
  - Das Video wird mit dem Puzzle gelöscht; Video-Schalter aus, Link gelöscht oder Hauptschalter aus löscht es auch
  - Hauptschalter unter Einstellungen › «Video-Download» (App und Kiosk-Admin), Standard aus; dort auch, ob der Renderer läuft, wartende und fertige Videos und Speicher (höchstens 2 GB, 50 wartende)
  - Kiosk: Gäste wählen beim Teilen «Download erlauben (Video)» (nur wenn der Hauptschalter an ist); der Foto-Download bleibt gesperrt, in der Fusszeile steht «Erstellt mit Puzzle». Der Aufräumjob löscht auch Videos ohne Puzzle
  - Schriften ohne Linux-Version (Segoe Script, Georgia, Impact, Comic Sans) erscheinen im Video in freien Ersatzschriften
- `CHG` Foto herunterladen (Empfängerseite): das Foto in voller Grösse mit der Grussbotschaft auf dem Motiv (wie auf dem fertigen Puzzle: Schrift, Grösse, Lage, Farbe, Bogen, Drehung) und dunklem Balken oben (Titel, von, Datum, Ort) und unten (Fusszeile «Erstellt mit …», ©), wie im Video; unabhängig vom Video-Hauptschalter, wird mit dem Puzzle gelöscht. Es entsteht im Hintergrund gleich beim Teilen bzw. Einschalten von «Download erlauben (Foto)», nicht erst beim Tippen, und neu, sobald sich Titel, Grussbotschaft oder Foto ändern
- `CHG` iPhone und iPad: «Foto herunterladen» und «Video herunterladen» öffnen das Teilen-Menü, dort «Bild sichern» bzw. «Video sichern» legt es direkt in Fotos ab (Safari speichert Downloads sonst nur in «Dateien»); die Seite lädt das Foto schon beim Abspielen, damit das Menü beim Tippen sofort kommt; andere Geräte laden wie bisher herunter
- `NEW` Eigener Link-Schalter «Download erlauben (Video)» neben «Download erlauben (Foto)» (beim Teilen und in den Link-Details), sichtbar sobald der Video-Hauptschalter an ist; so entstehen keine Videos für Links, die nur das Foto teilen. Bisherige Links mit Download haben das Video aus
- `NEW` Schnittstelle für Smart Albums: `GET`/`PATCH /api/internal/shares` kennen neu `video` (Video-Download) und `share` («Teilen erlauben», Standard aus, wird noch nirgends angezeigt) und melden sie in `actions`; `download` bedeutet nur noch das Foto

## 0.1.16 – 2026-11-23
- `CHG` Kiosk: Puzzle erstellen in drei Schritten Foto · Gestalten · Teilen, immer ein Panel in der Mitte mit «Schritt n von 3» und Zurück/Weiter statt der Spalte mit neun Kästen
  - Foto: gewähltes Foto gross, «Anderes Foto» und «Ausschnitt anpassen»; nach dem Start öffnet sich gleich die Fotoauswahl
  - Gestalten: Vorschau läuft mit, dazu Anzahl Teile, Gesamtzeit (Standard 30 s) und Gruss; unter «Erweitert» Tempo, Teile am Anfang, Form, «Zuerst den Rahmen», Schrift mit Schriftfarbe (wie im Panel «Schriftzug») und Foto im Hintergrund
  - «Puzzle fertigstellen» erstellt den Link; Teilen zeigt «Dein Puzzle ist bereit», Link, Teilen, QR-Code (nur am Computer) und den Bearbeitungslink; Website-Titel und Gültigkeit unter «Erweitert»
  - «Neues Puzzle» fragt nach, solange der Bearbeitungslink nicht als gespeichert abgehakt ist
  - Startseite: «Weiter an deinem letzten Puzzle», wenn auf diesem Gerät noch eines offen ist
- `FIX` Kiosk: Nach «Zurück» im Browser blieb «Foto hochladen» gesperrt, obwohl das Datenschutz-Häkchen gesetzt war

## 0.1.15 – 2026-11-23
- `NEW` Klassische Puzzleteile wählbar: die Kante läuft gerade bis zur Nase, dort eine Ecke, die Nase hat einen runden Kopf (wie ein gekauftes Puzzle); Randteile bleiben aussen gerade, Ecken mit zwei geraden Kanten. Gilt im Editor, auf der Abspielseite, im Kiosk und im Vorschaubild für Messenger
  - Neu im Panel «Puzzle»: «Form der Teile» Organisch (Original, die bisherige geschwungene Form) oder Klassisch (gerade Kante); neue Puzzles starten organisch wie bisher, gespeicherte Puzzles und geteilte Links bleiben unverändert
  - Die Form zählt zu «Standard für neue Puzzles»

## 0.1.14 – 2026-11-23
- `CHG` Ein Klick irgendwo auf eine Box mit [>] öffnet ihr Panel, nochmals Klick schliesst es (Menü links, Kiosk-Admin, Grussbotschaften im Schriftzug, Karten unter «Geteilte Puzzles», «Änderungen» unter «Über»); das [>] zeigt nur noch, dass es weitergeht, und bleibt der Knopf für Tastatur und Screenreader. Schalter, Felder und Knöpfe in der Box behalten ihren eigenen Klick

## 0.1.13 – 2026-11-23
- `NEW` Kiosk: Meldungen aufs Handy über ntfy (`NTFY_URL`, `NTFY_TOPIC`, `NTFY_TOKEN` im Stack; ohne diese Variablen keine Meldungen)
  - Grenzen erreicht: Kiosk voll (Anzahl oder Speicher), Speicher knapp (ab 80 %), ein Gerät hat sein Stundenbudget oder seine aktiven Puzzles aufgebraucht (ohne IP, höchstens einmal pro Stunde mit Anzahl der Ablehnungen; voll und knapp erst wieder, wenn der Kiosk zwischendurch unter der Grenze war)
  - Fehler und Neustart: Start nach einem Absturz (der letzte Lauf endete nicht sauber), Aufräum-Job gescheitert und wieder in Ordnung
  - Tageszusammenfassung des Vortags: neue Puzzles, Links, Aufrufe, Abgelehnte, Stand von Puzzles, Links und Speicher
  - Admin → Einstellungen, Block «Kiosk»: je Meldungsart ein Schalter, Uhrzeit der Tageszusammenfassung (Standard 08:00), «Testmeldung senden» und «Zusammenfassung jetzt senden»; Adresse und Token nur aus dem Stack, das Token zeigt der Admin nie

## 0.1.12 – 2026-11-22
- `CHG` Statistik: Umami zeigt den Editor als «/puzzle-editor» und geteilte Links samt Hinweisseiten als «/puzzle-shared-link» statt «/» und «/p/»; bisherige Aufrufe bleiben unter den alten Adressen

# Changelog Puzzle

## 0.1.11 – 2026-11-22
- `FIX` Statistik: Umami-Skript heisst jetzt `hp.js` statt `script.js` (wie im übrigen Homelab), damit Werbeblocker auf dem Handy die Aufrufe von Editor und geteilten Links nicht mehr verschlucken
- `FIX` Statistik im Editor: pro Laden der Seite genau ein Aufruf mit Adresse «/»; bisher zählte jeder Panel-Wechsel und das Anlegen eines Puzzles als eigener Aufruf, mit Puzzle-ID in der Adresse

## 0.1.10 – 2026-11-22
- `FIX` Kiosk: Automatisches Speichern zählte nicht mehr als Fotowechsel, wenn nur das Startbild für die Linkvorschau über 256 KB war; auf dem Handy war das Stundenbudget sonst nach wenigen Änderungen weg und «Link erstellen» meldete «schon viele Puzzles» (Schwelle für «mit Foto» jetzt 1 MB, kleinere Fotos zählen weiterhin)

## 0.1.9 – 2026-11-22
- `NEW` Kiosk-Modus (`PUZZLE_MODE=kiosk`, eigener Container und Datenordner, `compose.kiosk.yaml`): Gäste machen ohne Login aus einem hochgeladenen Foto ein Puzzle und teilen es mit einem Link
  - Port 8080 nur für Gäste: Startseite mit drei Schritten, Bestätigung «Hinweise zum Datenschutz gelesen», danach Motiv, Motiv bearbeiten, Puzzle, Dauer und Tempo, Schriftzug, Vorschau, Teilen; ohne Immich, Liste, Profil, Einstellungen und eigene Musik (Schritt Musik nur mit Bibliothek)
  - Teilen: Feld «Website-Titel» vor dem Link (Vorgabe «Ein Puzzle-Gruss für dich», im Admin änderbar), Gültigkeit heute, bis morgen oder 7 Tage, ohne Download und Passwort; danach QR-Code und Bearbeitungslink (nur damit lässt sich das Puzzle später ändern oder löschen, gespeichert wird nur ein Hash des Schlüssels)
  - Grenzen über `KIOSK_*`: Fotogrösse, Puzzles pro Gerät und Stunde (anonym, Tagessalz, nur im Speicher), Anzahl und Speicher; Startseite zeigt dann nur eine zentrierte Karte mit Logo: «Der Puzzle-Kiosk macht gerade Pause» oder «… ist gerade voll», ohne Kopfzeile und ohne dass die App vorher aufblitzt
  - Startseite für Gäste wie Mockup S1: bis der Datenschutz bestätigt ist, nur eine zentrierte Karte mit Logo, Beispiel-Animation, Hinweisen, Häkchen und «Foto hochladen»; unten Version, Datenschutz und Sprache (auch bei Pause und voll)
  - Aufräumjob: Entwürfe ohne Link nach 24 h, Puzzles mit abgelaufenem Link, verwaiste Ordner und Reste; gelöschte Links zeigen 30 Tage «abgelaufen» (410)
  - Datenschutzseite `/datenschutz` mit Text und Kontaktadresse aus dem Admin
  - Port 8082 Admin mit Login: die normale App plus Box «Kiosk» mit Ein/Aus, Zahlen pro Tag und Aufräumjob («Jetzt aufräumen»), der Schalter auch zuoberst in der Box «Kiosk-Status»; in den Einstellungen ein Block «Kiosk» (Kontaktadresse, Vorgabe Website-Titel, Datenschutztext); «Über» zeigt «Modus: Kiosk» statt der Verbindung zu Immich
- `NEW` Empfängerseite im Kiosk: kein Like und kein ©, Fusszeile «Erstellt mit Puzzle · Mach dein eigenes Puzzle» (Link aus «Erstellt mit» in den Einstellungen)
- `CHG` Empfängerseite: Untertitel entfällt, wenn er gleich lautet wie der Titel
- `FIX` Schriftzug: Meldung «Ungültige Einstellung «textPos»» beim Speichern; eine leere Position gilt als Mitte, alte Werte (oben/Mitte/unten) werden auch bei der Grussbotschaft übernommen
- `FIX` Kiosk-Sicherheit (Prüfung 22.11.2026): Fotos nur als JPEG, PNG oder WEBP bis 40 Megapixel, Grösse geprüft vor dem Dekodieren, höchstens zwei Fotos gleichzeitig in Arbeit
  - Ein neues Foto in einem bestehenden Puzzle zählt wie ein neues Puzzle ins Stundenbudget; neu höchstens `KIOSK_MAX_ACTIVE_PER_IP` Puzzles pro Gerät (Vorgabe 20); IPv6 zählt pro /64
  - Status, Budget und Länge der Anfrage werden geprüft, bevor das Foto gelesen wird; negative oder unlesbare Länge gibt 400, zu grosse 413; Speicherbelegung nur noch einmal pro Minute gezählt
  - Admin: «Notbremse» löscht alle Gäste-Puzzles der letzten Stunden, auch solche mit Link
- `FIX` CSV-Export: Zellen, die mit `=`, `+`, `-`, `@`, Tab oder Zeilenumbruch beginnen, bekommen ein `'` davor (keine Formeln in Excel)
- `FIX` Gäste-Editor und Empfängerseite mit strenger Content-Security-Policy (Skripte nur mit Nonce), `X-Frame-Options: DENY`, `frame-ancestors 'none'` und HSTS hinter https
- `FIX` Compose gehärtet: eigener Benutzer, alles nur lesend ausser `/data`, keine Capabilities, `no-new-privileges`, Grenze für Speicher, CPU-Priorität (`cpu_shares`; harte CPU- und Prozessgrenzen kennt der Synology-Kernel nicht); pip-Pakete mit Hashes (`requirements.txt`)

## 0.1.8 – 2026-11-22
- `NEW` Schritt 1 «Puzzle Details» als eigenes Panel: Name, Für wen / Anlass (steht in der Liste, wird als Schriftzug vorgeschlagen), Vorlage übernehmen (Einstellungen eines bisherigen Puzzles), Interne Notiz (nie auf der Seite der Empfänger)
  - Pro Schritt ein Häkchen «selber einstellen» mit dem Standardwert daneben; Schritte ohne Häkchen stehen im Ablauf grau mit «Standard», zählen als erledigt und lassen sich trotzdem öffnen
  - Der Ablauf zeigt bei Schritt 1 den Namen; «Starte jetzt» auf der Startseite und der Knopf «Neues Puzzle» öffnen «Puzzle Details»
  - Nach der Motivwahl geht es zum ersten Schritt mit Häkchen, ohne Häkchen direkt zum Teilen
  - Die Schrittwahl zeigt den ganzen Ablauf: «Motiv wählen» und «Teilen» mit festem Häkchen (immer dabei), dazu neu «Vorschau» als Schritt (auch im Ablauf)
  - Werkseinstellung: nur «Vorschau» an, Motiv bearbeiten, Musik, Puzzle, Dauer und Tempo und Schriftzug aus
  - «Schrittwahl als Standard speichern»: die Häkchen gelten für alle neuen Puzzles
  - Jede Zeile zeigt die aktuelle Einstellung (Schriftzug mit Text), ohne Häkchen den Standard
  - Vorlage: Die Auswahl bleibt stehen, Schritte, in denen die Vorlage vom Standard abweicht, bekommen ein Häkchen
  - Einstellungen «Standard für neue Puzzles»: die Schritte für «Neues Puzzle» direkt anhaken statt nur aufgelistet
- `NEW` Schnellweg «Motiv wählen und gleich teilen»: nach der Motivwahl wird mit dem Standard gespeichert und ein Link (30 Tage) erstellt
- `NEW` Einstellungen «Standard für neue Puzzles»: Werte und Häkchen, mit denen jedes neue Puzzle startet; «Aktuelles Puzzle als Standard» und «Werkseinstellung»
  - Im Puzzle (Fertig-Panel) «Als Standard übernehmen»; Name und Schriftzug gehören nie zum Standard
  - Jedes Einstellungs-Panel (Motiv bearbeiten, Musik, Puzzle, Dauer und Tempo, Schriftzug) hat unten «Als Standard»: speichert nur die Werte dieses Schritts, der übrige Standard bleibt
- `NEW` Seite der Empfänger: Leiste oben rechts mit Tempo, Ton an/aus und ♥ Like, von Anfang an sichtbar (Ton nur mit Musik)
  - ein Like pro Besucher (Zufalls-Kennung im Browser, gespeichert nur als Hash, keine IP), nochmals tippen nimmt es zurück; die Zahl sehen alle
  - die Zahl steht in den Angaben des Links: «Geteilter Link» (neben «… Mal geöffnet», auch bei 0), «Zugriffe» als dritte Box neben «total geöffnet» und «Tage mit Aufrufen», Liste «Geteilte Puzzles» (ab 1 Like) und Puzzle-Details; nicht im Protokoll und nicht in der Shares-API
- `NEW` Fusszeile der Empfängerseite in zwei Zeilen, immer sichtbar, auch während das Puzzle läuft
  - Erste Zeile «Erstellt mit …»: Text und Link (optional) in den Einstellungen, mit Link anklickbar
  - Zweite Zeile «© Jahr by …» fest: Eintrag `copyright` in `settings.json` auf dem Server, in der App nicht änderbar (die Einstellungen zeigen ihn an)
- `NEW` Panel «Puzzle»: «Motiv transparent anzeigen beim Start» (bisher «Vorlage blass auf dem Tisch») mit Regler für die Deckkraft (5–60 %, Standard 15 %)
  - Unter «Neu mischen» zeigt ein Startbild, wie die Teile am Anfang liegen (verstreut, Haufen, gemischt) und wie stark das Motiv durchscheint
- `CHG` Empfängerseite: Untertitel «Ein Puzzle-Gruss für dich» statt «Ein Puzzle von» mit dem Anzeigenamen des Logins
- `CHG` Startseite: «Starte jetzt dein Puzzle» ist eine Box wie die Schritte 1–3 («Jetzt bist du dran»), ein Klick auf die ganze Box öffnet Schritt 1 «Puzzle Details»
- `CHG` «Nur Einstellungen übernehmen» übernimmt auch die Häkchen und öffnet «Puzzle Details»
- `FIX` Shares-API: Vorschaubild (`preview`) nur noch bei abrufbaren Links; bei abgelaufenen, ausgeschalteten, noch nicht gültigen oder geschützten Links `null`, damit Smart Albums nicht vergeblich lädt (wie Mosaik)
- `CHG` Teilen und Details eines Links: neue Reihenfolge Aktiv, Download, Metadaten, Passwort (optional), Gültig ab, Gültig bis
  - Jeder Link hat ein «Gültig ab» (Standard heute), auch in der Shares-API (`validFrom`)
  - «Gültig bis» als Auswahl (Heute, bis Morgen, 7/30/60/90 Tage, 1 Jahr, unbegrenzt, bis Datum …), darunter das Datum: berechnet ab «Gültig ab», bei «bis Datum …» editierbar, bei «unbegrenzt» nur der Text
- `NEW` Protokoll (Vorlage Smart Albums): neue Box «Protokoll» mit Wann, Wer, Was und Puzzle, neueste zuerst, es bleiben die letzten 2000 Einträge
  - Erfasst: Puzzle erstellt, geändert (automatisches Speichern innert 10 Minuten als ein Eintrag), gelöscht; Link erstellt, geändert (was: Download, Infos, ein/aus, Passwort, Gültig ab, Ablauf, Titel), gelöscht, aufgerufen; Passwort richtig/falsch/gesperrt; Musik hochgeladen/gelöscht; Adresse der Live-Links; An- und Abmelden, fehlgeschlagene Anmeldung
  - Wer: angemeldeter Benutzer, «Smart Albums» bei Änderungen über die Shares-API, «Besucher» bei Aufrufen (ohne IP)
  - Suche über alle Spalten (alle Wörter, ohne Akzente), Filter nach Wer, Export als CSV
- `NEW` Shares-API: ausgeschaltete Links melden `offSince` (seit wann aus), beim Einschalten fällt es weg
- `NEW` Details eines Links: Knopf «Per WhatsApp senden», nur bei aktiven, gültigen Links
- `NEW` Geteilte Puzzles: Häkchen pro Link und oben «Alle Treffer auswählen» (gilt für Filter und Suche, Shift-Klick wählt von–bis); ab einem gewählten Link oben eine Leiste mit «Aktion wählen …», «Löschen» und «Ausführen», vorher Bestätigung mit Anzahl
  - Ersetzt den Knopf «Abgelaufene löschen»: Filter «Abgelaufen», alle oder einzelne wählen, löschen
- `NEW` Zugriffs-Statistik: Link-Details haben «N Mal geöffnet ›», das öffnet «Zugriffe» mit Total, Tagen mit Aufrufen und Aufrufen pro Tag (neueste zuerst), Suche von/bis und «Alle Tage»
  - «Geteilte Puzzles» und die Kachel «Aufrufe» auf der Startseite öffnen dasselbe für alle Links; dort steht die Liste der Links nach Aufrufen im Zeitraum, ein Klick zeigt nur diesen Link
  - Gelöschte Links fallen aus dem Total; Aufrufe vor den Tageswerten stehen als «ohne Datum»
- `CHG` «Geteilte Puzzles»: Zugriffs-Statistik steht zuoberst, darunter ein Suchfeld (Titel, Puzzle-Name, Adresse)
- `FIX` Panels wackeln auf dem iPhone nicht mehr seitlich (u.a. «Zugriffe» mit den Datumsfeldern)
- `CHG` Musik wie im Datei-Explorer: Ordner der Musikquelle stehen über den Stücken (mit Anzahl), ein Klick öffnet den Ordner, zuoberst «‹‹ Zurück» eine Ebene hoch; eigene Musik als Ordner «Eigene Musik»; die Suche findet weiter über alle Ordner
- `CHG` Musik: Liste so hoch wie das Panel statt eines kleinen Kastens, Play-Knopf rechts in der Zeile
  - Das Panel öffnet im Ordner der gewählten Musik
- `FIX` Neu laden bleibt im offenen Panel, Browser vor und zurück gehen durch die Panels statt auf eine leere Seite (offene Panels stehen in der Adresse)
- `FIX` Schritt 3 «Motiv bearbeiten» hat unten wie die übrigen Schritte «‹ Vorheriger», «Nächster ›» und «Fertig»; alle drei übernehmen den Ausschnitt, × verwirft ihn
- `CHG` Musik aus 6 «Dauer und Tempo» öffnet als Unterpanel, «Fertig» führt zurück zu 6
- `CHG` Nach der Motivwahl (Hochladen, aus Immich, Link) geht es direkt weiter zu 3 «Motiv bearbeiten» statt zurück zum Ablauf
- `FIX` Handy: nach «Fertig» steht der Kasten des zuletzt offenen Schritts oben auf der Startseite, statt weiter unten (z. B. «Musik» zuoberst nach «Motiv bearbeiten»)
- `CHG` «Nur Einstellungen übernehmen» führt zu Schritt 1 ins Feld «Name» statt zum Motiv
- `CHG` Schritt 1 zeigt «Zuerst ein Motiv wählen» nicht mehr (nur noch bei 2, solange kein Motiv gewählt ist)
- `CHG` Musik: Knöpfe «Keine Musik» und «Musik hochladen» zuoberst über der Suche; «Keine Musik» steht nicht mehr in der Liste, das Hochlade-Feld unten fällt weg
- `FIX` Empfängerseite Handy: nach dem Drehen (hoch/quer) wird das Puzzle neu eingepasst statt verzogen; der Schluss-Zoom lässt Titel oben und Knöpfe unten frei
- `FIX` «Per WhatsApp senden» öffnet auf dem Handy direkt WhatsApp (ohne leere Seite), am Computer WhatsApp Web in einem neuen Tab
- `CHG` Vorschaubild für WhatsApp und Co.: zeigt das Startbild des Puzzles, also die Teile verstreut, als Haufen oder gemischt und das Motiv so transparent wie eingestellt; der Editor zeichnet es beim Speichern in 1200 × 630 mit
  - Puzzles, die seither nicht neu gespeichert wurden, zeigen das Foto als Puzzle mit Teilen, drei Teile fehlen noch
- `NEW` Knopf «Link teilen» öffnet das Teilen-Menü des Geräts (wo der Browser es kann)
- `NEW` Link: «Gültig ab» Heute, Morgen oder ein Datum; bis dahin zeigt der Link «noch nicht verfügbar», im Panel steht «ab …»
- `NEW` Schriftzug (Panel 7) mit zwei Boxen ›: «Grussbotschaft auf dem leeren Puzzle» und «Grussbotschaft auf dem fertigen Puzzle» (bisherige Einstellungen), beide mit Schrift, Grösse, Bogen, Drehen, Position und Farbe
  - Grussbotschaft auf dem leeren Tisch: steht vor dem Start auf dem leeren Brett und blendet aus, sobald die ersten Teile fliegen; auch im Vorschaubild für WhatsApp und Co.
  - Bogen: Regler von «nach unten» über «gerade» (Mitte) bis «nach oben», jede Zeile als Bogen
  - Drehen: Regler −45° bis 45°, 0 in der Mitte
- `FIX` Empfängerseite: Titel, Untertitel, Knöpfe und Fusszeile liegen nie im Bild (auch quer auf dem Handy), das Brett steht dazwischen
- `CHG` Empfängerseite: «Replay» (DE «Nochmals abspielen») statt «Again»; der Knopf «Ton aus» unten fällt weg, Ton an/aus bleibt oben rechts
- `NEW` Empfängerseite: Bild antippen zeigt nur das Bild auf dem ganzen Bildschirm, nochmals antippen geht zurück (auch Esc)
- `NEW` Startseite: «Starte jetzt dein Puzzle ›» führt direkt zur Motivwahl
- `CHG` 6 «Dauer und Tempo» in der Reihenfolge der Zeitleiste und in Sekunden: «Gesamtdauer», neu «Start nach … s», «Rahmen fertig nach … s», Tempo-Bogen, «Schneller werden ab … s», «Fertiges Bild zeigen»
  - Zeitleiste mit Sekundenskala, die Marke heisst «Rahmen fertig»

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
