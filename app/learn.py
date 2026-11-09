"""Learning features: Lernzettel per topic (+ follow-up questions) and variations of failed tasks.

Token strategy:
- Lernzettel: topic material is sent ONCE, the result is cached; follow-up questions only send the
  Lernzettel (+ the last Q&A), material only on request
- variations: only the original task, its solution, the student's answer and the feedback are sent
"""
import hashlib
import json
import re
import time

from . import claude_cli, exam, store, topics as tp

NOTE_BUDGET = 25000  # max material tokens for one Lernzettel

NOTE_SYSTEM = r"""Du bist Tutor/in an einer Hochschule und erklärst ein Prüfungsthema für Studierende ohne Vorwissen – ausschließlich auf Basis des Materials (Begriffe und Notation wie dort). Ziel: verstehen, nicht auswendig lernen.
Antworte nur mit Markdown (Deutsch, Fachbegriffe wie im Material), kompakt (ca. 700-1300 Wörter), mit genau diesen Abschnitten:
## Worum geht es?
2-4 Sätze: welches Problem, welche Idee, wozu.
## Kernbegriffe
knappe Definitionen als Liste.
## So funktioniert es
Schritt für Schritt, Zusammenhänge, ggf. Tabelle.
## Durchgerechnetes Beispiel
vollständig, jeder Schritt begründet (bei Theorie: ausgearbeitete Argumentation).
## Typische Fehler
Verwechslungen und Fallstricke.
## Was in Klausuren gefragt wird
je AB1, AB2, AB3 ein typischer Aufgabentyp mit Operator.
## Selbsttest
4 kurze Fragen als Liste, Antwort jeweils direkt dahinter in ||...||.
Mit ⟦!...⟧ oder "SEHR WICHTIG" markierte Inhalte besonders betonen. Formeln LaTeX $...$, Code in ```-Blöcken."""

ASK_SYSTEM = r"""Du bist Tutor/in an einer Hochschule. Beantworte die Frage knapp, verständlich und fachlich korrekt auf Basis des Lernzettels (und des Materials, falls mitgeliefert). Nutze bei Bedarf ein kleines Beispiel. Wenn Lernzettel/Material die Frage nicht abdecken, sag das kurz und antworte mit allgemeinem Fachwissen. Markdown, Formeln LaTeX $...$."""


# ======================= Lernzettel =======================
def note_path(cid, tid):
    if any(c in tid for c in "/\\."):
        raise ValueError("bad id")
    return store.course_dir(cid) / "notes" / (tid + ".json")


def get_note(cid, tid):
    return store.read_json(note_path(cid, tid))


def _topic(course, tid):
    t = next((x for x in course["topics"] if x["id"] == tid), None)
    if not t:
        raise KeyError("Thema nicht gefunden")
    return t


def _topic_material(course, tid, budget=NOTE_BUDGET):
    sel, by_id, total = tp.select_material(course, [tid], budget)
    mat = tp.material_text(course, sel, by_id)
    fp = hashlib.sha1(mat.encode("utf-8")).hexdigest()[:12]
    return mat, fp, sel, by_id


def _capture(c):
    """What the AI gets from a page: tr = text + picture transcribed · miss = only a picture, content missing ·
    fig = text yes, figure not described · skip = figure marked "no transcription needed" · ok = plain text."""
    if c.get("tr"):
        return "tr"
    if c.get("notr") and (c.get("v") or c.get("vi")):
        return "skip"
    return "miss" if c.get("v") else "fig" if c.get("vi") else "ok"


def topic_info(course, tid, settings):
    t = _topic(course, tid)
    mat, fp, sel, by_id = _topic_material(course, tid)
    note = get_note(course["id"], tid)
    m = next((x for x in exam.mastery(course)["topics"] if x["id"] == tid), None)
    all_ex = store.list_exams(course["id"])
    path = exam.practice(course, all_ex)[tid]
    lvl = path["level"]
    if path["drill"]:  # the mistakes of this topic in the failed attempt -> what the variations are made from
        src = next(e for e in all_ex if e["id"] == path["drill"])
        res = src["result"]["parts"]
        path["fails"] = [p["id"] for tk in src["tasks"] for p in tk["parts"]
                         if (p.get("tid") or tk.get("tid")) == tid and res.get(p["id"], {}).get("p", 0) < p["pts"]]
        path["src_title"] = src.get("title", "")
    _, qmeta = exam.build_gen_prompt(course, {"kind": "quiz", "topics": [tid]}, settings)
    sources = {}
    for cid in sel:
        mm, c = by_id[cid]
        sources.setdefault(mm["name"], []).append((c.get("pg", int(cid.split(":")[1])) + 1))
    # every assigned, not excluded page/slide for the visual overview (material order)
    # + picture-only pages right next to the topic that no topic has (no text -> could not be assigned yet)
    mine, slides = set(t["chunks"]), {}
    assigned = {x for tt in course["topics"] for x in tt["chunks"]}
    near = set()
    for cid, (mm, c) in by_id.items():
        if cid in assigned or not c.get("v") or c.get("tr") or c.get("notr"):
            continue
        m_, i = cid.rsplit(":", 1)
        if any(f"{m_}:{int(i) + d}" in mine for d in (-3, -2, -1, 1, 2, 3)):
            near.add(cid)
    for cid, (mm, c) in by_id.items():
        if cid not in mine and cid not in near:
            continue
        i = int(cid.split(":")[1])
        g = slides.setdefault(mm["id"], {"mid": mm["id"], "name": mm["name"], "kind": mm["kind"], "pages": []})
        g["pages"].append({"i": i, "n": c.get("pg", i) + 1, "t": c.get("t") or "", "st": c.get("st") or "",
                           "img": mm["kind"] == "pdf" or bool(c.get("img")), "used": cid in sel, "s": _capture(c), "near": cid in near,
                           "x": re.sub(r"```\w*|^#+\s*", "",c.get("tr") or c.get("x") or "", flags=re.M)[:400] if mm["kind"] != "pdf" and not c.get("img") else ""})
    exams = []
    for e in all_ex:
        if any((p.get("tid") or tk.get("tid")) == tid for tk in e["tasks"] for p in tk["parts"]):
            r = e.get("result") or {}
            exams.append({"id": e["id"], "title": e.get("title"), "kind": e.get("kind", "exam"),
                          "status": e.get("status"), "pct": r.get("pct"), "created": e.get("created")})
    return {"topic": t, "mastery": m, "note": note, "stale": bool(note and note.get("fp") != fp),
            "note_est_in": tp.est_tokens(len(mat) + len(NOTE_SYSTEM)), "note_est_out": 4500,
            "has_material": len(mat) > 50,
            "quiz": {"level": lvl, "name": exam.QUIZ_LEVELS[lvl][0], "est_in": qmeta["est_in"],
                     "from_notes": qmeta["from_notes"]},
            "path": path, "levels": {k: v[0] for k, v in exam.QUIZ_LEVELS.items()}, "pass": round(100 * exam.PASS),
            "sources": [{"name": k, "pages": v} for k, v in sources.items()],
            "slides": list(slides.values()),
            "exams": exams[::-1], "models": {"learn": settings["learn_model"], "quiz": settings["quiz_model"],
                       "aux": settings["aux_model"]}}


def generate_note(course, tid, settings, progress=None):
    t = _topic(course, tid)
    mat, fp, _, _ = _topic_material(course, tid)
    if len(mat) < 50:
        raise ValueError("Zu diesem Thema gibt es kein verwertbares Material (ggf. erst transkribieren).")
    text = f"<material>\n{mat}\n</material>\nThema: {t['name']}\nErstelle jetzt den Lernzettel."
    out, usage = claude_cli.run(NOTE_SYSTEM, text, settings["learn_model"], "Lernzettel",
                                course["id"], settings.get("learn_effort"), progress)
    md = re.sub(r"^```(?:markdown|md)?\s*|\s*```$", "", out.strip())
    old = get_note(course["id"], tid) or {}
    note = {"tid": tid, "md": md, "fp": fp, "created": time.time(), "model": usage["model"],
            "usage": usage, "qa": old.get("qa", [])}
    store.write_json(note_path(course["id"], tid), note)
    return note


def ask(course, tid, question, with_material, settings, progress=None):
    t = _topic(course, tid)
    note = get_note(course["id"], tid)
    if not note:
        raise ValueError("Erst den Lernzettel erstellen.")
    question = (question or "").strip()[:2000]
    if not question:
        raise ValueError("Leere Frage")
    parts = []
    if with_material:
        mat, _, _, _ = _topic_material(course, tid, 15000)
        parts.append(f"<material>\n{mat}\n</material>")
    parts.append(f"<lernzettel thema=\"{t['name']}\">\n{note['md']}\n</lernzettel>")
    hist = note.get("qa", [])[-3:]
    if hist:
        parts.append("Bisherige Nachfragen:\n" + "\n".join(f"F: {x['q']}\nA: {x['a'][:600]}" for x in hist))
    parts.append(f"Frage: {question}")
    out, usage = claude_cli.run(ASK_SYSTEM, "\n\n".join(parts), settings["learn_model"], "Nachfrage",
                                course["id"], settings.get("learn_effort"), progress)
    item = {"q": question, "a": out.strip(), "ts": time.time(), "mat": bool(with_material),
            "tok": usage["in"] + usage["cache_w"] + usage["cache_r"] + usage["out"]}
    note.setdefault("qa", []).append(item)
    store.write_json(note_path(course["id"], tid), note)
    return item


def delete_qa(course, tid, idx):
    note = get_note(course["id"], tid)
    if note and 0 <= idx < len(note.get("qa", [])):
        note["qa"].pop(idx)
        store.write_json(note_path(course["id"], tid), note)
    return note


# ======================= variations of failed tasks =======================
VARIANT_SYSTEM = r"""Du bist erfahrene/r Hochschul-Tutor/in. Ein/e Studierende/r hat Prüfungsaufgaben nicht (voll) gelöst. Zu jeder Aufgabe erstellst du eine Übungsvariation, die genau diese Lücke trainiert. Sprache: Deutsch, Begriffe und Notation wie im Material.

Vorgehen je Aufgabe (nur gedanklich, nicht ausgeben):
1. Diagnose: Welcher konkrete Denkfehler oder welche Wissenslücke zeigt die Antwort samt Bewertung? Leere Antwort = Wissen fehlt.
2. Kern: Welches Konzept bzw. welche Fähigkeit prüft die Aufgabe? Das bleibt gleich – gleiche AB-Stufe, gleicher Operator, gleiche Tiefe.
3. Neue Situation: ein anderer Fall bzw. eine andere Entscheidung, bei der dasselbe Konzept den Ausschlag gibt – nicht dieselbe Situation mit anderen Namen, Branchen oder Zahlen. Andere Beispiele/Objekte/Daten, gern andere Fragerichtung (z. B. vom Ergebnis auf die Ursache schließen, Fehler in einer vorgegebenen Lösung finden), sodass die alte Lösung nicht übertragbar ist.
4. Fehler gezielt prüfen: So bauen, dass derselbe Denkfehler wieder zu einer falschen Antwort führt. sc/mc: die Fehlvorstellung als plausiblen, neu formulierten Distraktor aufnehmen. tf: eine Aussage, die genau die Fehlvorstellung enthält. Offene Aufgaben: die Stelle, an der der Fehler passiert ist, muss wieder nötig sein.
5. Nichts verraten: Die Aufgabenstellung enthält keine Hinweise auf den Fehler („achten Sie darauf …“, „nicht X, sondern …“, „ausdrücklich“, fett hervorgehobene Stolperstellen) und keine Gliederung (a)(b)(c), die das Original nicht hatte. Sonst wird nur geprüft, ob der Hinweis gelesen wurde.
6. Lernhilfe erst nach der Abgabe: "why" bzw. "sol" beginnt mit „Typischer Fehler: …“ (1 Satz zur Fehlvorstellung, ohne die Originalaufgabe zu erwähnen), danach die Lösung.

Regeln:
- Fachlich korrekt und im Rahmen des Stoffs: nutze <material> und die Musterlösung des Originals; nichts erfinden, was dort nicht gedeckt ist.
- Wissensbindung: Die Lösung erfordert die Fachinhalte des Kurses (Begriffe, Modelle, Kriterien, Verfahren). Mit Allgemeinwissen, gesundem Menschenverstand oder Raten aus der Formulierung darf sie nicht lösbar sein. Distraktoren sind echte Fachbegriffe oder typische Verwechslungen, und das Bewertungsschema verlangt die konkreten Fachbegriffe.
- Eigenständig lösbar: nötiger Kontext vollständig in "ctx" bzw. "q", nie auf „die vorige Aufgabe“ verweisen.
- Gleicher Typ, gleiche AB-Stufe, gleiche Punktzahl, gleiche Schwierigkeit (nicht leichter). sc/mc/tf/cloze dürfen in einen anderen objektiven Typ wechseln, wenn er den Fehler besser prüft; match/order mit neuen Elementen.
- 0 Punkte bei AB2/AB3: als 2 Teilaufgaben (erst der entscheidende Teilschritt bzw. die Grundidee, dann die eigentliche Aufgabe), Summe = Originalpunkte.
- Rechenaufgaben: neue Werte mit sauberem Ergebnis; selbst durchrechnen.
- Antwortschlüssel und Musterlösung vor der Ausgabe prüfen.

Pro Variation genau eine Aufgabe in "tasks", Reihenfolge V1, V2, …; "topic" = angegebene T-ID.
""" + "Antworte NUR" + exam.GEN_SYSTEM.split("Antworte NUR", 1)[1]
VARIANT_MATERIAL = 2500  # tokens of matching material, so variations stay within the course content


def _variant_material(course, picked):
    """The 2 best-matching sections per failed task (BM25, preferably from the task's topic), within a small budget."""
    topic_chunks = {t["id"]: set(t["chunks"]) for t in course["topics"]}
    _, by_id, _ = tp.select_material(course, [], 10 ** 9)
    chosen, used, budget = [], 0, VARIANT_MATERIAL * tp.cpt()
    for t, p in picked:
        q = " ".join(str(x) for x in (t.get("ctx", "")[:400], p["q"], p.get("sol", ""), p.get("why", "")))
        own = topic_chunks.get(p.get("tid") or t.get("tid"), set())
        hits = tp.match_chunks(course, q, 12)
        hits = [c for c in hits if c in own][:2] or hits[:1]
        for cid in hits:
            n = len(tp.chunk_text(by_id[cid][1])) if cid in by_id else 0
            if cid in by_id and cid not in chosen and used + n <= budget:
                chosen.append(cid)
                used += n
    order = {cid: i for i, cid in enumerate(by_id)}  # material order reads better than hit order
    return tp.material_text(course, sorted(chosen, key=order.get), by_id) if chosen else ""


def answer_summary(p, a):
    ty, a = p["type"], a or {}
    if ty in ("sc", "mc"):
        return ", ".join(p["opts"][i] for i in a.get("sel") or [] if i < len(p["opts"])) or "-"
    if ty == "tf":
        return "; ".join(f"{s[:60]}: {'wahr' if v else 'falsch'}" for s, v in zip(p["stmts"], a.get("tf") or [])
                         if v is not None) or "-"
    if ty == "cloze":
        return " | ".join(a.get("gaps") or []) or "-"
    if ty == "order":
        return " -> ".join(p["items"][i] for i in a.get("order") or [] if i < len(p["items"])) or "-"
    if ty == "match":
        return "; ".join(f"{l} -> {p['right'][r]}" for l, r in zip(p["left"], a.get("map") or [])
                         if r is not None and r < len(p["right"])) or "-"
    return exam._answer_text(p, a)[:1500]


def variants(course, ex, pids, settings, progress=None):
    res = (ex.get("result") or {}).get("parts", {})
    picked = [(t, p) for t in ex["tasks"] for p in t["parts"]
              if p["id"] in pids and res.get(p["id"], {}).get("p", 0) < p["pts"]]
    if not picked:
        raise ValueError("Keine Aufgaben mit Punktabzug ausgewählt.")
    alias, rev = {}, {}
    blocks = []
    for k, (t, p) in enumerate(picked, 1):
        tid = p.get("tid") or t.get("tid")
        if tid and tid not in rev:
            rev[tid] = f"T{len(rev) + 1}"
            alias[rev[tid]] = tid
        r = res.get(p["id"], {})
        part = {kk: v for kk, v in p.items() if kk not in ("id", "disp", "tid", "topic")}
        blocks.append(
            f"### V{k} (topic {rev.get(tid, '-')})\n"
            + (f"Kontext: {t['ctx'][:1500]}\n" if t.get("ctx") else "")
            + f"Originalaufgabe: {json.dumps(part, ensure_ascii=False)}\n"
            + f"Antwort des Studierenden: {answer_summary(p, ex['answers'].get(p['id']))}\n"
            + f"Bewertung: {r.get('p', 0):g}/{p['pts']:g} P. {r.get('fb', '')} {r.get('fix', '')}".strip())
    mat = _variant_material(course, picked)
    text = ((f"<material>\n{mat}\n</material>\n\n" if mat else "")
            + "\n\n".join(blocks) + f"\n\nErstelle jetzt die {len(picked)} Variation(en) als JSON (title, min, tasks).")
    # quality over speed: at least medium effort, the call is small
    eff = settings.get("quiz_effort")
    eff = "medium" if eff in (None, "", "low") else eff
    out, usage = claude_cli.run(VARIANT_SYSTEM, text, settings["quiz_model"], "Variationen",
                                course["id"], eff, progress)
    raw = claude_cli.parse_json(out)
    new = exam.validate(raw, course, alias)
    new.update({"id": store.new_id("e"), "created": time.time(), "status": "open", "kind": "drill",
                "level": exam.exam_level(ex, {e["id"]: e for e in store.list_exams(course["id"])}),
                "parent": ex["id"], "title": "Variationen: " + re.sub(r"^Variationen: ", "", ex.get("title", ""))[:90],
                "min": max(3, int(sum(p["pts"] for t in new["tasks"] for p in t["parts"]))),
                "cfg": {"kind": "drill", "from": ex["id"], "pids": pids}, "answers": {}, "usage": {"gen": usage}})
    store.save_exam(course["id"], new)
    return new
