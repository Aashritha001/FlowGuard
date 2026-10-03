/* Staff pages part 1: dashboard, money map, jobs, job detail + trace, review centre */
"use strict";
const jobsLink = f => "#/jobs?" + qs(f);

route("/", async (c) => {
  if (FG.user.role === "AGENT") return agentHome(c);
  const d = await GET("/api/dashboard");
  const k = d.kpis;
  const statusRows = d.status;
  const leakMax = Math.max(...d.leakage.map(l => +l.value), 1);
  c.innerHTML = head("Dashboard", `Where the money is, what is ready, what is held, and where money or trust could leak. ${FG.meta.business_date_fixed ? "Business date" : "As of"} ${dd(FG.meta.business_date)}.`,
    `<a class="btn" href="#/money-map">Open Money Map</a><a class="btn primary" href="#/invoices">Invoicing</a>`) + `
  <div class="grid g6">
    ${kpi("Total verified jobs", num(k.verified_jobs), `${gbp0(k.total_verified_value)} priced value`, "#/jobs?verification=VERIFIED")}
    ${kpi("Invoice-ready value", gbp0(k.invoice_ready_value), `${num(k.invoice_ready_jobs)} jobs awaiting invoice`, jobsLink({ status: "READY", invoiced: "no" }))}
    ${kpi("Blocked value", gbp0(k.blocked_value), `${num(k.blocked_jobs)} verified jobs${k.blocked_unpriced ? `, ${k.blocked_unpriced} unpriced` : ""}`, jobsLink({ status: "BLOCKED", verification: "VERIFIED" }))}
    ${kpi("Needs-review value", gbp0(k.review_value), `${num(k.review_jobs)} jobs, ${num(k.review_unpriced)} unpriced`, "#/reviews")}
    ${kpi("Straight-through rate", k.straight_through_rate + "%", `${num(k.straight_through_jobs)} of ${num(k.verified_jobs)} with no human touch`, "#/ai")}
    ${kpi(`Wrong-output count <span class="target">target 0</span>`, num(k.wrong_output_count), `Reconciliation mismatches: ${k.reconciliation_mismatches}`, "#/reconciliation", k.wrong_output_count === 0 ? "zero" : "bad")}
  </div>
  <div class="grid g-main mt">
    <div class="card">
      <div class="card-head"><h2>Money at risk</h2><span class="muted r">${gbp(k.money_at_risk)} across ${d.leakage.reduce((a, l) => a + l.jobs, 0)} jobs</span></div>
      <div class="stack" style="gap:8px">${d.leakage.map(l => `
        <div class="leak" role="button" tabindex="0" data-href="${jobsLink({ bucket: l.bucket })}">
          <div><b>${esc(l.label)}</b><div class="bar-track" style="margin-top:6px"><div class="bar-fill" style="width:${(100 * +l.value / leakMax).toFixed(1)}%;background:${l.bucket === "verified_not_invoiced" ? "var(--ready)" : "var(--review)"}"></div></div></div>
          <div class="lj">${num(l.jobs)} jobs${l.unpriced ? `<br>${l.unpriced} unpriced` : ""}</div><div class="lv">${gbp(l.value)}</div>
        </div>`).join("")}</div>
      <p class="faint" style="font-size:12px;margin-top:8px">Unpriced jobs have no safe deterministic price yet, so they carry no £ value. FlowGuard does not estimate one.</p>
    </div>
    <div class="stack">
      <div class="card"><h2>Money by status</h2>
        <div class="bar-list">${statusRows.map(s => { const max = Math.max(...statusRows.map(x => +x.value), 1); return `
          <div class="bar-item" data-href="${s.status === "INVOICED" ? jobsLink({ invoiced: "yes" }) : jobsLink({ status: s.status, invoiced: "no", verification: "VERIFIED" })}" role="button" tabindex="0">
            <div>${st(s.status)}</div><div class="bar-track"><div class="bar-fill" style="width:${(100 * +s.value / max).toFixed(1)}%;background:var(--${{ INVOICED: "info", READY: "ready", NEEDS_REVIEW: "review", BLOCKED: "blocked" }[s.status]})"></div></div>
            <div class="nowrap"><b>${gbp0(s.value)}</b> <span class="muted">· ${num(s.jobs)}</span></div></div>`; }).join("")}</div>
        <div class="sep"></div>
        <div class="row muted" style="font-size:12.5px">Held before verification (untouchable): <a href="${jobsLink({ verification: "UNVERIFIED" })}">${d.held_before_verification.UNVERIFIED} unverified</a> · <a href="${jobsLink({ verification: "REJECTED" })}">${d.held_before_verification.REJECTED} rejected</a></div>
      </div>
      <div class="card"><h2>Straight-through rate by job week</h2><canvas id="stpChart" height="150" aria-label="Weekly straight-through rate"></canvas></div>
    </div>
  </div>
  <div class="grid g3 mt">
    <div class="card"><h2>Top recurring exception causes</h2><div class="bar-list">${d.reasons.slice(0, 7).map(r => { const max = d.reasons[0]?.jobs || 1; return `
      <div class="bar-item" data-href="${jobsLink({ reason: r.code })}" role="button" tabindex="0"><div style="font-size:13px">${esc(r.title)}</div><div class="bar-track"><div class="bar-fill" style="width:${100 * r.jobs / max}%"></div></div><div class="nowrap"><b>${r.jobs}</b></div></div>`; }).join("") || `<div class="empty">No open exceptions</div>`}</div></div>
    <div class="card"><h2>Top clients by priced value</h2><canvas id="clientChart" height="190" aria-label="Top clients by value"></canvas></div>
    <div class="card"><h2>Operational health</h2><dl class="kv">
      <dt>Invoices generated</dt><dd><a href="#/invoices">${num(k.invoices_generated)}</a> (${d.invoices.client} client · ${d.invoices.agent} agent)</dd>
      <dt>Already invoiced</dt><dd>${gbp(k.invoiced_value)} · ${num(k.invoiced_jobs)} jobs</dd>
      <dt>Exception rate</dt><dd>${k.exception_rate}% of verified jobs</dd>
      <dt>Average review time</dt><dd>${k.avg_review_hours ?? "–"} hours</dd>
      <dt>Reconciliation</dt><dd>${k.reconciliation_mismatches ? `<span class="st BLOCKED">${k.reconciliation_mismatches} mismatches</span>` : `<span class="st READY">Balanced</span>`}</dd>
      <dt>AI financial authority</dt><dd>Disabled</dd></dl></div>
  </div>
  <div class="card flush mt"><div class="card-head" style="padding:14px 16px 0"><h2>Jobs needing attention</h2><a class="r" href="#/jobs?status=NEEDS_REVIEW,BLOCKED&verification=VERIFIED">All jobs needing attention</a></div><div id="dashTable"></div></div>`;
  $$("[data-href]", c).forEach(el => { el.onclick = () => go(el.dataset.href); el.onkeydown = e => e.key === "Enter" && go(el.dataset.href); });
  chart($("#stpChart"), { type: "line", data: { labels: d.trend.map(t => dd(t.week).slice(0, 6)), datasets: [{ data: d.trend.map(t => t.stp), borderColor: cssv("--brand"), backgroundColor: cssv("--brand-soft"), fill: true, tension: .25, pointRadius: 3 }] },
    options: { plugins: { legend: { display: false }, tooltip: { callbacks: { label: x => `${x.parsed.y}% of ${d.trend[x.dataIndex].jobs} jobs` } } }, scales: { y: { min: 0, max: 100, ticks: { callback: v => v + "%" } } } } });
  chart($("#clientChart"), { type: "bar", data: { labels: d.top_clients.map(x => x.name), datasets: [{ data: d.top_clients.map(x => +x.value), backgroundColor: cssv("--brand") }] },
    options: { indexAxis: "y", plugins: { legend: { display: false }, tooltip: { callbacks: { label: x => gbp(x.parsed.x) } } }, onClick: (e, el) => el[0] && go(jobsLink({ client: d.top_clients[el[0].index].client_id })), scales: { x: { ticks: { callback: v => "£" + v / 1000 + "k" } } } } });
  const t = await GET("/api/jobs", { status: "NEEDS_REVIEW,BLOCKED", verification: "VERIFIED", page_size: 12, sort: "client_net" });
  $("#dashTable").innerHTML = jobTable(t.rows); bindRows($("#dashTable"), t.rows, r => go("#/jobs/" + r.id));
});

/* ---------------------------------------------------------------- money map */
route("/money-map", async (c) => {
  const m = await GET("/api/money-map");
  const s = Object.fromEntries(m.stages.map(x => [x.id, x]));
  const maxV = Math.max(+s.verified.value + +s.lane_b.value, 1);
  const link = (v) => `<div class="mm-link"><span style="height:${Math.max(3, Math.round(24 * (+v || 0) / maxV))}px"></span></div>`;
  const node = (x, cls, href, extra = "") => `<button class="mm-node ${cls}" onclick="go('${href}')"><div class="t1">${esc(x.label)}</div><div class="t2">${x.value !== undefined ? gbp0(x.value) : num(x.jobs)}</div><div class="t3">${num(x.jobs ?? x.invoices)} ${x.jobs !== undefined ? "jobs" : "invoices"}${x.unpriced ? ` · ${x.unpriced} unpriced` : ""}</div>${extra}</button>`;
  const verifiedTotal = +s.ready.value + +s.review.value + +s.blocked.value + +s.lane_b.value;
  c.innerHTML = head("Live Money Map", "Every stage is counted live from the database. Select any stage to open the jobs behind it. Steps before verification are outside FlowGuard's processing scope and shown for context.") + `
  <div class="card">
    <div class="scope-band"><span class="pill">Dashed: operational context</span><span class="pill" style="border-color:var(--brand);color:var(--brand)">Solid: FlowGuard processing</span><span class="pill" style="color:var(--blocked)">Red notes: money or trust at risk</span></div>
    <div class="mm">
      <div class="mm-col"><div class="lane-label" style="color:var(--muted)">Before FlowGuard</div>${node(s.received, "context", "#/jobs")}
        <button class="mm-node context" onclick="go('#/jobs')"><div class="t1">Agent completes job</div><div class="t2">${num(s.completed.jobs)}</div><div class="t3">evidence and report captured</div></button>
        <button class="mm-node context" onclick="go('#/jobs?verification=UNVERIFIED')"><div class="t1">GSC admin verifies</div><div class="t2">${num(s.verified.jobs)} verified</div>
        <span class="leakflag">${s.verification.held.unverified} unverified · ${s.verification.held.rejected} rejected (never invoiced)</span></button></div>${link(verifiedTotal)}
      <div class="mm-col"><div class="lane-label">Lane A</div><button class="mm-node lane" onclick="go('#/jobs?verification=VERIFIED')"><div class="t1">Pricing and readiness</div><div class="t2">${num(s.lane_a.jobs)}</div><div class="t3">verified jobs priced deterministically</div><span class="leakflag">${num(s.lane_a.exceptions)} pending exceptions</span></button></div>${link(+s.ready.value + +s.review.value + +s.blocked.value)}
      <div class="mm-col">
        ${node(s.ready, "ready", jobsLink({ status: "READY", invoiced: "no" }), `<span class="leakflag" style="color:var(--ready)">Verified, not yet invoiced</span>`)}
        ${node(s.review, "review", "#/reviews", `<span class="leakflag">Held for a person</span>`)}
        ${node(s.blocked, "blocked", jobsLink({ status: "BLOCKED", verification: "VERIFIED" }), `<span class="leakflag">Known rule prevents invoicing</span>`)}
      </div>${link(s.lane_b.value)}
      <div class="mm-col"><div class="lane-label">Lane B</div><button class="mm-node lane" onclick="go('#/invoices')"><div class="t1">Two invoices per job</div><div class="t2">${gbp0(s.lane_b.value)}</div><div class="t3">${num(s.lane_b.jobs)} jobs · ${s.lane_b.client_invoices} client + ${s.lane_b.agent_invoices} agent invoices</div><div class="t3">Agent side ${gbp0(s.lane_b.agent_value)}</div></button></div>${link(s.reconciliation.value)}
      <div class="mm-col"><button class="mm-node ${s.reconciliation.mismatches ? "blocked" : "ready"}" onclick="go('#/reconciliation')"><div class="t1">Reconciliation</div><div class="t2">${s.reconciliation.mismatches ? s.reconciliation.mismatches + " mismatches" : "Balanced"}</div><div class="t3">${gbp0(s.reconciliation.value)} generated</div></button></div>${link(s.export.value)}
      <div class="mm-col"><button class="mm-node" onclick="go('#/invoices')"><div class="t1">Accounting / Sage export</div><div class="t2">${num(s.export.exported)} / ${num(s.export.invoices)}</div><div class="t3">invoices exported</div>${s.export.not_exported ? `<span class="leakflag">${s.export.not_exported} not yet exported</span>` : ""}</button></div>
    </div>
  </div>
  <div class="grid g2 mt">
    <div class="card"><h2>Where money or trust is leaking</h2><div class="stack" style="gap:8px">${m.leaks.filter(l => l.jobs).map(l => `
      <div class="leak" role="button" tabindex="0" onclick="go('${jobsLink({ bucket: l.bucket })}')"><b>${esc(l.label)}</b><div class="lj">${num(l.jobs)} jobs${l.unpriced ? ` · ${l.unpriced} unpriced` : ""}</div><div class="lv">${gbp(l.value)}</div></div>`).join("")}</div></div>
    <div class="card"><h2>Reading the map</h2><p>A pound moves right only when a deterministic check passes. Anything uncertain stops in <b>Needs review</b> with a reason code; anything a known rule forbids stops in <b>Blocked</b>. Nothing reaches Lane B unless it is <b>Ready</b>.</p>
      <p class="muted">Connector thickness is proportional to the £ value flowing between stages.</p></div>
  </div>`;
});

/* ---------------------------------------------------------------- jobs */
function jobTable(rows, opt = {}) {
  return table([
    { h: "Job", sort: "id", v: r => `<a href="#/jobs/${r.id}"><b>${esc(r.ref)}</b></a><div class="faint" style="font-size:12px">${esc(r.external_job_id)}</div>` },
    { h: "Client", v: r => esc(r.client || "–") },
    { h: "Agent", v: r => esc(r.agent || "–") },
    { h: "Type", sort: "job_type", v: r => esc(r.job_type || "–") + (r.weekend ? ` <span class="tag">wknd</span>` : "") + (r.emergency ? ` <span class="tag">emerg</span>` : "") + (r.revisit ? ` <span class="tag">revisit</span>` : "") },
    { h: "Date", sort: "job_date", nowrap: 1, v: r => dd(r.job_date) },
    { h: "Verified", v: r => r.verification_status === "VERIFIED" ? `<span class="muted">Verified</span>` : `<span class="st neutral">${esc(r.verification_status.toLowerCase())}</span>` },
    { h: "Status", sort: "status", v: r => st(r.status) },
    { h: "Reason", v: r => r.reasons.map(code).join(" ") || `<span class="faint">–</span>` },
    { h: "Client £", num: 1, sort: "client_net", v: r => r.client_net ? gbp(r.client_net) : `<span class="faint">${r.value_at_stake ? "≤ " + gbp(r.value_at_stake) : "unpriced"}</span>` },
    { h: "Agent £", num: 1, v: r => gbp(r.agent_net) },
    { h: "PO", v: r => esc(r.po_number || "–") },
    { h: "Invoice", v: r => r.invoiced ? `<span class="st INVOICED">${esc(r.client_invoice)}</span>` : r.invoice_status === "AWAITING" ? `<span class="muted">Awaiting</span>` : `<span class="faint">–</span>` },
    { h: "Updated", nowrap: 1, v: r => dt(r.updated_at) },
  ], rows, { onRow: 1, empty: "No jobs match these filters", ...opt });
}
route("/jobs", async (c, _, q) => {
  const f = { page: 1, page_size: 25, sort: "id", dir: "desc", ...q };
  const m = FG.meta;
  const sel = (name, label, opts) => `<label class="f">${label}<select name="${name}"><option value="">Any</option>${opts.map(([v, l]) => `<option value="${esc(v)}" ${String(f[name] || "") === String(v) ? "selected" : ""}>${esc(l)}</option>`).join("")}</select></label>`;
  c.innerHTML = head("Jobs", "Every job in FlowGuard's normalised schema with its single current processing state. Natural-language filtering is available in the AI assistant.") + `
  <div class="card flush">
    <form class="filters" id="jf">
      <label class="f">Search<input type="search" name="q" value="${esc(f.q || "")}" placeholder="Job ID, external ID, PO, postcode"></label>
      ${sel("client", "Client", m.clients.map(x => [x.id, x.name]))}
      ${sel("agent", "Agent", m.agents.map(x => [x.id, x.name]))}
      ${sel("job_type", "Job type", m.job_types.map(x => [x.code, x.code]))}
      ${sel("status", "Status", [["READY", "Ready"], ["NEEDS_REVIEW", "Needs review"], ["BLOCKED", "Blocked"], ["NEEDS_REVIEW,BLOCKED", "Needs review or blocked"]])}
      ${sel("verification", "Verification", [["VERIFIED", "Verified"], ["UNVERIFIED", "Unverified"], ["REJECTED", "Rejected"]])}
      ${sel("reason", "Reason", m.reason_codes.map(r => [r.code, r.title]))}
      ${sel("invoiced", "Invoiced", [["yes", "Invoiced"], ["no", "Not invoiced"]])}
      <label class="f">From<input type="date" name="date_from" value="${esc(f.date_from || "")}"></label>
      <label class="f">To<input type="date" name="date_to" value="${esc(f.date_to || "")}"></label>
      <button class="btn primary" type="submit">Apply</button><a class="btn ghost" href="#/jobs">Clear</a>
      ${f.bucket ? `<span class="pill">Money-at-risk bucket: ${esc(f.bucket.replace(/_/g, " "))}</span>` : ""}${f.scenario ? `<span class="pill">Scenario: ${esc(f.scenario)}</span>` : ""}
    </form>
    <div id="jt"><div class="empty">Loading…</div></div>
  </div>`;
  $("#jf").onsubmit = e => { e.preventDefault(); const o = Object.fromEntries([...new FormData(e.target)].filter(([, v]) => v)); go("#/jobs?" + qs({ ...o, sort: f.sort, dir: f.dir })); };
  const r = await GET("/api/jobs", f);
  const pages = Math.max(1, Math.ceil(r.total / r.page_size));
  $("#jt").innerHTML = jobTable(r.rows, { sort: f.sort, dir: f.dir }) + `<div class="pager"><span>${num(r.total)} jobs</span><span style="flex:1"></span>
    <button class="btn sm" ${r.page <= 1 ? "disabled" : ""} id="pp">Previous</button><span>Page ${r.page} of ${pages}</span><button class="btn sm" ${r.page >= pages ? "disabled" : ""} id="np">Next</button></div>`;
  bindRows($("#jt"), r.rows, row => go("#/jobs/" + row.id));
  $("#pp") && ($("#pp").onclick = () => go("#/jobs?" + qs({ ...f, page: r.page - 1 })));
  $("#np") && ($("#np").onclick = () => go("#/jobs?" + qs({ ...f, page: r.page + 1 })));
  $$("th.sortable", c).forEach(th => th.onclick = () => go("#/jobs?" + qs({ ...f, sort: th.dataset.sort, dir: f.sort === th.dataset.sort && f.dir === "desc" ? "asc" : "desc", page: 1 })));
});

function traceView(trace, holder) {
  let sel = trace.findIndex(n => n.state === "fail"); if (sel < 0) sel = trace.findIndex(n => n.key === "net"); if (sel < 0) sel = 0;
  const draw = () => {
    holder.innerHTML = `<div class="trace"><div class="tl" role="list">${trace.map((n, i) => `
      <button class="tl-node ${n.state} ${i === sel ? "sel" : ""}" data-i="${i}" role="listitem"><span class="dot"></span><span><span class="lbl">${esc(n.label)}</span><br><span class="val">${esc(n.value)}</span></span><span class="amt">${n.detail?.client_amount ? gbp(n.detail.client_amount) : ""}</span></button>`).join("")}</div>
      <div class="card" style="background:var(--surface-2)">${nodeDetail(trace[sel])}</div></div>`;
    $$(".tl-node", holder).forEach(b => b.onclick = () => { sel = +b.dataset.i; draw(); });
  };
  draw();
}
function nodeDetail(n) {
  const d = n.detail || {};
  const rows = Object.entries(d).filter(([, v]) => v !== null && v !== "" && !(Array.isArray(v) && !v.length)).map(([k, v]) => `<dt>${esc(k.replace(/_/g, " "))}</dt><dd>${typeof v === "object" ? `<code>${esc(JSON.stringify(v))}</code>` : /amount|net|gross|margin/.test(k) && !isNaN(+v) ? gbp(v) : esc(v)}</dd>`).join("");
  const ref = n.ref ? `<p class="muted" style="margin-top:10px">Source record: ${code(n.ref)}${n.ref.startsWith("invoice:") ? ` · <a href="#/invoices/${n.ref.split(":")[1]}">open invoice</a>` : n.ref.startsWith("rcv:") ? ` · <a href="#/rate-cards">open rate card</a>` : ""}</p>` : "";
  return `<div class="row"><h3 style="margin:0">${esc(n.label)}</h3>${n.state === "fail" ? `<span class="st BLOCKED">Check failed</span>` : n.state === "ok" ? `<span class="st READY">Passed</span>` : ""}</div>
    <p style="font-size:18px;font-weight:650;margin:6px 0 10px">${esc(n.value)}</p>${rows ? `<dl class="kv">${rows}</dl>` : `<p class="muted">No further detail recorded for this step.</p>`}${ref}`;
}

route("/jobs/:id", async (c, { id }) => {
  const j = await GET("/api/jobs/" + id);
  const d = j.decision || {};
  c.innerHTML = head(`${j.ref}`, `${esc(j.external_job_id)} · ${esc(j.job_type || "no job type")} · ${dd(j.job_date)} · ${esc(j.client || "")}`,
    `${j.open_exception ? `<a class="btn primary" href="#/reviews/${j.open_exception}">Review this exception</a>` : ""}<button class="btn" id="explain">${icon("ai", 14)} Explain in plain English</button>`) + `
  <div class="grid g4">
    <div class="card"><div class="muted" style="font-size:12.5px">Processing state</div><div style="margin-top:6px">${st(j.invoiced ? "INVOICED" : j.status)} ${j.invoiced ? st(j.status) : ""}</div><div class="faint" style="font-size:12px;margin-top:6px">Engine ${esc(d.engine_version)} · decided ${dt(d.decided_at)}</div></div>
    <div class="card"><div class="muted" style="font-size:12.5px">Client amount <button class="why" data-why="net">WHY?</button></div><div style="font-size:22px;font-weight:650">${gbp(j.client_net)}</div><div class="faint" style="font-size:12px">${j.client_net ? "net, before VAT" : j.value_at_stake ? "up to " + gbp(j.value_at_stake) + " at stake" : "no safe price"}</div></div>
    <div class="card"><div class="muted" style="font-size:12.5px">Agent amount <button class="why" data-why="base_rule">TRACE</button></div><div style="font-size:22px;font-weight:650">${gbp(j.agent_net)}</div><div class="faint" style="font-size:12px">${esc(j.agent || "")}</div></div>
    <div class="card"><div class="muted" style="font-size:12.5px">Invoices</div><div style="margin-top:6px">${j.client_invoice ? `<a href="#/invoices?q=${esc(j.client_invoice)}">${esc(j.client_invoice)}</a> · <a href="#/invoices?q=${esc(j.agent_invoice)}">${esc(j.agent_invoice)}</a>` : `<span class="muted">${j.status === "READY" ? "Awaiting next invoice batch" : "Not eligible until READY"}</span>`}</div></div>
  </div>
  ${d.reasons?.length ? `<div class="card mt"><h2>Why this job is ${esc(STATUS_LABEL[j.status].toLowerCase())}</h2>${d.reasons.map(r => `
    <div class="reason"><div class="row">${code(r.code)}<b>${esc(r.title)}</b><span class="tag">${esc(r.outcome.replace("_", " ").toLowerCase())}</span><span class="tag">Team: ${esc(r.team)}</span>${r.rules?.length ? `<span class="muted">Rules ${r.rules.map(code).join(" ")}</span>` : ""}</div>
    <p style="margin:8px 0 4px">${esc(r.message)}</p>${Object.keys(r.evidence || {}).length ? `<details><summary class="muted" style="font-size:12.5px">Evidence</summary><code>${esc(JSON.stringify(r.evidence))}</code></details>` : ""}</div>`).join("")}
    <div id="aiExplain"></div></div>` : `<div id="aiExplain"></div>`}
  ${jobFactorsCard(j)}
  <div class="card mt"><div class="card-head"><h2>Financial trace</h2><span class="muted r">Deterministic lineage. Select a step to inspect it. No AI involved.</span></div><div id="trace"></div></div>
  <div class="grid g2 mt">
    <div class="card"><h2>Job record</h2><dl class="kv">
      <dt>External ID</dt><dd>${esc(j.external_job_id)}</dd><dt>Source</dt><dd>${esc(j.source_system)} · ${esc(j.source_record_id || "–")}</dd>
      <dt>Verification</dt><dd>${esc(j.verification_status)} ${j.verified_by ? "by " + esc(j.verified_by) : ""} ${j.verification_timestamp ? dt(j.verification_timestamp) : ""}</dd>
      <dt>Conditions</dt><dd>${[j.weekend && "weekend", j.emergency && "emergency", j.revisit && "revisit"].filter(Boolean).join(", ") || "none"}</dd>
      <dt>Occupancy</dt><dd>${esc(j.occupancy_status || "–")}</dd><dt>PO</dt><dd>${esc(j.po_number || "–")}</dd><dt>Postcode</dt><dd>${esc(j.postcode || "–")}</dd>
      ${j.scenario ? `<dt>Tags</dt><dd><span class="tag">${esc(j.scenario)}</span></dd>` : ""}
      ${j.notes ? `<dt>Notes</dt><dd><div class="note warn" style="font-size:12.5px"><b>Untrusted text from the source system.</b> It is displayed, never executed, and cannot affect pricing or status.<br><span style="white-space:pre-wrap">${esc(j.notes)}</span></div></dd>` : ""}</dl></div>
    <div class="card"><h2>History</h2>
      <h3>Decisions</h3>${table([{ h: "When", v: h => dt(h.decided_at) }, { h: "Status", v: h => st(h.status) }, { h: "Client £", num: 1, v: h => gbp(h.client_net) }, { h: "Codes", v: h => h.codes.map(code).join(" ") }, { h: "", v: h => h.current ? `<span class="tag">current</span>` : "" }], j.decision_history)}
      <h3 class="mt">Reviews</h3>${table([{ h: "When", v: r => dt(r.at) }, { h: "Reviewer", v: r => esc(r.reviewer) }, { h: "Action", v: r => code(r.action) }, { h: "Reason", v: r => esc(r.reason) }, { h: "Result", v: r => st(r.new) }], j.reviews, { empty: "No human review" })}
      <h3 class="mt">Audit</h3>${table([{ h: "When", v: a => dt(a.ts) }, { h: "Who", v: a => esc(a.actor) }, { h: "Action", v: a => code(a.action) }, { h: "Change", v: a => esc(a.old) + (a.new ? " → " + esc(a.new) : "") }], j.audit, { empty: "No job-level audit events" })}</div>
  </div>`;
  traceView(j.trace || [], $("#trace"));
  $("#editFactors") && ($("#editFactors").onclick = () => jobFactorModal(j));
  $$(".why", c).forEach(b => b.onclick = () => { const i = (j.trace || []).findIndex(n => n.key === b.dataset.why); $("#trace").scrollIntoView({ behavior: "smooth" }); if (i >= 0) $$(".tl-node", c)[i]?.click(); });
  $("#explain").onclick = async () => {
    $("#aiExplain").innerHTML = `<div class="note ai mt">Asking the assistant…</div>`;
    try { const r = await POST(`/api/jobs/${id}/explain`); $("#aiExplain").innerHTML = `<div class="note ai mt"><b>${icon("ai", 14)} Plain-English explanation</b><p style="margin-top:6px">${esc(r.text)}</p><div class="faint" style="font-size:12px">${esc(r.note || "")} Source: ${esc(r.mode_explain === "ollama" ? "local model rewording the deterministic facts" : "deterministic template (Ollama unavailable)")}. Financial changes: none.</div></div>`; }
    catch (e) { $("#aiExplain").innerHTML = errBox(e); }
  };
});

/* ---------------------------------------------------------------- review centre */
route("/reviews", async (c, _, q) => {
  const r = await GET("/api/reviews", q);
  const tab = (p, l) => `<a class="btn sm ${q.priority === p || (!q.priority && !p) ? "primary" : ""}" href="#/reviews${p ? "?priority=" + p : ""}">${l}</a>`;
  c.innerHTML = head("Review Centre", "Only items that need a human financial decision. Ordinary system notices live in Notifications. Reviewers supply structured evidence; the deterministic engine still decides the outcome.") + `
  <div class="grid g4">${kpi("Open exceptions", num(r.total), "awaiting a person", "#/reviews")}${kpi("Critical", num(r.counts.CRITICAL), "VAT and rule conflicts first", "#/reviews?priority=CRITICAL", r.counts.CRITICAL ? "bad" : "")}${kpi("High", num(r.counts.HIGH), "", "#/reviews?priority=HIGH")}${kpi("Value at stake", gbp0(r.value), "priced exceptions only", "#/reviews")}</div>
  <div class="card flush mt"><div class="filters">${tab("", "All")}${tab("CRITICAL", "Critical")}${tab("HIGH", "High")}${tab("NORMAL", "Normal")}<span style="flex:1"></span><a class="btn sm ghost" href="#/reviews?include_unverified=yes">Include unverified / rejected holds</a></div>
  <div id="rt"></div></div>`;
  const rows = r.rows;
  $("#rt").innerHTML = table([
    { h: "Priority", v: x => `<span class="prio ${x.priority}">${x.priority.toLowerCase()}</span>` },
    { h: "Job", v: x => `<b>${esc(x.job_ref)}</b><div class="faint" style="font-size:12px">${esc(x.external_job_id)}</div>` },
    { h: "Reason", v: x => `${code(x.reason_code)}<div style="font-size:12.5px;margin-top:2px">${esc(x.title)}</div>` },
    { h: "Explanation", v: x => `<span style="font-size:12.5px">${esc(x.message)}</span>` },
    { h: "£ at stake", num: 1, v: x => x.value_at_stake ? gbp(x.value_at_stake) : `<span class="faint">unpriced</span>` },
    { h: "Client", v: x => esc(x.client || "") },
    { h: "Suggested reviewer", v: x => esc(x.team) + (x.referred_to ? `<div class="faint" style="font-size:12px">referred</div>` : "") },
    { h: "Opened", nowrap: 1, v: x => dt(x.created_at) },
    { h: "", v: x => `<a class="btn sm" href="#/reviews/${x.id}">Review</a>` }], rows, { onRow: 1, empty: "Nothing needs review" });
  bindRows($("#rt"), rows, x => go("#/reviews/" + x.id));
});

const ACTION_LABEL = { SELECT_RULE: "Select the applicable rule", APPLY_PRECEDENCE: "Apply contract precedence", NOT_DUPLICATE: "Not a duplicate", CONFIRM_DUPLICATE: "Confirm duplicate (block)", ADD_PO: "Add purchase order", REFER: "Refer to team", ACKNOWLEDGE: "Acknowledge (immutable control)" };
route("/reviews/:id", async (c, { id }) => {
  const x = await GET("/api/reviews/" + id);
  const j = x.job;
  const reason = j.decision.reasons.find(r => r.code === x.reason_code) || {};
  const canDecide = ["REVIEWER", "FINANCE_ADMIN"].includes(FG.user.role);
  const params = a => {
    if (a === "SELECT_RULE") return `<label class="f">Rule<select name="rule_code">${reason.rules.map(r => `<option>${esc(r)}</option>`).join("")}</select></label>
      <div class="muted" style="font-size:12.5px">Candidates: ${(reason.evidence.candidates || []).map(code).join(" ")}. See the trace below for each amount.</div>`;
    if (a === "APPLY_PRECEDENCE") { const cs = reason.evidence.conditions || []; return `<label class="f">Which uplift applies?<select name="rule">${[cs.join(">"), [...cs].reverse().join(">")].map(r => `<option value="${esc(r)}">${esc(r.replace(">", " supersedes "))}</option>`).join("")}</select></label>`; }
    if (a === "ADD_PO") return `<label class="f">PO number<input type="text" name="po_number" placeholder="e.g. PO-B12345" required></label>`;
    if (a === "REFER") return `<label class="f">Refer to<select name="team"><option>${esc(x.team)}</option><option>Finance</option><option>Commercial</option><option>Operations</option></select></label>`;
    return "";
  };
  c.innerHTML = head(`Review ${j.ref}`, `${esc(x.title)} · opened ${dt(x.created_at)} · suggested reviewer: ${esc(x.team)}`, `<a class="btn" href="#/jobs/${j.id}">Open job</a>`) + `
  <div class="grid g-main">
    <div class="stack">
      <div class="card"><div class="row"><span class="prio ${x.priority}">${x.priority.toLowerCase()}</span>${st(x.status)}${code(x.reason_code)}<span class="muted">£ at stake: <b>${x.value_at_stake ? gbp(x.value_at_stake) : "unpriced"}</b></span></div>
        <p style="font-size:15px;margin:12px 0 8px">${esc(x.message)}</p>
        ${x.all_codes.length > 1 ? `<p class="muted">Other open reasons on this job: ${x.all_codes.filter(c => c !== x.reason_code).map(code).join(" ")}</p>` : ""}
        <h3 class="mt">Evidence</h3><dl class="kv">${Object.entries(reason.evidence || {}).map(([k, v]) => `<dt>${esc(k.replace(/_/g, " "))}</dt><dd>${esc(Array.isArray(v) ? v.join(", ") : v)}</dd>`).join("") || "<dt>–</dt><dd>See trace</dd>"}
          <dt>Relevant rules</dt><dd>${(reason.rules || []).map(code).join(" ") || "–"}</dd><dt>Client</dt><dd>${esc(j.client)}</dd><dt>Agent</dt><dd>${esc(j.agent)}</dd>
          <dt>Job</dt><dd>${esc(j.job_type)} on ${dd(j.job_date)} ${[j.weekend && "weekend", j.emergency && "emergency", j.revisit && "revisit"].filter(Boolean).map(t => `<span class="tag">${t}</span>`).join(" ")}</dd></dl>
        ${x.fix_hint ? `<div class="note mt">${esc(x.fix_hint)}</div>` : ""}
        <div id="aiE" class="mt"></div><button class="btn sm mt" id="ex">${icon("ai", 14)} Explain with AI</button>
      </div>
      <div class="card"><h2>Trace</h2><div id="trace"></div></div>
    </div>
    <div class="card" style="align-self:start"><h2>Decision</h2>
      ${canDecide ? `<p class="muted">Every decision needs a reason and is written to the audit trail. You cannot set a job to Ready directly: FlowGuard re-prices the job using your input.</p>
      <form id="df" class="stack"><label class="f">Action<select name="action" id="act">${x.actions.map(a => `<option value="${a}">${esc(ACTION_LABEL[a] || a)}</option>`).join("")}</select></label>
        <div id="params" class="stack"></div>
        <label class="f">Reason (required)<textarea name="reason" rows="3" required minlength="5" placeholder="e.g. Contract schedule 6.2 states the emergency rate replaces the weekend uplift"></textarea></label>
        <label class="f">Evidence reference<input type="text" name="evidence_ref" placeholder="Contract clause, email reference"></label>
        <div id="derr"></div><button class="btn primary" type="submit">Record decision</button></form>`
      : `<div class="note">Your role (${esc(FG.user.role.toLowerCase())}) can view exceptions but not decide them.</div>`}
      ${x.history.length ? `<h3 class="mt">Previous decisions</h3>${x.history.map(h => `<div class="reason"><b>${esc(h.reviewer)}</b> · ${code(h.action)} · ${dt(h.at)}<br>${esc(h.reason)}</div>`).join("")}` : ""}
    </div>
  </div>`;
  traceView(j.trace || [], $("#trace"));
  $("#ex").onclick = async () => { const r = await POST(`/api/jobs/${j.id}/explain`); $("#aiE").innerHTML = `<div class="note ai">${esc(r.text)}<div class="faint" style="font-size:12px;margin-top:4px">${r.mode_explain === "ollama" ? "Reworded by the local model from deterministic facts." : "Deterministic template; Ollama unavailable."} The reason code above is authoritative.</div></div>`; };
  if (!canDecide) return;
  const drawP = () => $("#params").innerHTML = params($("#act").value); drawP(); $("#act").onchange = drawP;
  $("#df").onsubmit = async e => {
    e.preventDefault(); const f = Object.fromEntries(new FormData(e.target));
    const { action, reason: why, ...p } = f;
    try { const r = await POST(`/api/reviews/${id}/decide`, { action, reason: why, params: p });
      toast(`Decision recorded. Job is now ${STATUS_LABEL[r.new_status] || r.new_status}.`); refreshCounts(); go("#/jobs/" + j.id);
    } catch (err) { $("#derr").innerHTML = errBox(err); }
  };
});

/* ---------------------------------------------------------------- job: price factor values */
function factorValue(f, v) {
  if (v === undefined || v === null || v === "") return `<span class="muted">not recorded</span>`;
  if (f.kind === "BOOLEAN") return v ? "Yes" : "No";
  return esc(v) + (f.kind === "NUMBER" && f.unit ? " " + esc(f.unit) : "");
}
function jobFactorsCard(j) {
  const fs = FG.meta.price_fields || [];
  if (!fs.length) return "";
  const a = j.attributes || {};
  const can = ["SUPER_ADMIN", "FINANCE_ADMIN", "REVIEWER"].includes(FG.user.role) && !j.invoiced;
  const isReq = f => f.required_for.includes("*") || f.required_for.includes(j.job_type);
  return `<div class="card mt"><div class="card-head"><h2>Price factors</h2><span class="muted">Facts about this job that pricing rules can use.</span>
      ${can ? `<button class="btn sm r" id="editFactors">Edit values</button>` : j.invoiced ? `<span class="tag r">Invoiced: values frozen</span>` : ""}</div>
    <dl class="kv">${fs.map(f => `<dt>${esc(f.label)}${isReq(f) ? ` <span class="tag">required</span>` : ""}</dt><dd>${factorValue(f, a[f.key])}${isReq(f) && (a[f.key] === undefined || a[f.key] === null) ? ` <span class="st NEEDS_REVIEW">needed to price this job</span>` : ""}</dd>`).join("")}</dl></div>`;
}
function jobFactorModal(j) {
  const fs = FG.meta.price_fields || [], a = j.attributes || {};
  const input = f => f.kind === "BOOLEAN" ? `<select name="${esc(f.key)}"><option value="">Not recorded</option><option value="yes" ${a[f.key] === true ? "selected" : ""}>Yes</option><option value="no" ${a[f.key] === false ? "selected" : ""}>No</option></select>`
    : f.kind === "CHOICE" ? `<select name="${esc(f.key)}"><option value="">Not recorded</option>${f.choices.map(x => `<option ${a[f.key] === x ? "selected" : ""}>${esc(x)}</option>`).join("")}</select>`
    : `<input type="number" name="${esc(f.key)}" min="0" step="any" value="${esc(a[f.key] ?? "")}" placeholder="${esc(f.unit || "")}">`;
  modal(`<h2>Price factors for ${esc(j.ref)}</h2><p class="muted" style="font-size:13px">Saving re-prices this job with the deterministic engine. Every change is audited.</p>
    <form id="jf" class="stack">${fs.map(f => `<label class="f">${esc(f.label)}${f.kind === "NUMBER" && f.unit ? ` (${esc(f.unit)})` : ""}${input(f)}</label>`).join("")}
      <label class="f">Where did these values come from?<textarea name="_reason" rows="2" required placeholder="e.g. Agent's site photos, client work order"></textarea></label>
      <div id="jerr"></div><div class="row"><button class="btn primary" type="submit">Save and re-price</button><button class="btn" type="button" data-close>Cancel</button></div></form>`, (m, close) => {
    $("#jf", m).onsubmit = async e => {
      e.preventDefault();
      const fd = Object.fromEntries(new FormData(e.target)), reason = fd._reason; delete fd._reason;
      try {
        const r = await PUT(`/api/jobs/${j.id}/fields`, { values: fd, reason });
        close();
        toast(Object.keys(r.changed).length ? `Saved. Job is now ${STATUS_LABEL[r.status] || r.status}${r.client_net ? " at " + gbp(r.client_net) : ""}.` : "No changes");
        onRoute();
      } catch (err) { $("#jerr", m).innerHTML = errBox(err); }
    };
  });
}
