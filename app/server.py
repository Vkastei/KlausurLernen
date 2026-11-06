"""Local web server (stdlib only). Run:  python -m app.server   -> http://127.0.0.1:8765"""
import base64
import hashlib
import json
import mimetypes
import re
import sys
import threading
import time
import traceback
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import analyze, claude_cli, exam, extract, learn, store, topics as tp

STATIC = store.ROOT / "static"
PORT = 8765
JOBS = {}
_course_lock = threading.Lock()


# ---------------- jobs ----------------
def start_job(kind, fn):
    jid = store.new_id("j")
    job = {"id": jid, "kind": kind, "status": "running", "progress": "", "result": None, "error": None,
           "t0": time.time()}
    JOBS[jid] = job

    def prog(*a):
        job["progress"] = a

    def run():
        try:
            job["result"] = fn(prog)
            job["status"] = "done"
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            job["error"] = str(e)
            job["status"] = "error"
    threading.Thread(target=run, daemon=True).start()
    return job


# ---------------- helpers ----------------
def course_summary(c):
    return {"id": c["id"], "name": c["name"], "materials": len(c["materials"]), "topics": len(c["topics"]),
            "mastery": exam.mastery(c)["overall"] if c["topics"] else None}


def exam_summary(e):
    r = e.get("result") or {}
    return {"id": e["id"], "title": e.get("title"), "created": e.get("created"), "status": e.get("status"),
            "kind": e.get("kind", "exam"), "parent": e.get("parent"),
            "max_pts": e.get("max_pts"), "pct": r.get("pct"), "grade": r.get("grade"), "min": e.get("min"),
            "topics": sorted({t["topic"] for t in e["tasks"] if t.get("topic")})}


def course_full(cid):
    c = store.get_course(cid)
    assigned = {x for t in c["topics"] for x in t["chunks"]}
    ms = []
    for m in c["materials"]:
        data = store.get_chunks(cid, m["id"])
        if m.get("kind") == "pdf" and data.get("figv") != extract.FIG_VERSION:
            try:  # one-time: re-flag figures with the logo-aware heuristic (text/marks/transcriptions untouched)
                data = extract.refresh_pdf_figures(store.material_path(cid, m["id"], m["ext"]), data)
                store.save_chunks(cid, m["id"], data)
            except Exception:  # noqa: BLE001
                traceback.print_exc()
        if data.get("rolev") != analyze.ROLE_VERSION:  # one-time local role analysis (0 tokens)
            analyze.analyze(data, m["name"])
            store.save_chunks(cid, m["id"], data)
        ch = data["chunks"]
        live = [x for x in ch if x.get("st") != "x"]
        unassigned = sum(1 for i, x in enumerate(ch) if tp.needs_topic(x)
                         and f"{m['id']}:{i}" not in assigned) if c["topics"] else 0
        ms.append({**m, "chunks": len(ch), "chars": sum(len(tp.chunk_text(x)) for x in live),
                   "unassigned": unassigned,
                   "sugg": len(analyze.pending(data)) if data.get("review") == "open" else 0,
                   "triage": analyze.triage_estimate(data, tp.cpt()),
                   "trans": sum(1 for x in ch if x.get("tr")),
                   "sty": sum(1 for x in live if x.get("sty")),
                   "goals": sum(1 for x in live if x.get("role") == "goals" and x.get("x")),
                   "raw_chars": sum(len(x.get("x", "")) for x in ch), "excluded": len(ch) - len(live),
                   "marked": sum(1 for x in live if tp.has_marks(x)),
                   # open decisions only: pages the user marked "no transcription needed" don't count
                   "vision": sum(1 for x in live if x.get("v") and not x.get("tr") and not x.get("notr")),
                   "figs": sum(1 for x in live if x.get("vi") and not x.get("tr") and not x.get("notr")),
                   "skipped": sum(1 for x in live if x.get("notr")),
                   "triaged": sum(1 for x in live if x.get("rsrc") == "ai"), "live": len(live)})
    return {**c, "materials": ms, "exams": [exam_summary(e) for e in store.list_exams(cid)],
            "mastery": exam.mastery(c)}


def add_material(cid, name, data):
    ext = Path(name).suffix.lower()
    if ext in extract.IMG_EXT:
        ext = ".jpg"  # browser already converted to JPEG
    h = hashlib.sha1(data).hexdigest()[:12]
    with _course_lock:
        c = store.get_course(cid)
        if any(m.get("hash") == h for m in c["materials"]):
            return {"skipped": name, "reason": "bereits vorhanden"}
    mid = "m" + h
    src = store.material_path(cid, mid, ext)
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(data)
    try:
        res = extract.extract(src, name, store.material_path(cid, mid + "_img"))
    except Exception as e:  # noqa: BLE001
        src.unlink(missing_ok=True)
        raise ValueError(f"{name}: {e}")
    analyze.analyze(res, name)
    store.save_chunks(cid, mid, res)
    with _course_lock:
        c = store.get_course(cid)
        c["materials"].append({"id": mid, "name": name, "ext": ext, "kind": res["kind"], "hash": h,
                               "added": time.time()})
        store.save_course(c)
    return {"id": mid, "chunks": len(res["chunks"])}


def remove_material(cid, mid):
    with _course_lock:
        c = store.get_course(cid)
        m = next((x for x in c["materials"] if x["id"] == mid), None)
        if not m:
            return
        c["materials"].remove(m)
        for t in c["topics"]:
            t["chunks"] = [x for x in t["chunks"] if not x.startswith(mid + ":")]
        store.save_course(c)
    store.material_path(cid, mid, m["ext"]).unlink(missing_ok=True)
    store.material_path(cid, mid, ".json").unlink(missing_ok=True)
    import shutil
    shutil.rmtree(store.material_path(cid, mid + "_img"), ignore_errors=True)


def _key(name):
    return re.sub(r"\W+", " ", name.lower()).strip()


def exam_topic_ids(cid):
    ids = set()
    for e in store.list_exams(cid):
        for t in e.get("tasks", []):
            ids.add(t.get("tid"))
            ids.update(p.get("tid") for p in t.get("parts", []))
    ids.discard(None)
    return ids


def refresh_topics(c, new):
    """Merge a regenerated topic list into the existing one WITHOUT losing progress:
    - same name -> same id (mastery + weak points stay), slide assignment updated
    - manual topics and topics already used in exams are always kept
    - unused automatic topics that no longer appear are dropped"""
    used = exam_topic_ids(c["id"])
    incoming = {}
    for t in new:
        k = _key(t["name"])
        if k and k not in incoming:
            incoming[k] = t
    out, matched = [], set()
    for o in c["topics"]:
        k = _key(o["name"])
        n = incoming.get(k)
        if n is not None:
            matched.add(k)
            chunks = n.get("chunks") or tp.match_chunks(c, o["name"])
            if o.get("manual"):
                chunks = list(dict.fromkeys(o["chunks"] + chunks))
            out.append({**o, "chunks": chunks})
        elif o.get("manual"):  # pick up newly included slides by keyword match
            out.append({**o, "chunks": list(dict.fromkeys(o["chunks"] + tp.match_chunks(c, o["name"])))})
        elif o["id"] in used:
            out.append(o)
    own = [set(o["chunks"]) for o in out if o.get("manual")]
    for k, n in incoming.items():
        if k in matched:
            continue
        chunks = n.get("chunks") or tp.match_chunks(c, n["name"])
        # skip if a user-owned (e.g. renamed) topic already covers these slides -> no duplicates
        if chunks and any(len(set(chunks) & s) >= 0.8 * len(chunks) for s in own):
            continue
        out.append({"id": store.new_id("t"), "name": n["name"], "chunks": chunks, "manual": False})
    c["topics"] = out


def set_topics(cid, new, replace=False):
    with _course_lock:
        c = store.get_course(cid)
        if replace:
            refresh_topics(c, new)
            tp.auto_assign(c)  # close gaps locally (neighbours + keywords), 0 tokens
            store.save_course(c)
            return c
        have = {t["name"].lower() for t in c["topics"]}
        for t in new:
            if t["name"].lower() in have:
                continue
            chunks = t.get("chunks") or tp.match_chunks(c, t["name"])
            c["topics"].append({"id": store.new_id("t"), "name": t["name"], "chunks": chunks,
                                "manual": bool(t.get("manual"))})
            have.add(t["name"].lower())
        store.save_course(c)
    return c


def usage_summary():
    rows = store.read_usage()
    now = time.time()
    day0 = time.mktime(time.localtime(now)[:3] + (0, 0, 0, 0, 0, -1))

    def agg(rs):
        return {"calls": len(rs), "in": sum(r["in"] for r in rs), "cache_w": sum(r["cache_w"] for r in rs),
                "cache_r": sum(r["cache_r"] for r in rs), "out": sum(r["out"] for r in rs),
                "cost": round(sum(r["cost"] for r in rs), 4)}
    by_action, by_day, by_model = {}, {}, {}
    for r in rows:
        by_action.setdefault(r["action"], []).append(r)
        by_day.setdefault(time.strftime("%Y-%m-%d", time.localtime(r["ts"])), []).append(r)
        by_model.setdefault(r.get("model") or "?", []).append(r)
    names = {c["id"]: c["name"] for c in store.list_courses()}
    return {"today": agg([r for r in rows if r["ts"] >= day0]),
            "week": agg([r for r in rows if r["ts"] >= now - 7 * 86400]), "all": agg(rows),
            "by_action": {k: agg(v) for k, v in by_action.items()},
            "by_model": {k: agg(v) for k, v in by_model.items()},
            "by_day": {k: agg(v) for k, v in sorted(by_day.items())[-30:]},
            "recent": [{**r, "course": names.get(r.get("course"), "")} for r in rows[-60:][::-1]]}


# ---------------- routing ----------------
ROUTES = []


def route(method, pattern):
    def deco(fn):
        ROUTES.append((method, re.compile("^" + pattern + "$"), fn))
        return fn
    return deco


@route("GET", "/api/courses")
def r_courses(h):
    return [course_summary(c) for c in store.list_courses()]


@route("POST", "/api/courses")
def r_course_new(h):
    return store.create_course((h.json().get("name") or "").strip())


@route("GET", "/api/courses/([^/]+)")
def r_course(h, cid):
    return course_full(cid)


@route("PATCH", "/api/courses/([^/]+)")
def r_course_patch(h, cid):
    body = h.json()
    with _course_lock:
        c = store.get_course(cid)
        for k in ("name", "instructions"):
            if k in body:
                c[k] = str(body[k])
        if "topics" in body:  # rename / delete / reorder
            by = {t["id"]: t for t in c["topics"]}
            out = []
            for t in body["topics"]:
                o = by.get(t.get("id"))
                if not o:
                    continue
                name = str(t.get("name") or o["name"])
                # a renamed topic is the user's own -> survives later topic refreshes
                out.append({**o, "name": name, "manual": o.get("manual") or name != o["name"]})
            c["topics"] = out
        store.save_course(c)
    return course_full(cid)


@route("DELETE", "/api/courses/([^/]+)")
def r_course_del(h, cid):
    store.delete_course(cid)
    return {"ok": True}


@route("POST", "/api/courses/([^/]+)/materials")
def r_upload(h, cid):
    name = urllib.parse.unquote(h.headers.get("X-Filename") or "datei.txt")
    return add_material(cid, Path(name).name, h.body())


@route("DELETE", "/api/courses/([^/]+)/materials/([^/]+)")
def r_mat_del(h, cid, mid):
    remove_material(cid, mid)
    return course_full(cid)


@route("GET", "/api/courses/([^/]+)/materials/([^/]+)")
def r_mat_view(h, cid, mid):
    data = store.get_chunks(cid, mid)
    c = store.get_course(cid)
    if c["topics"]:  # pages that need a topic but have none -> "Ohne Thema" filter in the preview
        assigned = {x for t in c["topics"] for x in t["chunks"]}
        data["unassigned"] = [i for i, x in enumerate(data["chunks"])
                              if tp.needs_topic(x) and f"{mid}:{i}" not in assigned]
    return data


def _material(cid, mid):
    m = next((x for x in store.get_course(cid)["materials"] if x["id"] == mid), None)
    if not m:
        raise KeyError("Material nicht gefunden")
    return m


def _page_set(spec):
    want = set()
    for part in re.split(r"[,;\s]+", spec or ""):
        mt = re.fullmatch(r"(\d+)(?:-(\d+))?", part)
        if mt:
            a = int(mt.group(1))
            want.update(range(a, min(int(mt.group(2) or a), a + 5000) + 1))
    return want


@route("GET", "/api/courses/([^/]+)/materials/([^/]+)/suggestions")
def r_sugg(h, cid, mid):
    return analyze.suggestions(store.get_chunks(cid, mid), _material(cid, mid)["kind"])


@route("POST", "/api/courses/([^/]+)/materials/([^/]+)/suggestions")
def r_sugg_apply(h, cid, mid):
    """body: {roles: [...]} -> apply those suggestions; {dismiss: true} -> hide the suggestion badge."""
    b = h.json()
    with _course_lock:
        data = store.get_chunks(cid, mid)
        c = store.get_course(cid)
        if b.get("dismiss"):
            data["review"] = "done"
            store.save_chunks(cid, mid, data)
            return {"changed": 0, "added": ""}
        changed, added = analyze.apply(data, set(b.get("roles") or []), c)
        store.save_chunks(cid, mid, data)
        if added:
            store.save_course(c)
    return {"changed": changed, "added": added}


@route("POST", "/api/courses/([^/]+)/materials/([^/]+)/triage")
def r_triage(h, cid, mid):
    """AI pre-check (Sonnet by default, text only). Only active, not yet checked pages are sent."""
    _material(cid, mid)

    def fn(p):
        s = store.get_settings()
        data = analyze.triage(store.get_chunks(cid, mid), s["triage_model"], s.get("triage_effort"), cid, p)
        with _course_lock:  # merge: the user may have marked pages meanwhile -> keep their status
            cur = store.get_chunks(cid, mid)
            for c_new, c_cur in zip(data["chunks"], cur["chunks"]):
                for k in ("role", "rsrc"):
                    if k in c_new:
                        c_cur[k] = c_new[k]
            for k in ("ai_info", "triaged"):
                if k in data:
                    cur[k] = data[k]
            cur["review"] = "open" if analyze.pending(cur) else cur.get("review", "done")
            store.save_chunks(cid, mid, cur)
        return len(analyze.pending(cur))
    return start_job("triage", fn)


@route("POST", "/api/courses/([^/]+)/materials/([^/]+)/analyze")
def r_reanalyze(h, cid, mid):
    """Re-run the free local analysis (figures + roles) on an already uploaded file."""
    m = _material(cid, mid)
    with _course_lock:
        data = store.get_chunks(cid, mid)
        if m["kind"] == "pdf":
            extract.refresh_pdf_figures(store.material_path(cid, mid, m["ext"]), data)
        analyze.analyze(data, m["name"])
        store.save_chunks(cid, mid, data)
    return {"pending": len(analyze.pending(data))}


@route("POST", "/api/courses/([^/]+)/materials/([^/]+)/reset")
def r_reset(h, cid, mid):
    """Undo transcriptions (all, or a page range): restores the locally extracted text for free,
    so the pages can be transcribed again with the current settings."""
    m = _material(cid, mid)
    want = _page_set(h.json().get("range"))
    hit = lambda c, i: c.get("tr") and not (want and c.get("pg", i) + 1 not in want)  # noqa: E731
    data0 = store.get_chunks(cid, mid)
    by_pg = {}
    if any(hit(c, i) and "x0" not in c for i, c in enumerate(data0["chunks"])):  # no backup -> re-extract once
        src = store.material_path(cid, mid, m["ext"])
        fresh = extract.extract(src, m["name"], store.material_path(cid, mid + "_img"))["chunks"]
        by_pg = {f.get("pg"): f for f in fresh if f.get("pg") is not None}
    n = 0
    with _course_lock:
        data = store.get_chunks(cid, mid)
        for i, c in enumerate(data["chunks"]):
            if not hit(c, i):
                continue
            f = by_pg.get(c.get("pg"))
            c.pop("v", None)
            c.pop("vi", None)
            if "t0" in c:  # title taken over from the transcription
                c["t"] = c.pop("t0")
            if "x0" in c:  # backup made at transcription time
                c["x"] = c.pop("x0")
                c["v" if len(c["x"].strip()) < 60 else "vi"] = True
            elif m["kind"] == "image":
                c["x"], c["v"] = "", True
            elif f:
                c["x"] = f.get("x", "")
                for k in ("v", "vi"):
                    if f.get(k):
                        c[k] = True
            else:
                c["x"] = ""
            c.pop("tr", None)
            n += 1
        analyze.analyze(data, m["name"])
        store.save_chunks(cid, mid, data)
    return {"reset": n}


@route("PUT", "/api/courses/([^/]+)/materials/([^/]+)/marks")
def r_marks(h, cid, mid):
    """items: [{i, st?: ""|"x"|"rel"|"imp", mk?: [{k, t, o}]}] -- non-destructive, reversible."""
    with _course_lock:
        data = store.get_chunks(cid, mid)
        ch = data["chunks"]
        for it in h.json().get("items", []):
            i = it.get("i")
            if not isinstance(i, int) or not 0 <= i < len(ch):
                continue
            c = ch[i]
            if "st" in it:
                if it["st"] in ("x", "rel", "imp"):
                    c["st"] = it["st"]
                else:
                    c.pop("st", None)
            if "sty" in it:  # style reference for exam tasks (independent of relevance)
                if it["sty"]:
                    c["sty"] = True
                    c.pop("nosty", None)
                else:
                    c.pop("sty", None)
                    c["nosty"] = True  # user said no -> don't suggest it again
            if "mk" in it:
                mk = [{"k": m["k"], "t": str(m["t"])[:5000], "o": int(m.get("o", -1))}
                      for m in it["mk"] or [] if m.get("k") in ("irr", "rel", "imp") and m.get("t")]
                if mk:
                    c["mk"] = mk
                else:
                    c.pop("mk", None)
        store.save_chunks(cid, mid, data)
    return {"ok": True}


@route("GET", r"/api/courses/([^/]+)/materials/([^/]+)/img/(\d+)")
def r_mat_img(h, cid, mid, i):
    """Page preview: PDF pages are rendered once and cached; slides/images show their picture."""
    i = int(i)
    c = store.get_course(cid)
    m = next((x for x in c["materials"] if x["id"] == mid), None)
    if not m:
        raise KeyError("Material nicht gefunden")
    img_dir = store.material_path(cid, mid + "_img")
    q = urllib.parse.parse_qs(urllib.parse.urlparse(h.path).query)
    w = min(1000, max(160, int((q.get("w") or ["1000"])[0]))) if (q.get("w") or ["1000"])[0].isdigit() else 1000
    if m["kind"] == "pdf":
        f = img_dir / (f"p{i}.jpg" if w >= 1000 else f"p{i}_{w}.jpg")  # small thumbnails for the page picker
        if not f.exists():
            ch = store.get_chunks(cid, mid)["chunks"]
            data, _ = extract.render_pdf_page(store.material_path(cid, mid, m["ext"]), ch[i].get("pg", i), w)
            img_dir.mkdir(parents=True, exist_ok=True)
            f.write_bytes(data)
        return h.send_file(f)
    ch = store.get_chunks(cid, mid)["chunks"]
    imgs = ch[i].get("img") or [] if 0 <= i < len(ch) else []
    if not imgs:
        return h.send(404, {"error": "kein Bild"})
    src = img_dir / imgs[0]
    if w < 1000 and src.exists():
        t = img_dir / f"t{i}_{w}.jpg"
        if not t.exists():
            data, _ = extract.shrink_image(src.read_bytes(), w)
            t.write_bytes(data)
        return h.send_file(t)
    return h.send_file(src)


@route("POST", "/api/courses/([^/]+)/materials/([^/]+)/transcribe")
def r_transcribe(h, cid, mid):
    """Page picker: {pages: [...] to transcribe, skip: [...] "no transcription needed",
    unskip: [...], exclude: [...] image-only pages without value -> excluded}.
    Decisions are saved right away; a job only starts if pages were chosen. Legacy: {all: bool}."""
    b = h.json()
    if "pages" not in b:
        all_figs = bool(b.get("all"))
        return start_job("transcribe", lambda p: (exam.transcribe(store.get_course(cid), mid, store.get_settings(), all_figs, p), None)[1])
    with _course_lock:
        data = store.get_chunks(cid, mid)
        ch = data["chunks"]
        ok = lambda i: isinstance(i, int) and 0 <= i < len(ch)  # noqa: E731
        for i in filter(ok, b.get("skip") or []):
            ch[i]["notr"] = True
        for i in filter(ok, b.get("unskip") or []):
            ch[i].pop("notr", None)
        for i in filter(ok, b.get("exclude") or []):
            ch[i]["st"] = "x"
        for i in filter(ok, b.get("pages") or []):
            ch[i].pop("notr", None)
        store.save_chunks(cid, mid, data)
    pages = [i for i in (b.get("pages") or []) if isinstance(i, int)]
    if not pages:
        return {"saved": True}
    return start_job("transcribe", lambda p: (exam.transcribe(store.get_course(cid), mid, store.get_settings(), False, p, pages), None)[1])


@route("POST", "/api/courses/([^/]+)/topics/assign")
def r_topics_assign(h, cid):
    """Assign pages without topic: {ai:false} locally (0 tokens, instant), {ai:true} locally + AI for the rest."""
    if not h.json().get("ai"):
        with _course_lock:
            c = store.get_course(cid)
            n, rest = tp.auto_assign(c)
            store.save_course(c)
        return {"assigned": n, "remaining": len(rest), "est_ai": exam.assign_estimate(c, rest)}

    def fn(p):
        c = store.get_course(cid)
        n, rest = tp.auto_assign(c)
        mapping = exam.assign_topics_ai(c, rest, store.get_settings())
        with _course_lock:  # merge into the current state (the user may have changed topics meanwhile)
            cur = store.get_course(cid)
            n, _ = tp.auto_assign(cur)
            by = {t["id"]: t for t in cur["topics"]}
            have = {x for t in cur["topics"] for x in t["chunks"]}
            k = 0
            for x, tid in mapping.items():
                if tid in by and x not in have:
                    by[tid]["chunks"].append(x)
                    k += 1
            store.save_course(cur)
            left = len(tp.unassigned(cur))
        return {"assigned": n + k, "ai": k, "remaining": left}
    return start_job("assign", fn)


@route("POST", "/api/courses/([^/]+)/topics/local")
def r_topics_local(h, cid):
    c = store.get_course(cid)
    set_topics(cid, tp.local_suggest(c), replace=True)
    return course_full(cid)


@route("POST", "/api/courses/([^/]+)/topics/suggest")
def r_topics_ai(h, cid):
    from_goals = bool(h.json().get("goals"))

    def fn(p):
        res = exam.suggest_topics(store.get_course(cid), store.get_settings(), from_goals)
        set_topics(cid, res, replace=True)
        return None
    return start_job("topics", fn)


@route("POST", "/api/courses/([^/]+)/topics")
def r_topic_add(h, cid):
    names = [n.strip() for n in re.split(r"[\n;]", h.json().get("name", "")) if n.strip()]
    set_topics(cid, [{"name": n, "manual": True} for n in names])
    return course_full(cid)


@route("GET", "/api/courses/([^/]+)/topics/([^/]+)")
def r_topic(h, cid, tid):
    return learn.topic_info(store.get_course(cid), tid, store.get_settings())


@route("POST", "/api/courses/([^/]+)/topics/([^/]+)/note")
def r_note(h, cid, tid):
    return start_job("note", lambda p: (learn.generate_note(store.get_course(cid), tid, store.get_settings(), p), None)[1])


@route("POST", "/api/courses/([^/]+)/topics/([^/]+)/ask")
def r_ask(h, cid, tid):
    b = h.json()
    return start_job("ask", lambda p: learn.ask(store.get_course(cid), tid, b.get("q"), b.get("material"),
                                                 store.get_settings(), p))


@route("DELETE", r"/api/courses/([^/]+)/topics/([^/]+)/qa/(\d+)")
def r_qa_del(h, cid, tid, idx):
    learn.delete_qa(store.get_course(cid), tid, int(idx))
    return {"ok": True}


@route("POST", "/api/courses/([^/]+)/exams/([^/]+)/variants")
def r_variants(h, cid, eid):
    pids = h.json().get("pids") or []
    return start_job("variants", lambda p: learn.variants(store.get_course(cid), store.get_exam(cid, eid), pids,
                                                          store.get_settings(), p)["id"])


@route("POST", "/api/courses/([^/]+)/estimate")
def r_estimate(h, cid):
    _, meta = exam.build_gen_prompt(store.get_course(cid), h.json(), store.get_settings())
    meta.pop("alias", None)
    return meta


@route("POST", "/api/courses/([^/]+)/exams")
def r_generate(h, cid):
    cfg = h.json()

    def fn(p):
        return exam.generate(store.get_course(cid), cfg, store.get_settings(), p)["id"]
    return start_job("generate", fn)


@route("GET", "/api/courses/([^/]+)/exams/([^/]+)")
def r_exam(h, cid, eid):
    e = store.get_exam(cid, eid)
    out = exam.public_view(e)
    if e.get("status") == "graded" and e.get("kind") in ("quiz", "drill"):
        # learning path: what this attempt means per topic and what comes next
        c = store.get_course(cid)
        exams = store.list_exams(cid)
        L = exam.exam_level(e, {x["id"]: x for x in exams})
        P = exam.practice(c, exams)
        names = {t["id"]: t["name"] for t in c["topics"]}
        res = e["result"]["parts"]
        out["path"] = {"level": L, "pass": round(100 * exam.PASS),
                       "levels": {k: v[0] for k, v in exam.QUIZ_LEVELS.items()}, "topics": [
            {"id": tid, "name": names[tid], "pct": round(100 * g / m), "ok": g / m >= exam.PASS, **P[tid],
             "fails": [p["id"] for t in e["tasks"] for p in t["parts"]
                       if (p.get("tid") or t.get("tid")) == tid and res.get(p["id"], {}).get("p", 0) < p["pts"]]}
            for tid, (g, m) in exam._topic_points(e).items() if tid in P and m]} if L else None
    return out


@route("DELETE", "/api/courses/([^/]+)/exams/([^/]+)")
def r_exam_del(h, cid, eid):
    store.exam_path(cid, eid).unlink(missing_ok=True)
    return {"ok": True}


@route("PUT", "/api/courses/([^/]+)/exams/([^/]+)/answers")
def r_answers(h, cid, eid):
    e = store.get_exam(cid, eid)
    if e["status"] == "graded":
        return {"ok": False}
    body = h.json()
    e["answers"] = body.get("answers", {})
    e.setdefault("started", time.time())
    e["elapsed"] = body.get("elapsed", e.get("elapsed", 0))
    store.save_exam(cid, e)
    return {"ok": True}


@route("POST", "/api/courses/([^/]+)/exams/([^/]+)/files")
def r_file(h, cid, eid):
    name = re.sub(r"[^\w.-]", "_", urllib.parse.unquote(h.headers.get("X-Filename") or "bild.jpg"))
    name = store.new_id("f") + "_" + name[-40:]
    (store.upload_dir(cid, eid) / name).write_bytes(h.body())
    return {"name": name}


@route("GET", "/api/courses/([^/]+)/exams/([^/]+)/files/([^/]+)")
def r_file_get(h, cid, eid, name):
    f = store.upload_dir(cid, eid) / Path(name).name
    return h.send_file(f)


@route("POST", "/api/courses/([^/]+)/exams/([^/]+)/grade")
def r_grade(h, cid, eid):
    body = h.json()
    e = store.get_exam(cid, eid)
    if body.get("answers") is not None:
        e["answers"] = body["answers"]
    e["elapsed"] = body.get("elapsed", e.get("elapsed", 0))
    up = store.upload_dir(cid, eid)
    for pid, url in (body.get("sketches") or {}).items():
        if url and url.startswith("data:image"):
            fn = "sketch_" + re.sub(r"[^\w]", "", pid) + ".jpg"
            (up / fn).write_bytes(base64.b64decode(url.split(",", 1)[1]))
            e["answers"].setdefault(pid, {})["sketch_img"] = fn
    e["submitted"] = time.time()
    store.save_exam(cid, e)

    def fn(p):
        exam.grade(store.get_course(cid), store.get_exam(cid, eid), store.get_settings(), p)
        return eid
    return start_job("grade", fn)


@route("POST", "/api/courses/([^/]+)/exams/([^/]+)/override")
def r_override(h, cid, eid):
    b = h.json()
    return exam.override(store.get_course(cid), store.get_exam(cid, eid), b["pid"], b["points"])


@route("POST", "/api/courses/([^/]+)/exams/([^/]+)/retake")
def r_retake(h, cid, eid):
    e = store.get_exam(cid, eid)
    for k in ("result", "graded", "submitted", "started", "elapsed"):
        e.pop(k, None)
    e.update({"id": store.new_id("e"), "created": time.time(), "status": "open", "answers": {},
              "title": e["title"] + " (Wdh.)", "usage": {}})
    store.save_exam(cid, e)
    return {"id": e["id"]}


@route("GET", "/api/jobs/([^/]+)")
def r_job(h, jid):
    j = JOBS.get(jid)
    if not j:
        raise KeyError("Job unbekannt")
    return {**j, "elapsed": round(time.time() - j["t0"])}


@route("GET", "/api/usage")
def r_usage(h):
    return usage_summary()


@route("GET", "/api/settings")
def r_settings(h):
    return {**store.get_settings(), "claude": claude_cli.find_claude()}


@route("PUT", "/api/settings")
def r_settings_put(h):
    return store.save_settings(h.json())


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        if "/api/jobs/" not in (args[0] if args else ""):
            sys.stderr.write("%s\n" % (fmt % args))

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def json(self):
        b = self.body()
        return json.loads(b) if b else {}

    def send(self, code, obj=None, raw=None, ctype="application/json; charset=utf-8"):
        data = raw if raw is not None else json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)
        return _SENT

    def send_file(self, f):
        f = Path(f)
        if not f.is_file():
            return self.send(404, {"error": "nicht gefunden"})
        ct = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
        return self.send(200, raw=f.read_bytes(), ctype=ct)

    def handle_any(self, method):
        path = urllib.parse.urlparse(self.path).path
        if path.startswith("/api/"):
            for m, rx, fn in ROUTES:
                mt = rx.match(path)
                if m == method and mt:
                    try:
                        res = fn(self, *mt.groups())
                        if res is not _SENT:
                            self.send(200, res)
                    except KeyError as e:
                        self.send(404, {"error": str(e).strip("'")})
                    except (ValueError, extract.ExtractError, claude_cli.ClaudeError) as e:
                        self.send(400, {"error": str(e)})
                    except Exception as e:  # noqa: BLE001
                        traceback.print_exc()
                        self.send(500, {"error": str(e)})
                    return
            return self.send(404, {"error": "unbekannte Route"})
        if method != "GET":
            return self.send(405, {"error": "method"})
        f = (STATIC / (path.lstrip("/") or "index.html")).resolve()
        if not str(f).startswith(str(STATIC.resolve())) or not f.is_file():
            f = STATIC / "index.html"
        self.send_file(f)

    def do_GET(self):
        self.handle_any("GET")

    def do_POST(self):
        self.handle_any("POST")

    def do_PUT(self):
        self.handle_any("PUT")

    def do_PATCH(self):
        self.handle_any("PATCH")

    def do_DELETE(self):
        self.handle_any("DELETE")


_SENT = object()


def main():
    mimetypes.add_type("text/javascript", ".js")
    mimetypes.add_type("font/woff2", ".woff2")
    port = next((int(a) for a in sys.argv[1:] if a.isdigit()), PORT)
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}"
    print(f"Probeklausur läuft auf {url}  (Strg+C zum Beenden)")
    if "--no-browser" not in sys.argv:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
