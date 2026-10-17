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
const fmt = (n) => new Intl.NumberFormat().format(n || 0);
const state = { replayTimer: null, replayStep: 0, replaySpeed: 1400, workspaceStage: null, tourStep: 0, lastFocus: null };

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
    : ["FAIL", "ERROR", "REFUSED"].includes(u) ? "fail" : "";
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
  const checks = details.reduce((n, d) => n + (d.meta?.checks_total || 0), 0);
  const attempts = details.reduce((n, d) => n + d.attempts.length, 0);
  const failures = details.reduce((n, d) => n + d.attempts.reduce((x, a) => x + a.checks_failed, 0), 0);
  const demo = details.find((d) => d.run_id === "live-ordering-revision") || details[0];
  const rows = details.map((d) => {
    const failed = d.attempts.reduce((n, a) => n + a.checks_failed, 0);
    const search = `${d.run_id} ${d.meta?.verdict || ""} ${d.source_kind} ${d.meta?.model || "scripted"}`.toLowerCase();
    return `<tr class="link run-row" data-href="#/run/${enc(d.run_id)}" data-search="${esc(search)}" data-verdict="${esc(d.meta?.verdict || "")}" data-kind="${esc(d.source_kind)}">
      <td><input type="checkbox" class="pick" value="${esc(d.run_id)}" aria-label="select for compare"></td>
      <td><a href="#/run/${enc(d.run_id)}"><strong>${esc(d.run_id)}</strong></a><small class="cell-sub">${d.attempts.length} attempt${d.attempts.length === 1 ? "" : "s"}</small></td>
      <td>${verdictBadge(d.meta?.verdict)}</td><td>${typeBadges(d)}</td>
      <td>${d.attempts.length}</td><td>${d.meta?.checks_total ?? ""}</td><td>${failed}</td></tr>`;
  }).join("");
  return `<section class="hero">
      <div class="hero-copy"><span class="eyebrow">EVIDENCE-DRIVEN MIGRATION REVIEW</span>
        <h1>Ship schema changes<br><span>without flying blind.</span></h1>
        <p>Replay how agents investigate, propose, test, fail, and revise while a deterministic runner remains the source of truth.</p>
        <div class="hero-actions">${demo ? `<a class="button primary" href="#/workspace/${enc(demo.run_id)}">&#9655; Open interactive workspace</a>` : ""}<button class="button ghost" data-scroll-runs>Explore evidence</button></div>
      </div>
      <div class="hero-visual" aria-label="Agent workflow"><div class="orbit orbit-one"></div><div class="orbit orbit-two"></div>
        <div class="flow-node investigator"><small>01</small><strong>Investigate</strong><span>Code impact</span></div>
        <div class="flow-node planner"><small>02</small><strong>Plan</strong><span>Staged SQL</span></div>
        <div class="flow-node runner"><small>03</small><strong>Verify</strong><span>Runner evidence</span></div>
        <div class="flow-line line-a"></div><div class="flow-line line-b"></div></div>
    </section>
    <section class="stat-grid">
      <div class="stat-card"><span>Recorded reviews</span><strong>${fmt(details.length)}</strong><small>Fixtures + local runs</small></div>
      <div class="stat-card"><span>Checks executed</span><strong>${fmt(checks)}</strong><small>Runner observations</small></div>
      <div class="stat-card"><span>Plan attempts</span><strong>${fmt(attempts)}</strong><small>Failed attempts preserved</small></div>
      <div class="stat-card danger"><span>Failures surfaced</span><strong>${fmt(failures)}</strong><small>Evidence, not hidden</small></div>
    </section>
    <section id="runs-table" class="section-head"><div><span class="eyebrow">REVIEW LIBRARY</span><h2>Recorded migration runs</h2></div>
      <div class="row"><button id="compare" disabled>Compare selected</button><button class="button-muted" disabled title="Controller integration is not available">Run Review <span class="soon">soon</span></button></div></section>
    <div class="filterbar"><input id="run-search" type="search" placeholder="Search runs, model, verdict..." aria-label="Search runs">
      <select id="verdict-filter" aria-label="Filter by verdict"><option value="">All verdicts</option><option>PASS</option><option>FAIL</option><option>REPRODUCED</option><option>ERROR</option></select>
      <select id="kind-filter" aria-label="Filter by source"><option value="">All sources</option><option value="fixture">Recorded fixtures</option><option value="local_run">Local runs</option></select></div>
    <div class="card table-wrap runs-card"><table>
      <tr><th></th><th>run</th><th>verdict</th><th>type</th><th>attempts</th><th>checks</th><th>failed</th></tr>
      ${rows || `<tr><td colspan="7" class="muted">No runs in examples/ or runs/.</td></tr>`}
    </table></div>
    ${controller_available ? "" : `<div class="notice"><strong>Why is Run Review disabled?</strong><span>The replay UI is intentionally read-only until the stable controller API is connected.</span></div>`}`;
}

function replaySteps(run) {
  const steps = [{kind: "request", actor: "investigator", title: "Review request received", detail: run.meta?.scenario || "Migration scenario loaded from recorded evidence."}];
  const findings = run.claims_without_attempt || [];
  steps.push({kind: "investigate", actor: "investigator", title: "Application impact mapped", detail: `${findings.length} recorded investigator finding${findings.length === 1 ? "" : "s"}.`});
  for (const attempt of run.attempts) {
    const label = short(attempt.attempt_id);
    steps.push({kind: "plan", actor: "planner", title: `${label}: staged plan proposed`, detail: attempt.plans[0]?.body?.summary || "Plan artifact recorded.", attempt});
    for (const stage of attempt.stages) steps.push({kind: "stage", actor: "runner", title: `${label}: ${stage.stage_id}`, detail: `${stage.stage_kind}${stage.phase ? ` / ${stage.phase}` : ""}`, stage, attempt});
    if (attempt.ok === 0) steps.push({kind: "failure", actor: "tester", title: `${label}: evidence exposed a failure`, detail: attempt.halted || `${attempt.checks_failed} check(s) failed.`, attempt});
    const cause = attempt.claims.find((c) => c.kind === "suspected_cause");
    if (cause) steps.push({kind: "feedback", actor: "tester", title: "Tester returned scoped feedback", detail: cause.text, attempt});
    if (attempt.ok === 1) steps.push({kind: "success", actor: "runner", title: `${label}: verification passed`, detail: `${attempt.checks_total} checks recorded with no final-attempt failures.`, attempt});
  }
  steps.push({kind: "verdict", actor: "runner", title: `Final tested outcome: ${run.meta?.verdict || "unknown"}`, detail: "The verdict is produced from runner observations, not an agent claim."});
  return steps;
}

function replayMarkup(run, steps, index) {
  const step = steps[index];
  const pct = steps.length > 1 ? Math.round(index / (steps.length - 1) * 100) : 100;
  const actorLabel = {investigator: "Investigator", planner: "Planner", tester: "Tester", runner: "Deterministic runner"}[step.actor];
  const tracks = run.attempts.map((a) => `<div class="attempt-track ${a.ok === 1 ? "ok" : "bad"}"><span>${esc(short(a.attempt_id))}</span><div><i style="width:${a === step.attempt ? pct + "%" : a.ok === 1 ? "100%" : "72%"}"></i></div><small>${a.ok === 1 ? "passed" : a.ok === 0 ? "failed" : "recorded"}</small></div>`).join("");
  return `<div class="demo-shell">
    <section class="demo-head"><div><span class="eyebrow">GUIDED RECORDED REPLAY</span><h1>${esc(run.run_id)}</h1><div class="row">${verdictBadge(run.meta?.verdict)} ${typeBadges(run)}</div></div><a class="button ghost" href="#/run/${enc(run.run_id)}">Open evidence browser</a></section>
    <div class="replay-progress"><i style="width:${pct}%"></i></div>
    <section class="replay-grid"><div class="system-map">
      <div class="map-title"><span>Agent workflow</span><small>Step ${index + 1} of ${steps.length}</small></div>
      <div class="agent-lane">${["investigator", "planner", "tester"].map((a) => `<div class="agent-node ${step.actor === a ? "active" : ""}"><span>${a[0].toUpperCase()}</span><strong>${a[0].toUpperCase() + a.slice(1)}</strong><small>${a === "investigator" ? "code context" : a === "planner" ? "migration plan" : "test strategy"}</small></div>`).join('<span class="connector">&#8594;</span>')}</div>
      <div class="runner-node ${step.actor === "runner" ? "active" : ""}"><span>R</span><div><strong>Deterministic runner</strong><small>Old Saleor + New Saleor + PostgreSQL</small></div><b>source of truth</b></div>
      <div class="schema-strip"><span>old app</span><i></i><span>shared database</span><i></i><span>new app</span></div>
    </div><div class="replay-focus ${esc(step.kind)}"><span class="actor-chip">${esc(actorLabel)}</span><h2>${esc(step.title)}</h2><p>${esc(step.detail)}</p>
      ${step.kind === "failure" ? `<div class="failure-callout">Evidence stopped unsafe contract execution.</div>` : ""}${step.kind === "success" ? `<div class="success-callout">All recorded final-attempt checks passed.</div>` : ""}</div></section>
    <section class="attempt-timeline"><div class="map-title"><span>Attempt history</span><small>Earlier failures remain visible</small></div>${tracks}</section>
    <section class="replay-controls"><button id="replay-prev" ${index === 0 ? "disabled" : ""}>&#8592; Previous</button><button id="replay-play" class="primary">${state.replayTimer ? "Pause" : "Play replay"}</button><button id="replay-next" ${index === steps.length - 1 ? "disabled" : ""}>Next &#8594;</button>
      <select id="replay-speed" aria-label="Replay speed"><option value="2200">0.5x</option><option value="1400" ${state.replaySpeed === 1400 ? "selected" : ""}>1x</option><option value="700">2x</option></select><span class="step-counter">${index + 1} / ${steps.length}</span></section>
    <div class="step-dots">${steps.map((_, i) => `<button data-replay-step="${i}" class="${i === index ? "active" : ""}" aria-label="Go to step ${i + 1}"></button>`).join("")}</div></div>`;
}

async function pageDemo(id) {
  const run = await getRun(id);
  crumbs([["Runs", "#/"], ["Guided demo"]]);
  const steps = replaySteps(run);
  state.replayStep = Math.min(state.replayStep, steps.length - 1);
  if (state.replayStep === steps.length - 1 && state.replayTimer) {
    clearInterval(state.replayTimer);
    state.replayTimer = null;
  }
  return replayMarkup(run, steps, state.replayStep);
}

const stageTitle = (stage) => {
  const id = (stage?.stage_id || stage?.id || "Recorded stage").replaceAll("-", " ");
  if (/expand|add column|trigger/.test(id)) return "Add compatible field";
  if (/backfill/.test(id)) return "Backfill existing data";
  if (/deploy new/.test(id)) return "Deploy new application";
  if (/retire old/.test(id)) return "Retire old application";
  if (/contract|drop old|remove old/.test(id)) return "Remove legacy field";
  return id.replace(/\b\w/g, (c) => c.toUpperCase());
};

function matrixStatus(rows, stageId, side) {
  const scoped = rows.filter((r) => r.stage_id === stageId && (r.path || []).includes(side));
  if (!scoped.length) return {kind: "unknown", label: "Not tested", obs: null};
  const failed = scoped.find((r) => r.status !== "PASS");
  return failed
    ? {kind: "fail", label: failed.status || "Fail", obs: failed.observation_id}
    : {kind: "pass", label: "Pass", obs: scoped[0].observation_id};
}

function workspaceMark(result, stage, side) {
  const icon = result.kind === "pass" ? "&#10003;" : result.kind === "fail" ? "&#10005;" : "&#183;";
  const spoken = `${side} application: ${result.label} at ${stageTitle(stage)}`;
  const attrs = result.obs ? ` data-obs="${esc(result.obs)}" title="Open TestEvidence" aria-label="${esc(spoken)}. Open evidence."` : ` disabled title="No scoped observation recorded" aria-label="${esc(spoken)}"`;
  return `<button class="compat-result ${result.kind}"${attrs}><span>${icon}</span>${esc(result.label)}</button>`;
}

function planColumn(attempt, label, tone) {
  const plan = attempt?.plans?.[0]?.body;
  const stages = plan?.stages || [];
  return `<article class="plan-compare ${tone}"><header><span>${esc(label)}</span>${verdictBadge(attempt?.status)}</header>
    <h3>${esc(plan?.summary || "Plan not recorded")}</h3>
    <ol>${stages.map((s, i) => `<li><b>${i + 1}</b><div><strong>${stageTitle(s)}</strong><small>${esc(s.description || s.kind || "")}</small></div></li>`).join("")}</ol></article>`;
}

async function pageWorkspace(id) {
  const run = await getRun(id);
  crumbs([["Runs", "#/"], ["Interactive workspace"]]);
  const matrices = await Promise.all(run.attempts.map((a) => api(`/api/matrix?run=${enc(id)}&attempt=${enc(a.attempt_id)}`)));
  const first = run.attempts[0];
  const revised = run.attempts[1] || first;
  const selectedAttempt = revised;
  const matrix = matrices[run.attempts.indexOf(selectedAttempt)] || {rows: [], db_checks: []};
  const stages = selectedAttempt?.stages || [];
  const selectedId = state.workspaceStage || stages[0]?.stage_id;
  state.workspaceStage = selectedId;
  const selectedIndex = Math.max(0, stages.findIndex((s) => s.stage_id === selectedId));
  const selectedStage = stages[selectedIndex] || stages[0];
  const allRows = [...matrix.rows, ...matrix.db_checks];
  const failed = [...(matrices[0]?.rows || []), ...(matrices[0]?.db_checks || [])].find((r) => r.status !== "PASS");
  const feedback = revised?.claims?.find((c) => c.kind === "revision_notes") || first?.claims?.find((c) => c.kind === "suspected_cause");
  const investigator = run.claims_without_attempt?.[0];
  const planner = revised?.claims?.find((c) => c.role === "planner");
  const tester = first?.claims?.find((c) => c.role === "tester-report");
  const calls = run.results?.model_calls || [];
  const timeline = stages.map((s, i) => `<button class="workspace-stage ${s.stage_id === selectedId ? "active" : ""}" data-stage-select="${esc(s.stage_id)}"${s.stage_id === selectedId ? ' aria-current="step"' : ""}><span>${i + 1}</span><div><strong>${stageTitle(s)}</strong><small>${esc(s.description || s.stage_kind || "")}</small></div><i>${s.phase ? esc(s.phase) : esc(s.stage_kind)}</i></button>`).join("");
  const rows = stages.map((s) => {
    const old = matrixStatus(matrix.rows, s.stage_id, "old");
    const newer = matrixStatus(matrix.rows, s.stage_id, "new-staged");
    return `<tr data-workspace-row data-stage="${esc(s.stage_id)}" data-status="${old.kind === "fail" || newer.kind === "fail" ? "FAIL" : old.kind === "pass" || newer.kind === "pass" ? "PASS" : "UNKNOWN"}" data-version="${esc((matrix.rows.filter((r) => r.stage_id === s.stage_id).flatMap((r) => r.path || [])).join(" "))}"><th scope="row"><strong>${stageTitle(s)}</strong><small><code>${esc(s.stage_id)}</code> · ${esc(s.phase || s.stage_kind)}</small></th><td>${workspaceMark(old, s, "Old")}</td><td>${workspaceMark(newer, s, "New")}</td></tr>`;
  }).join("");
  const trace = [
    ["01", "Investigator", investigator?.text || "No structured investigator conclusion recorded.", run.claims_without_attempt?.length || 0],
    ["02", "Planner", planner?.text || revised?.plans?.[0]?.body?.summary || "No planner claim recorded.", calls.filter((c) => c.role?.startsWith("planner")).length],
    ["03", "Tester", tester?.text || first?.halted || "No tester conclusion recorded.", calls.filter((c) => c.role?.startsWith("tester")).length],
    ["04", "Feedback", feedback?.text || "No revision feedback recorded.", run.attempts.length - 1]
  ].map(([n, role, text, count]) => `<article class="trace-step"><b>${n}</b><div><span>${role}</span><p>${esc(text)}</p><small>${role === "Feedback" ? `${count} revision round(s)` : `${count} recorded ${role === "Investigator" ? "finding(s)" : "model call(s)"}`}</small></div></article>`).join("");
  return `<div class="mode-banner"><span>&#9655;</span><div><strong>Recorded migration review</strong><small>You are replaying saved evidence. Nothing on this screen changes a database.</small></div></div><div class="workspace-head"><div><span class="eyebrow">INTERACTIVE COMPATIBILITY REVIEW</span><h1>${esc(id)}</h1><p>Follow one clear story: the first plan was blocked, agent feedback revised it, and the runner verified the safer ordering.</p></div><div class="workspace-actions"><button class="button ghost" data-open-tour>&#10024; Guide me</button><button class="button primary" data-reveal-revision>Compare plans</button><div class="final-outcome"><span>&#10003;</span><p><small>FINAL OUTCOME</small><strong>${run.attempts.length > 1 ? "Revised plan passed" : esc(run.meta?.verdict || "Unknown")}</strong><em>${run.attempts.length - 1} unsafe attempt preserved</em></p></div></div></div>
    <section class="journey-strip"><div><span>Migration journey</span><strong>Step ${selectedIndex + 1} of ${stages.length}</strong></div><div class="journey-track"><i style="width:${stages.length ? ((selectedIndex + 1) / stages.length) * 100 : 0}%"></i>${stages.map((s, i) => `<button class="${i <= selectedIndex ? "visited" : ""}" data-stage-select="${esc(s.stage_id)}" title="${stageTitle(s)}" aria-label="Step ${i + 1}: ${stageTitle(s)}"${i === selectedIndex ? ' aria-current="step"' : ""}><span>${i + 1}</span></button>`).join("")}</div><div class="journey-now"><span class="stage-pulse"></span><p><small>YOU ARE HERE</small><strong>${stageTitle(selectedStage)}</strong></p><button data-next-stage>${selectedIndex === stages.length - 1 ? "Restart journey" : "Next stage"} &#8594;</button></div></section>
    <section class="workspace-layout">
      <aside class="workspace-panel plan-panel"><div class="panel-heading"><div><span class="eyebrow">MIGRATION PLAN</span><h2>Safe rollout timeline</h2></div><span class="counter">${stages.length} steps</span></div>${timeline}
        <div class="execution-controls"><span class="eyebrow">REPLAY CONTROLS</span><button data-reset-workspace>Restart replay</button><button data-replay-selected>Replay selected step</button><button data-focus-checks>View recorded checks</button><small>Replay controls only. No database is changed.</small></div></aside>
      <main class="workspace-panel matrix-panel" data-tour-target="matrix"><div class="panel-heading"><div><span class="eyebrow">OLD VS. NEW</span><h2>Compatibility matrix</h2><p class="panel-help">Click any pass or fail result to inspect the evidence behind it.</p></div><span class="source-pill"><i></i> Live from recorder</span></div>
        <div class="scope-filters"><select id="workspace-version" aria-label="Filter by application version"><option value="">All app versions</option><option value="old">Old application</option><option value="new-staged">New application</option></select><select id="workspace-stage" aria-label="Filter by migration stage"><option value="">All stages</option>${stages.map((s) => `<option value="${esc(s.stage_id)}">${stageTitle(s)}</option>`).join("")}</select><select id="workspace-status" aria-label="Filter by test status"><option value="">All statuses</option><option value="PASS">Passed</option><option value="FAIL">Failed</option><option value="UNKNOWN">Not tested</option></select><p class="scope-unavailable">Additional scope not recorded in this v0 fixture: schema fingerprint and snapshot ID.</p></div>
        <div class="compat-table" id="recorded-checks" tabindex="-1"><table><caption>Old and new application compatibility by migration stage. Select a result to inspect its evidence.</caption><thead><tr><th scope="col">Migration stage</th><th scope="col">Old application</th><th scope="col">New application</th></tr></thead><tbody>${rows}</tbody></table></div>
        <div class="scope-callout"><b>Why scope matters</b><p>A pass for the new application does not prove the old application can read or write the same schema. Every cell is derived from observations scoped to an application path and migration stage.</p></div>
      </main>
      <aside class="workspace-panel insight-panel"><div class="panel-heading"><div><span class="eyebrow">RECOMMENDATION</span><h2>What should I do?</h2></div><span class="counter fail">Action required</span></div><article class="recommendation"><span>FINAL RECOMMENDATION</span><h3>Do not deploy an immediate rename</h3><dl><dt>Reason</dt><dd>${esc(first?.halted || "The recorded first plan failed a mandatory runner check.")}</dd><dt>Evidence</dt><dd>${failed ? `<button data-obs="${esc(failed.observation_id)}">${esc(failed.observation_id)}</button>` : unknown("not recorded")}</dd><dt>Safe alternative</dt><dd>Additive field + dual-write + backfill + retire old app before contract.</dd></dl></article></aside>
    </section>
    <details class="progressive-card revision-section" id="revision-story"><summary><span><small>PLAN COMPARISON</small><strong>Why did the first attempt fail?</strong></span><em>Compare failed and revised plans &#8594;</em></summary><div class="progressive-body"><div class="revision-grid">${planColumn(first, "Attempt 1 · blocked", "unsafe")}<div class="revision-evidence"><span>RUNNER FAILURE</span><strong>${esc(failed?.check_name || first?.failed?.[0] || "Recorded failure")}</strong><p>${esc(first?.halted || "The first plan did not complete.")}</p>${failed ? `<button class="button ghost" data-obs="${esc(failed.observation_id)}">Inspect evidence</button>` : ""}<hr><span>AGENT FEEDBACK</span><p>${esc(feedback?.text || "No revision note recorded.")}</p></div>${planColumn(revised, "Attempt 2 · passed", "safe")}</div></div></details>
    <details class="progressive-card"><summary><span><small>AGENT TRACE</small><strong>How did the agents reach the revision?</strong></span><em>${run.attempts.length - 1} revision round &#8594;</em></summary><div class="progressive-body"><div class="trace-list">${trace}</div></div></details>
    <details class="progressive-card"><summary><span><small>EVALUATION</small><strong>View recorded model and test measurements</strong></span><em>Open metrics &#8594;</em></summary><div class="progressive-body evaluation-strip"><div><span class="eyebrow">RECORDED RESULTS</span><h2>Measured, not inferred</h2></div><div><span>Plan attempts</span><strong>${run.attempts.length}</strong></div><div><span>Final test-pass rate</span><strong>${revised?.checks_total ? Math.round((revised.checks_total - revised.checks_failed) / revised.checks_total * 100) : 0}%</strong></div><div><span>Token usage</span><strong>${fmt(run.results?.total_tokens)}</strong></div><div><span>Model latency</span><strong>${nr(run.results?.model_seconds)}s</strong></div><p>Single-agent vs. multi-agent safe-plan rate is not shown because no equivalent baseline has been recorded.</p></div></details>`;
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
    body = `<p><code>${esc(plan.plan_id)}</code></p>
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
  const claim = (c) => `<li><strong>${esc(c.role)}</strong> <span class="muted">${esc(c.kind || "")}</span>: ${esc(c.text)}</li>`;
  const perAttempt = run.attempts.filter((a) => a.claims.length).map((a) =>
    `<h2>${esc(short(a.attempt_id))}</h2><ul>${a.claims.map(claim).join("")}</ul>`).join("");
  const inv = run.claims_without_attempt;
  const any = perAttempt || inv.length;
  return `${runHeader(run, "agents")}
    ${any ? `<p class="muted">Unstructured v0 claims: free text from agent outputs, no cited evidence IDs.</p>` : `<p class="muted">No agent output in this run.</p>`}
    ${perAttempt}
    ${inv.length ? `<details><summary>Investigator findings (${inv.length}, before any attempt)</summary><ul>${inv.map(claim).join("")}</ul></details>` : ""}`;
}

async function pageMetrics(id) {
  const run = await getRun(id);
  crumbs([["Runs", "#/"], [id, `#/run/${enc(id)}`], ["Metrics"]]);
  const r = run.results || {};
  const calls = (r.model_calls || []).map((c) =>
    `<tr><td>${val(c.role)}</td><td>${nr(c.seconds)}</td><td>${nr(c.total_tokens)}</td></tr>`).join("");
  return `${runHeader(run, "metrics")}
    <div class="card">${kv([
      ["model", nr(r.model)], ["endpoint", nr(r.base_url)], ["checkpoint", nr(r.checkpoint_revision)],
      ["serving backend", nr(r.serving_backend)], ["hardware", nr(r.hardware)],
      ["wall time (s)", nr(r.elapsed_s)], ["model calls", nr(r.model_call_count)],
      ["model time (s)", nr(r.model_seconds)],
      ["tokens (in / out / total)", r.total_tokens == null ? nr(null) : `${r.prompt_tokens} / ${r.completion_tokens} / ${r.total_tokens}`],
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

async function openEvidence(oid) {
  state.lastFocus = document.activeElement;
  const drawer = $("#evidence-drawer");
  const backdrop = $("#drawer-backdrop");
  const content = $("#drawer-content");
  content.innerHTML = `<div class="drawer-loading">Loading recorded evidence...</div>`;
  drawer.classList.add("open"); drawer.setAttribute("aria-hidden", "false"); backdrop.hidden = false;
  $("#drawer-close").focus();
  try {
    const o = await api(`/api/observation?id=${enc(oid)}`);
    const steps = o.steps.length ? `<div class="drawer-steps">${o.steps.map((s, i) => `<div class="drawer-step ${s.ok === 1 ? "ok" : s.ok === 0 ? "bad" : ""}"><span>${i + 1}</span><div><strong>${val(s.app_version)} / ${val(s.op)}</strong><small>${s.error ? esc(s.error) : s.value == null ? "No value recorded" : esc(s.value)}</small></div></div>`).join("")}</div>` : `<p class="muted">Database- or runner-level check; no app steps recorded.</p>`;
    const expected = o.expectation ?? o.raw?.expected ?? o.raw?.msg;
    const observed = o.outcome ?? o.error ?? o.raw?.observed ?? (o.raw?.old_alive !== undefined ? `old_alive=${o.raw.old_alive}` : null);
    const versions = o.app_versions?.length ? o.app_versions.join(", ") : null;
    content.innerHTML = `<span class="eyebrow">RECORDED EVIDENCE</span><div class="row drawer-title"><h2>${esc(o.check_name)}</h2>${verdictBadge(o.status)}</div>
      <div class="scope-grid"><div><span>Attempt</span><strong>${val(short(o.attempt_id))}</strong></div><div><span>Stage</span><strong>${val(o.stage_id)}</strong></div><div><span>Application version</span><strong>${val(versions, "not recorded")}</strong></div><div><span>Application commit</span><strong>${val(o.raw?.application_commit, "not recorded")}</strong></div><div><span>Schema fingerprint</span><strong>${val(o.raw?.schema_fingerprint ?? o.schema_state, "not recorded")}</strong></div><div><span>Snapshot ID</span><strong>${val(o.raw?.snapshot_id, "not recorded")}</strong></div><div><span>Scope</span><strong>${val(o.scope_source)}</strong></div><div><span>Provenance</span><strong>${val(o.executed_by)}</strong></div></div>
      <div class="evidence-outcomes"><div><span>Expected result</span><p>${val(expected, "not recorded")}</p></div><div><span>Observed result</span><p>${val(observed, "not recorded")}</p></div></div>
      <h3>Step trace</h3>${steps}${o.error ? `<h3>Error</h3><pre>${esc(o.error)}</pre>` : ""}
      <details><summary>Raw observation</summary><pre>${esc(JSON.stringify(o.raw, null, 2))}</pre></details>
      <div class="row"><a class="button ghost drawer-link" href="#/obs/${enc(o.observation_id)}">Open full evidence page</a>${o.raw?.log_link ? `<a class="button ghost drawer-link" href="${esc(o.raw.log_link)}">Open log</a>` : `<span class="muted drawer-link">Log link not recorded</span>`}</div>`;
  } catch (err) { content.innerHTML = `<div class="notice">Failed to load evidence: ${esc(err.message)}</div>`; }
}

function closeDrawer() {
  $("#evidence-drawer").classList.remove("open");
  $("#evidence-drawer").setAttribute("aria-hidden", "true");
  $("#drawer-backdrop").hidden = true;
  state.lastFocus?.focus?.();
}

function showToast(message, tone = "info") {
  const toast = $("#toast");
  toast.textContent = message;
  toast.className = `toast show ${tone}`;
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => { toast.className = "toast"; }, 2800);
}

function openTour() {
  state.lastFocus = document.activeElement;
  const tour = $("#welcome-tour");
  tour.hidden = false;
  requestAnimationFrame(() => { tour.classList.add("open"); tour.querySelector("[data-tour-start]")?.focus(); });
}

function closeTour() {
  const tour = $("#welcome-tour");
  tour.classList.remove("open");
  localStorage.setItem("migrationlab-tour-seen", "1");
  setTimeout(() => { tour.hidden = true; }, 220);
  state.lastFocus?.focus?.();
}

function trapFocus(container, ev) {
  if (ev.key !== "Tab") return;
  const items = [...container.querySelectorAll('a[href],button:not([disabled]),select:not([disabled]),[tabindex]:not([tabindex="-1"])')];
  if (!items.length) return;
  const first = items[0], last = items[items.length - 1];
  if (ev.shiftKey && document.activeElement === first) { ev.preventDefault(); last.focus(); }
  else if (!ev.shiftKey && document.activeElement === last) { ev.preventDefault(); first.focus(); }
}

function celebrate(target) {
  if (matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  const colors = ["#61dafb", "#8b5cf6", "#57e39b", "#f6c453"];
  for (let i = 0; i < 22; i += 1) {
    const spark = document.createElement("i");
    spark.className = "celebration-spark";
    spark.style.setProperty("--x", `${(Math.random() - .5) * 340}px`);
    spark.style.setProperty("--y", `${-50 - Math.random() * 180}px`);
    spark.style.setProperty("--c", colors[i % colors.length]);
    target.appendChild(spark);
    setTimeout(() => spark.remove(), 950);
  }
}

function filterRuns() {
  const q = ($("#run-search")?.value || "").toLowerCase();
  const verdict = $("#verdict-filter")?.value || "";
  const kind = $("#kind-filter")?.value || "";
  document.querySelectorAll(".run-row").forEach((row) => {
    row.hidden = !(row.dataset.search.includes(q) && (!verdict || row.dataset.verdict === verdict) && (!kind || row.dataset.kind === kind));
  });
}

function rerenderReplay(delta = 0, exact = null) {
  if (exact !== null) state.replayStep = exact;
  else state.replayStep = Math.max(0, state.replayStep + delta);
  route();
}

// ---------- router ----------

async function route() {
  const parts = location.hash.replace(/^#\/?/, "").split("/").filter(Boolean).map(decodeURIComponent);
  const view = $("#view");
  try {
    let html;
    if (!parts.length) html = await pageRuns();
    else if (parts[0] === "demo" && parts.length === 2) html = await pageDemo(parts[1]);
    else if (parts[0] === "workspace" && parts.length === 2) html = await pageWorkspace(parts[1]);
    else if (parts[0] === "run" && parts.length === 2) html = await pageOverview(parts[1]);
    else if (parts[0] === "run" && parts[2] === "plan") html = await pagePlan(parts[1], parts[3] || "a1");
    else if (parts[0] === "run" && parts[2] === "matrix") html = await pageMatrix(parts[1], parts[3] || "a1");
    else if (parts[0] === "run" && parts[2] === "agents") html = await pageAgents(parts[1]);
    else if (parts[0] === "run" && parts[2] === "metrics") html = await pageMetrics(parts[1]);
    else if (parts[0] === "obs") html = await pageEvidence(parts.slice(1).join("/"));
    else if (parts[0] === "compare" && parts.length === 3) html = await pageCompare(parts[1], parts[2]);
    else html = `<p>Page not found. <a href="#/">Back to runs</a></p>`;
    view.innerHTML = html;
    document.querySelectorAll("[data-nav]").forEach((link) => link.classList.remove("active"));
    const navKey = parts[0] === "workspace" ? "workspace" : parts[0] === "compare" ? "compare" : parts[0] === "demo" ? "demo" : "runs";
    document.querySelector(`[data-nav="${navKey}"]`)?.classList.add("active");
    window.scrollTo(0, 0);
    if (parts[0] === "workspace" && !localStorage.getItem("migrationlab-tour-seen")) requestAnimationFrame(openTour);
  } catch (err) {
    view.innerHTML = `<div class="card">Failed to load: ${esc(err.message)}</div>`;
  }
}

document.addEventListener("click", (ev) => {
  if (ev.target.closest("[data-open-tour]")) { openTour(); return; }
  if (ev.target.closest("[data-tour-close]")) { closeTour(); return; }
  if (ev.target.closest("[data-tour-start]")) {
    closeTour(); state.workspaceStage = null; route();
    setTimeout(() => showToast("Start on the left: choose a migration stage, then open a result."), 150);
    return;
  }
  if (ev.target.closest("[data-next-stage]")) {
    const stages = [...document.querySelectorAll(".workspace-stage")];
    const current = stages.findIndex((x) => x.classList.contains("active"));
    const next = stages[(current + 1) % stages.length];
    if (next) { state.workspaceStage = next.dataset.stageSelect; route(); showToast(current === stages.length - 1 ? "Journey restarted." : "Stage advanced. Now inspect its scoped evidence."); }
    return;
  }
  if (ev.target.closest("[data-reveal-revision]")) {
    const target = $("#revision-story");
    if (target) target.open = true;
    target?.scrollIntoView({behavior: "smooth", block: "center"});
    if (target) { target.classList.add("revealed"); celebrate(target); }
    showToast("The safe plan keeps the failed attempt visible.", "success");
    return;
  }
  const stageSelect = ev.target.closest("[data-stage-select]");
  if (stageSelect) { state.workspaceStage = stageSelect.dataset.stageSelect; route(); showToast(`Selected: ${stageSelect.title || "migration stage"}`); return; }
  if (ev.target.closest("[data-reset-workspace]")) { state.workspaceStage = null; route(); showToast("Replay state reset. No database was changed."); return; }
  if (ev.target.closest("[data-replay-selected]")) { const row = document.querySelector(`[data-workspace-row][data-stage="${CSS.escape(state.workspaceStage || "")}"]`); row?.scrollIntoView({behavior: "smooth", block: "center"}); row?.classList.add("spotlight"); setTimeout(() => row?.classList.remove("spotlight"), 1200); showToast("Showing recorded results for the selected step."); return; }
  if (ev.target.closest("[data-focus-checks]")) { $("#recorded-checks")?.focus(); $("#recorded-checks")?.scrollIntoView({behavior: "smooth", block: "center"}); showToast("Choose a scoped result to inspect its evidence."); return; }
  if (ev.target.closest("[data-scroll-runs]")) {
    document.querySelector("#runs-table")?.scrollIntoView({behavior: "smooth", block: "start"});
    return;
  }
  const obs = ev.target.closest("[data-obs]");
  if (obs) { openEvidence(obs.dataset.obs); return; }
  if (ev.target.closest("#drawer-close") || ev.target.id === "drawer-backdrop") { closeDrawer(); return; }
  const replayDot = ev.target.closest("[data-replay-step]");
  if (replayDot) { rerenderReplay(0, Number(replayDot.dataset.replayStep)); return; }
  if (ev.target.closest("#replay-prev")) { rerenderReplay(-1); return; }
  if (ev.target.closest("#replay-next")) { rerenderReplay(1); return; }
  if (ev.target.closest("#replay-play")) {
    if (state.replayTimer) { clearInterval(state.replayTimer); state.replayTimer = null; route(); }
    else { state.replayTimer = setInterval(() => { state.replayStep += 1; route(); }, state.replaySpeed); route(); }
    return;
  }
  if (ev.target.closest("#theme-toggle")) {
    const root = document.documentElement;
    root.dataset.theme = root.dataset.theme === "light" ? "dark" : "light";
    localStorage.setItem("migrationlab-theme", root.dataset.theme); return;
  }
  if (ev.target.closest("a, input, button, summary")) return;
  const row = ev.target.closest("[data-href]");
  if (row) location.hash = row.dataset.href;
});
document.addEventListener("change", (ev) => {
  if (["workspace-version", "workspace-stage", "workspace-status"].includes(ev.target.id)) {
    const version = $("#workspace-version")?.value || "";
    const stage = $("#workspace-stage")?.value || "";
    const status = $("#workspace-status")?.value || "";
    document.querySelectorAll("[data-workspace-row]").forEach((row) => { row.hidden = !((!version || row.dataset.version.includes(version)) && (!stage || row.dataset.stage === stage) && (!status || row.dataset.status === status)); });
    return;
  }
  if (["verdict-filter", "kind-filter"].includes(ev.target.id)) { filterRuns(); return; }
  if (ev.target.id === "replay-speed") {
    state.replaySpeed = Number(ev.target.value);
    if (state.replayTimer) { clearInterval(state.replayTimer); state.replayTimer = setInterval(() => { state.replayStep += 1; route(); }, state.replaySpeed); }
    return;
  }
  if (!ev.target.classList.contains("pick")) return;
  const picked = [...document.querySelectorAll(".pick:checked")].map((x) => x.value);
  const btn = $("#compare");
  btn.disabled = picked.length !== 2;
  btn.onclick = () => { location.hash = `#/compare/${enc(picked[0])}/${enc(picked[1])}`; };
});
document.addEventListener("input", (ev) => { if (ev.target.id === "run-search") filterRuns(); });
document.addEventListener("keydown", (ev) => {
  const tour = $("#welcome-tour");
  const drawer = $("#evidence-drawer");
  if (!tour.hidden) trapFocus(tour, ev);
  else if (drawer.classList.contains("open")) trapFocus(drawer, ev);
  if (ev.key === "Escape") { if (!tour.hidden) closeTour(); else if (drawer.classList.contains("open")) closeDrawer(); }
  if (location.hash.startsWith("#/demo/") && ev.key === "ArrowRight") rerenderReplay(1);
  if (location.hash.startsWith("#/demo/") && ev.key === "ArrowLeft") rerenderReplay(-1);
  if (location.hash.startsWith("#/workspace/") && ["ArrowRight", "ArrowLeft"].includes(ev.key) && !ev.target.matches("input,select,textarea")) {
    const stages = [...document.querySelectorAll(".workspace-stage")];
    const current = stages.findIndex((x) => x.classList.contains("active"));
    const delta = ev.key === "ArrowRight" ? 1 : -1;
    const next = stages[Math.max(0, Math.min(stages.length - 1, current + delta))];
    if (next && next !== stages[current]) { state.workspaceStage = next.dataset.stageSelect; route(); }
  }
});
window.addEventListener("hashchange", route);
document.documentElement.dataset.theme = localStorage.getItem("migrationlab-theme") || "dark";
route();
