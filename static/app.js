"use strict";
const app = document.getElementById("app");
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const el = html => { const t = document.createElement("template"); t.innerHTML = html.trim(); return t.content.firstElementChild; };
const fmt = n => Math.round(n || 0).toLocaleString("de-DE");
const tok = n => (n = n || 0) >= 1e6 ? (n / 1e6).toFixed(2).replace(".", ",") + " M" : n >= 1e3 ? (n / 1e3).toFixed(1).replace(".", ",") + " k" : String(Math.round(n));
const pct = x => x == null ? "–" : Math.round(x) + " %";
const AB = ["", "I", "II", "III"];
const TYPES = { sc: "Single Choice", mc: "Multiple Choice", tf: "Wahr/Falsch", cloze: "Lückentext", order: "Reihenfolge", match: "Zuordnung", num: "Rechenaufgabe", short: "Kurzantwort", long: "Freitext", code: "Code", draw: "Zeichnung" };
const LOCAL = new Set(["sc", "mc", "tf", "cloze", "order", "match"]);
const ls = { get(k, d) { try { const v = localStorage.getItem(k); return v ? JSON.parse(v) : d; } catch { return d; } }, set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch {} } };
let cleanups = [];
let CPT = 2.6; // characters per token, auto-calibrated on the server from real usage

async function api(method, url, body) {
  const opt = { method, headers: {} };
  if (body !== undefined) { opt.headers["Content-Type"] = "application/json"; opt.body = JSON.stringify(body); }
  const r = await fetch(url, opt);
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || r.statusText);
  return j;
}
async function upload(url, name, blob) {
  const r = await fetch(url, { method: "POST", headers: { "X-Filename": encodeURIComponent(name) }, body: blob });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || r.statusText);
  return j;
}
function toast(msg, err) {
  const t = el(`<div class="toast-msg ${err ? "err" : ""}"></div>`);
  t.textContent = msg;
  $("#toast").appendChild(t);
  setTimeout(() => t.remove(), err ? 8000 : 3500);
}
async function poll(jid, onProg) {
  for (;;) {
    await new Promise(r => setTimeout(r, 1000));
    const j = await api("GET", "/api/jobs/" + jid);
    if (onProg) onProg(j);
    if (j.status === "done") return j.result;
    if (j.status === "error") throw new Error(j.error);
  }
}
async function toJpeg(file, max = 1600) {
  const bmp = await createImageBitmap(file);
  const s = Math.min(1, max / Math.max(bmp.width, bmp.height));
  const c = document.createElement("canvas");
  c.width = Math.round(bmp.width * s); c.height = Math.round(bmp.height * s);
  const g = c.getContext("2d");
  g.fillStyle = "#fff"; g.fillRect(0, 0, c.width, c.height); g.drawImage(bmp, 0, 0, c.width, c.height);
  return new Promise(r => c.toBlob(r, "image/jpeg", 0.85));
}
function modal(html, onClose, cls = "") {
  const bg = el(`<div class="modal-bg"><div class="modal ${cls}">${html}</div></div>`);
  const close = () => { bg.remove(); document.removeEventListener("keydown", onKey); if (onClose) onClose(); };
  const onKey = e => { if (e.key === "Escape") close(); };
  bg.addEventListener("click", e => { if (e.target === bg || e.target.closest("[data-close]")) close(); });
  document.addEventListener("keydown", onKey);
  document.body.appendChild(bg);
  bg.close = close;
  return bg;
}
function mathPreview(ta) {
  const pv = el(`<div class="preview md small" hidden></div>`);
  ta.after(pv);
  let t;
  const upd = () => { clearTimeout(t); t = setTimeout(() => { const v = ta.value; const on = /\$|\\\(|\\\[/.test(v); pv.hidden = !on; if (on) pv.innerHTML = md(v); }, 300); };
  ta.addEventListener("input", upd); upd();
}

const KIND = { quiz: "Quiz", drill: "Variation" };
async function runVariants(cid, eid, pids, btn) {
  const label = btn.innerHTML; btn.disabled = true;
  try {
    const j = await api("POST", `/api/courses/${cid}/exams/${eid}/variants`, { pids });
    const nid = await poll(j.id, s => { const [n, th] = s.progress || []; btn.innerHTML = `<span class="spinner"></span> ${th && !n ? "denkt nach" : "erstelle Variationen"}${n ? ` · ${fmt(n)} Zeichen` : ""} · ${s.elapsed} s`; });
    location.hash = `#/c/${cid}/e/${nid}`;
  } catch (e) { toast(e.message, true); btn.innerHTML = label; btn.disabled = false; }
}
async function startGen(cid, cfg, st, btn) {
  btn.disabled = true;
  try {
    const j = await api("POST", `/api/courses/${cid}/exams`, cfg);
    const eid = await poll(j.id, s => { const [n, th] = s.progress || []; st.innerHTML = `<span class="spinner"></span> ${n ? (th ? "denkt nach" : "schreibt") + ` · ${fmt(n)} Zeichen` : "startet"} · ${s.elapsed} s`; });
    location.hash = `#/c/${cid}/e/${eid}`;
    return true;
  } catch (e) { toast(e.message, true); st.textContent = ""; return false; } finally { btn.disabled = false; }
}
// Lernzettel self-test answers are written as ||answer|| -> click-to-reveal (only after the "Selbsttest" heading, never in code)
function spoilers(html) {
  const i = html.search(/<h2[^>]*>\s*Selbsttest/i);
  if (i < 0) return html;
  const tail = html.slice(i).split(/(<pre[\s\S]*?<\/pre>)/).map(s => s.startsWith("<pre") ? s : s.replace(/\|\|([\s\S]+?)\|\|/g, '<span class="spoiler" title="Klicken zum Aufdecken">$1</span>')).join("");
  return html.slice(0, i) + tail;
}
document.addEventListener("click", e => { const s = e.target.closest(".spoiler"); if (s) s.classList.toggle("open"); });

// ---------------- router ----------------
async function route() {
  cleanups.forEach(f => f()); cleanups = [];
  try { CPT = (await api("GET", "/api/settings")).chars_per_token || CPT; } catch {}
  const p = (location.hash.slice(1) || "/").split("/").filter(Boolean);
  window.scrollTo(0, 0);
  try {
    if (!p.length) await viewHome();
    else if (p[0] === "c" && p[2] === "e") await viewExam(p[1], p[3]);
    else if (p[0] === "c" && p[2] === "t") await viewTopic(p[1], p[3]);
    else if (p[0] === "c") await viewCourse(p[1]);
    else if (p[0] === "usage") await viewUsage();
    else if (p[0] === "settings") await viewSettings();
    else await viewHome();
  } catch (e) { app.innerHTML = `<div class="card"><b>Fehler:</b> ${esc(e.message)}</div>`; }
}
window.addEventListener("hashchange", route);
window.addEventListener("DOMContentLoaded", route);

// ---------------- home ----------------
async function viewHome() {
  const cs = await api("GET", "/api/courses");
  app.innerHTML = `<h1>Kurse</h1>
  <form class="card row" id="nc"><input name="n" class="grow" placeholder="Neuer Kurs, z. B. Datenbanksysteme WS 26/27" required><button class="primary">Anlegen</button></form>
  ${cs.map(c => `<a class="card row between" href="#/c/${c.id}" style="color:inherit">
      <div><b>${esc(c.name)}</b><div class="small muted">${c.materials} Dateien · ${c.topics} Themen</div></div>
      <div style="text-align:right"><div class="hero" style="font-size:1.6rem">${pct(c.mastery)}</div><div class="tiny muted">verstanden</div></div></a>`).join("")
    || `<p class="muted">Noch keine Kurse. Lege einen an und ziehe dein Material hinein.</p>`}`;
  $("#nc").onsubmit = async e => { e.preventDefault(); const c = await api("POST", "/api/courses", { name: e.target.n.value }); location.hash = "#/c/" + c.id; };
}

// ---------------- course ----------------
async function viewCourse(cid) {
  let C = await api("GET", "/api/courses/" + cid);
  const selKey = "sel_" + cid, cfgKey = "cfg_" + cid;
  const S = await api("GET", "/api/settings");
  let sel = new Set(ls.get(selKey, C.topics.map(t => t.id)));
  const cfg = Object.assign({ minutes: S.default_minutes, ab: S.ab_split, types: Object.keys(TYPES), focus_weak: false, extra: "" }, ls.get(cfgKey, {}));
  const saveSel = () => ls.set(selKey, [...sel]);
  const saveCfg = () => ls.set(cfgKey, { minutes: cfg.minutes, ab: cfg.ab, types: cfg.types, focus_weak: cfg.focus_weak });

  app.innerHTML = `<div class="row between"><h1 id="cname" title="Doppelklick zum Umbenennen">${esc(C.name)}</h1><button class="ghost small" id="cdel">Kurs löschen</button></div>
  <div class="grid2"><div>
    <section class="card"><h2>Material</h2>
      <div class="drop" id="drop">Dateien oder Ordner hierher ziehen<br><span class="small">PDF, PowerPoint, Word, Bilder, Code, Notebooks, Text · oder <a href="#" id="pick">Dateien wählen</a> / <a href="#" id="pickd">Ordner wählen</a></span></div>
      <input type="file" id="fi" multiple hidden><input type="file" id="fd" webkitdirectory hidden>
      <div id="upst" class="small muted"></div><div id="mguide"></div><ul class="list" id="mats"></ul></section>
    <section class="card"><div class="row between"><h2>Themen</h2><div class="row">
      <button class="small" id="tloc" title="Aus Inhaltsverzeichnis/Überschriften – 0 Tokens">Aus Gliederung</button>
      <button class="small" id="tai">Mit Claude gruppieren</button>
      <button class="small" id="tgoal" hidden title="Themen aus den Lernziel-Seiten des Materials ableiten – entspricht meist dem Prüfungsumfang">Aus Lernzielen</button></div></div>
      <form class="row" id="tadd" style="margin-bottom:8px"><input name="n" class="grow" placeholder="Thema hinzufügen (mehrere mit ; trennen)"><button>+</button></form>
      <div class="row small" style="margin-bottom:4px"><span class="muted">Auswahl für Klausur:</span><a href="#" id="sall">alle</a><a href="#" id="snone">keine</a><a href="#" id="sweak">schwächste 5</a></div>
      <div id="thint"></div><ul class="list" id="tops"></ul></section>
    <section class="card"><h2>Kursvorgaben</h2>
      <textarea id="ins" rows="3" placeholder="Gilt für jede Klausur dieses Kurses, z. B.: Hilfsmittel keine · Prof. legt Wert auf Herleitungen · immer eine SQL-Aufgabe · Notation wie in VL"></textarea></section>
  </div><div>
    <section class="card" id="mas"></section>
    <section class="card"><h2>Neue Probeklausur</h2>
      <div class="row" style="margin-bottom:10px"><label>Dauer <input type="number" id="gmin" min="10" max="240" step="5"> min</label>
        <span class="grow"></span><span class="small muted">AB</span>
        <label class="small">I <input type="number" id="ab1" min="0" max="100" style="width:58px"></label>
        <label class="small">II <input type="number" id="ab2" min="0" max="100" style="width:58px"></label>
        <label class="small">III <input type="number" id="ab3" min="0" max="100" style="width:58px"></label><span class="small muted">%</span></div>
      <div class="row" id="gtypes" style="margin-bottom:10px"></div>
      <label class="chk small" style="margin-bottom:8px"><input type="checkbox" id="gweak"> Bekannte Schwächen gezielt erneut prüfen</label>
      <textarea id="gextra" rows="2" placeholder="Zusätzliche Hinweise nur für diese Klausur (optional)"></textarea>
      <div class="small muted" id="gest" style="margin:8px 0">…</div>
      <div class="row"><button class="primary" id="ggo">Probeklausur erstellen</button>
        <button id="gquiz" title="10 min, 4–6 kurze Aufgaben auf der Lernpfad-Stufe der gewählten Themen (bei mehreren die niedrigste) – Schritt für Schritt geht es pro Thema auf der Themenseite">Kurz-Quiz (10 min)</button>
        <span id="gst" class="small muted"></span></div></section>
    <section class="card"><h2>Verlauf</h2><ul class="list" id="hist"></ul></section>
  </div></div>`;

  // --- header
  $("#cname").ondblclick = async () => { const n = prompt("Kursname:", C.name); if (n) { C = await api("PATCH", "/api/courses/" + cid, { name: n }); $("#cname").textContent = C.name; } };
  $("#cdel").onclick = async () => { if (confirm(`Kurs „${C.name}“ mit allen Dateien und Klausuren löschen?`)) { await api("DELETE", "/api/courses/" + cid); location.hash = "#/"; } };

  // --- materials
  // Setup steps per file, in the order that saves tokens and avoids rework:
  // ② clean up (exclude noise) → ③ images (only the pages that are left) → ④ topics (so every page is covered)
  const setupSteps = m => [
    { k: "clean", n: "②", label: "Bereinigen", done: !m.sugg,
      act: m.sugg ? { a: "sugg", t: `💡 ${m.sugg} Vorschläge prüfen`, tip: "Organisatorisches, Literatur, Duplikate, laut Dozent nicht Klausurrelevantes ausschließen; Lernziele markieren" } : null,
      opt: m.triage && m.triage.pages && m.triaged < m.live ? { a: "triage", t: `🔎 KI-Vorprüfung (≈ ${tok(m.triage.est_in)})`, tip: "Claude prüft Titel + Textanfang (keine Bilder) und verfeinert die Vorschläge" } : null },
    { k: "img", n: "③", label: "Bilder", done: !m.vision,
      act: m.vision ? { a: "pick-v", t: `🖼 ${m.vision} Seiten ohne Text auswählen`, tip: "Diese Seiten sind nur ein Bild – ohne Transkription fehlen sie in Klausuren und Lernzetteln" } : null,
      opt: m.figs ? { a: "pick-vi", t: `Abbildungen (${m.figs}) – optional`, tip: "Seiten mit Text und echter Abbildung: nur die Abbildung beschreiben lassen" } : null },
    { k: "topics", n: "④", label: "Themen", done: C.topics.length > 0 && !m.unassigned,
      act: !C.topics.length ? { a: "topics", t: "Themen erstellen ↓", tip: "Im Bereich „Themen“ unten" } : m.unassigned ? { a: "assign", t: `${m.unassigned} ohne Thema zuordnen`, tip: "Erst lokal (0 Tokens), Rest optional per KI" } : null },
  ];
  const renderMats = () => {
    const anyOpen = C.materials.some(m => setupSteps(m).some(s => !s.done));
    $("#mguide").innerHTML = anyOpen ? `<div class="guide small">So bereitest du eine Datei vor: <b>① Hochladen</b> → <b>② Bereinigen</b> <span class="muted">(unwichtige Seiten raus – spart Tokens)</span> → <b>③ Bilder</b> <span class="muted">(Seiten ohne Text lesbar machen)</span> → <b>④ Themen</b> → lernen & Klausuren. Pro Datei zeigt „Nächster Schritt“, was als Nächstes dran ist.</div>` : "";
    $("#mats").innerHTML = C.materials.map(m => {
      const st = setupSteps(m), cur = st.find(s => !s.done), opts = st.map(s => s.opt).filter(Boolean);
      return `<li class="mat" data-id="${m.id}"><div class="mat-main">
        <span class="chip">${m.kind}</span>
        <div class="grow"><a href="#" data-view title="Vorschau: Seiten ansehen, ausschließen, markieren">${esc(m.name)}</a><div class="tiny muted">${m.chunks} ${m.kind === "pdf" ? "Seiten" : "Abschnitte"} · ≈ ${tok(m.chars / CPT)} Tokens
          ${m.excluded ? ` · <span class="badge">${m.excluded} ausgeschl.</span>` : ""}${m.marked ? ` · <span class="badge warn">${m.marked} markiert</span>` : ""}${m.sty ? ` · <span class="badge good" title="Werden als Vorlage für den Aufgabenstil an Claude gegeben">${m.sty} Stilvorlagen</span>` : ""}${m.trans ? ` · ${m.trans} transkribiert` : ""}</div></div>
        <button class="small" data-view title="Seiten ansehen, ausschließen, als wichtig/irrelevant/Stilvorlage markieren">Vorschau & markieren</button>
        <button class="ghost" data-del title="Datei entfernen">✕</button></div>
        <div class="steps"><span class="step done">✓ ① Hochgeladen</span>${st.map(s => `<span class="sep">›</span><button class="step ${s.done ? "done" : s === cur ? "cur" : ""}" data-step="${s.k}" title="${esc((s.act || s.opt || {}).tip || (s.done ? "erledigt" : ""))}">${s.done ? "✓" : ""} ${s.n} ${s.label}</button>`).join("")}</div>
        <div class="row next">${cur ? `<span class="tiny muted">Nächster Schritt:</span><button class="small primary" data-act="${cur.act.a}" title="${esc(cur.act.tip)}">${cur.act.t}</button>` : `<span class="badge good">✓ Bereit zum Lernen</span>`}
          ${opts.map(o => `<button class="ghost tiny" data-act="${o.a}" title="${esc(o.tip)}">${o.t}</button>`).join("")}</div></li>`;
    }).join("") || `<li class="muted small">Noch kein Material – Dateien oben hineinziehen.</li>`;
  };
  const runAction = async (a, m, btn) => {
    const st = setupSteps(m);
    if ((a === "pick-v" || a === "pick-vi") && m.sugg &&
      !confirm(`Tipp: Für „${m.name}“ gibt es noch ${m.sugg} Bereinigungs-Vorschläge (Schritt ②).\nWenn du zuerst bereinigst, musst du weniger Seiten transkribieren und sparst Tokens.\n\nTrotzdem jetzt Bilder auswählen?`)) return;
    if ((a === "topics" || a === "assign") && st.slice(0, 2).some(s => !s.done) &&
      !confirm(`Tipp: Schritte ② Bereinigen / ③ Bilder sind für „${m.name}“ noch offen. Themen funktionieren trotzdem; neue Seiten kannst du später mit „Automatisch zuordnen“ ergänzen.\n\nJetzt fortfahren?`)) return;
    if (a === "sugg") openSuggestions(cid, m, reload);
    else if (a === "triage") { if (await runTriage(cid, m, btn)) { await reload(); const m2 = C.materials.find(x => x.id === m.id); if (m2 && m2.sugg) openSuggestions(cid, m2, reload); } }
    else if (a === "pick-v" || a === "pick-vi") openPicker(cid, m, a === "pick-v" ? "v" : "vi", reload, $("#upst"));
    else if (a === "topics") { const card = $("#tops").closest(".card"); card.scrollIntoView({ behavior: "smooth", block: "start" }); card.classList.remove("flash"); void card.offsetWidth; card.classList.add("flash"); }
    else if (a === "assign") autoAssign(btn);
  };
  $("#mats").onclick = async e => {
    const li = e.target.closest("li[data-id]"); if (!li) return;
    const mid = li.dataset.id, m = C.materials.find(x => x.id === mid);
    if (e.target.closest("[data-del]")) { if (confirm(`„${m.name}“ entfernen?`)) { C = await api("DELETE", `/api/courses/${cid}/materials/${mid}`); renderAll(); } }
    else if (e.target.closest("[data-view]")) { e.preventDefault(); openMaterial(cid, m, reload); }
    else if (e.target.closest("[data-act]")) { const b = e.target.closest("[data-act]"); runAction(b.dataset.act, m, b); }
    else if (e.target.closest("[data-step]")) {
      const s = setupSteps(m).find(x => x.k === e.target.closest("[data-step]").dataset.step);
      const a = (s.act || s.opt || {}).a;
      if (a) runAction(a, m, e.target.closest("[data-step]")); else toast(`${s.label}: erledigt ✓`);
    }
  };
  const SKIP = /(^|\/)(node_modules|\.git|__pycache__|\.venv|venv|dist|build|target|\.idea|\.vscode)(\/|$)/;
  const BIN = /\.(exe|dll|zip|rar|7z|tar|gz|mp4|mp3|wav|mov|class|jar|o|obj|pyc|lnk|tmp|bak|iso|psd|ai|heic)$/i;
  async function handleFiles(files) {
    files = files.filter(f => !f.name.startsWith(".") && !BIN.test(f.name) && !SKIP.test(f.webkitRelativePath || f.fullPath || "") && f.size < 200e6);
    let ok = 0, n = 0;
    const big = [];
    for (const f of files) {
      $("#upst").innerHTML = `<span class="spinner"></span> ${++n}/${files.length}: ${esc(f.name)}`;
      try {
        const blob = /^image\//.test(f.type) ? await toJpeg(f) : f;
        const r = await upload(`/api/courses/${cid}/materials`, f.name, blob);
        if (r.skipped) toast(`${f.name}: ${r.reason}`); else { ok++; if (r.chunks >= 50) big.push(r.id); }
      } catch (e) { toast(e.message, true); }
    }
    $("#upst").textContent = files.length ? `${ok} von ${files.length} Dateien hinzugefügt.` : "";
    await reload();
    // large files: offer the AI pre-check right away, then show the suggestions
    for (const id of big) {
      const m = C.materials.find(x => x.id === id); if (!m) continue;
      $("#upst").innerHTML = `<span class="spinner"></span> KI-Vorprüfung „${esc(m.name)}“ …`;
      if (await runTriage(cid, m, null)) await reload();
      $("#upst").textContent = "";
      const m2 = C.materials.find(x => x.id === id);
      if (m2 && m2.sugg) await new Promise(res => openSuggestions(cid, m2, async () => { await reload(); res(); }));
    }
    if (ok && !C.topics.length) { await api("POST", `/api/courses/${cid}/topics/local`); await reload(); toast("Themen aus Gliederung erzeugt – mit „Mit Claude gruppieren“ geht es feiner."); }
  }
  async function collect(dt) {
    const entries = [...(dt.items || [])].map(i => i.webkitGetAsEntry && i.webkitGetAsEntry()).filter(Boolean);
    if (!entries.length) return [...dt.files];
    const out = [];
    const walk = async (en, path) => {
      if (SKIP.test(path)) return;
      if (en.isFile) { const f = await new Promise((r, j) => en.file(r, j)); out.push(f); }
      else if (en.isDirectory) {
        const rd = en.createReader(); let b;
        do { b = await new Promise((r, j) => rd.readEntries(r, j)); for (const x of b) await walk(x, path + "/" + x.name); } while (b.length);
      }
    };
    for (const en of entries) await walk(en, en.name);
    return out;
  }
  const drop = $("#drop");
  drop.ondragover = e => { e.preventDefault(); drop.classList.add("over"); };
  drop.ondragleave = () => drop.classList.remove("over");
  drop.ondrop = async e => { e.preventDefault(); drop.classList.remove("over"); handleFiles(await collect(e.dataTransfer)); };
  drop.onclick = e => { if (!e.target.closest("a")) $("#fi").click(); };
  $("#pick").onclick = e => { e.preventDefault(); $("#fi").click(); };
  $("#pickd").onclick = e => { e.preventDefault(); $("#fd").click(); };
  $("#fi").onchange = e => handleFiles([...e.target.files]);
  $("#fd").onchange = e => handleFiles([...e.target.files]);

  // --- topics
  const mastery = () => Object.fromEntries(C.mastery.topics.map(t => [t.id, t]));
  const renderTopics = () => {
    const M = mastery();
    $("#tgoal").hidden = !C.materials.some(m => m.goals); // only if the material has learning-goal pages
    const loose = C.materials.reduce((a, m) => a + (m.unassigned || 0), 0);
    $("#thint").innerHTML = loose && C.topics.length ? `<div class="hint small"><b>${loose} Abschnitte gehören zu keinem Thema</b> und kommen nur in Klausuren ohne Themenauswahl vor (z. B. Übungen, Lösungen, neu eingeschlossene Folien).
      <div class="row" style="margin-top:6px"><button class="small primary" data-th="auto" title="Nachbarseiten und Stichwörter – lokal, 0 Tokens. Unklare Seiten bleiben offen.">Automatisch zuordnen (0 Tokens)</button>
        <button class="small" data-th="loc">Themen neu aus Gliederung</button><button class="small" data-th="ai">neu mit Claude</button>
        <span class="tiny muted">Fortschritt bleibt erhalten</span></div></div>` : "";
    $("#tops").innerHTML = C.topics.map(t => { const m = M[t.id] || {}; return `<li data-id="${t.id}">
      <input type="checkbox" ${sel.has(t.id) ? "checked" : ""}>
      <div class="grow"><a href="#/c/${cid}/t/${t.id}" title="Lernzettel, Nachfragen, Kurz-Quiz">${esc(t.name)}</a><div class="tiny muted">${t.chunks.length} Abschnitte${t.manual ? " · manuell" : ""}</div></div>
      <div style="width:90px"><div class="bar" title="${m.tested ? "Verständnis " + m.score + " %" : "noch nicht geprüft"}"><i style="width:${m.score || 0}%"></i></div></div>
      <span class="small" style="width:40px;text-align:right">${m.tested ? Math.round(m.score) + "%" : "–"}</span>
      <button class="ghost" data-ren title="Umbenennen">✎</button><button class="ghost" data-del title="Löschen">✕</button></li>`; }).join("") || `<li class="muted small">Noch keine Themen. Material hochladen oder Themen eintragen.</li>`;
  };
  $("#tops").onchange = e => { const li = e.target.closest("li"); e.target.checked ? sel.add(li.dataset.id) : sel.delete(li.dataset.id); saveSel(); estimate(); };
  $("#tops").onclick = async e => {
    const s = e.target.closest("[data-ren]");
    if (s) {
      const id = s.closest("li").dataset.id, t = C.topics.find(x => x.id === id), n = prompt("Thema umbenennen:", t.name);
      if (n) { t.name = n; C = await api("PATCH", "/api/courses/" + cid, { topics: C.topics }); renderAll(); }
      return;
    }
    if (!e.target.closest("[data-del]")) return;
    const id = e.target.closest("li").dataset.id;
    C = await api("PATCH", "/api/courses/" + cid, { topics: C.topics.filter(t => t.id !== id) }); sel.delete(id); saveSel(); renderAll();
  };
  $("#tadd").onsubmit = async e => {
    e.preventDefault(); const n = e.target.n.value.trim(); if (!n) return;
    const before = new Set(C.topics.map(t => t.id));
    C = await api("POST", `/api/courses/${cid}/topics`, { name: n }); e.target.n.value = "";
    C.topics.forEach(t => { if (!before.has(t.id)) sel.add(t.id); }); saveSel(); renderAll();
  };
  const autoSel = (old) => { C.topics.forEach(t => { if (!old.has(t.id)) sel.add(t.id); }); sel = new Set([...sel].filter(id => C.topics.some(t => t.id === id))); saveSel(); };
  $("#tloc").onclick = async () => { const old = new Set(C.topics.map(t => t.id)); C = await api("POST", `/api/courses/${cid}/topics/local`); autoSel(old); renderAll(); toast("Themen aktualisiert – Lernfortschritt bleibt erhalten"); };
  // assign pages without topic: locally first (0 tokens), then optionally the rest via AI
  async function autoAssign(b) {
    if (b) b.disabled = true;
    try {
      const r = await api("POST", `/api/courses/${cid}/topics/assign`, { ai: false });
      await reload();
      if (!r.remaining) return toast(`${r.assigned} Abschnitte zugeordnet – alles hat ein Thema ✓`);
      if (!confirm(`${r.assigned} Abschnitte lokal zugeordnet. ${r.remaining} sind unklar.\n\nRest mit Claude (${S.aux_model}) zuordnen? Gesendet werden nur Titel und Textanfang dieser Abschnitte plus die Themennamen: ≈ ${tok(r.est_ai)} Tokens.`)) return;
      const j = await api("POST", `/api/courses/${cid}/topics/assign`, { ai: true });
      const res = await poll(j.id, s => { $("#upst").innerHTML = `<span class="spinner"></span> Claude ordnet Themen zu … ${s.elapsed} s`; });
      $("#upst").textContent = "";
      await reload();
      toast(`${res.ai} weitere per KI zugeordnet${res.remaining ? ` – ${res.remaining} ohne passendes Thema (in der Vorschau: Filter „Ohne Thema“)` : " – alles hat ein Thema ✓"}`);
    } catch (err) { toast(err.message, true); $("#upst").textContent = ""; await reload(); }
  }
  $("#thint").onclick = e => {
    const b = e.target.closest("[data-th]"); if (!b) return;
    if (b.dataset.th !== "auto") return $(b.dataset.th === "ai" ? "#tai" : "#tloc").click();
    autoAssign(b);
  };
  const suggestTopics = async (goals) => {
    const chunks = C.materials.reduce((a, m) => a + m.chunks - m.excluded, 0);
    if (!chunks) return toast("Erst Material hochladen.", true);
    if (!confirm((goals ? `Claude (${S.aux_model}) leitet die Themen aus den Lernziel-Seiten ab und ordnet die Abschnitte zu. ` :
      `Claude (${S.aux_model}) gruppiert die Gliederung in Themen. `) + `Gesendet werden nur Titel${goals ? " und Lernziele" : ""}: ≈ ${tok(chunks * 12 + (goals ? 1500 : 400))} Tokens. ` +
      `Bestehende Themen behalten Namen und Lernfortschritt. Fortfahren?`)) return;
    const b = $(goals ? "#tgoal" : "#tai"), label = b.textContent; b.disabled = true; b.innerHTML = `<span class="spinner"></span> …`;
    try { const old = new Set(C.topics.map(t => t.id)); const j = await api("POST", `/api/courses/${cid}/topics/suggest`, { goals }); await poll(j.id); await reload(); autoSel(old); renderAll(); toast("Themen aktualisiert"); }
    catch (err) { toast(err.message, true); } finally { b.disabled = false; b.textContent = label; }
  };
  $("#tai").onclick = () => suggestTopics(false);
  $("#tgoal").onclick = () => suggestTopics(true);
  $("#sall").onclick = e => { e.preventDefault(); sel = new Set(C.topics.map(t => t.id)); saveSel(); renderTopics(); estimate(); };
  $("#snone").onclick = e => { e.preventDefault(); sel = new Set(); saveSel(); renderTopics(); estimate(); };
  $("#sweak").onclick = e => { e.preventDefault(); sel = new Set([...C.mastery.topics].sort((a, b) => a.score - b.score).slice(0, 5).map(t => t.id)); saveSel(); renderTopics(); estimate(); };

  // --- instructions
  $("#ins").value = C.instructions || "";
  $("#ins").onchange = async e => { await api("PATCH", "/api/courses/" + cid, { instructions: e.target.value }); C.instructions = e.target.value; toast("Kursvorgaben gespeichert"); estimate(); };

  // --- mastery
  const renderMastery = () => {
    const M = C.mastery, ts = [...M.topics].sort((a, b) => a.score - b.score);
    $("#mas").innerHTML = `<div class="row between"><div><div class="hero">${pct(M.overall)}</div><div class="small muted">des Materials verstanden · ${M.tested}/${M.topics.length} Themen geprüft · ${M.exams} Klausuren</div></div>
      <span class="small muted" style="max-width:220px;text-align:right">100 % = jedes Thema in AB I–III zuletzt vollständig gelöst (neuere Klausuren zählen mehr)</span></div>
      ${ts.length ? `<table class="t" style="margin-top:12px"><thead><tr><th>Thema</th><th style="width:34%"></th><th class="n">%</th><th class="n small">AB I · II · III</th></tr></thead><tbody>
      ${ts.map(t => `<tr title="${esc(t.name)}: ${t.tested ? t.score + " %" : "noch nicht geprüft"}"><td>${esc(t.name)}</td><td><div class="bar"><i style="width:${t.score}%"></i></div></td><td class="n">${t.tested ? Math.round(t.score) : "–"}</td>
        <td class="n tiny muted">${[1, 2, 3].map(a => t.ab[a] ?? "–").join(" · ")}</td></tr>`).join("")}</tbody></table>` : ""}`;
  };

  // --- generation
  $("#gmin").value = cfg.minutes; [1, 2, 3].forEach(i => $("#ab" + i).value = cfg.ab[i - 1]);
  $("#gweak").checked = !!cfg.focus_weak;
  $("#gtypes").innerHTML = Object.entries(TYPES).map(([k, v]) => `<button type="button" class="chip ${cfg.types.includes(k) ? "on" : ""}" data-k="${k}">${v}</button>`).join("");
  $("#gtypes").onclick = e => { const b = e.target.closest("[data-k]"); if (!b) return; b.classList.toggle("on"); cfg.types = $$("#gtypes .on").map(x => x.dataset.k); saveCfg(); estimate(); };
  const readCfg = () => {
    cfg.minutes = +$("#gmin").value || 90; cfg.ab = [1, 2, 3].map(i => +$("#ab" + i).value || 0);
    cfg.focus_weak = $("#gweak").checked; cfg.extra = $("#gextra").value; saveCfg();
    return { ...cfg, topics: [...sel] };
  };
  ["#gmin", "#ab1", "#ab2", "#ab3", "#gweak"].forEach(s => $(s).addEventListener("change", () => estimate()));
  let estT;
  function estimate() {
    clearTimeout(estT);
    estT = setTimeout(async () => {
      const c = readCfg(), sum = c.ab.reduce((a, b) => a + b, 0);
      if (!C.materials.length) { $("#gest").textContent = "Zuerst Material hinzufügen."; return; }
      try {
        const m = await api("POST", `/api/courses/${cid}/estimate`, c);
        const cut = m.total_chars > m.mat_chars * 1.05 ? ` · Budget: ${Math.round(100 * m.mat_chars / m.total_chars)} % des Materials` : "";
        const openImg = C.materials.reduce((a, x) => a + (x.vision || 0), 0);
        const warn = openImg ? `<div class="tiny" style="color:var(--warn);margin-top:2px">⚠ ${openImg} Seiten ohne Text sind noch nicht transkribiert und fehlen in der Klausur (Schritt ③ im Material).</div>` : "";
        $("#gest").innerHTML = `${sel.size || "alle"} Themen · ${m.chunks} Abschnitte${m.style_chunks ? ` + ${m.style_chunks} Stilvorlagen` : ""} · ≈ <b>${tok(m.est_in)}</b> Eingabe + ≈ ${tok(m.est_out)} Ausgabe-Tokens (${esc(S.gen_model)})${cut}${sum !== 100 ? ` · <span class="badge warn">AB-Summe ${sum} %</span>` : ""}${warn}`;
      } catch (e) { $("#gest").textContent = e.message; }
    }, 400);
  }
  $("#ggo").onclick = async () => {
    const c = readCfg();
    if (!C.materials.length) return toast("Zuerst Material hinzufügen.", true);
    if (C.topics.length && !sel.size && !confirm("Keine Themen ausgewählt – Klausur über das gesamte Material erstellen?")) return;
    if (await startGen(cid, c, $("#gst"), $("#ggo"))) $("#gextra").value = "";
  };
  $("#gquiz").onclick = async () => {
    if (!C.materials.length) return toast("Zuerst Material hinzufügen.", true);
    const c = readCfg();
    startGen(cid, { kind: "quiz", topics: c.topics, types: c.types, extra: c.extra }, $("#gst"), $("#gquiz"));
  };

  // --- history
  const renderHist = () => {
    $("#hist").innerHTML = [...C.exams].reverse().map(e => `<li data-id="${e.id}"><div class="grow">${KIND[e.kind] ? `<span class="chip">${KIND[e.kind]}</span> ` : ""}<a href="#/c/${cid}/e/${e.id}">${esc(e.title)}</a>
      <div class="tiny muted">${new Date(e.created * 1000).toLocaleString("de-DE", { dateStyle: "short", timeStyle: "short" })} · ${e.min} min · ${fmt(e.max_pts)} P.${e.topics.length ? " · " + esc(e.topics.slice(0, 3).join(", ")) + (e.topics.length > 3 ? " …" : "") : ""}</div></div>
      ${e.status === "graded" ? `<span class="badge ${e.pct >= 75 ? "good" : e.pct >= 50 ? "warn" : "bad"}">${Math.round(e.pct)} % · ${e.grade}</span>` : `<span class="chip">offen</span>`}
      <button class="ghost" data-del>✕</button></li>`).join("") || `<li class="muted small">Noch keine Klausuren.</li>`;
  };
  $("#hist").onclick = async e => {
    if (!e.target.closest("[data-del]")) return;
    const id = e.target.closest("li").dataset.id;
    if (confirm("Klausur löschen? (zählt dann nicht mehr zum Verständnis)")) { await api("DELETE", `/api/courses/${cid}/exams/${id}`); await reload(); }
  };

  async function reload() {
    C = await api("GET", "/api/courses/" + cid);
    if (document.activeElement !== $("#ins")) $("#ins").value = C.instructions || ""; // e.g. exam info taken over from suggestions
    renderAll();
  }
  function renderAll() { renderMats(); renderTopics(); renderMastery(); renderHist(); estimate(); }
  renderAll();
}

// ---------------- topic page: Lernzettel, follow-up questions, quiz ----------------
async function viewTopic(cid, tid) {
  let T = await api("GET", `/api/courses/${cid}/topics/${tid}`);
  app.innerHTML = `<a href="#/c/${cid}" class="small">← Kurs</a>
    <div class="row between"><h1 style="margin:4px 0 12px">${esc(T.topic.name)}</h1><div id="tm" class="row"></div></div>
    <div class="grid2" style="grid-template-columns:minmax(0,1.6fr) minmax(0,1fr)">
      <div><section class="card" id="note"></section><section class="card" id="qa"></section></div>
      <div><section class="card" id="quiz"></section><section class="card" id="src"></section></div></div>`;

  function renderHead() {
    const m = T.mastery || {};
    $("#tm").innerHTML = m.tested ? `<span class="hero" style="font-size:1.6rem">${Math.round(m.score)} %</span>
      <span class="small muted">verstanden · AB ${[1, 2, 3].map(a => m.ab[a] ?? "–").join(" · ")}</span>` : `<span class="chip">noch nicht geprüft</span>`;
  }
  function renderNote() {
    const n = T.note;
    const openN = (T.slides || []).flatMap(g => g.pages).filter(p => p.s === "miss" || p.s === "fig").length;
    const openHint = openN ? `<div class="hint small">${openN} ${openN === 1 ? "Seite enthält" : "Seiten enthalten"} Abbildungen, die die KI noch nicht kennt (im Material rot markiert). Dort vormerken → „Transkribieren + Lernzettel ${n ? "neu " : ""}erstellen“.</div>` : "";
    if (!n) {
      $("#note").innerHTML = `<h2>Lernzettel</h2>${openHint}
        <p class="small">Erklärt das Thema von Grund auf – aus <b>deinem</b> Material (Markierungen werden betont): Überblick, Kernbegriffe, Schritt-für-Schritt, ein durchgerechnetes Beispiel, typische Fehler, was in Klausuren gefragt wird und ein Selbsttest.</p>
        <p class="small muted">Wird einmal erstellt und gespeichert. ≈ ${tok(T.note_est_in)} Eingabe + ≈ ${tok(T.note_est_out)} Ausgabe-Tokens (${esc(T.models.learn)}).</p>
        <div class="row"><button class="primary" id="ngo" ${T.has_material ? "" : "disabled"}>Lernzettel erstellen</button><span id="nst" class="small muted">${T.has_material ? "" : "Kein verwertbares Material – Seiten ggf. erst transkribieren."}</span></div>`;
    } else {
      $("#note").innerHTML = `<div class="row between"><h2 style="margin:0">Lernzettel</h2><div class="row">
          ${T.stale ? `<span class="badge warn" title="Material oder Markierungen haben sich seit der Erstellung geändert">Material geändert</span>` : ""}
          <button class="small" id="ngo" title="≈ ${tok(T.note_est_in)} Tokens">Neu erstellen</button></div></div>
        ${openHint}<div class="md note">${spoilers(md(n.md))}</div>
        <div class="tiny muted" style="margin-top:10px">${new Date(n.created * 1000).toLocaleString("de-DE", { dateStyle: "short", timeStyle: "short" })} · ${esc(n.model)} · ${tok(n.usage.in + n.usage.cache_w + n.usage.cache_r)} ein / ${tok(n.usage.out)} aus · Selbsttest-Antworten: anklicken zum Aufdecken</div>
        <span id="nst" class="small muted"></span>`;
    }
    $("#ngo").onclick = async () => {
      if (T.note && !confirm(`Lernzettel neu erstellen? (≈ ${tok(T.note_est_in)} Tokens; Nachfragen bleiben erhalten)`)) return;
      const b = $("#ngo"); b.disabled = true;
      try {
        const j = await api("POST", `/api/courses/${cid}/topics/${tid}/note`);
        await poll(j.id, s => { const [n2] = s.progress || []; $("#nst").innerHTML = `<span class="spinner"></span> schreibt${n2 ? ` · ${fmt(n2)} Zeichen` : ""} · ${s.elapsed} s`; });
        T = await api("GET", `/api/courses/${cid}/topics/${tid}`); renderAll();
      } catch (e) { toast(e.message, true); b.disabled = false; $("#nst").textContent = ""; }
    };
  }
  function renderQA() {
    const qa = (T.note && T.note.qa) || [];
    $("#qa").innerHTML = `<h2>Nachfragen</h2>
      ${T.note ? "" : `<p class="small muted">Verfügbar, sobald der Lernzettel erstellt ist.</p>`}
      ${qa.map((x, i) => `<div class="qa"><div class="row between"><b class="grow">${esc(x.q)}</b><button class="ghost tiny" data-del="${i}" title="Löschen">✕</button></div>
        <div class="md">${md(x.a)}</div><div class="tiny muted">${x.mat ? "mit Material · " : ""}${tok(x.tok)} Tokens</div></div>`).join("")}
      ${T.note ? `<form id="ask"><textarea name="q" rows="2" placeholder="Was ist unklar? z. B. „Warum ist im Beispiel Schritt 3 nötig?“"></textarea>
        <div class="row" style="margin-top:6px"><label class="chk small" title="Sendet zusätzlich das Material des Themas (≈ ${tok(Math.min(T.note_est_in, 15000))} Tokens) – nur nötig, wenn der Lernzettel etwas nicht abdeckt"><input type="checkbox" name="mat"> Material einbeziehen</label>
        <span class="grow"></span><span id="ast" class="small muted"></span><button class="primary">Fragen</button></div>
        <div class="tiny muted">Ohne Material werden nur Lernzettel + letzte Nachfragen gesendet (≈ 4–6 k Tokens).</div></form>` : ""}`;
    const f = $("#ask");
    if (f) f.onsubmit = async e => {
      e.preventDefault(); if (!f.q.value.trim()) return;
      const b = $("button.primary", f); b.disabled = true;
      try {
        const j = await api("POST", `/api/courses/${cid}/topics/${tid}/ask`, { q: f.q.value, material: f.mat.checked });
        await poll(j.id, s => { $("#ast").innerHTML = `<span class="spinner"></span> ${s.elapsed} s`; });
        T = await api("GET", `/api/courses/${cid}/topics/${tid}`); renderQA();
      } catch (err) { toast(err.message, true); b.disabled = false; $("#ast").textContent = ""; }
    };
    $("#qa").onclick = async e => {
      const d = e.target.closest("[data-del]"); if (!d) return;
      await api("DELETE", `/api/courses/${cid}/topics/${tid}/qa/${d.dataset.del}`);
      T = await api("GET", `/api/courses/${cid}/topics/${tid}`); renderQA();
    };
  }
  // learning path: quiz on level L -> >= pass % -> next level; below -> vary the mistakes until >= pass % -> next level
  function renderQuiz() {
    const q = T.quiz, P = T.path, L = P.level, can = T.has_material || T.note;
    const steps = [1, 2, 3].map(k => {
      const cls = P.done || k < L ? "done" : k === L ? "cur" : "";
      return `<span class="step ${cls}">${P.done || k < L ? "✓" : k} ${esc(T.levels[k])}</span>`;
    }).join(`<span class="sep">›</span>`);
    const last = P.last, nf = (P.fails || []).length;
    let msg, main;
    if (P.open) {
      msg = `Du hast ${P.open.kind === "drill" ? "Variationen" : "ein Quiz"} auf Stufe ${L} angefangen.`;
      main = `<a class="btn primary" href="#/c/${cid}/e/${P.open.id}">Weiter bearbeiten</a>`;
    } else if (P.drill) {
      msg = `Letzter Versuch auf Stufe ${L}: <b class="bad-t">${last.pct} %</b> – zum Weiterkommen brauchst du ${T.pass} %.
        Übe jetzt ${nf === 1 ? "deinen Fehler" : `deine ${nf} Fehler`} mit Variationen (neues Beispiel, gleiches Konzept). Schaffst du dort ${T.pass} %, geht es mit ${L < 3 ? `Stufe ${L + 1}` : "dem Abschluss"} weiter – sonst wird erneut variiert.`;
      main = `<button class="primary" id="qvar">↻ ${nf === 1 ? "Fehler" : `${nf} Fehler`} variieren</button>`;
    } else if (P.done) {
      msg = `Alle drei Stufen bestanden${last ? ` (zuletzt ${last.pct} %)` : ""}. Wiederhole mit Transfer-Quizzen oder teste dich mit einer Probeklausur.`;
      main = `<button class="primary" id="qgo" ${can ? "" : "disabled"}>Quiz Stufe 3 wiederholen</button>`;
    } else {
      msg = (last && last.ok ? `✓ Stufe ${L - 1} bestanden (${last.pct} %). ` : "") +
        `Jetzt <b>Stufe ${L} – ${esc(T.levels[L])}</b>: ab ${T.pass} % geht es ${L < 3 ? `zu Stufe ${L + 1}` : "zum Abschluss"}, darunter übst du deine Fehler mit Variationen.`;
      main = `<button class="primary" id="qgo" ${can ? "" : "disabled"}>Quiz Stufe ${L} starten</button>`;
    }
    $("#quiz").innerHTML = `<h2>Üben – Lernpfad</h2>
      <div class="steps lpath">${steps}</div>
      <p class="small">${msg}</p>
      <div class="row">${P.drill || P.open ? "" : `<select id="qmin"><option value="5">5 min</option><option value="10" selected>10 min</option><option value="15">15 min</option></select>`}${main}</div>
      <div class="tiny muted" style="margin:6px 0">${P.drill ? `aus: ${esc(P.src_title || "")} · ≈ 2–5 k Tokens` : `≈ ${tok(q.est_in)} Eingabe-Tokens (${esc(T.models.quiz)})${q.from_notes ? " · basiert auf dem Lernzettel (günstiger)" : ""}`}</div>
      <span id="qst" class="small muted"></span>
      <details class="small" style="margin-top:8px"><summary class="muted">Andere Stufe üben${P.drill ? " / neues Quiz statt Variationen" : ""}</summary>
        <div class="row" style="margin-top:6px"><select id="qlv">${[1, 2, 3].map(k => `<option value="${k}" ${k === L ? "selected" : ""}>Stufe ${k} – ${esc(T.levels[k])}</option>`).join("")}</select>
        <button class="small" id="qalt" ${can ? "" : "disabled"}>Quiz starten</button></div>
        <div class="tiny muted">Nur die aktuelle Stufe (${L}) zählt für den Lernpfad – leichtere sind Wiederholung, schwerere Extra-Übung.</div></details>
      ${T.exams.length ? `<h3 style="margin-top:14px">Bisher</h3><ul class="list">${T.exams.map(e => `<li><span class="chip">${KIND[e.kind] || "Klausur"}</span>
        <a class="grow" href="#/c/${cid}/e/${e.id}">${esc(e.title)}</a>${e.status === "graded" ? `<span class="badge ${e.pct >= 75 ? "good" : e.pct >= 50 ? "warn" : "bad"}">${Math.round(e.pct)} %</span>` : `<span class="chip">offen</span>`}</li>`).join("")}</ul>` : ""}`;
    const go = $("#qgo");
    if (go) go.onclick = () => startGen(cid, { kind: "quiz", topics: [tid], minutes: +$("#qmin").value, level: L }, $("#qst"), go);
    const vb = $("#qvar");
    if (vb) vb.onclick = () => runVariants(cid, P.drill, P.fails, vb);
    $("#qalt").onclick = () => startGen(cid, { kind: "quiz", topics: [tid], minutes: +($("#qmin")?.value || 10), level: +$("#qlv").value }, $("#qst"), $("#qalt"));
  }
  // material overview: highlighter overlay = what the AI gets from each page; pick figures to transcribe
  // pick: "mid:i" -> Set of figure indices (empty = whole page)
  const pick = new Map(), LAY = {};
  let showAll = false;
  async function loadLay(flat) {
    const need = {};
    flat.filter(p => p.img && !(p.key in LAY)).forEach(p => (need[p.g.mid] ||= []).push(p.i));
    const mids = Object.keys(need);
    if (!mids.length) return false;
    await Promise.all(mids.map(mid => api("GET", `/api/courses/${cid}/materials/${mid}/layout?i=${need[mid].join(",")}`)
      .then(d => need[mid].forEach(i => { LAY[`${mid}:${i}`] = d.pages[i] || null; }))
      .catch(() => need[mid].forEach(i => { LAY[`${mid}:${i}`] = null; }))));
    return true;
  }
  const openFigs = key => (LAY[key]?.f || []).map((f, k) => f[4] !== "ok" ? k : -1).filter(k => k >= 0);
  const pickPage = p => {
    if (pick.has(p.key)) pick.delete(p.key); else pick.set(p.key, new Set(openFigs(p.key)));
  };
  const pickFig = (p, k) => {
    const s = pick.get(p.key) || new Set();
    s.has(k) ? s.delete(k) : s.add(k);
    s.size ? pick.set(p.key, s) : pick.delete(p.key);
  };
  function renderSrc() {
    const G = T.slides || [], flat = G.flatMap(g => g.pages.map(p => ({ ...p, g, key: `${g.mid}:${p.i}` })));
    const UNIT = { pdf: "Seite", pptx: "Folie", image: "Bild" };
    const LIMIT = 24;
    for (const x of [...pick.keys()]) if (!flat.some(p => p.key === x && canPick(p))) pick.delete(x);
    const open = flat.filter(p => p.s === "miss" || p.s === "fig");
    const nReg = [...pick.values()].reduce((a, s) => a + Math.max(1, s.size), 0);
    const est = [...pick.keys()].reduce((a, x) => a + (flat.find(p => p.key === x)?.s === "miss" ? 1300 : 450 + 500 * Math.max(1, pick.get(x).size)), 0);
    $("#src").innerHTML = `<h2>Material</h2>` + (flat.length ? `<div class="slegend tiny">${HL_LEGEND}</div>` + G.map(g => `<div class="sgroup">
        <div class="row between small"><b class="grow" style="overflow-wrap:anywhere">${esc(g.name)}</b><span class="muted">${g.pages.length} ${g.pages.length === 1 ? (UNIT[g.kind] || "Abschnitt") : ({ pdf: "Seiten", pptx: "Folien", image: "Bilder" }[g.kind] || "Abschnitte")}</span></div>
        <div class="sgrid">${g.pages.map(p => {
          const k = flat.findIndex(f => f.g === g && f.i === p.i), f = flat[k], on = pick.has(f.key), c = CAPTURE[p.s] || CAPTURE.ok;
          return `<div class="stile ${p.img ? "" : "txt"} ${p.used || p.near ? "" : "unused"} ${p.near ? "near" : ""} ${on ? "picked" : ""}" data-k="${k}" ${k >= LIMIT && !showAll ? "hidden" : ""} role="button" tabindex="0"
              title="${esc(`${UNIT[g.kind] || "Abschnitt"} ${p.n}${p.t ? " – " + p.t : ""}\n${c.tip}${p.near ? "\nNoch keinem Thema zugeordnet (liegt direkt daneben) – wird nach dem Transkribieren diesem Thema zugeordnet." : ""}`)}">
            ${p.img ? pageHTML(`/api/courses/${cid}/materials/${g.mid}/img/${p.i}?w=320`, LAY[f.key], pick.get(f.key), true) : `<span class="stxt"><b class="hlx">${esc(p.t)}</b> <span class="hlx">${esc(p.x)}</span></span>`}
            <span class="snum">${p.n}</span>${p.st === "imp" ? `<span class="simp" title="sehr wichtig">★</span>` : ""}
            ${canPick(f) ? `<button class="spick" data-pick="${f.key}" title="Alle offenen Abbildungen dieser Seite vormerken">${on ? "✓" : ""}</button>` : ""}</div>`;
        }).join("")}</div></div>`).join("")
      + (flat.length > LIMIT && !showAll ? `<button class="small" id="sall" style="margin-top:8px">Alle ${flat.length} anzeigen</button>` : "")
      + `<div class="spickbar row">${pick.size
          ? `<span class="small grow"><b>${nReg}</b> ${nReg === 1 ? "Abbildung" : "Abbildungen"} auf ${pick.size} ${pick.size === 1 ? "Seite" : "Seiten"} vorgemerkt · ≈ ${tok(est)} Tokens (${esc(T.models.aux || "Sonnet")})</span>
             <button class="small ghost" id="spclr">Leeren</button><button class="small" id="sptr">Transkribieren</button>
             <button class="small primary" id="sptn" title="Danach den Lernzettel mit dem neuen Inhalt erstellen (≈ ${tok(T.note_est_in)} + Bildtext)">Transkribieren + Lernzettel ${T.note ? "neu " : ""}erstellen</button>`
          : open.length ? `<span class="small grow">${open.length} ${open.length === 1 ? "Seite hat" : "Seiten haben"} Abbildungen, die die KI noch nicht kennt (rot).</span><button class="small" id="spall">Alle offenen vormerken</button>` : ""}</div>
         <span id="spst" class="small muted"></span>`
      + `<p class="tiny muted">Seite anklicken = groß anzeigen; dort rote Abbildungen anklicken = vormerken (← → blättern, <kbd>M</kbd> = alle Abbildungen der Seite). ${flat.some(p => !p.used && !p.near) ? "Blasse Seiten passen nicht mehr ins Lernzettel-Budget. " : ""}${flat.some(p => p.near) ? "Gestrichelt = reine Bildseite direkt neben dem Thema, noch ohne Thema. " : ""}Ausschließen und Markieren über „Vorschau & markieren“ im Kurs.</p>`
      : `<p class="small muted">Diesem Thema ist kein Material zugeordnet.</p>`);
    const all = $("#sall");
    if (all) all.onclick = () => { showAll = true; renderSrc(); };
    const byKey = key => flat.find(p => p.key === key);
    const opt = { pick, canPick, LAY, pickPage: p => { pickPage(p); renderSrc(); }, pickFig: (p, k) => { pickFig(p, k); renderSrc(); } };
    $("#src").onclick = e => {
      const b = e.target.closest("[data-pick]");
      if (b) { pickPage(byKey(b.dataset.pick)); return renderSrc(); }
      const t = e.target.closest(".stile");
      if (t) slideViewer(cid, flat, +t.dataset.k, opt);
    };
    $("#src").onkeydown = e => { const t = e.target.closest(".stile"); if (t && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); t.click(); } };
    const on = (id, fn) => { const b = $(id); if (b) b.onclick = fn; };
    on("#spall", () => { open.forEach(p => pick.has(p.key) || pickPage(p)); renderSrc(); });
    on("#spclr", () => { pick.clear(); renderSrc(); });
    on("#sptr", () => runPick(false));
    on("#sptn", () => runPick(true));
    loadLay(flat).then(ch => { if (ch) renderSrc(); });
  }
  async function runPick(withNote) {
    const n = pick.size;
    if (!confirm(`${n} ${n === 1 ? "Seite" : "Seiten"} mit Claude transkribieren (nur die vorgemerkten Abbildungen)${withNote ? " und danach den Lernzettel " + (T.note ? "neu " : "") + "erstellen" : ""}?` +
      (withNote && T.note ? "\nDer bisherige Lernzettel wird ersetzt (Nachfragen bleiben erhalten)." : "") + "\n\nStarten?")) return;
    $$("#src button").forEach(b => b.disabled = true);
    const regions = {};
    pick.forEach((s, key) => { if (s.size) regions[key] = [...s]; });
    try {
      const j = await api("POST", `/api/courses/${cid}/topics/${tid}/transcribe`, { pages: [...pick.keys()], regions, note: withNote });
      await poll(j.id, s => {
        const [ph, a, b] = s.progress || [];
        $("#spst").innerHTML = `<span class="spinner"></span> ${ph === "note" ? `Lernzettel wird geschrieben${a ? ` · ${fmt(a)} Zeichen` : ""}` : `transkribiert${b ? ` ${a}/${b}` : ""}`} · ${s.elapsed} s`;
      });
      pick.clear();
      toast(withNote ? "Transkribiert, Lernzettel erstellt." : "Transkribiert – die KI kennt jetzt die Abbildungen.");
    } catch (e) { toast(e.message, true); }
    for (const k in LAY) delete LAY[k];
    T = await api("GET", `/api/courses/${cid}/topics/${tid}`); renderAll();
  }
  function renderAll() { renderHead(); renderNote(); renderQA(); renderQuiz(); renderSrc(); }
  renderAll();
}
// what the AI gets from a page (status "s" from the topic info)
const CAPTURE = {
  ok: { ic: "✓", short: "Text", tip: "Text erfasst – die KI nutzt den Inhalt dieser Seite." },
  tr: { ic: "✓", short: "transkribiert", tip: "Transkribiert – Text und Abbildungen sind für die KI erfasst." },
  fig: { ic: "◐", short: "Abbildung offen", tip: "Text erfasst, Abbildungen (rot) aber nicht – die KI kennt nur den Text." },
  miss: { ic: "✕", short: "Bild fehlt", tip: "Nur ein Bild ohne Text – die KI kennt den Inhalt nicht. Transkribieren!" },
  skip: { ic: "–", short: "übersprungen", tip: "Abbildung als „keine Transkription nötig“ markiert – die KI kennt nur den Text." },
};
const canPick = p => p.img && p.s !== "tr";
const HL_LEGEND = `<span class="slg"><i class="sw hlt"></i> Text erfasst</span><span class="slg"><i class="sw hlu"></i> Text nicht genutzt</span>
  <span class="slg"><i class="sw hlf ok"></i> Abbildung transkribiert</span><span class="slg"><i class="sw hlf open"></i> Abbildung fehlt der KI</span>
  <span class="slg"><i class="sw hlf sel"></i> vorgemerkt</span>`;
const FIG_TIP = { ok: "Abbildung transkribiert – die KI kennt sie", open: "Abbildung fehlt der KI – anklicken zum Vormerken",
  skip: "Abbildung übersprungen („keine Transkription nötig“) – anklicken zum Vormerken", sel: "Vorgemerkt – anklicken zum Abwählen" };
// page picture + highlighter: L = {ar, t: [[x0,y0,x1,y1,ok]], f: [[x0,y0,x1,y1,state]]} (0..1), sel = picked figure indices
function pageHTML(src, L, sel, thumb) {
  const box = r => `left:${(r[0] * 100).toFixed(2)}%;top:${(r[1] * 100).toFixed(2)}%;width:${((r[2] - r[0]) * 100).toFixed(2)}%;height:${((r[3] - r[1]) * 100).toFixed(2)}%`;
  const fit = thumb ? `onload="const r=this.naturalWidth/this.naturalHeight;this.parentNode.style.aspectRatio=r;this.parentNode.classList.toggle('h',r<4/3)"` : "";
  const hl = L ? `<div class="hl">${L.t.map(r => `<i class="${r[4] ? "hlt" : "hlu"}" style="${box(r)}"></i>`).join("")}
    ${L.f.map((r, k) => { const s = sel && sel.has(k) ? "sel" : r[4]; return `<i class="hlf ${s}" data-f="${k}" style="${box(r)}" title="${FIG_TIP[s]}"></i>`; }).join("")}</div>` : "";
  return `<div class="spg ${L && L.ar && L.ar < 4 / 3 ? "h" : ""}" ${L && L.ar ? `style="aspect-ratio:${L.ar}"` : ""}><img ${thumb ? `loading="lazy"` : ""} src="${src}" alt="" draggable="false" ${fit}>${hl}</div>`;
}
// big view of a topic's slides/pages: ← → or buttons to browse, Esc closes; text-only sections show their text
// opt {pick, canPick, LAY, pickPage, pickFig}: click red figures to pick them, M = whole page
async function slideViewer(cid, list, k, opt = {}) {
  const UNIT = { pdf: "Seite", pptx: "Folie", image: "Bild" }, texts = {};
  let asText = false;
  const bg = modal(`<div class="row between"><div id="svcap" class="grow small"></div>
      <button class="small" id="svai" title="Zeigt den Text, den die KI von dieser Seite bekommt">Was die KI sieht</button>
      <button class="small" id="svpick" hidden></button>
      <a id="svorig" class="small" target="_blank">Original ↗</a><button class="ghost" data-close>✕</button></div>
    <div class="sview"><button class="ghost svnav" data-d="-1" title="Zurück (←)">‹</button><div id="svbody" class="svbody"></div><button class="ghost svnav" data-d="1" title="Weiter (→)">›</button></div>
    <div class="slegend tiny" style="justify-content:center;margin-top:6px">${HL_LEGEND}</div>`,
    () => document.removeEventListener("keydown", onKey), "wide");
  const chunk = async g => {
    if (!texts[g.mid]) texts[g.mid] = api("GET", `/api/courses/${cid}/materials/${g.mid}`).then(d => d.chunks);
    return texts[g.mid];
  };
  const show = async () => {
    const p = list[k], g = p.g, c = CAPTURE[p.s] || CAPTURE.ok, L = opt.LAY?.[p.key];
    const sel = opt.pick?.get(p.key), nOpen = (L?.f || []).filter(f => f[4] !== "ok").length;
    $("#svcap", bg).innerHTML = `<b>${UNIT[g.kind] || "Abschnitt"} ${p.n}</b>${p.t && !/^(Seite|Folie|Bild|Abschnitt) \d+$/.test(p.t) ? " – " + esc(p.t) : ""}
      <span class="muted"> · ${esc(g.name)} · ${k + 1}/${list.length}</span>${p.st === "imp" ? ` <span class="badge warn">sehr wichtig</span>` : ""}
      <div class="tiny" style="margin-top:2px">${esc(c.tip)}${p.near ? " Noch keinem Thema zugeordnet." : ""}${nOpen && opt.pick ? ` <b>Rote Abbildungen anklicken = vormerken.</b>` : ""}</div>`;
    const src = `/api/courses/${cid}/materials/${g.mid}/img/${p.i}`;
    const o = $("#svorig", bg); o.hidden = !p.img; o.href = src;
    $("#svai", bg).hidden = !p.img;
    $("#svai", bg).classList.toggle("on", asText);
    const pb = $("#svpick", bg), can = opt.pick && opt.canPick(p);
    pb.hidden = !can;
    if (can) {
      pb.textContent = sel ? `✓ ${sel.size > 1 ? sel.size + " Abbildungen" : sel.size ? "1 Abbildung" : "Seite"} vorgemerkt` : nOpen > 1 ? "Alle Abbildungen vormerken" : "Zum Transkribieren vormerken";
      pb.className = "small" + (sel ? " primary" : ""); pb.title = sel ? "Abwählen (Taste M)" : "Taste M";
    }
    $$(".svnav", bg).forEach(b => b.disabled = k + +b.dataset.d < 0 || k + +b.dataset.d >= list.length);
    const body = $("#svbody", bg);
    if (p.img && !asText) { body.innerHTML = pageHTML(src, L, sel, false); return; }
    body.innerHTML = `<span class="spinner"></span>`;
    const ch = (await chunk(g))[p.i] || {}, txt = (ch.x || "").trim();
    if (list[k] !== p) return;
    const note = p.s === "miss" ? `<p class="small bad-t">Kein Text – der Inhalt dieser Seite fehlt der KI, bis sie transkribiert ist.</p>`
      : p.s === "fig" || p.s === "skip" ? `<p class="small muted">Nur dieser Text ist erfasst – rot markierte Abbildungen werden nicht beschrieben.</p>`
      : p.s === "tr" ? `<p class="small muted">Transkribiert${/\[Abbildung\]/.test(txt) ? " – die Abbildungsbeschreibung steht nach „[Abbildung]“" : ""}.</p>` : "";
    body.innerHTML = `<div class="svtext">${p.img ? note : ""}${txt ? `<div class="md">${ch.code ? `<pre><code>${esc(txt)}</code></pre>` : md(txt)}</div>` : ""}</div>`;
  };
  const go = d => { if (k + d >= 0 && k + d < list.length) { k += d; show(); } };
  const flip = () => { const p = list[k]; if (opt.pick && opt.canPick(p)) { opt.pickPage(p); show(); } };
  const onKey = e => {
    if (e.target.closest?.("input,textarea,select")) return;
    if (e.key === "ArrowLeft") go(-1); else if (e.key === "ArrowRight") go(1);
    else if (e.key === "m" || e.key === "M") flip();
  };
  document.addEventListener("keydown", onKey);
  bg.addEventListener("click", e => {
    const b = e.target.closest(".svnav"); if (b) return go(+b.dataset.d);
    if (e.target.closest("#svpick")) return flip();
    if (e.target.closest("#svai")) { asText = !asText; return show(); }
    const f = e.target.closest(".hlf");
    if (f && opt.pick && opt.canPick(list[k]) && !f.classList.contains("ok")) { opt.pickFig(list[k], +f.dataset.f); show(); }
  });
  show();
}
function compactPages(ps) {
  const s = [...new Set(ps)].sort((a, b) => a - b), out = [];
  for (let i = 0; i < s.length; i++) { let j = i; while (j + 1 < s.length && s[j + 1] === s[j] + 1) j++; out.push(j > i ? `${s[i]}–${s[j]}` : `${s[i]}`); i = j; }
  return out.join(", ");
}

// ---------------- page picker for transcription ----------------
// mode "v": pages that are only an image (no text) · mode "vi": text pages with a real figure (optional)
// select: click · Shift+click = range · drag with the mouse = box (starting on a selected page deselects) · double-click = big
const NOISE_ROLES = new Set(["org", "lit", "thanks", "divider", "interactive", "dup", "anim", "notexam", "notexam_t"]);
const SHORT_ROLE = { org: "Organisatorisch", lit: "Literatur", thanks: "Abschluss", divider: "Trennfolie", interactive: "Umfrage",
  dup: "Duplikat", anim: "Animation", notexam: "nicht klausurrel.", notexam_t: "Notiz: nicht rel." };
async function openPicker(cid, m, mode, onDone, statusEl) {
  const d = await api("GET", `/api/courses/${cid}/materials/${m.id}`);
  const ch = d.chunks;
  const unit = { pdf: "Seite", pptx: "Folie", image: "Bild" }[d.kind] || "Abschnitt";
  const units = { pdf: "Seiten", pptx: "Folien", image: "Bilder" }[d.kind] || "Abschnitte";
  const num = (c, i) => (c.pg ?? i) + 1;
  const isCand = c => c.st !== "x" && !c.tr && (mode === "v" ? c.v : c.vi);
  const all = ch.map((c, i) => [c, i]).filter(([c]) => isCand(c));
  if (!all.length) return toast("Keine passenden Seiten (mehr).");
  const skippedN = all.filter(([c]) => c.notr).length;
  let showSkipped = false;
  const visible = () => all.filter(([c]) => showSkipped || !c.notr);
  // image-only pages: preselect everything with learning content; figures: nothing preselected (optional)
  const sel = new Set(mode === "v" ? all.filter(([c]) => !c.notr && !NOISE_ROLES.has(c.role)).map(([, i]) => i) : []);
  const PER = mode === "v" ? 1300 : 1150; // ≈ tokens per page incl. image, output and share of the call overhead
  const thumb = i => `/api/courses/${cid}/materials/${m.id}/img/${i}?w=320`;

  const bg = modal(`<div class="row between"><h2 style="margin:0">${mode === "v" ? `${units} ohne Text transkribieren` : "Abbildungen transkribieren"} – ${esc(m.name)}</h2><button class="ghost" data-close>✕</button></div>
    <p class="small muted" style="margin:6px 0 8px">${mode === "v"
      ? `Diese ${units} bestehen nur aus einem Bild – ohne Transkription fehlen sie in Klausuren und Lernzetteln. Wähle aus, welche Claude lesen soll. Seiten ohne Lerninhalt (Organisatorisches, Titelfolien …) sind vorab abgewählt.`
      : `Diese ${units} haben schon Text <b>und</b> eine echte Abbildung (Diagramm, Modell, Grafik – keine Logos). Der Text bleibt; Claude beschreibt nur die Abbildung. Wähle nur Abbildungen aus, die für die Klausur wichtig sind.`}</p>
    ${m.sugg ? `<div class="hint small">Tipp: Für diese Datei gibt es noch ${m.sugg} offene Bereinigungs-Vorschläge (Schritt ②) – danach sind hier weniger ${units}.</div>` : ""}
    <div class="row pick-tools"><button class="small" data-pk="all">Alle</button><button class="small" data-pk="none">Keine</button><button class="small" data-pk="inv">Umkehren</button>
      <button class="small" data-pk="content" title="Alle außer Organisatorisches, Literatur, Titel-/Trennfolien, Umfragen, Duplikate">Nur Lerninhalte</button>
      ${skippedN ? `<label class="chk small"><input type="checkbox" id="pskip"> früher übersprungene anzeigen (${skippedN})</label>` : ""}
      <span class="grow"></span><span id="pstat" class="small"></span></div>
    <div class="tiny muted" style="margin:4px 0">Klick = auswählen · Umschalt+Klick = Bereich · mit gedrückter Maus ziehen = mehrere (auf einer ausgewählten Seite beginnen = abwählen) · Doppelklick = groß anzeigen</div>
    <div id="pgrid" class="pgrid"></div>
    <div class="row pick-foot">${mode === "v"
      ? `<label class="chk small" title="Ohne Text wären sie sonst leer und würden immer wieder als offen angezeigt"><input type="checkbox" id="pexcl" checked> Nicht ausgewählte ${units} ausschließen</label>`
      : `<span class="tiny muted">Nicht ausgewählte werden als „keine Transkription nötig“ gemerkt – jederzeit wieder auswählbar.</span>`}
      <span class="grow"></span><button class="small" id="psave" title="Entscheidungen speichern, ohne jetzt zu transkribieren">Nur speichern</button><button class="primary" id="pgo">Transkribieren</button></div>`,
    () => { window.removeEventListener("mousemove", onMove); window.removeEventListener("mouseup", onUp); box.remove(); }, "wide");
  const grid = $("#pgrid", bg), box = el(`<div id="marquee" hidden></div>`);
  document.body.appendChild(box);
  const tile = i => $(`.ptile[data-i="${i}"]`, grid);
  const setSel = (i, on) => {
    on ? sel.add(i) : sel.delete(i);
    const t = tile(i); if (!t) return;
    t.classList.toggle("on", on); $(".pcheck", t).textContent = on ? "✓" : "";
  };
  const stat = () => {
    const n = visible().filter(([, i]) => sel.has(i)).length;
    $("#pstat", bg).innerHTML = `<b>${n}</b> von ${visible().length} ausgewählt · ≈ <b>${tok(n * PER)}</b> Tokens`;
    $("#pgo", bg).disabled = !n;
    $("#pgo", bg).textContent = n ? `${n} ${n === 1 ? unit : units} transkribieren` : "Transkribieren";
  };
  const render = () => {
    grid.innerHTML = visible().map(([c, i]) => `<div class="ptile ${sel.has(i) ? "on" : ""} ${c.notr ? "skipped" : ""}" data-i="${i}" title="${esc(c.t)}">
      <img loading="lazy" src="${thumb(i)}" alt="" draggable="false"><span class="pcheck">${sel.has(i) ? "✓" : ""}</span>
      <div class="pcap"><b>${unit} ${num(c, i)}</b> ${/^(Seite|Folie|Bild|Abschnitt) \d+$/.test(c.t || "") ? "" : esc(c.t)}</div>
      ${NOISE_ROLES.has(c.role) ? `<span class="prole" title="${esc(ROLE_LABEL[c.role] || c.role)}">${SHORT_ROLE[c.role] || c.role}</span>` : ""}</div>`).join("");
    stat();
  };
  render();

  $(".pick-tools", bg).onclick = e => {
    const b = e.target.closest("[data-pk]"); if (!b) return;
    const k = b.dataset.pk;
    visible().forEach(([c, i]) => setSel(i, k === "all" ? true : k === "none" ? false : k === "inv" ? !sel.has(i) : !NOISE_ROLES.has(c.role)));
    stat();
  };
  const ps = $("#pskip", bg);
  if (ps) ps.onchange = () => { showSkipped = ps.checked; render(); };

  // click / shift-range / drag-box selection
  const order = () => visible().map(([, i]) => i);
  let last = null, drag = null;
  const sc = bg; // the modal background is the scroll container
  grid.addEventListener("mousedown", e => {
    if (e.button !== 0) return;
    const t = e.target.closest(".ptile");
    e.preventDefault();
    drag = { x: e.clientX, y: e.clientY + sc.scrollTop, moved: false, t, on: t ? !sel.has(+t.dataset.i) : true, base: new Set(sel), shift: e.shiftKey };
  });
  function onMove(e) {
    if (!drag) return;
    const y0 = drag.y - sc.scrollTop;
    if (!drag.moved && Math.hypot(e.clientX - drag.x, e.clientY - y0) < 6) return;
    drag.moved = true;
    if (e.clientY > innerHeight - 40) sc.scrollTop += 18; else if (e.clientY < 40) sc.scrollTop -= 18;
    const top = drag.y - sc.scrollTop;
    const r = { l: Math.min(drag.x, e.clientX), r: Math.max(drag.x, e.clientX), t: Math.min(top, e.clientY), b: Math.max(top, e.clientY) };
    Object.assign(box.style, { left: r.l + "px", top: r.t + "px", width: r.r - r.l + "px", height: r.b - r.t + "px" });
    box.hidden = false;
    $$(".ptile", grid).forEach(t => {
      const b = t.getBoundingClientRect(), i = +t.dataset.i;
      const hit = b.left < r.r && b.right > r.l && b.top < r.b && b.bottom > r.t;
      const want = hit ? drag.on : drag.base.has(i);
      if (want !== sel.has(i)) setSel(i, want);
    });
    stat();
  }
  function onUp(e) {
    if (!drag) return;
    const d0 = drag; drag = null; box.hidden = true;
    if (d0.moved || !d0.t) return;
    const i = +d0.t.dataset.i, on = !sel.has(i), ord = order();
    if (d0.shift && last != null && ord.includes(last)) {
      const [a, b] = [ord.indexOf(last), ord.indexOf(i)].sort((x, y) => x - y);
      ord.slice(a, b + 1).forEach(k => setSel(k, on));
    } else setSel(i, on);
    last = i; stat();
  }
  window.addEventListener("mousemove", onMove);
  window.addEventListener("mouseup", onUp);
  grid.addEventListener("dblclick", e => { const t = e.target.closest(".ptile"); if (t) window.open(`/api/courses/${cid}/materials/${m.id}/img/${t.dataset.i}`, "_blank"); });

  const submit = async go => {
    const vis = visible().map(([, i]) => i);
    const pages = go ? vis.filter(i => sel.has(i)) : [];
    const unsel = vis.filter(i => !sel.has(i));
    const body = { pages };
    if (mode === "v" && $("#pexcl", bg).checked) body.exclude = unsel; else body.skip = unsel;
    if (!go) body.unskip = vis.filter(i => sel.has(i)); // keep selected ones open for later
    if (go && !confirm(`${pages.length} ${pages.length === 1 ? unit : units} mit Claude (${m.kind === "image" ? "Bild" : "Seitenbild"}) transkribieren: ≈ ${tok(pages.length * PER)} Tokens.` +
      (unsel.length ? `\n${unsel.length} nicht ausgewählte werden ${body.exclude ? "ausgeschlossen" : "als „keine Transkription nötig“ gemerkt"}.` : "") + "\n\nStarten?")) return;
    const r = await api("POST", `/api/courses/${cid}/materials/${m.id}/transcribe`, body);
    bg.close();
    if (!r.id) { toast("Auswahl gespeichert"); return onDone && onDone(); }
    try {
      await poll(r.id, s => { if (statusEl) statusEl.innerHTML = `<span class="spinner"></span> Transkribiere „${esc(m.name)}“: ${s.progress ? `${s.progress[0]}/${s.progress[1]}` : "startet"} · ${s.elapsed} s`; });
      toast(`${pages.length} ${pages.length === 1 ? unit : units} transkribiert`);
    } catch (err) { toast(err.message, true); }
    if (statusEl) statusEl.textContent = "";
    if (onDone) onDone();
  };
  $("#pgo", bg).onclick = () => submit(true);
  $("#psave", bg).onclick = () => submit(false);
}

// ---------------- material preview & marking ----------------
// Optional layer: exclude pages/slides, mark chunks or text spans as relevant / very important / not relevant.
// Nothing marked -> material is used exactly as extracted.
const MARK = { imp: "Sehr wichtig", rel: "Relevant", irr: "Nicht relevant" };
const ROLE_LABEL = { goals: "Lernziele", exercise: "Übung/Aufgabe", solution: "Lösung/Besprechung", examinfo: "Prüfungsinfo",
  org: "Organisatorisches/Gliederung", lit: "Literatur/Quellen", thanks: "Abschluss/Danke", divider: "Titel-/Trennfolie",
  interactive: "Umfrage/Interaktion", anim: "Animationsschritt", dup: "Duplikat" };

ROLE_LABEL.key = "Ausdrücklich prüfungsrelevant";
ROLE_LABEL.notexam = "Laut Material nicht klausurrelevant";
ROLE_LABEL.notexam_t = "Notiz „nicht klausurrelevant“";

// AI pre-check (text only): refines roles + summarizes exam info; results show up as suggestions
async function runTriage(cid, m, btn) {
  const t = m.triage || {};
  if (!confirm(`KI-Vorprüfung für „${m.name}“?\n\n${t.pages} aktive Seiten/Abschnitte – gesendet werden nur Titel und Textanfang (keine Bilder): ≈ ${tok(t.est_in)} Eingabe-Tokens.\n` +
    `Claude erkennt Lernziele, Übungen, Prüfungsinfos, ausdrückliche Prüfungshinweise und Seiten ohne Lerninhalt.\nDanach siehst du Vorschläge – nichts wird automatisch geändert.`)) return false;
  const label = btn ? btn.innerHTML : "";
  if (btn) btn.disabled = true;
  try {
    const j = await api("POST", `/api/courses/${cid}/materials/${m.id}/triage`);
    const n = await poll(j.id, s => { if (btn) btn.innerHTML = `<span class="spinner"></span> ${s.progress ? `${s.progress[0]}/${s.progress[1]} · ` : ""}${s.elapsed} s`; });
    toast(`KI-Vorprüfung fertig – ${n} offene Vorschläge`);
    return true;
  } catch (e) { toast(e.message, true); return false; }
  finally { if (btn) { btn.disabled = false; btn.innerHTML = label; } }
}

// suggestions (local analysis + optional AI pre-check): grouped by category, nothing happens without confirmation
async function openSuggestions(cid, m, onClose) {
  const S = await api("GET", `/api/courses/${cid}/materials/${m.id}/suggestions`);
  if (!S.cats.length) { toast("Keine offenen Vorschläge."); if (onClose) onClose(); return; }
  const units = { Seite: "Seiten", Folie: "Folien" }[S.unit] || "Abschnitte";
  const bg = modal(`<div class="row between"><h2 style="margin:0">Vorschläge für ${esc(m.name)}</h2><button class="ghost" data-close>✕</button></div>
    <p class="small muted" style="margin:6px 0 12px">${S.triaged ? `Lokale Analyse + KI-Vorprüfung (${S.triaged} von ${S.live} aktiven ${units} geprüft).` : "Lokale Analyse (0 Tokens) anhand von Titeln und Text."}
      Nichts wird ohne deine Bestätigung geändert, alles bleibt in „Vorschau & markieren“ umkehrbar. ${units}, die du schon selbst markiert hast, werden nicht angefasst.</p>
    ${S.triaged < S.live && m.triage && m.triage.pages ? `<div class="hint small row" style="margin-bottom:10px"><span class="grow">Genauer mit KI-Vorprüfung: erkennt auch Prüfungshinweise im Text und fasst Prüfungsinfos zusammen.</span>
      <button class="small" id="stri">🔎 KI-Vorprüfung (≈ ${tok(m.triage.est_in)})</button></div>` : ""}
    <div id="scats">${S.cats.map(c => `<div class="scat"><label class="chk"><input type="checkbox" value="${c.role}" checked>
      <b>${esc(c.label)}</b> <span class="muted small">· ${c.pages.length} ${c.pages.length === 1 ? S.unit : units} → ${esc(c.action_label)}</span></label>
      ${c.action === "instr" && S.info ? `<div class="pre" style="margin:6px 0 0 24px" title="Zusammenfassung der KI-Vorprüfung – wird so in die Kursvorgaben übernommen">${esc(S.info)}</div>` : ""}
      <details><summary class="tiny">${units} anzeigen</summary><ul class="small slist">${c.pages.map(p => `<li><b>${S.unit} ${p.num}</b> ${esc(p.t)}${p.why ? ` <span class="tiny muted">· ${esc(p.why)}</span>` : ""}
        ${c.action === "instr" && !S.info ? `<div class="pre">${esc(p.x)}${p.x.length >= 160 ? " …" : ""}</div>` : ""}</li>`).join("")}</ul></details></div>`).join("")}</div>
    <div class="row" style="margin-top:14px"><button class="primary" id="sgo">Ausgewählte übernehmen</button><button id="slater">Später</button>
      <span class="grow"></span><button class="ghost small" id="sdis" title="Vorschläge ausblenden (Rollen bleiben im Filter der Vorschau sichtbar)">Ignorieren</button></div>`,
    onClose);
  $("#sgo", bg).onclick = async () => {
    const roles = $$("#scats input:checked", bg).map(i => i.value);
    const r = await api("POST", `/api/courses/${cid}/materials/${m.id}/suggestions`, { roles });
    toast(`${r.changed} ${units} angepasst${r.added ? " · Prüfungsinfo in Kursvorgaben übernommen" : ""}`); bg.close();
  };
  $("#slater", bg).onclick = () => bg.close();
  const tri = $("#stri", bg);
  if (tri) tri.onclick = async () => {
    if (!(await runTriage(cid, m, tri))) return;
    bg.remove(); // reopen with the refined suggestions (without triggering onClose twice)
    const C2 = await api("GET", "/api/courses/" + cid);
    openSuggestions(cid, C2.materials.find(x => x.id === m.id) || m, onClose);
  };
  $("#sdis", bg).onclick = async () => { await api("POST", `/api/courses/${cid}/materials/${m.id}/suggestions`, { dismiss: true }); bg.close(); };
}
function resolveMarks(x, mk) {
  const rs = [];
  (mk || []).forEach((m, idx) => {
    if (!m.t) return;
    const s = m.o >= 0 && x.substr(m.o, m.t.length) === m.t ? m.o : x.indexOf(m.t);
    if (s >= 0) rs.push([s, s + m.t.length, m.k, idx]);
  });
  rs.sort((a, b) => a[0] - b[0]);
  const out = []; let end = -1;
  for (const r of rs) if (r[0] >= end) { out.push(r); end = r[1]; }
  return out;
}
async function openMaterial(cid, m, onClose) {
  const d = await api("GET", `/api/courses/${cid}/materials/${m.id}`);
  const ch = d.chunks;
  const noTopic = new Set(d.unassigned || []);
  const unit = { pdf: "Seite", pptx: "Folie", image: "Bild" }[d.kind] || "Abschnitt";
  const units = { pdf: "Seiten", pptx: "Folien", image: "Bilder" }[d.kind] || "Abschnitte";
  const num = (c, i) => (c.pg ?? i) + 1;
  const imgUrl = i => `/api/courses/${cid}/materials/${m.id}/img/${i}`;
  const hasImg = c => d.kind === "pdf" || (c.img && c.img.length);
  const rank = c => c.st === "imp" || (c.mk || []).some(x => x.k === "imp") ? 0 : c.st === "rel" || (c.mk || []).some(x => x.k === "rel") ? 1 : 2;
  const effLen = c => { const x = c.x || ""; return x.length - resolveMarks(x, c.mk).filter(r => r[2] === "irr").reduce((a, r) => a + r[1] - r[0], 0); };
  let filter = "all", changed = false;
  const tool = el(`<div id="mtool" hidden></div>`);
  document.body.appendChild(tool);
  const roleCount = {};
  ch.forEach(c => { if (c.role && c.role !== "content") roleCount[c.role] = (roleCount[c.role] || 0) + 1; });
  const bg = modal(`<div class="row between"><h2 style="margin:0">${esc(m.name)}</h2><div class="row">
      <button class="small" id="mana" title="Lokale Analyse erneut ausführen (Abbildungen, Rollen) – 0 Tokens">Neu analysieren</button>
      <button class="primary" data-close>Fertig</button></div></div>
    <p class="small muted" style="margin:6px 0 12px">Optional: ${units} ausschließen oder als <b>relevant</b> / <b>sehr wichtig</b> kennzeichnen. Text mit der Maus markieren → sehr wichtig / relevant / nicht relevant.
      Ohne Markierungen wird alles normal verwendet. Die Originaldatei bleibt unverändert, alles ist umkehrbar.</p>
    <div class="row" style="margin-bottom:12px">
      <div class="seg" id="mf"><button data-f="all" class="on">Alle</button><button data-f="marked">Markiert</button><button data-f="x">Ausgeschlossen</button>${(d.unassigned || []).length ? `<button data-f="nt" title="Aktive Inhaltsseiten, die zu keinem Thema gehören">Ohne Thema (${d.unassigned.length})</button>` : ""}</div>
      ${Object.keys(roleCount).length ? `<select id="mrole" title="Nach Rolle filtern (lokale Analyse)"><option value="">Alle Rollen</option>${Object.entries(roleCount).map(([r, n]) => `<option value="${r}">${ROLE_LABEL[r] || r} (${n})</option>`).join("")}</select>` : ""}
      <form class="row" id="mr"><input name="r" placeholder="${units}, z. B. 1-3, 40-45" style="width:190px"><button class="small" data-a="x">ausschließen</button><button class="small" data-a="">einschließen</button>
        <button class="small" data-sty title="Aufgaben, Altklausur-/Quizfragen oder Lösungen als Vorlage für den Aufgabenstil – bleiben normales Lernmaterial. Leeres Feld = ganze Datei.">als Stilvorlage</button>
        ${ch.some(c => c.tr) ? `<button class="small" data-reset title="Stellt für transkribierte ${units} den lokal extrahierten Originaltext wieder her (0 Tokens), danach neu transkribierbar. Leeres Feld = alle.">Transkription zurücksetzen</button>` : ""}</form>
      <span class="grow"></span><span id="mstat" class="small muted"></span></div>
    <div id="mlist"></div>`, () => { tool.remove(); if (changed) onClose(); }, "wide");
  const list = $("#mlist", bg);

  const save = items => { changed = true; stat(); return api("PUT", `/api/courses/${cid}/materials/${m.id}/marks`, { items }).catch(e => toast(e.message, true)); };
  function stat() {
    const live = ch.filter(c => c.st !== "x");
    const sent = live.reduce((a, c) => a + effLen(c), 0), raw = ch.reduce((a, c) => a + (c.x || "").length, 0);
    $("#mstat", bg).innerHTML = `${ch.length} ${units} · ${ch.length - live.length} ausgeschlossen · ${live.filter(c => rank(c) < 2).length} markiert · ${live.filter(c => c.sty).length} Stilvorlagen · ≈ <b>${tok(sent / CPT)}</b> von ${tok(raw / CPT)} Tokens`;
  }
  function textHTML(c) {
    const x = c.x || "";
    if (!x) return `<span class="muted">${c.v ? "Nur Bild – erst transkribieren, dann markierbar." : "–"}</span>`;
    let h = "", pos = 0;
    for (const [s, e, k, idx] of resolveMarks(x, c.mk)) { h += esc(x.slice(pos, s)) + `<mark class="k-${k}" data-m="${idx}" title="${MARK[k]} – klicken zum Entfernen">${esc(x.slice(s, e))}</mark>`; pos = e; }
    return h + esc(x.slice(pos));
  }
  function card(c, i) {
    const st = c.st || "";
    return `<div class="mcard st-${st || "n"} ${hasImg(c) ? "" : "nothumb"}" data-i="${i}">
      ${hasImg(c) ? `<div class="mthumb"><a href="${imgUrl(i)}" target="_blank"><img loading="lazy" src="${imgUrl(i)}" alt="${unit} ${num(c, i)}"></a></div>` : ""}
      <div style="min-width:0"><div class="row between"><div class="grow" style="min-width:0"><b>${unit} ${num(c, i)}</b> <span class="small muted">${esc(c.t)}</span>
        ${c.role && c.role !== "content" ? `<span class="chip" title="Rolle laut ${c.rsrc === "ai" ? "KI-Vorprüfung" : "lokaler Analyse"}">${ROLE_LABEL[c.role] || c.role}</span>` : ""}
        ${c.tr ? '<span class="badge good">transkribiert</span>' : ""}</div>
        <div class="row" style="gap:6px"><button class="chip ${c.sty ? "on" : ""}" data-styt title="Als Vorlage für den Aufgabenstil der Klausur nutzen (Aufgaben, Quizfragen, Lösungen) – bleibt Lernmaterial">✎ Stilvorlage</button>
        <div class="seg">${[["x", "Ausschließen"], ["", "Normal"], ["rel", "Relevant"], ["imp", "Sehr wichtig"]].map(([k, l]) => `<button data-st="${k}" class="${k} ${st === k ? "on" : ""}">${l}</button>`).join("")}</div></div></div>
      <div class="mtext">${textHTML(c)}</div></div></div>`;
  }
  function render() {
    const role = $("#mrole", bg)?.value || "";
    const vis = ch.map((c, i) => [c, i]).filter(([c, i]) => (!role || c.role === role) &&
      (filter === "all" || (filter === "nt" ? noTopic.has(i) : filter === "x" ? c.st === "x" : c.st !== "x" && (rank(c) < 2 || c.sty || (c.mk || []).length))));
    list.innerHTML = vis.map(([c, i]) => card(c, i)).join("") || `<p class="muted">Nichts in dieser Ansicht.</p>`;
    stat();
  }
  const rerenderCard = i => { const old = $(`.mcard[data-i="${i}"]`, list); if (old) old.outerHTML = card(ch[i], i); };

  $("#mf", bg).onclick = e => { const b = e.target.closest("[data-f]"); if (!b) return; filter = b.dataset.f; $$("#mf button", bg).forEach(x => x.classList.toggle("on", x === b)); render(); };
  $("#mr", bg).onsubmit = e => e.preventDefault();
  const mrole = $("#mrole", bg);
  if (mrole) mrole.onchange = render;
  const reopen = () => { changed = true; bg.close(); openMaterial(cid, m, onClose); };
  $("#mana", bg).onclick = async () => {
    const r = await api("POST", `/api/courses/${cid}/materials/${m.id}/analyze`);
    toast(`Analyse aktualisiert – ${r.pending} offene Vorschläge`); reopen();
  };
  const rb = $("[data-reset]", bg);
  if (rb) rb.onclick = async e => {
    e.preventDefault();
    const range = $("#mr", bg).r.value.trim();
    const n = ch.filter(c => c.tr).length;
    if (!confirm(range ? `Transkription für ${units} ${range} zurücksetzen? Der lokal extrahierte Originaltext wird wiederhergestellt (0 Tokens).`
      : `Transkription für ALLE ${n} transkribierten ${units} zurücksetzen? Der lokal extrahierte Originaltext wird wiederhergestellt (0 Tokens), danach kannst du neu transkribieren.`)) return;
    rb.disabled = true; rb.innerHTML = `<span class="spinner"></span>`;
    try { const r = await api("POST", `/api/courses/${cid}/materials/${m.id}/reset`, { range }); toast(`${r.reset} ${units} zurückgesetzt`); reopen(); }
    catch (err) { toast(err.message, true); rb.disabled = false; rb.textContent = "Transkription zurücksetzen"; }
  };
  const pageSet = s => {
    const want = new Set();
    for (const part of s.split(/[,;\s]+/)) {
      const mm = part.match(/^(\d+)(?:-(\d+))?$/); if (!mm) continue;
      for (let k = +mm[1]; k <= +(mm[2] || mm[1]) && k < +mm[1] + 2000; k++) want.add(k);
    }
    return want;
  };
  $$("#mr button[data-a]", bg).forEach(b => b.onclick = e => {
    e.preventDefault();
    const want = pageSet($("#mr", bg).r.value);
    const items = [];
    ch.forEach((c, i) => { if (want.has(num(c, i))) { if (b.dataset.a) c.st = "x"; else if (c.st === "x") delete c.st; items.push({ i, st: c.st || "" }); } });
    if (!items.length) return toast(`Keine passenden ${units}.`, true);
    save(items); render();
    toast(`${items.length} ${units} ${b.dataset.a ? "ausgeschlossen" : "eingeschlossen – ggf. transkribieren und danach unter „Themen“ aktualisieren"}`);
  });
  $("[data-sty]", bg).onclick = e => {
    e.preventDefault();
    const want = pageSet($("#mr", bg).r.value);
    if (!want.size && !confirm(`Alle aktiven ${units} dieser Datei als Stilvorlage nutzen? (z. B. für eine Altklausur oder ein Übungsblatt)`)) return;
    const items = [];
    ch.forEach((c, i) => { if (c.st !== "x" && (!want.size || want.has(num(c, i)))) { c.sty = true; items.push({ i, sty: true }); } });
    if (!items.length) return toast(`Keine passenden ${units}.`, true);
    save(items); render(); toast(`${items.length} ${units} als Stilvorlage markiert – bleiben Lernmaterial`);
  };
  list.addEventListener("click", e => {
    const sb = e.target.closest("[data-styt]");
    if (sb) {
      const i = +sb.closest(".mcard").dataset.i, c = ch[i];
      c.sty = !c.sty; save([{ i, sty: c.sty }]);
      if (filter !== "all") render(); else rerenderCard(i);
      return;
    }
    const b = e.target.closest("[data-st]");
    if (!b) return;
    const i = +b.closest(".mcard").dataset.i, c = ch[i];
    if (b.dataset.st) c.st = b.dataset.st; else delete c.st;
    save([{ i, st: c.st || "" }]);
    if (filter !== "all") render(); else rerenderCard(i);
  });

  // --- text selection -> mark toolbar
  const hide = () => { tool.hidden = true; };
  function showTool(rect, html, onPick) {
    tool.innerHTML = html; tool.hidden = false;
    const w = tool.offsetWidth;
    tool.style.left = Math.max(8, Math.min(innerWidth - w - 8, rect.left + rect.width / 2 - w / 2)) + "px";
    tool.style.top = (rect.top > 50 ? rect.top - 44 : rect.bottom + 8) + "px";
    tool.onmousedown = ev => ev.preventDefault(); // keep the selection alive
    tool.onclick = ev => { const k = ev.target.closest("[data-k]"); if (k) { onPick(k.dataset.k); hide(); } };
  }
  function onSelect(e) {
    if (e.target.closest && e.target.closest("#mtool")) return;
    const mk = e.target.closest && e.target.closest("mark[data-m]");
    const sel = getSelection();
    if (mk && sel.isCollapsed) {
      const i = +mk.closest(".mcard").dataset.i, idx = +mk.dataset.m;
      return showTool(mk.getBoundingClientRect(), `<span class="small muted" style="padding:3px 6px">${MARK[ch[i].mk[idx].k]}</span><button class="small" data-k="del">Markierung entfernen</button>`, () => {
        ch[i].mk.splice(idx, 1); save([{ i, mk: ch[i].mk }]); rerenderCard(i);
      });
    }
    if (sel.isCollapsed || !sel.rangeCount) return hide();
    const r = sel.getRangeAt(0);
    const anc = r.commonAncestorContainer.nodeType === 1 ? r.commonAncestorContainer : r.commonAncestorContainer.parentElement;
    const box = anc && anc.closest(".mtext");
    if (!box || !list.contains(box)) return hide();
    const pre = document.createRange(); pre.selectNodeContents(box); pre.setEnd(r.startContainer, r.startOffset);
    let s = pre.toString().length, t = r.toString();
    s += t.length - t.trimStart().length; t = t.trim();
    if (t.length < 2) return hide();
    const i = +box.closest(".mcard").dataset.i;
    showTool(r.getBoundingClientRect(), Object.entries(MARK).map(([k, l]) => `<button class="small mk-${k}" data-k="${k}">${l}</button>`).join(""), k => {
      const c = ch[i], e2 = s + t.length, hits = resolveMarks(c.x, c.mk);
      const keep = (c.mk || []).filter((_, idx) => !hits.some(([a, b, , j]) => j === idx && a < e2 && b > s));
      c.mk = [...keep, { k, t, o: s }];
      save([{ i, mk: c.mk }]); sel.removeAllRanges(); rerenderCard(i);
    });
  }
  list.addEventListener("mouseup", e => setTimeout(() => onSelect(e), 0));
  list.addEventListener("touchend", e => setTimeout(() => onSelect(e), 300));
  bg.addEventListener("scroll", hide, true);
  render();
}

// ---------------- exam ----------------
async function viewExam(cid, eid) {
  let E = await api("GET", `/api/courses/${cid}/exams/${eid}`);
  const graded = E.status === "graded";
  const A = E.answers || {};
  const R = graded ? E.result.parts : {};
  const sketches = {};
  let elapsed = E.elapsed || 0, dirty = false, saveT;
  const ans = pid => (A[pid] = A[pid] || {});
  const save = () => {
    if (graded) return;
    dirty = true; clearTimeout(saveT);
    saveT = setTimeout(flush, 1200);
    updCount();
  };
  const flush = () => { if (!dirty || graded) return; dirty = false; api("PUT", `/api/courses/${cid}/exams/${eid}/answers`, { answers: A, elapsed }).catch(e => toast("Speichern fehlgeschlagen: " + e.message, true)); };
  cleanups.push(() => { clearTimeout(saveT); flush(); });
  window.addEventListener("beforeunload", flush);
  cleanups.push(() => window.removeEventListener("beforeunload", flush));

  const parts = E.tasks.flatMap(t => t.parts.map(p => [t, p]));
  const answered = p => {
    const a = A[p.id] || {};
    return !!((a.sel && a.sel.length) || (a.tf && a.tf.some(x => x != null)) || (a.gaps && a.gaps.some(Boolean)) || a.touched ||
      (a.map && a.map.some(x => x != null)) || (a.val || "").trim() || (a.work || "").trim() || (a.text || "").trim() ||
      (a.code != null && a.code.trim() !== (p.start || "").trim()) || (a.img && a.img.length) || (a.sketch && a.sketch.s && a.sketch.s.length));
  };

  const back = E.parent ? `<a href="#/c/${cid}/e/${E.parent}" class="small">← Ursprüngliche Klausur</a> · ` : "";
  app.innerHTML = `<div class="exam-head"><div class="row between"><div>${back}<a href="#/c/${cid}" class="small">← Kurs</a>
    <h1 style="margin:0">${KIND[E.kind] ? `<span class="chip">${KIND[E.kind]}</span> ` : ""}${esc(E.title)}</h1></div>
    <div class="row" id="ehead"></div></div></div><div id="summary"></div><div id="tasks"></div>`;
  function updCount() {
    if (graded) return;
    const n = parts.filter(([, p]) => answered(p)).length;
    const c = $("#cnt"); if (c) c.textContent = `${n}/${parts.length} bearbeitet`;
  }
  if (!graded) {
    $("#ehead").innerHTML = `<span class="small muted" id="cnt"></span><span class="mono" id="timer"></span><button class="primary" id="submit">Abgeben & korrigieren</button>`;
    const tick = () => { const m = Math.floor(elapsed / 60), s = elapsed % 60; const over = elapsed > E.min * 60; $("#timer").innerHTML = `<span class="${over ? "badge bad" : ""}">${m}:${String(s).padStart(2, "0")}</span> <span class="muted">/ ${E.min}:00</span>`; };
    const iv = setInterval(() => { if (!document.hidden) { elapsed++; tick(); if (elapsed % 30 === 0) { dirty = true; flush(); } } }, 1000);
    cleanups.push(() => clearInterval(iv)); tick(); updCount();
    $("#submit").onclick = submit;
  } else {
    $("#ehead").innerHTML = `<button id="retake">Erneut schreiben</button>`;
    $("#retake").onclick = async () => { const r = await api("POST", `/api/courses/${cid}/exams/${eid}/retake`); location.hash = `#/c/${cid}/e/${r.id}`; };
    renderSummary();
  }

  function renderSummary() {
    const r = E.result, u = E.usage || {};
    const wrong = parts.filter(([, p]) => (r.parts[p.id]?.p ?? 0) < p.pts).map(([, p]) => p.id);
    const usage = ["gen", "grade"].filter(k => u[k]).map(k => `${k === "gen" ? "Erstellung" : "Korrektur"}: ${tok(u[k].in + u[k].cache_w + u[k].cache_r)} ein / ${tok(u[k].out)} aus`).join(" · ");
    const rows = (obj, lab) => Object.entries(obj).map(([k, [g, m]]) => `<tr><td>${lab(k)}</td><td style="width:45%"><div class="bar" title="${fmt(g)} / ${fmt(m)} P."><i style="width:${m ? 100 * g / m : 0}%"></i></div></td><td class="n">${g.toLocaleString("de-DE")} / ${m.toLocaleString("de-DE")}</td></tr>`).join("");
    $("#summary").innerHTML = `<div class="card"><div class="row" style="gap:28px;align-items:flex-start">
      <div><div class="hero">${Math.round(r.pct)} %</div><div class="muted small">${r.total.toLocaleString("de-DE")} / ${r.max.toLocaleString("de-DE")} Punkte · Note ${r.grade}</div></div>
      <div class="grow" style="min-width:260px"><table class="t"><tbody>${rows(r.by_ab, k => "AB " + AB[k])}${rows(r.by_topic, k => esc(k))}</tbody></table></div></div>
      ${(r.tips || []).length ? `<h3 style="margin-top:14px">Lernhinweise</h3><ul class="md">${r.tips.map(t => `<li>${md(t, true)}</li>`).join("")}</ul>` : ""}
      ${wrong.length ? `<div class="row" style="margin-top:12px"><button id="varall">↻ ${wrong.length === 1 ? "Fehler" : `Alle ${wrong.length} Fehler`} variieren</button>
        <span class="tiny muted">Neue Aufgaben zum selben Konzept – nur die Aufgaben und passende Materialausschnitte werden gesendet</span></div>` : ""}
      <div class="tiny muted" style="margin-top:8px">${usage}</div></div>`;
    if (wrong.length) $("#varall").onclick = e => makeVariants(wrong, e.currentTarget);
    renderNext();
  }

  // learning path: what this quiz/variation means per topic and the one next step
  function renderNext() {
    const P = E.path;
    if (!P) return;
    const lv = k => `Stufe ${k} – ${esc(P.levels[k])}`;
    const rows = P.topics.map(t => {
      const res = `<b class="${t.ok ? "good-t" : "bad-t"}">${t.pct} %</b>`;
      let txt, btn = "";
      if (t.ok && (t.done || t.level > P.level)) {
        txt = t.done ? `✓ ${res} – Stufe 3 bestanden, <b>alle Stufen geschafft</b>. Weiter mit einer Probeklausur.`
          : `✓ ${res} – bestanden. Weiter mit <b>${lv(t.level)}</b>.`;
        btn = t.done ? "" : `<button class="primary" data-nq="${t.id}" data-lv="${t.level}">Quiz ${lv(t.level)} starten</button>`;
      } else if (!t.ok && t.drill === E.id) {
        txt = `${res} – unter ${P.pass} %. Nächster Schritt: ${t.fails.length === 1 ? "deinen Fehler" : `deine ${t.fails.length} Fehler`} mit Variationen üben; ab ${P.pass} % geht es weiter${P.level < 3 ? ` zu Stufe ${P.level + 1}` : ""}.`;
        btn = `<button class="primary" data-nv="${t.id}">↻ ${t.fails.length === 1 ? "Fehler" : `${t.fails.length} Fehler`} variieren</button>`;
      } else {
        txt = `${res} – zählt als Extra-Übung, dein Lernpfad steht auf <b>${lv(t.level)}</b>${t.drill ? " (Variationen offen)" : ""}.`;
      }
      return `<div class="nrow"><div class="row between"><b>${esc(t.name)}</b><a class="small" href="#/c/${cid}/t/${t.id}">Thema & Lernpfad →</a></div>
        <div class="row" style="margin-top:4px"><span class="small grow">${txt}</span>${btn}</div><span class="nst small muted"></span></div>`;
    }).join("");
    const box = el(`<div class="card nextstep"><h3 style="margin:0 0 4px">Lernpfad · ${lv(P.level)}</h3>${rows}</div>`);
    $("#summary").prepend(box);
    box.onclick = e => {
      const q = e.target.closest("[data-nq]"), v = e.target.closest("[data-nv]");
      if (q) startGen(cid, { kind: "quiz", topics: [q.dataset.nq], level: +q.dataset.lv, minutes: 10 }, $(".nst", q.closest(".nrow")), q);
      if (v) makeVariants(P.topics.find(t => t.id === v.dataset.nv).fails, v);
    };
  }

  // --- render tasks
  const root = $("#tasks");
  for (const t of E.tasks) {
    const sum = t.parts.reduce((a, p) => a + p.pts, 0);
    const got = graded ? t.parts.reduce((a, p) => a + (R[p.id]?.p || 0), 0) : null;
    const tk = el(`<section class="task"><h2>Aufgabe ${t.id}: ${md(t.title, true)} <span class="chip">${esc(t.topic || "")}</span><span class="grow"></span><span class="small muted">${graded ? `${got.toLocaleString("de-DE")} / ` : ""}${sum.toLocaleString("de-DE")} P.</span></h2>
      ${t.ctx ? `<div class="ctx md">${md(t.ctx)}</div>` : ""}</section>`);
    root.appendChild(tk);
    for (const p of t.parts) tk.appendChild(renderPart(t, p));
  }

  function renderPart(t, p) {
    const a = ans(p.id), r = R[p.id];
    const ratio = r ? r.p / p.pts : 0;
    const box = el(`<div class="part" id="p-${p.id}"><div class="part-h"><b>${p.id.replace(/^\d+/, "")})</b>
      <span class="chip">AB ${AB[p.ab]}</span>${p.topic ? `<span class="chip">${esc(p.topic)}</span>` : ""}<span class="chip">${TYPES[p.type]}${p.lang ? " · " + esc(p.lang) : ""}</span><span class="small muted">${p.pts.toLocaleString("de-DE")} P.</span>
      ${r ? `<span class="badge ${ratio >= .85 ? "good" : ratio >= .4 ? "warn" : "bad"}">${r.p.toLocaleString("de-DE")} / ${p.pts.toLocaleString("de-DE")}</span>` : ""}</div>
      <div class="md">${md(p.q)}</div><div class="w"></div></div>`);
    const w = $(".w", box);
    const dis = graded ? "disabled" : "";
    const textarea = (key, rows, ph, cls = "") => {
      const ta = el(`<textarea rows="${rows}" class="${cls}" placeholder="${ph}" ${dis}></textarea>`);
      ta.value = a[key] ?? ""; ta.oninput = () => { a[key] = ta.value; save(); };
      return ta;
    };
    switch (p.type) {
      case "sc": case "mc": {
        const s = new Set(a.sel || []);
        const box2 = el(`<div class="opts"></div>`);
        p.opts.forEach((o, i) => {
          const cls = graded ? (p.ans.includes(i) ? "good" : s.has(i) ? "bad" : "") : "";
          const lab = el(`<label class="opt ${cls}"><input type="${p.type === "sc" ? "radio" : "checkbox"}" name="o${p.id}" ${s.has(i) ? "checked" : ""} ${dis}><div class="md">${md(o, true)}</div></label>`);
          $("input", lab).onchange = e => { if (p.type === "sc") s.clear(); e.target.checked ? s.add(i) : s.delete(i); a.sel = [...s]; save(); };
          box2.appendChild(lab);
        });
        if (p.type === "mc" && !graded) w.appendChild(el(`<div class="tiny muted">Mehrere Antworten möglich.</div>`));
        w.appendChild(box2); break;
      }
      case "tf": {
        a.tf = a.tf || p.stmts.map(() => null);
        const tb = el(`<table class="t tf"><thead><tr><th>Aussage</th><th>wahr</th><th>falsch</th></tr></thead><tbody></tbody></table>`);
        p.stmts.forEach((s, i) => {
          const ok = graded ? (a.tf[i] == null ? "" : a.tf[i] === p.ans[i] ? "good" : "bad") : "";
          const tr = el(`<tr class="${ok ? "opt " + ok : ""}"><td class="md">${md(s, true)}${graded ? ` <span class="tiny muted">(${p.ans[i] ? "wahr" : "falsch"})</span>` : ""}</td>
            <td><input type="radio" name="tf${p.id}_${i}" ${a.tf[i] === true ? "checked" : ""} ${dis}></td><td><input type="radio" name="tf${p.id}_${i}" ${a.tf[i] === false ? "checked" : ""} ${dis}></td></tr>`);
          const [r1, r2] = $$("input", tr);
          r1.onchange = () => { a.tf[i] = true; save(); }; r2.onchange = () => { a.tf[i] = false; save(); };
          $("tbody", tb).appendChild(tr);
        });
        w.appendChild(tb); break;
      }
      case "cloze": {
        a.gaps = a.gaps || [];
        const norm = s => String(s || "").trim().toLowerCase().replace(/["'„“”`´]/g, "").replace(/\s+/g, " ").replace(/[.;:!]+$/, "");
        const numEq = (x, y) => { const f = v => parseFloat(String(v).replace(",", ".")); return !isNaN(f(x)) && /^[-+\d.,eE ]+$/.test(String(y).trim()) && Math.abs(f(x) - f(y)) <= Math.abs(f(y)) * 0.005 + 1e-12; };
        const div = el(`<div class="md"></div>`);
        div.innerHTML = md(p.text).replace(/\{\{(\d+)\}\}/g, (_, i) => `<input class="gap" data-g="${i}" ${dis}>`);
        $$("input.gap", div).forEach(inp => {
          const i = +inp.dataset.g; inp.value = a.gaps[i] || "";
          inp.oninput = () => { a.gaps[i] = inp.value; save(); };
          if (graded && p.ans[i]) {
            const ok = norm(inp.value) && (p.ans[i].some(x => norm(x) === norm(inp.value)) || numEq(inp.value, p.ans[i][0]));
            inp.classList.add(ok ? "good" : "bad");
            if (!ok) inp.after(el(`<span class="small" style="color:var(--good)"> → ${esc(p.ans[i][0])}</span>`));
          }
        });
        w.appendChild(div); break;
      }
      case "order": {
        if (!a.order) { a.order = [...p.disp]; }
        const ul = el(`<ul class="list ord"></ul>`);
        const sync = () => { a.order = $$("li", ul).map(li => +li.dataset.i); a.touched = true; save(); };
        a.order.forEach((oi, pos) => {
          const li = el(`<li data-i="${oi}" draggable="${!graded}" class="${graded ? (oi === pos ? "opt good" : "opt bad") : ""}"><span class="muted">⋮⋮</span><div class="grow md">${md(p.items[oi], true)}</div>
            ${graded ? "" : `<button type="button" class="ghost" data-up>↑</button><button type="button" class="ghost" data-dn>↓</button>`}</li>`);
          ul.appendChild(li);
        });
        if (!graded) {
          let drag = null;
          ul.addEventListener("dragstart", e => { drag = e.target.closest("li"); drag.classList.add("drag"); });
          ul.addEventListener("dragend", () => { drag?.classList.remove("drag"); drag = null; sync(); });
          ul.addEventListener("dragover", e => { e.preventDefault(); const li = e.target.closest("li"); if (!drag || !li || li === drag) return; const r = li.getBoundingClientRect(); ul.insertBefore(drag, e.clientY > r.top + r.height / 2 ? li.nextSibling : li); });
          ul.addEventListener("click", e => { const li = e.target.closest("li"); if (e.target.closest("[data-up]") && li.previousElementSibling) { ul.insertBefore(li, li.previousElementSibling); sync(); } if (e.target.closest("[data-dn]") && li.nextElementSibling) { ul.insertBefore(li.nextElementSibling, li); sync(); } });
        }
        w.appendChild(ul);
        if (graded) w.appendChild(el(`<div class="small"><b>Richtige Reihenfolge:</b><ol class="md">${p.items.map(x => `<li>${md(x, true)}</li>`).join("")}</ol></div>`));
        break;
      }
      case "match": {
        // drag & drop: answer cards from the pool onto the slot next to each term.
        // Touch fallback: tap a card, then tap a slot (or the pool to put it back). a.map[i] = original right index.
        a.map = a.map || p.left.map(() => null);
        const box = el(`<div class="match">${graded ? "" : `<div class="tiny muted" style="margin-bottom:6px">Ziehe jede Antwort in das passende Feld – oder Karte antippen, dann Feld antippen. Zurück in den Vorrat ziehen zum Entfernen.</div>`}
          <div class="mrows"></div><div class="mpool"></div></div>`);
        let picked = null;
        const cardHTML = ri => `<div class="mcardd" draggable="${!graded}" data-ri="${ri}" ${graded ? "" : 'tabindex="0"'}><span class="md">${md(p.right[ri], true)}</span></div>`;
        const render = () => {
          const used = new Set(a.map.filter(x => x != null));
          $(".mrows", box).innerHTML = p.left.map((lft, i) => {
            const sv = a.map[i], ok = graded ? (sv === i ? "good" : "bad") : "";
            return `<div class="mrow ${ok}"><div class="mleft md">${md(lft, true)}</div>
              <div class="mslot ${sv == null ? "empty" : ""}" data-slot="${i}">${sv != null ? cardHTML(sv) : `<span class="tiny muted">${graded ? "nicht zugeordnet" : "hierher ziehen"}</span>`}</div>
              ${graded && sv !== i ? `<div class="mfix small">richtig: <span class="md">${md(p.right[i], true)}</span></div>` : ""}</div>`;
          }).join("");
          const pool = $(".mpool", box);
          pool.hidden = graded;
          pool.innerHTML = p.disp.filter(ri => !used.has(ri)).map(cardHTML).join("") || `<span class="tiny muted">Alle Antworten zugeordnet ✓</span>`;
          if (picked != null) $$(`[data-ri="${picked}"]`, box).forEach(x => x.classList.add("picked"));
        };
        const assign = (ri, slot) => { // slot index, or null = back to the pool; a replaced card returns to the pool
          a.map = a.map.map(x => (x === ri ? null : x));
          if (slot != null) a.map[slot] = ri;
          a.touched = true; picked = null; save(); render();
        };
        if (!graded) {
          let dragRi = null;
          const target = e => e.target.closest && e.target.closest(".mslot, .mpool");
          const clear = () => $$(".over", box).forEach(x => x.classList.remove("over"));
          box.addEventListener("dragstart", e => {
            const c = e.target.closest && e.target.closest("[data-ri]"); if (!c) return;
            dragRi = +c.dataset.ri; e.dataTransfer.setData("text/plain", c.dataset.ri); e.dataTransfer.effectAllowed = "move";
            requestAnimationFrame(() => c.classList.add("drag"));
          });
          box.addEventListener("dragend", () => { dragRi = null; clear(); $$(".drag", box).forEach(x => x.classList.remove("drag")); });
          box.addEventListener("dragover", e => { const t = target(e); if (!t || dragRi == null) return; e.preventDefault(); if (!t.classList.contains("over")) { clear(); t.classList.add("over"); } });
          box.addEventListener("dragleave", e => { const t = target(e); if (t && !t.contains(e.relatedTarget)) t.classList.remove("over"); });
          box.addEventListener("drop", e => {
            const t = target(e); if (!t || dragRi == null) return;
            e.preventDefault(); clear(); assign(dragRi, t.dataset.slot != null ? +t.dataset.slot : null);
          });
          box.addEventListener("click", e => {
            const c = e.target.closest("[data-ri]"), slot = e.target.closest(".mslot"), pool = e.target.closest(".mpool");
            if (picked == null) { if (c) { picked = +c.dataset.ri; render(); } return; }
            if (slot) assign(picked, +slot.dataset.slot);       // place (or swap into an occupied slot)
            else if (c) { picked = +c.dataset.ri; render(); }   // choose another card from the pool
            else if (pool) assign(picked, null);                // put back
          });
          box.addEventListener("keydown", e => { if ((e.key === "Enter" || e.key === " ") && e.target.matches("[data-ri]")) { e.preventDefault(); e.target.click(); } });
        }
        render();
        w.appendChild(box); break;
      }
      case "num": {
        const ta = textarea("work", 5, "Rechenweg (LaTeX mit $…$ möglich)");
        w.appendChild(ta); mathPreview(ta);
        const row = el(`<div class="row" style="margin-top:8px"><b>Ergebnis:</b><input class="res" style="width:180px" ${dis}><span>${esc(p.unit || "")}</span>
          ${graded ? `<span class="small" style="color:var(--good)">richtig: ${String(p.ans).replace(".", ",")} ${esc(p.unit || "")}</span>` : ""}</div>`);
        const inp = $("input", row); inp.value = a.val || ""; inp.oninput = () => { a.val = inp.value; save(); };
        w.appendChild(row); break;
      }
      case "short": case "long": {
        const ta = textarea("text", p.type === "short" ? 3 : 10, p.type === "short" ? "Antwort" : "Ausführliche Antwort (LaTeX mit $…$ möglich)");
        w.appendChild(ta); mathPreview(ta); break;
      }
      case "code": {
        if (a.code == null && p.start) a.code = p.start;
        const ta = textarea("code", 14, "Code", "code-in");
        ta.spellcheck = false;
        ta.addEventListener("keydown", e => {
          if (e.key === "Tab" && !graded) { e.preventDefault(); const s = ta.selectionStart; ta.setRangeText("    ", s, ta.selectionEnd, "end"); a.code = ta.value; save(); }
        });
        w.appendChild(ta); break;
      }
      case "draw": {
        addSketch(w, p, a);
        const ta = textarea("text", 2, "Erläuterung (optional)"); ta.style.marginTop = "8px"; w.appendChild(ta); break;
      }
    }
    if (!LOCAL.has(p.type)) box.appendChild(attachments(p, a));
    if (graded) box.appendChild(feedback(p, r));
    return box;
  }

  function addSketch(host, p, a) {
    if (graded && !(a.sketch && a.sketch.s && a.sketch.s.length)) return;
    sketches[p.id] = new Sketch(host, a.sketch, d => { a.sketch = d; save(); }, graded);
  }

  function attachments(p, a) {
    const box = el(`<div class="attach"></div>`);
    const render = () => {
      box.innerHTML = "";
      (a.img || []).forEach((n, i) => {
        const th = el(`<div class="thumb"><a href="/api/courses/${cid}/exams/${eid}/files/${n}" target="_blank"><img src="/api/courses/${cid}/exams/${eid}/files/${n}"></a>${graded ? "" : `<button type="button" title="Entfernen">✕</button>`}</div>`);
        if (!graded) $("button", th).onclick = () => { a.img.splice(i, 1); save(); render(); };
        box.appendChild(th);
      });
      if (graded) return;
      const up = el(`<label class="btn small">📷 Foto/Scan anhängen<input type="file" accept="image/*" multiple hidden></label>`);
      $("input", up).onchange = async e => {
        for (const f of e.target.files) {
          try { const r = await upload(`/api/courses/${cid}/exams/${eid}/files`, f.name.replace(/\.\w+$/, "") + ".jpg", await toJpeg(f)); (a.img = a.img || []).push(r.name); save(); }
          catch (err) { toast(err.message, true); }
        }
        render();
      };
      box.appendChild(up);
      if (p.type !== "draw" && !sketches[p.id]) {
        const b = el(`<button type="button" class="small">✏️ Skizze</button>`);
        b.onclick = () => { b.remove(); addSketch(box.parentElement.querySelector(".w"), p, a); };
        box.appendChild(b);
      }
    };
    render();
    if (p.type !== "draw" && a.sketch && a.sketch.s && a.sketch.s.length) setTimeout(() => { if (!sketches[p.id]) { addSketch(box.parentElement.querySelector(".w"), p, a); render(); } });
    return box;
  }

  function feedback(p, r) {
    r = r || { p: 0, fb: "" };
    const rub = (p.rub || []).filter(x => Array.isArray(x));
    const f = el(`<div class="fb">
      ${r.fb ? `<h4>Bewertung</h4><div class="md">${md(r.fb)}</div>` : ""}
      ${r.fix ? `<h4>So wäre es richtig</h4><div class="md">${md(r.fix)}</div>` : ""}
      ${p.why ? `<h4>Erklärung</h4><div class="md">${md(p.why)}</div>` : ""}
      ${p.sol ? `<details ${ratioOpen(p, r) ? "open" : ""}><summary>Musterlösung</summary><div class="md">${md(p.sol)}</div></details>` : ""}
      ${rub.length ? `<details><summary>Bewertungsschema</summary><ul class="small">${rub.map(c => `<li>${md(String(c[0]), true)} – ${c[1]} P.</li>`).join("")}</ul></details>` : ""}
      <div class="row tiny muted" style="margin-top:6px">${r.src === "claude" ? "korrigiert von Claude" : r.manual ? "manuell angepasst" : "automatisch korrigiert"}
        ${r.p < p.pts ? `<button class="small" data-var title="Gleiches Konzept, neue Zahlen/neuer Kontext – nur diese Aufgabe wird gesendet (≈ 1–3 k Tokens)">↻ Nochmal, anders</button>` : ""}
        <span class="grow"></span>Punkte anpassen <input type="number" step="0.5" min="0" max="${p.pts}" value="${r.p}" style="width:70px"><button class="small" data-ok>OK</button></div></div>`);
    $("[data-ok]", f).onclick = async () => {
      E = await api("POST", `/api/courses/${cid}/exams/${eid}/override`, { pid: p.id, points: +$("input", f).value });
      toast("Punkte angepasst"); route();
    };
    const vb = $("[data-var]", f);
    if (vb) vb.onclick = () => makeVariants([p.id], vb);
    return f;
  }
  const makeVariants = (pids, btn) => runVariants(cid, eid, pids, btn);
  function ratioOpen(p, r) { return r && r.p < p.pts && !r.fix; }

  async function submit() {
    const open = parts.filter(([, p]) => !answered(p)).length;
    if (!confirm(open ? `${open} Teilaufgabe(n) sind unbearbeitet. Trotzdem abgeben?` : "Klausur abgeben und korrigieren lassen?")) return;
    const sk = {};
    for (const [pid, s] of Object.entries(sketches)) if (!s.isEmpty()) sk[pid] = s.toJPEG();
    const b = $("#submit"); b.disabled = true;
    clearTimeout(saveT); dirty = false;
    try {
      const j = await api("POST", `/api/courses/${cid}/exams/${eid}/grade`, { answers: A, elapsed, sketches: sk });
      await poll(j.id, s => { const [n] = s.progress || []; b.innerHTML = `<span class="spinner"></span> korrigiere${n ? ` · ${fmt(n)} Zeichen` : ""} · ${s.elapsed} s`; });
      route();
    } catch (e) { toast(e.message, true); b.disabled = false; b.textContent = "Abgeben & korrigieren"; }
  }
}

// ---------------- usage ----------------
async function viewUsage() {
  const U = await api("GET", "/api/usage");
  const tin = a => a.in + a.cache_w + a.cache_r;
  const card = (lab, a) => `<div class="card grow" style="min-width:200px"><div class="small muted">${lab}</div><div class="hero" style="font-size:1.8rem">${tok(tin(a) + a.out)}</div>
    <div class="small">${tok(tin(a))} Eingabe <span class="muted">(davon ${tok(a.cache_r)} Cache)</span> · ${tok(a.out)} Ausgabe</div><div class="tiny muted">${a.calls} Aufrufe · ≈ $${a.cost.toFixed(2)} API-Äquivalent</div></div>`;
  const days = Object.entries(U.by_day);
  const mx = Math.max(1, ...days.map(([, a]) => tin(a) + a.out));
  const table = (obj, first) => `<table class="t"><thead><tr><th>${first}</th><th class="n">Aufrufe</th><th class="n">Eingabe</th><th class="n">Cache gelesen</th><th class="n">Ausgabe</th><th class="n">≈ $</th></tr></thead><tbody>
    ${Object.entries(obj).map(([k, a]) => `<tr><td>${esc(k)}</td><td class="n">${a.calls}</td><td class="n">${tok(tin(a))}</td><td class="n">${tok(a.cache_r)}</td><td class="n">${tok(a.out)}</td><td class="n">${a.cost.toFixed(2)}</td></tr>`).join("") || `<tr><td colspan="6" class="muted">–</td></tr>`}</tbody></table>`;
  app.innerHTML = `<h1>Token-Verbrauch</h1>
  <div class="row" style="align-items:stretch;gap:16px">${card("Heute", U.today)}${card("Letzte 7 Tage", U.week)}${card("Gesamt", U.all)}</div>
  <div class="card"><div class="row between"><h2>Pro Tag (letzte 30 Tage mit Nutzung)</h2><div class="legend"><span>Eingabe</span><span class="o">Ausgabe</span></div></div>
    ${days.length ? `<div class="days">${days.map(([d, a]) => { const H = 100 * (tin(a) + a.out) / mx; return `<div class="col" title="${d}: ${tok(tin(a))} Eingabe · ${tok(a.out)} Ausgabe · ${a.calls} Aufrufe"><div style="height:${H}%;display:flex;flex-direction:column-reverse;gap:2px">
      <i style="flex:${tin(a)} 0 0"></i><i class="out" style="flex:${a.out} 0 0"></i></div></div>`; }).join("")}</div>
      <div class="row between tiny muted"><span>${days[0][0]}</span><span>${days[days.length - 1][0]}</span></div>` : `<p class="muted">Noch keine Aufrufe.</p>`}</div>
  <div class="grid2"><div class="card scroll-x"><h2>Nach Aktion</h2>${table(U.by_action, "Aktion")}</div><div class="card scroll-x"><h2>Nach Modell</h2>${table(U.by_model, "Modell")}</div></div>
  <div class="card scroll-x"><h2>Letzte Aufrufe</h2><table class="t"><thead><tr><th>Zeit</th><th>Aktion</th><th>Kurs</th><th>Modell</th><th class="n">Eingabe</th><th class="n">Cache r/w</th><th class="n">Ausgabe</th><th class="n">Dauer</th><th class="n">≈ $</th></tr></thead><tbody>
    ${U.recent.map(r => `<tr><td class="small">${new Date(r.ts * 1000).toLocaleString("de-DE", { dateStyle: "short", timeStyle: "short" })}</td><td>${esc(r.action)}${r.ok === false ? ' <span class="badge bad">Fehler</span>' : ""}</td><td class="small">${esc(r.course || "")}</td><td class="small mono">${esc(r.model)}</td>
      <td class="n">${tok(r.in + r.cache_w + r.cache_r)}</td><td class="n small">${tok(r.cache_r)} / ${tok(r.cache_w)}</td><td class="n">${tok(r.out)}</td><td class="n small">${Math.round(r.ms / 1000)} s</td><td class="n">${r.cost.toFixed(3)}</td></tr>`).join("") || `<tr><td colspan="9" class="muted">–</td></tr>`}</tbody></table>
    <p class="tiny muted">Werte laut Claude CLI. „$“ ist das API-Preis-Äquivalent; mit Claude-Abo wird stattdessen dein Nutzungslimit belastet.</p></div>`;
}

// ---------------- settings ----------------
async function viewSettings() {
  const S = await api("GET", "/api/settings");
  const models = { "claude-opus-5-5": "Opus 5.5", "claude-sonnet-5-5": "Sonnet 5.5", "claude-haiku-5-5": "Haiku 5.5" };
  const msel = (k) => `<select data-k="${k}">${[...new Set([...Object.keys(models), S[k]])].map(m => `<option value="${esc(m)}" ${m === S[k] ? "selected" : ""}>${esc(models[m] || m)}</option>`).join("")}</select>`;
  const esel = (k) => `<select data-k="${k}">${["", "low", "medium", "high", "max"].map(m => `<option value="${m}" ${m === S[k] ? "selected" : ""}>${m || "Standard"}</option>`).join("")}</select>`;
  app.innerHTML = `<h1>Einstellungen</h1>
  <div class="card"><p class="small">Claude CLI: ${S.claude ? `<span class="badge good">gefunden</span> <span class="mono tiny">${esc(S.claude)}</span>` : `<span class="badge bad">nicht gefunden</span> – installiere Claude Code und melde dich mit <span class="mono">claude</span> an.`}</p>
  <table class="t"><thead><tr><th>Aufgabe</th><th>Modell</th><th>Effort (Denkaufwand)</th></tr></thead><tbody>
    <tr><td>Klausur erstellen</td><td>${msel("gen_model")}</td><td>${esel("gen_effort")}</td></tr>
    <tr><td>Korrektur</td><td>${msel("grade_model")}</td><td>${esel("grade_effort")}</td></tr>
    <tr><td>Themen gruppieren &amp; Bilder transkribieren</td><td>${msel("aux_model")}</td><td>${esel("aux_effort")}</td></tr>
    <tr><td>KI-Vorprüfung des Materials</td><td>${msel("triage_model")}</td><td>${esel("triage_effort")}</td></tr>
    <tr><td>Lernzettel &amp; Nachfragen</td><td>${msel("learn_model")}</td><td>${esel("learn_effort")}</td></tr>
    <tr><td>Kurz-Quiz &amp; Variationen</td><td>${msel("quiz_model")}</td><td>${esel("quiz_effort")}</td></tr></tbody></table>
  <p class="tiny muted">Andere Modellnamen (z. B. eine konkrete Version) kannst du in data/settings.json eintragen.</p>
  <p class="tiny muted">Token-Schätzung: ${String(S.chars_per_token).replace(".", ",")} Zeichen pro Token – wird nach jeder größeren Textanfrage automatisch an den echten Verbrauch angepasst.</p></div>
  <div class="card"><div class="row"><label>Material-Budget pro Klausur <input type="number" data-k="max_material_tokens" value="${S.max_material_tokens}" step="5000" min="2000" style="width:110px"> Tokens</label></div>
  <p class="tiny muted">Obergrenze für das an Claude gesendete Material. Bei mehr Material werden Abschnitte gleichmäßig über die gewählten Themen verteilt.</p>
  <div class="row"><label>Standard-Dauer <input type="number" data-k="default_minutes" value="${S.default_minutes}" step="5"> min</label>
    <label>AB-Verteilung I/II/III <input data-k="ab_split" value="${S.ab_split.join("/")}" style="width:110px"> %</label></div></div>
  <button class="primary" id="ssave">Speichern</button>`;
  $("#ssave").onclick = async () => {
    const patch = {};
    $$("[data-k]").forEach(i => { const k = i.dataset.k; let v = i.value; if (k === "ab_split") v = v.split("/").map(Number); else if (i.type === "number") v = +v; patch[k] = v; });
    await api("PUT", "/api/settings", patch); toast("Gespeichert");
  };
}
