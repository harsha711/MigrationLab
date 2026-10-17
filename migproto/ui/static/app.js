// Replay Recorded Run: read-only, hash-routed pages over /api/*. No execution paths.
"use strict";

const $ = (sel, root = document) => root.querySelector(sel);
const esc = (v) => String(v).replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const unknown = (label = "unknown") => `<span class="unknown">${esc(label)}</span>`;
const val = (v, label) => (v === null || v === undefined || v === "") ? unknown(label) : esc(v);
const nr = (v) => val(v, "not recorded");
const enc = encodeURIComponent;
const short = (attemptId) => attemptId ? attemptId.split("/").pop() : null;

async function api(path) {
  const r = await fetch(path);
  if (!r.ok) throw new Error(`${path}: ${r.status}`);
  return r.json();
}
const runCache = new Map();
const getRun = (id) => {
  if (!runCache.has(id)) runCache.set(id, api(`/api/run?id=${enc(id)}`));
  return runCache.get(id);
};

function verdictBadge(v) {
  if (!v) return `<span class="badge">${unknown()}</span>`;
  const u = String(v).toUpperCase();
  const cls = ["PASS", "REPRODUCED"].includes(u) ? "pass"
    : ["FAIL", "ERROR", "REFUSED"].includes(u) ? "fail" : u === "INCOMPLETE" ? "warn" : "";
  return `<span class="badge ${cls}">${esc(v)}</span>`;
}

// v1 outcome when recorded, else the v0 status. v0 FAIL stays FAIL (cause unknown, G2).
const kindOf = (x) => (x.outcome || x.status || "").toUpperCase().replace("SKIPPED", "SKIP");
const MARKS = { PASS: "✓", FAIL: "✗", REFUSED: "⊘", ERROR: "!", SKIP: "–" };
const isBad = (x) => ["FAIL", "REFUSED", "ERROR"].includes(kindOf(x));
const countsText = (c) => c ? Object.entries(c).map(([k, n]) => `${n} ${esc(k)}`).join(" · ") : "";

function typeBadges(run) {
  const m = run.meta || {};
  const source = run.source_kind === "fixture" ? "recorded fixture" : "local run";
  let model;
  if (m.dev_only_model) model = `<span class="badge warn" title="${esc(m.model)}">dev run, outside submission budget</span>`;
  else if (m.model) model = `<span class="badge">live: ${esc(m.model)}</span>`;
  else model = `<span class="badge">scripted, no model</span>`;
  const v1 = (m.contract_version || "").endsWith("/v1") ? ` <span class="badge">v1 scope</span>` : "";
  return `<span class="badge">${source}</span> ${model}${v1}`;
}

function kv(rows) {
  return `<table class="kv">${rows.map(([k, v, title]) =>
    `<tr><th${title ? ` title="${esc(title)}"` : ""}>${esc(k)}</th><td>${v}</td></tr>`).join("")}</table>`;
}

function crumbs(parts) {
  $("#crumbs").innerHTML = parts.map(([label, href]) =>
    href ? `<a href="${href}">${esc(label)}</a>` : esc(label)).join(" › ");
}

function runHeader(run, active) {
  const id = run.run_id;
  const first = short(run.attempts[0]?.attempt_id) || "a1";
  const tabs = [["Overview", `#/run/${enc(id)}`, "overview"],
                ["Plan", `#/run/${enc(id)}/plan/${first}`, "plan"],
                ["Matrix", `#/run/${enc(id)}/matrix/${first}`, "matrix"],
                ["Agents", `#/run/${enc(id)}/agents`, "agents"],
                ["Metrics", `#/run/${enc(id)}/metrics`, "metrics"]];
  return `<div class="row"><h1>${esc(id)}</h1>${verdictBadge(run.meta?.verdict)}</div>
    <div class="row">${typeBadges(run)}</div>
    <nav class="tabs">${tabs.map(([l, h, k]) => `<a href="${h}" class="${k === active ? "active" : ""}">${l}</a>`).join("")}</nav>`;
}

function attemptPills(run, page, current) {
  return `<div class="pills">${run.attempts.map((a) => {
    const s = short(a.attempt_id);
    const cls = [s === current ? "active" : "", a.ok === 0 ? "bad" : a.ok === 1 ? "ok" : ""].join(" ");
    return `<a class="pill ${cls}" href="#/run/${enc(run.run_id)}/${page}/${s}">${esc(s)} ${a.status ? "· " + esc(a.status) : ""}</a>`;
  }).join("")}</div>`;
}

const findAttempt = (run, s) => run.attempts.find((a) => short(a.attempt_id) === s);

// ---------- pages ----------

async function pageRuns() {
  crumbs([["Runs"]]);
  const { runs, controller_available } = await api("/api/runs");
  const details = await Promise.all(runs.map((r) => getRun(r.run_id)));
  const rows = details.map((d) => {
    const failed = d.attempts.reduce((n, a) => n + a.checks_failed, 0);
    return `<tr class="link" data-href="#/run/${enc(d.run_id)}">
      <td><input type="checkbox" class="pick" value="${esc(d.run_id)}" aria-label="select for compare"></td>
      <td><a href="#/run/${enc(d.run_id)}">${esc(d.run_id)}</a></td>
      <td>${verdictBadge(d.meta?.verdict)}</td><td>${typeBadges(d)}</td>
      <td>${d.attempts.length}</td><td>${d.meta?.checks_total ?? ""}</td><td>${failed}</td></tr>`;
  }).join("");
  return `<div class="row spread"><h1>Runs</h1>
      <div class="row">
        <button id="compare" disabled>Compare 2 selected</button>
        <button disabled title="controller not available">Run Review</button>
      </div></div>
    <div class="card table-wrap"><table>
      <tr><th></th><th>run</th><th>verdict</th><th>type</th><th>attempts</th><th>checks</th><th>failed</th></tr>
      ${rows || `<tr><td colspan="7" class="muted">No runs in examples/ or runs/.</td></tr>`}
    </table></div>
    ${controller_available ? "" : `<p class="muted">Run Review is disabled until the controller exists.</p>`}`;
}

async function pageOverview(id) {
  const run = await getRun(id);
  crumbs([["Runs", "#/"], [id]]);
  const m = run.meta || {};
  const attempts = run.attempts.map((a) => {
    const s = short(a.attempt_id);
    const cause = a.claims.find((c) => c.role === "tester-report" && c.kind === "suspected_cause");
    const cls = a.ok === 1 ? "ok" : a.ok === 0 ? "bad" : "";
    return `<div class="card attempt ${cls}">
      <div class="row spread">
        <div class="row"><strong>${esc(s)}</strong> ${verdictBadge(a.status)}
          <span class="muted">${a.outcome_counts ? countsText(a.outcome_counts)
            : `${a.checks_total} checks · ${a.checks_failed} failed`}</span></div>
        <div class="row"><a href="#/run/${enc(id)}/plan/${s}">Plan</a> · <a href="#/run/${enc(id)}/matrix/${s}">Matrix</a></div>
      </div>
      ${a.halted ? `<p><span class="badge fail">halted</span> ${esc(a.halted)}</p>` : ""}
      ${(a.failed || []).length ? `<p class="muted">Failed: ${a.failed.map((f) => `<code>${esc(f)}</code>`).join(", ")}</p>` : ""}
      ${cause ? `<p class="muted">Tester's diagnosis: ${esc(cause.text)}</p>` : ""}
    </div>`;
  }).join("");
  const man = run.manifest;
  return `${runHeader(run, "overview")}
    <h2>Requested change</h2>
    <p>${m.scenario ? esc(m.scenario) : unknown("not recorded in this run")}</p>
    <h2>Attempts</h2>${attempts}
    ${man ? `<details><summary>Fixture notes</summary><p>${val(man.note)}</p>
      <ul>${(man.sanitization || []).map((s) => `<li>${esc(s)}</li>`).join("")}</ul></details>` : ""}`;
}

async function pagePlan(id, s) {
  const run = await getRun(id);
  crumbs([["Runs", "#/"], [id, `#/run/${enc(id)}`], [`Plan ${s}`]]);
  const a = findAttempt(run, s);
  if (!a) return `${runHeader(run, "plan")}<p>${unknown("no such attempt")}</p>`;
  const plan = a.plans[0];
  let body;
  if (!plan) body = `<p>${unknown("no plan recorded for this attempt")}</p>`;
  else {
    const b = plan.body || {};
    body = `<p><code>${esc(plan.plan_id)}</code>${plan.parent_plan_id
        ? ` <span class="muted">revises <code>${esc(plan.parent_plan_id)}</code></span>` : ""}${
        plan.solution_origin ? ` <span class="badge">${esc(plan.solution_origin)}</span>` : ""}</p>
      ${b.summary ? `<p>${esc(b.summary)}</p>` : ""}
      ${b.revision_notes ? `<div class="card"><strong>Revision notes</strong><p>${esc(b.revision_notes)}</p></div>` : ""}
      <h2>Stages</h2>
      <ol class="stages">${(b.stages || []).map((st) => `<li>
        <code>${esc(st.id)}</code> <span class="badge">${esc(st.kind)}${st.phase ? " · " + esc(st.phase) : ""}</span>
        ${(st.sql || []).length ? `<details><summary>SQL</summary><pre>${esc(st.sql.join("\n\n"))}</pre></details>` : ""}
      </li>`).join("")}</ol>`;
  }
  const executed = a.stages.map((x) => esc(x.stage_id)).join(" → ");
  return `${runHeader(run, "plan")}${attemptPills(run, "plan", s)}${body}
    <h2>Executed</h2><p>${executed || unknown("no stage events")}</p>`;
}

async function pageMatrix(id, s) {
  const run = await getRun(id);
  crumbs([["Runs", "#/"], [id, `#/run/${enc(id)}`], [`Matrix ${s}`]]);
  const a = findAttempt(run, s);
  if (!a) return `${runHeader(run, "matrix")}<p>${unknown("no such attempt")}</p>`;
  const m = await api(`/api/matrix?run=${enc(id)}&attempt=${enc(a.attempt_id)}`);
  let grid = `<p class="muted">No app-level cases with step scope in this attempt.</p>`;
  if (m.rows.length) {
    let body = "", last;
    for (const r of m.rows) {
      if (r.stage_id !== last) {
        body += `<tr class="stage-row"><td colspan="${m.columns.length + 1}">${val(r.stage_id)}</td></tr>`;
        last = r.stage_id;
      }
      body += `<tr><td title="${esc(r.path.join(" → "))}"><code>${esc(r.check_name.replace(/^case\./, ""))}</code></td>${
        m.columns.map((c) => `<td class="cell">${c === r.column
          ? `<button class="mark ${esc(kindOf(r))}" data-obs="${esc(r.observation_id)}" title="${esc(r.outcome || r.status)}">${MARKS[kindOf(r)] || "?"}</button>`
          : `<span class="dot">·</span>`}</td>`).join("")}</tr>`;
    }
    grid = `<div class="card table-wrap"><table class="matrix">
      <tr><th>case</th>${m.columns.map((c) => `<th class="col">${esc(c.replace(" -> ", " → "))}</th>`).join("")}</tr>
      ${body}</table>
      <p class="legend">Columns: writer → reader app version. ✓ pass, ✗ fail, ⊘ refused, ! error, – skipped, · not tested. Click a mark for its evidence.</p></div>`;
  }
  const dbBad = m.db_checks.filter(isBad);
  const dbPass = m.db_checks.filter((d) => kindOf(d) === "PASS").length;
  const dbSkip = m.db_checks.filter((d) => kindOf(d) === "SKIP").length;
  const db = m.db_checks.length ? `<h2>Other checks</h2>
    <p class="muted">${dbPass} passed${dbSkip ? `, ${dbSkip} skipped` : ""}${dbBad.length ? `, ${dbBad.length} not passed:` : ""}</p>
    <div class="chips">${dbBad.map((d) =>
      `<a class="chip ${esc(kindOf(d))}" href="#/obs/${enc(d.observation_id)}">${MARKS[kindOf(d)] || ""} ${esc(d.check_name)} (${val(d.stage_id)})</a>`).join("")}</div>
    <details><summary>Show all</summary><div class="chips" style="margin-top:6px">${m.db_checks.map((d) =>
      `<a class="chip ${esc(kindOf(d))}" href="#/obs/${enc(d.observation_id)}">${esc(d.check_name)}</a>`).join("")}</div></details>` : "";
  return `${runHeader(run, "matrix")}${attemptPills(run, "matrix", s)}${grid}${db}`;
}

async function pageAgents(id) {
  const run = await getRun(id);
  crumbs([["Runs", "#/"], [id, `#/run/${enc(id)}`], ["Agents"]]);
  const link = (e) => e.resolved
    ? `<a class="chip ${esc(kindOf(e))}" href="#/obs/${enc(e.observation_id)}">${MARKS[kindOf(e)] || ""} ${esc(e.check_name)}</a>`
    : `<span class="chip SKIP" title="${esc(e.observation_id)}">not found: ${esc(e.observation_id.split("/").pop())}</span>`;
  const claim = (c) => `<li><strong>${esc(c.role)}</strong> <span class="muted">${esc(c.kind || "")}</span>: ${esc(c.text)}
    ${c.structured
      ? `<div class="chips" style="margin:4px 0 6px">${c.evidence.length ? c.evidence.map(link).join("") : `<span class="muted">no evidence cited</span>`}</div>`
      : ""}</li>`;
  const perAttempt = run.attempts.filter((a) => a.claims.length).map((a) =>
    `<h2>${esc(short(a.attempt_id))}</h2><ul>${a.claims.map(claim).join("")}</ul>`).join("");
  const inv = run.claims_without_attempt;
  const allClaims = [...run.attempts.flatMap((a) => a.claims), ...inv];
  const hasV0 = allClaims.some((c) => !c.structured);
  const retrieved = (run.retrievals || []).flatMap((r) => r.observation_ids || []);
  return `${runHeader(run, "agents")}
    ${!allClaims.length ? `<p class="muted">No agent output in this run.</p>`
      : hasV0 ? `<p class="muted">Claims without evidence chips are unstructured v0 text from agent outputs.</p>` : ""}
    ${retrieved.length ? `<div class="card"><strong>Prior evidence given to the planner</strong>
      <div class="chips" style="margin-top:6px">${retrieved.map((id) => `<a class="chip SKIP" href="#/obs/${enc(id)}">${esc(id)}</a>`).join("")}</div></div>` : ""}
    ${perAttempt}
    ${inv.length ? `<details><summary>Investigator findings (${inv.length}, before any attempt)</summary><ul>${inv.map(claim).join("")}</ul></details>` : ""}`;
}

async function pageMetrics(id) {
  const run = await getRun(id);
  crumbs([["Runs", "#/"], [id, `#/run/${enc(id)}`], ["Metrics"]]);
  const r = run.results || {};
  const calls = (r.model_calls || []).map((c) =>
    `<tr><td>${val(c.role)}</td><td>${nr(c.seconds)}</td><td>${nr(c.total_tokens)}</td></tr>`).join("");
  const b = r.budget || {};
  const used = (value, limit) => value == null ? nr(null) : limit == null ? esc(value) : `${esc(value)} / ${esc(limit)}`;
  const sampling = Object.entries(r.sampling || {}).filter(([, v]) => v != null).map(([k, v]) => `${k}=${v}`).join(", ");
  const serving = Object.entries(r.serving || {}).filter(([, v]) => v != null).map(([k, v]) => `${k}=${v}`).join(", ");
  return `${runHeader(run, "metrics")}
    <div class="card">${kv([
      ["model", nr(r.model)], ["endpoint", nr(r.base_url)], ["checkpoint", nr(r.checkpoint_revision)],
      ["serving backend (as recorded)", nr(r.serving_backend)], ["serving details", nr(serving)],
      ["hardware", nr(r.hardware)], ["topology", nr(r.topology)], ["sampling", nr(sampling)],
      ["wall time (s)", nr(r.elapsed_s)],
      ["model calls (used / limit)", used(r.model_call_count, b.max_model_calls)],
      ["model time (s)", nr(r.model_seconds)],
      ["tokens (in / out / total)", r.total_tokens == null ? nr(null) : `${r.prompt_tokens} / ${r.completion_tokens} / ${r.total_tokens}`],
      ["token budget (used / limit)", used(r.total_tokens, b.max_total_tokens)],
      ["valid proposal rate", nr(r.valid_proposal_rate)],
      ["stopped because", nr(r.reason)],
      ["contract", val(run.meta?.contract_version)],
      ["recorded run id", nr(r.recorded_run_id)],
      ["database snapshot", r.database_snapshot_ids ? r.database_snapshot_ids.map((s) => `<code>${esc(s)}</code>`).join(", ") : nr(null)],
    ])}</div>
    ${(r.start_states || []).length ? `<details><summary>Start state (app identity, patch hash)</summary>
      <pre>${esc(JSON.stringify(r.start_states, null, 2))}</pre></details>` : ""}
    ${calls ? `<details><summary>Per call</summary><div class="table-wrap"><table>
      <tr><th>role</th><th>seconds</th><th>tokens</th></tr>${calls}</table></div></details>` : ""}
    <h2>NeMo Gym</h2><p class="muted">Not connected (no gym/results/ yet).</p>`;
}

async function pageEvidence(oid) {
  const o = await api(`/api/observation?id=${enc(oid)}`);
  if (!o) return `<p>${unknown("observation not found")}</p>`;
  const s = short(o.attempt_id);
  crumbs([["Runs", "#/"], [o.run_id, `#/run/${enc(o.run_id)}`],
          ...(s ? [[`Matrix ${s}`, `#/run/${enc(o.run_id)}/matrix/${s}`]] : []), [o.check_name]]);
  const hist = await api(`/api/history?check=${enc(o.check_name)}`);
  const steps = o.steps.length ? `<div class="table-wrap"><table>
      <tr><th>#</th><th>app</th><th>op</th><th>ok</th><th>value / error</th></tr>
      ${o.steps.map((st) => `<tr><td>${st.step}</td><td>${val(st.app_version)}</td><td>${val(st.op)}</td>
        <td>${st.ok === 1 ? "✓" : st.ok === 0 ? "✗" : unknown()}</td>
        <td>${st.error ? esc(st.error) : st.value == null ? "" : `<code>${esc(st.value)}</code>`}</td></tr>`).join("")}
    </table></div>` : `<p class="muted">No steps (database- or runner-level check).</p>`;
  const others = hist.rows.filter((h) => h.observation_id !== o.observation_id);
  const json = (x) => x == null ? unknown() : `<code>${esc(JSON.stringify(x))}</code>`;
  const v1 = o.scope_source === "recorded_v1";
  const rows = [
    ["run", val(o.run_id)], ["attempt", val(s)], ["plan", val(o.plan_id)], ["stage", val(o.stage_id)],
    ["outcome", o.outcome ? verdictBadge(o.outcome) : unknown(),
      "v0 FAIL does not distinguish failed assertion, runner refusal and execution error (contracts G2)"],
  ];
  if (v1) rows.push(
    ["check type", val(o.check_type)],
    ["app scope (runner)", val(o.app_scope, "none")],
    ["app commit", json(o.app_commit)],
    ["database snapshot", val(o.database_snapshot_id)],
    ["expected", json(o.expected)], ["observed", json(o.observed)]);
  rows.push(
    ["schema state", json(o.schema_state)],
    ["defined by / executed by", `${val(o.definition_provenance)} / ${val(o.executed_by)}`],
    ["scope source", val(o.scope_source)]);
  return `<div class="row"><h1><code>${esc(o.check_name)}</code></h1>${verdictBadge(o.outcome || o.status)}</div>
    <div class="card">${kv(rows)}</div>
    <h2>Steps</h2>${steps}
    ${o.error ? `<h2>Error</h2><pre>${esc(o.error)}</pre>` : ""}
    <details><summary>Recorded line</summary><pre>${esc(JSON.stringify(o.raw, null, 2))}</pre></details>
    ${others.length ? `<h2>Same check elsewhere</h2><div class="table-wrap"><table>
      <tr><th>run</th><th>attempt</th><th>stage</th><th>status</th></tr>
      ${others.map((h) => `<tr class="link" data-href="#/obs/${enc(h.observation_id)}"><td>${esc(h.run_id)}</td>
        <td>${val(short(h.attempt_id))}</td><td>${val(h.stage_id)}</td><td>${verdictBadge(h.status)}</td></tr>`).join("")}
    </table></div>` : ""}`;
}

async function pageCompare(a, b) {
  crumbs([["Runs", "#/"], ["Compare"]]);
  const col = async (id) => {
    const run = await getRun(id);
    return `<div class="card"><div class="row"><a href="#/run/${enc(id)}"><strong>${esc(id)}</strong></a>${verdictBadge(run.meta?.verdict)}</div>
      <div class="row" style="margin:6px 0">${typeBadges(run)}</div>
      ${run.attempts.map((x) => `<p><strong>${esc(short(x.attempt_id))}</strong> ${verdictBadge(x.status)}
        <span class="muted">${x.checks_total} checks · ${x.checks_failed} failed</span>
        ${(x.failed || []).length ? `<br><span class="muted">${x.failed.map((f) => `<code>${esc(f)}</code>`).join(", ")}</span>` : ""}</p>`).join("")}
    </div>`;
  };
  return `<h1>Compare</h1><p class="muted">Separate runs, shown side by side. Results are not merged.</p>
    <div class="compare">${await col(a)}${await col(b)}</div>`;
}

// ---------- router ----------

async function route() {
  const parts = location.hash.replace(/^#\/?/, "").split("/").filter(Boolean).map(decodeURIComponent);
  const view = $("#view");
  try {
    let html;
    if (!parts.length) html = await pageRuns();
    else if (parts[0] === "run" && parts.length === 2) html = await pageOverview(parts[1]);
    else if (parts[0] === "run" && parts[2] === "plan") html = await pagePlan(parts[1], parts[3] || "a1");
    else if (parts[0] === "run" && parts[2] === "matrix") html = await pageMatrix(parts[1], parts[3] || "a1");
    else if (parts[0] === "run" && parts[2] === "agents") html = await pageAgents(parts[1]);
    else if (parts[0] === "run" && parts[2] === "metrics") html = await pageMetrics(parts[1]);
    else if (parts[0] === "obs") html = await pageEvidence(parts.slice(1).join("/"));
    else if (parts[0] === "compare" && parts.length === 3) html = await pageCompare(parts[1], parts[2]);
    else html = `<p>Page not found. <a href="#/">Back to runs</a></p>`;
    view.innerHTML = html;
    window.scrollTo(0, 0);
  } catch (err) {
    view.innerHTML = `<div class="card">Failed to load: ${esc(err.message)}</div>`;
  }
}

document.addEventListener("click", (ev) => {
  const obs = ev.target.closest("[data-obs]");
  if (obs) { location.hash = `#/obs/${enc(obs.dataset.obs)}`; return; }
  if (ev.target.closest("a, input, button, summary")) return;
  const row = ev.target.closest("[data-href]");
  if (row) location.hash = row.dataset.href;
});
document.addEventListener("change", (ev) => {
  if (!ev.target.classList.contains("pick")) return;
  const picked = [...document.querySelectorAll(".pick:checked")].map((x) => x.value);
  const btn = $("#compare");
  btn.disabled = picked.length !== 2;
  btn.onclick = () => { location.hash = `#/compare/${enc(picked[0])}/${enc(picked[1])}`; };
});
window.addEventListener("hashchange", route);
route();
