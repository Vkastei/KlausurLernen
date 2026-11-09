"""Exam generation, validation, local grading, Claude grading, mastery score.

Token strategy:
- generation returns solutions + rubrics once -> grading never needs the material again
- objective formats (sc, mc, tf, cloze, order, match, num) are graded locally (0 tokens)
- empty answers are graded locally; only non-empty open answers go to Claude
- explanations for objective items come from the generation ("why"), not a second call
"""
import random
import re
import time

from . import claude_cli, extract, store, topics as tp

LOCAL = {"sc", "mc", "tf", "cloze", "order", "match", "num"}
OPEN = {"short", "long", "code", "draw"}
TYPE_LABEL = {
    "sc": "Single Choice", "mc": "Multiple Choice", "tf": "Wahr/Falsch", "cloze": "Lückentext",
    "order": "Reihenfolge", "match": "Zuordnung", "num": "Rechenaufgabe", "short": "Kurzantwort",
    "long": "Freitext/Erörterung", "code": "Programmieren/Code-Analyse", "draw": "Zeichnung/Diagramm",
}
AB_W = {1: 1.0, 2: 1.5, 3: 2.0}

GEN_SYSTEM = r"""Du bist erfahrene/r Hochschulprüfer/in und erstellst realistische Probeklausuren ausschließlich auf Basis des gelieferten Materials. Sprache: Deutsch (Fachbegriffe wie im Material).

Anforderungsbereiche (AB) – jede Stufe prüft Fachwissen aus dem Material:
AB1 Reproduktion (Begriffe, Fakten): gelernte Fachbegriffe, Definitionen, Merkmale, Klassifikationen, Modelle, Formeln, Abläufe im gelernten Zusammenhang wiedergeben oder erkennen. Operatoren: nennen, angeben, definieren, beschreiben, beschriften, zuordnen (Begriff ↔ Definition/Merkmal).
AB2 Reorganisation/Transfer (Begriffe abrufen und anwenden): gelernte Konzepte, Modelle und Verfahren selbständig auswählen, einordnen und auf einen NEUEN, ähnlichen Fall anwenden. Ist das Erkennen des passenden Konzepts Teil der Leistung, nennt die Aufgabe es nicht („Welches … liegt vor? Begründen Sie anhand der Merkmale.“). Operatoren: anwenden, berechnen, bestimmen, einordnen/klassifizieren, mit Fachbegriffen erklären/erläutern, analysieren, vergleichen, darstellen, überführen, implementieren.
AB3 Reflexion/Problemlösung: mehrere Konzepte des Kurses in einer unbekannten, komplexeren Situation verknüpfen; anhand der Kriterien aus dem Material beurteilen/bewerten, entwerfen/entwickeln, beweisen, Fehler in einer gegebenen Lösung finden und korrigieren, Alternativen und Grenzen abwägen.
AB2/AB3 brauchen neue Szenarien/Daten statt bloßer Wiedergabe.

Wissensbindung (wichtigste Qualitätsregel): Prüfe jede Teilaufgabe – könnte jemand ohne diesen Kurs sie mit Allgemeinwissen, gesundem Menschenverstand oder Raten aus der Formulierung lösen? Dann umbauen.
- Gefragt wird nach dem, was das Material lehrt (Begriffe, Definitionen, Modelle, Kriterien, Verfahren, Formeln, Kennzahlen), nicht nach allgemeiner Plausibilität oder Meinung.
- sc/mc/tf/match/cloze: Distraktoren sind echte Fachbegriffe/Konzepte aus dem Material oder typische Verwechslungen, nichts offensichtlich Absurdes; die richtige Option ist nicht an Länge, Absolutwörtern („immer“, „nie“) oder Wortgleichheit mit der Frage erkennbar.
- Offene Aufgaben: Musterlösung und Bewertungsschema verlangen die konkreten Fachbegriffe, Kriterien und Schritte aus dem Material; allgemein plausible Antworten ohne Fachbezug erreichen nur wenige Punkte.
- Szenarien tragen nur den Fall; sie dürfen die Lösung nicht nahelegen.

Wie eine echte Klausur: Hauptaufgaben mit Teilaufgaben, gemeinsamer Kontext (Szenario, Code, Tabelle, Daten) wo sinnvoll, Punkte ≈ Minuten. Formate passend zum Inhalt mischen. Fachlich korrekt, eindeutig, ohne Hilfsmittel lösbar (falls nicht anders vorgegeben).
Falls <stil> mitgeliefert: das sind Aufgaben/Fragen/Lösungen des Dozenten. Aufgabentypen, Operatoren, Formulierung, Umfang, Schwierigkeit und erwartete Antworttiefe übernehmen; ähnliche Aufgaben (neues Szenario, Variation) sind erwünscht, wörtliche Kopien nicht. Inhalte kommen aus den gewählten Themen.

Antworte NUR mit kompaktem JSON (kein Codeblock, kein Text davor/danach):
{"title":str,"min":int,"tasks":[{"title":str,"topic":"T-ID","ctx":md?,"parts":[Part]}]}
Part: {"type":T,"ab":1|2|3,"pts":num,"q":md,"topic"?:T-ID falls abweichend,...typfelder}
sc: "opts":[str],"ans":[i]
mc: "opts":[str],"ans":[i,...] (mind. 1 richtig)
tf: "stmts":[str],"ans":[bool]
cloze: "text":"...{{0}}...{{1}}","ans":[[str,Alternative...],...] (kurze eindeutige Lücken)
order: "items":[str] in KORREKTER Reihenfolge
match: "left":[str],"right":[str] (right[i] gehört zu left[i])
num: "ans":num,"tol":relative Toleranz (0 bei exakten/ganzzahligen Werten, sonst z.B. 0.01),"unit":str? (Endergebnis; Rechenweg wird mitgeschrieben)
short | long | code ("lang":str,"start":Vorlage?) | draw (UML, ER, Graph, Automat, Skizze, Plot...)
sc/mc/tf/cloze/order/match/num zusätzlich "why": knappe Begründung (inkl. warum Distraktoren falsch).
num/short/long/code/draw zusätzlich "sol": Musterlösung (knapp, vollständig; bei draw textuell) und "rub":[[Kriterium,Punkte],...] (Summe=pts), präzise genug zum Korrigieren ohne Material: Kriterien nennen die erwarteten Fachbegriffe/Inhalte, nicht nur „sinnvoll begründet“.
Antwortschlüssel vor der Ausgabe prüfen; "why" enthält keine Selbstkorrekturen.
Markdown erlaubt; Formeln LaTeX $...$/$$...$$ mit escapten Backslashes (\\frac); Code in ```-Blöcken. Optionen nicht in "q" wiederholen."""

GRADE_SYSTEM = r"""Du korrigierst Klausurantworten fair und genau wie ein/e Prüfer/in, auf Basis von Aufgabe, Musterlösung und Bewertungsschema. Gleichwertige andere Lösungswege voll anerkennen; Teilpunkte nach Schema in 0,5er-Schritten. Bilder sind handschriftliche Lösungen/Zeichnungen des Prüflings.
Antworte NUR mit JSON (kein Codeblock):
{"r":[{"id":str,"p":num,"fb":md,"fix":md}],"tips":[str]}
Punkte gibt es für die im Schema verlangten fachlichen Inhalte (Fachbegriffe, Kriterien, Schritte; Synonyme und gleichwertige Wege zählen). Allgemein plausible Aussagen ohne diesen Fachbezug bekommen für das Kriterium keine Punkte.
fb: konkret, was richtig ist und was falsch/fehlend ist und warum; fehlende Fachinhalte beim Namen nennen.
fix: nur bei Punktabzug – wie die korrekte Lösung bzw. der korrekte Weg aussieht; sonst "".
tips: 2-4 konkrete Lernhinweise zu erkannten Lücken.
Formeln LaTeX $...$ mit escapten Backslashes."""

TOPIC_SYSTEM = r"""Du strukturierst Lernmaterial für Prüfungen. Gegeben ist eine Gliederung (Material-ID, Abschnittsnummern, Titel). Gruppiere sie in 5-25 prüfungsrelevante Themen (sinnvoll benannt, nicht zu grob, nicht zu fein).
JEDER Abschnitt gehört zu genau einem Thema – auch Übungen, Aufgaben, Lösungen, Beispiele und Wiederholungen (zum Thema, das sie üben). Nur rein Organisatorisches darf fehlen. Bereiche lückenlos angeben.
Antworte NUR mit JSON: [{"n":"Themenname","c":"m1:0-5,m2:3"}]"""

ASSIGN_SYSTEM = r"""Du ordnest einzelne Abschnitte von Lernmaterial bestehenden Themen zu. Gegeben: <themen> (T-ID, Name, Beispieltitel) und <abschnitte> (Nr|Datei|Titel|Textanfang). Übungen, Aufgaben und Lösungen gehören zu dem Thema, das sie üben. Passt wirklich keines, "-".
Antworte NUR mit JSON: {"a":"1:T3,2:T3,3-7:T1,8:-"}"""

TRANSCRIBE_SYSTEM = r"""Du transkribierst Lernmaterial aus Bildern (Folien, Skripte, Notizen, Aufgabenblätter) als kompakten Markdown-Text in Originalsprache: Lerninhalt vollständig, Formeln LaTeX $...$, Tabellen als Markdown, fachliche Diagramme/Graphen/Modelle strukturiert und knapp (Elemente, Beziehungen, Werte).
Weglassen: Folientitel bei "NUR ABBILDUNG" (ist bekannt), Logos, Kopf-/Fußzeilen, Seitenzahlen, Quellenangaben, dekorative Bilder, Fotos, Stock-Illustrationen und Screenshots von Webseiten/Programmen ohne fachlichen Inhalt (höchstens 1 kurzer Satz, falls für das Verständnis nötig). Keine Einleitung, keine Kommentare.
Bild "VOLLSTÄNDIG": gesamten Lerninhalt transkribieren, erste Zeile "# Titel", falls die Seite einen Titel hat. Bild "NUR ABBILDUNG": der Seitentext ist schon bekannt – nur fachliche Abbildungen (Diagramme, Modelle, Graphen, Tabellen/Code als Bild) beschreiben, Text nicht wiederholen.
Enthält ein Bild nichts Lernrelevantes (mehr), nur "–" ausgeben.
Format: für jedes Bild k eine Zeile ===k=== gefolgt vom Inhalt."""
TRANSCRIBE_BATCH = 6  # images per call: the fixed per-call overhead is shared by more pages


# ======================= generation =======================
# one level per Anforderungsbereich: the quiz concentrates on that AB
QUIZ_LEVELS = {
    1: ("Begriffe lernen (AB I)", [90, 10, 0],
        "Ziel: die Fachbegriffe, Definitionen, Merkmale, Klassifikationen, Modelle, Formeln und Abläufe des Themas sicher "
        "kennen. Jede Teilaufgabe fragt konkrete Begriffe/Fakten aus dem Material ab (z. B. match Begriff ↔ Definition, "
        "cloze mit Fachbegriffen, sc/mc mit anderen Fachbegriffen des Materials als Distraktoren, order für Phasen/Abläufe, "
        "short „nennen/definieren“). Möglichst viele verschiedene Kernbegriffe abdecken; keine Szenario- oder Meinungsfragen"),
    2: ("Wissen anwenden (AB II)", [10, 80, 10],
        "Ziel: gelernte Begriffe abrufen UND anwenden. Kurze neue Fälle, in denen man das passende Konzept/Modell/Verfahren "
        "aus dem Kurs selbst erkennen und benennen (nicht in der Aufgabe vorgeben), zuordnen/klassifizieren, durchführen "
        "bzw. berechnen und mit Fachbegriffen begründen muss. Ohne Kurswissen nicht lösbar; mindestens die Hälfte der "
        "Punkte offen oder num, Bewertungsschema verlangt die Fachbegriffe"),
    3: ("Transfer & Bewerten (AB III)", [0, 30, 70],
        "Ziel: mehrere Konzepte des Themas in einer unbekannten Situation verknüpfen: anhand der Kriterien aus dem Material "
        "beurteilen, entwerfen, Fehler in einer vorgegebenen Lösung finden und korrigieren, Alternativen abwägen; "
        "überwiegend offene Formate, Bewertungsschema verlangt konkrete Fachinhalte"),
}
PASS = 0.75  # share of points a topic needs in a quiz/variation to move on to the next level


def _topic_points(e):
    """{tid: [points, max]} of a graded exam."""
    acc = {}
    for t in e["tasks"]:
        for p in t["parts"]:
            tid = p.get("tid") or t.get("tid")
            if tid:
                r = e["result"]["parts"].get(p["id"], {"p": 0})
                a = acc.setdefault(tid, [0.0, 0.0])
                a[0] += r["p"]
                a[1] += p["pts"]
    return acc


def exam_level(e, by_id):
    """Quiz level of a quiz, or of the quiz a variation (of a variation …) came from."""
    seen = set()
    while e and e["id"] not in seen:
        if e.get("kind") in ("quiz", "drill") and e.get("level"):
            return e["level"]
        seen.add(e["id"])
        e = by_id.get(e.get("parent"))
    return None


def practice(course, exams=None):
    """Learning path per topic, replayed from all graded quizzes and variations (so manual point
    corrections and deleted attempts are always reflected):
    quiz on level L  -> >= 75 %: next level (after level 3: mastered)
                     -> below:   variations of the mistakes, again and again, until one reaches 75 % -> next level."""
    exams = store.list_exams(course["id"]) if exams is None else exams
    by_id = {e["id"]: e for e in exams}
    st = {t["id"]: {"level": 1, "done": False, "drill": None, "last": None, "ts": 0, "open": None}
          for t in course["topics"]}
    done = sorted((e for e in exams if e.get("status") == "graded" and e.get("kind") in ("quiz", "drill")),
                  key=lambda e: e.get("graded") or e.get("created") or 0)
    for e in done:
        L = exam_level(e, by_id)
        if not L:
            continue
        for tid, (g, m) in _topic_points(e).items():
            s = st.get(tid)
            if not s or not m:
                continue
            if L != s["level"]:
                continue  # only the current level moves the path: easier = repetition, harder = extra practice, no skipping
            ok = g / m >= PASS
            s["last"] = {"id": e["id"], "kind": e["kind"], "level": L, "pct": round(100 * g / m), "ok": ok}
            s["ts"] = e.get("graded") or 0
            if ok:
                s["drill"] = None
                if L >= 3:
                    s["level"], s["done"] = 3, True
                else:
                    s["level"] = L + 1
            else:
                s["drill"] = e["id"]  # mistakes of this attempt are the next thing to practise
    # an attempt on the current level that was started but not handed in yet
    for e in sorted(exams, key=lambda e: e.get("created", 0), reverse=True):
        if e.get("status") != "graded" and e.get("kind") in ("quiz", "drill"):
            L = exam_level(e, by_id)
            for tid in {p.get("tid") or t.get("tid") for t in e["tasks"] for p in t["parts"]}:
                s = st.get(tid)
                if s and not s["open"] and L == s["level"] and (e.get("created") or 0) > s["ts"]:
                    s["open"] = {"id": e["id"], "kind": e["kind"], "title": e.get("title", "")}
    return st


def quiz_level(course, tids, prac=None):
    """Level of the learning path; for several topics the lowest one (nothing is skipped)."""
    P = prac or practice(course)
    ls = [P[t]["level"] for t in tids if t in P] or [s["level"] for s in P.values()]
    return max(1, min(3, min(ls) if ls else 1))


STYLE_BUDGET = 4000       # tokens of style references per exam
STYLE_BUDGET_QUIZ = 1500


def build_gen_prompt(course, cfg, settings):
    from .learn import get_note  # lazy: learn imports exam
    quiz = cfg.get("kind") == "quiz"
    chosen = [t for t in course["topics"] if t["id"] in (cfg.get("topics") or [])]
    sel_topics = chosen or list(course["topics"])  # nothing chosen -> whole material, all topics offered
    budget = int(cfg.get("budget") or settings["max_material_tokens"])
    level = None
    if quiz:  # explicit level (topic page "Stufe wählen") or the learning path
        level = int(cfg["level"]) if str(cfg.get("level")) in ("1", "2", "3") else quiz_level(course, [t["id"] for t in chosen])
    notes = [get_note(course["id"], t["id"]) for t in chosen] if quiz and level == 1 else []
    # style references (exercises, old exam questions, solutions): sent ONCE, in their own block
    sty = tp.select_style(course, [t["id"] for t in chosen], STYLE_BUDGET_QUIZ if quiz else STYLE_BUDGET)
    sty_txt = ""
    if sty:
        _, sby, _ = tp.select_material(course, [], 10 ** 9)  # lookup table for all active chunks
        sty_txt = tp.material_text(course, sty, sby)
    if notes and all(notes):
        # beginner quiz on topics with a Lernzettel: quiz the Lernzettel (far fewer tokens than raw material)
        sel, by_id, total = [], {}, 0
        mat = "\n\n".join(f"### Lernzettel: {t['name']}\n{n['md']}" for t, n in zip(chosen, notes))
        total = len(mat)
    else:
        sel, by_id, total = tp.select_material(course, [t["id"] for t in chosen],
                                               min(budget, 15000) if quiz else budget, exclude=set(sty))
        mat = tp.material_text(course, sel, by_id)
    minutes = int(cfg.get("minutes") or (10 if quiz else settings["default_minutes"]))
    ab = QUIZ_LEVELS[level][1] if quiz else (cfg.get("ab") or settings["ab_split"])
    types = [t for t in (cfg.get("types") or list(TYPE_LABEL)) if t in TYPE_LABEL]
    alias = {}
    tlines = []
    for i, t in enumerate(sel_topics, 1):
        alias[f"T{i}"] = t["id"]
        tlines.append(f"T{i} {t['name']}")
    if quiz:
        name, _, hint = QUIZ_LEVELS[level]
        v = [f"Art: Kurz-Quiz zum Üben, Stufe {level} – {name}: {hint}. {minutes} min (≈{minutes} Punkte), "
             f"4-6 kurze Teilaufgaben in 1-3 Aufgaben, Kontext knapp halten. Jede Teilaufgabe prüft einen anderen "
             f"Kernaspekt des Themas; keine Fangfragen zu Nebensächlichkeiten"]
    else:
        v = [f"Dauer: {minutes} min (≈{minutes} Punkte), ca. {max(2, round(minutes / 15))} Hauptaufgaben"]
    v += [f"Punkteverteilung: AB1 {ab[0]} %, AB2 {ab[1]} %, AB3 {ab[2]} %",
          "Formate: " + ", ".join(f"{k} ({TYPE_LABEL[k]})" for k in types)]
    if not sel_topics:
        v.append('Themen: frei aus dem Material, "topic" = kurzer Themenname')
    if any(tp.has_marks(by_id[c][1]) for c in sel):  # only costs tokens if the user marked something
        v.append("Markierungen des Studierenden: Abschnitte „| SEHR WICHTIG“ und Text in ⟦!…⟧ unbedingt prüfen, "
                 "bevorzugt in AB2/AB3 und mit mehr Punkten; „| relevant“ bzw. ⟦…⟧ = prüfungsrelevant, abdecken. "
                 "Markierungszeichen nicht in Aufgaben übernehmen.")
    if course.get("instructions", "").strip():
        v.append("Kursvorgaben: " + course["instructions"].strip())
    if cfg.get("extra", "").strip():
        v.append("Zusätzliche Hinweise: " + cfg["extra"].strip())
    if cfg.get("focus_weak") or (quiz and chosen):  # quizzes always revisit the topic's known gaps
        weak = []
        for t in sel_topics or course["topics"]:
            for w in (course.get("weak", {}).get(t["id"]) or [])[-3:]:
                weak.append(f"- {t['name']}: {w}")
        if weak:
            v.append("Bekannte Schwächen gezielt (in neuer Form) prüfen:\n" + "\n".join(weak[:15]))
    # earlier questions and scenarios on the same topics (newest first) -> new cases instead of repeats
    tids = {t["id"] for t in chosen}
    short = lambda s, n: re.sub(r"\s+", " ", re.sub(r"[#*_`>|$]", "", s or "")).strip()[:n]
    scen, qs = [], []
    for e in sorted(store.list_exams(course["id"]), key=lambda e: e.get("created", 0), reverse=True)[:12]:
        hit = [(t, p) for t in e.get("tasks", []) for p in t["parts"] if not tids or (p.get("tid") or t.get("tid")) in tids]
        for t, p in hit:
            if t.get("ctx") and short(t["ctx"], 90) not in scen:
                scen.append(short(t["ctx"], 90))
            if short(p["q"], 90) not in qs:
                qs.append(short(p["q"], 90))
        if len(qs) >= 25:
            break
    if qs:
        v.append("Bereits gestellt – weder Fragen noch Szenarien wiederholen, neue Fälle/Beispiele/Begriffskombinationen wählen:\n"
                 + "\n".join(f"- Szenario: {s}" for s in scen[:8]) + ("\n" if scen else "")
                 + "\n".join(f"- {q}" for q in qs[:25]))
    # material first (stable prefix -> prompt cache), variable parts after
    text = f"<material>\n{mat}\n</material>\n"
    if sty_txt:
        text += f"<stil>\n{sty_txt}\n</stil>\n"
    if tlines:
        text += "<themen>\n" + "\n".join(tlines) + "\n</themen>\n"
    text += "<vorgaben>\n" + "\n".join(v) + "\n</vorgaben>\n" + \
        ("Erstelle jetzt das Quiz als JSON." if quiz else "Erstelle jetzt die Klausur als JSON.")
    meta = {"chunks": len(sel), "mat_chars": len(mat), "total_chars": total, "alias": alias,
            "est_in": tp.est_tokens(len(text) + len(GEN_SYSTEM)), "est_out": int(minutes * 90),
            "level": level, "level_name": QUIZ_LEVELS[level][0] if level else None,
            "from_notes": bool(notes and all(notes)), "style_chunks": len(sty), "style_chars": len(sty_txt)}
    return text, meta


def generate(course, cfg, settings, progress=None):
    quiz = cfg.get("kind") == "quiz"
    text, meta = build_gen_prompt(course, cfg, settings)
    if meta["mat_chars"] < 50:
        raise ValueError("Kein verwertbares Material für die gewählten Themen (ggf. Bilder erst transkribieren).")
    if quiz:
        out, usage = claude_cli.run(GEN_SYSTEM, text, settings["quiz_model"], "Quiz erstellen",
                                    course["id"], settings.get("quiz_effort"), progress)
    else:
        out, usage = claude_cli.run(GEN_SYSTEM, text, settings["gen_model"], "Klausur erstellen",
                                    course["id"], settings.get("gen_effort"), progress)
    raw = claude_cli.parse_json(out)
    ex = validate(raw, course, meta["alias"])
    if quiz:
        ex["title"] = f"Quiz Stufe {meta['level']} – {meta['level_name']}: " + \
            (", ".join(t["name"] for t in course["topics"] if t["id"] in (cfg.get("topics") or []))[:80] or "alle Themen")
    ex.update({"id": store.new_id("e"), "created": time.time(), "cfg": cfg, "status": "open",
               "kind": "quiz" if quiz else "exam", "level": meta["level"],
               "answers": {}, "usage": {"gen": usage}})
    store.save_exam(course["id"], ex)
    return ex


def _num(x, default=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def validate(raw, course, alias):
    tasks_out = []
    names = {t["name"].lower(): t["id"] for t in course["topics"]}
    for ti, t in enumerate(raw.get("tasks") or [], 1):
        topic = str(t.get("topic") or "")
        tid = alias.get(topic) or names.get(topic.lower())
        tname = next((x["name"] for x in course["topics"] if x["id"] == tid), topic)
        parts = []
        for p in t.get("parts") or []:
            p = _clean_part(p)
            if not p:
                continue
            ptop = str(p.pop("topic", "") or "")
            ptid = alias.get(ptop) or names.get(ptop.lower())
            if ptid and ptid != tid:
                p["tid"] = ptid
                p["topic"] = next(x["name"] for x in course["topics"] if x["id"] == ptid)
            p["id"] = f"{ti}{chr(97 + len(parts))}"
            parts.append(p)
        if parts:
            title = re.sub(r"^\s*Aufgabe\s*\d+\s*[:.)\-–]?\s*", "", str(t.get("title") or "")) or f"Thema {ti}"
            tasks_out.append({"id": str(ti), "title": title,
                              "topic": tname, "tid": tid, "ctx": t.get("ctx") or "", "parts": parts})
    if not tasks_out:
        raise claude_cli.ClaudeError("Generierte Klausur enthielt keine gültigen Aufgaben")
    mx = sum(p["pts"] for t in tasks_out for p in t["parts"])
    return {"title": raw.get("title") or "Probeklausur", "min": int(_num(raw.get("min"), 90)),
            "tasks": tasks_out, "max_pts": mx}


def _clean_part(p):
    ty = p.get("type")
    if ty not in TYPE_LABEL:
        ty = "short"
    p["type"] = ty
    p["ab"] = min(3, max(1, int(_num(p.get("ab"), 2))))
    p["pts"] = max(0.5, round(_num(p.get("pts"), 2) * 2) / 2)
    p["q"] = str(p.get("q") or "")
    try:
        if ty in ("sc", "mc"):
            opts = [str(o) for o in p["opts"]]
            ans = {int(i) for i in (p["ans"] if isinstance(p["ans"], list) else [p["ans"]])}
            perm = list(range(len(opts)))
            random.shuffle(perm)  # model tends to put the right answer first
            p["opts"] = [opts[i] for i in perm]
            p["ans"] = sorted(perm.index(i) for i in ans if i < len(opts))
            if not p["ans"] or len(opts) < 2:
                return None
        elif ty == "tf":
            p["stmts"] = [str(s) for s in p["stmts"]]
            p["ans"] = [bool(a) if not isinstance(a, str) else a.lower() in ("true", "wahr", "w", "1")
                        for a in p["ans"]][:len(p["stmts"])]
            if len(p["ans"]) != len(p["stmts"]):
                return None
        elif ty == "cloze":
            n = len(re.findall(r"\{\{\d+\}\}", p["text"]))
            p["ans"] = [[str(x) for x in (a if isinstance(a, list) else [a])] for a in p["ans"]]
            if n == 0 or len(p["ans"]) < n:
                return None
        elif ty == "order":
            p["items"] = [str(x) for x in p["items"]]
            p["disp"] = _derange(len(p["items"]))
        elif ty == "match":
            p["left"] = [str(x) for x in p["left"]]
            p["right"] = [str(x) for x in p["right"]][:len(p["left"])]
            p["disp"] = _derange(len(p["right"]))
        elif ty == "num":
            p["ans"] = _num(p["ans"])
            p["tol"] = min(0.2, abs(_num(p.get("tol"), 0.005)))
    except (KeyError, TypeError, ValueError, IndexError):
        return None
    return p


def _derange(n):
    perm = list(range(n))
    for _ in range(10):
        random.shuffle(perm)
        if n < 2 or any(i != v for i, v in enumerate(perm)):
            break
    return perm


SECRET = ("ans", "sol", "rub", "why", "tol")


def public_view(ex):
    """Exam without answer keys while it is still being written."""
    if ex.get("status") == "graded":
        return ex
    out = dict(ex)
    out["tasks"] = []
    for t in ex["tasks"]:
        t2 = dict(t)
        t2["parts"] = [{k: v for k, v in p.items() if k not in SECRET} for p in t["parts"]]
        out["tasks"].append(t2)
    return out


# ======================= grading =======================
def _norm(s):
    s = str(s or "").strip().lower()
    s = re.sub(r"[„“”\"'`´]", "", s)
    s = re.sub(r"\s+", " ", s)
    return s.rstrip(".;:!")


def parse_number(s):
    s = str(s or "").replace(" ", "").replace("−", "-")
    s = re.sub(r"[·⋅x×\*]10\^?\(?([-+]?\d+)\)?", r"e\1", s)
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")
    else:
        s = s.replace(",", ".")
    m = re.search(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?", s)
    return float(m.group(0)) if m else None


def _num_ok(val, ans, tol):
    if val is None:
        return False
    if ans == 0:
        return abs(val) <= max(tol, 1e-9)
    return abs(val - ans) <= abs(ans) * tol + 1e-9


def _half(x):
    return round(x * 2) / 2


def grade_local(p, a):
    """Return (points, feedback) or None if Claude is needed."""
    ty, pts = p["type"], p["pts"]
    a = a or {}
    if ty in ("sc", "mc"):
        sel = set(a.get("sel") or [])
        if not sel:
            return 0, "Keine Antwort."
        if ty == "sc":
            return (pts, "Richtig.") if sel == set(p["ans"]) else (0, "Falsche Auswahl.")
        n = len(p["opts"])
        right = sum(1 for i in range(n) if (i in sel) == (i in p["ans"]))
        return _half(pts * max(0, (right - (n - right)) / n)), f"{right} von {n} Optionen korrekt beurteilt."
    if ty == "tf":
        given = a.get("tf") or []
        n = len(p["ans"])
        right = sum(1 for i in range(n) if i < len(given) and given[i] is not None and bool(given[i]) == p["ans"][i])
        wrong = sum(1 for i in range(n) if i < len(given) and given[i] is not None and bool(given[i]) != p["ans"][i])
        return _half(pts * max(0, (right - wrong) / n)), f"{right} von {n} Aussagen richtig."
    if ty == "cloze":
        gaps = a.get("gaps") or []
        n = len(p["ans"])
        ok = 0
        for i, alts in enumerate(p["ans"]):
            g = gaps[i] if i < len(gaps) else ""
            if any(_norm(g) == _norm(x) for x in alts) and _norm(g):
                ok += 1
            else:
                gv, av = parse_number(g), parse_number(alts[0])
                if gv is not None and av is not None and re.fullmatch(r"[-+\d.,eE ]+", str(alts[0]).strip()) \
                        and _num_ok(gv, av, 0.005):
                    ok += 1
        return _half(pts * ok / n), f"{ok} von {n} Lücken richtig."
    if ty == "order":
        seq = a.get("order") or []
        n = len(p["items"])
        if not seq:
            return 0, "Keine Antwort."
        ok = sum(1 for i, v in enumerate(seq[:n]) if v == i)
        return _half(pts * ok / n), f"{ok} von {n} Positionen richtig."
    if ty == "match":
        mp = a.get("map") or []
        n = len(p["left"])
        ok = sum(1 for i in range(n) if i < len(mp) and mp[i] == i)
        return _half(pts * ok / n), f"{ok} von {n} Zuordnungen richtig."
    if ty == "num":
        val = parse_number(a.get("val"))
        if _num_ok(val, p["ans"], p["tol"]):
            return pts, "Ergebnis richtig."
        if (a.get("work") or "").strip() or a.get("img") or a.get("sketch"):
            return None  # partial credit for the working -> Claude
        return 0, "Ergebnis falsch." if val is not None else "Kein Ergebnis angegeben."
    # open types
    has = (a.get("text") or a.get("code") or "").strip() or a.get("img") or a.get("sketch")
    if ty == "code" and (a.get("code") or "").strip() == (p.get("start") or "").strip() and not a.get("img"):
        has = False
    if not has:
        return 0, "Keine Antwort."
    return None


def _answer_text(p, a):
    ty = p["type"]
    if ty == "num":
        return f"Ergebnis: {a.get('val') or '-'} (erwartet {p['ans']:g} {p.get('unit') or ''})\nRechenweg:\n{a.get('work') or '-'}"
    if ty == "code":
        t = f"```{p.get('lang') or ''}\n{a.get('code') or ''}\n```"
        return t + (f"\n{a['text']}" if (a.get("text") or "").strip() else "")
    return (a.get("text") or "").strip() or "(siehe Bild)"


def build_grade_content(course_id, ex, need):
    """need: list of (task, part). Returns content blocks (text + images)."""
    blocks, buf, seen_ctx = [], [], set()
    up = store.upload_dir(course_id, ex["id"])
    for t, p in need:
        a = ex["answers"].get(p["id"]) or {}
        head = []
        if t.get("ctx") and t["id"] not in seen_ctx:
            seen_ctx.add(t["id"])
            head.append(f"Kontext Aufgabe {t['id']}:\n{t['ctx']}")
        rub = "; ".join(f"{c[0]} ({c[1]})" for c in (p.get("rub") or []) if isinstance(c, list) and len(c) >= 2)
        head.append(f"### {p['id']} ({p['pts']:g} P., AB{p['ab']}, {p['type']})\nAufgabe: {p['q']}\n"
                    f"Musterlösung: {p.get('sol') or '-'}\nSchema: {rub or '-'}\nAntwort: {_answer_text(p, a)}")
        buf.append("\n".join(head))
        imgs = list(a.get("img") or [])
        if a.get("sketch_img"):
            imgs.append(a["sketch_img"])
        for name in imgs:
            f = up / name
            if f.exists():
                data, mt = extract.shrink_image(f.read_bytes(), 1200)
                buf.append(f"[Bild zu {p['id']}]")
                blocks.append({"type": "text", "text": "\n\n".join(buf)})
                buf = []
                blocks.append({"type": "image", "source": {"type": "base64", "media_type": mt,
                                                            "data": extract.b64(data)}})
    buf.append("Korrigiere alle Teilaufgaben.")
    blocks.append({"type": "text", "text": "\n\n".join(buf)})
    return blocks


def grade(course, ex, settings, progress=None):
    res, need = {}, []
    for t in ex["tasks"]:
        for p in t["parts"]:
            a = ex["answers"].get(p["id"]) or {}
            r = grade_local(p, a)
            if r is None:
                need.append((t, p))
            else:
                res[p["id"]] = {"p": r[0], "fb": r[1], "fix": "", "src": "local"}
    tips = []
    if need:
        content = build_grade_content(course["id"], ex, need)
        out, usage = claude_cli.run(GRADE_SYSTEM, content, settings["grade_model"], "Korrektur",
                                    course["id"], settings.get("grade_effort"), progress)
        ex["usage"]["grade"] = usage
        data = claude_cli.parse_json(out)
        rows = data.get("r", []) if isinstance(data, dict) else data
        tips = data.get("tips", []) if isinstance(data, dict) else []
        got = {str(r.get("id")): r for r in rows if isinstance(r, dict)}
        for t, p in need:
            r = got.get(p["id"])
            if r is None:
                res[p["id"]] = {"p": 0, "fb": "Keine Bewertung erhalten – bitte erneut korrigieren.", "fix": "", "src": "missing"}
            else:
                res[p["id"]] = {"p": min(p["pts"], max(0, _half(_num(r.get("p"))))), "fb": r.get("fb") or "",
                                "fix": r.get("fix") or "", "src": "claude"}
    ex["result"] = {"parts": res, "tips": tips}
    summarize(ex)
    ex["status"] = "graded"
    ex["graded"] = time.time()
    store.save_exam(course["id"], ex)
    _update_weak(course, ex)
    return ex


def summarize(ex):
    res = ex["result"]["parts"]
    tot = mx = 0
    by_ab, by_topic = {}, {}
    for t in ex["tasks"]:
        for p in t["parts"]:
            r = res.get(p["id"], {"p": 0})
            pts = r["p"]
            tot += pts
            mx += p["pts"]
            for d, k in ((by_ab, str(p["ab"])), (by_topic, p.get("topic") or t["topic"] or "–")):
                d.setdefault(k, [0, 0])
                d[k][0] += pts
                d[k][1] += p["pts"]
    pct = 100 * tot / mx if mx else 0
    ex["result"].update({"total": tot, "max": mx, "pct": round(pct, 1), "grade": german_grade(pct),
                         "by_ab": by_ab, "by_topic": by_topic})


def german_grade(pct):
    for lim, g in ((95, "1,0"), (90, "1,3"), (85, "1,7"), (80, "2,0"), (75, "2,3"), (70, "2,7"),
                   (65, "3,0"), (60, "3,3"), (55, "3,7"), (50, "4,0")):
        if pct >= lim:
            return g
    return "5,0"


def override(course, ex, pid, points):
    for t in ex["tasks"]:
        for p in t["parts"]:
            if p["id"] == pid:
                r = ex["result"]["parts"].setdefault(pid, {"fb": "", "fix": ""})
                r["p"] = min(p["pts"], max(0, _half(_num(points))))
                r["manual"] = True
    summarize(ex)
    store.save_exam(course["id"], ex)
    return ex


def _update_weak(course, ex):
    c = store.get_course(course["id"])
    weak = c.setdefault("weak", {})
    for t in ex["tasks"]:
        for p in t["parts"]:
            tid = p.get("tid") or t.get("tid")
            r = ex["result"]["parts"].get(p["id"])
            if tid and r and r["p"] < 0.6 * p["pts"]:
                src = r.get("fix") or r.get("fb") if r.get("src") == "claude" else f'{p["q"][:70]} – {p.get("why") or ""}'
                note = re.sub(r"\s+", " ", src or p["q"])[:160]
                lst = weak.setdefault(tid, [])
                lst.append(f"[AB{p['ab']}] {note}")
                del lst[:-6]
    store.save_course(c)


# ======================= mastery =======================
KIND_WEIGHT = {"exam": 1.0, "quiz": 0.7, "drill": 0.5}  # practice counts, but less than full exams


def mastery(course):
    """Topic mastery from graded exams, recency-weighted per AB level.
    100 % = every topic solved fully in AB2 and AB3 (AB1 inferred from higher levels if untested)."""
    exams = sorted([e for e in store.list_exams(course["id"]) if e.get("status") == "graded"],
                   key=lambda e: e.get("graded", 0), reverse=True)
    hist = {}  # (tid, ab) -> [(fraction, kind weight) newest first]
    for e in exams:
        kw = KIND_WEIGHT.get(e.get("kind", "exam"), 1.0)
        acc = {}
        for t in e["tasks"]:
            for p in t["parts"]:
                tid = p.get("tid") or t.get("tid")
                if not tid:
                    continue
                r = e["result"]["parts"].get(p["id"], {"p": 0})
                k = (tid, p["ab"])
                acc.setdefault(k, [0, 0])
                acc[k][0] += r["p"]
                acc[k][1] += p["pts"]
        for k, (g, m) in acc.items():
            if m:
                hist.setdefault(k, []).append((g / m, kw))
    out = []
    for t in course["topics"]:
        lv = {}
        for ab in (1, 2, 3):
            h = hist.get((t["id"], ab))
            if h:
                w = [0.5 ** i * kw for i, (_, kw) in enumerate(h)]
                lv[ab] = sum(f * x for (f, _), x in zip(h, w)) / sum(w)
        tested = bool(lv)
        l1 = lv.get(1, max(lv.get(2, 0), lv.get(3, 0)))
        score = (0.2 * l1 + 0.4 * lv.get(2, 0) + 0.4 * lv.get(3, 0)) if tested else 0
        out.append({"id": t["id"], "name": t["name"], "score": round(100 * score, 1), "tested": tested,
                    "ab": {k: round(100 * v) for k, v in lv.items()}})
    overall = round(sum(x["score"] for x in out) / len(out), 1) if out else 0
    return {"overall": overall, "topics": out, "tested": sum(x["tested"] for x in out), "exams": len(exams)}


# ======================= topics + transcription via Claude =======================
def _assign_lines(course, cids):
    by = {cid: (m, c) for cid, m, c in tp.all_chunks(course)}
    lines = []
    for n, cid in enumerate(cids, 1):
        m, c = by[cid]
        snip = re.sub(r"\s+", " ", re.sub(r"[#*_`>|]", " ", c.get("x", ""))).strip()[:150]
        title = re.sub(r"^(Folie|Seite)\s*\d+\s*:\s*", "", c.get("t", ""))[:70]
        lines.append(f"{n}|{m['name'][:40]}|{title}|{snip}")
    return lines


def assign_estimate(course, cids):
    if not cids:
        return 0
    chars = sum(len(l) for l in _assign_lines(course, cids)) + 60 * len(course["topics"]) + len(ASSIGN_SYSTEM)
    return tp.est_tokens(chars) + claude_cli.CLI_OVERHEAD


def assign_topics_ai(course, cids, settings):
    """AI fallback for pages the local assignment could not place. Sends only titles + snippets of
    those pages and the topic names with a few example titles. Returns {cid: topic_id}."""
    if not cids or not course["topics"]:
        return {}
    by = {cid: c for cid, _, c in tp.all_chunks(course)}
    tl, alias = [], {}
    for k, t in enumerate(course["topics"], 1):
        alias[f"T{k}"] = t["id"]
        ex = list(dict.fromkeys(re.sub(r"^(Folie|Seite)\s*\d+\s*:\s*", "", by[x].get("t", ""))[:40]
                                for x in t["chunks"] if x in by))[:4]
        tl.append(f"T{k} {t['name']}" + (f" (z. B. {'; '.join(ex)})" if ex else ""))
    out = {}
    for start in range(0, len(cids), 200):
        batch = cids[start:start + 200]
        text = "<themen>\n" + "\n".join(tl) + "\n</themen>\n<abschnitte>\n" + "\n".join(_assign_lines(course, batch)) + "\n</abschnitte>"
        res, _ = claude_cli.run(ASSIGN_SYSTEM, text, settings["aux_model"], "Themen zuordnen",
                                course["id"], settings.get("aux_effort"))
        data = claude_cli.parse_json(res)
        for part in re.split(r"[,;\s]+", str(data.get("a", "") if isinstance(data, dict) else "")):
            mt = re.fullmatch(r"(\d+)(?:-(\d+))?:(T\d+|-)", part.strip())
            if not mt or mt.group(3) == "-" or mt.group(3) not in alias:
                continue
            a, b = int(mt.group(1)), int(mt.group(2) or mt.group(1))
            for n in range(a, min(b, len(batch)) + 1):
                if n >= 1:
                    out[batch[n - 1]] = alias[mt.group(3)]
    return out


def goals_text(course, limit=6000):
    """Text of all active learning-goal pages (role "goals"), in document order."""
    out = []
    for cid, m, c in tp.all_chunks(course):
        if c.get("role") == "goals" and c.get("x"):
            out.append(f"[{m['name']} · {c.get('t', '')}]\n{c['x']}")
    return "\n\n".join(out)[:limit]


def suggest_topics(course, settings, from_goals=False):
    text, alias = tp.outline(course)
    goals = goals_text(course) if from_goals else ""
    if goals:  # the lecturer's learning goals define the exam scope -> topics follow them
        text += ("\n\n<lernziele>\n" + goals + "\n</lernziele>\nLeite die Themen aus diesen Lernzielen ab: ein Thema je "
                 "inhaltlichem Lernziel bzw. eng verwandter Gruppe (organisatorische Ziele weglassen), kurzer prägnanter "
                 "Name, und ordne die passenden Abschnitte der Gliederung zu.")
    if course["topics"]:  # keep names stable -> ids and learning progress survive the refresh
        text += ("\n\nBestehende Themen – Namen exakt übernehmen, wenn sie weiter passen; "
                 "neue Abschnitte zuordnen, nur bei Bedarf neue Themen ergänzen:\n"
                 + "; ".join(t["name"] for t in course["topics"]))
    out, _ = claude_cli.run(TOPIC_SYSTEM, text, settings["aux_model"], "Themen vorschlagen",
                            course["id"], settings.get("aux_effort"))
    data = claude_cli.parse_json(out)
    res = []
    for d in data if isinstance(data, list) else data.get("topics", []):
        name = str(d.get("n") or d.get("name") or "").strip()
        ch = tp.parse_ranges(str(d.get("c") or ""), alias)
        if name:
            res.append({"name": name[:100], "chunks": ch})
    return res


def transcribe(course, mid, settings, all_figs=False, progress=None, pages=None, regions=None):
    """Turn image-only pages/pictures into text once; cached in the chunk file.
    pages: explicit chunk indices chosen by the user in the page picker (otherwise all candidates).
    regions: {chunk index: ([[x0, y0, x1, y1] normalized], covers all open figures)} -> on PDF pages with
    text only these figures are cut out and described; the page counts as transcribed once all are done."""
    regions = regions or {}
    cid = course["id"]
    m = next(x for x in course["materials"] if x["id"] == mid)
    data = store.get_chunks(cid, mid)
    src = store.material_path(cid, mid, m["ext"])
    img_dir = store.material_path(cid, mid + "_img")
    if pages is not None:
        want = set(pages)
        todo = [i for i, c in enumerate(data["chunks"])
                if i in want and (c.get("v") or c.get("vi") or c.get("img") or data["kind"] == "pdf")
                and not c.get("tr") and c.get("st") != "x"]
    else:
        todo = [i for i, c in enumerate(data["chunks"])
                if (c.get("v") or (all_figs and c.get("vi"))) and not c.get("tr") and c.get("st") != "x"]
    done = 0
    # figure-only mode: pages that already have usable text keep it; Claude only describes the figure
    fig_only = lambda c: len(c.get("x", "").strip()) >= 60  # noqa: E731
    for start in range(0, len(todo), TRANSCRIBE_BATCH):
        batch = todo[start:start + TRANSCRIBE_BATCH]
        blocks = []
        for k, i in enumerate(batch, 1):
            c = data["chunks"][i]
            crop = data["kind"] == "pdf" and fig_only(c) and regions.get(i)
            if fig_only(c):
                ctx = re.sub(r"\s+", " ", c["x"])[:300]
                what = f"NUR ABBILDUNG{'EN (Ausschnitte der Seite)' if crop and len(crop[0]) > 1 else ' (Ausschnitt der Seite)' if crop else ''}"
                blocks.append({"type": "text", "text": f"Bild {k} – {what} (Seitentext ist bereits bekannt: {ctx} …)"})
            else:
                blocks.append({"type": "text", "text": f"Bild {k} – VOLLSTÄNDIG"})
            if crop:
                imgs = [extract.render_pdf_page(src, c["pg"], clip=r) for r in crop[0][:4]]
            elif data["kind"] == "pdf":
                img, mt = extract.render_pdf_page(src, c["pg"])
                imgs = [(img, mt)]
            else:
                imgs = [extract.shrink_image((img_dir / n).read_bytes()) for n in c.get("img", [])[:2]]
            for img, mt in imgs:
                blocks.append({"type": "image", "source": {"type": "base64", "media_type": mt, "data": extract.b64(img)}})
        out, _ = claude_cli.run(TRANSCRIBE_SYSTEM, blocks, settings["aux_model"], "Bilder transkribieren",
                                cid, settings.get("aux_effort"))
        parts = re.split(r"^===\s*(\d+)\s*===\s*$", out, flags=re.M)
        got = {int(parts[j]): parts[j + 1].strip() for j in range(1, len(parts) - 1, 2)}
        if not got and len(batch) == 1:
            got = {1: out.strip()}
        for k, i in enumerate(batch, 1):
            c = data["chunks"][i]
            t = got.get(k, "")
            if not t:
                continue
            crop = data["kind"] == "pdf" and fig_only(c) and regions.get(i)
            if crop:  # only some figures: remember which, the page is done once all are described
                c["trr"] = c.get("trr", []) + crop[0]
            if re.fullmatch(r"[-–—\s]*", t):  # nothing learning-relevant on this page -> keep existing text
                if not crop or crop[1]:
                    c["tr"] = True
                    c.pop("v", None)
                continue
            c.setdefault("x0", c.get("x", ""))  # backup of the locally extracted text (for "reset")
            if crop:
                c["x"] = c["x"].rstrip() + "\n[Abbildung] " + t
                if crop[1]:
                    c["tr"] = True
                continue
            if fig_only(c):
                c["x"] = c["x"].rstrip() + "\n[Abbildung] " + t
            else:
                hm = re.match(r"\s*#{1,3}\s+(.+)\n?", t)
                if hm and re.fullmatch(r"(Seite|Folie|Bild|Abschnitt) \d+|", c.get("t", "").strip()):
                    c.setdefault("t0", c.get("t", ""))
                    c["t"] = hm.group(1).strip()[:120]  # real title instead of "Seite 12"
                    t = t[hm.end():].strip()
                c["x"] = t
            c["tr"] = True
            c.pop("v", None)
        done += len(batch)
        store.save_chunks(cid, mid, data)
        if progress:
            progress(done, len(todo))
    # the pages now have text -> re-run the local role analysis (e.g. a scanned "Organisatorisches" page)
    from . import analyze
    analyze.analyze(data, m["name"])
    store.save_chunks(cid, mid, data)
    return data
