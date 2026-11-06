// Markdown + KaTeX rendering (offline). Math is cut out before markdown so marked can't mangle it.
(function () {
  const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  marked.use({ gfm: true, breaks: true, renderer: { html: (h) => esc(typeof h === "string" ? h : h.text) } });
  const RX = /(```[\s\S]*?```|`[^`\n]*`)|\$\$([\s\S]+?)\$\$|\\\[([\s\S]+?)\\\]|\\\(([\s\S]+?)\\\)|\$([^\s$](?:[^$\n]*?[^\s$])?)\$/g;
  function tex(s, display) {
    try { return katex.renderToString(s, { displayMode: display, throwOnError: false, strict: "ignore" }); }
    catch { return esc(s); }
  }
  function md(src, inline) {
    const math = [];
    const s = String(src ?? "").replace(RX, (m, code, d1, d2, i1, i2) => {
      if (code) return code;
      const display = d1 != null || d2 != null;
      math.push(tex(d1 ?? d2 ?? i1 ?? i2, display));
      return `@@M${math.length - 1}@@`;
    });
    let html = inline ? marked.parseInline(s) : marked.parse(s);
    return html.replace(/@@M(\d+)@@/g, (_, i) => math[+i]);
  }
  window.md = md;
  window.esc = esc;
})();
