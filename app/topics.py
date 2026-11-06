"""Topics: local suggestion, keyword retrieval (BM25), outline for cheap Claude grouping,
and budget-limited material selection for exam generation."""
import math
import re
from collections import Counter

from . import store

STOP = set("""der die das und oder ein eine einer eines einem einen ist sind war wird werden wurde mit von zu zum zur
im in an am auf aus bei für fur über unter vor nach durch als auch nicht nur wie was wer wo es sie er wir ihr ich du
dem den des sich so dass daß kann können muss soll man the a an of to in and or is are for on with by as be this that
it from at which not""".split())


def toks(s):
    return [w for w in re.findall(r"[a-zA-ZäöüÄÖÜß0-9_+#]{2,}", s.lower()) if w not in STOP]


def cpt():
    """Characters per token, auto-calibrated from real CLI usage (see claude_cli.calibrate)."""
    try:
        return min(4.0, max(2.0, float(store.get_settings().get("chars_per_token") or 2.6)))
    except (TypeError, ValueError):
        return 2.6


def est_tokens(chars):
    return int(chars / cpt()) + 1


def all_chunks(course):
    """[(cid 'mid:i', material, chunk)] in material order."""
    out = []
    for m in course["materials"]:
        for i, c in enumerate(store.get_chunks(course["id"], m["id"])["chunks"]):
            if c.get("st") != "x":  # excluded pages/slides never leave the machine
                out.append((f"{m['id']}:{i}", m, c))
    return out


# ---------- user marks (optional layer; no marks -> text unchanged, zero extra tokens) ----------
# chunk["st"]: "x" excluded | "rel" relevant | "imp" very important   (absent = normal)
# chunk["mk"]: [{"k": "irr"|"rel"|"imp", "t": marked text, "o": offset hint}]
MARK_WRAP = {"rel": ("⟦", "⟧"), "imp": ("⟦!", "⟧")}


def resolve_marks(x, mk):
    rs = []
    for m in mk or []:
        t = m.get("t") or ""
        if not t or m.get("k") not in ("irr", "rel", "imp"):
            continue
        o = m.get("o", -1)
        s = o if isinstance(o, int) and 0 <= o <= len(x) - len(t) and x[o:o + len(t)] == t else x.find(t)
        if s >= 0:
            rs.append((s, s + len(t), m["k"]))
    out, end = [], -1
    for r in sorted(rs):
        if r[0] >= end:
            out.append(r)
            end = r[1]
    return out


def chunk_text(c):
    """Text as sent to Claude: 'irrelevant' spans cut, important/relevant spans wrapped."""
    x = c.get("x", "")
    rs = resolve_marks(x, c.get("mk"))
    if not rs:
        return x
    out, pos = [], 0
    for s, e, k in rs:
        out.append(x[pos:s])
        if k in MARK_WRAP:
            a, b = MARK_WRAP[k]
            out.append(a + x[s:e] + b)
        pos = e
    out.append(x[pos:])
    return re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]{2,}", " ", "".join(out))).strip()


def chunk_rank(c):
    """0 = very important, 1 = relevant, 2 = normal (used to fill the budget in priority order)."""
    st = c.get("st")
    kinds = {m.get("k") for m in c.get("mk") or []}
    if st == "imp" or "imp" in kinds:
        return 0
    if st == "rel" or "rel" in kinds:
        return 1
    return 2


def has_marks(c):
    return chunk_rank(c) < 2


class BM25:
    def __init__(self, docs):
        self.docs = [Counter(toks(d)) for d in docs]
        self.len = [sum(d.values()) for d in self.docs]
        self.avg = (sum(self.len) / len(self.len)) if self.len else 1
        df = Counter()
        for d in self.docs:
            df.update(d.keys())
        n = len(self.docs)
        self.idf = {w: math.log(1 + (n - f + 0.5) / (f + 0.5)) for w, f in df.items()}

    def scores(self, q, k1=1.4, b=0.75):
        qt = toks(q)
        out = []
        for d, L in zip(self.docs, self.len):
            s = 0.0
            for w in qt:
                f = d.get(w)
                if f:
                    s += self.idf[w] * f * (k1 + 1) / (f + k1 * (1 - b + b * L / self.avg))
            out.append(s)
        return out


# pages that intentionally belong to no single topic (meta / noise) -> never counted as "ohne Thema"
NO_TOPIC_ROLES = {"goals", "examinfo", "org", "lit", "thanks", "divider", "interactive", "dup", "anim", "notexam"}


def needs_topic(c):
    return c.get("st") != "x" and bool(c.get("x")) and c.get("role") not in NO_TOPIC_ROLES


def unassigned(course):
    """[(cid, material, chunk)] of active content pages that no topic covers."""
    assigned = {x for t in course["topics"] for x in t["chunks"]}
    return [(cid, m, c) for cid, m, c in all_chunks(course) if needs_topic(c) and cid not in assigned]


def auto_assign(course, max_gap=3):
    """Assign uncovered pages to existing topics locally (0 tokens). Mutates course["topics"].
    1) neighbours: a page next to / between pages of a topic in the same file belongs to it
       (exercises and solutions usually follow the slides they practise)
    2) keywords: BM25 of the page against each topic (name + titles + text of its pages);
       only clear winners are taken -- unclear pages stay open (for the AI step or the user)
    Returns (number assigned, [remaining cids])."""
    topics = course["topics"]
    todo = unassigned(course)
    if not topics or not todo:
        return 0, [cid for cid, _, _ in todo]
    owner = {}
    for t in topics:
        for x in t["chunks"]:
            owner.setdefault(x, t["id"])
    by_cid = {cid: c for cid, _, c in all_chunks(course)}
    docs = []
    for t in topics:
        parts = [t["name"]] * 3
        for x in t["chunks"][:60]:
            c = by_cid.get(x)
            if c:
                parts.append(c.get("t", "") + " " + c.get("x", "")[:200])
        docs.append(" ".join(parts)[:6000])
    bm = BM25(docs)
    idx = {t["id"]: k for k, t in enumerate(topics)}
    by_tid = {t["id"]: t for t in topics}
    new, rest = {}, []
    for cid, m, c in todo:
        mid, i = cid.rsplit(":", 1)
        i = int(i)
        cands = []
        for d in range(1, max_gap + 1):  # nearest originally assigned neighbours before / after
            if f"{mid}:{i - d}" in owner:
                cands.append(owner[f"{mid}:{i - d}"])
                break
        for d in range(1, max_gap + 1):
            if f"{mid}:{i + d}" in owner:
                cands.append(owner[f"{mid}:{i + d}"])
                break
        sc = bm.scores((c.get("t", "") + " ") * 2 + c.get("x", "")[:500])
        best = max(range(len(topics)), key=lambda k: sc[k])
        ranked = sorted(sc, reverse=True)
        clear = ranked[0] > 0 and ranked[0] >= 1.3 * (ranked[1] if len(ranked) > 1 else 0)
        if c.get("role") in ("exercise", "solution"):
            # exercises often cover a whole lecture: assign locally only if content AND position agree,
            # everything else goes to the AI step (much more reliable for tasks)
            if clear and topics[best]["id"] in cands:
                new[cid] = topics[best]["id"]
            else:
                rest.append(cid)
        elif cands:
            pick = max(cands, key=lambda tid: (sc[idx[tid]], -cands.index(tid)))  # ties -> previous page
            if sc[best] > 0 and sc[best] >= 2 * max(sc[idx[pick]], 0.01) and topics[best]["id"] not in cands:
                pick = topics[best]["id"]  # text clearly points elsewhere
            new[cid] = pick
        elif ranked[0] > 0 and ranked[0] >= 1.3 * (ranked[1] if len(ranked) > 1 else 0):
            new[cid] = topics[best]["id"]
        else:
            rest.append(cid)
    for cid, tid in new.items():
        by_tid[tid]["chunks"].append(cid)
    return len(new), rest


def match_chunks(course, query, limit=25):
    ch = all_chunks(course)
    if not ch:
        return []
    bm = BM25([c["t"] + " " + c["t"] + " " + c.get("x", "") for _, _, c in ch])
    sc = bm.scores(query)
    best = max(sc) if sc else 0
    if best <= 0:
        return []
    ranked = sorted(range(len(ch)), key=lambda i: -sc[i])
    return [ch[i][0] for i in ranked[:limit] if sc[i] >= best * 0.35]


def local_suggest(course):
    """Topics from TOC / headings / slide titles without any tokens."""
    topics = []
    for m in course["materials"]:
        chunks = store.get_chunks(course["id"], m["id"])["chunks"]
        live = [i for i, c in enumerate(chunks) if c.get("st") != "x"]
        groups = []  # consecutive chunks with same normalized title
        for i in live:
            t = re.sub(r"\s*\((Forts\.|\d+)\)\s*$|\s*\d+/\d+$", "", chunks[i]["t"]).strip()
            if re.fullmatch(r"(Seite|Folie) \d+", t):
                t = ""
            if groups and (groups[-1][0] == t or not t):
                groups[-1][1].append(i)
            else:
                groups.append([t or m["name"], [i]])
        if len(groups) > 25:  # too fine-grained (e.g. slide titles) -> one topic per material
            groups = [[m["name"].rsplit(".", 1)[0], live]]
        for t, idx in groups:
            if idx:
                topics.append({"name": t, "chunks": [f"{m['id']}:{i}" for i in idx]})
    return topics


def outline(course, snippet=50):
    """Compact outline for Claude: ~10 tokens per chunk. Excluded chunks are left out.
    Returns (text, alias map)."""
    lines, alias = [], {}
    for mi, m in enumerate(course["materials"], 1):
        a = f"m{mi}"
        alias[a] = m["id"]
        chunks = store.get_chunks(course["id"], m["id"])["chunks"]
        # noise/meta pages (organisational, literature, dividers ...) are not sent: fewer tokens, cleaner topics
        live = [(i, c) for i, c in enumerate(chunks) if c.get("st") != "x" and c.get("role") not in NO_TOPIC_ROLES]
        if not live:
            continue
        lines.append(f"## {a} {m['name']}")
        k = 0
        while k < len(live):
            i, c = live[k]
            j = k
            while j + 1 < len(live) and live[j + 1][1]["t"] == c["t"] and live[j + 1][0] == live[j][0] + 1:
                j += 1
            t = c["t"]
            if re.fullmatch(r"(Seite|Folie) \d+", t) or len(t) < 4:
                t = (t + ": " if t else "") + re.sub(r"\s+", " ", c.get("x", ""))[:snippet]
            if c.get("st") == "imp":
                t += " (wichtig)"
            last = live[j][0]
            lines.append(f"{i}{'-' + str(last) if last > i else ''} {t}")
            k = j + 1
    return "\n".join(lines), alias


def parse_ranges(spec, alias):
    """'m1:3-7,m2:1' -> ['<mid>:3',...]"""
    out = []
    for part in re.split(r"[,;\s]+", spec or ""):
        m = re.fullmatch(r"(m\d+):(\d+)(?:-(\d+))?", part.strip())
        if not m or m.group(1) not in alias:
            continue
        a, b = int(m.group(2)), int(m.group(3) or m.group(2))
        out.extend(f"{alias[m.group(1)]}:{i}" for i in range(a, min(b, a + 400) + 1))
    return out


def select_style(course, topic_ids, budget_tokens):
    """Style references (exercises, old exam questions, solutions marked "sty") for exam generation.
    Priority: selected topics first, tasks before solutions; output in document order."""
    ch = all_chunks(course)
    topics = [t for t in course["topics"] if t["id"] in topic_ids] if topic_ids else []
    in_topics = {x for t in topics for x in t["chunks"]}
    cand = [(cid, c) for cid, _, c in ch if c.get("sty") and c.get("x")]
    order = {cid: i for i, (cid, _, _) in enumerate(ch)}
    cand.sort(key=lambda p: (bool(topics) and p[0] not in in_topics, p[1].get("role") == "solution", order[p[0]]))
    budget, used, out = budget_tokens * cpt(), 0, []
    for cid, c in cand:
        size = len(chunk_text(c)) + 20
        if used + size <= budget:
            out.append(cid)
            used += size
    return sorted(out, key=lambda c: order[c])


def select_material(course, topic_ids, budget_tokens, exclude=()):
    """Pick chunks for the selected topics within the token budget.
    Order of filling: very important -> relevant -> rest (round-robin over topics).
    Deterministic output order -> identical prompt prefix -> prompt-cache hits on repeated runs."""
    ch = all_chunks(course)  # excluded chunks already removed
    by_id = {cid: (m, c) for cid, m, c in ch}
    topics = [t for t in course["topics"] if t["id"] in topic_ids] if topic_ids else []
    if topics:
        lists = [[x for x in t["chunks"] if x in by_id and x not in exclude] for t in topics]
    else:
        lists = [[cid for cid, _, _ in ch if cid not in exclude]]
    size = {cid: len(chunk_text(by_id[cid][1])) + 20 for lst in lists for cid in lst}
    budget = budget_tokens * cpt()
    chosen, used = set(), 0

    def take(cid):
        nonlocal used
        if cid in chosen:
            return
        if used + size[cid] <= budget or not chosen:
            chosen.add(cid)
            used += size[cid]

    for level in (0, 1):  # marked material first
        for lst in lists:
            for cid in lst:
                if chunk_rank(by_id[cid][1]) == level:
                    take(cid)
    # round-robin over topics so every topic gets material
    idx = [0] * len(lists)
    progress = True
    while progress and used < budget:
        progress = False
        for k, lst in enumerate(lists):
            while idx[k] < len(lst) and lst[idx[k]] in chosen:
                idx[k] += 1
            if idx[k] < len(lst):
                take(lst[idx[k]])
                progress = True
                idx[k] += 1
    order = {cid: i for i, (cid, _, _) in enumerate(ch)}
    sel = sorted(chosen, key=lambda c: order[c])
    total = sum(size.values()) - 20 * len(size)
    return sel, by_id, total


def material_text(course, sel, by_id):
    out, last = [], None
    for cid in sel:
        m, c = by_id[cid]
        body = chunk_text(c)
        if not body:
            continue
        if m["id"] != last:
            out.append(f"### {m['name']}")
            last = m["id"]
        if c.get("code"):
            body = f"```{c['code']}\n{body}\n```"
        flag = {"imp": " | SEHR WICHTIG", "rel": " | relevant"}.get(c.get("st"), "")
        out.append(f"[{c['t']}{flag}]\n{body}")
    return "\n\n".join(out)
