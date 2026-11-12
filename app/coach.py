"""Lern-Coach: the one recommended next step for the whole course ("learn on click").

Clicking only the recommended button walks through the entire material to 100 %:
strictly linear through the topics (course order), each topic Lernzettel -> learning path (quiz levels 1-3
with variations of the mistakes), checkpoint exams in between, spaced retakes of weak exams, and at the end
targeted practice of the weakest (topic, AB) pair until every topic is fully solved.

Priority of the rules (first match = main button, the next distinct ones = alternatives):
 1. setup             no material / no topics
 2. continue          an attempt that was started but not handed in
 3. variations        open mistakes on the learning path (the path requires them before moving on),
                      then the mistakes of the latest full exam (once)
 4. exam              checkpoint after every 3 completed topics, final exam once all topics are done
 5. retake            an old exam < 90 %, written >= 3 days ago (spaced repetition with the same tasks)
 6. note / quiz       current topic: read the Lernzettel once, then the quiz of its learning-path level
 7. polish            all topics done: weakest (topic, AB) -> variations of its last mistakes / quiz on that level
Everything is replayed from stored attempts; the only extra state is the "Lernzettel read" flag of a note.
"""
import time

from . import exam, learn, store, topics as tp

CHECKPOINT_EVERY = 3        # completed topics per checkpoint exam
MIN_PER_TOPIC = 15          # checkpoint/weak-topic exam length per topic (minutes)
RETAKE_BELOW = 90           # retake exams below this score …
RETAKE_AFTER = 3 * 86400    # … once the answers are no longer fresh
ROMAN = {1: "I", 2: "II", 3: "III"}


def _tid(t, p):
    return p.get("tid") or t.get("tid")


def _fails(e, tid):
    res = (e.get("result") or {}).get("parts", {})
    return [p["id"] for t in e["tasks"] for p in t["parts"]
            if _tid(t, p) == tid and res.get(p["id"], {}).get("p", 0) < p["pts"]]


def _covers(e, tid):
    """A full exam covers a topic if it was chosen for it (no choice = whole material)."""
    chosen = (e.get("cfg") or {}).get("topics") or []
    return not chosen or tid in chosen


def _act(kind, label, why, **kw):
    return {"kind": kind, "label": label, "why": why, **kw}


def _exam_minutes(n, settings):
    return int(min(settings["default_minutes"], max(30, MIN_PER_TOPIC * n)))


def recommend(course, settings):
    cid = course["id"]
    topics = course["topics"]
    exams = store.list_exams(cid)
    by_id = {e["id"]: e for e in exams}
    P = exam.practice(course, exams)
    M = exam.mastery(course, exams)
    ms = {m["id"]: m for m in M["topics"]}
    usable = [t for t in topics if t["chunks"]]  # topics without material can't be quizzed
    order = [t["id"] for t in usable]
    names = {t["id"]: t["name"] for t in topics}
    cur = next((t for t in usable if not P[t["id"]]["done"]), None)
    all_done = bool(usable) and cur is None
    now = time.time()
    cands = []
    add = cands.append

    # 1 setup
    if not course["materials"]:
        add(_act("upload", "Material hochladen", "Ohne Material kann nichts gelernt oder geprüft werden – Dateien in die Ablage ziehen."))
    elif not topics:
        add(_act("topics", "Themen aus Gliederung erstellen",
                 "Das Material wird lokal (0 Tokens) in Themen gegliedert – danach geht es Thema für Thema."))
    elif not usable:
        add(_act("topics", "Themen aus Gliederung erstellen", "Keinem Thema ist Material zugeordnet."))

    if not cands:
        # 2 attempt started but not handed in
        # the current topic's path attempt first, then started ones, then the newest (abandoned ones can be discarded)
        cur_open = (P[cur["id"]]["open"] or {}).get("id") if cur else None
        opens = sorted((e for e in exams if e.get("status") != "graded"),
                       key=lambda e: (e["id"] == cur_open, any((e.get("answers") or {}).values()), e.get("created", 0)), reverse=True)
        if opens:
            e = opens[0]
            n = len([a for a in (e.get("answers") or {}).values() if a])
            add(_act("continue", "Weiter: " + e.get("title", "angefangener Versuch"),
                     ("Angefangen" if n else "Erstellt") + ", aber noch nicht abgegeben – erst abschließen, dann geht es weiter.",
                     eid=e["id"], ekind=e.get("kind", "exam")))

        # 3 open mistakes on the learning path (current topic first)
        drills = sorted((tid for tid in order if P[tid]["drill"]),
                        key=lambda tid: (tid != (cur or {}).get("id"), order.index(tid)))
        for tid in drills[:1]:
            src = by_id.get(P[tid]["drill"])
            fails = _fails(src, tid) if src else []
            if fails:
                s = P[tid]
                add(_act("variants", f"{'Fehler' if len(fails) == 1 else f'{len(fails)} Fehler'} variieren: {names[tid]}",
                         f"Letzter Versuch auf Stufe {s['level']}: {s['last']['pct']} % – ab {round(100 * exam.PASS)} % geht es weiter. "
                         "Neue Aufgaben zum selben Konzept, die genau diese Fehler prüfen.",
                         eid=src["id"], pids=fails, tid=tid))

        # 3b mistakes of the latest full exam: practise them once (a variation of it exists -> done)
        full = [e for e in exams if e.get("kind", "exam") == "exam"]
        last_ex = max((e for e in full if e.get("status") == "graded"), key=lambda e: e.get("graded", 0), default=None)
        if last_ex and not any(x.get("parent") == last_ex["id"] for x in exams):
            res = last_ex["result"]["parts"]
            fails = sorted(((res.get(p["id"], {}).get("p", 0) / p["pts"], p["id"]) for t in last_ex["tasks"] for p in t["parts"]
                            if res.get(p["id"], {}).get("p", 0) < p["pts"]))
            if fails:
                pids = [pid for _, pid in fails[:8]]  # the worst ones, keeps the session short
                add(_act("variants", f"{len(pids)} Fehler aus „{last_ex.get('title', 'Klausur')}“ variieren",
                         f"Die Klausur ergab {round(last_ex['result']['pct'])} %. Bevor es weitergeht: "
                         f"{'die schwächsten ' if len(fails) > len(pids) else ''}Fehler einmal mit neuen Aufgaben zum selben Konzept üben.",
                         eid=last_ex["id"], pids=pids))

        # 4 checkpoint / final exam
        done_ts = {tid: P[tid]["done_ts"] or P[tid]["ts"] for tid in order if P[tid]["done"]}
        if all_done:
            last = max(done_ts.values() or [0])
            final = any(all(_covers(e, tid) for tid in order) and (e.get("created") or 0) >= last for e in full)
            if not final:
                add(_act("exam", "Abschlussklausur über alle Themen",
                         f"Alle {len(order)} Themen haben den Lernpfad geschafft. Eine echte Probeklausur zeigt, was sitzt – "
                         "Fehler werden danach gezielt nachgeübt.",
                         cfg={"topics": order, "minutes": settings["default_minutes"], "focus_weak": True}, final=True))
        else:
            unc = [tid for tid, ts in done_ts.items()
                   if not any(_covers(e, tid) and (e.get("created") or 0) >= ts for e in full)]
            if len(unc) >= CHECKPOINT_EVERY:
                add(_act("exam", f"Zwischenklausur: {len(unc)} abgeschlossene Themen",
                         f"Du hast {len(unc)} Themen abgeschlossen ({', '.join(names[t] for t in unc[:3])}{' …' if len(unc) > 3 else ''}). "
                         "Eine gemischte Klausur festigt sie, bevor neuer Stoff dazukommt.",
                         cfg={"topics": unc, "minutes": _exam_minutes(len(unc), settings), "focus_weak": True}))

        # 5 spaced retake of a weak exam (same tasks)
        retaken = {e.get("retake_of") for e in exams} | {e.get("title", "")[:-7] for e in exams if e.get("title", "").endswith(" (Wdh.)")}
        due = [e for e in full if e.get("status") == "graded" and e["id"] not in retaken and e.get("title") not in retaken
               and (e.get("result") or {}).get("pct", 100) < RETAKE_BELOW and now - (e.get("graded") or now) >= RETAKE_AFTER
               and all(P[t]["last"] or P[t]["done"] for t in exam._topic_points(e) if t in P)]
        if due:
            e = min(due, key=lambda e: e["result"]["pct"])
            days = int((now - e["graded"]) // 86400)
            add(_act("retake", f"Alte Klausur wiederholen: {e.get('title', '')}",
                     f"Vor {days} Tagen {round(e['result']['pct'])} %. Inzwischen hast du die Themen geübt – "
                     "dieselben Aufgaben mit Abstand nochmal zeigen, ob es jetzt hängen geblieben ist.", eid=e["id"]))

        # 6 current topic: Lernzettel once, then the quiz of its level
        if cur:
            tid, s = cur["id"], P[cur["id"]]
            k = order.index(tid) + 1
            note = learn.get_note(cid, tid)
            if not s["last"] and not s["drill"] and not (note and note.get("seen")):
                add(_act("note", f"{'Lernzettel lesen' if note else 'Lernzettel erstellen & lesen'}: {names[tid]}",
                         f"Neues Thema ({k}/{len(order)}). Erst verstehen, dann üben: Überblick, Kernbegriffe, Beispiel und "
                         "Selbsttest – danach kommt das erste Quiz.", tid=tid, exists=bool(note)))
            lv = s["level"]
            intro = (f"✓ Stufe {lv - 1} bestanden ({s['last']['pct']} %). " if s["last"] and s["last"]["ok"] and lv > 1 else "")
            if not s["open"]:  # a started quiz on this level is continued instead (rule 2)
                add(_act("quiz", f"Quiz Stufe {lv} – {exam.QUIZ_LEVELS[lv][0]}: {names[tid]}",
                         intro + f"Thema {k}/{len(order)}. Ab {round(100 * exam.PASS)} % geht es "
                         + (f"zu Stufe {lv + 1}" if lv < 3 else "zum nächsten Thema")
                         + ", darunter übst du deine Fehler mit Variationen.",
                         cfg={"kind": "quiz", "topics": [tid], "level": lv, "minutes": 10}, tid=tid))

        # 7 polish: weakest (topic, AB) until everything is fully solved
        if all_done:
            pairs = []
            for tid in order:
                m = ms.get(tid) or {}
                ab = m.get("ab") or {}
                for a in (1, 2, 3):
                    v = ab.get(a, max(ab.get(2, 0), ab.get(3, 0)) if a == 1 else 0)
                    if v < 100:
                        pairs.append((v, order.index(tid), tid, a))
            pairs.sort()
            if pairs:
                v, _, tid, a = pairs[0]
                graded = sorted((e for e in exams if e.get("status") == "graded" and tid in exam._topic_points(e)),
                                key=lambda e: e.get("graded", 0))
                e = graded[-1] if graded else None
                if e:
                    fails = _fails(e, tid)
                    g, mx = exam._topic_points(e)[tid]
                    child = any(x.get("parent") == e["id"] for x in exams)
                    if fails and not child and (e.get("kind") != "drill" or g < exam.PASS * mx):
                        add(_act("variants", f"Fehler variieren: {names[tid]}",
                                 f"Festigen: Im letzten Versuch zu „{names[tid]}“ hattest du {round(100 * g / mx)} %. "
                                 "Neue Aufgaben zu genau diesen Fehlern.", eid=e["id"], pids=fails, tid=tid))
                add(_act("quiz", f"Festigen: {names[tid]} – Stufe {a}",
                         f"Schwächste Stelle: AB {ROMAN[a]} bei {v} % (Verständnis {ms[tid]['score']:g} %). "
                         "Neuere Versuche zählen mehr – ein voll gelöstes Quiz hebt den Wert deutlich.",
                         cfg={"kind": "quiz", "topics": [tid], "level": a, "minutes": 10}, tid=tid, polish=True))
                weak = list(dict.fromkeys(t for _, _, t, _ in pairs))[:3]
                if len(weak) > 1:
                    add(_act("exam", f"Probeklausur: {len(weak)} schwächste Themen",
                             "Mischt die Themen mit dem meisten Nachholbedarf: " + ", ".join(names[t] for t in weak) + ".",
                             cfg={"topics": weak, "minutes": _exam_minutes(len(weak), settings), "focus_weak": True}))
            else:
                add(_act("finished", "Alles verstanden – 100 %",
                         "Jedes Thema ist in AB I–III zuletzt vollständig gelöst. Zur Auffrischung: eine neue Abschlussklausur.",
                         cfg={"topics": order, "minutes": settings["default_minutes"]}))

        # always available as an alternative: a fresh exam over everything learned so far
        learned = [tid for tid in order if P[tid]["last"] or P[tid]["done"]]
        if learned and not all_done:
            add(_act("exam", f"Probeklausur über {'das bisher gelernte Thema' if len(learned) == 1 else f'{len(learned)} bisher gelernte Themen'}",
                     "Freiwilliger Test zwischendurch – zählt voll zum Verständnis.",
                     cfg={"topics": learned, "minutes": _exam_minutes(len(learned), settings), "focus_weak": True}))

    # main button + up to 2 distinct alternatives
    seen, pick = set(), []
    for a in cands:
        key = (a["kind"], a.get("tid"), a.get("eid"), tuple((a.get("cfg") or {}).get("topics") or ()))
        if key not in seen:
            seen.add(key)
            pick.append(a)
    pick = pick[:3]
    for a in pick:
        _estimate(course, a, settings)

    warn = [f"„{t['name']}“ hat kein Material und kann nicht geübt werden – zuordnen oder löschen, sonst sind 100 % nicht erreichbar."
            for t in topics if not t["chunks"]][:3]
    open_img = 0
    for m in course["materials"]:
        open_img += sum(1 for c in store.get_chunks(cid, m["id"])["chunks"]
                        if c.get("v") and not c.get("tr") and not c.get("notr") and c.get("st") != "x")
    if open_img:
        warn.append(f"{open_img} Seiten ohne Text sind noch nicht transkribiert und fehlen in Quizzen und Klausuren (Schritt ③ im Material).")
    phase = ("setup" if pick and pick[0]["kind"] in ("upload", "topics") else "done" if pick and pick[0]["kind"] == "finished"
             else "polish" if all_done else "learn")
    return {"phase": phase, "overall": M["overall"], "done": sum(1 for t in order if P[t]["done"]), "total": len(order),
            "cur": {"id": cur["id"], "name": cur["name"], "level": P[cur["id"]]["level"], "index": order.index(cur["id"]) + 1}
            if cur else None,
            "topics": [{"id": t["id"], "name": t["name"], "level": P[t["id"]]["level"],
                        "state": "empty" if not t["chunks"] else "done" if P[t["id"]]["done"]
                        else "cur" if cur and t["id"] == cur["id"] else "todo",
                        "score": (ms.get(t["id"]) or {}).get("score", 0)} for t in topics],
            "primary": pick[0] if pick else None, "alts": pick[1:], "warn": warn,
            "rules": {"checkpoint": CHECKPOINT_EVERY, "retake_below": RETAKE_BELOW, "retake_days": RETAKE_AFTER // 86400,
                      "pass": round(100 * exam.PASS)}}


def _estimate(course, a, settings):
    """Token estimate + model for the buttons (local, 0 tokens)."""
    try:
        if a["kind"] in ("quiz", "exam", "finished"):
            cfg = a["cfg"]
            _, meta = exam.build_gen_prompt(course, cfg, settings)
            a["est"] = meta["est_in"]
            a["model"] = settings["quiz_model" if cfg.get("kind") == "quiz" else "gen_model"]
        elif a["kind"] == "note" and not a.get("exists"):
            mat, _, _, _ = learn._topic_material(course, a["tid"])
            a["est"] = tp.est_tokens(len(mat) + len(learn.NOTE_SYSTEM))
            a["model"] = settings["learn_model"]
        elif a["kind"] == "variants":
            a["est"] = 2500 + 900 * len(a["pids"])
            a["model"] = settings["quiz_model"]
    except Exception:  # noqa: BLE001 – an estimate must never break the recommendation
        pass


def mark_seen(course, tid):
    note = learn.get_note(course["id"], tid)
    if note and not note.get("seen"):
        note["seen"] = time.time()
        store.write_json(learn.note_path(course["id"], tid), note)
    return bool(note)
