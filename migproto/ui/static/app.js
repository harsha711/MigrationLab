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
const state = { replayTimer: null, batchPoll: null, replayStep: 0, replaySpeed: 1400, workspaceStage: null, tourStep: 0, dashboardRunId: null };

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
  return `<section class="run-page-header"><span class="eyebrow">RECORDED MIGRATION REVIEW</span><div class="row"><h1>${esc(id)}</h1>${verdictBadge(run.meta?.verdict)}</div>
    <div class="row">${typeBadges(run)}</div>
    <nav class="tabs">${tabs.map(([l, h, k]) => `<a href="${h}" class="${k === active ? "active" : ""}">${l}</a>`).join("")}</nav></section>`;
}

function attemptPills(run, page, current) {
  return `<div class="pills">${run.attempts.map((a) => {
    const s = short(a.attempt_id);
    const cls = [s === current ? "active" : "", a.ok === 0 ? "bad" : a.ok === 1 ? "ok" : ""].join(" ");
    return `<a class="pill ${cls}" href="#/run/${enc(run.run_id)}/${page}/${s}">${esc(s)} ${a.status ? "· " + esc(a.status) : ""}</a>`;
  }).join("")}</div>`;
}

const findAttempt = (run, s) => run.attempts.find((a) => short(a.attempt_id) === s);
const humanize = (value) => String(value || "unknown").replace(/^case\./, "").replace(/[._-]+/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
const reviewUnavailableReason = (reason) => {
  const text = String(reason || "");
  if (/fcntl|execution lock unavailable/i.test(text)) {
    return "Live review requires Linux or macOS because the execution lock uses fcntl. Recorded replay remains available.";
  }
  if (/controller not available/i.test(text)) {
    return "The live-review controller is not connected. Recorded replay remains available.";
  }
  return text || "Live review is not available. Recorded replay remains available.";
};

// ---------- pages ----------

async function pageRuns() {
  crumbs([["Runs"]]);
  const { runs, controller_available, controller_reason } = await api("/api/runs");
  const details = await Promise.all(runs.map((r) => getRun(r.run_id)));
  const defaultRun = details.find((d) => d.run_id === "live-ordering-revision") || details[0];
  const demo = details.find((d) => d.run_id === state.dashboardRunId) || defaultRun;
  state.dashboardRunId = demo?.run_id || null;
  const finalAttempt = demo?.attempts.at(-1);
  const firstAttempt = demo?.attempts[0];
  const matrix = finalAttempt ? await api(`/api/matrix?run=${enc(demo.run_id)}&attempt=${enc(finalAttempt.attempt_id)}`) : {rows: [], db_checks: []};
  const rows = details.map((d) => {
    const failed = d.attempts.reduce((n, a) => n + a.checks_failed, 0);
    const search = `${d.run_id} ${d.meta?.verdict || ""} ${d.source_kind} ${d.meta?.model || "scripted"}`.toLowerCase();
    return `<tr class="link run-row" data-href="#/run/${enc(d.run_id)}" data-search="${esc(search)}" data-verdict="${esc(d.meta?.verdict || "")}" data-kind="${esc(d.source_kind)}">
      <td><input type="checkbox" class="pick" value="${esc(d.run_id)}" aria-label="select for compare"></td>
      <td><a href="#/run/${enc(d.run_id)}"><strong>${esc(d.run_id)}</strong></a><small class="cell-sub">${d.attempts.length} attempt${d.attempts.length === 1 ? "" : "s"}</small></td>
      <td>${verdictBadge(d.meta?.verdict)}</td><td>${typeBadges(d)}</td>
      <td>${d.attempts.length}</td><td>${d.meta?.checks_total ?? ""}</td><td>${failed}</td></tr>`;
  }).join("");
  const processStages = [
    "Review request",
    "Staged plan",
    "Runner checks",
    "Tested outcome"
  ];
  const stages = processStages.map((title, index) => `<div class="editorial-stage done"><div><span>${index + 1}</span><strong>${esc(title)}</strong></div><i>${index < processStages.length - 1 ? "→" : ""}</i></div>`).join("");
  const paths = (matrix.columns || []).map((column) => [column, column.replaceAll("new-staged", "new").replace(" -> ", " → ")]);
  const checks = [...new Set(matrix.rows.map((item) => item.check_name))]
    .map((name) => [name, new Set(matrix.rows.filter((item) => item.check_name === name).map((item) => item.column)).size])
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])).slice(0, 5).map(([name]) => name);
  const scopedResults = [...new Map(matrix.rows.map((item) => [
    `${item.stage_id || "unknown"}|${item.check_name}|${item.column}`,
    item
  ])).values()];
  const passedResults = scopedResults.filter((item) => kindOf(item) === "PASS").length;
  const reviewResults = scopedResults.filter(isBad).length;
  const passRate = scopedResults.length ? Math.round((passedResults / scopedResults.length) * 100) : null;
  const crossVersionResults = scopedResults.filter((item) => {
    const [writer, reader] = String(item.column || "").split(" -> ");
    return writer && reader && writer !== reader;
  });
  const crossVersionPassed = crossVersionResults.filter((item) => kindOf(item) === "PASS").length;
  const matrixMetrics = `<div class="matrix-metrics" aria-label="Recorded compatibility metrics">
    <div><span>Scoped results</span><strong>${scopedResults.length}</strong><small>stage · check · path</small></div>
    <div><span>Pass rate</span><strong>${passRate == null ? "—" : `${passRate}%`}</strong><small>${passedResults} of ${scopedResults.length} recorded</small></div>
    <div class="${reviewResults ? "metric-alert" : "metric-ok"}"><span>Needs review</span><strong>${reviewResults}</strong><small>failed, refused, or errored</small></div>
    <div><span>Cross-version</span><strong>${crossVersionResults.length ? `${crossVersionPassed}/${crossVersionResults.length}` : "—"}</strong><small>recorded paths passed</small></div>
  </div>`;
  const matrixGrid = `<div class="matrix-corner"></div>${paths.map(([, label]) => `<b>${esc(label)}</b>`).join("")}` + checks.map((checkName) => {
    const cells = paths.map(([column]) => {
      const candidates = matrix.rows.filter((item) => item.column === column && item.check_name === checkName);
      const item = candidates.find(isBad) || candidates[0];
      if (!item) return `<span class="editorial-matrix-cell unknown" title="No recorded observation for this exact scope">—</span>`;
      const status = isBad(item) ? "fail" : kindOf(item) === "PASS" ? "pass" : "unknown";
      const mark = status === "pass" ? "✓" : status === "fail" ? "×" : "—";
      return `<button class="editorial-matrix-cell ${status}" data-obs="${esc(item.observation_id)}" title="${esc(item.check_name)}"><b>${mark}</b></button>`;
    }).join("");
    return `<strong title="${esc(checkName)}">${humanize(checkName)}</strong>${cells}`;
  }).join("");
  const evidenceItems = (matrix.db_checks || []).filter((item) => item.stage_id && item.stage_id !== "S0");
  const evidence = (finalAttempt?.stages || []).map((stage) => {
    const item = evidenceItems.find((candidate) => candidate.stage_id === stage.stage_id);
    const detail = item ? `${esc(item.check_name)} · recorded observation` : esc(stage.description || stage.phase || stage.stage_kind || "Recorded stage");
    const content = `<i class="${item && isBad(item) ? "bad" : "ok"}">${item && isBad(item) ? "×" : "✓"}</i><span><strong>${stageTitle(stage)}</strong><small>${detail}</small></span><b>›</b>`;
    return item ? `<a class="editorial-evidence-item" href="#/obs/${enc(item.observation_id)}">${content}</a>` : `<div class="editorial-evidence-item">${content}</div>`;
  }).join("");
  const attempts = (demo?.attempts || []).map((attempt) => {
    const passed = attempt.ok === 1;
    const label = attempt.status || (passed ? "PASS" : attempt.ok === 0 ? "FAIL" : "RECORDED");
    const detail = attempt.halted || `${attempt.checks_total} recorded checks · ${attempt.checks_failed} failed`;
    return `<article class="${passed ? "passed" : "blocked"}"><i>${passed ? "✓" : "×"}</i><div><small>${humanize(short(attempt.attempt_id))} · ${esc(label)}</small><strong>${esc(detail)}</strong></div><a href="#/run/${enc(demo.run_id)}/matrix/${esc(short(attempt.attempt_id))}">Recorded evidence</a></article>`;
  }).join(`<span>→</span>`);
  const verdict = demo?.meta?.verdict || finalAttempt?.status || "unknown";
  const finalPassed = ["PASS", "REPRODUCED"].includes(String(verdict).toUpperCase());
  const runOptions = details.map((item) => `<option value="${esc(item.run_id)}" ${item.run_id === demo?.run_id ? "selected" : ""}>${esc(item.run_id)} · ${esc(item.meta?.verdict || "unknown")}</option>`).join("");
  return `<header class="editorial-head"><div><span class="eyebrow">RECORDED RUNS / EVIDENCE</span><h1>Migration evidence</h1><p>Follow each plan, runner check, and revision from request to tested outcome.</p></div><div class="editorial-head-actions"><select id="dashboard-run" class="run-selector" aria-label="Select recorded run">${runOptions}</select><span class="scope-label">Scope · sequential interleaving</span></div></header>
    <section class="editorial-layout">
      <main class="editorial-main">
        <section class="stage-panel"><div class="stage-panel-title"><span class="eyebrow">HOW THE REVIEW WORKS</span><span>Evidence follows the runner</span></div><div class="editorial-stages">${stages || `<p class="muted">No stages recorded.</p>`}</div></section>
        <section class="attempt-transition ${demo?.attempts.length === 1 ? "single" : ""}" aria-label="Attempt revision history">${attempts || `<p class="muted">No attempt records available.</p>`}</section>
        <section class="editorial-panel matrix-editorial"><header><div><h2>Old / New compatibility</h2><p>Results from automated and recorded checks across each application path.</p></div><span class="recorded-view">Recorded checks</span></header>${matrixMetrics}<div class="editorial-matrix" style="--matrix-columns:${Math.max(paths.length, 1)}" role="group" aria-label="Application compatibility matrix">${matrixGrid || `<p class="muted">No application-scoped checks were recorded.</p>`}</div><p class="matrix-legend">Metrics cover recorded final-attempt application paths only. — means this exact check and application path was not recorded; missing scope remains unknown.</p></section>
      </main>
      <aside class="editorial-panel evidence-rail"><header><span class="rail-verdict ${finalPassed ? "" : "failed"}">${finalPassed ? "✓" : "×"}</span><div><h2>${humanize(short(finalAttempt?.attempt_id))} · ${esc(verdict)}</h2><p>${demo?.source_kind === "fixture" ? "Recorded fixture" : "Local recorded run"}</p></div></header><dl><div><dt>Run</dt><dd>${esc(demo?.run_id || "unknown")}</dd></div><div><dt>Elapsed</dt><dd>${demo?.meta?.elapsed_s == null ? "not recorded" : `${esc(demo.meta.elapsed_s)} s`}</dd></div><div><dt>Checks</dt><dd>${fmt(finalAttempt?.checks_total)} recorded</dd></div><div><dt>Model</dt><dd>${esc(demo?.meta?.model || "scripted / not recorded")}</dd></div></dl><h3 class="rail-section-title">Stage evidence</h3><div class="editorial-evidence-list">${evidence || `<p class="muted">No database-level evidence recorded.</p>`}</div><footer><strong>Key observations</strong><p>${fmt(finalAttempt?.checks_total)} checks were recorded for the final attempt.</p><p>${demo?.attempts.length || 0} attempt${demo?.attempts.length === 1 ? "" : "s"} remain visible.</p><p>Missing scope is displayed as unknown.</p></footer></aside>
    </section>
    <section id="runs-table" class="section-head"><div><span class="eyebrow">REVIEW LIBRARY</span><h2>Recorded migration runs</h2></div>
      <div class="row"><button id="compare" disabled>Compare selected</button><a class="button ${controller_available ? "primary" : "ghost"}" href="#/review" title="${esc(controller_available ? "Start a review through the controller" : reviewUnavailableReason(controller_reason))}">Run Review</a></div></section>
    <div class="filterbar"><input id="run-search" type="search" placeholder="Search runs, model, verdict..." aria-label="Search runs">
      <select id="verdict-filter" aria-label="Filter by verdict"><option value="">All verdicts</option><option>PASS</option><option>FAIL</option><option>REPRODUCED</option><option>ERROR</option></select>
      <select id="kind-filter" aria-label="Filter by source"><option value="">All sources</option><option value="fixture">Recorded fixtures</option><option value="local_run">Local runs</option></select></div>
    <div class="card table-wrap runs-card"><table>
      <tr><th></th><th>run</th><th>verdict</th><th>type</th><th>attempts</th><th>checks</th><th>failed</th></tr>
      ${rows || `<tr><td colspan="7" class="muted">No runs in examples/ or runs/.</td></tr>`}
    </table></div>
    ${controller_available ? "" : `<div class="notice"><strong>Why is Run Review unavailable?</strong><span>${esc(reviewUnavailableReason(controller_reason))}</span></div>`}`;
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

function workspaceMark(result) {
  const icon = result.kind === "pass" ? "&#10003;" : result.kind === "fail" ? "&#10005;" : "&#183;";
  const attrs = result.obs ? ` data-obs="${esc(result.obs)}" title="Open TestEvidence"` : " disabled title=\"No scoped observation recorded\"";
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
  const timeline = stages.map((s, i) => `<button class="workspace-stage ${s.stage_id === selectedId ? "active" : ""}" data-stage-select="${esc(s.stage_id)}"><span>${i + 1}</span><div><strong>${stageTitle(s)}</strong><small>${esc(s.description || s.stage_kind || "")}</small></div><i>${s.phase ? esc(s.phase) : esc(s.stage_kind)}</i></button>`).join("");
  const rows = stages.map((s) => {
    const old = matrixStatus(matrix.rows, s.stage_id, "old");
    const newer = matrixStatus(matrix.rows, s.stage_id, "new-staged");
    return `<tr data-workspace-row data-stage="${esc(s.stage_id)}" data-status="${old.kind === "fail" || newer.kind === "fail" ? "FAIL" : old.kind === "pass" || newer.kind === "pass" ? "PASS" : "UNKNOWN"}" data-version="${esc((matrix.rows.filter((r) => r.stage_id === s.stage_id).flatMap((r) => r.path || [])).join(" "))}"><td><strong>${stageTitle(s)}</strong><small>${esc(s.phase || s.stage_kind)}</small></td><td>${workspaceMark(old)}</td><td>${workspaceMark(newer)}</td></tr>`;
  }).join("");
  const trace = [
    ["01", "Investigator", investigator?.text || "No structured investigator conclusion recorded.", run.claims_without_attempt?.length || 0],
    ["02", "Planner", planner?.text || revised?.plans?.[0]?.body?.summary || "No planner claim recorded.", calls.filter((c) => c.role?.startsWith("planner")).length],
    ["03", "Tester", tester?.text || first?.halted || "No tester conclusion recorded.", calls.filter((c) => c.role?.startsWith("tester")).length],
    ["04", "Feedback", feedback?.text || "No revision feedback recorded.", run.attempts.length - 1]
  ].map(([n, role, text, count]) => `<article class="trace-step"><b>${n}</b><div><span>${role}</span><p>${esc(text)}</p><small>${role === "Feedback" ? `${count} revision round(s)` : `${count} recorded ${role === "Investigator" ? "finding(s)" : "model call(s)"}`}</small></div></article>`).join("");
  return `<div class="workspace-head"><div><span class="eyebrow">INTERACTIVE COMPATIBILITY REVIEW</span><h1>${esc(id)}</h1><p>Explore recorded plan decisions and runner evidence. Selecting a result opens its scoped TestEvidence.</p></div><div class="workspace-actions"><button class="button ghost" data-open-tour>&#10024; Guide me</button><button class="button primary" data-reveal-revision>Reveal safe plan</button><div class="row">${verdictBadge(run.meta?.verdict)} ${typeBadges(run)}</div></div></div>
    <section class="journey-strip"><div><span>Migration journey</span><strong>Step ${selectedIndex + 1} of ${stages.length}</strong></div><div class="journey-track"><i style="width:${stages.length ? ((selectedIndex + 1) / stages.length) * 100 : 0}%"></i>${stages.map((s, i) => `<button class="${i <= selectedIndex ? "visited" : ""}" data-stage-select="${esc(s.stage_id)}" title="${stageTitle(s)}"><span>${i + 1}</span></button>`).join("")}</div><div class="journey-now"><span class="stage-pulse"></span><p><small>YOU ARE HERE</small><strong>${stageTitle(selectedStage)}</strong></p><button data-next-stage>${selectedIndex === stages.length - 1 ? "Restart journey" : "Next stage"} &#8594;</button></div></section>
    <section class="workspace-layout">
      <aside class="workspace-panel plan-panel"><div class="panel-heading"><div><span class="eyebrow">MIGRATION PLAN</span><h2>Safe rollout timeline</h2></div><span class="counter">${stages.length} steps</span></div>${timeline}</aside>
      <main class="workspace-panel matrix-panel" data-tour-target="matrix"><div class="panel-heading"><div><span class="eyebrow">OLD VS. NEW</span><h2>Compatibility matrix</h2><p class="panel-help">Click any pass or fail result to inspect the evidence behind it.</p></div><span class="source-pill"><i></i> Recorded evidence</span></div>
        <div class="scope-filters"><select id="workspace-version"><option value="">All app versions</option><option value="old">Old application</option><option value="new-staged">New application</option></select><select id="workspace-stage"><option value="">All stages</option>${stages.map((s) => `<option value="${esc(s.stage_id)}">${stageTitle(s)}</option>`).join("")}</select><select id="workspace-status"><option value="">All statuses</option><option value="PASS">Passed</option><option value="FAIL">Failed</option><option value="UNKNOWN">Not tested</option></select></div>
        <div class="compat-table"><table><thead><tr><th>Migration stage</th><th>Old application</th><th>New application</th></tr></thead><tbody>${rows}</tbody></table></div>
        <div class="scope-callout"><b>Why scope matters</b><p>A pass for the new application does not prove the old application can read or write the same schema. Every cell is derived from observations scoped to an application path and migration stage.</p></div>
      </main>
      <aside class="workspace-panel insight-panel"><div class="panel-heading"><div><span class="eyebrow">AGENT TRACE</span><h2>Reasoning handoffs</h2></div><span class="counter">${run.attempts.length - 1} revision</span></div><div class="trace-list">${trace}</div>
        <article class="recommendation"><span>FINAL RECOMMENDATION</span><h3>Do not deploy an immediate rename</h3><dl><dt>Reason</dt><dd>${esc(first?.halted || "The recorded first plan failed a mandatory runner check.")}</dd><dt>Evidence</dt><dd>${failed ? `<button data-obs="${esc(failed.observation_id)}">${esc(failed.observation_id)}</button>` : unknown("not recorded")}</dd><dt>Safe alternative</dt><dd>Additive field + dual-write + backfill + retire old app before contract.</dd></dl></article></aside>
    </section>
    <section class="revision-section" id="revision-story"><div class="panel-heading"><div><span class="eyebrow">FAILED PLAN &#8594; REVISED PLAN</span><h2>The failure stays visible</h2></div><span class="source-pill">${run.attempts.length} attempts</span></div><div class="revision-grid">${planColumn(first, "Attempt 1 · unsafe ordering", "unsafe")}<div class="revision-evidence"><span>RUNNER FAILURE</span><strong>${esc(failed?.check_name || first?.failed?.[0] || "Recorded failure")}</strong><p>${esc(first?.halted || "The first plan did not complete.")}</p>${failed ? `<button class="button ghost" data-obs="${esc(failed.observation_id)}">Inspect evidence</button>` : ""}<hr><span>AGENT FEEDBACK</span><p>${esc(feedback?.text || "No revision note recorded.")}</p></div>${planColumn(revised, "Attempt 2 · revised ordering", "safe")}</div></section>
    <section class="evaluation-strip"><div><span class="eyebrow">EVALUATION RESULTS</span><h2>Recorded measurements</h2></div><div><span>Plan attempts</span><strong>${run.attempts.length}</strong></div><div><span>Final test-pass rate</span><strong>${revised?.checks_total ? Math.round((revised.checks_total - revised.checks_failed) / revised.checks_total * 100) : 0}%</strong></div><div><span>Token usage</span><strong>${fmt(run.results?.total_tokens)}</strong></div><div><span>Model latency</span><strong>${nr(run.results?.model_seconds)}s</strong></div><p>Single-agent vs. multi-agent safe-plan rate is not shown because no equivalent baseline has been recorded.</p></section>`;
}

async function pageReview() {
  crumbs([["Runs", "#/"], ["Run Review"]]);
  const status = await api("/api/review/status");
  const current = status.current;
  const running = current && current.state === "running";
  const unavailable = status.ok ? "" : reviewUnavailableReason(status.reason);
  return `<h1>Run Review</h1><p class="muted">Starts one review through the controller, which calls the runner. The UI never runs SQL or starts Saleor directly.</p>
    ${current ? `<div class="card">Last review: <a href="#/review/${enc(current.run_id)}">${esc(current.run_id)}</a> ${running ? `<span class="badge">running</span>` : verdictBadge(current.verdict)} ${current.error ? `<span class="muted">${esc(current.error)}</span>` : ""}</div>` : ""}
    <div class="card">${status.ok ? "" : `<p><span class="badge warn">unavailable</span> ${esc(unavailable)}</p>`}<div class="row">
      <label>Proposals <select id="rv-mode"><option value="scripted">scripted (no model)</option><option value="llm">llm (configured endpoint)</option></select></label>
      <label>Topology <select id="rv-topology"><option value="roles">roles</option><option value="single">single agent</option></select></label>
      <button id="rv-start" ${status.ok && !running ? "" : "disabled"}>Start</button></div><p id="rv-msg" class="muted"></p></div>`;
}

async function startReview() {
  const msg = $("#rv-msg");
  msg.textContent = "Starting…";
  const response = await fetch("/api/review", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({mode: $("#rv-mode").value, topology: $("#rv-topology").value})});
  const body = await response.json();
  if (response.status === 202) { location.hash = `#/review/${enc(body.run_id)}`; return; }
  msg.textContent = body.error + (body.owner ? ` (${JSON.stringify(body.owner)})` : "");
}

async function pageReviewLive(runId) {
  crumbs([["Runs", "#/"], ["Run Review", "#/review"], [runId]]);
  setTimeout(() => pollReview(runId, 0), 0);
  return `<div class="row"><h1>${esc(runId)}</h1><span id="rv-state" class="badge">running</span></div><p class="muted">Live events from the controller-owned run log.</p><div class="card table-wrap"><table id="rv-events"><tr><th>t (s)</th><th>kind</th><th>name</th><th>status</th></tr></table></div>`;
}

async function pollReview(runId, after) {
  if (location.hash !== `#/review/${enc(runId)}`) return;
  let data;
  try { data = await api(`/api/review/events?run=${enc(runId)}&after=${after}`); }
  catch (err) { $("#rv-state").textContent = "error"; return; }
  const table = $("#rv-events");
  if (!table) return;
  for (const event of data.events) {
    const row = table.insertRow();
    const outcome = event.kind === "check" ? kindOf(event) : "";
    row.innerHTML = `<td>${event.t == null ? "" : Number(event.t).toFixed(1)}</td><td>${esc(event.kind)}</td><td>${esc(event.name || event.stage || event.msg || "")}</td><td>${outcome ? verdictBadge(event.outcome || event.status) : ""}</td>`;
  }
  if (!data.done) { setTimeout(() => pollReview(runId, data.next), 1500); return; }
  const status = await api("/api/review/status");
  const done = status.current && status.current.run_id === runId ? status.current : null;
  runCache.delete(runId);
  $("#rv-state").outerHTML = `${verdictBadge(done ? done.verdict : "done")} <a href="#/run/${enc(runId)}">Open run</a>`;
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

// ---------- Brev batches (supervisor records, read-only) ----------

const BATCH_VERDICTS = ["PASS", "FAIL", "INCOMPLETE", "ERROR"];
const BATCH_POLL_MS = 20000;
const SCORE_LABEL = "scored against manual expected verdicts (never shown to agents)";
const scoreHead = `<th scope="col" class="score-head">matches expected<small>${SCORE_LABEL}</small></th>`;

const duration = (s) => {
  if (s == null) return unknown("not recorded");
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  return h ? `${h} h ${m} min` : m ? `${m} min ${sec} s` : `${sec} s`;
};
const clock = (iso) => iso ? esc(iso.replace("T", " ")) : unknown("not recorded");

function scoreCell(r) {
  if (r.matches_expected == null) return `<span class="muted" title="No manual expected verdict for this scenario, or an infrastructure failure">not scored</span>`;
  const t = r.expected ? `expected ${r.expected.verdict} with ${r.expected.recommendations.join(" or ")}; runner: ${r.verdict ?? "?"} with ${r.runner_recommendation ?? "?"}` : "";
  return `<span class="badge ${r.matches_expected ? "pass" : "fail"}" title="${esc(t)}">${r.matches_expected ? "yes" : "no"}</span>`;
}

function batchCounts(counts) {
  if (!counts.length) return `<p class="muted">No model results in this batch.</p>`;
  const other = (c) => Object.entries(c.other).map(([k, n]) => `${n} ${esc(k)}`).join(", ");
  return `<div class="table-wrap"><table class="batch-counts">
    <tr><th scope="col">scenario</th><th scope="col">topology</th>${BATCH_VERDICTS.map((v) => `<th scope="col" class="count">${v}</th>`).join("")}<th scope="col">other</th>${scoreHead}</tr>
    ${counts.map((c) => `<tr><th scope="row">${val(c.scenario, "not recorded")}</th><td>${val(c.topology, "not recorded")}</td>
      ${BATCH_VERDICTS.map((v) => `<td class="count ${c[v] ? v.toLowerCase() : "zero"}">${c[v]}</td>`).join("")}<td>${other(c) || ""}</td>
      <td class="count">${c.scored ? `${c.matched} of ${c.scored}` : `<span class="muted">not scored</span>`}</td></tr>`).join("")}
  </table></div>`;
}

function inProgressTable(b) {
  if (!b.in_progress.length) return "";
  const running = b.in_progress.filter((r) => r.status === "running");
  const lost = b.in_progress.filter((r) => r.status !== "running");
  const rows = (list) => list.map((r) => `<tr>
    <td>${val(r.n, "?")}</td><td>${val(r.scenario, "not recorded")}</td><td>${val(r.topology, "not recorded")}</td>
    <td>${val(r.worker, "not recorded")}</td><td>${clock(r.started_at)}</td>
    <td>${r.status === "running" ? `<span class="badge running">running</span> ${duration(r.elapsed_seconds)}` : `<span class="badge warn">no runs.jsonl row</span>`}</td></tr>`).join("");
  const table = (list) => `<div class="card table-wrap"><table class="batch-runs">
    <tr><th>#</th><th>scenario</th><th>topology</th><th>worker</th><th>started (supervisor clock)</th><th>status</th></tr>${rows(list)}</table></div>`;
  return `${running.length ? `<h2>Running now</h2><p class="muted">Started in supervisor.log, no runs.jsonl row yet. No verdict exists until the run finishes; these are not counted.</p>${table(running)}` : ""}
    ${lost.length ? `<h2>Started without a result</h2><p class="muted">supervisor.log says these started and the batch logged DONE, but runs.jsonl has no readable row for them.</p>${table(lost)}` : ""}`;
}

function batchStatusBadge(b) {
  if (b.status === "running") return `<span class="badge running" title="supervisor.log has no DONE line yet; last line ${esc(b.log_last_line_at || "?")}">running${b.planned_runs ? ` · ${b.run_count} of ${b.planned_runs} recorded` : ""}${b.running_count ? ` · ${b.running_count} in progress` : ""}</span>`;
  if (b.status === "finished") return `<span class="badge">finished (DONE logged)</span>`;
  return `<span class="badge">${unknown("no supervisor.log")}</span>`;
}

const pollNote = (b) => `<p class="muted poll-note">Read at ${clock(b.checked_at)}${b.status === "running" && b.log_last_line_at ? `; last supervisor.log line ${clock(b.log_last_line_at)}` : ""}. Refreshes every ${BATCH_POLL_MS / 1000} s.</p>`;

function attemptChain(attempts) {
  if (!attempts) return unknown("not recorded");
  if (!attempts.length) return `<span class="muted">no attempt executed</span>`;
  return `<span class="att-chain">${attempts.map((a) => {
    const bad = (a.fail || 0) + (a.refused || 0) + (a.error || 0);
    const title = `${a.attempt}: ${a.fail ?? "?"} fail · ${a.refused ?? "?"} refused · ${a.error ?? "?"} error · ${a.pass ?? "?"} pass · ${a.skipped ?? "?"} skipped`;
    const extra = `${a.refused ? `<small>+${a.refused}⊘</small>` : ""}${a.error ? `<small>+${a.error}!</small>` : ""}`;
    return `<span class="att-chip ${bad ? "bad" : "ok"}" title="${esc(title)}">${val(a.fail, "?")}${extra}</span>`;
  }).join(`<i>→</i>`)}</span>`;
}

function recommendationCell(r) {
  const agrees = r.recommendation_agrees === true ? `<span class="badge pass">agree</span>`
    : r.recommendation_agrees === false ? `<span class="badge fail">disagree</span>` : "";
  return `<span class="rec">agent: ${val(r.recommendation, r.recommendation_recorded ? "none" : "not recorded")}</span><span class="rec">runner: ${val(r.runner_recommendation, "not recorded")}</span>${agrees}`;
}

function runDirWarning(r) {
  const parts = [];
  if (r.summary_conflict?.length) parts.push(`summary.json disagrees with runs.jsonl on ${r.summary_conflict.join(", ")}: it was written by another run, so only the runs.jsonl row is shown`);
  if (r.shared_with?.length) parts.push(`run directory also named by ${r.shared_with.map((o) => `${o.batch_id} run ${o.n ?? "?"}`).join(", ")} (same-second run ID); its files may mix both runs`);
  return parts.length ? `<small class="cell-sub run-dir-warning" title="${esc(parts.join(". "))}">&#9888; ${esc(parts.join(". "))}. Not scored.</small>` : "";
}

function batchRunRows(runs) {
  return runs.map((r) => {
    const suspect = r.summary_conflict?.length || r.shared_with?.length;
    return `<tr>
    <td>${val(r.n, "?")}</td>
    <td>${r.run_id ? `<code>${esc(r.run_id)}</code>` : unknown("no run directory")}${r.has_summary || r.summary_conflict?.length ? "" : `<small class="cell-sub">no summary.json</small>`}${runDirWarning(r)}</td>
    <td>${val(r.scenario, "not recorded")}</td><td>${val(r.topology, "not recorded")}</td>
    <td>${verdictBadge(r.verdict)}${r.stopped ? `<small class="cell-sub" title="${esc(r.stopped)}">${esc(String(r.stopped).slice(0, 60))}</small>` : ""}</td>
    <td>${recommendationCell(r)}</td>
    <td class="attempts">${attemptChain(r.attempts)}</td>
    <td>${scoreCell(r)}</td>
    <td class="num">${r.total_tokens == null ? nr(null) : fmt(r.total_tokens)}</td>
    <td class="num">${nr(r.model_calls)}</td>
    <td class="num">${r.wall_seconds == null ? nr(null) : Number(r.wall_seconds).toFixed(0)}</td>
    <td class="links">${suspect ? `<span class="muted" title="The run directory holds another run's files">no link (shared directory)</span>`
      : `${r.in_store ? `<a href="#/run/${enc(r.run_id)}">Replay</a>` : `<span class="muted" title="Run is not in the evidence store">not ingested</span>`}
      ${r.trace_url ? `<a href="${esc(r.trace_url)}" target="_blank" rel="noopener noreferrer" title="Langfuse trace (viewer only; summary.json and events.jsonl are the evidence)">Trace &#8599;</a>` : `<span class="muted">no trace</span>`}`}</td>
  </tr>`;
  }).join("");
}

function batchRunsTable(runs) {
  return `<div class="card table-wrap"><table class="batch-runs">
    <tr><th>#</th><th>run</th><th>scenario</th><th>topology</th><th>verdict</th><th>recommendation</th><th>failed checks per attempt</th>${scoreHead}<th>tokens</th><th>calls</th><th>wall (s)</th><th>links</th></tr>
    ${batchRunRows(runs)}</table></div>`;
}

const BATCH_LEGEND = `<p class="legend">Failed checks per attempt, a1 → last: the number counts failed assertions; refusals (⊘) and execution errors (!) are counted separately. Every attempt stays visible, including failed ones. Hover a chip for full outcome counts.</p>`;

function batchBadges(b) {
  const s = b.scenario_score;
  return `${batchStatusBadge(b)} ${b.models.map((m) => `<span class="badge">model: ${esc(m)}</span>`).join(" ") || `<span class="badge">${unknown("model not recorded")}</span>`}
    <span class="badge">${b.model_result_count} model result${b.model_result_count === 1 ? "" : "s"}</span>
    ${s.scored ? `<span class="badge" title="${SCORE_LABEL}">matches expected: ${s.matched} of ${s.scored} scored</span>` : ""}
    ${b.infrastructure_failures.length ? `<span class="badge warn">${b.infrastructure_failures.length} infrastructure failure${b.infrastructure_failures.length === 1 ? "" : "s"}, not counted</span>` : ""}
    ${b.stop_file ? `<span class="badge">STOP set</span>` : ""}
    ${b.unreadable_lines ? `<span class="badge warn">${b.unreadable_lines} unreadable runs.jsonl line(s)</span>` : ""}`;
}

const configNote = (b) => b.config_note ? `<pre class="batch-note">${esc(b.config_note)}</pre>` : `<p>${unknown("no CONFIG-NOTE")}</p>`;

async function pageBatches() {
  crumbs([["Runs", "#/"], ["Batches"]]);
  const { batches } = await api("/api/batches");
  const withRuns = batches.filter((b) => b.has_runs_jsonl || b.in_progress.length);
  const empty = batches.filter((b) => !b.has_runs_jsonl && !b.in_progress.length);
  const cards = withRuns.map((b) => `<section class="card batch-card">
    <div class="row spread"><h2><a href="#/batches/${enc(b.batch_id)}">${esc(b.batch_id)}</a></h2><a class="button ghost" href="#/batches/${enc(b.batch_id)}">Open batch &#8594;</a></div>
    <div class="row">${batchBadges(b)}</div>
    ${configNote(b)}
    ${batchCounts(b.counts)}
  </section>`).join("");
  return `<header class="editorial-head"><div><h1>Brev batches</h1><p>Supervisor records from <code>runs/brev-batch-*</code>, newest first. Counts are per scenario and topology. Runs named in a batch's INFRA-NOTE are infrastructure failures and are never counted as model results. A pass applies only to the conditions that batch tested. A batch is finished only when its supervisor.log has a DONE line.</p></div></header>
    ${batches.length ? pollNote(batches[0]) : ""}
    ${cards || `<p class="muted">No batch with runs.jsonl under runs/.</p>`}
    ${empty.length ? `<details class="card"><summary>${empty.length} batch director${empty.length === 1 ? "y" : "ies"} without runs.jsonl (no review completed)</summary><ul>${empty.map((b) => `<li><code>${esc(b.batch_id)}</code>${b.config_note ? `: ${esc(b.config_note)}` : ""}</li>`).join("")}</ul></details>` : ""}`;
}

async function pageBatch(id) {
  crumbs([["Runs", "#/"], ["Batches", "#/batches"], [id]]);
  const b = await api(`/api/batch?id=${enc(id)}`);
  const modelRuns = b.runs.filter((r) => !r.infrastructure_failure);
  return `<div class="row"><h1>${esc(b.batch_id)}</h1></div>
    <div class="row">${batchBadges(b)}</div>${pollNote(b)}
    <h2>Configuration</h2>${configNote(b)}
    <h2>Model results by scenario and topology</h2>${batchCounts(b.counts)}
    ${inProgressTable(b)}
    <h2>Finished runs</h2>${modelRuns.length ? batchRunsTable(modelRuns) + BATCH_LEGEND : `<p class="muted">No model-result runs.</p>`}
    ${b.infrastructure_failures.length ? `<section class="infra-section"><h2>Infrastructure failures</h2>
      <p class="muted">Listed in INFRA-NOTE. These are not model results and are excluded from the counts above.</p>
      <pre class="batch-note">${esc(b.infra_note)}</pre>${batchRunsTable(b.infrastructure_failures)}</section>`
      : b.infra_note ? `<h2>INFRA-NOTE</h2><pre class="batch-note">${esc(b.infra_note)}</pre><p class="muted">No run in runs.jsonl matches a run named in this note.</p>` : ""}`;
}

async function openEvidence(oid) {
  const drawer = $("#evidence-drawer");
  const backdrop = $("#drawer-backdrop");
  const content = $("#drawer-content");
  content.innerHTML = `<div class="drawer-loading">Loading recorded evidence...</div>`;
  drawer.classList.add("open"); drawer.setAttribute("aria-hidden", "false"); backdrop.hidden = false;
  try {
    const o = await api(`/api/observation?id=${enc(oid)}`);
    const steps = o.steps.length ? `<div class="drawer-steps">${o.steps.map((s) => `<div class="drawer-step ${s.ok === 1 ? "ok" : s.ok === 0 ? "bad" : ""}"><span>${s.ok === 1 ? "✓" : s.ok === 0 ? "×" : "—"}</span><div><strong>${val(s.app_version)} / ${val(s.op)}</strong><small>${s.error ? esc(s.error) : s.value == null ? "No value recorded" : esc(s.value)}</small></div></div>`).join("")}</div>` : `<p class="muted">Database- or runner-level check; no app steps recorded.</p>`;
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
}

function showToast(message, tone = "info") {
  const toast = $("#toast");
  toast.textContent = message;
  toast.className = `toast show ${tone}`;
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => { toast.className = "toast"; }, 2800);
}

function openTour() {
  const tour = $("#welcome-tour");
  tour.hidden = false;
  requestAnimationFrame(() => tour.classList.add("open"));
}

function closeTour() {
  const tour = $("#welcome-tour");
  tour.classList.remove("open");
  localStorage.setItem("migrationlab-tour-seen", "1");
  setTimeout(() => { tour.hidden = true; }, 220);
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

const routeParts = () => location.hash.replace(/^#\/?/, "").split("/").filter(Boolean).map(decodeURIComponent);
const batchPage = (parts) => parts[0] === "batches" && parts.length <= 2
  ? (parts.length === 1 ? pageBatches : () => pageBatch(parts[1])) : null;

// Batches pages re-read the supervisor files every BATCH_POLL_MS (GET only), keeping scroll
// position and open <details>.
function scheduleBatchPoll() {
  clearTimeout(state.batchPoll);
  if (!batchPage(routeParts())) return;
  state.batchPoll = setTimeout(async () => {
    const hash = location.hash;
    const page = batchPage(routeParts());
    if (!page) return;
    if (document.hidden) { scheduleBatchPoll(); return; }
    try {
      const html = await page();
      if (location.hash !== hash) return;
      const view = $("#view");
      const open = [...view.querySelectorAll("details")].map((d) => d.open);
      const y = window.scrollY;
      view.innerHTML = html;
      view.querySelectorAll("details").forEach((d, i) => { if (open[i]) d.open = true; });
      window.scrollTo(0, y);
    } catch (err) { /* keep the last rendering; the next poll retries */ }
    scheduleBatchPoll();
  }, BATCH_POLL_MS);
}

async function route() {
  const parts = routeParts();
  const view = $("#view");
  clearTimeout(state.batchPoll);
  const activeNav = !parts.length ? "runs"
    : parts[0] === "batches" ? "batches"
    : parts[0] === "compare" ? "compare"
    : parts[0] === "demo" ? "demo"
    : parts[0] === "run" && parts.length === 2 ? "overview" : "runs";
  document.querySelectorAll(".side-nav [data-nav]").forEach((link) => {
    const active = link.dataset.nav === activeNav;
    link.classList.toggle("is-active", active);
    if (active) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  });
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
    else if (parts[0] === "batches" && parts.length === 1) html = await pageBatches();
    else if (parts[0] === "batches" && parts.length === 2) html = await pageBatch(parts[1]);
    else if (parts[0] === "review" && parts.length === 1) html = await pageReview();
    else if (parts[0] === "review" && parts.length === 2) html = await pageReviewLive(parts[1]);
    else html = `<p>Page not found. <a href="#/">Back to runs</a></p>`;
    view.innerHTML = html;
    window.scrollTo(0, 0);
    scheduleBatchPoll();
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
    target?.scrollIntoView({behavior: "smooth", block: "center"});
    if (target) { target.classList.add("revealed"); celebrate(target); }
    showToast("The safe plan keeps the failed attempt visible.", "success");
    return;
  }
  const stageSelect = ev.target.closest("[data-stage-select]");
  if (stageSelect) { state.workspaceStage = stageSelect.dataset.stageSelect; route(); showToast(`Selected: ${stageSelect.title || "migration stage"}`); return; }
  if (ev.target.closest("[data-scroll-runs]")) {
    document.querySelector("#runs-table")?.scrollIntoView({behavior: "smooth", block: "start"});
    return;
  }
  const obs = ev.target.closest("[data-obs]");
  if (obs) { openEvidence(obs.dataset.obs); return; }
  if (ev.target.id === "rv-start") { startReview(); return; }
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
    localStorage.setItem("migrationlab-theme", root.dataset.theme);
    syncThemeToggle(); return;
  }
  if (ev.target.closest("a, input, button, summary")) return;
  const row = ev.target.closest("[data-href]");
  if (row) location.hash = row.dataset.href;
});
document.addEventListener("change", (ev) => {
  if (ev.target.id === "dashboard-run") {
    state.dashboardRunId = ev.target.value;
    route();
    return;
  }
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
  if (ev.key === "Escape") closeDrawer();
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
function syncThemeToggle() {
  const button = document.querySelector("#theme-toggle");
  if (!button) return;
  const dark = document.documentElement.dataset.theme === "dark";
  button.textContent = dark ? "☀" : "☾";
  button.setAttribute("aria-label", dark ? "Use light theme" : "Use dark theme");
  button.title = dark ? "Use light theme" : "Use dark theme";
}
document.documentElement.dataset.theme = localStorage.getItem("migrationlab-theme") || "light";
syncThemeToggle();
route();
