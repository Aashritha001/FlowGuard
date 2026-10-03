/* Staff pages part 2: invoices, reconciliation, commercial config, governance, data */
"use strict";
route("/invoices", async (c, _, q) => {
  const canGen = FG.user.role === "FINANCE_ADMIN";
  const canExport = ["FINANCE_ADMIN", "AUDITOR"].includes(FG.user.role);
  const [r, el, b] = await Promise.all([GET("/api/invoices", q), GET("/api/invoices/eligible").catch(() => null), GET("/api/batches")]);
  c.innerHTML = head("Invoices", "Lane B: one client (sales) invoice line and one agent (purchase or self-billed) line for every READY job. VAT comes from the VAT configuration in force.",
    `<button class="btn" id="dlList">Download list (CSV)</button>${q.batch_id && FG.user.role !== "AGENT" ? `<button class="btn" id="dlBatch">Download batch PDFs (ZIP)</button>` : ""}${canExport ? `<button class="btn" id="exp">Sage-ready export (CSV)</button>` : ""}`) + `
  ${el ? `<div class="card mb"><div class="row"><div><h2 style="margin:0">Next invoice batch</h2><div class="muted">${num(el.jobs)} READY jobs · client net ${gbp(el.client_net)} · agent net ${gbp(el.agent_net)} · ${el.clients} clients · ${el.agents} agents</div></div><span style="flex:1"></span>
    ${canGen ? `<button class="btn primary" id="gen" ${el.jobs ? "" : "disabled"}>Generate ${num(el.jobs)} jobs' invoices</button>` : `<span class="muted">Only Finance Admin can generate invoices</span>`}</div>
    <p class="faint" style="font-size:12px;margin:8px 0 0">Only jobs whose current decision is READY are included. Blocked, under-review, unverified, rejected and shadow-mode jobs can never be invoiced, and a job can never be invoiced twice (database constraint).</p></div>` : ""}
  <div class="card flush"><form class="filters" id="if"><label class="f">Search<input type="search" name="q" value="${esc(q.q || "")}" placeholder="Invoice number"></label>
    <label class="f">Side<select name="kind"><option value="">Both</option><option value="CLIENT" ${q.kind === "CLIENT" ? "selected" : ""}>Client (sales)</option><option value="AGENT" ${q.kind === "AGENT" ? "selected" : ""}>Agent (purchase)</option></select></label>
    <label class="f">Batch<select name="batch_id"><option value="">Any</option>${b.rows.map(x => `<option value="${x.id}" ${String(q.batch_id) === String(x.id) ? "selected" : ""}>${esc(x.reference)}</option>`).join("")}</select></label>
    <button class="btn primary">Apply</button><a class="btn ghost" href="#/invoices">Clear</a></form><div id="it"></div></div>`;
  $("#if").onsubmit = e => { e.preventDefault(); go("#/invoices?" + qs(Object.fromEntries(new FormData(e.target)))); };
  $("#it").innerHTML = table([
    { h: "Invoice", v: x => `<b>${esc(x.number)}</b>` }, { h: "Side", v: x => x.kind === "CLIENT" ? "Client sales" : x.self_billed ? "Agent self-billed" : "Agent purchase" },
    { h: "Party", v: x => esc(x.party || "") }, { h: "Issued", nowrap: 1, v: x => dd(x.issue_date) }, { h: "Lines", num: 1, v: x => x.lines },
    { h: "Net", num: 1, v: x => gbp(x.net) }, { h: "VAT", num: 1, v: x => gbp(x.vat) }, { h: "Gross", num: 1, v: x => `<b>${gbp(x.gross)}</b>` },
    { h: "Export", v: x => x.exported ? `<span class="muted">Exported</span>` : `<span class="tag">Not exported</span>` },
    { h: "", v: x => `<button class="btn sm" data-pdf="${x.id}" data-n="${esc(x.number)}" title="Download PDF">PDF</button>` }], r.rows, { onRow: 1, empty: "No invoices yet" });
  $$("[data-pdf]", c).forEach(b => b.onclick = e => { e.stopPropagation(); downloadFile(`/api/invoices/${b.dataset.pdf}/pdf`, b.dataset.n + ".pdf", b); });
  $("#dlList").onclick = e => downloadFile("/api/invoice-list.csv?" + qs(q), "flowguard_invoices.csv", e.currentTarget);
  $("#dlBatch") && ($("#dlBatch").onclick = e => downloadFile(`/api/batches/${q.batch_id}/pdfs`, "invoices.zip", e.currentTarget));
  bindRows($("#it"), r.rows, x => go("#/invoices/" + x.id));
  $("#gen") && ($("#gen").onclick = async () => {
    $("#gen").disabled = true;
    $("#gen").disabled = false; payRunModal(el);
  });
  $("#exp") && ($("#exp").onclick = async () => { const f = await api("GET", "/api/exports/sage", { query: q.batch_id ? { batch_id: q.batch_id } : {} }); download(f.text, f.filename || "flowguard_sage_export.csv"); toast("Sage-ready export downloaded"); });
});

route("/invoices/:id", async (c, { id }) => {
  const inv = await GET("/api/invoices/" + id);
  const agentPortal = FG.user.role === "AGENT";
  c.innerHTML = head(`${inv.number}`, `${inv.kind === "CLIENT" ? "Client sales invoice" : inv.self_billed ? "Self-billed agent invoice" : "Agent purchase record"} · issued ${dd(inv.issue_date)}`,
    `<button class="btn primary" id="dlPdf">Download PDF</button><button class="btn" id="rec">Reproduce this invoice</button>`) + `
  <div class="grid g-main">
    <div class="card"><div class="row" style="align-items:flex-start"><div><div class="muted" style="font-size:12.5px">${inv.kind === "CLIENT" ? "From" : inv.self_billed ? "Issued by GSC on behalf of" : "Supplier"}</div>
      <b>${esc(inv.kind === "CLIENT" ? inv.issuer.name : inv.party_detail?.name)}</b><div class="muted">${esc(inv.kind === "CLIENT" ? inv.issuer.vat_number : inv.party_detail?.vat_number ? "VAT " + inv.party_detail.vat_number : "Not VAT registered")}</div></div>
      <span style="flex:1"></span><div style="text-align:right"><div class="muted" style="font-size:12.5px">${inv.kind === "CLIENT" ? "Bill to" : "Customer"}</div><b>${esc(inv.kind === "CLIENT" ? inv.party_detail?.name : inv.issuer.name)}</b>
      <div class="muted">${esc(inv.kind === "CLIENT" ? inv.party_detail?.billing_email || "" : "")}</div></div></div>
      ${inv.self_billed ? `<div class="note mt"><b>SELF-BILLING</b>${+inv.vat > 0 ? ` · The VAT shown is your output tax due to HMRC.` : ""}<br>${inv.self_billing_ref ? `Self-billing agreement ${code(inv.self_billing_ref)}. The supplier has agreed GSC raises this invoice on their behalf.` : "Supplier not VAT registered: GSC issues this statement on their behalf."}</div>` : ""}
      ${inv.po_refs?.length ? `<p class="muted mt">PO references: ${inv.po_refs.map(code).join(" ")}</p>` : ""}
      <div class="mt">${table([{ h: "Job", v: l => agentPortal ? esc(l.job_ref) : `<a href="#/jobs/${l.job_id}">${esc(l.job_ref)}</a>` }, { h: "Description", v: l => esc(l.description) }, { h: "PO", v: l => esc(l.po_number || "–") }, { h: "Net", num: 1, v: l => gbp(l.net) }], inv.lines)}</div>
      <div style="display:flex;justify-content:flex-end;margin-top:12px"><dl class="kv" style="min-width:240px"><dt>Net</dt><dd style="text-align:right">${gbp(inv.net)}</dd><dt>VAT ${(inv.vat_rate * 100).toFixed(0)}%</dt><dd style="text-align:right">${gbp(inv.vat)}</dd><dt><b>Total</b></dt><dd style="text-align:right"><b>${gbp(inv.gross)}</b></dd></dl></div>
      <p class="faint" style="font-size:12px">VAT treatment: ${esc(inv.vat_treatment)}. VAT is calculated once on the invoice net by deterministic code; no AI involvement.</p></div>
    <div class="stack">
      ${inv.config_snapshot ? `<div class="card"><h2>Configuration used</h2><dl class="kv"><dt>Rate card versions</dt><dd>${inv.config_snapshot.rate_card_versions.map(v => code("rcv:" + v)).join(" ")}</dd><dt>Contracts</dt><dd>${inv.config_snapshot.contracts.map(v => code("contract:" + v)).join(" ")}</dd><dt>VAT config</dt><dd>v${inv.config_snapshot.vat_config.version} (${inv.config_snapshot.vat_config.rate})</dd><dt>Engine</dt><dd>${inv.config_snapshot.engine_versions.join(", ")}</dd>
        <dt>Other side</dt><dd>${(inv.counterpart_invoices || []).map(n => `<a href="#/invoices?q=${esc(n)}">${esc(n)}</a>`).join(", ")}</dd></dl></div>` : ""}
      <div class="card" id="recOut"><h2>Historical reconstruction</h2><p class="muted">Re-runs the deterministic engine with the exact contract, rate-card, rule and VAT versions recorded on this invoice, even if today's configuration is different.</p></div>
    </div>
  </div>`;
  $("#dlPdf").onclick = e => downloadFile(`/api/invoices/${id}/pdf`, inv.number + ".pdf", e.currentTarget);
  $("#rec").onclick = async () => {
    $("#recOut").innerHTML = `<h2>Historical reconstruction</h2><div class="muted">Re-running…</div>`;
    const r = await GET(`/api/invoices/${id}/reconstruct`);
    const ok = r.result === "REPRODUCED SUCCESSFULLY";
    $("#recOut").innerHTML = `<h2>Historical reconstruction</h2><div class="note ${ok ? "good" : "bad"}"><b>${esc(r.result)}</b><br>Recorded ${gbp(r.recorded.gross)} · reproduced ${gbp(r.reproduced.gross)}</div>
      ${table([{ h: "Job", v: l => esc(l.ref) }, ...(agentPortal ? [] : [{ h: "Versions", v: l => `${esc(l.contract)} · ${esc(l.rate_card)}${l.rate_card_status_today !== "ACTIVE" ? ` <span class="tag">${esc(l.rate_card_status_today.toLowerCase())} today</span>` : ""}` }, { h: "Rules", v: l => (l.rules || []).map(code).join(" ") }]),
        { h: "Recorded", num: 1, v: l => gbp(l.recorded) }, { h: "Reproduced", num: 1, v: l => gbp(l.reproduced) }, { h: "", v: l => l.match ? "✓" : "✗" }], r.lines.slice(0, 50))}`;
  };
});

route("/reconciliation", async (c) => {
  const r = await GET("/api/reconciliation");
  const x = r.current;
  const canRun = ["FINANCE_ADMIN", "AUDITOR"].includes(FG.user.role);
  const diff = v => +v === 0 ? `<span class="st READY">0.00</span>` : `<span class="st BLOCKED">${gbp(v)}</span>`;
  c.innerHTML = head("Reconciliation", "Compares Lane A decisions with Lane B outputs. Any mismatch is shown here, never hidden.", canRun ? `<button class="btn primary" id="run">Run reconciliation</button>` : "") + `
  <div class="grid g3">
    <div class="card"><h2>Client side</h2><dl class="kv"><dt>Expected client total</dt><dd>${gbp(x.expected_client_total)}</dd><dt>Generated client total</dt><dd>${gbp(x.generated_client_total)}</dd><dt>Difference</dt><dd>${diff(x.client_difference)}</dd></dl></div>
    <div class="card"><h2>Agent side</h2><dl class="kv"><dt>Expected agent total</dt><dd>${gbp(x.expected_agent_total)}</dd><dt>Generated agent total</dt><dd>${gbp(x.generated_agent_total)}</dd><dt>Difference</dt><dd>${diff(x.agent_difference)}</dd></dl></div>
    <div class="card"><h2>Counts</h2><dl class="kv"><dt>Invoiced jobs</dt><dd>${num(x.invoiced_jobs)}</dd><dt>Expected invoice lines</dt><dd>${num(x.expected_invoice_lines)}</dd><dt>Generated invoice lines</dt><dd>${num(x.generated_invoice_lines)}</dd><dt>Difference</dt><dd>${x.line_difference === 0 ? `<span class="st READY">0</span>` : `<span class="st BLOCKED">${x.line_difference}</span>`}</dd><dt>Invoices</dt><dd>${x.client_invoices} client · ${x.agent_invoices} agent</dd></dl></div>
  </div>
  <div class="grid g4 mt">${[["Duplicate invoice lines", x.duplicates], ["Missing invoice lines", x.missing], ["Orphan lines", x.orphans], ["Amount mismatches", x.amount_mismatches], ["Header total mismatches", x.header_mismatches], ["Invoiced but not verified/live", x.wrong_outputs]].map(([l, v]) =>
    `<div class="card"><div class="muted" style="font-size:12.5px">${l}</div><div style="font-size:22px;font-weight:650;color:var(--${v.length ? "blocked" : "ready"})">${v.length}</div>${v.length ? `<code style="font-size:11px">${esc(JSON.stringify(v.slice(0, 3)))}</code>` : ""}</div>`).join("")}
    <div class="card"><div class="muted" style="font-size:12.5px">READY, awaiting invoice</div><div style="font-size:22px;font-weight:650">${num(x.ready_awaiting_invoice)}</div><div class="muted">${gbp(x.ready_awaiting_value)} (not a mismatch)</div></div>
    <div class="card"><div class="muted" style="font-size:12.5px">Total mismatches</div><div style="font-size:22px;font-weight:650;color:var(--${x.mismatch_count ? "blocked" : "ready"})">${x.mismatch_count}</div></div></div>
  <div class="grid g2 mt"><div class="card flush"><div class="card-head" style="padding:14px 16px 0"><h2>Batches</h2></div>${table([{ h: "Batch", v: b => esc(b.reference) }, { h: "Expected jobs", num: 1, v: b => b.expected_jobs }, { h: "Actual", num: 1, v: b => b.actual_jobs }, { h: "Expected client net", num: 1, v: b => gbp(b.expected_client_net) }, { h: "Actual", num: 1, v: b => gbp(b.actual_client_net) }, { h: "", v: b => b.ok ? `<span class="st READY">Matched</span>` : `<span class="st BLOCKED">Mismatch</span>` }], x.batches)}</div>
  <div class="card flush"><div class="card-head" style="padding:14px 16px 0"><h2>Run history</h2></div>${table([{ h: "Run", v: x => "#" + x.id }, { h: "When", v: x => dt(x.at) }, { h: "By", v: x => esc(x.by) }, { h: "Mismatches", num: 1, v: x => x.mismatches }], r.runs)}</div></div>`;
  $("#run") && ($("#run").onclick = async () => { const o = await POST("/api/reconciliation/run"); toast(`Reconciliation run #${o.id}: ${o.result.mismatch_count} mismatches`); onRoute(); });
});

/* ---------------------------------------------------------------- commercial */
route("/clients", async (c) => {
  const r = await GET("/api/clients");
  c.innerHTML = head("Clients", "Clients from your imported data. PO requirements are business configuration and changed through governed proposals.") +
    `<div class="card flush">${table([{ h: "Code", v: x => code(x.code) }, { h: "Client", v: x => `<b>${esc(x.name)}</b>` }, { h: "Sector", v: x => esc(x.sector) }, { h: "PO required", v: x => x.po_required ? "Yes" : "No" }, { h: "Sage account", v: x => esc(x.sage_account_ref) }, { h: "Billing", v: x => esc(x.billing_email) }, { h: "Jobs", num: 1, v: x => `<a href="#/jobs?client=${x.id}">${num(x.jobs)}</a>` }], r.rows)}</div>`;
});
route("/agents", async (c) => {
  const r = await GET("/api/agents");
  c.innerHTML = head("Agents / Suppliers", "Supplier type, VAT status and self-billing agreements drive the agent side of Lane B.") +
    `<div class="card flush">${table([{ h: "Code", v: x => code(x.code) }, { h: "Agent", v: x => `<b>${esc(x.name)}</b><div class="faint" style="font-size:12px">${esc(x.region)}</div>` }, { h: "Type", v: x => x.supplier_type === "SOLE_TRADER" ? "Sole trader" : "Limited company" },
      { h: "VAT", v: x => x.vat_status === "REGISTERED" ? (x.vat_number ? `Registered · ${esc(x.vat_number)}` : `<span class="st NEEDS_REVIEW">Registered, no number</span>`) : x.vat_status === "NOT_REGISTERED" ? "Not registered" : `<span class="st NEEDS_REVIEW">Unknown</span>` },
      { h: "Self-billing", v: x => x.self_billing ? (x.agreements.length ? x.agreements.map(a => `${code(a.ref)} <span class="faint">${dd(a.from)} – ${a.to ? dd(a.to) : "open"}</span>`).join("<br>") : `<span class="st NEEDS_REVIEW">No agreement</span>`) : "Supplier invoices" },
      { h: "Jobs", num: 1, v: x => `<a href="#/jobs?agent=${x.id}">${num(x.jobs)}</a>` }], r.rows)}</div>`;
});
route("/contracts", async (c) => {
  const r = await GET("/api/contracts");
  c.innerHTML = head("Contracts", "Contract versions in force by date, with payment terms.") +
    `<div class="card flush">${table([{ h: "Contract", v: x => `<b>${esc(x.code)}</b> v${x.version}` }, { h: "Client", v: x => esc(x.client) }, { h: "From", v: x => dd(x.valid_from) }, { h: "To", v: x => x.valid_to ? dd(x.valid_to) : "open" }, { h: "Status", v: x => esc(x.status) }, { h: "Terms", v: x => `<code style="font-size:11.5px">${esc(JSON.stringify(x.terms))}</code>` }], r.rows)}</div>`;
});
route("/rate-cards", async (c) => {
  const r = await GET("/api/rate-cards");
  const canPropose = ["SUPER_ADMIN", "FINANCE_ADMIN"].includes(FG.user.role);
  c.innerHTML = head("Rate Cards", "Versions are never edited once active. A change creates a new version; history stays for reproduction. Retired versions are kept and still reproduce the invoices that used them.", canPropose ? `<a class="btn primary" href="#/configuration/new">Propose a change</a>` : "") +
    r.rows.map(card => `<div class="card mb"><div class="card-head"><h2>${esc(card.name)}</h2><span class="muted">${esc(card.client)} · ${esc(card.contract_code)}</span>${canPropose ? `<button class="btn sm r" data-rb="${card.id}">Roll back…</button>` : ""}</div>
      ${card.versions.slice().reverse().map(v => `<details ${v.status === "ACTIVE" ? "open" : ""} style="border-top:1px solid var(--line);padding:8px 0">
        <summary class="row" style="cursor:pointer"><b>v${v.version}</b><span class="st ${v.status === "ACTIVE" ? "READY" : "neutral"}">${esc(v.status.toLowerCase())}</span><span>${dd(v.valid_from)} – ${v.valid_to ? dd(v.valid_to) : "open"}</span><span class="muted">${esc(v.note)}</span>${v.rollback_of_version ? `<span class="tag">rollback of v${v.rollback_of_version}</span>` : ""}<span class="faint" style="margin-left:auto;font-size:12px">maker ${esc(v.created_by)} · checker ${esc(v.approved_by || "–")}${v.proposal_id ? ` · <a href="#/configuration/${v.proposal_id}">proposal #${v.proposal_id}</a>` : ""}</span></summary>
        ${v.precedence?.length ? `<p class="muted" style="margin:6px 0">Precedence: ${v.precedence.map(p => code(p)).join(" ")}</p>` : ""}
        ${table([{ h: "Rule", v: x => code(x.code) }, { h: "Kind", v: x => x.kind.toLowerCase() }, { h: "Job type", v: x => esc(x.job_type) }, { h: "Condition", v: x => esc(x.condition || "–") }, { h: "Group", v: x => esc(x.group || "–") }, { h: "Client £", num: 1, v: x => gbp(x.client_amount) }, { h: "Agent £", num: 1, v: x => gbp(x.agent_amount) }, { h: "Clause", v: x => esc(x.clause_ref) }], v.rules)}</details>`).join("")}</div>`).join("");
  $$("[data-rb]", c).forEach(b => b.onclick = () => {
    const card = r.rows.find(x => x.id == b.dataset.rb);
    modal(`<h2>Roll back ${esc(card.name)}</h2><p class="muted">Rollback never deletes history: it creates a new version that restores an earlier version's behaviour, and needs four-eyes approval.</p>
      <label class="f">Restore the behaviour of<select id="tv">${card.versions.map(v => `<option value="${v.version}">v${v.version} (${esc(v.status.toLowerCase())})</option>`).join("")}</select></label>
      <div id="rbErr"></div><div class="row mt"><button class="btn primary" id="rbGo">Create rollback proposal</button><button class="btn" data-close>Cancel</button></div>`, (m, close) => {
      $("#rbGo", m).onclick = async () => { try { const p = await POST("/api/config/proposals", { kind: "ROLLBACK", payload: { rate_card_id: card.id, target_version: +$("#tv", m).value } }); close(); go("#/configuration/" + p.id); } catch (e) { $("#rbErr", m).innerHTML = errBox(e); } };
    });
  });
});

route("/rules", async (c) => {
  const r = await GET("/api/rules");
  c.innerHTML = head("Rules", "System safety controls are code and cannot be configured, disabled or overridden by anyone, including the AI. Business configuration is versioned and governed.") + `
  <div class="grid g2">
    <div class="card"><div class="card-head"><h2>Immutable system safety controls</h2><span class="tag r">not configurable</span></div>${table([{ h: "ID", v: x => code(x.id) }, { h: "Control", v: x => `<b>${esc(x.name)}</b>` }, { h: "Enforced in", v: x => `<span class="muted" style="font-size:12px">${esc(x.enforced_in)}</span>` }], r.safety_controls)}</div>
    <div class="stack">
      <div class="card"><h2>Active configuration health</h2>${r.config_health.length ? `<div class="note warn">${r.config_health.length} issue(s) in configuration already in force. Jobs hit by these are held for review, never guessed.</div>${table([{ h: "Code", v: x => code(x.code) }, { h: "Detail", v: x => esc(x.detail) }], r.config_health)}` : `<div class="note good">No conflicts in active configuration.</div>`}</div>
      <div class="card"><h2>VAT configuration</h2>${table([{ h: "Version", v: x => "v" + x.version }, { h: "Standard rate", v: x => (x.rate * 100).toFixed(0) + "%" }, { h: "From", v: x => dd(x.from) }, { h: "Status", v: x => esc(x.status) }], r.vat)}<p class="faint" style="font-size:12px;margin-top:8px">${esc(r.vat[0]?.note)}</p></div>
      <div class="card"><h2>Four-eyes policy</h2><p>${r.four_eyes_policy === "ALL" ? "Every configuration change needs a second person to approve it." : "High-risk changes need a second person; low-risk changes may be self-approved."} The maker can never be the checker on a high-risk change.</p></div>
    </div>
  </div>
  <div class="card flush mt"><div class="card-head" style="padding:14px 16px 0"><h2>Reason codes and explanation templates</h2><span class="muted r">These templates explain every decision without AI.</span></div>
    ${table([{ h: "Code", v: x => code(x.code) }, { h: "Outcome", v: x => st(x.outcome) }, { h: "Priority", v: x => `<span class="prio ${x.priority}">${x.priority.toLowerCase()}</span>` }, { h: "Routed to", v: x => esc(x.team) }, { h: "Staff template", v: x => `<span style="font-size:12.5px">${esc(x.template)}</span>` }, { h: "Agent wording", v: x => `<span style="font-size:12.5px" class="muted">${esc(x.agent_template)}</span>` }], r.reason_codes)}</div>`;
});

/* ---------------------------------------------------------------- configuration governance */
route("/configuration", async (c) => {
  const [r, pf] = await Promise.all([GET("/api/config/proposals"), GET("/api/fields")]);
  const can = ["SUPER_ADMIN", "FINANCE_ADMIN"].includes(FG.user.role);
  c.innerHTML = head("Configuration", "Every change flows: proposal → schema validation → permission check → conflict check → impact simulation → four-eyes approval → new version.", can ? `<a class="btn primary" href="#/configuration/new">New proposal</a>` : "") + `
  ${factorCard(pf.rows, can)}
  <div class="card mb"><div class="steps">${["Proposal", "Schema validation", "Permission check", "Rule-conflict check", "Impact simulation", "Human review", "Approval", "New version", "Deterministic engine"].map(s => `<span class="s">${s}</span>`).join("")}</div></div>
  <div class="card flush">${table([{ h: "#", v: x => x.id }, { h: "Proposal", v: x => `<b>${esc(x.title)}</b>` }, { h: "Source", v: x => x.source === "AI" ? `<span class="tag ai">AI drafted</span>` : "Human" }, { h: "Status", v: x => propStatus(x.status) }, { h: "Risk", v: x => x.risk_level === "HIGH" ? `<span class="prio HIGH">high</span>` : `<span class="prio NORMAL">normal</span>` },
    { h: "Affected jobs", num: 1, v: x => x.impact.affected_jobs ?? "–" }, { h: "Billing impact", num: 1, v: x => gbp(x.impact.billing_difference) }, { h: "Maker", v: x => esc(x.created_by) }, { h: "Checker", v: x => esc(x.approvals.map(a => a.approver).join(", ") || "–") }, { h: "Created", nowrap: 1, v: x => dt(x.created_at) }], r.rows, { onRow: 1 })}</div>`;
  bindRows(c, r.rows, x => go("#/configuration/" + x.id));
  bindFactorCard(c, pf.rows);
});

/* ---------------------------------------------------------------- price factors (admin-defined fields) */
const KIND_LABEL = { BOOLEAN: "Yes / no", NUMBER: "Number", CHOICE: "Pick-list" };
const factorByKey = k => (FG.meta.price_fields || []).find(f => f.key === k);
function condLabel(cond) {
  if (!cond || !cond.startsWith("F:")) return esc(cond);
  const [key, choice] = cond.slice(2).split("=");
  const f = factorByKey(key);
  return `<span class="tag">${esc(f ? f.label : key)}${choice ? " = " + esc(choice) : ""}</span>`;
}
function ruleSummary(u, f) {
  const per = f.kind === "NUMBER" ? ` per ${esc(f.unit || "unit")}${u.included_units ? ` over ${u.included_units}` : ""}` : "";
  return `${code(u.rule)} ${u.choice ? esc(u.choice) + ": " : ""}+${gbp(u.client_amount)} client / +${gbp(u.agent_amount)} agent${per} <span class="faint">· ${esc(u.rate_card)}${u.job_type === "*" ? " · all job types" : " · " + esc(u.job_type)}</span>`;
}
function factorCard(rows, can) {
  const live = rows.filter(f => f.status === "ACTIVE");
  return `<div class="card flush mb" id="factors"><div class="card-head" style="padding:14px 16px 0"><h2>Price factors</h2>
      <span class="muted">Anything else that changes a price: parking permit, floors climbed, access type, distance…</span>
      ${can ? `<button class="btn primary sm r" id="addFactor">+ Add price factor</button>` : ""}</div>
    <p class="muted" style="padding:6px 16px 0;font-size:12.5px">Adding a factor changes no price. Prices move only when a pricing rule that uses it is proposed, simulated and approved by a second person. Making a factor <b>required</b> holds jobs without a value for review, never a guess.</p>
    ${rows.length ? table([
      { h: "Factor", v: f => `<b>${esc(f.label)}</b><div class="faint" style="font-size:12px">${code(f.key)}${f.description ? " · " + esc(f.description) : ""}</div>` },
      { h: "Type", v: f => esc(KIND_LABEL[f.kind]) + (f.kind === "NUMBER" && f.unit ? ` <span class="faint">(${esc(f.unit)})</span>` : "") + (f.kind === "CHOICE" ? `<div style="margin-top:3px">${f.choices.map(x => `<span class="tag">${esc(x)}</span>`).join(" ")}</div>` : "") },
      { h: "Required for", v: f => !f.required_for.length ? `<span class="muted">Optional</span>` : f.required_for.includes("*") ? "All job types" : f.required_for.map(code).join(" ") },
      { h: "Pricing rules in force", v: f => f.rules.length ? f.rules.map(u => `<div style="font-size:12.5px">${ruleSummary(u, f)}</div>`).join("") : `<span class="muted">None yet</span>` },
      { h: "Status", v: f => f.status === "ACTIVE" ? `<span class="st READY">Active</span>` : `<span class="st neutral">Retired</span>` },
      { h: "", v: f => can && f.status === "ACTIVE" ? `<div class="row" style="flex-wrap:nowrap"><a class="btn sm primary" href="#/configuration/new?factor=${encodeURIComponent(f.key)}">Add pricing rule</a><button class="btn sm" data-edit="${esc(f.key)}">Edit</button>${f.rules.length ? "" : `<button class="btn sm danger" data-retire="${esc(f.key)}">Retire</button>`}</div>` : "" }],
      [...live, ...rows.filter(f => f.status !== "ACTIVE")]) :
      `<div class="empty"><b>No price factors yet</b>${can ? "Add one when something other than job type, weekend, emergency or revisit changes the price." : ""}</div>`}</div>`;
}
function bindFactorCard(c, rows) {
  $("#addFactor", c) && ($("#addFactor", c).onclick = () => factorModal(null));
  $$("[data-edit]", c).forEach(b => b.onclick = () => factorModal(rows.find(f => f.key === b.dataset.edit)));
  $$("[data-retire]", c).forEach(b => b.onclick = () => modal(`<h2>Retire ${esc(b.dataset.retire)}?</h2><p class="muted">Jobs keep their recorded values for history. A retired factor can no longer be used in new rules.</p>
      <label class="f">Reason<textarea id="rr" rows="2" required></textarea></label><div id="rerr"></div><div class="row mt"><button class="btn danger" id="rgo">Retire</button><button class="btn" data-close>Cancel</button></div>`, (m, close) => {
    $("#rgo", m).onclick = async () => { try { await POST(`/api/fields/${b.dataset.retire}/retire`, { reason: $("#rr", m).value }); close(); FG.meta = await GET("/api/meta"); toast("Price factor retired"); onRoute(); } catch (e) { $("#rerr", m).innerHTML = errBox(e); } };
  }));
}
function factorModal(f) {
  const req = f ? f.required_for : [];
  const mode = !req.length ? "none" : req.includes("*") ? "all" : "some";
  modal(`<h2>${f ? "Edit price factor" : "Add a price factor"}</h2>
    <p class="muted" style="font-size:13px">A price factor is a fact about a job that can change its price. After saving, add a pricing rule that says how much it changes the price.</p>
    <form id="ff" class="stack">
      <label class="f">Name<input type="text" name="label" required maxlength="80" value="${esc(f?.label || "")}" placeholder="e.g. Floors climbed"></label>
      <label class="f">Type<select name="kind" ${f ? "disabled" : ""}>${Object.entries(KIND_LABEL).map(([k, l]) => `<option value="${k}" ${f?.kind === k ? "selected" : ""}>${l}</option>`).join("")}</select></label>
      <label class="f" data-for="CHOICE">Options (comma separated)<input type="text" name="choices" value="${esc((f?.choices || []).join(", "))}" placeholder="Standard, Gated estate, High rise"></label>
      <label class="f" data-for="NUMBER">Unit<input type="text" name="unit" maxlength="24" value="${esc(f?.unit || "")}" placeholder="e.g. floors, miles, hours"></label>
      <fieldset style="border:1px solid var(--line);border-radius:var(--r);padding:10px 12px"><legend class="muted" style="font-size:12.5px;padding:0 4px">Must every job have a value?</legend>
        <label class="row" style="gap:6px"><input type="radio" name="req" value="none" ${mode === "none" ? "checked" : ""}> Optional (no value means it does not apply)</label>
        <label class="row" style="gap:6px"><input type="radio" name="req" value="all" ${mode === "all" ? "checked" : ""}> Required for all job types</label>
        <label class="row" style="gap:6px"><input type="radio" name="req" value="some" ${mode === "some" ? "checked" : ""}> Required for selected job types</label>
        <div id="jts" class="row" style="margin:6px 0 0 22px;gap:4px 12px">${FG.meta.job_types.map(t => `<label class="row" style="gap:4px;font-size:12.5px"><input type="checkbox" name="jt" value="${esc(t.code)}" ${req.includes(t.code) ? "checked" : ""}>${esc(t.code)}</label>`).join("")}</div></fieldset>
      <label class="f">Description (optional)<textarea name="description" rows="2">${esc(f?.description || "")}</textarea></label>
      <div id="ferr"></div>
      <div class="row"><button class="btn primary" type="submit">${f ? "Save changes" : "Add price factor"}</button><button class="btn" type="button" data-close>Cancel</button></div>
    </form>`, (m, close) => {
    const form = $("#ff", m);
    const sync = () => {
      const kind = form.kind.value;
      $$("[data-for]", m).forEach(x => x.hidden = x.dataset.for !== kind);
      $("#jts", m).hidden = form.querySelector("[name=req]:checked").value !== "some";
    };
    form.onchange = sync; sync();
    form.onsubmit = async e => {
      e.preventDefault();
      const fd = new FormData(form), r = fd.get("req");
      const data = { label: fd.get("label"), kind: form.kind.value, choices: fd.get("choices") || "", unit: fd.get("unit") || "",
        description: fd.get("description") || "", required_for: r === "all" ? ["*"] : r === "some" ? fd.getAll("jt") : [] };
      try {
        const res = f ? await PUT(`/api/fields/${f.key}`, data) : await POST("/api/fields", data);
        close(); FG.meta = await GET("/api/meta");
        const held = res.effect ? res.effect.counts.NEEDS_REVIEW : null;
        toast(f ? "Price factor saved" : `Added "${res.field.label}". Next: add a pricing rule for it.` + (held !== null ? ` ${num(res.effect.changed)} job decisions changed.` : ""));
        onRoute();
      } catch (err) { $("#ferr", m).innerHTML = errBox(err); }
    };
  });
}
function factorRow(pre = {}) {
  const fs = FG.meta.price_fields || [];
  return `<div class="adj grid" style="grid-template-columns:minmax(0,1.4fr) minmax(0,1fr) 110px 110px 110px 34px;gap:8px;align-items:end">
    <label class="f">Price factor<select name="f_key">${fs.map(f => `<option value="${esc(f.key)}" ${pre.factor === f.key ? "selected" : ""}>${esc(f.label)}</option>`).join("")}</select></label>
    <label class="f f-choice">When it is<select name="f_choice"></select></label>
    <label class="f"><span class="f-cl">Client +£</span><input type="number" name="f_client" step="0.01" min="0" required></label>
    <label class="f"><span class="f-ag">Agent +£</span><input type="number" name="f_agent" step="0.01" min="0" value="0"></label>
    <label class="f f-inc">First N free<input type="number" name="f_inc" step="1" min="0" value="0"></label>
    <button type="button" class="btn ghost sm f-del" aria-label="Remove adjustment" title="Remove">✕</button></div>`;
}
function bindFactorRow(row) {
  const sync = () => {
    const f = factorByKey($("[name=f_key]", row).value); if (!f) return;
    const ch = $("[name=f_choice]", row), wrap = $(".f-choice", row);
    wrap.style.visibility = f.kind === "CHOICE" ? "visible" : "hidden";
    if (f.kind === "CHOICE") { const keep = ch.value; ch.innerHTML = f.choices.map(x => `<option ${x === keep ? "selected" : ""}>${esc(x)}</option>`).join(""); }
    else ch.innerHTML = `<option value="">${f.kind === "BOOLEAN" ? "Yes" : "any value"}</option>`;
    const u = f.unit || "unit";
    $(".f-inc", row).style.visibility = f.kind === "NUMBER" ? "visible" : "hidden";
    $(".f-cl", row).textContent = f.kind === "NUMBER" ? `Client £ per ${u}` : "Client +£";
    $(".f-ag", row).textContent = f.kind === "NUMBER" ? `Agent £ per ${u}` : "Agent +£";
  };
  $("[name=f_key]", row).onchange = sync;
  $(".f-del", row).onclick = () => leave(row, 0);
  sync();
}

const propStatus = s => ({ ACTIVE: `<span class="st READY">Active</span>`, PENDING_APPROVAL: `<span class="st NEEDS_REVIEW">Awaiting approval</span>`, BLOCKED_CONFLICT: `<span class="st BLOCKED">Rule conflict</span>`, REJECTED: `<span class="st neutral">Rejected</span>` }[s] || esc(s));

function simView(s) {
  return `<div class="grid g4">${[["Affected jobs", num(s.affected_jobs)], ["Billing difference", gbp(s.billing_difference)], ["Status changes", num(s.status_changes)], ["Issued invoices that would differ", num(s.historical_invoices_affected)]].map(([l, v]) => `<div class="card" style="padding:12px"><div class="muted" style="font-size:12.5px">${l}</div><div style="font-size:20px;font-weight:650">${v}</div></div>`).join("")}</div>
   <dl class="kv mt"><dt>READY → NEEDS REVIEW</dt><dd>${s.ready_to_review}</dd><dt>NEEDS REVIEW → READY</dt><dd>${s.review_to_ready}</dd><dt>READY → BLOCKED</dt><dd>${s.ready_to_blocked ?? 0}</dd><dt>BLOCKED → READY</dt><dd>${s.blocked_to_ready ?? 0}</dd>
   <dt>Rule conflicts</dt><dd>${s.conflicts.length ? s.conflicts.map(x => `<div class="note bad" style="margin-bottom:4px">${code(x.code)} ${esc(x.detail)}</div>`).join("") : "0"}</dd>
   <dt>New versions</dt><dd>${(s.new_versions || []).map(v => `${esc(v.rate_card)} v${v.version} (${dd(v.valid_from)} – ${v.valid_to ? dd(v.valid_to) : "open"})`).join("<br>") || "–"}</dd></dl>
   ${s.historical_invoices_affected ? `<div class="note warn mt">${esc(s.historical_note)} Affected: ${s.historical_invoice_numbers.map(code).join(" ")}</div>` : ""}
   ${s.examples?.length ? `<h3 class="mt">Example changes</h3>${table([{ h: "Job", v: e => `<a href="#/jobs/${e.job_id}">${esc(e.ref)}</a>` }, { h: "Date", v: e => dd(e.date) }, { h: "Before", v: e => `${st(e.before)} ${gbp(e.before_amount)}` }, { h: "After", v: e => `${st(e.after)} ${gbp(e.after_amount)}` }], s.examples)}` : ""}`;
}

route("/configuration/new", async (c, _, q) => {
  FG.meta = await GET("/api/meta");
  const m = FG.meta;
  const preFactor = q.factor && factorByKey(q.factor) ? q.factor : null;
  c.innerHTML = head("New configuration proposal", "Nothing here is activated. You will see the conflict check and impact simulation before anything is submitted for approval.") + `
  <div class="grid g-main"><form class="card stack" id="pf">
    <div class="grid g2"><label class="f">Client<select name="client_code">${m.clients.map(x => `<option value="${x.code}">${esc(x.name)}</option>`).join("")}</select></label>
    <label class="f">Job type<select name="job_type"><option value="*" ${preFactor ? "selected" : ""}>All job types (adjustments only)</option>${m.job_types.map(x => `<option ${!preFactor && x === m.job_types[0] ? "selected" : ""}>${x.code}</option>`).join("")}</select></label>
    <label class="f">New client rate (£)<input type="number" name="client_amount" step="0.01" min="0" placeholder="leave blank to keep"></label>
    <label class="f">New agent pay (£)<input type="number" name="agent_amount" step="0.01" min="0" placeholder="leave blank to keep"></label>
    <label class="f">Effective from<input type="date" name="effective_from" required value="${FG.meta.business_date}"></label>
    <label class="f">PO requirement<select name="po_required"><option value="">No change</option><option value="true">Required</option><option value="false">Not required</option></select></label>
    <label class="f">Weekend uplift (£, optional)<input type="number" name="weekend" step="0.01" min="0"></label>
    <label class="f">Emergency uplift (£, optional)<input type="number" name="emergency" step="0.01" min="0"></label></div>
    <div class="sep"></div>
    <div class="row"><h3 style="margin:0">Price factor adjustments</h3><span class="muted" style="font-size:12.5px">Add to the price when a job's factor matches. ${m.price_fields.length ? "" : `No price factors yet: <a href="#/configuration">add one</a>.`}</span>
      ${m.price_fields.length ? `<button type="button" class="btn sm r" id="addAdj" style="margin-left:auto">+ Add adjustment</button>` : ""}</div>
    <div id="adjs" class="stack"></div>
    <label class="row" style="gap:6px"><input type="checkbox" name="close_previous" checked> Close the version currently in force (untick to see conflict detection)</label>
    <div id="perr"></div>
    <div class="row"><button type="button" class="btn" id="sim">Run conflict check and simulation</button><button class="btn primary" type="submit">Submit for approval</button></div>
    <p class="faint" style="font-size:12px">Tip: you can also ask the AI assistant, e.g. "For &lt;client code&gt;, make &lt;job type&gt; £45 from 15 January."</p>
  </form><div class="card" id="simOut"><h2>Impact simulation</h2><p class="muted">Run the simulation to see affected jobs, billing difference, status changes, historical invoices and conflicts.</p></div></div>`;
  const addAdj = pre => { const w = document.createElement("div"); w.innerHTML = factorRow(pre); const row = w.firstElementChild; $("#adjs").appendChild(row); bindFactorRow(row); row.style.animation = "rise .3s var(--ease) both"; };
  $("#addAdj") && ($("#addAdj").onclick = () => addAdj({}));
  if (preFactor) addAdj({ factor: preFactor });
  const payload = () => {
    const f = Object.fromEntries(new FormData($("#pf")));
    const mods = [["WEEKEND", f.weekend], ["EMERGENCY", f.emergency]].filter(([, v]) => v).map(([cnd, v]) => ({ condition: cnd, client_amount: v }));
    $$("#adjs .adj").forEach(row => {
      const fk = $("[name=f_key]", row).value, fd = factorByKey(fk);
      mods.push({ factor: fk, choice: fd?.kind === "CHOICE" ? $("[name=f_choice]", row).value : null, client_amount: $("[name=f_client]", row).value,
        agent_amount: $("[name=f_agent]", row).value || "0", included_units: fd?.kind === "NUMBER" ? +$("[name=f_inc]", row).value || 0 : null });
    });
    return { client_code: f.client_code, job_type: f.job_type, client_amount: f.client_amount || null, agent_amount: f.agent_amount || null, effective_from: f.effective_from, modifiers: mods, po_required: f.po_required === "" ? null : f.po_required === "true", close_previous: !!f.close_previous };
  };
  $("#sim").onclick = async () => { $("#perr").innerHTML = ""; try { const s = await POST("/api/config/simulate", { kind: "RATE_CHANGE", payload: payload() }); $("#simOut").innerHTML = `<h2>Impact simulation</h2>${simView(s)}`; } catch (e) { $("#perr").innerHTML = errBox(e); } };
  $("#pf").onsubmit = async e => { e.preventDefault(); try { const p = await POST("/api/config/proposals", { kind: "RATE_CHANGE", payload: payload() }); toast("Proposal submitted. It is not active until a second person approves it."); go("#/configuration/" + p.id); } catch (err) { $("#perr").innerHTML = errBox(err); } };
});

route("/configuration/:id", async (c, { id }) => {
  const p = await GET("/api/config/proposals/" + id);
  FG.lastProposal = p.id;
  const canApprove = ["SUPER_ADMIN", "FINANCE_ADMIN"].includes(FG.user.role) && p.status === "PENDING_APPROVAL";
  const isMaker = p.created_by === FG.user.username;
  c.innerHTML = head(`Proposal #${p.id}`, esc(p.title), "") + `
  <div class="grid g-main">
    <div class="stack"><div class="card"><div class="row">${propStatus(p.status)}${p.source === "AI" ? `<span class="tag ai">Drafted by AI from a prompt</span>` : ""}<span class="prio ${p.risk_level === "HIGH" ? "HIGH" : "NORMAL"}">${p.risk_level.toLowerCase()} risk</span>${p.risk_reasons.map(r => `<span class="tag">${esc(r)}</span>`).join("")}</div>
      <h3 class="mt">Proposed configuration</h3><dl class="kv">${Object.entries(p.payload).filter(([, v]) => v !== null && !(Array.isArray(v) && !v.length)).map(([k, v]) => `<dt>${esc(k.replace(/_/g, " "))}</dt><dd>${Array.isArray(v) ? v.map(m => `${condLabel(m.condition)} +${gbp(m.client_amount)}${m.included_units !== null && m.included_units !== undefined ? " per unit" + (m.included_units ? ` over ${m.included_units}` : "") : ""}`).join(", ") : /amount/.test(k) ? gbp(v) : esc(v)}</dd>`).join("")}</dl>
      ${p.status !== "ACTIVE" ? `<div class="note warn mt"><b>This configuration has NOT been activated.</b></div>` : `<div class="note good mt">Active. New version ${code("rcv:" + p.resulting_rcv_id)} created; jobs re-priced deterministically.</div>`}</div>
      <div class="card"><h2>Impact simulation</h2>${simView(p.simulation)}</div></div>
    <div class="card" style="align-self:start"><h2>Four-eyes approval</h2><dl class="kv"><dt>Maker</dt><dd>${esc(p.created_by)}</dd><dt>Checker</dt><dd>${p.approvals.map(a => `${esc(a.approver)} · ${esc(a.decision.toLowerCase())} · ${dt(a.at)}<br><span class="muted">${esc(a.reason)}</span>`).join("<br>") || "–"}</dd></dl>
      ${p.status === "BLOCKED_CONFLICT" ? `<div class="note bad mt">Activation is blocked until the conflict is resolved. Create a corrected proposal.</div>` : ""}
      ${canApprove ? (isMaker ? `<div class="note mt">You created this proposal, so a different person must approve it. Sign in as <b>checker</b> to approve.</div><button class="btn danger mt" id="rej">Withdraw (reject)</button>` :
      `<form id="af" class="stack mt"><label class="f">Reason (required)<textarea name="reason" rows="3" required placeholder="e.g. Matches signed variation letter dated 12 Jan"></textarea></label><div id="aerr"></div><div class="row"><button class="btn primary" name="d" value="APPROVE">Approve and activate</button><button class="btn danger" name="d" value="REJECT">Reject</button></div></form>`) : ""}
    </div>
  </div>`;
  const decide = async (decision, reason) => { try { await POST(`/api/config/proposals/${id}/decide`, { decision, reason }); toast(decision === "APPROVE" ? "Approved. New version active and jobs re-priced." : "Rejected."); onRoute(); } catch (e) { ($("#aerr") || c).insertAdjacentHTML("afterbegin", errBox(e)); } };
  $("#af") && ($("#af").onsubmit = e => { e.preventDefault(); decide(e.submitter.value, new FormData(e.target).get("reason")); });
  $("#rej") && ($("#rej").onclick = () => decide("REJECT", "Withdrawn by maker"));
});

/* ---------------------------------------------------------------- pay-run invoicing */
function isoDate(d) { return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`; }
function payRunModal(el) {
  const bd = new Date(FG.meta.business_date + "T00:00:00");
  const cutoff = isoDate(new Date(bd.getFullYear(), bd.getMonth(), 0));
  modal(`<h2>Generate invoices</h2><p class="muted" style="font-size:13px">One client invoice per client and one agent invoice (self-bill or purchase record) per payee. Only READY jobs are included; the database prevents any job being invoiced twice.</p>
    <form id="pr" class="stack">
      <label class="row" style="gap:6px"><input type="checkbox" name="use_cutoff" checked> Pay-run cut-off: only work verified on or before</label>
      <input type="date" name="verified_by" value="${cutoff}" style="margin-left:22px">
      <div class="grid g2"><label class="f">Client invoice date<input type="date" name="issue_date" value="${cutoff}" required></label>
      <label class="f">Agent invoice / pay date<input type="date" name="agent_issue_date" value="${FG.meta.business_date}" required></label></div>
      <p class="faint" style="font-size:12px;margin:0">${num(el.jobs)} READY jobs in total (${gbp(el.client_net)} client net). Jobs verified after the cut-off stay READY for the next run.</p>
      <div id="prErr"></div><div class="row"><button class="btn primary" type="submit">Generate</button><button class="btn" type="button" data-close>Cancel</button></div></form>`, (m, close) => {
    const f = $("#pr", m);
    f.use_cutoff.onchange = () => { f.verified_by.disabled = !f.use_cutoff.checked; };
    f.onsubmit = async e => {
      e.preventDefault();
      const btn = f.querySelector("[type=submit]"); btn.disabled = true;
      try {
        const g = await POST("/api/invoices/generate", { verified_by: f.use_cutoff.checked ? f.verified_by.value : null, issue_date: f.issue_date.value, agent_issue_date: f.agent_issue_date.value });
        close(); toast(`${g.batch}: ${g.invoices} invoices for ${num(g.expected.ready_jobs)} jobs. Reconciliation mismatches: ${g.reconciliation_mismatches}.`); go("#/invoices?batch_id=" + g.batch_id);
      } catch (err) { $("#prErr", m).innerHTML = errBox(err); btn.disabled = false; }
    };
  });
}
