"""Tiny JSON file storage. Everything lives under ./data (local only)."""
import json
import os
import secrets
import shutil
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
COURSES = DATA / "courses"
SETTINGS_FILE = DATA / "settings.json"
USAGE_FILE = DATA / "usage.jsonl"

_lock = threading.RLock()

DEFAULT_SETTINGS = {
    # full IDs: the CLI aliases ("opus"/"sonnet") may point to older versions depending on the CLI build
    "gen_model": "claude-opus-5-5",       # exam generation
    "grade_model": "claude-sonnet-5-5",   # correction
    "aux_model": "claude-sonnet-5-5",     # topic suggestion + image transcription
    "triage_model": "claude-sonnet-5-5",  # AI pre-check of material (text only)
    "triage_effort": "low",
    "learn_model": "claude-sonnet-5-5",   # Lernzettel + follow-up questions
    "quiz_model": "claude-sonnet-5-5",    # short quizzes + variations of failed tasks
    "learn_effort": "medium",
    "quiz_effort": "low",
    "gen_effort": "medium",     # "" = CLI default
    "grade_effort": "low",
    "aux_effort": "low",
    "max_material_tokens": 40000,  # budget of material sent per exam generation
    "chars_per_token": 2.6,        # auto-calibrated after text-only calls (German + Markdown ~2.5-2.8)
    "default_minutes": 90,
    "ab_split": [15, 45, 40],      # AB I / II / III in % of points
}


def new_id(prefix=""):
    return prefix + time.strftime("%y%m%d%H%M%S") + secrets.token_hex(2)


def read_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def write_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with _lock:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, path)


# ---------- settings ----------
def get_settings():
    s = dict(DEFAULT_SETTINGS)
    s.update(read_json(SETTINGS_FILE, {}) or {})
    return s


def save_settings(patch):
    s = get_settings()
    for k, v in patch.items():
        if k in DEFAULT_SETTINGS:
            s[k] = v
    write_json(SETTINGS_FILE, s)
    return s


# ---------- courses ----------
def course_dir(cid):
    if not cid or any(c in cid for c in "/\\.."):
        raise ValueError("bad id")
    return COURSES / cid


def list_courses():
    out = []
    if COURSES.exists():
        for d in sorted(COURSES.iterdir()):
            c = read_json(d / "course.json")
            if c:
                out.append(c)
    return out


def get_course(cid):
    c = read_json(course_dir(cid) / "course.json")
    if c is None:
        raise KeyError("Kurs nicht gefunden")
    return c


def save_course(c):
    write_json(course_dir(c["id"]) / "course.json", c)


def create_course(name):
    c = {"id": new_id("c"), "name": name or "Neuer Kurs", "created": time.time(),
         "instructions": "", "materials": [], "topics": [], "weak": {}}
    save_course(c)
    return c


def delete_course(cid):
    shutil.rmtree(course_dir(cid), ignore_errors=True)


def material_path(cid, mid, ext=""):
    return course_dir(cid) / "materials" / (mid + ext)


def get_chunks(cid, mid):
    return read_json(material_path(cid, mid, ".json"), {"chunks": []})


def save_chunks(cid, mid, data):
    write_json(material_path(cid, mid, ".json"), data)


# ---------- exams ----------
def exam_path(cid, eid):
    if any(c in eid for c in "/\\.."):
        raise ValueError("bad id")
    return course_dir(cid) / "exams" / (eid + ".json")


def list_exams(cid):
    d = course_dir(cid) / "exams"
    out = []
    if d.exists():
        for p in sorted(d.glob("*.json")):
            e = read_json(p)
            if e:
                out.append(e)
    return out


def get_exam(cid, eid):
    e = read_json(exam_path(cid, eid))
    if e is None:
        raise KeyError("Klausur nicht gefunden")
    return e


def save_exam(cid, e):
    write_json(exam_path(cid, e["id"]), e)


def upload_dir(cid, eid):
    d = course_dir(cid) / "uploads" / eid
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------- usage ----------
def log_usage(row):
    USAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with _lock, open(USAGE_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def read_usage():
    rows = []
    try:
        with open(USAGE_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        rows.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    except FileNotFoundError:
        pass
    return rows
