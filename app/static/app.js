/* PDF Transfer — interfaz web (sin dependencias). */
(() => {
  "use strict";

  const $ = (sel) => document.querySelector(sel);
  const PRICES = { free: "0 €", pro: "9 €", business: "29 €" };
  const SAMPLE = `---
title: Mi documento
---

# Mi documento

Escribe aquí en **Markdown**. La vista previa de la derecha muestra
exactamente cómo quedará el PDF.

## Qué puedes usar

- Listas, **negritas**, *cursivas* y \`código\`
- [Enlaces](https://commonmark.org) y notas al pie[^1]
- [x] Listas de tareas

| Formato | Soportado |
|---------|-----------|
| Tablas  | Sí        |
| Código  | Sí        |

\`\`\`python
def saludo(nombre):
    return f"Hola, {nombre}"
\`\`\`

> Consejo: al volver a convertir este PDF a Markdown recuperarás este texto exacto.

[^1]: Las notas al pie aparecen al final del documento.
`;

  const state = {
    mode: "pdf2md",
    lists: { pdf2md: [], md2pdf: [] },
    active: { pdf2md: null, md2pdf: null },
    sharedAssets: {},
    config: null,
    apiKey: safeGet("apiKey") || "",
  };
  let seq = 0;
  let previewTimer = null;
  let previewToken = 0;

  // ------------------------------------------------------------ utilidades
  function safeGet(key) { try { return localStorage.getItem(key); } catch { return null; } }
  function safeSet(key, value) { try { value == null ? localStorage.removeItem(key) : localStorage.setItem(key, value); } catch { /* sin almacenamiento */ } }

  function headers(extra = {}) {
    return state.apiKey ? { "X-API-Key": state.apiKey, ...extra } : extra;
  }

  async function api(path, options = {}) {
    const res = await fetch(path, { ...options, headers: headers(options.headers || {}) });
    if (!res.ok) {
      let msg = `Error ${res.status}`;
      try { const body = await res.json(); if (body.detail) msg = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail); } catch { /* no JSON */ }
      throw new Error(msg);
    }
    return res;
  }

  function toast(message, type = "", html = false) {
    const el = document.createElement("div");
    el.className = `toast ${type}`;
    el[html ? "innerHTML" : "textContent"] = message;
    $("#toasts").appendChild(el);
    setTimeout(() => el.remove(), type === "err" ? 7000 : 4200);
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  function stem(name) { return (name || "documento").replace(/\.[^./\\]+$/, "") || "documento"; }
  function ext(name) { const m = /\.([^.]+)$/.exec(name || ""); return m ? m[1].toLowerCase() : ""; }
  function fmtBytes(n) { return n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(0)} KB` : `${(n / 1048576).toFixed(1)} MB`; }

  function download(blob, filename) {
    const url = URL.createObjectURL(blob);
    const a = Object.assign(document.createElement("a"), { href: url, download: filename });
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 60_000);
  }

  function b64ToBlob(b64, type = "application/octet-stream") {
    const bin = atob(b64); const bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    return new Blob([bytes], { type });
  }

  function blobToB64(blob) {
    return new Promise((resolve, reject) => {
      const r = new FileReader();
      r.onload = () => resolve(String(r.result).split(",")[1] || "");
      r.onerror = reject;
      r.readAsDataURL(blob);
    });
  }

  function mimeFor(name) {
    const e = ext(name);
    return { png: "image/png", jpg: "image/jpeg", jpeg: "image/jpeg", gif: "image/gif", svg: "image/svg+xml", webp: "image/webp", bmp: "image/bmp" }[e] || "application/octet-stream";
  }

  function normPath(p) {
    try { p = decodeURI(p); } catch { /* ruta con % literal */ }
    return p.split(/[?#]/)[0].replace(/\\/g, "/").replace(/^(\.\/)+/, "").replace(/^\/+/, "");
  }

  function findAsset(assets, src) {
    const want = normPath(src);
    const keys = Object.keys(assets);
    const hit = keys.find((k) => normPath(k) === want || `images/${normPath(k)}` === want);
    if (hit) return hit;
    const base = want.split("/").pop();
    return keys.find((k) => normPath(k).split("/").pop() === base) || null;
  }

  // --------------------------------------------------------------- estado
  const list = () => state.lists[state.mode];
  const activeItem = () => list().find((i) => i.id === state.active[state.mode]) || null;

  function addItem(props) {
    const item = { id: ++seq, status: "queued", markdown: "", assets: {}, warnings: [], ...props };
    list().push(item);
    return item;
  }

  function planBatchLimit() {
    return state.config && state.config.limits_enabled ? state.config.plan.batch_size : Infinity;
  }

  // ---------------------------------------------------------------- modos
  function setMode(mode) {
    state.mode = mode;
    document.querySelectorAll(".tab").forEach((t) => {
      const on = t.dataset.mode === mode;
      t.classList.toggle("active", on);
      t.setAttribute("aria-selected", on);
    });
    const pdf = mode === "pdf2md";
    $("#dropTitle").textContent = pdf ? "Arrastra aquí tus PDF" : "Arrastra aquí tus archivos Markdown";
    $("#dropSub").innerHTML = pdf
      ? "o <u>haz clic para elegirlos</u> · varios a la vez"
      : "o <u>haz clic para elegirlos</u> · .md, .zip (Markdown + imágenes) o imágenes sueltas";
    $("#fileInput").accept = pdf ? ".pdf,application/pdf" : ".md,.markdown,.txt,.zip,image/*";
    $("#newDoc").hidden = pdf;
    $("#pdfOptions").hidden = !pdf;
    $("#mdOptions").hidden = pdf;
    renderFiles();
    renderWorkspace();
  }

  // ---------------------------------------------------------- añadir archivos
  async function addFiles(fileList) {
    const files = Array.from(fileList || []);
    if (!files.length) return;
    if (state.mode === "pdf2md") {
      const pdfs = files.filter((f) => ext(f.name) === "pdf" || f.type === "application/pdf");
      const others = files.length - pdfs.length;
      if (others) toast(others === files.length ? "En esta pestaña solo se admiten PDF. Para Markdown usa «Markdown a PDF»." : `Se ignoraron ${others} archivo(s) que no son PDF.`, others === files.length ? "err" : "");
      enqueue(pdfs, (f) => addItem({ name: stem(f.name), kind: "pdf", file: f, size: f.size }));
      processQueue();
    } else {
      const images = files.filter((f) => f.type.startsWith("image/"));
      const docs = files.filter((f) => ["md", "markdown", "txt", "zip"].includes(ext(f.name)));
      const pdfs = files.filter((f) => ext(f.name) === "pdf");
      if (pdfs.length) toast("Los PDF se convierten en la pestaña «PDF a Markdown».");
      for (const img of images) state.sharedAssets[img.name] = await blobToB64(img);
      if (images.length) { toast(`${images.length} imagen(es) disponibles para tus documentos.`, "ok"); schedulePreview(0); }
      enqueue(docs, (f) => addItem({ name: stem(f.name), kind: ext(f.name) === "zip" ? "zip" : "md", file: f, size: f.size }));
      for (const item of list().filter((i) => i.status === "queued")) await loadMarkdownItem(item);
    }
    renderFiles();
  }

  function enqueue(files, make) {
    const room = planBatchLimit() - list().filter((i) => i.status !== "error").length;
    if (files.length > room) {
      toast(`Tu plan permite ${planBatchLimit()} archivos por lote. Vacía la lista o mejora tu plan.`, "err");
      files = files.slice(0, Math.max(0, room));
    }
    files.forEach(make);
  }

  async function loadMarkdownItem(item) {
    item.status = "working"; renderFiles();
    try {
      if (item.kind === "zip") {
        const fd = new FormData(); fd.append("file", item.file);
        const data = await (await api("/api/unpack", { method: "POST", body: fd })).json();
        item.markdown = data.markdown; item.assets = data.assets || {};
      } else {
        item.markdown = await item.file.text();
      }
      item.status = "ready";
      if (!state.active.md2pdf) state.active.md2pdf = item.id;
    } catch (e) {
      item.status = "error"; item.error = e.message;
    }
    renderFiles(); renderWorkspace();
  }

  let processing = false;
  async function processQueue() {
    if (processing) return;
    processing = true;
    try {
      let item;
      while ((item = state.lists.pdf2md.find((i) => i.status === "queued"))) {
        item.status = "working"; renderFiles();
        try {
          const fd = new FormData();
          fd.append("file", item.file);
          fd.append("images", $("#optImages").checked ? "embed" : "none");
          const data = await (await api("/api/convert/pdf-to-md", { method: "POST", body: fd })).json();
          Object.assign(item, { status: "done", markdown: data.markdown, assets: data.assets || {}, source: data.source, pages: data.pages, warnings: data.warnings || [] });
          if (!state.active.pdf2md) state.active.pdf2md = item.id;
        } catch (e) {
          item.status = "error"; item.error = e.message;
        }
        renderFiles();
        if (state.mode === "pdf2md") renderWorkspace();
      }
    } finally {
      processing = false;
    }
  }

  // ------------------------------------------------------------- acciones
  function itemAssets(item) {
    return state.mode === "md2pdf" ? { ...state.sharedAssets, ...item.assets } : item.assets;
  }

  async function generatePdf(item, { silent = false } = {}) {
    if (item.pdfBlob) return item.pdfBlob;
    item.status = "working"; renderFiles(); renderWorkspace();
    try {
      const fd = new FormData();
      fd.append("markdown", item.markdown);
      fd.append("filename", item.name);
      fd.append("theme", $("#optTheme").value);
      fd.append("page_size", $("#optPage").value);
      fd.append("embed_source", $("#optEmbed").checked ? "true" : "false");
      const assets = { ...state.sharedAssets, ...item.assets };
      for (const [name, b64] of Object.entries(assets)) {
        if (item.markdown.includes(name.split("/").pop())) fd.append("assets", b64ToBlob(b64, mimeFor(name)), name);
      }
      const res = await api("/api/convert/md-to-pdf", { method: "POST", body: fd });
      item.pdfBlob = await res.blob();
      item.status = "done";
      if (!silent) {
        download(item.pdfBlob, `${item.name}.pdf`);
        const url = URL.createObjectURL(item.pdfBlob);
        toast(`PDF listo: <a href="${url}" target="_blank" rel="noopener">abrir en una pestaña</a>`, "ok", true);
      }
      return item.pdfBlob;
    } catch (e) {
      item.status = "ready";
      toast(e.message, "err");
      throw e;
    } finally {
      renderFiles(); renderWorkspace();
    }
  }

  function markdownWithInlineImages(item) {
    let md = item.markdown;
    const assets = item.assets || {};
    return md.replace(/(!\[[^\]]*\]\()([^)\s]+)((?:\s+"[^"]*")?\))/g, (all, pre, src, post) => {
      const key = findAsset(assets, src);
      return key ? `${pre}data:${mimeFor(key)};base64,${assets[key]}${post}` : all;
    });
  }

  async function downloadZipFor(item) {
    const files = [{ path: `${item.name}.md`, text: item.markdown }];
    for (const [name, b64] of Object.entries(item.assets || {})) files.push({ path: `images/${name}`, base64: b64 });
    const res = await api("/api/bundle", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: item.name, files }) });
    download(await res.blob(), `${item.name}.zip`);
  }

  async function downloadAll() {
    const items = list().filter((i) => ["done", "ready"].includes(i.status));
    if (!items.length) return toast("No hay resultados todavía.");
    const btn = $("#downloadAll"); btn.disabled = true;
    try {
      const files = [];
      const used = new Set();
      const unique = (n) => { let name = n, k = 2; while (used.has(name)) name = `${n} (${k++})`; used.add(name); return name; };
      for (const item of items) {
        const name = unique(item.name);
        if (state.mode === "pdf2md") {
          const hasAssets = Object.keys(item.assets || {}).length > 0;
          const dir = hasAssets ? `${name}/` : "";
          files.push({ path: `${dir}${name}.md`, text: item.markdown });
          for (const [a, b64] of Object.entries(item.assets || {})) files.push({ path: `${dir}images/${a}`, base64: b64 });
        } else {
          const blob = await generatePdf(item, { silent: true });
          files.push({ path: `${name}.pdf`, base64: await blobToB64(blob) });
        }
      }
      const res = await api("/api/bundle", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: state.mode === "pdf2md" ? "markdown" : "pdfs", files }) });
      download(await res.blob(), state.mode === "pdf2md" ? "markdown.zip" : "pdfs.zip");
    } catch (e) {
      toast(e.message, "err");
    } finally {
      btn.disabled = false;
    }
  }

  function sendToPdf(item) {
    const copy = { name: item.name, kind: "md", status: "ready", markdown: item.markdown, assets: { ...item.assets } };
    state.mode = "md2pdf";
    const created = addItem(copy);
    state.active.md2pdf = created.id;
    setMode("md2pdf");
    $("#workspace").scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function newDocument() {
    const item = addItem({ name: "mi-documento", kind: "md", status: "ready", markdown: SAMPLE });
    state.active.md2pdf = item.id;
    renderFiles(); renderWorkspace();
    $("#editor").focus();
  }

  // --------------------------------------------------------------- render
  function statusText(item) {
    switch (item.status) {
      case "queued": return "En cola…";
      case "working": return state.mode === "pdf2md" ? "Convirtiendo…" : "Procesando…";
      case "error": return item.error || "Error";
      case "done":
        if (state.mode === "pdf2md") {
          const n = Object.keys(item.assets || {}).length;
          return `${item.pages} pág. · ${item.source === "embedded" ? "original recuperado sin pérdidas" : "reconstruido"}${n ? ` · ${n} imagen(es)` : ""}`;
        }
        return "PDF generado";
      default: return item.size ? `${fmtBytes(item.size)} · listo para convertir` : "Listo para convertir";
    }
  }

  function renderFiles() {
    const ul = $("#files");
    ul.innerHTML = "";
    for (const item of list()) {
      const li = document.createElement("li");
      li.className = `file${item.id === state.active[state.mode] ? " active" : ""}`;
      const kind = item.kind === "pdf" ? "PDF" : item.kind === "zip" ? "ZIP" : "MD";
      li.innerHTML = `
        <span class="file-kind ${item.kind}">${kind}</span>
        <div class="file-main">
          <div class="file-name">${escapeHtml(item.name)}</div>
          <div class="file-meta ${item.status === "error" ? "err" : ""}">${escapeHtml(statusText(item))}</div>
        </div>
        <div class="file-actions"></div>`;
      const actions = li.querySelector(".file-actions");
      if (item.status === "working" || item.status === "queued") {
        actions.innerHTML = '<span class="spinner" aria-label="Procesando"></span>';
      } else {
        const rm = document.createElement("button");
        rm.className = "btn ghost small icon"; rm.title = "Quitar"; rm.textContent = "✕";
        rm.onclick = (ev) => { ev.stopPropagation(); removeItem(item); };
        actions.appendChild(rm);
      }
      li.onclick = () => { if (item.status !== "error") { state.active[state.mode] = item.id; renderFiles(); renderWorkspace(); } };
      ul.appendChild(li);
    }
    $("#batchActions").hidden = list().length < 2;
  }

  function removeItem(item) {
    const arr = list();
    arr.splice(arr.indexOf(item), 1);
    if (state.active[state.mode] === item.id) {
      const next = arr.find((i) => ["done", "ready"].includes(i.status));
      state.active[state.mode] = next ? next.id : null;
    }
    renderFiles(); renderWorkspace();
  }

  function button(label, onClick, cls = "btn ghost small") {
    const b = document.createElement("button");
    b.className = cls; b.type = "button"; b.innerHTML = label; b.onclick = onClick;
    return b;
  }

  let renderedId = null;
  function renderWorkspace() {
    const item = activeItem();
    const ws = $("#workspace");
    if (!item || !["done", "ready", "working"].includes(item.status) || (state.mode === "pdf2md" && item.status === "working")) {
      ws.hidden = true; renderedId = null; return;
    }
    ws.hidden = false;
    const editor = $("#editor");
    if (renderedId !== item.id) {
      editor.value = item.markdown;
      editor.scrollTop = 0;
      renderedId = item.id;
      schedulePreview(0);
    }
    $("#docName").value = item.name;

    const badge = $("#sourceBadge");
    if (state.mode === "pdf2md" && item.source) {
      badge.hidden = false;
      badge.className = `badge ${item.source === "embedded" ? "ok" : "info"}`;
      badge.textContent = item.source === "embedded" ? "✓ Original exacto recuperado" : "Reconstruido desde el diseño";
      badge.title = item.source === "embedded"
        ? "Este PDF se creó con PDF Transfer: se ha recuperado el Markdown original sin ninguna pérdida."
        : "El Markdown se ha reconstruido analizando el diseño del PDF. Revísalo en el editor.";
    } else {
      badge.hidden = true;
    }

    const warn = $("#warnings");
    warn.hidden = !(item.warnings && item.warnings.length);
    warn.textContent = (item.warnings || []).join(" ");

    const actions = $("#wsActions");
    actions.innerHTML = "";
    if (state.mode === "pdf2md") {
      actions.append(
        button("Copiar", async () => {
          try { await navigator.clipboard.writeText(item.markdown); toast("Markdown copiado.", "ok"); } catch { toast("No se pudo copiar.", "err"); }
        }),
        button("Descargar .md", () => {
          const hasAssets = Object.keys(item.assets || {}).length > 0;
          const text = hasAssets ? markdownWithInlineImages(item) : item.markdown;
          download(new Blob([text], { type: "text/markdown;charset=utf-8" }), `${item.name}.md`);
        }),
      );
      if (Object.keys(item.assets || {}).length) {
        actions.append(button("Descargar .zip (con imágenes)", () => downloadZipFor(item).catch((e) => toast(e.message, "err"))));
      }
      actions.append(button("Crear PDF →", () => sendToPdf(item), "btn small"));
    } else {
      const busy = item.status === "working";
      const gen = button(busy ? '<span class="spinner"></span> Generando…' : "Generar PDF", () => generatePdf(item).catch(() => {}), "btn small");
      gen.disabled = busy;
      actions.append(
        button("Descargar .md", () => download(new Blob([item.markdown], { type: "text/markdown;charset=utf-8" }), `${item.name}.md`)),
        gen,
      );
    }
    updateStats();
  }

  function updateStats() {
    const text = $("#editor").value;
    const words = (text.match(/\S+/g) || []).length;
    $("#stats").textContent = `${words.toLocaleString("es")} palabras · ${text.length.toLocaleString("es")} caracteres`;
  }

  function schedulePreview(delay = 350) {
    clearTimeout(previewTimer);
    previewTimer = setTimeout(renderPreview, delay);
  }

  async function renderPreview() {
    const item = activeItem();
    if (!item || $("#workspace").hidden) return;
    const token = ++previewToken;
    try {
      const res = await api("/api/preview", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ markdown: item.markdown, theme: $("#optTheme").value || "moderno", page_size: $("#optPage").value || "A4" }),
      });
      const { html } = await res.json();
      if (token !== previewToken) return;
      const doc = new DOMParser().parseFromString(html, "text/html");
      const assets = itemAssets(item);
      doc.querySelectorAll("img[src]").forEach((img) => {
        const src = img.getAttribute("src");
        if (/^(data:|https?:)/i.test(src)) return;
        const key = findAsset(assets, src);
        if (key) img.src = `data:${mimeFor(key)};base64,${assets[key]}`;
      });
      const base = doc.createElement("base"); base.target = "_blank"; doc.head.prepend(base);
      const frame = $("#preview");
      let scroll = 0;
      try { scroll = frame.contentWindow.scrollY; } catch { /* sin acceso */ }
      frame.srcdoc = "<!doctype html>" + doc.documentElement.outerHTML;
      frame.onload = () => { try { frame.contentWindow.scrollTo(0, scroll); } catch { /* sin acceso */ } };
    } catch (e) {
      if (token === previewToken) toast(`Vista previa: ${e.message}`, "err");
    }
  }

  // ----------------------------------------------------------- editor
  function wrapSelection(before, after) {
    const ed = $("#editor");
    const { selectionStart: s, selectionEnd: e, value } = ed;
    const sel = value.slice(s, e);
    ed.setRangeText(before + sel + after, s, e, "end");
    if (!sel) ed.setSelectionRange(s + before.length, s + before.length);
    ed.focus(); ed.dispatchEvent(new Event("input"));
  }

  function prefixLines(prefix) {
    const ed = $("#editor");
    const { value } = ed;
    const s = value.lastIndexOf("\n", ed.selectionStart - 1) + 1;
    let e = value.indexOf("\n", ed.selectionEnd); if (e === -1) e = value.length;
    const lines = value.slice(s, e).split("\n").map((l) => prefix + l.replace(/^(#{1,6} |[-*] \[[ x]\] |[-*] |\d+\. |> )/, ""));
    ed.setRangeText(lines.join("\n"), s, e, "end");
    ed.focus(); ed.dispatchEvent(new Event("input"));
  }

  function insertBlock(template) {
    const ed = $("#editor");
    const [before, after = ""] = template.split("|");
    const pre = ed.selectionStart > 0 && ed.value[ed.selectionStart - 1] !== "\n" ? "\n\n" : "";
    wrapSelection(pre + before, after + "\n");
  }

  // ------------------------------------------------------------- planes
  function renderPlans() {
    const cfg = state.config;
    const wrap = $("#plans");
    wrap.innerHTML = "";
    for (const p of cfg.plans) {
      const div = document.createElement("div");
      div.className = `plan${p.id === "pro" ? " featured" : ""}`;
      const rate = p.requests_per_hour ? `${p.requests_per_hour.toLocaleString("es")} conversiones/hora` : "Conversiones ilimitadas";
      const current = cfg.plan.id === p.id;
      div.innerHTML = `
        ${p.id === "pro" ? '<span class="tag">Más popular</span>' : ""}
        <h3>${escapeHtml(p.name)}</h3>
        <div class="price">${PRICES[p.id] || "—"} <small>${p.id === "free" ? "para siempre" : "/ mes"}</small></div>
        <ul>
          <li>Archivos de hasta ${p.max_file_mb} MB</li>
          <li>PDF de hasta ${p.max_pages.toLocaleString("es")} páginas</li>
          <li>${rate}</li>
          <li>Lotes de ${p.batch_size} archivos</li>
          ${p.id !== "free" ? "<li>Acceso a la API</li>" : "<li>Ida y vuelta sin pérdidas</li>"}
        </ul>`;
      let action;
      if (current) {
        action = button("Tu plan actual", null, "btn ghost"); action.disabled = true;
      } else if (p.id === "free") {
        action = button("Empezar gratis", () => $("#convertir").scrollIntoView({ behavior: "smooth" }), "btn ghost");
      } else if (cfg.contact_email) {
        action = document.createElement("a");
        action.className = p.id === "pro" ? "btn" : "btn ghost";
        action.href = `mailto:${cfg.contact_email}?subject=${encodeURIComponent(`Plan ${p.name} de ${cfg.app_name}`)}`;
        action.textContent = "Solicitar";
      } else {
        action = button("Próximamente", null, p.id === "pro" ? "btn" : "btn ghost"); action.disabled = true;
      }
      div.appendChild(action);
      wrap.appendChild(div);
    }
    const keyRow = document.createElement("p");
    keyRow.className = "muted center";
    keyRow.style.gridColumn = "1 / -1";
    keyRow.innerHTML = state.apiKey
      ? `Usando una clave de API. <a href="#" id="keyBtn">Cambiar o quitar clave</a>`
      : `¿Ya tienes una clave de API? <a href="#" id="keyBtn">Introdúcela aquí</a>`;
    wrap.appendChild(keyRow);
    $("#keyBtn").onclick = (ev) => {
      ev.preventDefault();
      const key = prompt("Clave de API (déjalo vacío para quitarla):", state.apiKey);
      if (key === null) return;
      state.apiKey = key.trim();
      safeSet("apiKey", state.apiKey || null);
      loadConfig().then(() => toast(state.apiKey ? `Plan activo: ${state.config.plan.name}` : "Clave eliminada.", "ok"));
    };
  }

  async function loadConfig() {
    try {
      state.config = await (await api("/api/config")).json();
    } catch (e) {
      if (state.apiKey) {
        toast(`${e.message} Se usará el plan gratuito.`, "err");
        state.apiKey = ""; safeSet("apiKey", null);
        state.config = await (await api("/api/config")).json();
      } else {
        throw e;
      }
    }
    const cfg = state.config;
    document.querySelectorAll("[data-app-name]").forEach((el) => { el.textContent = cfg.app_name; });
    $("#planChip").textContent = `Plan ${cfg.plan.name}`;
    const theme = $("#optTheme"), page = $("#optPage");
    if (!theme.options.length) {
      theme.innerHTML = cfg.themes.map((t) => `<option value="${t.id}">${escapeHtml(t.name)}</option>`).join("");
      page.innerHTML = cfg.page_sizes.map((p) => `<option>${p}</option>`).join("");
      theme.value = safeGet("pdfTheme") || "moderno";
      page.value = safeGet("pdfPage") || "A4";
    }
    renderPlans();
  }

  // --------------------------------------------------------- tema visual
  function applyTheme(t) { document.documentElement.dataset.theme = t; }
  const stored = safeGet("uiTheme");
  applyTheme(stored || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"));
  $("#themeToggle").onclick = () => {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    applyTheme(next); safeSet("uiTheme", next);
  };

  // ------------------------------------------------------------- eventos
  document.querySelectorAll(".tab").forEach((t) => { t.onclick = () => setMode(t.dataset.mode); });

  const drop = $("#drop");
  const input = $("#fileInput");
  drop.onclick = (ev) => { if (!ev.target.closest("#newDoc")) input.click(); };
  drop.onkeydown = (ev) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); input.click(); } };
  input.onchange = () => { addFiles(input.files); input.value = ""; };
  ["dragenter", "dragover"].forEach((n) => drop.addEventListener(n, (ev) => { ev.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach((n) => drop.addEventListener(n, (ev) => { ev.preventDefault(); drop.classList.remove("over"); }));
  drop.addEventListener("drop", (ev) => addFiles(ev.dataTransfer.files));
  window.addEventListener("dragover", (ev) => ev.preventDefault());
  window.addEventListener("drop", (ev) => { ev.preventDefault(); if (!drop.contains(ev.target)) addFiles(ev.dataTransfer.files); });

  $("#newDoc").onclick = (ev) => { ev.stopPropagation(); newDocument(); };
  $("#clearAll").onclick = () => {
    state.lists[state.mode] = state.lists[state.mode].filter((i) => i.status === "working" || i.status === "queued");
    state.active[state.mode] = null;
    renderFiles(); renderWorkspace();
  };
  $("#downloadAll").onclick = downloadAll;

  const editor = $("#editor");
  editor.addEventListener("input", () => {
    const item = activeItem();
    if (!item) return;
    item.markdown = editor.value;
    item.pdfBlob = null;
    if (state.mode === "md2pdf" && item.status === "done") { item.status = "ready"; renderFiles(); }
    updateStats();
    schedulePreview();
  });
  editor.addEventListener("keydown", (ev) => {
    if (ev.key === "Tab" && !ev.shiftKey && !ev.ctrlKey && !ev.metaKey) { ev.preventDefault(); wrapSelection("    ", ""); }
    if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === "b") { ev.preventDefault(); wrapSelection("**", "**"); }
    if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === "i") { ev.preventDefault(); wrapSelection("*", "*"); }
    if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === "s") {
      ev.preventDefault();
      const item = activeItem();
      if (item && state.mode === "md2pdf") generatePdf(item).catch(() => {});
    }
  });

  $("#docName").addEventListener("input", (ev) => {
    const item = activeItem();
    if (item) { item.name = ev.target.value.trim() || "documento"; item.pdfBlob = null; renderFiles(); }
  });

  $("#toolbar").addEventListener("click", (ev) => {
    const b = ev.target.closest("button");
    if (!b) return;
    if (b.dataset.md) { const [a, c] = b.dataset.md.split("|"); wrapSelection(a, c); }
    else if (b.dataset.line) prefixLines(b.dataset.line);
    else if (b.dataset.block) insertBlock(b.dataset.block);
    else if (b.dataset.view) {
      $("#panes").dataset.view = b.dataset.view;
      document.querySelectorAll(".view-switch button").forEach((x) => x.classList.toggle("on", x === b));
    }
  });

  ["optTheme", "optPage"].forEach((id) => $(`#${id}`).addEventListener("change", (ev) => {
    safeSet(id === "optTheme" ? "pdfTheme" : "pdfPage", ev.target.value);
    state.lists.md2pdf.forEach((i) => { i.pdfBlob = null; if (i.status === "done") i.status = "ready"; });
    renderFiles();
    schedulePreview(0);
  }));
  $("#optEmbed").addEventListener("change", () => state.lists.md2pdf.forEach((i) => { i.pdfBlob = null; }));

  document.querySelectorAll("[data-origin]").forEach((el) => { el.textContent = location.origin; });
  $("#year").textContent = new Date().getFullYear();

  loadConfig().catch((e) => toast(`No se pudo conectar con el servidor: ${e.message}`, "err"));
  setMode("pdf2md");
})();
