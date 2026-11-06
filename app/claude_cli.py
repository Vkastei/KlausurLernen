"""Wrapper around `claude -p` tuned for minimal token overhead.

- own short system prompt (replaces the ~5k-token Claude Code prompt)
- no tools, no MCP, no skills, no settings/CLAUDE.md -> ~130 tokens baseline
- content (text + images) passed via stream-json on stdin: no tool round-trips
- every call is logged to data/usage.jsonl for the token tracker
"""
import json
import re
import shutil
import subprocess
import threading
import time

from . import store

BASE_FLAGS = ["--no-session-persistence", "--tools", "", "--strict-mcp-config",
              "--disable-slash-commands", "--setting-sources", ""]


class ClaudeError(Exception):
    pass


def find_claude():
    return shutil.which("claude") or shutil.which("claude.exe") or shutil.which("claude.cmd")


def run(system, content, model, action, course=None, effort=None, on_progress=None, timeout=1800):
    """Run one single-turn request. content: str or list of content blocks.
    Returns (text, usage_row)."""
    exe = find_claude()
    if not exe:
        raise ClaudeError("Claude CLI nicht gefunden – ist `claude` im PATH?")
    if isinstance(content, str):
        content = [{"type": "text", "text": content}]
    sys_arg = system
    if exe.lower().endswith(".cmd"):
        # batch wrappers mangle special chars in args -> move system prompt into the message
        content = [{"type": "text", "text": "<anweisungen>\n" + system + "\n</anweisungen>"}] + content
        sys_arg = "Folge den Anweisungen im ersten Block."
    args = [exe, "-p", "--model", model, "--system-prompt", sys_arg,
            "--input-format", "stream-json", "--output-format", "stream-json",
            "--verbose", "--include-partial-messages", *BASE_FLAGS]
    if effort:
        args += ["--effort", effort]
    msg = json.dumps({"type": "user", "message": {"role": "user", "content": content}}, ensure_ascii=False)

    cwd = store.DATA / ".claude-cwd"   # empty dir: nothing project-specific gets picked up
    cwd.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    proc = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            cwd=str(cwd), encoding="utf-8", errors="replace",
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    err_buf = []
    threading.Thread(target=lambda: err_buf.append(proc.stderr.read()), daemon=True).start()
    timer = threading.Timer(timeout, proc.kill)
    timer.start()
    try:
        proc.stdin.write(msg + "\n")
        proc.stdin.close()
        result, chars = None, 0
        for line in proc.stdout:
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            t = ev.get("type")
            if t == "stream_event":
                d = (ev.get("event") or {}).get("delta") or {}
                n = len(d.get("text") or d.get("thinking") or d.get("partial_json") or "")
                if n:
                    chars += n
                    if on_progress:
                        on_progress(chars, "thinking" in d)
            elif t == "result":
                result = ev
        proc.wait()
    finally:
        timer.cancel()

    if result is None:
        raise ClaudeError("Keine Antwort von Claude CLI. " + (("".join(err_buf))[-800:] or f"Exit {proc.returncode}"))
    u = result.get("usage") or {}
    row = {
        "ts": time.time(), "action": action, "course": course,
        "model": ",".join((result.get("modelUsage") or {}).keys()) or model,
        "in": u.get("input_tokens", 0), "cache_w": u.get("cache_creation_input_tokens", 0),
        "cache_r": u.get("cache_read_input_tokens", 0), "out": u.get("output_tokens", 0),
        "cost": round(result.get("total_cost_usd") or 0, 5), "ms": int((time.time() - t0) * 1000),
        "ok": not result.get("is_error"),
    }
    if row["ok"]:
        r = calibrate(system, content, row)
        if r:
            row["cpt"] = r
    store.log_usage(row)
    if result.get("is_error"):
        raise ClaudeError("Claude-Fehler: " + str(result.get("result") or result.get("subtype"))[:800])
    return result.get("result") or "", row


CLI_OVERHEAD = 1000  # tokens the CLI adds to every call even with our minimal flags (measured with 5.5 models)


def calibrate(system, content, row):
    """Learn the real characters-per-token ratio from text-only calls, so material budgets and
    estimates match what is actually billed. Returns the measured ratio (or None)."""
    if any(b.get("type") != "text" for b in content):
        return None  # images distort the ratio
    tokens = row["in"] + row["cache_w"] + row["cache_r"] - CLI_OVERHEAD
    if tokens < 3000:  # small calls are dominated by the fixed overhead -> too noisy
        return None
    chars = len(system) + sum(len(b.get("text", "")) for b in content)
    ratio = chars / tokens
    if not 1.5 <= ratio <= 5.0:
        return None
    old = float(store.get_settings().get("chars_per_token") or 2.6)
    store.save_settings({"chars_per_token": round(min(4.0, max(2.0, 0.7 * old + 0.3 * ratio)), 3)})
    return round(ratio, 3)


# ---------- JSON parsing helpers (no --json-schema: it costs ~2k tokens + an extra turn) ----------
_CTRL_FIX = {"\x0c": "\\f", "\x08": "\\b", "\t": "\\t", "\r": "\\r"}


def _fix_math(s):
    """Repair LaTeX whose backslashes were not escaped in JSON (\\frac -> formfeed+'rac')."""
    def fix(m):
        seg = m.group(0)
        for k, v in _CTRL_FIX.items():
            seg = seg.replace(k, v)
        return re.sub(r"\n(?=[a-zA-Z])", r"\\n", seg)
    return re.sub(r"\$\$.+?\$\$|\$[^$]+?\$", fix, s, flags=re.S)


def _walk(o):
    if isinstance(o, str):
        return _fix_math(o) if "$" in o else o
    if isinstance(o, list):
        return [_walk(x) for x in o]
    if isinstance(o, dict):
        return {k: _walk(v) for k, v in o.items()}
    return o


def _parse_sequence(t):
    dec, pos, vals = json.JSONDecoder(strict=False), 0, []
    while pos < len(t):
        while pos < len(t) and t[pos] not in "{[":
            pos += 1
        if pos >= len(t):
            break
        try:
            v, pos = dec.raw_decode(t, pos)
        except json.JSONDecodeError:
            return None
        vals.append(v)
    if not vals:
        return None
    if all(isinstance(v, dict) for v in vals):
        out = {}
        for v in vals:
            for k, x in v.items():
                if k not in out or (not out[k] and x):
                    out[k] = x
                elif isinstance(out[k], str) and isinstance(x, str) and x:
                    out[k] = out[k] + ("," if k == "r" else "\n") + x  # "r" = page ranges -> join with comma
        return out
    return vals[0]


def parse_json(text):
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    starts = [i for i in (t.find("{"), t.find("[")) if i >= 0]
    if not starts:
        raise ClaudeError("Antwort enthielt kein JSON")
    t = t[min(starts):]
    end = max(t.rfind("}"), t.rfind("]"))
    t = t[:end + 1]
    for attempt in range(3):
        try:
            return _walk(json.loads(t, strict=False))
        except json.JSONDecodeError as e:
            if e.msg == "Extra data":  # several JSON values in a row -> read all, merge objects
                merged = _parse_sequence(t)
                if merged is not None:
                    return _walk(merged)
            if attempt == 0:   # trailing commas
                t = re.sub(r",\s*([}\]])", r"\1", t)
            elif attempt == 1:  # invalid escapes like \( \alpha  -> double them
                t = re.sub(r'\\(?!["\\/bfnrtu])', r"\\\\", t)
            else:
                raise ClaudeError(f"JSON konnte nicht gelesen werden: {e}")
