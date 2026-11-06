// Minimal vector sketch pad: pen, line, arrow, rect, ellipse, text, eraser, grid, undo/redo.
// Strokes are stored as JSON in the answer; exported to JPEG only when submitting.
class Sketch {
  constructor(host, data, onChange, readOnly) {
    this.data = data && data.s ? data : { s: [], h: 560, grid: true };
    this.onChange = onChange || (() => {});
    this.ro = !!readOnly;
    this.W = 1000;
    this.tool = "pen"; this.color = "#111111"; this.width = 3;
    this.redo = [];
    this.el = document.createElement("div");
    this.el.className = "sketch";
    if (!this.ro) this.el.appendChild(this.toolbar());
    this.cv = document.createElement("canvas");
    this.el.appendChild(this.cv);
    host.appendChild(this.el);
    this.resize();
    if (!this.ro) this.bind();
  }
  toolbar() {
    const bar = document.createElement("div");
    bar.className = "sketch-tools";
    const tools = [["pen", "✎ Stift"], ["line", "╱ Linie"], ["arrow", "→ Pfeil"], ["rect", "▭ Rechteck"], ["ellipse", "◯ Ellipse"], ["text", "T Text"], ["erase", "⌫ Radierer"]];
    const btn = (label, fn, cls) => { const b = document.createElement("button"); b.type = "button"; b.innerHTML = label; if (cls) b.className = cls; b.onclick = fn; bar.appendChild(b); return b; };
    this.toolBtns = tools.map(([t, l]) => { const b = btn(l, () => { this.tool = t; this.sync(); }); b.dataset.t = t; return b; });
    bar.appendChild(Object.assign(document.createElement("span"), { style: "width:8px" }));
    this.sw = ["#111111", "#1d4ed8", "#dc2626", "#15803d"].map(c => { const b = btn("", () => { this.color = c; this.sync(); }, "sw"); b.style.background = c; b.dataset.c = c; b.title = "Farbe"; return b; });
    const ws = document.createElement("select");
    ws.innerHTML = `<option value="2">dünn</option><option value="3" selected>mittel</option><option value="6">dick</option>`;
    ws.onchange = () => this.width = +ws.value;
    bar.appendChild(ws);
    bar.appendChild(Object.assign(document.createElement("span"), { style: "flex:1" }));
    btn("↶", () => { const s = this.data.s.pop(); if (s) { this.redo.push(s); this.changed(); } }).title = "Rückgängig";
    btn("↷", () => { const s = this.redo.pop(); if (s) { this.data.s.push(s); this.changed(); } }).title = "Wiederholen";
    btn("#", () => { this.data.grid = !this.data.grid; this.changed(); }).title = "Raster";
    btn("+ Platz", () => { this.data.h += 300; this.resize(); this.changed(); });
    btn("Leeren", () => { if (confirm("Zeichnung leeren?")) { this.redo = this.data.s.splice(0).reverse(); this.changed(); } });
    setTimeout(() => this.sync());
    return bar;
  }
  sync() {
    this.toolBtns?.forEach(b => b.classList.toggle("on", b.dataset.t === this.tool));
    this.sw?.forEach(b => b.classList.toggle("on", b.dataset.c === this.color));
  }
  resize() { this.cv.width = this.W; this.cv.height = this.data.h; this.render(); }
  changed() { this.render(); this.onChange(this.data); }
  pos(e) {
    const r = this.cv.getBoundingClientRect();
    return [Math.round((e.clientX - r.left) * this.W / r.width), Math.round((e.clientY - r.top) * this.data.h / r.height)];
  }
  bind() {
    let cur = null;
    this.cv.addEventListener("pointerdown", e => {
      e.preventDefault();
      this.cv.setPointerCapture(e.pointerId);
      const p = this.pos(e);
      if (this.tool === "text") {
        const t = prompt("Text:");
        if (t) { this.data.s.push({ t: "text", c: this.color, a: p, x: t, z: 14 + this.width * 4 }); this.redo = []; this.changed(); }
        return;
      }
      if (this.tool === "erase") { cur = { t: "erase" }; this.eraseAt(p); return; }
      cur = this.tool === "pen" ? { t: "pen", c: this.color, w: this.width, p: [p] } : { t: this.tool, c: this.color, w: this.width, a: p, b: p };
      this.data.s.push(cur);
      this.redo = [];
    });
    this.cv.addEventListener("pointermove", e => {
      if (!cur) return;
      const p = this.pos(e);
      if (cur.t === "erase") return this.eraseAt(p);
      if (cur.t === "pen") cur.p.push(p); else cur.b = p;
      this.render();
    });
    const end = () => { if (cur) { if (cur.t !== "erase") this.simplify(cur); cur = null; this.changed(); } };
    this.cv.addEventListener("pointerup", end);
    this.cv.addEventListener("pointercancel", end);
  }
  simplify(s) { // drop near-duplicate points to keep the JSON small
    if (s.t !== "pen") return;
    const out = [s.p[0]];
    for (const p of s.p) { const q = out[out.length - 1]; if (Math.abs(p[0] - q[0]) + Math.abs(p[1] - q[1]) >= 3) out.push(p); }
    s.p = out;
  }
  eraseAt([x, y]) {
    const hit = s => {
      const near = (px, py) => Math.hypot(px - x, py - y) < 14;
      if (s.t === "pen") return s.p.some(p => near(p[0], p[1]));
      if (s.t === "text") return Math.abs(s.a[0] - x) < 60 && Math.abs(s.a[1] - y) < 20;
      const [x1, y1] = s.a, [x2, y2] = s.b;
      if (s.t === "rect" || s.t === "ellipse") return x >= Math.min(x1, x2) - 10 && x <= Math.max(x1, x2) + 10 && y >= Math.min(y1, y2) - 10 && y <= Math.max(y1, y2) + 10;
      const L = Math.hypot(x2 - x1, y2 - y1) || 1, t = Math.max(0, Math.min(1, ((x - x1) * (x2 - x1) + (y - y1) * (y2 - y1)) / L / L));
      return near(x1 + t * (x2 - x1), y1 + t * (y2 - y1));
    };
    const n = this.data.s.length;
    this.data.s = this.data.s.filter(s => !hit(s));
    if (this.data.s.length !== n) this.render();
  }
  render() {
    const g = this.cv.getContext("2d"), W = this.W, H = this.data.h;
    g.fillStyle = "#fff"; g.fillRect(0, 0, W, H);
    if (this.data.grid) {
      g.strokeStyle = "#e6e9ef"; g.lineWidth = 1; g.beginPath();
      for (let x = 25; x < W; x += 25) { g.moveTo(x + .5, 0); g.lineTo(x + .5, H); }
      for (let y = 25; y < H; y += 25) { g.moveTo(0, y + .5); g.lineTo(W, y + .5); }
      g.stroke();
    }
    g.lineCap = "round"; g.lineJoin = "round";
    for (const s of this.data.s) {
      g.strokeStyle = g.fillStyle = s.c; g.lineWidth = s.w || 3;
      g.beginPath();
      if (s.t === "pen") { s.p.forEach((p, i) => i ? g.lineTo(p[0], p[1]) : g.moveTo(p[0], p[1])); if (s.p.length === 1) g.lineTo(s.p[0][0] + .1, s.p[0][1]); g.stroke(); }
      else if (s.t === "line" || s.t === "arrow") {
        g.moveTo(...s.a); g.lineTo(...s.b); g.stroke();
        if (s.t === "arrow") {
          const ang = Math.atan2(s.b[1] - s.a[1], s.b[0] - s.a[0]), L = 10 + s.w * 3;
          g.beginPath(); g.moveTo(...s.b);
          g.lineTo(s.b[0] - L * Math.cos(ang - .4), s.b[1] - L * Math.sin(ang - .4));
          g.lineTo(s.b[0] - L * Math.cos(ang + .4), s.b[1] - L * Math.sin(ang + .4));
          g.closePath(); g.fill();
        }
      } else if (s.t === "rect") { g.strokeRect(Math.min(s.a[0], s.b[0]), Math.min(s.a[1], s.b[1]), Math.abs(s.b[0] - s.a[0]), Math.abs(s.b[1] - s.a[1])); }
      else if (s.t === "ellipse") { g.ellipse((s.a[0] + s.b[0]) / 2, (s.a[1] + s.b[1]) / 2, Math.abs(s.b[0] - s.a[0]) / 2, Math.abs(s.b[1] - s.a[1]) / 2, 0, 0, Math.PI * 2); g.stroke(); }
      else if (s.t === "text") { g.font = `${s.z}px system-ui, sans-serif`; g.textBaseline = "middle"; g.fillText(s.x, s.a[0], s.a[1]); }
    }
  }
  isEmpty() { return !this.data.s.length; }
  toJPEG() { this.render(); return this.cv.toDataURL("image/jpeg", 0.85); }
}
window.Sketch = Sketch;
