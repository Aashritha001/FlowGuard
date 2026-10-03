/* Pages part 3: imports + shadow mode, integrations, audit, AI & automation, notifications, settings, agent portal */
"use strict";
route("/imports", async (c, _, q) => {
  const r = await GET("/api/imports");
  const can = ["SUPER_ADMIN", "FINANCE_ADMIN"].includes(FG.user.role);
  c.innerHTML = head("Imports and Shadow Mode", "Uploaded files are untrusted: they are quarantined, mapped, validated and previewed. Nothing invalid is imported silently. Shadow Mode compares FlowGuard's decisions with your existing process and never sends invoices or writes to source systems.") + `
  <div class="card mb"><div class="steps">${["Upload", "Quarantine", "Column mapping", "Schema validation", "Preview", "Import summary", "Shadow Mode"].map(s => `<span class="s">${s}</span>`).join("")}</div>
    <p class="muted" style="margin-top:10px">Migration path: historical CSV → validate → read-only live integration → shadow mode → controlled pilot → production.</p></div>
  ${can ? `<div class="card mb"><h2>Import a GSC data pack (.xlsx or .csv)</h2><p class="muted">The workbook with the <b>Jobs</b>, <b>Agent_Tasks</b>, <b>Agents</b> and <b>Rates_and_Details</b> sheets, or its single-file CSV form (made with <code>tools/workbook_to_csv.py</code>). Safe to import again with a later file: new tasks are added, changed tasks updated, and invoiced tasks are never altered. The first import creates the rate cards; later imports report rate differences instead of changing prices.</p>
    <div class="row"><input type="file" id="wbFile" accept=".xlsx,.csv,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" aria-label="Data pack file"><button class="btn primary" id="wbUp">Import data pack</button></div><div id="wbErr"></div></div>
  <div class="card mb"><h2>Upload a CSV of jobs</h2><div class="row"><input type="file" id="file" accept=".csv,text/csv" aria-label="CSV file"><button class="btn" id="up">Upload to quarantine</button></div>
    <p class="faint" style="font-size:12px;margin-top:8px">Limits: ${esc(r.limits.extensions.join(", "))} only, up to ${Math.round(r.limits.max_bytes / 1024)} KB and ${num(r.limits.max_rows)} rows.</p><div id="uerr"></div></div>` : ""}
  <div class="card flush">${table([{ h: "#", v: b => b.id }, { h: "File", v: b => `<b>${esc(b.filename)}</b>` }, { h: "Status", v: b => `<span class="tag">${esc(b.status.toLowerCase().replace(/_/g, " "))}</span>` }, { h: "Kind", v: b => b.summary.kind === "WORKBOOK" ? "Workbook" : "CSV" }, { h: "Rows / tasks", num: 1, v: b => b.summary.kind === "WORKBOOK" ? `${num(b.summary.tasks_added)} new · ${num(b.summary.tasks_updated)} updated` : num(b.summary.rows) }, { h: "Valid", num: 1, v: b => b.summary.valid ?? "–" }, { h: "Invalid", num: 1, v: b => b.summary.invalid ?? "–" }, { h: "By", v: b => esc(b.uploaded_by) }, { h: "When", v: b => dt(b.created_at) }], r.rows, { onRow: 1, empty: "No imports yet. Import your GSC workbook to start." })}</div>`;
  bindRows(c, r.rows, b => go("#/imports/" + b.id));
  if (!can) return;
  const send = async (filename, content) => { try { const b = await POST("/api/imports", { filename, content }); go("#/imports/" + b.id); } catch (e) { $("#uerr").innerHTML = errBox(e); } };
  $("#up").onclick = () => { const f = $("#file").files[0]; if (!f) return toast("Choose a CSV file first", true); const rd = new FileReader(); rd.onload = () => send(f.name, rd.result); rd.readAsText(f); };
  $("#wbUp").onclick = () => {
    const f = $("#wbFile").files[0]; if (!f) return toast("Choose the data pack (.xlsx or .csv) first", true);
    const btn = $("#wbUp"); btn.disabled = true; btn.textContent = "Importing…"; $("#wbErr").innerHTML = "";
    const rd = new FileReader();
    rd.onload = async () => {
      try {
        const s = await POST("/api/imports/workbook", { filename: f.name, content_b64: String(rd.result).split(",")[1] || "" });
        FG.meta = await GET("/api/meta"); refreshCounts();
        toast(`Imported ${num(s.tasks_added)} new tasks, updated ${num(s.tasks_updated)}.`); go("#/imports/" + s.batch_id);
      } catch (e) { $("#wbErr").innerHTML = errBox(e); btn.disabled = false; btn.textContent = "Import data pack"; }
    };
    rd.readAsDataURL(f);
  };
});

route("/imports/:id", async (c, { id }) => {
  const [b, meta] = await Promise.all([GET("/api/imports/" + id), GET("/api/imports")]);
  if (b.summary.kind === "WORKBOOK") return workbookView(c, b);
  const fields = meta.fields;
  const s = b.summary, sh = b.shadow_result || {};
  const can = ["SUPER_ADMIN", "FINANCE_ADMIN"].includes(FG.user.role);
  const stepOn = { QUARANTINED: 2, VALIDATED: 5, SHADOW_COMPLETE: 6, IMPORTED_LIVE: 6 }[b.status] ?? 1;
  c.innerHTML = head(`Import #${b.id}: ${b.filename}`, `Status: ${esc(b.status.toLowerCase().replace(/_/g, " "))} · ${num(s.rows)} rows`) + `
  <div class="card mb"><div class="steps">${["Upload", "Quarantine", "Column mapping", "Schema validation", "Preview", "Import summary", "Shadow Mode"].map((x, i) => `<span class="s ${i < stepOn ? "done" : i === stepOn ? "on" : ""}">${x}</span>`).join("")}</div></div>
  <div class="grid g2">
    <div class="card"><div class="card-head"><h2>Column mapping</h2>${b.mapping._template ? `<span class="tag r">template: ${esc(b.mapping._template)}</span>` : ""}</div>
      <p class="muted">Source field names are mapped to FlowGuard's normalised schema; business logic never sees source names.</p>
      <form id="mf"><table class="t"><thead><tr><th>FlowGuard field</th><th>Source column</th></tr></thead><tbody>${Object.entries(fields).map(([f, d]) => `<tr><td>${d.label ? `<b>${esc(d.label)}</b> <span class="tag">price factor</span>` : code(f)} ${d.required ? `<span class="tag">required</span>` : ""}</td><td><select name="${f}" ${can ? "" : "disabled"}><option value="">(not mapped)</option>${b.headers.map(h => `<option ${b.mapping[f] === h ? "selected" : ""}>${esc(h)}</option>`).join("")}</select></td></tr>`).join("")}</tbody></table>
      ${can ? `<div class="row mt"><label class="f" style="flex:1">Save as template (optional)<input type="text" name="_save" placeholder="e.g. GSC legacy export v2"></label><button class="btn primary" type="submit">Validate</button></div>` : ""}<div id="merr"></div></form></div>
    <div class="stack">
      <div class="card"><h2>Validation summary</h2>${s.valid !== undefined ? `<div class="grid g4">${[["Valid", s.valid, "ready"], ["Invalid", s.invalid, s.invalid ? "blocked" : "ready"], ["Duplicate IDs", s.duplicate_ids, s.duplicate_ids ? "blocked" : "ready"], ["Missing fields", s.missing_fields, s.missing_fields ? "blocked" : "ready"], ["Unknown clients", s.unknown_clients], ["Unknown agents", s.unknown_agents], ["Invalid dates", s.invalid_dates], ["Already in FlowGuard", s.already_in_flowguard]].map(([l, v, col]) => `<div><div class="muted" style="font-size:12px">${l}</div><div style="font-size:20px;font-weight:650;${col ? `color:var(--${col})` : ""}">${num(v)}</div></div>`).join("")}</div>
        <div class="row mt"><button class="btn sm" id="errs">Download invalid rows</button>${can ? `<button class="btn primary" id="shadow">Run Shadow Mode on ${num(s.valid)} valid rows</button><button class="btn" id="live" title="Adds valid rows as live jobs; Lane A prices them like any source">Import valid rows as live jobs</button>` : ""}</div>` : `<p class="muted">Map the columns and validate. Invalid rows stay in quarantine with their errors.</p>`}</div>
      ${sh.compared !== undefined ? `<div class="card"><div class="card-head"><h2>Shadow Mode result</h2><span class="tag r">Invoices sent: ${sh.invoices_sent} · source writes: ${sh.source_writes}</span></div>
        <div class="grid g3">${[["Jobs compared", num(sh.compared)], ["Matching decisions", num(sh.matching_decisions)], ["Differences", num(sh.differences)], ["Straight-through rate", sh.straight_through_rate + "%"], ["Potential wrong outputs avoided", num(sh.potential_wrong_outputs)], ["Financial difference", gbp(sh.financial_difference)]].map(([l, v]) => `<div><div class="muted" style="font-size:12px">${l}</div><div style="font-size:20px;font-weight:650">${v}</div></div>`).join("")}</div>
        <p class="muted mt">Legacy billed ${gbp(sh.legacy_total)}; FlowGuard would release ${gbp(sh.flowguard_total)} as READY. "Potential wrong outputs" are jobs the existing process billed that FlowGuard would have held for a deterministic reason.</p>
        <h3>Exception differences</h3><div class="row">${Object.entries(sh.exception_codes).map(([k, v]) => `${code(k)} <b>${v}</b>`).join(" &nbsp; ")}</div>
        ${sh.examples?.length ? `<h3 class="mt">Examples</h3>${table([{ h: "Row", v: e => e.row }, { h: "Job", v: e => esc(e.external_id) }, { h: "Legacy", v: e => `${esc(e.legacy)} ${gbp(e.legacy_amount)}` }, { h: "FlowGuard", v: e => `${st(e.flowguard)} ${gbp(e.flowguard_amount)}` }, { h: "Codes", v: e => e.codes.map(code).join(" ") }], sh.examples)}` : ""}</div>` : ""}
    </div>
  </div>
  <div class="card flush mt"><div class="card-head" style="padding:14px 16px 0"><h2>Preview (first ${b.preview.length} rows)</h2></div>${table([{ h: "Row", v: r => r.row }, { h: "Status", v: r => r.status === "VALID" ? `<span class="st READY">Valid</span>` : r.status === "INVALID" ? `<span class="st BLOCKED">Invalid</span>` : `<span class="st neutral">Pending</span>` }, { h: "Errors", v: r => esc(r.errors.join("; ")) }, ...b.headers.slice(0, 7).map(h => ({ h, v: r => esc(r.raw[h]) })), { h: "Shadow", v: r => r.shadow ? st(r.shadow.status) : "" }], b.preview)}</div>`;
  if (can) $("#mf").onsubmit = async e => { e.preventDefault(); const f = Object.fromEntries(new FormData(e.target)); const save = f._save; delete f._save; try { await POST(`/api/imports/${id}/map`, { mapping: f, save_as: save || null }); onRoute(); } catch (err) { $("#merr").innerHTML = errBox(err); } };
  $("#errs") && ($("#errs").onclick = async () => { const f = await api("GET", `/api/imports/${id}/errors.csv`); download(f.text, f.filename); });
  $("#shadow") && ($("#shadow").onclick = async () => { $("#shadow").disabled = true; $("#shadow").textContent = "Running…"; await POST(`/api/imports/${id}/shadow`); onRoute(); });
  $("#live") && ($("#live").onclick = async () => { if (!confirm("Import valid rows as live jobs? They will be priced by Lane A and can then be invoiced if READY.")) return; const r = await POST(`/api/imports/${id}/live`); toast(`${r.imported} jobs imported: ${r.counts.READY} ready, ${r.counts.NEEDS_REVIEW} review, ${r.counts.BLOCKED} blocked`); onRoute(); });
});

route("/integrations", async (c) => {
  const r = await GET("/api/integrations");
  c.innerHTML = head("Integrations", "Adapters feed a normalised internal job schema so business logic never depends on one source. Workbook and CSV upload are working; the rest are integration-ready interfaces.") + `
  <div class="grid g2"><div class="card flush">${table([{ h: "Source", v: x => `<b>${esc(x.name)}</b><div class="faint" style="font-size:12px">${esc(x.description)}</div>` }, { h: "Kind", v: x => code(x.kind) }, { h: "Status", v: x => x.status.startsWith("WORKING") ? `<span class="st READY">${esc(x.status.toLowerCase())}</span>` : `<span class="st neutral">${esc(x.status.toLowerCase().replace(/_/g, " "))}</span>` }, { h: "Access", v: x => esc(x.access_mode.toLowerCase().replace("_", " ")) }, { h: "Last sync", v: x => dt(x.last_sync_at) }], r.rows)}</div>
  <div class="card"><h2>Connection principles</h2><ul>${r.principles.map(p => `<li style="margin-bottom:6px">${esc(p)}</li>`).join("")}</ul>
    <h3 class="mt">Data FlowGuard keeps</h3><p class="muted">Only fields needed for pricing, readiness, invoicing, review, audit and traceability: job IDs, client, agent, type, date, verification status and time, conditions, PO, postcode. Photos, reports, unrelated customer data and credentials are not ingested.</p></div></div>`;
});

route("/audit", async (c, _, q) => {
  const r = await GET("/api/audit", q);
  c.innerHTML = head("Audit Trail", "Append-only: the database rejects any update or delete of these rows. Passwords, tokens, credentials and bank details are never logged.") + `
  <div class="card flush"><form class="filters" id="af"><label class="f">Action<select name="action"><option value="">Any</option>${r.actions.map(a => `<option ${q.action === a ? "selected" : ""}>${esc(a)}</option>`).join("")}</select></label>
    <label class="f">User<input type="text" name="actor" value="${esc(q.actor || "")}"></label><label class="f">Search reason / ID<input type="search" name="q" value="${esc(q.q || "")}"></label><button class="btn primary">Apply</button><a class="btn ghost" href="#/audit">Clear</a><span class="tag">append-only</span></form>
  ${table([{ h: "When", nowrap: 1, v: a => dt(a.ts) }, { h: "User", v: a => `${esc(a.actor)}<div class="faint" style="font-size:11.5px">${esc(a.role.toLowerCase())}</div>` }, { h: "Action", v: a => code(a.action) }, { h: "Entity", v: a => a.entity === "job" ? `<a href="#/jobs/${a.entity_id}">job ${esc(a.entity_id)}</a>` : esc(`${a.entity} ${a.entity_id}`) },
    { h: "Old → new", v: a => a.old || a.new ? `${esc(a.old)} → <b>${esc(a.new)}</b>` : "" }, { h: "Reason", v: a => `<span style="font-size:12.5px">${esc(a.reason)}</span>` }, { h: "Config version", v: a => esc(a.config_version) }, { h: "AI involvement", v: a => a.ai !== "None" ? `<span class="tag ai">${esc(a.ai)}</span>` : `<span class="faint">none</span>` }], r.rows)}
  <div class="pager"><span>${num(r.total)} events</span><span style="flex:1"></span><button class="btn sm" ${r.page <= 1 ? "disabled" : ""} id="pp">Newer</button><button class="btn sm" ${r.page * 50 >= r.total ? "disabled" : ""} id="np">Older</button></div></div>`;
  $("#af").onsubmit = e => { e.preventDefault(); go("#/audit?" + qs(Object.fromEntries(new FormData(e.target)))); };
  $("#pp").onclick = () => go("#/audit?" + qs({ ...q, page: r.page - 1 })); $("#np").onclick = () => go("#/audit?" + qs({ ...q, page: r.page + 1 }));
});

route("/ai", async (c) => {
  const [s, a, log] = await Promise.all([GET("/api/ai/status"), GET("/api/automation"), GET("/api/ai/log").catch(() => ({ rows: [] }))]);
  const can = ["SUPER_ADMIN", "FINANCE_ADMIN"].includes(FG.user.role);
  c.innerHTML = head("AI and Automation", "AI sits beside the financial path, never inside it. It explains, searches and proposes through an allow-listed tool gateway. Learned patterns become explicit, versioned rules only after human approval.",
    `<button class="btn" id="an">Re-analyse review outcomes</button>`) + `
  <div class="grid g3">
    <div class="card"><h2>Local model</h2><dl class="kv"><dt>Ollama</dt><dd>${s.ollama.available ? `<span class="st READY">Connected</span>` : `<span class="st neutral">Unavailable</span>`}</dd><dt>Base URL</dt><dd>${esc(s.base_url)}</dd><dt>Model</dt><dd>${esc(s.ollama.model || s.configured_model)}</dd></dl>
      ${s.ollama.available ? "" : `<div class="note mt">Financial processing is unaffected. Explanations use deterministic templates and the assistant uses the built-in structured parser.${s.embedded_preview ? " (A browser preview cannot reach Ollama.)" : ""}</div>`}</div>
    <div class="card"><h2>Governance</h2><dl class="kv"><dt>Local AI</dt><dd>Yes</dd><dt>External AI APIs</dt><dd>Disabled</dd><dt>AI financial authority</dt><dd>Disabled</dd><dt>AI direct database access</dt><dd>Disabled</dd><dt>AI can activate config</dt><dd>No</dd><dt>Untrusted data as instructions</dt><dd>Never</dd></dl></div>
    <div class="card"><h2>Automation maturity</h2>${a.maturity.map(m => `<div class="row" style="padding:5px 0;border-bottom:1px solid var(--line)"><b style="width:56px">Level ${esc(m.level)}</b><span style="flex:1">${esc(m.name)}${m.note ? `<div class="faint" style="font-size:12px">${esc(m.note)}</div>` : ""}</span>${m.state === "never" ? `<span class="st BLOCKED">Never</span>` : `<span class="st READY">Enabled</span>`}</div>`).join("")}</div>
  </div>
  <div class="card mt"><h2>Automation opportunities</h2><p class="muted">Learned only from structured human review outcomes. Threshold: at least ${a.thresholds.min_sample} reviews with ${a.thresholds.min_agreement * 100}% agreement. The AI never activates a rule.</p>
    ${a.rows.length ? a.rows.map(o => `<div class="reason"><div class="row"><span class="tag ai">AUTOMATION OPPORTUNITY</span><b>${esc(o.pattern.replace("+", " + ").toLowerCase())} → ${esc(o.resolution.replace(">", " supersedes ").toLowerCase())}</b><span class="tag">${esc(o.status.toLowerCase().replace(/_/g, " "))}</span></div>
      <div class="grid g4 mt"><div><div class="muted" style="font-size:12px">Similar exceptions reviewed</div><b style="font-size:18px">${o.sample_size}</b></div><div><div class="muted" style="font-size:12px">Decisions agreeing</div><b style="font-size:18px">${o.agreement}/${o.sample_size}</b></div><div><div class="muted" style="font-size:12px">Est. exceptions avoided</div><b style="font-size:18px">${o.est_monthly_avoided}/month</b></div><div><div class="muted" style="font-size:12px">Straight-through rate</div><b style="font-size:18px">${o.stp_before}% → ${o.stp_after}%</b></div></div>
      <div class="note mt"><b>Proposed deterministic rule</b><br><code>${esc(o.proposed_rule.text)}</code><br><span class="muted">${o.open_matching} open exceptions match this pattern right now.</span></div>
      <div class="row mt"><button class="btn sm" data-cases="${o.id}">Inspect cases</button>${can && o.status === "SUGGESTED" ? `<button class="btn sm danger" data-rej="${o.id}">Reject suggestion</button><button class="btn sm primary" data-draft="${o.id}">Create draft rule</button>` : ""}${o.proposal_id ? `<a class="btn sm" href="#/configuration/${o.proposal_id}">Open draft rule #${o.proposal_id}</a>` : ""}</div><div id="cases${o.id}"></div></div>`).join("") : `<div class="empty">No pattern meets the threshold yet.</div>`}</div>
  <div class="card flush mt"><div class="card-head" style="padding:14px 16px 0"><h2>AI interaction log</h2><span class="muted r">Raw prompts are not retained: only a hash and a short preview.</span></div>
    ${table([{ h: "ID", v: x => code(x.id) }, { h: "When", nowrap: 1, v: x => dt(x.ts) }, { h: "User", v: x => esc(x.actor) }, { h: "Request", v: x => `${code(x.request_type)}<div class="faint" style="font-size:12px">${esc(x.prompt_preview)}</div>` }, { h: "Tools", v: x => esc(x.tools.join(", ") || "–") }, { h: "Records", v: x => `<span style="font-size:12px">${esc(x.records.slice(0, 4).join(", "))}${x.records.length > 4 ? "…" : ""}</span>` }, { h: "Model", v: x => esc(x.model) }, { h: "Outcome", v: x => x.outcome === "REJECTED" || x.outcome === "DENIED" ? `<span class="st BLOCKED">${esc(x.outcome.toLowerCase())}</span>` : esc(x.outcome.toLowerCase().replace(/_/g, " ")) }, { h: "External", v: x => esc(x.external_transmission) }, { h: "Financial changes", v: x => `<b>${esc(x.financial_changes)}</b>` }], log.rows, { empty: "No AI interactions yet. Open the assistant to start." })}</div>`;
  $("#an").onclick = async () => { await POST("/api/automation/analyse"); onRoute(); };
  $$("[data-cases]", c).forEach(b => b.onclick = async () => { const r = await GET(`/api/automation/${b.dataset.cases}/cases`); $("#cases" + b.dataset.cases).innerHTML = `<div class="mt">${table([{ h: "Review", v: x => "#" + x.id }, { h: "Job", v: x => `<a href="#/jobs/${x.job_id}">${esc(x.job_ref)}</a>` }, { h: "Reviewer", v: x => esc(x.reviewer) }, { h: "Decision", v: x => code(x.resolution) }, { h: "Reason", v: x => esc(x.reason) }, { h: "Evidence", v: x => esc(x.evidence) }, { h: "When", v: x => dt(x.at) }], r.rows.slice(0, 50))}</div>`; });
  $$("[data-rej]", c).forEach(b => b.onclick = async () => { await POST(`/api/automation/${b.dataset.rej}/reject`, { reason: "Rejected from AI & Automation page" }); onRoute(); });
  $$("[data-draft]", c).forEach(b => b.onclick = async () => { try { const p = await POST(`/api/automation/${b.dataset.draft}/draft`); toast("Draft rule created. It needs four-eyes approval before it does anything."); go("#/configuration/" + p.id); } catch (e) { toast(e.message, true); } });
});

route("/notifications", async (c) => {
  const r = await GET("/api/notifications");
  c.innerHTML = head("Notifications", FG.user.role === "AGENT" ? "Updates about your jobs and invoices." : "System events. Items needing a human financial decision are in the Review Centre, not here.", `<button class="btn" id="mr">Mark all as read</button>`) +
    `<div class="card flush">${table([{ h: "", v: n => n.read ? "" : `<span class="st INVOICED" title="unread">new</span>` }, { h: "When", nowrap: 1, v: n => dt(n.ts) }, { h: "Type", v: n => `<span class="tag">${esc(n.kind.toLowerCase().replace(/_/g, " "))}</span>` }, { h: "Notification", v: n => `<b>${esc(n.title)}</b><div class="muted" style="font-size:12.5px">${esc(n.body)}</div>` }], r.rows, { empty: "No notifications" })}</div>`;
  $("#mr").onclick = async () => { await POST("/api/notifications/read"); onRoute(); };
});

route("/settings", async (c) => {
  const isSA = FG.user.role === "SUPER_ADMIN";
  const [rb, obs, users] = await Promise.all([GET("/api/rbac").catch(() => null), GET("/api/observability").catch(() => null), isSA ? GET("/api/users") : null]);
  c.innerHTML = head("Settings", "Users, permissions, governance policy, observability and the production path.") + `
  <div class="grid g2">
    <div class="card"><h2>Your account</h2><dl class="kv"><dt>User</dt><dd>${esc(FG.user.display_name)} (${esc(FG.user.username)})</dd><dt>Role</dt><dd>${esc(FG.user.role)}</dd><dt>MFA</dt><dd>${FG.user.mfa_enrolled ? "Enrolled" : "Not enrolled (MFA-ready: TOTP provider integration is a P3 item)"}</dd>
      <dt>Session</dt><dd>HttpOnly, SameSite=Strict cookie + CSRF token</dd></dl>
      <h3 class="mt">Change password</h3>${pwForm()}
      ${isSA ? `<h3 class="mt">Four-eyes policy</h3><div class="row"><select id="fe"><option value="ALL">Every change needs a second approver</option><option value="HIGH_RISK_ONLY">Only high-risk changes need a second approver</option></select><button class="btn" id="feSave">Save</button></div>` : ""}</div>
    ${obs ? `<div class="card"><h2>Observability</h2><dl class="kv"><dt>Processing errors</dt><dd>${obs.processing_errors}</dd><dt>Failed import rows</dt><dd>${obs.failed_imports}</dd><dt>AI failures</dt><dd>${obs.ai_failures}</dd><dt>AI requests rejected</dt><dd>${obs.ai_rejections}</dd><dt>Access denied</dt><dd>${obs.access_denied}</dd><dt>Failed sign-ins</dt><dd>${obs.failed_logins}</dd><dt>Rule conflicts detected</dt><dd>${obs.rule_conflicts}</dd><dt>Reconciliation mismatches</dt><dd>${obs.reconciliation_mismatches}</dd><dt>Integration failures</dt><dd>${obs.integration_failures}</dd></dl></div>` : ""}
  </div>
  ${["SUPER_ADMIN", "FINANCE_ADMIN"].includes(FG.user.role) ? businessDateCard() : ""}
  ${users ? `<div class="card flush mt"><div class="card-head" style="padding:14px 16px 0"><h2>Users</h2><button class="btn primary sm r" id="addUser">+ Add user</button></div>${table([{ h: "User", v: u => `<b>${esc(u.username)}</b>` }, { h: "Name", v: u => esc(u.display_name) }, { h: "Role", v: u => code(u.role) }, { h: "Agent", v: u => esc(u.agent || "–") }, { h: "Active", v: u => u.active ? `<span class="st READY">Active</span>` : `<span class="st neutral">Inactive</span>` }, { h: "", v: u => `<div class="row" style="flex-wrap:nowrap"><button class="btn sm" data-pw="${esc(u.username)}">Reset password</button>${u.username === FG.user.username ? "" : `<button class="btn sm ${u.active ? "danger" : ""}" data-act="${esc(u.username)}" data-on="${u.active ? 0 : 1}">${u.active ? "Deactivate" : "Activate"}</button>`}</div>` }], users.rows)}</div>` : ""}
  ${rb ? `<div class="card flush mt"><div class="card-head" style="padding:14px 16px 0"><h2>RBAC permission matrix</h2><span class="muted r">Enforced server-side on every request</span></div>${table([{ h: "Permission", v: p => code(p.p) }, ...rb.roles.map(r => ({ h: r.replace("_", " ").toLowerCase(), v: p => p.m[r] ? "✓" : `<span class="faint">–</span>` }))], Object.entries(rb.matrix).map(([p, m]) => ({ p, m })))}</div>` : ""}
  <div class="grid g2 mt"><div class="card"><h2>Production roadmap</h2>${FG.meta.roadmap.map(p => `<div class="row" style="padding:6px 0;border-bottom:1px solid var(--line)"><b style="width:70px">Phase ${p.phase}</b><span style="flex:1">${esc(p.name)}</span><span class="tag">${esc(p.state)}</span></div>`).join("")}<p class="muted mt">See the README for what production still needs (HTTPS, MFA, managed secrets, backups).</p></div></div>`;

  $("#feSave") && ($("#feSave").onclick = async () => { await api("PUT", "/api/settings/four-eyes", { body: { mode: $("#fe").value } }); toast("Four-eyes policy saved and audited"); });
  bindPwForm(c); bindBusinessDate(c);
  $("#addUser") && ($("#addUser").onclick = () => userModal());
  $$("[data-pw]", c).forEach(b => b.onclick = () => resetPwModal(b.dataset.pw));
  $$("[data-act]", c).forEach(b => b.onclick = async () => { try { await POST(`/api/users/${b.dataset.act}/active`, { active: b.dataset.on === "1" }); toast("Saved"); onRoute(); } catch (e) { toast(e.message, true); } });
});

/* ---------------------------------------------------------------- agent portal */
const AGENT_STATUS = { READY: "Ready for invoicing", NEEDS_REVIEW: "Under review", BLOCKED: "On hold" };
const agentSt = r => r.invoiced ? st("INVOICED", "Invoiced") : st(r.status, AGENT_STATUS[r.status]);
async function agentHome(c) {
  const d = await GET("/api/agent/dashboard");
  c.innerHTML = head(`Hello, ${FG.user.agent?.name || FG.user.display_name}`, "Your jobs, their status and your invoices. You only ever see your own records.") + `
  <div class="grid g4">${kpi("Total jobs", num(d.total), "", "#/my-jobs")}${kpi("Ready for invoicing", num(d.ready), gbp(d.awaiting_value) + " to be invoiced", "#/my-jobs?status=READY")}${kpi("Under review", num(d.review), "Finance is checking these", "#/my-jobs?status=NEEDS_REVIEW")}${kpi("Invoiced", num(d.invoiced), "", "#/my-invoices")}</div>
  ${d.blocked ? `<div class="note warn mt">${d.blocked} job(s) are on hold. Open them to see why.</div>` : ""}
  <div class="grid g2 mt"><div class="card flush"><div class="card-head" style="padding:14px 16px 0"><h2>Recent jobs</h2><a class="r" href="#/my-jobs">All jobs</a></div><div id="rj"></div></div>
  <div class="card flush"><div class="card-head" style="padding:14px 16px 0"><h2>Invoices</h2><a class="r" href="#/my-invoices">All invoices</a></div>${table([{ h: "Invoice", v: x => `<a href="#/my-invoices/${x.id}">${esc(x.number)}</a>` }, { h: "Date", v: x => dd(x.issue_date) }, { h: "Total", num: 1, v: x => gbp(x.gross) }], d.invoices, { empty: "No invoices yet" })}</div></div>`;
  $("#rj").innerHTML = agentJobTable(d.recent); bindRows($("#rj"), d.recent, r => go("#/my-jobs/" + r.id));
}
const agentJobTable = rows => table([{ h: "Job", v: r => `<b>${esc(r.external_job_id)}</b>` }, { h: "Type", v: r => esc(r.job_type || "–") }, { h: "Date", nowrap: 1, v: r => dd(r.job_date) }, { h: "Status", v: agentSt }, { h: "Your pay", num: 1, v: r => gbp(r.agent_net) }, { h: "Invoice", v: r => esc(r.agent_invoice || "–") }], rows, { onRow: 1, empty: "No jobs" });
route("/my-jobs", async (c, _, q) => {
  const r = await GET("/api/jobs", { page_size: 100, ...q });
  c.innerHTML = head("My Jobs", `${num(r.total)} jobs`) + `<div class="card flush"><div class="filters">${["", "READY", "NEEDS_REVIEW", "BLOCKED"].map(s => `<a class="btn sm ${(q.status || "") === s ? "primary" : ""}" href="#/my-jobs${s ? "?status=" + s : ""}">${s ? AGENT_STATUS[s] : "All"}</a>`).join("")}</div><div id="mj"></div></div>`;
  $("#mj").innerHTML = agentJobTable(r.rows); bindRows($("#mj"), r.rows, x => go("#/my-jobs/" + x.id));
});
route("/my-jobs/:id", async (c, { id }) => {
  const j = await GET("/api/jobs/" + id);
  c.innerHTML = head(j.external_job_id, `${esc(j.job_type || "")} · ${dd(j.job_date)}`) + `
  <div class="card"><div class="row">${agentSt(j)}</div><div class="stack mt">${j.explanations.map(e => `<div class="note ${j.status === "READY" ? "good" : "warn"}" style="font-size:15px">${esc(e)}</div>`).join("")}</div>
    <dl class="kv mt"><dt>Client</dt><dd>${esc(j.client)}</dd><dt>Date</dt><dd>${dd(j.job_date)}</dd><dt>Conditions</dt><dd>${[j.weekend && "weekend", j.emergency && "emergency", j.revisit && "revisit"].filter(Boolean).join(", ") || "none"}</dd><dt>Your pay (net)</dt><dd>${gbp(j.agent_net)}</dd>
    <dt>Invoice</dt><dd>${j.my_invoice ? `<a href="#/my-invoices/${j.my_invoice.id}">${esc(j.my_invoice.number)}</a> · ${dd(j.my_invoice.issue_date)}` : j.status === "READY" ? "Will appear on your next invoice" : "No invoice has been generated yet"}</dd></dl>
    <button class="btn mt" id="ask">${icon("ai", 14)} Ask about this job</button></div>`;
  $("#ask").onclick = () => { toggleAI(true); aiSend(`Why is job ${j.id} ${j.status === "READY" ? "ready" : "under review"}?`); };
});
route("/my-invoices", async (c) => {
  const r = await GET("/api/invoices");
  c.innerHTML = head("My Invoices", "Self-billed invoices GSC issued on your behalf, or purchase records for invoices you send.") + `<div class="card flush">${table([{ h: "Invoice", v: x => `<b>${esc(x.number)}</b>` }, { h: "Date", nowrap: 1, v: x => dd(x.issue_date) }, { h: "Jobs", num: 1, v: x => x.lines }, { h: "Net", num: 1, v: x => gbp(x.net) }, { h: "VAT", num: 1, v: x => gbp(x.vat) }, { h: "Total", num: 1, v: x => `<b>${gbp(x.gross)}</b>` }, { h: "Status", v: x => esc(x.status.toLowerCase()) }], r.rows, { onRow: 1, empty: "No invoices yet" })}</div>`;
  bindRows(c, r.rows, x => go("#/my-invoices/" + x.id));
});
route("/my-invoices/:id", async (c, p) => ROUTES["/invoices/:id"](c, p));
route("/profile", async (c) => {
  const p = await GET("/api/agent/profile");
  setTimeout(() => { c.insertAdjacentHTML("beforeend", `<div class="card mt"><h2>Change password</h2>${pwForm()}</div>`); bindPwForm(c); }, 0);
  c.innerHTML = head("Profile", "Details FlowGuard uses for your invoices. Contact GSC Finance to change them.") + `<div class="card"><dl class="kv"><dt>Agent code</dt><dd>${esc(p.code)}</dd><dt>Name</dt><dd>${esc(p.name)}</dd><dt>Supplier type</dt><dd>${p.supplier_type === "SOLE_TRADER" ? "Sole trader / self-employed" : "Limited company"}</dd><dt>VAT</dt><dd>${p.vat_status === "REGISTERED" ? "Registered " + esc(p.vat_number || "(number missing)") : "Not VAT registered"}</dd><dt>Self-billing</dt><dd>${p.self_billing ? (p.agreement ? `Agreement ${esc(p.agreement.ref)} from ${dd(p.agreement.from)}${p.agreement.to ? " to " + dd(p.agreement.to) : ""}` : "Agreement needed") : "You send your own invoices"}</dd><dt>Region</dt><dd>${esc(p.region)}</dd></dl></div>`;
});

start();

/* ---------------------------------------------------------------- workbook import summary */
function workbookView(c, b) {
  const s = b.summary, k = s.counts || {};
  const future = (s.notes || []).some(n => n.includes("after the business date"));
  c.innerHTML = head(`Import #${b.id}: ${b.filename}`, `GSC workbook imported by ${esc(b.uploaded_by)} · ${dt(b.created_at)}`,
    `<a class="btn" href="#/jobs">Jobs</a><a class="btn" href="#/reviews">Review Centre</a><a class="btn primary" href="#/invoices">Invoicing</a>`) + `
  <div class="grid g4">${kpi("New tasks", num(s.tasks_added), `${num(s.tasks_unchanged)} unchanged`, "#/jobs")}${kpi("Updated tasks", num(s.tasks_updated), "changed at source", "#/jobs")}${kpi("Clients / agents added", `${num(s.clients_added)} / ${num(s.agents_added)}`, `${num(s.agents_updated)} agents updated`, "#/agents")}${kpi("Rate differences", num((s.rate_differences || []).length), "not applied", "#/rate-cards", (s.rate_differences || []).length ? "bad" : "zero")}</div>
  ${k.READY !== undefined ? `<div class="grid g3 mt">${kpi("Ready", num(k.READY), "priced, all checks passed", "#/jobs?status=READY")}${kpi("Needs review", num(k.NEEDS_REVIEW), "a person decides", "#/reviews")}${kpi("Blocked", num(k.BLOCKED), "e.g. rejected or unverified", "#/jobs?status=BLOCKED")}</div>` : ""}
  ${(s.notes || []).length ? `<div class="card mt"><h2>Things to know</h2>${s.notes.map(n => `<div class="note warn" style="margin-bottom:6px">${esc(n)}</div>`).join("")}
    ${future && ["SUPER_ADMIN", "FINANCE_ADMIN"].includes(FG.user.role) ? `<a class="btn" href="#/settings">Set the business date</a>` : ""}</div>` : `<div class="note good mt">Imported cleanly.</div>`}
  ${(s.rate_differences || []).length ? `<div class="card flush mt"><div class="card-head" style="padding:14px 16px 0"><h2>Rate differences (not applied)</h2><a class="btn sm r" href="#/configuration/new">Propose a change</a></div>${table([{ h: "Client", v: x => code(x.client) }, { h: "Rule", v: x => esc(x.kind === "AGENT_RATE" ? "Special agent rate" : "Rate") }, { h: "Job type", v: x => code(x.job_type) }, { h: "Agent", v: x => esc(x.agent || "–") }, { h: "In force (client / agent / per 30)", v: x => x.in_force ? x.in_force.map(v => v ?? "–").join(" / ") : `<span class="muted">none</span>` }, { h: "Workbook", v: x => x.workbook.map(v => v ?? "–").join(" / ") }], s.rate_differences)}</div>` : ""}
  ${(s.changed_after_invoicing || []).length ? `<div class="card flush mt"><div class="card-head" style="padding:14px 16px 0"><h2>Invoiced tasks changed at source</h2><span class="muted r">Not changed in FlowGuard. Needs a credit/debit note.</span></div>${table([{ h: "Task", v: x => esc(x.task) }, { h: "Changed fields", v: x => x.fields.map(code).join(" ") }], s.changed_after_invoicing)}</div>` : ""}`;
}

/* ---------------------------------------------------------------- accounts, users, business date */
function pwForm() {
  return `<form class="stack pwf" style="max-width:360px"><label class="f">Current password<input type="password" name="current" autocomplete="current-password" required></label>
    <label class="f">New password<input type="password" name="new" autocomplete="new-password" minlength="12" required></label>
    <p class="faint" style="font-size:12px;margin:0">At least 12 characters with upper and lower case letters and a number.</p>
    <div class="pwerr"></div><div><button class="btn" type="submit">Change password</button></div></form>`;
}
function bindPwForm(c) {
  $$(".pwf", c).forEach(f => f.onsubmit = async e => {
    e.preventDefault();
    try { await POST("/api/auth/password", { current: f.current.value, new: f.new.value }); f.reset(); toast("Password changed"); }
    catch (err) { $(".pwerr", f).innerHTML = errBox(err); }
  });
}
function businessDateCard() {
  return `<div class="card mt"><h2>Business date</h2><p class="muted">The date FlowGuard treats as "today" for checks like "job date in the future". Leave it on today's date for day-to-day work; fix it to close or re-run a past period, or to process work dated later than today.</p>
    <div class="row"><span>Currently: <b>${dd(FG.meta.business_date)}</b> ${FG.meta.business_date_fixed ? `<span class="tag">fixed</span>` : `<span class="tag">today's date</span>`}</span></div>
    <div class="row mt"><input type="date" id="bdIn" value="${FG.meta.business_date}"><button class="btn primary" id="bdSet">Fix to this date</button><button class="btn" id="bdReal" ${FG.meta.business_date_fixed ? "" : "disabled"}>Use today's date</button></div><div id="bdErr"></div></div>`;
}
function bindBusinessDate(c) {
  const save = async v => {
    try {
      const r = await PUT("/api/settings/business-date", { date: v });
      FG.meta = await GET("/api/meta");
      toast(`Business date ${r.fixed ? "fixed to " + dd(r.business_date) : "now follows today's date"}. ${num(r.changed)} job decisions re-evaluated.`);
      renderShell(); onRoute();
    } catch (e) { $("#bdErr", c).innerHTML = errBox(e); }
  };
  $("#bdSet", c) && ($("#bdSet", c).onclick = () => save($("#bdIn", c).value));
  $("#bdReal", c) && ($("#bdReal", c).onclick = () => save(""));
}
function userModal() {
  const roles = [["FINANCE_ADMIN", "Finance Admin: invoices, configuration proposals and approvals"], ["REVIEWER", "Reviewer: decides exceptions"],
    ["AUDITOR", "Auditor: read-only"], ["SUPER_ADMIN", "Super Admin: users and system settings"], ["AGENT", "Agent: own jobs and invoices only"]];
  modal(`<h2>Add a user</h2><form id="uf" class="stack">
    <div class="grid g2"><label class="f">Username<input type="text" name="username" required pattern="[a-z0-9][a-z0-9._-]{2,63}" placeholder="e.g. j.smith"></label>
    <label class="f">Full name<input type="text" name="display_name" required></label></div>
    <label class="f">Role<select name="role">${roles.map(([r, l]) => `<option value="${r}">${esc(l)}</option>`).join("")}</select></label>
    <label class="f" id="agWrap" hidden>Agent (the supplier this login belongs to)<select name="agent_code"><option value="">Choose an agent…</option>${(FG.meta.agents || []).map(a => `<option value="${esc(a.code)}">${esc(a.code)} · ${esc(a.name)}</option>`).join("")}</select></label>
    <label class="f">Temporary password<input type="text" name="password" required minlength="12" value="${esc(tempPassword())}"></label>
    <p class="faint" style="font-size:12px;margin:0">Share it securely; they can change it under Settings (staff) or Profile (agents).</p>
    <div id="uerr2"></div><div class="row"><button class="btn primary" type="submit">Create user</button><button class="btn" type="button" data-close>Cancel</button></div></form>`, (m, close) => {
    const f = $("#uf", m);
    f.role.onchange = () => { const ag = f.role.value === "AGENT"; $("#agWrap", m).hidden = !ag; f.agent_code.required = ag; if (!ag) f.agent_code.value = ""; };
    f.onsubmit = async e => {
      e.preventDefault();
      const d = Object.fromEntries(new FormData(f)); if (d.role !== "AGENT") delete d.agent_code;
      try { await POST("/api/users", d); close(); toast(`User ${d.username} created`); onRoute(); } catch (err) { $("#uerr2", m).innerHTML = errBox(err); }
    };
  });
}
function resetPwModal(username) {
  modal(`<h2>Reset password for ${esc(username)}</h2><form id="rp" class="stack"><label class="f">New temporary password<input type="text" name="password" required minlength="12" value="${esc(tempPassword())}"></label>
    <p class="faint" style="font-size:12px;margin:0">Their existing sessions are signed out.</p><div id="rperr"></div>
    <div class="row"><button class="btn primary" type="submit">Reset</button><button class="btn" type="button" data-close>Cancel</button></div></form>`, (m, close) => {
    $("#rp", m).onsubmit = async e => { e.preventDefault(); try { await POST(`/api/users/${username}/password`, { password: e.target.password.value }); close(); toast("Password reset"); } catch (err) { $("#rperr", m).innerHTML = errBox(err); } };
  });
}
function tempPassword() {
  const a = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789", r = new Uint32Array(16); crypto.getRandomValues(r);
  let p = Array.from(r, x => a[x % a.length]).join("");
  return /[a-z]/.test(p) && /[A-Z]/.test(p) && /\d/.test(p) ? p : tempPassword();
}
