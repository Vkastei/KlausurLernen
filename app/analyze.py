"""Local material analysis (0 tokens): assigns a role to every page/slide/section and derives
SUGGESTIONS (never applied automatically): exclude noise, mark learning goals, move exam info into
the course instructions.

chunk["role"]: see ROLES; chunk["rsrc"]: "local" (later also "ai")
data["rolev"]: analysis version; data["review"]: "open" | "done"
"""
import hashlib
import re
from collections import Counter

ROLE_VERSION = 4

ROLES = {
    "content": "Lerninhalt", "key": "Ausdrücklich prüfungsrelevant", "notexam": "Laut Material nicht klausurrelevant",
    "notexam_t": "Notiz „nicht klausurrelevant“ im Text (evtl. nur teilweise – bitte prüfen)",
    "goals": "Lernziele",
    "exercise": "Übung/Aufgabe", "solution": "Lösung/Besprechung",
    "examinfo": "Prüfungsinfo", "org": "Organisatorisches/Gliederung", "lit": "Literatur/Quellen",
    "thanks": "Abschluss/Danke", "divider": "Titel-/Trennfolie", "interactive": "Umfrage/Interaktion",
    "anim": "Animationsschritt", "dup": "Duplikat",
}
# role -> suggested status ("x" exclude, "imp" very important, "instr" -> into course instructions + exclude)
SUGGEST = {"org": "x", "lit": "x", "thanks": "x", "divider": "x", "interactive": "x", "anim": "x", "dup": "x",
           "notexam": "x", "notexam_t": "x", "goals": "imp", "key": "imp", "examinfo": "instr",
           "exercise": "sty", "solution": "sty"}
# whole files that are task collections (old exams, exercise sheets, quizzes) or their solutions -- by file name
FILE_EXERCISE = re.compile(r"klausur|exam|prüfung|pruefung|übungsblatt|uebungsblatt|aufgabenblatt|übungszettel|aufgaben|"
                           r"exercises?|assignments?|homework|hausaufgabe|hausübung|tutorium|quiz|test\b|"
                           r"(blatt|sheet|zettel)[\s_-]*\d+", re.IGNORECASE)
FILE_SOLUTION = re.compile(r"lösung|loesung|solutions?|musterlösung|answers?", re.IGNORECASE)
# "nicht klausurrelevant!" / "(nicht klausurrelevant, Folien 414-431)" -- but NOT the explanation
# "nicht klausurrelevante Folien sind mit ... markiert" (adjective form)
NOTEXAM_TEXT = re.compile(r"nicht\s+(klausur|prüfungs|exam)[- ]?relevant(?![a-zäöü])", re.IGNORECASE)
NOTEXAM_RANGE = re.compile(r"nicht\s+(klausur|prüfungs|exam)[- ]?relevant\W{0,6}(folien|seiten|slides|pages)\s*(\d+)\s*[-–]\s*(\d+)",
                           re.IGNORECASE)
PROTECTED = {"exercise", "solution", "goals", "examinfo", "notexam", "dup", "anim"}  # AI may not turn these into "content"
ACTION_LABEL = {"x": "ausschließen", "imp": "als „sehr wichtig“ markieren",
                "instr": "in Kursvorgaben übernehmen und ausschließen",
                "sty": "als Stilvorlage für Klausuraufgaben nutzen (bleibt Lernmaterial)"}

_I = re.IGNORECASE
PATTERNS = [  # order matters: first match wins
    ("examinfo", re.compile(r"(informationen|hinweise|infos?) zur (prüfung|klausur)|prüfungsleistung|prüfungsmodalit|"
                            r"klausur ?(infos?|hinweise|organisation)|exam (info|information|details)", _I)),
    ("goals", re.compile(r"lernziel|kompetenzziel|learning (goals?|objectives?|outcomes?)", _I)),
    ("lit", re.compile(r"^(literatur|quellen|verwendete quellen|literaturverzeichnis|weiterführende literatur|"
                       r"references|bibliograph|sources)", _I)),
    ("thanks", re.compile(r"vielen dank|danke f(ü|ue)r|thank you|thanks for|^fragen\s*\??$|^questions\s*\??$|"
                          r"bis (morgen|zum nächsten|nächste woche)|ende der (vorlesung|veranstaltung)", _I)),
    ("interactive", re.compile(r"pingo|umfrage|brainstorming|miro\b|mentimeter|kahoot|\bpoll\b", _I)),
    ("org", re.compile(r"organisatorisch|verwendungshinweise|modulevaluation|lehrevaluation|hinweise zur evaluation|"
                       r"evaluation der (lehr)?veranstaltung|sprechstunde|^kontakt|"
                       r"impressum|copyright|lizenzhinweis|termine\b|ablauf der veranstaltung|^agenda|gliederung|"
                       r"^inhalt(sverzeichnis)?$", _I)),
    ("solution", re.compile(r"lösung|musterlösung|besprechung (von|der|zu)|solution", _I)),
    ("exercise", re.compile(r"(haus)?übung|aufgabe|aufgabenblatt|exercise|assignment|tutorium", _I)),
    ("divider", re.compile(r"^(vorlesung|termin|kapitel|lecture|chapter|teil|block|woche|week)\s*\d+\s*$", _I)),
]


def _title(c):
    return re.sub(r"^(Folie|Seite|Slide|Page)\s*\d+\s*:\s*", "", c.get("t", "")).strip()


def _norm_lines(x):
    return [l for l in (re.sub(r"\s+", " ", re.sub(r"[#*_`>|\-•]", " ", l)).strip().lower() for l in x.split("\n")) if l]


def _sig(c):
    body = re.sub(r"\d+", "#", " ".join(_norm_lines(c.get("x", ""))))
    return hashlib.sha1((_title(c).lower() + "|" + body).encode("utf-8")).hexdigest()


def classify(chunks):
    """Returns a role per chunk (list), purely from titles/text."""
    roles = ["content"] * len(chunks)
    titles = Counter(_title(c).lower() for c in chunks if _title(c))
    for i, c in enumerate(chunks):
        t, x = _title(c), c.get("x", "")
        for role, rx in PATTERNS:
            if rx.search(t):
                roles[i] = role
                break
        if roles[i] == "interactive" and (sum(1 for l in x.split("\n") if "?" in l) >= 2 or len(x) >= 300
                                          or re.search(r"(pingo|quiz)\b.*\s[-–:]\s*\w", t, _I)):
            roles[i] = "exercise"  # e.g. slides that list the actual quiz/Pingo questions -> valuable task style
        if roles[i] == "content" and not (c.get("v") and not c.get("tr")):  # image-only page: content unknown yet
            # very little text under a title that repeats >= 3x (module name as divider slide)
            if len(x.strip()) < 40 and titles.get(t.lower(), 0) >= 3:
                roles[i] = "divider"
            elif re.fullmatch(r"(Seite|Folie) \d+", c.get("t", "")) and len(x.strip()) < 25:
                roles[i] = "divider"
    # lecturer's own "not exam-relevant" signals: marker icon (extract.marker_flags), text note, range statement
    by_pg = {c.get("pg", i): i for i, c in enumerate(chunks)}
    for c in chunks:
        c.pop("why", None)
    for i, c in enumerate(chunks):
        if roles[i] == "examinfo":
            continue
        tx = c.get("t", "") + "\n" + c.get("x", "")
        if c.get("nx"):
            roles[i], c["why"] = "notexam", "Symbol des Dozenten"
        elif NOTEXAM_TEXT.search(tx):
            roles[i] = "notexam_t"  # may refer to only part of the page -> own category, AI may refine
        m = NOTEXAM_RANGE.search(tx)
        if m:
            a, b = int(m.group(3)), int(m.group(4))
            sm = re.match(r"(Folie|Slide)\s*(\d+)\s*:", c.get("t", ""))
            # slide numbers can differ from PDF page numbers -> derive the offset from this page's own slide number
            off = int(sm.group(2)) - (c.get("pg", i) + 1) if sm and m.group(2).lower() in ("folien", "slides") else 0
            for k in range(a, min(b, a + 500) + 1):
                j = by_pg.get(k - off - 1)
                if j is not None and roles[j] != "examinfo":
                    roles[j] = "notexam"
                    chunks[j]["why"] = f"Bereichsangabe {m.group(2)} {a}–{b}"
            roles[i], c["why"] = "notexam", "Bereichsangabe"
    # exact duplicates (e.g. repeated outline slides): keep the first occurrence
    seen = {}
    for i, c in enumerate(chunks):
        if roles[i] in ("goals", "examinfo") or len(c.get("x", "")) < 20:
            continue
        s = _sig(c)
        if s in seen:
            roles[i] = "dup"
        else:
            seen[s] = i
    # animation build-up: page i is fully contained in page i+1 with the same title -> keep the last step
    for i in range(len(chunks) - 1):
        a, b = chunks[i], chunks[i + 1]
        if roles[i] != "content" or _title(a).lower() != _title(b).lower():
            continue
        la, lb = _norm_lines(a.get("x", "")), _norm_lines(b.get("x", ""))
        if la and len(la) < len(lb) and set(la) <= set(lb):
            roles[i] = "anim"
    return roles


def analyze(data, name=None):
    """(Re)assign local roles. Keeps AI roles (phase 3) and the user's own marks.
    name: file name -> whole task collections (old exams, exercise sheets) / solution files are recognized."""
    roles = classify(data["chunks"])
    stem = re.sub(r"\.\w+$", "", name or "")
    file_role = "solution" if FILE_SOLUTION.search(stem) else "exercise" if FILE_EXERCISE.search(stem) else None
    if file_role:
        roles = [file_role if r == "content" else r for r in roles]
    for c, r in zip(data["chunks"], roles):
        if c.get("rsrc") == "ai" and r != "notexam":  # the lecturer's explicit marker beats the AI
            continue
        c["role"], c["rsrc"] = r, ("ai" if c.get("rsrc") == "ai" else "local")
    data["rolev"] = ROLE_VERSION
    data["review"] = "open" if pending(data) else "done"
    return data


def pending(data):
    """Pages with a suggestion that the user has not decided on yet (no own status set)."""
    out = []
    for i, c in enumerate(data["chunks"]):
        act = SUGGEST.get(c.get("role"))
        if not act:
            continue
        if act == "sty":  # style suggestion: open as long as the page is used and not yet a style reference
            if not c.get("sty") and c.get("st") != "x" and not c.get("nosty"):
                out.append(i)
        elif not c.get("st"):
            out.append(i)
    return out


def suggestions(data, kind):
    unit = {"pdf": "Seite", "pptx": "Folie"}.get(kind, "Abschnitt")
    cats = {}
    for i in pending(data):
        c = data["chunks"][i]
        r = c["role"]
        cats.setdefault(r, []).append({"i": i, "num": (c.get("pg", i)) + 1, "t": c.get("t", ""),
                                       "x": c.get("x", "")[:160], "why": c.get("why", "")})
    order = list(ROLES)
    live = [c for c in data["chunks"] if c.get("st") != "x"]
    return {"unit": unit, "review": data.get("review", "open"), "info": data.get("ai_info", ""),
            "triaged": sum(1 for c in live if c.get("rsrc") == "ai"), "live": len(live),
            "cats": [{"role": r, "label": ROLES[r], "action": SUGGEST[r], "action_label": ACTION_LABEL[SUGGEST[r]],
                      "pages": cats[r]} for r in sorted(cats, key=order.index)]}


def apply(data, roles, course):
    """Apply suggestions for the chosen roles. Returns (changed pages, instruction text added)."""
    changed, info = 0, []
    for i in pending(data):
        c = data["chunks"][i]
        act = SUGGEST.get(c["role"])
        if c["role"] not in roles:
            continue
        if act == "instr":
            info.append(c.get("x", "").strip())
            c["st"] = "x"
        elif act == "sty":
            c["sty"] = True  # stays normal learning material; in exam generation it moves to the style block
        else:
            c["st"] = act
        changed += 1
    added = ""
    if info:
        # prefer the AI's compact summary (phase 3) over the raw page text
        txt = data.get("ai_info") or "\n".join(t for t in info if t)
        txt = re.sub(r"^#+\s*", "", txt, flags=re.M).strip()
        if txt and txt not in course.get("instructions", ""):
            added = "Aus dem Material (Prüfungsinfo):\n" + txt[:1500]
            course["instructions"] = (course.get("instructions", "").rstrip() + "\n\n" + added).strip()
    data["review"] = "done"
    return changed, added


# ======================= phase 3: AI pre-check (text only, no images) =======================
TRIAGE_BATCH = 150
TRIAGE_SNIPPET = 100
CODES = {"c": "content", "k": "key", "n": "notexam", "g": "goals", "e": "exercise", "s": "solution", "i": "examinfo", "o": "org",
         "l": "lit", "t": "thanks", "d": "divider", "u": "interactive"}
LOCAL_CODE = {v: k for k, v in CODES.items()}
EXAM_HINT = re.compile(r"klausur|prüfung|exam\b|bewertung|hilfsmittel", _I)

TRIAGE_SYSTEM = r"""Du ordnest Seiten von Lernmaterial (Vorlesungsfolien, Skripte) für die Klausurvorbereitung ein. Zeilenformat: Nummer|Titel|lokaler Hinweis|F=Abbildung|Textanfang.
Rollen:
c Lerninhalt (prüfbarer Fachinhalt)
k Lerninhalt mit ausdrücklichem Prüfungshinweis im Text (z. B. "klausurrelevant", "genau so lernen", "wichtig für die Prüfung")
n laut Material ausdrücklich NICHT klausurrelevant (z. B. "nicht klausurrelevant", "Zusatzmaterial", "nur zur Info")
g Lernziele/Kompetenzziele
e Übung/Aufgabe/Fragen (auch Quiz/Pingo-Folien mit konkreten Fragen)
s Lösung/Besprechung einer Aufgabe
i Prüfungsinfo (Dauer, Format, Hilfsmittel, Umfang, Gewichtung)
o Organisatorisches, Gliederung/Agenda, Evaluation, Verwendungshinweise
l Literatur/Quellen
t Abschluss/Dank/"Fragen?"
d Titel-/Trennfolie ohne Fachinhalt
u Umfrage/Interaktion ohne fachlichen Inhalt
Der lokale Hinweis kann falsch sein. Im Zweifel c (lieber behalten als verlieren).
Antworte NUR mit JSON: {"r":"Bereiche je Rolle, z. B. 1-4:o,5:g,6-20:c,21:e","info":"..."}
"info": nur wenn <pruefungsinfo> mitgeliefert wird: kompakte Kursvorgabe für die Erstellung von Probeklausuren (Dauer, Aufbau/Gewichtung, Aufgabenformate, Hilfsmittel, prüfungsrelevante Inhalte) in 2-6 Zeilen, nur belegte Fakten; sonst ""."""


def triage_todo(data):
    """Active pages not yet checked by the AI (excluded pages cost nothing)."""
    return [i for i, c in enumerate(data["chunks"]) if c.get("st") != "x" and c.get("rsrc") != "ai"]


def triage_estimate(data, cpt):
    todo = triage_todo(data)
    chars = sum(min(len(data["chunks"][i].get("x", "")), TRIAGE_SNIPPET) + min(len(data["chunks"][i].get("t", "")), 70) + 12
                for i in todo)
    batches = max(1, -(-len(todo) // TRIAGE_BATCH)) if todo else 0
    return {"pages": len(todo), "est_in": int(chars / cpt) + batches * (len(TRIAGE_SYSTEM) // 3 + 1000),
            "est_out": batches * 500}


def _num(c, i):
    return c.get("pg", i) + 1


def triage(data, model, effort, course_id, progress=None):
    """AI pre-check: refines roles of active pages (rsrc="ai") and summarizes exam info (data["ai_info"])."""
    from . import claude_cli
    todo = triage_todo(data)
    if not todo:
        return data
    chunks = data["chunks"]
    by_num = {_num(chunks[i], i): i for i in todo}
    # full text of pages that look like exam information (usually 1-3 pages) -> summarized once
    info_pages = [i for i in todo if chunks[i].get("role") == "examinfo"
                  or (EXAM_HINT.search(chunks[i].get("t", "")) and chunks[i].get("role") not in ("exercise", "solution"))]
    info_txt = "\n\n".join(f"[{_num(chunks[i], i)}] {chunks[i].get('t', '')}\n{chunks[i].get('x', '')[:1500]}"
                           for i in info_pages)[:4000]
    batches = [todo[k:k + TRIAGE_BATCH] for k in range(0, len(todo), TRIAGE_BATCH)]
    infos = []
    for bi, batch in enumerate(batches):
        lines = []
        for i in batch:
            c = chunks[i]
            snippet = re.sub(r"\s+", " ", re.sub(r"[#*_`>|]", " ", c.get("x", ""))).strip()[:TRIAGE_SNIPPET]
            title = re.sub(r"^(Folie|Seite)\s*\d+\s*:\s*", "", c.get("t", ""))[:70]
            lines.append(f"{_num(c, i)}|{title}|{LOCAL_CODE.get(c.get('role'), '-')}|{'F' if c.get('vi') or c.get('v') else ''}|{snippet}")
        text = "\n".join(lines)
        if bi == 0 and info_txt:
            text += f"\n\n<pruefungsinfo>\n{info_txt}\n</pruefungsinfo>"
        out, _ = claude_cli.run(TRIAGE_SYSTEM, text, model, "KI-Vorprüfung", course_id, effort)
        res = claude_cli.parse_json(out)
        for part in re.split(r"[,;\s]+", str(res.get("r", ""))):
            m = re.fullmatch(r"(\d+)(?:-(\d+))?:([a-z])", part.strip())
            if not m or m.group(3) not in CODES:
                continue
            a, b = int(m.group(1)), int(m.group(2) or m.group(1))
            for n in range(a, min(b, a + 2000) + 1):
                i = by_num.get(n)
                if i is None or chunks[i].get("role") in ("dup", "anim", "notexam"):
                    continue  # structural findings and the lecturer's own markers stay
                if CODES[m.group(3)] == "content" and chunks[i].get("role") in PROTECTED:
                    continue  # clear title-based roles (Übung, Lösung, Lernziele …) are not downgraded
                chunks[i]["role"], chunks[i]["rsrc"] = CODES[m.group(3)], "ai"
        for i in batch:  # pages the AI did not list keep their local role, but count as checked
            chunks[i]["rsrc"] = "ai"
        if res.get("info"):
            infos.append(str(res["info"]).strip())
        if progress:
            progress(bi + 1, len(batches))
    if infos:
        data["ai_info"] = "\n".join(infos)[:1500]
    data["triaged"] = True
    data["review"] = "open" if pending(data) else data.get("review", "done")
    return data
