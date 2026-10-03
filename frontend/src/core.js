/* FlowGuard frontend core: API transport, state, routing, helpers, shell, login, AI assistant.
   All authorisation is enforced by the server; hiding a menu item here is a convenience, not a control. */
"use strict";
const FG = { user: null, csrf: null, meta: null, embedded: !!window.FG_EMBED, py: null, token: null, lastProposal: null, aiOpen: false };
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = v => v === null || v === undefined ? "" : String(v).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const gbp = v => v === null || v === undefined || v === "" ? "–" : "£" + Number(v).toLocaleString("en-GB", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const gbp0 = v => v === null || v === undefined || v === "" ? "–" : "£" + Number(v).toLocaleString("en-GB", { maximumFractionDigits: 0 });
const num = v => Number(v || 0).toLocaleString("en-GB");
const dt = v => v ? new Date(v.replace(" ", "T")).toLocaleString("en-GB", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }) : "–";
const dd = v => v ? new Date(v + "T00:00:00").toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" }) : "–";
const STATUS_LABEL = { READY: "Ready", NEEDS_REVIEW: "Needs review", BLOCKED: "Blocked", INVOICED: "Invoiced" };
const st = (s, label) => s ? `<span class="st ${esc(s)}">${esc(label || STATUS_LABEL[s] || s)}</span>` : `<span class="st neutral">–</span>`;
const code = c => `<span class="tag code">${esc(c)}</span>`;
const qs = o => Object.entries(o).filter(([, v]) => v !== "" && v !== null && v !== undefined).map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`).join("&");

/* ---------------------------------------------------------------- transport */
async function api(method, path, { query = {}, body = null } = {}) {
  let res;
  if (FG.embedded) {
    const r = FG.py.call(method, path, JSON.stringify(query), body ? JSON.stringify(body) : "", FG.token || "", FG.csrf || "");
    res = JSON.parse(r);
    if (res.set_session) { FG.token = res.set_session[0]; FG.csrf = res.set_session[1]; }
    if (res.clear_session) { FG.token = null; FG.csrf = null; }
  } else {
    const q = qs(query);
    const r = await fetch(path + (q ? "?" + q : ""), {
      method, credentials: "same-origin",
      headers: { "Content-Type": "application/json", ...(FG.csrf ? { "X-CSRF-Token": FG.csrf } : {}) },
      body: body ? JSON.stringify(body) : undefined
    });
    const ct = r.headers.get("content-type") || "";
    const binary = /pdf|zip|octet-stream/.test(ct);
    res = { status: r.status, content_type: ct.split(";")[0], body: ct.includes("json") ? await r.json() : binary ? await r.blob() : await r.text(),
            filename: (r.headers.get("content-disposition") || "").split("filename=")[1]?.replace(/"/g, "") };
  }
  if (res.status === 401 && path !== "/api/auth/login" && path !== "/api/auth/me") { FG.user = null; renderLogin("Your session has ended. Sign in again."); throw new Error("Sign in required"); }
  if (res.status >= 400) { const e = new Error(res.body?.message || "Request failed"); e.status = res.status; e.body = res.body; throw e; }
  if (res.content_type && res.content_type !== "application/json") return { text: res.body, filename: res.filename, type: res.content_type };
  return res.body;
}
const GET = (p, query) => api("GET", p, { query });
const POST = (p, body) => api("POST", p, { body: body || {} });
const PUT = (p, body) => api("PUT", p, { body: body || {} });

function download(text, filename, type = "text/csv") {
  const url = URL.createObjectURL(text instanceof Blob ? text : new Blob([text], { type }));
  const a = document.createElement("a"); a.href = url; a.download = filename || "export.csv"; document.body.appendChild(a); a.click();
  setTimeout(() => { URL.revokeObjectURL(url); a.remove(); }, 500);
}
function toast(msg, err) {
  const t = document.createElement("div"); t.className = "toast" + (err ? " err" : ""); t.setAttribute("role", "status"); t.textContent = msg;
  document.body.appendChild(t); setTimeout(() => leave(t), err ? 6000 : 3500);
}
function modal(html, onMount) {
  const bg = document.createElement("div"); bg.className = "modal-bg";
  bg.innerHTML = `<div class="modal" role="dialog" aria-modal="true">${html}</div>`;
  document.body.appendChild(bg);
  const close = () => leave(bg);
  bg.addEventListener("click", e => { if (e.target === bg || e.target.closest("[data-close]")) close(); });
  document.addEventListener("keydown", function k(e) { if (e.key === "Escape") { close(); document.removeEventListener("keydown", k); } });
  onMount && onMount(bg.querySelector(".modal"), close);
  bg.querySelector("input,select,textarea,button")?.focus();
  return close;
}
/* ---------------------------------------------------------------- motion helpers */
const REDUCED = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
// Plays the element's exit animation (".out") before removing it; removes at once when motion is reduced.
function leave(el, ms = 260) {
  if (REDUCED || !el.isConnected) { el.remove(); return; }
  el.classList.add("out"); setTimeout(() => el.remove(), ms);
}
const SKELETON = `<div class="skel" aria-label="Loading"><i class="h"></i><div class="row"><i class="k"></i><i class="k"></i><i class="k"></i><i class="k"></i></div><i></i><i style="width:85%"></i><i style="width:70%"></i><i style="width:92%"></i></div>`;
// Counts a rendered figure up from zero, keeping its prefix, suffix, separators and decimals.
function countUp(el, ms = 750) {
  if (REDUCED || el.children.length || el.dataset.counted) return;
  const m = el.textContent.trim().match(/^([^\d-]*)(-?[\d,]*\.?\d+)(.*)$/);
  if (!m) return;
  const target = parseFloat(m[2].replace(/,/g, "")); if (!isFinite(target) || target === 0) return;
  const dec = (m[2].split(".")[1] || "").length, comma = m[2].includes(",") || Math.abs(target) >= 10000;
  const fmt = v => m[1] + v.toLocaleString("en-GB", { minimumFractionDigits: dec, maximumFractionDigits: dec, useGrouping: comma }) + m[3];
  const final = el.textContent; el.dataset.counted = "1";
  const t0 = performance.now();
  const step = now => {
    const k = Math.min(1, (now - t0) / ms), e = 1 - Math.pow(1 - k, 3);
    el.textContent = k < 1 ? fmt(target * e) : final;
    if (k < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}
// Staggered entrance for a freshly rendered page. The class is removed afterwards so hover transforms work again.
function enter(c) {
  if (REDUCED) return;
  $$(".grid", c).forEach(g => [...g.children].forEach((x, i) => x.style.setProperty("--gi", Math.min(i, 12))));
  $$("tbody", c).forEach(tb => [...tb.rows].slice(0, 30).forEach((tr, i) => { tr.classList.add("stagger"); tr.style.setProperty("--ri", i); }));
  $$(".tl-node", c).forEach((n, i) => n.style.setProperty("--ti", Math.min(i, 20)));
  c.classList.remove("enter"); void c.offsetWidth; c.classList.add("enter");
  $$(".kpi .v, .mm-node .t2", c).forEach(el => countUp(el));
  clearTimeout(c._enterT); c._enterT = setTimeout(() => c.classList.remove("enter"), 1500);
}

async function downloadFile(path, fallbackName, btn) {
  const label = btn?.innerHTML;
  if (btn) { btn.disabled = true; btn.textContent = "Preparing…"; }
  try { const f = await api("GET", path); download(f.text, f.filename || fallbackName); }
  catch (e) { toast(e.message, true); }
  finally { if (btn) { btn.disabled = false; btn.innerHTML = label; } }
}
const errBox = e => `<div class="note bad"><b>${esc(e.status === 403 ? "Not permitted" : "Could not load")}</b><br>${esc(e.message)}</div>`;

/* ---------------------------------------------------------------- icons */
const I = {
  dash: "M3 13h8V3H3zM13 21h8V11h-8zM3 21h8v-6H3zM13 3v6h8V3z", map: "M9 3 3 6v15l6-3 6 3 6-3V3l-6 3-6-3zM9 3v15M15 6v15",
  jobs: "M4 6h16M4 12h16M4 18h10", review: "M12 3 2 21h20L12 3zM12 10v5M12 18h.01", inv: "M6 2h9l5 5v15H6zM14 2v6h6M9 13h6M9 17h6",
  client: "M3 21V7l9-4 9 4v14M9 21v-6h6v6", agent: "M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM4 21a8 8 0 0 1 16 0", contract: "M7 3h10v18H7zM10 7h4M10 11h4M10 15h2",
  rate: "M4 19V5M4 19h16M8 15l3-4 3 2 5-6", rules: "M12 2 4 6v6c0 5 4 9 8 10 4-1 8-5 8-10V6z", config: "M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12M20 18h0M14 4v4M8 10v4M16 16v4",
  import: "M12 3v12M7 10l5 5 5-5M4 21h16", integ: "M7 7h4v4H7zM13 13h4v4h-4zM11 9h4v4", recon: "M4 7h11l-3-3M20 17H9l3 3",
  audit: "M5 3h14v18l-7-4-7 4z", ai: "M12 3l2.5 5.5L20 11l-5.5 2.5L12 19l-2.5-5.5L4 11l5.5-2.5z", bell: "M6 8a6 6 0 1 1 12 0c0 7 3 8 3 8H3s3-1 3-8M10 21h4",
  gear: "M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z",
  user: "M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM4 21a8 8 0 0 1 16 0", menu: "M3 6h18M3 12h18M3 18h18", out: "M9 21H5V3h4M16 17l5-5-5-5M21 12H9",
  shield: "M12 2 4 6v6c0 5 4 9 8 10 4-1 8-5 8-10V6zM9 12l2 2 4-4"
};
const icon = (n, s = 16) => `<svg viewBox="0 0 24 24" width="${s}" height="${s}" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${I[n] || I.dash}"/></svg>`;

/* ---------------------------------------------------------------- routing */
const ROUTES = {};
const route = (pattern, fn) => { ROUTES[pattern] = fn; };
function go(h) { if (location.hash === h) onRoute(); else location.hash = h; }
function parseHash() {
  const [p, q] = (location.hash.slice(1) || "/").split("?");
  return { path: p || "/", query: Object.fromEntries(new URLSearchParams(q || "")) };
}
async function onRoute() {
  if (!FG.user) return;
  const { path, query } = parseHash();
  for (const [pat, fn] of Object.entries(ROUTES)) {
    const rx = new RegExp("^" + pat.replace(/:(\w+)/g, "(?<$1>[^/]+)") + "$");
    const m = path.match(rx);
    if (m) {
      const c = $("#content"); c.classList.remove("enter"); c.innerHTML = SKELETON; c.scrollTop = 0;
      $$(".nav a").forEach(a => a.classList.toggle("on", path.startsWith(a.dataset.base) && (a.dataset.base !== "/" || path === "/")));
      $(".side")?.classList.remove("open");
      try { await fn(c, m.groups || {}, query); } catch (e) { if (e.message !== "Sign in required") c.innerHTML = errBox(e); console.error(e); }
      enter(c);
      return;
    }
  }
  $("#content").innerHTML = `<div class="empty"><b>Page not found</b><a href="#/">Go to the dashboard</a></div>`; enter($("#content"));
}
window.addEventListener("hashchange", onRoute);

/* ---------------------------------------------------------------- shell */
const STAFF_NAV = [
  ["Overview", [["/", "Dashboard", "dash"], ["/money-map", "Money Map", "map"]]],
  ["Work", [["/jobs", "Jobs", "jobs"], ["/reviews", "Review Centre", "review", "reviewCount"], ["/invoices", "Invoices", "inv"], ["/reconciliation", "Reconciliation", "recon"]]],
  ["Commercial", [["/clients", "Clients", "client"], ["/agents", "Agents / Suppliers", "agent"], ["/contracts", "Contracts", "contract"], ["/rate-cards", "Rate Cards", "rate"], ["/rules", "Rules", "rules"], ["/configuration", "Configuration", "config"]]],
  ["Data", [["/imports", "Imports", "import"], ["/integrations", "Integrations", "integ"]]],
  ["Governance", [["/audit", "Audit Trail", "audit"], ["/ai", "AI & Automation", "ai"], ["/notifications", "Notifications", "bell"], ["/settings", "Settings", "gear"]]],
];
const AGENT_NAV = [["", [["/", "Dashboard", "dash"], ["/my-jobs", "My Jobs", "jobs"], ["/my-invoices", "My Invoices", "inv"], ["/notifications", "Notifications", "bell"], ["/profile", "Profile", "user"]]]];

function renderShell() {
  const agent = FG.user.role === "AGENT";
  const nav = (agent ? AGENT_NAV : STAFF_NAV).map(([g, items]) =>
    (g ? `<div class="grp">${esc(g)}</div>` : "") + items.map(([h, l, ic, cnt]) =>
      `<a href="#${h}" data-base="${h}">${icon(ic)}<span>${esc(l)}</span>${cnt ? `<span class="count" id="${cnt}" hidden></span>` : ""}</a>`).join("")).join("");
  document.body.innerHTML = `
  <div class="app">
    <aside class="side" aria-label="Main navigation">
      <div class="brand"><div class="mark">${icon("shield", 18)}</div><div><b>FlowGuard</b><small class="muted">${agent ? "Agent portal" : "Job-to-Cash control"}</small></div></div>
      <nav class="nav">${nav}</nav>
      <div class="side-foot">Rules decide the money.<br>AI explains and improves the rules.</div>
    </aside>
    <div class="main">
      <header class="top">
        <button class="btn ghost menu-btn" id="menu" aria-label="Open navigation">${icon("menu")}</button>
        <div class="crumbs hide-sm">GSC Agency · Lane A + Lane B</div>
        <div class="spacer"></div>
        ${FG.meta.storage_ephemeral ? `<span class="pill demo" title="No database is configured on this host (DATABASE_URL). Data is kept in temporary storage and is lost when the server instance restarts.">Temporary storage: data resets</span>` : ""}
        <span class="pill hide-sm" title="Business date used for pricing checks such as 'job date in the future'">${FG.meta.business_date_fixed ? "Business date" : "Today"} · ${dd(FG.meta.business_date)}</span>
        <span class="pill hide-sm">${esc(FG.user.display_name)} · ${esc(FG.user.role.replace("_", " ").toLowerCase())}</span>
        <button class="btn sm" id="logout">${icon("out", 14)} Sign out</button>
      </header>
      <main class="content" id="content" tabindex="-1"></main>
    </div>
  </div>
  <button class="ai-fab idle" id="aiFab" aria-label="${agent ? "Ask about my jobs" : "Open FlowGuard AI assistant"}" title="FlowGuard AI (local)">${icon("ai", 24)}</button><div id="aiPanel"></div>`;
  $("#logout").onclick = async () => { try { await POST("/api/auth/logout"); } catch (e) { } FG.user = null; renderLogin(); };
  $("#menu").onclick = () => $(".side").classList.toggle("open");
  FG.aiOpen = false;
  $("#aiFab").onclick = () => { $("#aiFab").classList.remove("idle"); toggleAI(); };
  if (!agent) refreshCounts();
}
async function refreshCounts() {
  try { const r = await GET("/api/reviews", { page_size: 1 }); const c = $("#reviewCount"); if (c) { c.textContent = r.total; c.hidden = !r.total; } } catch (e) { }
}

/* ---------------------------------------------------------------- login */
function renderLogin(msg) {
  document.body.innerHTML = `
  <div class="login">
    <section class="login-hero">
      <div><div class="row" style="gap:10px"><div class="mark" style="background:#fff;color:#0B1F3A">${icon("shield", 18)}</div><b style="font-size:16px;color:#fff">FlowGuard</b></div></div>
      <div>
        <h1>Every verified job, priced right or held for a person.</h1>
        <div class="flow" aria-label="Job-to-Cash flow">
          <div><i></i>Client sends job · agent completes · GSC admin verifies</div>
          <div class="k"><i></i>Lane A: contractual price and readiness checks</div>
          <div class="k"><i></i>Ready, blocked or needs review, never a guess</div>
          <div class="k"><i></i>Lane B: client invoice and agent invoice</div>
          <div class="k"><i></i>Reconciliation and Sage-ready export</div>
        </div>
        <p class="p">Deterministic rules calculate every pound. A local AI explains, searches and proposes, and has no authority over money.</p>
      </div>
      <p class="p">Job-to-Cash financial control: verified work to client invoice, agent pay and reconciliation.</p>
    </section>
    <section class="login-form">
      <div><h1 style="font-size:24px">Sign in</h1><p class="muted">Staff and agents use the same sign-in. You'll see the experience your account allows.</p></div>
      ${msg ? `<div class="note warn" style="max-width:380px">${esc(msg)}</div>` : ""}
      <form id="lf" autocomplete="on">
        <label class="f">Username<input type="text" name="username" autocomplete="username" required></label>
        <label class="f">Password<input type="password" name="password" autocomplete="current-password" required></label>
        <div id="lerr" class="note bad" hidden></div>
        <button class="btn primary" type="submit" style="justify-content:center">Sign in</button>
      </form>
    </section>
  </div>`;
  $$(".login-hero .flow div").forEach((d, i) => d.style.setProperty("--fi", i));
  $("#lf").onsubmit = async e => {
    e.preventDefault();
    const f = new FormData(e.target); const btn = e.target.querySelector("button[type=submit]"); btn.disabled = true;
    try {
      const r = await POST("/api/auth/login", { username: f.get("username"), password: f.get("password") });
      FG.user = r.user; FG.csrf = r.csrf_token; history.replaceState(null, "", "#/"); await afterLogin();
    } catch (err) { $("#lerr").hidden = false; $("#lerr").textContent = err.message; btn.disabled = false; }
  };
}
async function afterLogin() {
  AI_HIST.length = 0; FG.lastProposal = null;
  FG.meta = await GET("/api/meta");
  renderShell(); onRoute();
}

/* ---------------------------------------------------------------- AI assistant */
const AI_HIST = [];
function aiChips() {
  if (FG.user.role === "AGENT") return ["Why is my latest job under review?", "Show my invoices", "Change my job to Ready"];
  return ["Which jobs need review?", "Show blocked jobs", "Show the biggest sources of money at risk", "Where is the money?",
    "Show jobs held because of missing data", "Show invoices"];
}
function toggleAI(force) {
  FG.aiOpen = force ?? !FG.aiOpen;
  const p = $("#aiPanel");
  if (!FG.aiOpen) { const panel = $(".ai-panel", p); if (panel && !REDUCED) { panel.style.animation = "slide-in-r .18s var(--ease-in) reverse both"; setTimeout(() => { if (!FG.aiOpen) p.innerHTML = ""; }, 170); } else p.innerHTML = ""; return; }
  p.innerHTML = `<section class="ai-panel" aria-label="FlowGuard AI assistant">
    <div class="ai-head">${icon("ai", 18)}<b>FlowGuard AI</b><span class="pill local" id="aiMode">checking…</span><span class="spacer" style="flex:1"></span><button class="btn ghost sm" id="aiClose" aria-label="Close assistant">✕</button></div>
    <div class="ai-gov"><span>Local AI</span><span>External AI APIs disabled</span><span>Financial authority disabled</span><span>Direct DB access disabled</span></div>
    <div class="ai-log" id="aiLog" aria-live="polite"></div>
    <div class="ai-chips">${aiChips().map(c => `<button type="button">${esc(c)}</button>`).join("")}</div>
    <form class="ai-input" id="aiForm"><input type="text" id="aiIn" placeholder="${FG.user.role === "AGENT" ? "Ask about your jobs or invoices" : "Ask about jobs, invoices, money at risk or configuration"}" aria-label="Message"><button class="btn primary" type="submit">Send</button></form>
  </section>`;
  $("#aiClose").onclick = () => toggleAI(false);
  $$(".ai-chips button").forEach(b => b.onclick = () => aiSend(b.textContent));
  $("#aiForm").onsubmit = e => { e.preventDefault(); const v = $("#aiIn").value.trim(); if (v) aiSend(v); };
  if (!AI_HIST.length) AI_HIST.push({ who: "bot", html: FG.user.role === "AGENT" ? "Ask me why a job is under review or how an invoice was made up. I can only see your own jobs." : "I search and explain using FlowGuard's deterministic records. I can draft configuration proposals, but a person must approve them. I can't change money, statuses or controls." });
  drawAI();
  GET("/api/ai/status").then(s => { const m = $("#aiMode"); if (m) m.textContent = s.ollama.available ? `Ollama · ${s.ollama.model}` : "Ollama offline · structured mode"; }).catch(() => { });
  $("#aiIn").focus();
}
function drawAI() {
  const log = $("#aiLog"); if (!log) return;
  log.innerHTML = AI_HIST.map(m => `<div class="msg ${m.who}${m.cls ? " " + m.cls : ""}">${m.html}</div>`).join("");
  log.scrollTop = log.scrollHeight;
}
async function aiSend(text) {
  AI_HIST.push({ who: "me", html: esc(text) }); $("#aiIn") && ($("#aiIn").value = ""); drawAI();
  AI_HIST.push({ who: "bot", cls: "typing", html: "<span></span><span></span><span></span><span class='muted' style='width:auto;height:auto;background:none;animation:none'> Working</span>" }); drawAI();
  const { path } = parseHash();
  try {
    const r = await POST("/api/ai/chat", { message: text, page: { route: path, last_proposal_id: FG.lastProposal || "" } });
    AI_HIST.pop(); AI_HIST.push({ who: "bot", cls: r.type === "rejected" ? "rejected" : "", html: renderAI(r) });
    if (r.proposal_id) FG.lastProposal = r.proposal_id;
  } catch (e) { AI_HIST.pop(); AI_HIST.push({ who: "bot", cls: "rejected", html: esc(e.message) }); }
  drawAI();
}
function renderAI(r) {
  let h = r.type === "rejected" ? `<b>${esc(r.title || "Action rejected")}</b><br>` : "";
  h += `<div>${esc(r.text)}</div>`;
  if (r.type === "jobs" && r.rows?.length) {
    h += `<table class="t"><tbody>${r.rows.slice(0, 8).map(j => `<tr class="click" onclick="go('#/${FG.user.role === "AGENT" ? "my-jobs" : "jobs"}/${j.id}')"><td>${esc(j.ref)}</td><td>${esc(j.job_type || "–")}</td><td>${st(j.invoiced ? "INVOICED" : j.status)}</td></tr>`).join("")}</tbody></table>`;
    if (FG.user.role !== "AGENT") h += `<div class="row mt" style="margin-top:6px"><a href="#/jobs?${qs(Object.fromEntries(Object.entries(r.filters || {}).filter(([k]) => k !== "date_label")))}">Open all ${r.total} in Jobs</a></div>`;
    h += `<details class="meta"><summary>Structured query sent to the backend</summary><code>${esc(JSON.stringify(r.structured_query))}</code></details>`;
  }
  if (r.type === "job" && r.job) h += `<div class="row" style="margin-top:6px">${st(r.job.status)} <a href="#/${FG.user.role === "AGENT" ? "my-jobs" : "jobs"}/${r.job.id}">Open ${esc(r.job.ref)}</a></div>`;
  if (r.type === "invoice") h += `<div class="row" style="margin-top:6px"><span class="tag">${esc(r.reconstruction)}</span><a href="#/invoices/${r.invoice.id}">Open ${esc(r.invoice.number)}</a></div>`;
  if (r.type === "proposal") {
    const p = r.proposal, s = p.simulation || {};
    h += `<div class="note ai" style="margin-top:8px"><b>Proposed configuration</b><dl class="kv" style="margin-top:6px">
      <dt>Client</dt><dd>${esc(p.payload.client_code)}</dd>${p.payload.job_type ? `<dt>Job type</dt><dd>${esc(p.payload.job_type)}</dd>` : ""}
      ${p.payload.client_amount ? `<dt>Rate</dt><dd>${gbp(p.payload.client_amount)}</dd>` : ""}<dt>Effective</dt><dd>${dd(p.payload.effective_from)}</dd>
      ${(p.payload.modifiers || []).map(m => `<dt>${esc(m.condition.toLowerCase())} modifier</dt><dd>+${gbp(m.client_amount)}</dd>`).join("")}
      ${p.payload.po_required !== null && p.payload.po_required !== undefined ? `<dt>PO</dt><dd>${p.payload.po_required ? "Required" : "Not required"}</dd>` : ""}
      <dt>Impact</dt><dd>${num(s.affected_jobs)} jobs · ${gbp(s.billing_difference)} · ${num(s.ready_to_blocked)} would become blocked</dd>
      <dt>Risk</dt><dd>${esc(p.risk)}${p.risk_reasons?.length ? " – " + esc(p.risk_reasons.join(", ")) : ""}</dd></dl>
      <p style="margin-top:8px"><b>This configuration has NOT been activated.</b></p><a href="#/configuration/${p.id}">Review proposal #${p.id}</a></div>`;
  }
  if (r.type === "simulation") h += `<div class="row" style="margin-top:6px"><a href="#/configuration/${r.proposal_id}">Open proposal #${r.proposal_id}</a></div>`;
  if (r.type === "leaks") h += `<div class="row" style="margin-top:6px"><a href="#/">See the money-at-risk panel</a></div>`;
  if (r.link) h += `<div class="row" style="margin-top:6px"><a href="${r.link}">Open</a></div>`;
  const ex = r.mode_explain ? (r.mode_explain === "ollama" ? "wording by local model" : "deterministic template") : "";
  h += `<div class="meta">Tools: ${esc((r.tools || []).join(", ") || "none")} · ${esc(r.mode === "ollama" ? "Ollama" : "structured parser")}${ex ? " · " + ex : ""} · financial changes: none</div>`;
  return h;
}

/* ---------------------------------------------------------------- shared components */
function kpi(label, value, sub, href, cls = "") {
  return `<button class="kpi ${cls}" onclick="go('${href}')"><div class="l">${label}</div><div class="v">${value}</div>${sub ? `<div class="s">${sub}</div>` : ""}</button>`;
}
function table(cols, rows, { onRow, empty = "Nothing to show", sort, dir } = {}) {
  if (!rows.length) return `<div class="empty"><b>${esc(empty)}</b></div>`;
  return `<div class="tbl-wrap"><table class="t"><thead><tr>${cols.map(c => `<th class="${c.num ? "num " : ""}${c.sort ? "sortable" : ""}" ${c.sort ? `data-sort="${c.sort}"` : ""} scope="col">${esc(c.h)}${c.sort && sort === c.sort ? (dir === "asc" ? " ↑" : " ↓") : ""}</th>`).join("")}</tr></thead>
  <tbody>${rows.map((r, i) => `<tr ${onRow ? `class="click" data-i="${i}" tabindex="0"` : ""}>${cols.map(c => `<td class="${c.num ? "num" : ""} ${c.nowrap ? "nowrap" : ""}">${c.v(r)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
}
function bindRows(el, rows, fn) {
  $$("tbody tr.click", el).forEach(tr => {
    const h = () => fn(rows[+tr.dataset.i]);
    tr.onclick = e => { if (!e.target.closest("a,button")) h(); };
    tr.onkeydown = e => { if (e.key === "Enter") h(); };
  });
}
function head(title, sub, actions = "") {
  return `<div class="page-head"><div><h1>${esc(title)}</h1>${sub ? `<div class="sub">${sub}</div>` : ""}</div>${actions ? `<div class="actions">${actions}</div>` : ""}</div>`;
}
const charts = [];
function chart(canvas, cfg) {
  if (!window.Chart) { canvas.replaceWith(Object.assign(document.createElement("div"), { className: "empty", textContent: "Chart library unavailable; figures are listed alongside." })); return; }
  const css = getComputedStyle(document.documentElement);
  Chart.defaults.font.family = css.getPropertyValue("--font"); Chart.defaults.color = css.getPropertyValue("--muted").trim();
  Chart.defaults.borderColor = css.getPropertyValue("--line").trim();
  Chart.defaults.animation = REDUCED ? false : { duration: 800, easing: "easeOutQuart" };
  charts.push(new Chart(canvas, cfg));
}
const cssv = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();

/* ---------------------------------------------------------------- boot */
async function start() {
  try { const r = await GET("/api/auth/me"); FG.user = r.user; FG.csrf = r.csrf_token; await afterLogin(); }
  catch (e) { renderLogin(); }
}
