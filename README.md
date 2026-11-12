# KlausurLernen

## KOMPLETT GEVIBECODED!

Lokales Tool: Lernmaterial reinziehen → Themen wählen → Claude (CLI) erstellt eine realistische Probeklausur (Schwerpunkt AB II/III) → im Tool bearbeiten → korrigieren lassen → Verständnis-Score bis 100 %.

## Start

`start.bat` doppelklicken (installiert beim ersten Mal `pymupdf`, `python-pptx`, `python-docx`) → öffnet http://127.0.0.1:8765.
Alternativ: `python -m app.server [port] [--no-browser]`.
Voraussetzung: Python 3.10+, Claude Code CLI (`claude`) installiert und angemeldet.

## Ablauf

**▶ Lern-Coach** (ganz oben im Kurs, kompakt auch auf Themen- und Ergebnisseiten): ein Knopf für den jeweils nächsten Schritt. Wer nur diesen Knopf drückt, geht das gesamte Material bis 100 % durch. Der Coach entscheidet lokal (0 Tokens) aus allen bisherigen Versuchen, in dieser Reihenfolge:
1. Angefangenen Versuch abschließen (oder „verwerfen“).
2. Fehler üben: offene Variationen auf dem Lernpfad (Pflicht bis 75 %), danach einmal die Fehler der letzten Klausur.
3. Nach je 3 abgeschlossenen Themen eine **Zwischenklausur** (≈ 15 min pro Thema), wenn alle Themen geschafft sind eine **Abschlussklausur**.
4. **Alte Klausur wiederholen**: unter 90 % und mindestens 3 Tage her, dieselben Aufgaben mit Abstand.
5. **Aktuelles Thema** (strikt der Reihe nach): Lernzettel lesen (wird bei Bedarf erstellt), dann Quiz der Lernpfad-Stufe 1 → 2 → 3.
6. **Festigen**: Sind alle Themen durch, wird die jeweils schwächste Stelle (Thema × AB) geübt, bis jedes Thema 100 % hat.

Dazu zeigt der Coach bis zu zwei Alternativen (z. B. Probeklausur über alles bisher Gelernte), die Token-Schätzung pro Knopf, eine Fortschrittsleiste über alle Themen (anklickbar) und Warnungen (Themen ohne Material, nicht transkribierte Seiten).

1. **Kurs anlegen**, Dateien/Ordner in die Ablage ziehen (PDF, PPTX, DOCX, Bilder, Code, IPYNB, MD/TXT/TEX …).
   Jede Datei zeigt eine Schrittleiste **① Hochgeladen → ② Bereinigen → ③ Bilder → ④ Themen** und genau einen Knopf „Nächster Schritt“. Diese Reihenfolge spart Tokens: erst unwichtige Seiten ausschließen, dann nur die übrigen Bildseiten transkribieren, dann Themen zuordnen. Wer einen Schritt überspringt, bekommt einen Hinweis.
   **Bilder auswählen**: Vor jeder Transkription öffnet sich eine Seitenauswahl mit Vorschaubildern (Klick, Umschalt+Klick für Bereiche, mit der Maus ziehen für mehrere, „Alle / Keine / Umkehren / Nur Lerninhalte“, Token-Schätzung live). Nicht ausgewählte reine Bildseiten können direkt ausgeschlossen werden; nicht ausgewählte Abbildungen werden als „keine Transkription nötig“ gemerkt.
   Seiten, die nur aus Bildern bestehen, werden markiert → „transkribieren“ (einmalig, danach gecacht).
   **💡 Vorschläge** (lokal, 0 Tokens): Beim Hochladen bekommt jede Seite eine Rolle (Lernziele, Übung, Lösung, Prüfungsinfo, Organisatorisches, Literatur, Danke-/Trennfolie, Umfrage, Duplikat, Animationsschritt). Daraus entstehen Vorschläge: Rauschen ausschließen, Lernziele als „sehr wichtig“ markieren, Prüfungsinfos in die Kursvorgaben übernehmen. Nichts passiert ohne Bestätigung. Logos und Vorlagen-Grafiken zählen nicht mehr als Abbildung. „Transkription zurücksetzen“ stellt den Originaltext kostenlos wieder her.
   Erkannt wird auch, was der Dozent selbst als **nicht klausurrelevant** kennzeichnet: ein Symbol (wenn das Material diese Konvention erklärt), Bereichsangaben wie „Folien 414–431“ und Textnotizen.
   **✎ Stilvorlagen**: Übungen, Altklausur-/Quizfragen und Lösungen (per Seite, Bereich oder ganze Datei; Dateinamen wie „Altklausur…“, „Übungsblatt…“, „…Lösung“ werden erkannt) bleiben normales Lernmaterial. Bei der Klausurerstellung werden sie einmal in einem eigenen Stil-Block gesendet (max. ~4k Tokens), damit Aufgabentyp, Formulierung und Niveau dem Dozenten entsprechen.
   **Abbildungen**: Seiten mit Text werden im Abbildungsmodus transkribiert. Der Text bleibt, nur die fachliche Abbildung wird beschrieben und angehängt; der Originaltext wird vorher gesichert.
   **🔎 KI-Vorprüfung** (Sonnet, nur Text, ab 50 Seiten automatisch angeboten): verfeinert die Rollen, erkennt ausdrückliche Prüfungshinweise („genau so lernen“) und fasst Prüfungsinfos als Kursvorgabe zusammen. Bereits geprüfte und ausgeschlossene Seiten werden nicht erneut gesendet.
   **Vorschau & markieren** (optional, umkehrbar, Originaldatei bleibt unverändert):
   - Seiten, Folien oder Abschnitte *ausschließen* (einzeln oder per Bereich wie `1-3, 40-45`). Sie werden nie gesendet, nicht transkribiert und nicht für Themen genutzt.
   - Ganze Seiten als *relevant* oder *sehr wichtig* kennzeichnen.
   - Text mit der Maus markieren: *sehr wichtig* / *relevant* / *nicht relevant*. Nicht Relevantes wird vor dem Senden entfernt; Wichtiges wird markiert und bevorzugt geprüft, eher in AB II/III.
   - Wird das Material-Budget knapp, kommen markierte Inhalte zuerst. Ohne Markierungen bleibt der Prompt unverändert (0 Extra-Tokens).
2. **Themen**: „Aus Gliederung“ (lokal, 0 Tokens), „Mit Claude gruppieren“ (es werden nur die Titel gesendet) oder „Aus Lernzielen“ (sobald das Material Lernziel-Seiten enthält; Themen entsprechen dann dem Prüfungsumfang). Eigene Themen kann man ergänzen; der Lernfortschritt bleibt bei jeder Aktualisierung erhalten.
   **Thema lernen** (Themenname anklicken):
   - *Lernzettel*: einmal aus dem Material des Themas erstellt und gespeichert – Überblick, Kernbegriffe, Schritt für Schritt, durchgerechnetes Beispiel, typische Fehler, typische Klausuraufgaben je AB, Selbsttest mit aufdeckbaren Antworten.
   - *Nachfragen*: senden nur den Lernzettel und die letzten Fragen (Material optional).
   - *Lernpfad* mit Kurz-Quizzen (5–15 min): Stufe 1 Begriffe lernen (AB I: Fachbegriffe, Definitionen, Merkmale, Abläufe) → Stufe 2 Wissen anwenden (AB II: das passende Konzept in einem neuen Fall selbst erkennen, zuordnen, anwenden, mit Fachbegriffen begründen) → Stufe 3 Transfer & Bewerten (AB III: Konzepte verknüpfen, anhand der Kurskriterien beurteilen, entwerfen, Fehler finden). Alle Aufgaben sind an das Kurswissen gebunden: Was mit gesundem Menschenverstand lösbar wäre, wird umgebaut. Distraktoren sind echte Fachbegriffe, und Bewertungsschema und Korrektur verlangen die Fachinhalte. Frühere Fragen und Szenarien zum selben Thema werden nicht wiederholt. Quiz der aktuellen Stufe ≥ 75 % → nächste Stufe. Darunter werden die Fehler variiert, so oft bis eine Variation ≥ 75 % erreicht → nächste Stufe. Keine Stufe wird übersprungen. Nur Versuche auf der aktuellen Stufe zählen; andere Stufen lassen sich unter „Andere Stufe üben“ zusätzlich trainieren. Nach jeder Korrektur zeigt die Klausurseite den nächsten Schritt. Der Pfad wird aus allen korrigierten Versuchen berechnet, Punktkorrekturen wirken also sofort. Das Kurz-Quiz im Kurs nimmt bei mehreren Themen die niedrigste Stufe. Auf Stufe 1 wird der Lernzettel statt des Materials genutzt (günstiger).
   - *↻ Nochmal, anders*: bei jeder nicht voll gelösten Aufgabe und als „Alle Fehler variieren“. Claude diagnostiziert den Denkfehler aus Antwort und Bewertung und baut eine neue Situation zum selben Konzept, in der derselbe Fehler wieder auffallen würde. Die Aufgabe verrät nichts; die Erklärung beginnt nach der Abgabe mit „Typischer Fehler: …“. Mitgesendet werden nur die Aufgaben und die passendsten Materialausschnitte (≈ 2,5k Tokens), damit die Variation im Stoff bleibt.
   - *Material*: alle zugeordneten Folien/Seiten als Vorschaubilder (Text-Abschnitte als Textkacheln). Anklicken = groß anzeigen, mit ← → blättern, „Original ↗“ öffnet das Bild in voller Größe.
     Ein Textmarker direkt auf der Folie zeigt, was die KI davon kennt: **gelb** = Textzeile wird gesendet, gestrichelt unterstrichen = nicht genutzt (Kopf-/Fußzeile, als „nicht relevant“ markiert), **grün** = Abbildung transkribiert, **rot** = Abbildung fehlt der KI. Logos und Vorlagen-Grafiken werden nicht markiert. Positionen kommen lokal aus dem PDF (einmal berechnet, gecacht); Folien/Bilder werden als Ganzes markiert. „Was die KI sieht“ in der Großansicht zeigt den erfassten Text.
     In der Großansicht rote Abbildungen anklicken = vormerken (blau); Kästchen auf der Kachel, <kbd>M</kbd> oder „Alle offenen vormerken“ = alle Abbildungen der Seite → „Transkribieren“ bzw. „Transkribieren + Lernzettel erstellen“ (ein Durchgang). Bei Seiten mit Text wird nur der Ausschnitt der gewählten Abbildung gesendet (≈ halb so viele Tokens wie die ganze Seite), die Beschreibung wird als „[Abbildung] …“ angehängt. Reine Bildseiten direkt neben dem Thema, die noch keinem Thema zugeordnet sind (gestrichelt), werden mit angezeigt und nach dem Transkribieren diesem Thema zugeordnet.
   - Quiz (×0,7) und Variationen (×0,5) zählen schwächer zum Verständnis-Score als Klausuren.
   - Empfohlener Ablauf bei neuem Stoff: Lernzettel lesen → Selbsttest → Kurz-Quiz → Fehler variieren → Tage später nächstes Quiz → am Ende Probeklausuren.
3. **Kursvorgaben** (gelten für jede Klausur) und **Hinweise** (nur für diese Klausur) eintragen; Dauer, AB-Verteilung und Formate wählen.
4. **Klausur schreiben**: Single/Multiple Choice, Wahr/Falsch, Lückentext, Reihenfolge, Zuordnung, Rechenaufgabe, Kurz-/Freitext, Code, Zeichnung (Canvas). Bei jeder offenen Aufgabe können Fotos handschriftlicher Lösungen angehängt werden. Antworten werden automatisch gespeichert.
5. **Abgeben**: Objektive Formate werden lokal korrigiert, offene von Claude: Punkte, Begründung und „So wäre es richtig“. Falsch automatisch bewertete Antworten lassen sich manuell anpassen.
6. **Verständnis**: Pro Thema und AB-Stufe, neuere Klausuren zählen mehr. 100 % = jedes Thema zuletzt in AB I–III vollständig gelöst. „Schwächste 5“ und „Schwächen gezielt prüfen“ zielen auf Lücken.

## Token-Sparmaßnahmen

| Maßnahme | Effekt |
|---|---|
| Eigener kurzer System-Prompt, `--tools ""`, keine MCP-Server/Skills/Einstellungen | ~130 statt ~5.600 Tokens Grundlast pro Aufruf |
| Kein `--json-schema` (kostet ~2k Tokens + zweite Runde); JSON wird lokal geparst und repariert | keine Extra-Runde mit dem gesamten Material |
| Text-Extraktion, Kopf-/Fußzeilen- und Seitenzahl-Entfernung lokal | nur der eigentliche Inhalt wird gesendet |
| Material wird nach Thema ausgewählt und durch ein Budget begrenzt (Einstellungen) | gezielter Kontext statt aller Dateien |
| Material steht vorne und deterministisch geordnet im Prompt | Prompt-Cache-Treffer bei Wiederholungen |
| Generierung liefert Musterlösung und Bewertungsschema mit | Korrektur braucht das Material nicht noch einmal |
| MC/WF/Lücke/Reihenfolge/Zuordnung/Zahl lokal korrigiert; leere Antworten = 0 lokal | nur offene Antworten gehen an Claude |
| Bilder lokal verkleinert (≤ 1.400 px), Transkription einmalig gecacht | weniger Vision-Tokens |
| Opus 5.5 nur für die Erstellung, Sonnet 5.5 für Korrektur und Hilfsaufgaben | konfigurierbar unter Einstellungen |

Der Reiter **Tokens** zeigt den Verbrauch pro Aufruf, Aktion, Modell und Tag (Quelle: Usage-Angaben der Claude CLI).

## Struktur

```
app/server.py      HTTP-Server (stdlib) + Hintergrund-Jobs
app/claude_cli.py  CLI-Wrapper (stream-json in/out, Usage-Log), JSON-Reparatur
app/extract.py     PDF/PPTX/DOCX/IPYNB/Code/Text/Bild → Abschnitte
app/topics.py      Gliederung, BM25-Zuordnung, Materialauswahl nach Budget
app/exam.py        Prompts, Generierung, Validierung, Korrektur, Verständnis-Score
app/coach.py       Lern-Coach: nächster Schritt aus allen Versuchen (lokal)
app/learn.py       Lernzettel, Nachfragen, Variationen
static/            UI (Vanilla JS), Zeichen-Canvas, Markdown+KaTeX offline
data/              alle Kurse, Klausuren, Uploads, usage.jsonl (lokal, nicht im Git)
```
