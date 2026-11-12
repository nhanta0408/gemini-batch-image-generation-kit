"use strict";
// Image Batch Studio dashboard. Plain DOM, no build step. Every value from the project is inserted as text
// (never innerHTML), so a prompt containing HTML cannot run in the page.

const TOKEN = document.querySelector('meta[name="gbig-token"]').content;
const main = document.getElementById("main");
const state = { route: "", batch: null, filter: "all", search: "", selected: new Set(), confirm: null,
                editing: new Set(), openPrompts: new Set(), filesFilter: "problems", timer: null };

// ---- tiny helpers ----
function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "class") el.className = v;
    else if (k === "style") el.style.cssText = v;  // CSSOM, allowed by the page's CSP (style attributes are not)
    else if (v === true) el.setAttribute(k, "");
    else el.setAttribute(k, v);
  }
  for (const kid of kids.flat()) {
    if (kid === null || kid === undefined || kid === false) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}
// replaceChildren would print null/false as text; drop them like h() does
function put(el, ...kids) { el.replaceChildren(...kids.flat().filter((k) => k !== null && k !== undefined && k !== false)); }
async function api(method, path, body) {
  const res = await fetch(path, { method, headers: { "X-GBIG-Token": TOKEN, "Content-Type": "application/json" },
                                  body: body === undefined ? undefined : JSON.stringify(body) });
  const data = await res.json().catch(() => ({ error: `HTTP ${res.status}` }));
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}
let toastTimer;
function toast(msg, err) {
  const t = document.getElementById("toast");
  t.textContent = msg; t.className = "toast show" + (err ? " err" : "");
  clearTimeout(toastTimer); toastTimer = setTimeout(() => (t.className = "toast"), err ? 8000 : 3000);
}
const fail = (e) => toast(e.message || String(e), true);
function ago(iso) {
  if (!iso) return "";
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return `${Math.round(s)}s ago`;
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${(s / 3600).toFixed(1)} h ago`;
  return `${Math.round(s / 86400)} d ago`;
}
function duration(from, to) {
  if (!from) return "";
  const s = Math.max(0, ((to ? new Date(to) : new Date()) - new Date(from)) / 1000);
  return s < 3600 ? `${Math.round(s / 60)} min` : `${(s / 3600).toFixed(1)} h`;
}
const JOB_STATE = {
  JOB_STATE_PENDING: ["Queued", "info"], JOB_STATE_RUNNING: ["Drawing", "info"],
  JOB_STATE_SUCCEEDED: ["Succeeded", "ok"], JOB_STATE_FAILED: ["Failed", "bad"],
  JOB_STATE_CANCELLED: ["Cancelled", "warn"], JOB_STATE_EXPIRED: ["Expired", "bad"],
};
function jobPill(j) {
  const [label, cls] = JOB_STATE[j.state] || [j.state || "Unknown", ""];
  const fetched = j.fetched && j.state === "JOB_STATE_SUCCEEDED" ? " · downloaded" : "";
  return h("span", { class: `pill ${cls}` }, label + fetched);
}
const INTEGRITY = { ok: "ok", modified: "warn", renamed: "info", missing: "bad", untracked: "warn" };
const stat = (label, n, cls) => h("span", { class: `pill ${cls || ""}` }, `${label} ${n}`);
function isBusyEditing() {
  const a = document.activeElement;
  if (document.querySelector("dialog[open]")) return true;
  return state.editing.size > 0 || (a && ["INPUT", "TEXTAREA", "SELECT"].includes(a.tagName) && main.contains(a));
}

// ---- router ----
function route() {
  const hash = location.hash || "#/";
  const [, page, arg] = hash.split("/");
  state.route = page || "batches";
  document.querySelectorAll("[data-nav]").forEach((a) =>
    a.classList.toggle("on", a.dataset.nav === (page === "batch" || !page ? "batches" : page)));
  clearInterval(state.timer);
  if (page === "batch" && arg) {
    if (state.batch !== arg) { state.selected.clear(); state.confirm = null; state.editing.clear(); }
    state.batch = decodeURIComponent(arg);
    loadBatch(true);
    state.timer = setInterval(() => !isBusyEditing() && loadBatch(false), 15000);
  } else if (page === "jobs") {
    loadJobs(); state.timer = setInterval(loadJobs, 10000);
  } else if (page === "files") {
    loadFiles();
  } else {
    loadOverview(); state.timer = setInterval(() => !isBusyEditing() && loadOverview(), 15000);
  }
}
window.addEventListener("hashchange", route);

function header(ov) {
  const slot = document.getElementById("ws-name");
  state.ov = ov;
  const sig = JSON.stringify(ov.projects.map((p) => [p.name, p.current]));
  if (slot.dataset.sig !== sig) {  // rebuild only when the list changes, so an open dropdown is not reset
    slot.dataset.sig = sig;
    put(slot, ov.projects.length > 1
      ? h("select", { "aria-label": "Project", title: ov.workspace ? ov.workspace.root : "", onchange: async (e) => {
          try { await api("POST", "/api/project", { project: e.target.value }); location.hash = "#/"; route(); }
          catch (err) { fail(err); }
        } }, ov.projects.map((p) => h("option", { value: p.name, selected: p.current }, `Project: ${p.name}`)))
      : h("span", { title: ov.workspace ? ov.workspace.root : "" }, ov.workspace ? ov.workspace.name : "No project yet"),
      h("button", { class: "small ghost", onclick: () => newProjectDialog() }, "+ New project"));
  }
  const pill = document.getElementById("key-pill");
  pill.textContent = ov.key_set ? "Gemini key" : "Add Gemini key";
  pill.className = "pill " + (ov.key_set ? "ok" : "bad");
  pill.title = ov.key_set ? "Click to replace the key" : "Click to add your Gemini API key";
  pill.onclick = () => keyDialog();
}

// ---- overview ----
async function loadOverview() {
  try {
    const ov = await api("GET", "/api/overview");
    header(ov);
    if (!ov.workspace) return put(main, welcome(ov));
    const flying = ov.jobs.filter((j) => !j.fetched);
    put(main,
      h("div", { class: "head" },
        h("div", {}, h("h1", {}, ov.workspace.name), h("p", { class: "mono muted" }, ov.workspace.root)),
        h("div", { class: "settings" },
          flying.length ? h("a", { href: "#/jobs" }, h("span", { class: "pill info" }, `${flying.length} drawing`)) : null,
          h("button", { class: "primary", onclick: () => newBatchDialog() }, "New batch"))),
      ov.batches.length ? h("div", { class: "cards" }, ov.batches.map(batchCard))
        : h("div", { class: "empty" }, h("p", {}, h("b", {}, "No batches yet")),
            h("p", {}, "A batch is one set of pictures with a shared look."),
            h("p", {}, h("button", { class: "primary lg", onclick: () => newBatchDialog() }, "New batch"))));
  } catch (e) { fail(e); }
}
function copyButton(text, label) {
  const btn = h("button", { class: "small", "aria-label": `Copy: ${text}` }, label || "Copy");
  btn.addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(text); btn.textContent = "Copied"; }
    catch { btn.textContent = "Select and copy"; }
    setTimeout(() => (btn.textContent = label || "Copy"), 1600);
  });
  return btn;
}
function slugify(text, max) {
  return text.normalize("NFD").replace(/[\u0300-\u036f]/g, "").replace(/[\u0111\u0110]/g, "d").toLowerCase()
    .replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, max || 60).replace(/-+$/g, "");
}
function field(label, input, hint) {
  return h("label", { class: "field" }, h("span", {}, label), input, hint ? h("small", {}, hint) : null);
}
function modal(title, body, submitLabel, onSubmit) {
  const err = h("p", { class: "form-error", role: "alert" });
  const submit = h("button", { class: "primary", type: "submit" }, submitLabel);
  const dlg = h("dialog", { class: "modal" });
  const close = () => { dlg.close(); dlg.remove(); };
  const form = h("form", { method: "dialog", onsubmit: async (e) => {
      e.preventDefault(); err.textContent = ""; submit.disabled = true;
      try { await onSubmit(); close(); } catch (x) { err.textContent = x.message || String(x); }
      finally { submit.disabled = false; }
    } },
    h("div", { class: "modal-head" }, h("h2", {}, title),
      h("button", { type: "button", class: "small", "aria-label": "Close", onclick: close }, "✕")),
    h("div", { class: "modal-body" }, body), err,
    h("div", { class: "modal-actions" }, h("button", { type: "button", onclick: close }, "Cancel"), submit));
  dlg.append(form);
  dlg.addEventListener("cancel", (e) => { e.preventDefault(); close(); });
  document.body.append(dlg);
  dlg.showModal();
  const first = dlg.querySelector("input:not([type=radio]), textarea, select");
  if (first) first.focus();
  return dlg;
}
function newProjectDialog() {
  const ov = state.ov || {};
  const title = h("input", { type: "text", placeholder: "Blog pictures", required: true, autocomplete: "off" });
  const dir = h("input", { type: "text", placeholder: "~/Pictures", class: "mono" });
  const useDefault = h("input", { type: "radio", name: "where", checked: true });
  const useOther = h("input", { type: "radio", name: "where" });
  const pick = h("button", { type: "button" }, "Choose…");
  const otherRow = h("div", { class: "row-inline" }, dir, pick);
  const where = h("p", { class: "path-preview" });
  const slug = () => slugify(title.value) || "…";
  function preview() {
    otherRow.hidden = !useOther.checked;
    const base = useOther.checked ? (dir.value.trim() || "…") : ov.projects_dir;
    where.textContent = `${String(base).replace(/\/$/, "")}/${slug()}`;
  }
  [title, dir, useDefault, useOther].forEach((el) => el.addEventListener("input", preview));
  [useDefault, useOther].forEach((el) => el.addEventListener("change", preview));
  pick.addEventListener("click", async () => {
    pick.disabled = true;
    try { const r = await api("POST", "/api/choose-folder", {}); if (r.path) { dir.value = r.path; preview(); } }
    catch (e) { fail(e); }
    finally { pick.disabled = false; }
  });
  preview();
  modal("New project", [
    field("Name", title),
    h("div", { class: "field" }, h("span", {}, "Location"),
      h("div", { class: "seg" },
        h("label", {}, useDefault, h("b", {}, "Default"), h("small", {}, "output/ (not in git)")),
        h("label", {}, useOther, h("b", {}, "Choose folder"), h("small", {}, "anywhere on this Mac"))),
      otherRow),
    where,
  ], "Create project", async () => {
    if (!slug() || slug() === "…") throw new Error("Give the project a name.");
    if (useOther.checked && !dir.value.trim()) throw new Error("Choose a folder, or use Default.");
    await api("POST", "/api/projects", { name: slug(), title: title.value.trim(), dir: useOther.checked ? dir.value.trim() : "" });
    toast(`${title.value.trim()} created`);
    location.hash = "#/"; route();
  });
}
function keyDialog() {
  const input = h("input", { type: "password", placeholder: "AIza…", autocomplete: "off", spellcheck: "false", class: "mono" });
  modal(state.ov && state.ov.key_set ? "Replace Gemini key" : "Add Gemini key", [
    field("API key", input, "Stored only on this Mac."),
    h("a", { href: "https://aistudio.google.com/apikey", target: "_blank", rel: "noopener" }, "Get a key in Google AI Studio ↗"),
  ], "Save key", async () => {
    await api("POST", "/api/key", { key: input.value.trim() });
    toast("Key saved");
    route();
  });
}
function newBatchDialog() {
  const ov = state.ov || {};
  const month = new Date().toISOString().slice(0, 7);
  const topic = h("input", { type: "text", placeholder: "Healthy office", required: true, autocomplete: "off" });
  const style = h("textarea", { rows: 2, placeholder: "Editorial photo, warm window light, no text" });
  const aspect = h("select", {}, (ov.aspect_ratios || ["1:1"]).map((a) => h("option", { value: a, selected: a === "1:1" }, a)));
  modal("New batch", [
    field("Topic", topic),
    field("Look", style, "Added to every prompt."),
    field("Shape", aspect, "16:9 web · 4:5 feed · 9:16 story · 1:1 square"),
  ], "Create batch", async () => {
    const id = slugify(`${month}-${topic.value}`);
    if (id.length <= month.length) throw new Error("Give the batch a topic.");
    const r = await api("POST", "/api/batches", { id, topic: topic.value.trim(), style: style.value.trim(), aspect_ratio: aspect.value });
    location.hash = `#/batch/${encodeURIComponent(r.id)}`;
  });
}
function welcome(ov) {
  const columns = [[["4 / 5", "ok"], ["1 / 1", "ok"]], [["1 / 1", "ok"], ["4 / 5", "busy"]], [["4 / 5", "ok"], ["1 / 1", ""]]];
  return h("div", { class: "welcome" },
    h("section", {},
      h("p", { class: "eyebrow" }, "Gemini Batch, on your Mac"),
      h("h1", { class: "hero-title" }, "Whole image sets, ", h("em", {}, "half the price.")),
      h("p", { class: "hero-lead" }, "Your AI assistant writes the prompts. Gemini draws them in batch. You approve here."),
      h("div", { class: "cta" },
        h("button", { class: "primary lg", onclick: () => newProjectDialog() }, "Create project"),
        ov.key_set ? h("span", { class: "done-note" }, "✓ Gemini key added")
          : h("button", { class: "lg", onclick: () => keyDialog() }, "Add Gemini key")),
      h("ul", { class: "facts" }, h("li", {}, "−50% Batch pricing"), h("li", {}, "Runs locally"), h("li", {}, "Every file MD5-tracked"))),
    h("div", { class: "sheet", "aria-hidden": "true" }, columns.map((col) => h("div", { class: "sheet-col" },
      col.map(([ratio, st]) => h("div", { class: `tile ${st}`, style: `aspect-ratio: ${ratio}` },
        st === "busy" ? h("div", { class: "spinner" }) : st === "ok" ? h("span", { class: "tick" }, "✓") : null))))));
}
function batchCard(b) {
  const s = b.status;
  return h("a", { class: "card", href: `#/batch/${encodeURIComponent(b.id)}` },
    h("h3", {}, b.topic || b.id),
    h("div", { class: "mono muted" }, b.id),
    h("div", { class: "stats" }, stat("draft prompt", s.draft, "warn"), stat("approved", Math.max(0, s.approved - s.drawn), "info"),
      stat("generated", s.drawn, "ok"),
      s.in_flight ? stat("generating", s.in_flight, "info") : null, s.redo ? stat("redo", s.redo, "bad") : null),
    h("p", { class: "next" }, s.next));
}
// ---- batch ----
let batchData = null;
async function loadBatch(first) {
  try {
    if (first) put(main, h("p", { class: "muted" }, "Loading…"));
    const [ov, b] = await Promise.all([api("GET", "/api/overview"), api("GET", `/api/batch/${encodeURIComponent(state.batch)}`)]);
    header(ov); batchData = b; batchData.runs = ov.runs.filter((r) => r.batch === b.id);
    renderBatch();
  } catch (e) { fail(e); }
}
// Stored status is draft / approved / drop; what people see is the whole lifecycle of a picture.
const STAGE = {
  draft: ["Draft prompt", "warn"], approved: ["Approved prompt", "info"], generating: ["Generating", "info"],
  generated: ["Generated", "ok"], drop: ["Dropped", ""],
};
function stageOf(it) {
  const st = it.status || "draft";
  if (st === "drop") return "drop";
  if (it.in_flight) return "generating";
  if (st === "approved" && it.pictures.length) return "generated";
  return st === "approved" ? "approved" : "draft";
}
function stageCounts(items) {
  const c = { all: items.length, draft: 0, approved: 0, generating: 0, generated: 0, drop: 0, redo: 0 };
  items.forEach((it) => { c[stageOf(it)] += 1; if (it.result === "redo") c.redo += 1; });
  return c;
}
function visibleItems() {
  const q = state.search.toLowerCase();
  return batchData.items.filter((it) => {
    const ok = state.filter === "all" || (state.filter === "redo" ? it.result === "redo" : stageOf(it) === state.filter);
    return ok && (!q || [it.id, it.topic, it.summary, it.prompt, it.fix, it.notes].join(" ").toLowerCase().includes(q));
  });
}
function renderBatch() {
  const b = batchData, s = b.status;
  const items = visibleItems();
  const running = (b.runs || []).find((r) => r.running);
  const n = stageCounts(b.items);
  const tabs = [["all", "All"], ["draft", "Draft prompt"], ["approved", "Approved prompt"], ["generating", "Generating"],
                ["generated", "Generated"], ["redo", "Redo"], ["drop", "Dropped"]].filter(([k]) => k === "all" || n[k]);
  const sel = [...state.selected].filter((id) => b.items.some((i) => i.id === id));
  put(main, 
    h("p", {}, h("a", { href: "#/" }, "← Batches")),
    h("div", { class: "head" },
      h("div", {}, h("h1", {}, b.meta.topic || b.id), h("div", { class: "mono muted" }, b.id),
        b.items.length ? h("p", { class: "next" }, s.next) : null),
      h("div", { class: "settings" }, settingsForm(b),
        h("button", { onclick: () => openFolder(b.id, "images"), title: "Show this batch's pictures in Finder" }, "Open folder"),
        h("button", { class: "ghost", onclick: () => openFolder(b.id, "legacy"), title: "Earlier drawings of re-drawn prompts" }, "Legacy"))),
    h("details", { class: "style" }, h("summary", {}, "Look (added to every prompt)"), styleEditor(b)),
    b.errors.length ? h("div", { class: "confirm" }, h("b", {}, "prompts.csv has problems:"),
      h("ul", {}, b.errors.map((e) => h("li", {}, e)))) : null,
    running ? h("div", { class: "confirm" }, h("div", { class: "spinner" }), "Sending to Gemini…",
      h("pre", { class: "log" }, running.log)) : null,
    state.confirm ? confirmPanel(state.confirm) : null,
    h("div", { class: "bar" },
      h("div", { class: "tabs" }, tabs.map(([key, label]) => h("button", {
        class: "small" + (state.filter === key ? " on" : ""), onclick: () => { state.filter = key; renderBatch(); },
      }, `${label} ${n[key]}`))),
      h("input", { type: "search", placeholder: "Search", value: state.search, "aria-label": "Search prompts",
                   oninput: (e) => { state.search = e.target.value; renderBatchList(); } }),
      h("span", { class: "grow" }),
      sel.length ? h("span", { class: "muted" }, `${sel.length} selected`) : null,
      sel.length ? h("button", { class: "small", onclick: () => bulk("approved", sel) }, "Approve prompt") : null,
      sel.length ? h("button", { class: "small", onclick: () => bulk("draft", sel) }, "Back to draft") : null,
      sel.length ? h("button", { class: "small danger", onclick: () => bulk("drop", sel) }, "Drop") : null,
      sel.length ? h("button", { class: "small", onclick: () => askGenerate(sel) }, `Generate ${sel.length}`) : null,
      h("button", { class: "primary", disabled: !s.to_draw || !!running, onclick: () => askGenerate([]),
                    title: "Approved prompts without a picture, plus pictures marked Redo" },
        s.to_draw ? `Generate ${s.to_draw}` : "Generate")),
    h("div", { id: "list" }));
  renderBatchList(items);
}
function renderBatchList(items) {
  items = items || visibleItems();
  const list = document.getElementById("list");
  if (!list) return;
  const allSel = items.length && items.every((i) => state.selected.has(i.id));
  put(list, 
    items.length ? h("label", { class: "muted" }, h("input", { type: "checkbox", checked: allSel, onchange: (e) => {
      items.forEach((i) => (e.target.checked ? state.selected.add(i.id) : state.selected.delete(i.id))); renderBatch();
    } }), ` Select all ${items.length} shown`) : batchData.items.length ? h("div", { class: "empty" }, "Nothing matches this filter.")
      : emptyBatch(),
    ...items.map(itemRow));
}
function emptyBatch() {
  const ask = `Write the prompts for batch ${batchData.id} in project "${(state.ov && state.ov.workspace && state.ov.workspace.name) || ""}": `
            + "<what the pictures are for, how many, the look>";
  return h("div", { class: "empty" },
    h("p", {}, h("b", {}, "No prompts yet")),
    h("p", {}, "Ask your AI assistant:"),
    h("div", { class: "say" }, h("q", {}, ask), copyButton(ask)));
}
function settingsForm(b) {
  const c = b.choices;
  const select = (name, value, options, labelFn) => h("select", { "aria-label": name, onchange: (e) => saveMeta({ [name]: e.target.value }) },
    options.map((o) => h("option", { value: o, selected: o === value }, labelFn ? labelFn(o) : o)));
  return h("div", { class: "settings" },
    h("label", {}, "Model", select("model", b.model, c.models, (m) => `${m} (~$${(b.prices[m] / 2).toFixed(3)}/img)`)),
    h("label", {}, "Default shape", select("aspect_ratio", b.default_aspect, c.aspect_ratios)),
    h("label", {}, "Size", select("image_size", b.image_size, c.image_sizes, (s) => s || "default")));
}
function styleEditor(b) {
  const ta = h("textarea", { "aria-label": "Batch style" }, b.style);
  ta.addEventListener("input", () => state.editing.add("__style"));
  return h("div", {}, ta, h("p", {}, h("button", { class: "small", onclick: async () => {
    await saveMeta({ style: ta.value }); state.editing.delete("__style");
  } }, "Save style"), " ", h("span", { class: "muted" }, "Changes apply to pictures generated after saving.")));
}
async function saveMeta(fields) {
  try { await api("POST", `/api/batch/${encodeURIComponent(batchData.id)}/meta`, fields); toast("Saved"); loadBatch(false); }
  catch (e) { fail(e); }
}
function itemRow(it) {
  const st = it.status || "draft";
  const editing = state.editing.has(it.id);
  const [w, hgt] = (it.effective_aspect || "1:1").split(":").map(Number);
  const frames = [];
  for (let v = 1; v <= it.variant_count; v++) {
    const pic = it.pictures.find((p) => p.variant === v);
    const style = `aspect-ratio: ${w} / ${hgt}`;
    if (pic && pic.url) {
      frames.push(h("a", { class: "frame", style, href: pic.url, target: "_blank", rel: "noopener",
                           title: `${pic.file}\nrev ${pic.rev} · ${pic.job}\nMD5 ${pic.md5}` },
        h("img", { src: pic.url, alt: `${it.summary || it.id} (variant ${v})`, loading: "lazy" }),
        pic.integrity !== "ok" ? h("span", { class: "badge bad" }, pic.integrity) : null));
    } else if (pic) {
      frames.push(h("div", { class: "frame blank", style }, h("span", {}, "File missing (see Files)")));
    } else if (it.in_flight) {
      frames.push(h("div", { class: "frame blank", style }, h("div", {}, h("div", { class: "spinner" }), "Drawing…")));
    } else {
      frames.push(h("div", { class: "frame blank", style }, h("span", {}, "No result yet")));
    }
  }
  const drawn = it.pictures.length > 0;
  // Quick fix: sent to Gemini ahead of the prompt and wins over it, so a redraw can be corrected without editing.
  const fix = h("input", { type: "text", value: it.fix, class: "fix" + (it.fix ? " active" : ""),
                           placeholder: "Quick fix: what to change (overrides the prompt)", "aria-label": `Quick fix for ${it.id}`,
                           onchange: (e) => saveItem(it.id, { fix: e.target.value }) });
  const redo = () => it.result === "redo" && fix.value === it.fix
    ? saveItem(it.id, { result: "" })
    : saveItem(it.id, { fix: fix.value, result: "redo" });
  return h("div", { class: "row" + (st === "drop" ? " drop" : "") },
    h("div", {},
      h("div", { class: "meta" },
        h("input", { type: "checkbox", "aria-label": `Select ${it.id}`, checked: state.selected.has(it.id), onchange: (e) => {
          e.target.checked ? state.selected.add(it.id) : state.selected.delete(it.id); renderBatch();
        } }),
        h("span", { class: "mono" }, it.id),
        it.topic ? h("span", { class: "chip" }, it.topic) : null,
        stagePill(it),
        stageAction(it),
        h("span", { class: "chip" }, `${it.variant_count} × ${it.effective_aspect}`)),
      editing ? itemEditor(it) : [
        h("div", { class: "summary" }, it.summary || "(no summary)"),
        promptDetails(it),
        h("button", { class: "small", onclick: () => { state.editing.add(it.id); renderBatchList(); } }, "Edit prompt")]),
    h("div", { class: "results" }, frames,
      drawn ? h("div", { class: "review" },
        h("button", { class: "small" + (it.result === "ok" ? " on" : ""), onclick: () => saveItem(it.id, { result: it.result === "ok" ? "" : "ok" }) }, "✓ OK"),
        h("button", { class: "small" + (it.result === "redo" ? " on" : ""), onclick: redo,
                      title: "Draw again on the next Generate, with the quick fix" }, "↻ Redo"),
        fix) : h("div", { class: "review" }, fix)));
}
function stagePill(it) {
  const [label, cls] = STAGE[stageOf(it)];
  return h("span", { class: `pill ${cls}` }, label);
}
function stageAction(it) {
  const stage = stageOf(it);
  const btn = (label, status, cls) => h("button", { class: `small ${cls || ""}`, onclick: () => saveItem(it.id, { status }) }, label);
  if (stage === "draft") return [btn("Approve prompt", "approved", "primary"), btn("Drop", "drop", "ghost")];
  if (stage === "approved") return btn("Back to draft", "draft", "ghost");
  if (stage === "drop") return btn("Restore", "draft", "ghost");
  return null;  // generating / generated: the picture drives the next step (OK / Redo)
}
async function openFolder(batchId, what) {
  try { await api("POST", `/api/batch/${encodeURIComponent(batchId)}/open-folder`, { what }); }
  catch (e) { fail(e); }
}
function promptDetails(it) {
  const d = h("details", { open: state.openPrompts.has(it.id) },
    h("summary", {}, "Full prompt sent to Gemini"), h("pre", {}, it.full_prompt));
  d.addEventListener("toggle", () => (d.open ? state.openPrompts.add(it.id) : state.openPrompts.delete(it.id)));
  return d;
}
function itemEditor(it) {
  const sum = h("input", { type: "text", value: it.summary, style: "width:100%", "aria-label": "Summary" });
  const topic = h("input", { type: "text", value: it.topic, "aria-label": "Topic" });
  const variants = h("select", { "aria-label": "Variants" }, [1, 2, 3, 4, 5, 6, 7, 8].map((n) =>
    h("option", { value: n, selected: n === it.variant_count }, `${n} picture${n > 1 ? "s" : ""}`)));
  const aspect = h("select", { "aria-label": "Shape" }, ["", ...batchData.choices.aspect_ratios].map((a) =>
    h("option", { value: a, selected: a === it.aspect_ratio }, a || `batch default (${batchData.default_aspect})`)));
  const prompt = h("textarea", { "aria-label": "Prompt" }, it.prompt);
  const notes = h("input", { type: "text", value: it.notes, style: "width:100%", "aria-label": "Notes" });
  return h("div", {},
    h("p", {}, h("label", { class: "muted" }, "Summary", h("br"), sum)),
    h("p", { class: "settings" }, h("label", {}, "Topic", topic), h("label", {}, "Pictures", variants), h("label", {}, "Shape", aspect)),
    h("label", { class: "muted" }, "Prompt (the batch style is added after it)"), prompt,
    h("p", {}, h("label", { class: "muted" }, "Notes (for people, not sent to Gemini)", h("br"), notes)),
    h("p", {}, h("button", { class: "small primary", onclick: async () => {
      if (await saveItem(it.id, { summary: sum.value, topic: topic.value, prompt: prompt.value, variants: variants.value,
                                   aspect_ratio: aspect.value, notes: notes.value }, true)) { state.editing.delete(it.id); loadBatch(false); }
    } }, "Save"), " ", h("button", { class: "small", onclick: () => { state.editing.delete(it.id); renderBatchList(); } }, "Cancel")));
}
async function saveItem(id, fields, quiet) {
  try {
    await api("POST", `/api/batch/${encodeURIComponent(batchData.id)}/item`, { id, fields });
    if (!quiet) { toast("Saved"); loadBatch(false); }
    return true;
  } catch (e) { fail(e); return false; }
}
async function bulk(status, ids) {
  try { await api("POST", `/api/batch/${encodeURIComponent(batchData.id)}/bulk-status`, { status, ids });
        state.selected.clear(); toast(`${ids.length} set to ${status}`); loadBatch(false); }
  catch (e) { fail(e); }
}
async function askGenerate(ids) {
  try {
    const plan = await api("POST", `/api/batch/${encodeURIComponent(batchData.id)}/generate`, { ids, dry_run: true });
    state.confirm = { ...plan, ids }; renderBatch(); window.scrollTo({ top: 0, behavior: "smooth" });
  } catch (e) { fail(e); }
}
function confirmPanel(c) {
  const blocked = !c.key_set || c.errors.length || !c.pictures;
  return h("div", { class: "confirm", role: "dialog", "aria-label": "Confirm generation" },
    h("div", { class: "big" }, `${c.pictures} pictures · ~$${c.estimated_usd.toFixed(2)}`),
    h("p", { class: "muted" }, `${c.model} · Batch, usually minutes, max 24 h · estimate`),
    !c.key_set ? h("p", { class: "st-drop" }, "Add your Gemini key first (top right).") : null,
    c.notes.length ? h("ul", {}, c.notes.map((n) => h("li", {}, n))) : null,
    !c.pictures ? h("p", {}, "Nothing to draw. Approve prompts first.") : null,
    h("p", { class: "cta" }, h("button", { class: "primary", disabled: blocked, onclick: () => generate(c.ids) }, "Send to Gemini"),
      h("button", { class: "ghost", onclick: () => { state.confirm = null; renderBatch(); } }, "Cancel")));
}
async function generate(ids) {
  try {
    await api("POST", `/api/batch/${encodeURIComponent(batchData.id)}/generate`, { ids, dry_run: false });
    state.confirm = null; state.selected.clear(); toast("Sending to Gemini…");
    setTimeout(() => loadBatch(false), 1500); setTimeout(() => loadBatch(false), 6000);
  } catch (e) { fail(e); }
}

// ---- jobs monitor ----
async function loadJobs() {
  try {
    const ov = await api("GET", "/api/overview");
    header(ov);
    const p = ov.poller;
    put(main, 
      h("div", { class: "head" }, h("div", {}, h("h1", {}, "Jobs monitor"),
        h("p", { class: "muted" }, "Checked every minute while the dashboard runs.")),
        h("button", { onclick: async () => { try { await api("POST", "/api/refresh"); toast("Checking with Gemini…");
                                                   setTimeout(loadJobs, 2500); } catch (e) { fail(e); } } }, "Check now")),
      h("p", {}, p.running ? h("span", { class: "pill info" }, "Checking now…") : null, " ",
        p.last_at ? `Last check ${ago(p.last_at)}` : "Not checked yet", p.next_at ? ` · next around ${p.next_at}` : "",
        p.last_error ? h("span", { class: "pill bad" }, p.last_error) : null),
      ov.jobs.length ? h("div", { class: "table-wrap" }, h("table", {},
        h("thead", {}, h("tr", {}, ["Batch", "Job", "State", "Pictures", "Saved", "Failed", "Submitted", "Time", "Cost est.", ""].map((t) => h("th", {}, t)))),
        h("tbody", {}, ov.jobs.map((j) => h("tr", {},
          h("td", {}, h("a", { href: `#/batch/${encodeURIComponent(j.batch)}` }, j.batch)),
          h("td", { class: "mono" }, j.dir, h("br"), h("span", { class: "muted" }, j.batch_name)),
          h("td", {}, jobPill(j), j.last_error ? h("div", { class: "st-drop" }, j.last_error) : null),
          h("td", {}, j.count), h("td", {}, j.ok ?? "–"), h("td", {}, j.failed ? j.failed.length : "–"),
          h("td", {}, ago(j.submitted_at)), h("td", {}, duration(j.submitted_at, j.fetched_at)),
          h("td", {}, j.estimated_usd !== undefined ? `$${Number(j.estimated_usd).toFixed(2)}` : ""),
          h("td", {}, !j.fetched ? h("button", { class: "small danger", onclick: () => cancelJob(j) }, "Cancel") : null)))))) :
        h("div", { class: "empty" }, h("p", {}, h("b", {}, "No jobs yet"))),
      h("h2", {}, "Recent runs"),
      ov.runs.length ? ov.runs.map((r) => h("details", { open: r.running },
        h("summary", {}, `${r.started_at} · ${r.kind} · ${r.batch} · `, r.running ? "running" : (r.exit_code === 0 ? "done" : `failed (exit ${r.exit_code})`)),
        h("pre", { class: "log" }, r.log))) : h("p", { class: "muted" }, "None yet."));
  } catch (e) { fail(e); }
}
async function cancelJob(j) {
  if (!confirm(`Cancel ${j.dir} (${j.count} pictures)? Pictures not finished yet will not be drawn.`)) return;
  try { await api("POST", `/api/batch/${encodeURIComponent(j.batch)}/cancel`, { job: j.dir }); toast("Cancel requested"); loadJobs(); }
  catch (e) { fail(e); }
}

// ---- files & integrity ----
async function loadFiles() {
  try {
    put(main, h("h1", {}, "Files & integrity"), h("p", { class: "muted" }, "Checking every picture's MD5…"));
    const [ov, a] = await Promise.all([api("GET", "/api/overview"), api("GET", "/api/audit")]);
    header(ov);
    renderFiles(a);
  } catch (e) { fail(e); }
}
function renderFiles(a) {
  const rows = [...Object.values(a.batches).flat(), ...a.untracked];
  const f = state.filesFilter;
  const shown = rows.filter((r) => f === "all" ? true : f === "old" ? !r.current : f === "problems" ? r.current && r.status !== "ok" : r.current);
  const filters = [["problems", "Problems only"], ["current", "Current files"], ["old", "Earlier drawings"], ["all", "Everything"]];
  put(main, 
    h("div", { class: "head" }, h("div", {}, h("h1", {}, "Files & integrity"),
      h("p", { class: "muted" }, `MD5 vs. Gemini's original · checked ${ago(a.checked_at)}`)),
      h("button", { onclick: loadFiles }, "Check again")),
    h("div", { class: "stats" }, Object.entries(a.counts).map(([k, n]) => stat(k, n, INTEGRITY[k]))),
    h("div", { class: "tabs" }, filters.map(([k, label]) => h("button", { class: "small" + (f === k ? " on" : ""),
      onclick: () => { state.filesFilter = k; renderFiles(a); } }, label))),
    h("p"),
    shown.length ? h("div", { class: "table-wrap" }, h("table", {},
      h("thead", {}, h("tr", {}, ["", "File (as generated)", "Batch / item", "Job", "MD5 at generation", "Status", "Now at"].map((t) => h("th", {}, t)))),
      h("tbody", {}, shown.map((r) => h("tr", {},
        h("td", {}, r.found_at ? h("img", { class: "thumb", src: `/files/${r.found_at}?v=${(r.md5 || "").slice(0, 8)}`, alt: "", loading: "lazy" }) : null),
        h("td", { class: "mono" }, r.path), h("td", {}, r.batch, r.item ? ` / ${r.item}` : ""),
        h("td", { class: "mono" }, r.job || ""), h("td", { class: "mono" }, r.md5),
        h("td", {}, h("span", { class: `pill ${INTEGRITY[r.status] || ""}` }, r.status)),
        h("td", { class: "mono" }, r.status === "renamed" || r.status === "untracked" ? r.found_at : "")))))) :
      h("div", { class: "empty" }, f === "problems" ? "No problems: every file matches what Gemini produced." : "No files."));
}

route();
