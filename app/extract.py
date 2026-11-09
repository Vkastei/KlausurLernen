"""Local text extraction -> chunks. No tokens are spent here.

Chunk: {"t": title, "x": text, "v": needs_vision, "code": lang?, "img": [rel paths]?}
"""
import base64
import json
import re
from collections import Counter
from pathlib import Path

CODE_EXT = {
    ".py": "python", ".java": "java", ".c": "c", ".h": "c", ".cpp": "cpp", ".hpp": "cpp", ".cc": "cpp",
    ".cs": "csharp", ".js": "javascript", ".ts": "typescript", ".jsx": "javascript", ".tsx": "typescript",
    ".go": "go", ".rs": "rust", ".kt": "kotlin", ".swift": "swift", ".rb": "ruby", ".php": "php",
    ".sql": "sql", ".hs": "haskell", ".scala": "scala", ".r": "r", ".m": "matlab", ".sh": "bash",
    ".ps1": "powershell", ".asm": "asm", ".s": "asm", ".vhd": "vhdl", ".v": "verilog", ".pl": "prolog",
    ".lisp": "lisp", ".scm": "scheme", ".ml": "ocaml", ".dart": "dart", ".lua": "lua", ".jl": "julia",
    ".css": "css", ".xml": "xml", ".yaml": "yaml", ".yml": "yaml", ".toml": "toml",
}
TEXT_EXT = {".txt", ".md", ".markdown", ".tex", ".csv", ".json", ".rst", ".log", ".html", ".htm"}
IMG_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
MAX_CHUNK = 3500  # chars


class ExtractError(Exception):
    pass


def kind_of(name):
    ext = Path(name).suffix.lower()
    if ext == ".pdf":
        return "pdf"
    if ext in (".pptx", ".pptm"):
        return "pptx"
    if ext in (".docx", ".docm"):
        return "docx"
    if ext == ".ipynb":
        return "ipynb"
    if ext in IMG_EXT:
        return "image"
    if ext in CODE_EXT:
        return "code"
    if ext in TEXT_EXT:
        return "text"
    if ext in (".ppt", ".doc", ".xls"):
        raise ExtractError(f"Altes Office-Format {ext} – bitte als .pptx/.docx oder PDF speichern.")
    return "text"  # try as text


def clean(t):
    t = t.replace("\u00ad", "").replace("\u00a0", " ")
    t = re.sub(r"(\w)-\n([a-zäöüß])", r"\1\2", t)          # hyphenation
    t = re.sub(r"[ \t\f\v]+", " ", t)
    t = "\n".join(l.strip() for l in t.split("\n"))
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def split_long(title, text, limit=MAX_CHUNK):
    if len(text) <= limit:
        return [(title, text)]
    parts, cur = [], ""
    for para in re.split(r"\n\s*\n", text):
        if cur and len(cur) + len(para) > limit:
            parts.append(cur)
            cur = ""
        cur = (cur + "\n\n" + para) if cur else para
        while len(cur) > limit * 1.5:
            parts.append(cur[:limit])
            cur = cur[limit:]
    if cur:
        parts.append(cur)
    return [(title if i == 0 else f"{title} (Forts.)", p) for i, p in enumerate(parts)]


def extract(path, name, img_dir):
    """img_dir: where page/picture renders for later vision transcription go."""
    k = kind_of(name)
    fn = {"pdf": _pdf, "pptx": _pptx, "docx": _docx, "ipynb": _ipynb, "image": _image,
          "code": _code, "text": _text}[k]
    chunks = fn(Path(path), name, Path(img_dir))
    chunks = [c for c in chunks if c.get("x") or c.get("v")]
    out = {"kind": k, "chunks": chunks}
    if k == "pdf":
        out["figv"] = FIG_VERSION
    return out


# ---------------- PDF ----------------
def _pdf(path, name, img_dir):
    import pymupdf
    with pymupdf.open(path) as doc:  # always close: an open handle blocks deleting the file on Windows
        return _pdf_doc(doc)


def _pdf_doc(doc):
    toc = doc.get_toc(simple=True)  # [level, title, page]
    page_toc = {}
    for lvl, title, pg in toc:
        if lvl <= 2 and pg >= 1:
            page_toc.setdefault(pg - 1, title.strip())
    pages = []
    for i, page in enumerate(doc):
        d = page.get_text("dict")
        lines = []
        for b in d.get("blocks", []):
            for l in b.get("lines", []):
                spans = l.get("spans", [])
                txt = "".join(s["text"] for s in spans).strip()
                if txt:
                    lines.append((max(s["size"] for s in spans), txt))
        pages.append((page, lines))
    # header/footer lines repeated on many pages -> drop
    n = len(pages)
    cnt = Counter()
    for _, lines in pages:
        for t in {re.sub(r"\d+", "#", t) for _, t in lines}:
            cnt[t] += 1
    rep = {t for t, c in cnt.items() if n >= 4 and c >= max(3, n * 0.4) and len(t) < 80}
    flags = figure_flags(doc)
    marked = marker_flags(doc)
    chunks, cur_title = [], ""
    for i, (page, lines) in enumerate(pages):
        keep = [(s, t) for s, t in lines if re.sub(r"\d+", "#", t) not in rep and not re.fullmatch(r"[\d\s/|.\-–]+", t)]
        title = ""
        if keep:
            mx = max(s for s, _ in keep)
            body_size = Counter(round(s) for s, _ in keep).most_common(1)[0][0]
            if mx > body_size + 1.5:
                title = next(t for s, t in keep if s == mx)[:120]
        if i in page_toc:
            cur_title = page_toc[i]
        title = title or cur_title or f"Seite {i + 1}"
        if keep and keep[0][1].strip() == title:
            keep = keep[1:]  # title is already in the chunk header
        text = clean("\n".join(t for _, t in keep))
        c = {"t": title, "x": text, "pg": i}
        apply_figure_flags(c, flags[i])
        if i in marked:
            c["nx"] = True
        chunks.append(c)
    return chunks


FIG_VERSION = 3  # bump when the figure/marker heuristic changes -> existing materials get re-flagged once

CONVENTION = re.compile(r"nicht\s+(klausur|prüfungs|exam)[- ]?relevant\w*\s+(folien|seiten|inhalte|slides)?[^.\n]{0,80}"
                        r"(markiert|gekennzeichnet|symbol|icon|\bmit\b)", re.IGNORECASE)


def marker_flags(doc):
    """Lecturers sometimes mark non-exam-relevant slides with a small icon and explain it once
    ("nicht klausurrelevante Folien sind mit [icon] unten rechts markiert"). Find that icon on the
    explanation page and every page carrying an identical image. Returns a set of page indices."""
    import hashlib
    conv = [i for i, p in enumerate(doc) if CONVENTION.search(p.get_text())]
    if not conv:
        return set()

    cache = {}

    def h(x):
        if x not in cache:
            try:
                cache[x] = hashlib.md5(doc.extract_image(x)["image"]).hexdigest()
            except Exception:
                cache[x] = None
        return cache[x]
    cand = set()
    for i in conv:
        page = doc[i]
        area = abs(page.rect) or 1
        for im in page.get_images(full=True):
            try:
                a = sum(abs(r) for r in page.get_image_rects(im[0])) / area
            except Exception:
                continue
            if 0 < a < 0.015 and h(im[0]):
                cand.add(h(im[0]))
    if not cand:
        return set()
    hits = {}
    for i, page in enumerate(doc):
        if i in conv:
            continue
        for im in page.get_images(full=True):
            hh = h(im[0])
            if hh in cand:
                hits.setdefault(hh, set()).add(i)
    n = len(doc)
    out = set()
    for pages in hits.values():
        if 2 <= len(pages) < 0.6 * n:  # a logo on (almost) every page is not a marker
            out |= pages
    return out


def figure_flags(doc):
    """Per page: is there a REAL figure (not a logo / template decoration)?
    - images used on >= 30 % of pages (logos, backgrounds) are ignored
    - a remaining image counts if it covers >= 8 % of the page
    - vector drawings: template shapes repeated across pages are ignored; >= 8 own shapes spanning
      >= 5 % of the page count as a diagram (UML, charts, tables drawn with lines ...)"""
    import pymupdf
    n = len(doc)
    img_pages, sig_pages, per = Counter(), Counter(), []
    for page in doc:
        xr = {im[0] for im in page.get_images(full=True)}
        try:
            dr = page.get_drawings()
        except Exception:
            dr = []
        sigs = [tuple(round(v) for v in d["rect"]) for d in dr]
        per.append((xr, dr, sigs))
        img_pages.update(xr)
        sig_pages.update(set(sigs))
    lim = max(3, 0.3 * n) if n >= 4 else float("inf")
    out = []
    for page, (xr, dr, sigs) in zip(doc, per):
        area = abs(page.rect) or 1
        own_imgs = [x for x in xr if img_pages[x] < lim]
        img_area = 0.0
        for x in own_imgs:
            try:
                img_area += sum(abs(pymupdf.Rect(r) & page.rect) for r in page.get_image_rects(x))
            except Exception:
                pass
        own = [d for d, s in zip(dr, sigs) if sig_pages[s] < lim]
        vec = False
        if len(own) >= 8:
            bb = pymupdf.Rect(own[0]["rect"])
            for d in own[1:]:
                bb |= d["rect"]
            vec = abs(bb & page.rect) / area >= 0.05
        out.append({"fig": img_area / area >= 0.08 or vec, "img": bool(own_imgs) or vec})
    return out


def apply_figure_flags(c, f):
    """v  = page is (almost) only a picture -> must be transcribed to be usable
       vi = page has text AND a real figure -> figure can optionally be transcribed"""
    c.pop("v", None)
    c.pop("vi", None)
    if len(c.get("x", "")) < 60 and f["img"]:
        c["v"] = True
    elif f["fig"]:
        c["vi"] = True


def refresh_pdf_figures(path, data):
    """One-time re-flagging of an already extracted PDF (keeps text, marks and transcriptions)."""
    import pymupdf
    with pymupdf.open(path) as doc:  # close the file -> Windows can delete it later
        flags = figure_flags(doc)
        marked = marker_flags(doc)
    for c in data["chunks"]:
        pg = c.get("pg")
        if pg is None or pg >= len(flags):
            continue
        if pg in marked:
            c["nx"] = True  # carries the lecturer's "not exam-relevant" marker
        else:
            c.pop("nx", None)
        if not c.get("tr"):
            apply_figure_flags(c, flags[pg])
    data["figv"] = FIG_VERSION
    return data


VISION_PX = 1100  # max edge for images sent to Claude; slides stay readable, ~40 % fewer tokens than 1400


def render_pdf_page(path, pg, max_px=VISION_PX, clip=None):
    """clip: [x0, y0, x1, y1] normalized to 0..1 -> only that region (a figure), with a small margin."""
    import pymupdf
    with pymupdf.open(path) as doc:
        page = doc[pg]
        r = page.rect
        if clip:
            W, H, m = r.width, r.height, 0.01
            r = pymupdf.Rect((clip[0] - m) * W, (clip[1] - m) * H, (clip[2] + m) * W, (clip[3] + m) * H) & page.rect
        s = min(4.0, max_px / max(r.width, r.height, 1))
        pix = page.get_pixmap(matrix=pymupdf.Matrix(s, s), clip=r if clip else None)
        return pix.tobytes("jpeg", jpg_quality=75), "image/jpeg"


LAYOUT_VERSION = 1


def pdf_layout(path):
    """Per page (normalized 0..1): {"ar": width/height, "text": [[x0, y0, x1, y1, line], ...],
    "figs": [[x0, y0, x1, y1], ...]} -- figs = real figures (images, vector diagrams); logos and
    template shapes repeated on many pages are left out (same rule as figure_flags)."""
    import pymupdf
    with pymupdf.open(path) as doc:
        n = len(doc)
        img_pages, sig_pages, per = Counter(), Counter(), []
        for page in doc:
            xr = {im[0] for im in page.get_images(full=True)}
            try:
                dr = page.get_drawings()
            except Exception:
                dr = []
            sigs = [tuple(round(v) for v in d["rect"]) for d in dr]
            per.append((xr, dr, sigs))
            img_pages.update(xr)
            sig_pages.update(set(sigs))
        lim = max(3, 0.3 * n) if n >= 4 else float("inf")
        out = []
        for page, (xr, dr, sigs) in zip(doc, per):
            pr = page.rect
            W, H = pr.width or 1, pr.height or 1
            area = W * H

            def norm(r):
                return [round(max(0, r.x0) / W, 4), round(max(0, r.y0) / H, 4),
                        round(min(W, r.x1) / W, 4), round(min(H, r.y1) / H, 4)]
            lines = []
            for b in page.get_text("dict").get("blocks", []):
                for ln in b.get("lines", []):
                    t = "".join(s["text"] for s in ln.get("spans", [])).strip()
                    if t:
                        lines.append(norm(pymupdf.Rect(ln["bbox"])) + [t])
            rects = []
            for x in xr:
                if img_pages[x] >= lim:
                    continue
                try:
                    rs = page.get_image_rects(x)
                except Exception:
                    continue
                for r in rs:
                    r = pymupdf.Rect(r) & pr
                    if abs(r) / area >= 0.015:
                        rects.append(r)
            # vector diagrams: cluster the page's own shapes by proximity
            pad = 0.02 * max(W, H)
            groups = []  # [padded bbox, shape count, bbox]
            for d, s in zip(dr, sigs):
                if sig_pages[s] >= lim:
                    continue
                r = pymupdf.Rect(d["rect"])
                g = [pymupdf.Rect(r.x0 - pad, r.y0 - pad, r.x1 + pad, r.y1 + pad), 1, r]
                hit = True
                while hit:
                    hit = False
                    for o in groups:
                        if o[0].intersects(g[0]):
                            g = [o[0] | g[0], o[1] + g[1], o[2] | g[2]]
                            groups.remove(o)
                            hit = True
                            break
                groups.append(g)
            for _, k, r in groups:
                r = r & pr
                if k >= 6 and abs(r) / area >= 0.03:
                    rects.append(r)
            # merge overlapping regions (an image inside a drawn frame, a diagram made of several pictures)
            hit = True
            while hit:
                hit = False
                for a in range(len(rects)):
                    for b in range(a + 1, len(rects)):
                        inter = abs(rects[a] & rects[b])
                        if inter and inter >= 0.3 * min(abs(rects[a]), abs(rects[b])):
                            rects[a] = rects[a] | rects[b]
                            del rects[b]
                            hit = True
                            break
                    if hit:
                        break
            rects.sort(key=lambda r: (round(r.y0 / H, 1), r.x0))
            out.append({"ar": round(W / H, 4), "text": lines, "figs": [norm(r) for r in rects if abs(r) / area < 0.97 or not lines]})
    return out


def shrink_image(data, max_px=VISION_PX):
    """Re-encode image bytes as JPEG with max edge max_px (fewer vision tokens)."""
    import pymupdf
    try:
        with pymupdf.open(stream=data) as doc:
            page = doc[0]
            r = page.rect
            s = min(1.0, max_px / max(r.width, r.height)) if max(r.width, r.height) > 0 else 1
            pix = page.get_pixmap(matrix=pymupdf.Matrix(s, s))
            return pix.tobytes("jpeg", jpg_quality=75), "image/jpeg"
    except Exception:
        return data, "image/png"


# ---------------- PPTX ----------------
def _pptx(path, name, img_dir):
    from pptx import Presentation
    from pptx.enum.shapes import MSO_SHAPE_TYPE
    prs = Presentation(str(path))
    chunks = []
    img_dir.mkdir(parents=True, exist_ok=True)

    def walk(shapes, out, pics):
        for sh in shapes:
            if sh.shape_type == MSO_SHAPE_TYPE.GROUP:
                walk(sh.shapes, out, pics)
                continue
            if sh.has_text_frame:
                t = sh.text_frame.text.strip()
                if t:
                    out.append(t)
            if getattr(sh, "has_table", False) and sh.has_table:
                for row in sh.table.rows:
                    out.append(" | ".join(c.text.strip() for c in row.cells))
            if sh.shape_type == MSO_SHAPE_TYPE.PICTURE:
                try:
                    im = sh.image
                    if im.ext.lower() in ("png", "jpg", "jpeg", "gif", "bmp") and len(im.blob) > 8000:
                        pics.append(im)
                except Exception:
                    pass

    import hashlib
    slides = list(prs.slides)
    collected = []
    for slide in slides:
        out, pics = [], []
        walk(slide.shapes, out, pics)
        collected.append((out, pics))
    # pictures that appear on many slides (logos, decoration) are not figures
    seen = Counter(h for _, pics in collected for h in {hashlib.sha1(p.blob).hexdigest() for p in pics})
    lim = max(3, 0.3 * len(slides)) if len(slides) >= 4 else float("inf")

    for i, slide in enumerate(slides):
        title = ""
        if slide.shapes.title is not None and slide.shapes.title.has_text_frame:
            title = slide.shapes.title.text_frame.text.strip()
        out, pics = collected[i]
        pics = [p for p in pics if seen[hashlib.sha1(p.blob).hexdigest()] < lim]
        if title and out and out[0] == title:
            out = out[1:]
        if slide.has_notes_slide:
            nt = slide.notes_slide.notes_text_frame.text.strip() if slide.notes_slide.notes_text_frame else ""
            if nt:
                out.append("Notizen: " + nt)
        text = clean("\n".join(out))
        c = {"t": title[:120] or f"Folie {i + 1}", "x": text, "pg": i}
        if pics:
            rels = []
            for j, im in enumerate(sorted(pics, key=lambda p: -len(p.blob))[:3]):
                p = img_dir / f"s{i}_{j}.{im.ext}"
                p.write_bytes(im.blob)
                rels.append(p.name)
            c["img"] = rels
            if len(text) < 60:
                c["v"] = True
            else:
                c["vi"] = True
        chunks.append(c)
    return chunks


# ---------------- DOCX ----------------
def _docx(path, name, img_dir):
    import docx
    import docx.table
    import docx.text.paragraph
    d = docx.Document(str(path))
    sections, title, buf = [], Path(name).stem, []

    def flush():
        if buf:
            sections.extend(split_long(title, clean("\n".join(buf))))

    body = d.element.body
    for el in body.iterchildren():
        tag = el.tag.split("}")[-1]
        if tag == "p":
            p = docx.text.paragraph.Paragraph(el, d)
            t = p.text.strip()
            if not t:
                continue
            st = (p.style.name or "").lower() if p.style is not None else ""
            if st.startswith("heading") or st.startswith("überschrift") or st == "title":
                flush()
                buf, title = [], t[:120]
            else:
                pre = "- " if "list" in st or "liste" in st else ""
                buf.append(pre + t)
        elif tag == "tbl":
            tb = docx.table.Table(el, d)
            for row in tb.rows:
                buf.append(" | ".join(c.text.strip() for c in row.cells))
    flush()
    return [{"t": t, "x": x} for t, x in sections]


# ---------------- notebooks / code / text / images ----------------
def _ipynb(path, name, img_dir):
    nb = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    secs, title, buf = [], Path(name).stem, []
    for cell in nb.get("cells", []):
        src = "".join(cell.get("source", []))
        if not src.strip():
            continue
        if cell.get("cell_type") == "markdown":
            m = re.match(r"\s*#+\s*(.+)", src)
            if m:
                if buf:
                    secs.extend(split_long(title, "\n\n".join(buf)))
                buf, title = [], m.group(1).strip()[:120]
            buf.append(src.strip())
        elif cell.get("cell_type") == "code":
            buf.append("```python\n" + src.rstrip() + "\n```")
    if buf:
        secs.extend(split_long(title, "\n\n".join(buf)))
    return [{"t": t, "x": x} for t, x in secs]


def _read_text(path):
    raw = path.read_bytes()
    for enc in ("utf-8", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    raise ExtractError("Datei ist kein lesbarer Text")


def _code(path, name, img_dir):
    lang = CODE_EXT.get(path.suffix.lower(), "")
    src = _read_text(path).replace("\r\n", "\n").rstrip()
    src = re.sub(r"\n{3,}", "\n\n", src)
    return [{"t": name if i == 0 else f"{name} (Forts.)", "x": x, "code": lang}
            for i, (_, x) in enumerate(split_long(name, src, 5000))]


def _text(path, name, img_dir):
    t = _read_text(path).replace("\r\n", "\n")
    if "\x00" in t[:2000]:
        raise ExtractError("Binärdatei – Format wird nicht unterstützt")
    if path.suffix.lower() in (".html", ".htm"):
        t = re.sub(r"(?is)<(script|style).*?</\1>", "", t)
        t = re.sub(r"<[^>]+>", " ", t)
    secs, title, buf = [], Path(name).stem, []
    is_md = path.suffix.lower() in (".md", ".markdown", ".txt", ".rst")
    for line in t.split("\n"):
        m = re.match(r"#{1,3}\s+(.+)", line) if is_md else None
        m = m or (re.match(r"\\(?:section|subsection|chapter)\*?\{(.+?)\}", line) if path.suffix.lower() == ".tex" else None)
        if m:
            if "".join(buf).strip():
                secs.extend(split_long(title, clean("\n".join(buf))))
            buf, title = [], m.group(1).strip()[:120]
        else:
            buf.append(line)
    if "".join(buf).strip():
        secs.extend(split_long(title, clean("\n".join(buf))))
    return [{"t": t, "x": x} for t, x in secs]


def _image(path, name, img_dir):
    img_dir.mkdir(parents=True, exist_ok=True)
    data, _ = shrink_image(path.read_bytes())
    (img_dir / "0.jpg").write_bytes(data)
    return [{"t": Path(name).stem, "x": "", "v": True, "img": ["0.jpg"]}]


def b64(data):
    return base64.b64encode(data).decode("ascii")
