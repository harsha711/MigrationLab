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
const state = { replayTimer: null, batchPoll: null, replayStep: 0, replaySpeed: 1400, workspaceStage: null, tourStep: 0, dashboardRunId: null, topologyPair: null };

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
const counterpartFor = (id) => api(`/api/runs/${enc(id)}/counterpart`);

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
                ["Compatibility checks", `#/run/${enc(id)}/matrix/${first}`, "matrix"],
                ["Agents", `#/run/${enc(id)}/agents`, "agents"],
                ["Metrics", `#/run/${enc(id)}/metrics`, "metrics"]];
  return `<section class="run-page-header"><span class="eyebrow">RECORDED MIGRATION REVIEW</span><div class="row"><h1>${esc(id)}</h1>${verdictBadge(run.meta?.verdict)}</div>
    <div class="row">${typeBadges(run)}</div>
    <nav class="tabs">${tabs.map(([l, h, k]) => `<a href="${h}" class="${k === active ? "active" : ""}">${l}</a>`).join("")}</nav></section>`;
}

async function renderCounterpart(runId) {
  const slot = $("#run-counterpart");
  if (!slot) return;
  try {
    const info = await counterpartFor(runId);
    if (location.hash !== `#/run/${enc(runId)}` || !$("#run-counterpart")) return;
    const alternatives = info.counterparts || [];
    if (!alternatives.length) {
      slot.innerHTML = `<strong>Matched topology comparison unavailable</strong><span>${esc(info.reason || "no matched counterpart recorded")}</span>`;
      return;
    }
    const links = alternatives.map((other) => `<a class="button ghost" href="#/compare/${enc(runId)}/${enc(other.run_id)}">Compare with ${esc(other.topology)} <small>same batch, scenario, repetition</small> →</a>`).join("");
    slot.innerHTML = `<strong>Matched topology comparison</strong><span>Same batch ${esc(info.run.batch_id)}, scenario ${esc(info.run.scenario)}, derived repetition ${esc(info.run.repetition)}.</span><div>${links}</div>`;
  } catch (err) {
    slot.innerHTML = `<strong>Matched topology comparison unavailable</strong><span>Comparison context could not be read.</span>`;
  }
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
const excerpt = (value, limit = 280) => {
  const text = String(value || "").trim();
  return text.length > limit ? `${text.slice(0, limit - 1).trimEnd()}…` : text;
};
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

function pageAsk() {
  crumbs([["Ask evidence"]]);
  const suggestions = [
    ["Understand a failure", "Why did the recent run fail?", "Get the stop reason and the underlying checks that did not recover."],
    ["Find persistent issues", "What checks kept failing in the recent run?", "Separate recurring failures from one-off noise."],
    ["Compare approaches", "How did roles compare with single in the latest batch?", "Compare recorded outcomes without guessing from model opinions."],
    ["Check a claim", "Is parallel better in the latest batch?", "Apply the pre-registered statistical rule to finished results."],
    ["Spot a pattern", "Which check failed most on rename-note?", "Search the recorded history for the most common failure."],
    ["Understand limits", "Did the recent run prove zero downtime?", "See what the evidence supports—and what it does not."],
  ];
  return `<section class="ask-page">
    <header class="ask-hero"><span class="eyebrow">EVIDENCE ASSISTANT</span><h1>What do you want to understand?</h1><p>Ask in plain English. Answers are grounded in recorded runner evidence, with links back to the exact runs.</p></header>
    <section class="ask-composer" aria-labelledby="ask-title">
      <div class="ask-composer-head"><div><h2 id="ask-title">Ask about the experiments</h2><p>Try “Why did the latest run fail?” or paste a run ID.</p></div><span class="read-only-pill"><i></i> Read-only evidence</span></div>
      <form id="chat-form">
        <label for="chat-question" class="sr-only">Question about recorded runs</label>
        <textarea id="chat-question" maxlength="1000" rows="3" placeholder="Ask a question about a run, batch, failure, or comparison…" autofocus></textarea>
        <div class="ask-form-foot"><span><kbd>Ctrl</kbd> <span>+</span> <kbd>Enter</kbd> to ask</span><button class="primary" type="submit">Ask evidence <span aria-hidden="true">→</span></button></div>
      </form>
    </section>
    <div id="chat-result" class="chat-result" aria-live="polite"></div>
    <section class="question-library" aria-labelledby="question-library-title"><div class="question-library-head"><div><span class="eyebrow">GOOD STARTING POINTS</span><h2 id="question-library-title">Questions you can ask</h2></div><p>Choose one to place it in the question box. You can edit it before asking.</p></div>
      <div class="question-grid">${suggestions.map(([label, question, detail]) => `<button type="button" class="question-card" data-chat-question="${esc(question)}"><span>${esc(label)}</span><strong>${esc(question)}</strong><small>${esc(detail)}</small><b aria-hidden="true">→</b></button>`).join("")}</div>
    </section>
    <aside class="trust-note"><span aria-hidden="true">i</span><div><strong>How to read these answers</strong><p>Runner observations determine outcomes. Explanatory wording helps interpret them but is still a claim. Open the cited runs when a decision matters.</p></div></aside>
  </section>`;
}

// ---------- Migration activities (scenario-scoped evidence) ----------

const activityMetric = (value, unit = "") => value == null ? "—" : `${fmt(Math.round(value))}${unit}`;
const activityVerdicts = (counts) => BATCH_VERDICTS.filter((verdict) => counts?.[verdict]).map((verdict) =>
  `<span class="badge ${verdict === "PASS" ? "pass" : verdict === "INCOMPLETE" ? "warn" : "fail"}">${counts[verdict]} ${verdict}</span>`).join(" ") || `<span class="muted">Verdict not recorded</span>`;

function activityCards(activities) {
  return activities.map((activity) => {
    const groups = activity.performance.slice(0, 3).map((group) => `<div class="activity-mini-metric"><strong>${esc(group.topology)}</strong><span>${activityMetric(group.median_tokens)} tokens median</span><span>${activityMetric(group.median_seconds, " s")} median</span></div>`).join("");
    return `<a class="activity-card" href="#/activities/${enc(activity.scenario)}"><header><span class="scenario-code">${esc(activity.scenario)}</span><span>${activity.reviews} recorded review${activity.reviews === 1 ? "" : "s"}</span></header><h2>${esc(activity.title)}</h2><p>${esc(activity.description || "Recorded migration activity.")}</p><div class="activity-tags">${(activity.tags || []).map((tag) => `<span>${esc(tag)}</span>`).join("")}</div><div class="activity-outcomes">${activityVerdicts(activity.verdicts)}</div><div class="activity-mini-grid">${groups || `<span class="muted">Resource measurements not recorded</span>`}</div><footer>Inspect activity <b aria-hidden="true">→</b></footer></a>`;
  }).join("");
}

function activityPerformanceRows(activity) {
  return activity.performance.map((group) => `<tr><td><strong>${esc(group.topology)}</strong></td><td>${esc(group.model)}</td><td>${group.reviews}</td><td>${activityVerdicts(group.verdicts)}</td><td>${activityMetric(group.median_tokens)}</td><td>${activityMetric(group.median_seconds, " s")}</td><td>${activityMetric(group.median_calls)}</td></tr>`).join("");
}

async function pageActivities(scenarioId = null) {
  const data = await api("/api/scenario-activities");
  const activities = data.activities || [];
  const selected = scenarioId ? activities.find((activity) => activity.scenario === scenarioId) : null;
  if (scenarioId && !selected) return `<p>Migration activity not found. <a href="#/activities">View recorded activities</a></p>`;
  if (selected) {
    crumbs([["Migration activities", "#/activities"], [selected.title]]);
    const runs = selected.runs.map((run) => `<a class="activity-run" href="#/run/${enc(run.run_id)}"><span>${esc(run.topology || "topology not recorded")}</span><strong>${esc(run.run_id)}</strong>${verdictBadge(run.verdict)}<small>${activityMetric(run.total_tokens)} tokens · ${activityMetric(run.wall_seconds, " s")} · ${activityMetric(run.model_calls)} calls</small><b aria-hidden="true">›</b></a>`).join("");
    return `<header class="activity-detail-head"><a href="#/activities" class="back-link">← All migration activities</a><span class="eyebrow">RECORDED MIGRATION ACTIVITY</span><h1>${esc(selected.title)}</h1><p>${esc(selected.description || "Scenario definition is not recorded in this checkout.")}</p><div class="activity-detail-meta"><span>${esc(selected.scenario)}</span>${selected.table ? `<span>Table · ${esc(selected.table)}</span>` : ""}${(selected.tags || []).map((tag) => `<span>${esc(tag)}</span>`).join("")}</div></header>
      <section class="activity-detail-summary"><div><span>Recorded reviews</span><strong>${selected.reviews}</strong></div><div><span>Outcome mix</span><p>${activityVerdicts(selected.verdicts)}</p></div><div><span>Comparison rule</span><p>Models and topologies remain separated by this activity.</p></div></section>
      <section class="activity-performance"><header><div><span class="eyebrow">MODEL & TOPOLOGY PERFORMANCE</span><h2>How each setup performed on this activity</h2></div><p>Median resource values summarize only the listed recorded reviews. They are not a quality score.</p></header><div class="table-wrap"><table><tr><th>Approach</th><th>Model</th><th>Reviews</th><th>Recorded outcomes</th><th>Median tokens</th><th>Median wall time</th><th>Median calls</th></tr>${activityPerformanceRows(selected)}</table></div></section>
      <section class="activity-run-list"><header><span class="eyebrow">EVIDENCE TRAIL</span><h2>Recorded reviews</h2></header>${runs}</section>`;
  }
  crumbs([["Migration activities"]]);
  const reviewed = activities.reduce((total, activity) => total + activity.reviews, 0);
  const passing = activities.filter((activity) => activity.verdicts.PASS).length;
  return `<header class="activity-overview-head"><div><span class="eyebrow">SCENARIO-FIRST EVIDENCE</span><h1>Migration activities</h1><p>Start with the database change, then see how each recorded model and agent setup performed under that activity’s checks.</p></div><aside class="activity-overview-metrics"><article><strong>${activities.length}</strong><span>activities with evidence</span></article><article><strong>${reviewed}</strong><span>recorded reviews</span></article><article><strong>${passing}</strong><span>activities with a pass</span></article></aside></header>
    <section class="activity-principles"><div><b>1</b><span><strong>Choose an activity</strong><small>Column change, constraint, backfill, or index operation.</small></span></div><div><b>2</b><span><strong>Compare scoped evidence</strong><small>Read outcomes per model and topology—not a pooled leaderboard.</small></span></div><div><b>3</b><span><strong>Open a recorded run</strong><small>Verify the runner checks behind every result.</small></span></div></section>
    <section class="activity-library"><header><div><span class="eyebrow">ACTIVITY LIBRARY</span><h2>What migrations have we tested?</h2></div><p>Cards only appear after a finished run records that activity.</p></header><div class="activity-card-grid">${activityCards(activities) || `<p class="muted">No scenario-labelled finished runs are in the evidence store.</p>`}</div></section>`;
}

async function pageRuns() {
  crumbs([["Runs"]]);
  const { runs, controller_available, controller_reason } = await api("/api/runs");
  const rows = runs.map((d) => {
    const search = `${d.run_id} ${d.verdict || ""} ${d.source_kind} ${d.model || "scripted"}`.toLowerCase();
    return `<tr class="link run-row" data-href="#/run/${enc(d.run_id)}" data-search="${esc(search)}" data-verdict="${esc(d.verdict || "")}" data-kind="${esc(d.source_kind)}">
      <td><input type="checkbox" class="pick" value="${esc(d.run_id)}" aria-label="select for compare"></td>
      <td><a href="#/run/${enc(d.run_id)}"><strong>${esc(d.run_id)}</strong></a><small class="cell-sub">${d.attempt_count} attempt${d.attempt_count === 1 ? "" : "s"}</small></td>
      <td>${verdictBadge(d.verdict)}</td><td>${typeBadges({source_kind: d.source_kind, meta: {model: d.model}})}</td>
      <td>${d.elapsed_s == null ? "—" : `${nr(d.elapsed_s)} s`}</td><td>${d.checks_total ?? "—"}</td><td>${d.checks_failed ?? "—"}</td><td><a class="open-run-link" href="#/run/${enc(d.run_id)}">Open <b aria-hidden="true">→</b></a></td></tr>`;
  }).join("");
  const outcomeCounts = runs.reduce((counts, run) => ({...counts, [run.verdict || "unknown"]: (counts[run.verdict || "unknown"] || 0) + 1}), {});
  const finished = (outcomeCounts.PASS || 0) + (outcomeCounts.REPRODUCED || 0);
  const needsAttention = (outcomeCounts.FAIL || 0) + (outcomeCounts.INCOMPLETE || 0) + (outcomeCounts.ERROR || 0);
  const featured = runs.slice(0, 3).map((run) => `<a class="run-quick-link" href="#/run/${enc(run.run_id)}"><span>${verdictBadge(run.verdict)}</span><strong>${esc(run.run_id)}</strong><small>${esc(run.model || "model not recorded")} · ${run.checks_total ?? "—"} checks</small><b aria-hidden="true">→</b></a>`).join("");
  return `<header class="run-library-head"><div><span class="eyebrow">RECORDED RUN LIBRARY</span><h1>Browse migration reviews</h1><p>Choose one recorded review to understand its plan, compatibility checks, revisions, and runner-confirmed outcome.</p></div><a class="button ghost" href="#/activities">Browse by migration activity →</a></header>
    <section class="run-library-guide"><div><b>1</b><span><strong>Find a review</strong><small>Search by run, model, source, or recorded outcome.</small></span></div><div><b>2</b><span><strong>Open its evidence</strong><small>See the plan and compatibility checks behind that review.</small></span></div><div><b>3</b><span><strong>Compare carefully</strong><small>Select exactly two runs only when their context is comparable.</small></span></div></section>
    <section class="run-library-summary" aria-label="Run library summary"><div><span>Recorded reviews</span><strong>${runs.length}</strong></div><div class="summary-positive"><span>Passed or reproduced</span><strong>${finished}</strong></div><div class="summary-attention"><span>Needs investigation</span><strong>${needsAttention}</strong><small>fail, incomplete, or error</small></div><div><span>Sources</span><strong>${new Set(runs.map((run) => run.source_kind)).size}</strong><small>fixtures and local runs stay labeled</small></div></section>
    <section class="run-quick-picks"><header><div><span class="eyebrow">START WITH A RECORDED REVIEW</span><h2>Open a run to see the evidence behind its outcome</h2></div><p>The library itself stays lightweight; detailed observations load only after you open a run.</p></header><div class="run-quick-grid">${featured || `<p class="muted">No recorded reviews are available.</p>`}</div></section>
    <section id="runs-table" class="section-head"><div><span class="eyebrow">ALL RECORDED REVIEWS</span><h2>Find the review you need</h2><p>Runner outcomes are recorded facts. Model text and plans appear after opening a review.</p></div>
      <div class="row"><button id="compare" disabled>Compare selected</button><a class="button ${controller_available ? "primary" : "ghost"}" href="#/review" title="${esc(controller_available ? "Start a review through the controller" : reviewUnavailableReason(controller_reason))}">Run Review</a></div><p id="compare-selection" class="run-library-compare-note">Select exactly two reviews. The comparison page will show whether they are a recorded matched pair.</p></section>
    <div class="filterbar run-library-filter"><input id="run-search" type="search" placeholder="Search by run ID, model, source, or verdict" aria-label="Search runs">
      <select id="verdict-filter" aria-label="Filter by verdict"><option value="">All outcomes</option><option>PASS</option><option>FAIL</option><option>INCOMPLETE</option><option>REPRODUCED</option><option>ERROR</option></select>
      <select id="kind-filter" aria-label="Filter by source"><option value="">All sources</option><option value="fixture">Recorded fixtures</option><option value="local_run">Local runs</option></select></div>
    <div class="card table-wrap runs-card"><table>
      <tr><th><span class="sr-only">Compare</span></th><th>review</th><th>recorded outcome</th><th>source & model</th><th>elapsed</th><th>checks</th><th title="Includes every recorded attempt; the outcome column is the final recorded verdict.">failed checks*</th><th><span class="sr-only">Open</span></th></tr>
      ${rows || `<tr><td colspan="8" class="muted">No finished runs in examples/ or runs/.</td></tr>`}
    </table></div>
    <p class="run-library-compare-note"><strong>* Failed checks include every recorded attempt.</strong> The outcome column is the final recorded verdict. Comparison preserves each run’s context; use <strong>Compare selected</strong> only for two reviews you intend to inspect side by side.</p>
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
  crumbs([["New review"]]);
  const status = await api("/api/review/status");
  const unavailable = status.ok ? "" : reviewUnavailableReason(status.reason);
  const scenarios = (await api("/api/scenarios")).scenarios;
  const options = scenarios.map((s) => `<option value="${esc(s.id)}" data-description="${esc(s.description)}">${esc(s.title)}</option>`).join("");
  return `<header class="job-head"><span class="eyebrow">SUBMIT A REVIEW JOB</span><h1>New review</h1><p>Choose a defined migration activity. The controller asks agents for a plan; the deterministic runner records the outcome.</p></header>
    <form id="review-form" class="job-form"><label>Migration activity<select id="rv-scenario">${options}</select><small id="scenario-description"></small></label><div class="job-form-grid"><label>Approach<select id="rv-topology"><option value="roles">Roles — investigator, planner, tester</option><option value="single">Single agent</option><option value="parallel">Parallel planners</option></select></label><label>Mode<select id="rv-mode"><option value="scripted">Scripted — no GPU required</option><option value="llm" ${status.llm_available ? "" : "disabled"}>LLM — configured model endpoint</option></select><small>${status.llm_available ? "Uses the configured MIGPROTO_LLM_* endpoint." : "LLM mode is unavailable: set MIGPROTO_LLM_MODEL before starting the UI."}</small></label></div><fieldset><legend>Review budget</legend><label>Max revisions<input id="rv-revisions" type="number" min="0" value="1"></label><label>Max model calls<input id="rv-calls" type="number" min="1" value="10"></label><label>Max tokens<input id="rv-tokens" type="number" min="1" value="40000"></label><label>Wall time (seconds)<input id="rv-wall" type="number" min="1" value="600"></label></fieldset>${status.ok ? "" : `<p class="notice">${esc(unavailable)}</p>`}<button id="rv-start" class="primary" ${status.ok ? "" : "disabled"}>Submit review job →</button><p id="rv-msg" class="muted"></p></form>`;
}

async function startReview() {
  const msg = $("#rv-msg");
  msg.textContent = "Submitting…";
  const n = (id) => Number($(id).value);
  const response = await fetch("/api/review", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({mode: $("#rv-mode").value, topology: $("#rv-topology").value, scenario: $("#rv-scenario").value, budget: {max_revisions: n("#rv-revisions"), max_model_calls: n("#rv-calls"), max_total_tokens: n("#rv-tokens"), max_wall_seconds: n("#rv-wall")}})});
  const body = await response.json();
  if (response.status === 202) { location.hash = `#/jobs/${enc(body.run_id)}`; return; }
  msg.textContent = body.error + (body.owner ? ` (${JSON.stringify(body.owner)})` : "");
}

async function pageJobs() {
  crumbs([["Reviews"]]);
  const jobs = (await api("/api/jobs")).jobs || [];
  return `<header class="job-head"><span class="eyebrow">REVIEW JOBS</span><h1>Submit → watch → decide</h1><p>Every job keeps its request, all attempts, and the runner’s final recorded verdict together.</p></header><div class="job-list">${jobs.map((job) => `<a href="#/jobs/${enc(job.run_id)}"><span>${esc(job.scenario)}</span><strong>${esc(job.run_id)}</strong><small>${esc(job.state)}</small>${job.verdict ? verdictBadge(job.verdict) : ""}</a>`).join("") || `<p class="muted">No review jobs have been submitted from this UI yet.</p>`}</div>`;
}

async function pageJob(runId) {
  crumbs([["Reviews", "#/jobs"], [runId]]);
  const jobs = (await api("/api/jobs")).jobs || [], job = jobs.find((x) => x.run_id === runId);
  const request = job?.request || {};
  setTimeout(() => pollJob(runId, 0), 0);
  const lineage = request.continue_from ? `<p class="notice">Human-assisted revision of <a href="#/jobs/${enc(request.continue_from.run_id)}">${esc(request.continue_from.run_id)}</a>, using attempt ${esc(request.continue_from.attempt)}. Human feedback is unverified input; runner evidence remains separate.</p>` : "";
  return `<header class="job-head"><span class="eyebrow">${request.source_tags?.includes("human-assisted") ? "HUMAN-ASSISTED REVIEW" : "REVIEW JOB"}</span><h1>${esc(runId)}</h1><p>Runner outcomes are evidence. A human decision below is separate and never changes the recorded verdict.</p></header>${lineage}<section class="job-request"><h2>Request</h2><dl><div><dt>Activity</dt><dd>${esc(request.scenario || job?.scenario || "unknown")}</dd></div><div><dt>Mode</dt><dd>${esc(request.mode || job?.mode || "unknown")}</dd></div><div><dt>Topology</dt><dd>${esc(request.agent_topology || job?.topology || "unknown")}</dd></div><div><dt>Budget</dt><dd>${esc(JSON.stringify(request.budget || {}))}</dd></div></dl></section><section class="job-timeline"><header><h2>Attempt timeline</h2><span id="rv-state" class="badge">${esc(job?.state || "running")}</span></header><div id="rv-events" class="timeline-events"></div></section><section id="feedback-outcome" class="job-request" hidden></section><section id="job-revise" class="job-decision" hidden><span class="eyebrow">HUMAN-ASSISTED REVISION</span><h2>Revise with feedback</h2><p>Feedback is sent to an LLM planner as unverified input. The runner still decides the outcome. Scripted mode ignores feedback.</p><label>Base attempt<select id="feedback-attempt"></select></label><label>Issue<textarea id="feedback-issue" placeholder="A concrete concern to address"></textarea></label><label>Suggestion<textarea id="feedback-suggestion" placeholder="An optional revision suggestion"></textarea></label><label>Test request<textarea id="feedback-test" placeholder="An interaction the tester should consider"></textarea></label><button id="feedback-start" class="primary">Submit revision job →</button><p id="feedback-msg"></p></section><section id="job-decision" class="job-decision" hidden><span class="eyebrow">HUMAN DECISION</span><h2>Decide what to do with this review</h2><p>This is a recorded human decision, not runner evidence. A PASS covers only the data set, app versions, sequential interleaving, and this machine tested here.</p><textarea id="decision-note" placeholder="Why are you approving or rejecting this review?"></textarea><div><button data-decision="approve" class="primary">Approve review</button><button data-decision="reject">Reject review</button></div><p id="decision-msg"></p></section><details class="job-chat"><summary>Ask evidence about this job</summary><form id="chat-form" data-run-id="${esc(runId)}"><textarea id="chat-question" placeholder="What caused this attempt to fail?"></textarea><button type="submit">Ask evidence →</button></form><div id="chat-result"></div></details>`;
}

async function pollJob(runId, after) {
  if (location.hash !== `#/jobs/${enc(runId)}`) return;
  let data;
  try { data = await api(`/api/review/events?run=${enc(runId)}&after=${after}`); }
  catch (err) { $("#rv-state").textContent = "error"; return; }
  const timeline = $("#rv-events"); if (!timeline) return;
  for (const event of data.events) {
    if (["attempt", "stage", "attempt_result", "check"].includes(event.kind)) timeline.insertAdjacentHTML("beforeend", `<article><span>${event.t == null ? "" : Number(event.t).toFixed(1) + "s"}</span><strong>${esc(event.kind === "check" ? humanize(event.name) : event.name || event.stage || event.kind)}</strong>${event.status ? verdictBadge(event.status) : ""}<small>${esc(event.msg || event.error || "")}</small></article>`);
  }
  if (!data.done) { setTimeout(() => pollJob(runId, data.next), 1500); return; }
  const detail = await getRun(runId).catch(() => null), verdict = detail?.meta?.verdict || "finished";
  $("#rv-state").outerHTML = `${verdictBadge(verdict)} <a href="#/run/${enc(runId)}">Open complete evidence</a>`;
  $("#job-decision").hidden = false;
  const revise = $("#job-revise");
  if (revise) {
    const attempts = detail?.attempts || [];
    const best = [...attempts].sort((a, b) => (a.checks_failed || 0) - (b.checks_failed || 0))[0];
    $("#feedback-attempt").innerHTML = attempts.map((a) => `<option value="${esc(String(a.attempt).replace(/^a/, ""))}" ${a === best ? "selected" : ""}>Attempt ${esc(a.attempt)} · ${esc(a.status || "recorded")}</option>`).join("");
    revise.hidden = !attempts.length;
  }
  const allEvents = await api(`/api/review/events?run=${enc(runId)}&after=0`).catch(() => ({events: []}));
  const human = allEvents.events.filter((event) => event.kind === "human_feedback");
  const addressed = allEvents.events.find((event) => event.kind === "agent_claim" && event.claim_kind === "addressed_feedback")?.addressed_feedback || [];
  const outcome = $("#feedback-outcome");
  if (outcome && human.length) {
    outcome.hidden = false;
    outcome.innerHTML = `<h2>Feedback → planner action → runner outcome</h2><p>Planner actions are claims. The final runner verdict is the recorded outcome.</p><div class="table-wrap"><table><tr><th>Feedback</th><th>Planner action</th><th>Runner outcome</th></tr>${human.map((item) => { const action = addressed.find((entry) => entry.index === item.index); return `<tr><td>${esc(item.feedback_kind)} · ${esc(item.text)}</td><td>${action ? `${esc(action.action)} · ${esc(action.reason)}` : "not recorded"}</td><td>${verdictBadge(verdict)}</td></tr>`; }).join("")}</table></div>`;
  }
}

async function startFeedbackRevision() {
  const runId = routeParts()[1], msg = $("#feedback-msg");
  const fields = [["issue", "#feedback-issue"], ["suggestion", "#feedback-suggestion"], ["test", "#feedback-test"]];
  const human_feedback = fields.map(([kind, selector]) => ({kind, text: $(selector).value.trim(), author: "UI reviewer", at: new Date().toISOString()})).filter((item) => item.text);
  if (!human_feedback.length) { msg.textContent = "Add at least one feedback item."; return; }
  msg.textContent = "Submitting revision…";
  const jobs = (await api("/api/jobs")).jobs || [], parent = jobs.find((job) => job.run_id === runId);
  const response = await fetch("/api/review", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({mode: parent?.mode || "llm", topology: parent?.topology || "roles", scenario: parent?.scenario || "rename-note", continue_from: {run_id: runId, attempt: Number($("#feedback-attempt").value)}, human_feedback})});
  const body = await response.json();
  if (response.status === 202) { location.hash = `#/jobs/${enc(body.run_id)}`; return; }
  msg.textContent = body.error || "Could not submit revision.";
}

async function submitDecision(decision) {
  const runId = routeParts()[1], msg = $("#decision-msg");
  msg.textContent = "Saving human decision…";
  const response = await fetch(`/api/jobs/${enc(runId)}/decision`, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({decision, note: $("#decision-note").value})});
  const body = await response.json();
  msg.textContent = response.ok ? `Recorded ${body.decision} as a human decision.` : body.error;
}

async function pageOverview(id) {
  const run = await getRun(id);
  crumbs([["Runs", "#/"], [id]]);
  setTimeout(() => renderCounterpart(id), 0);
  const m = run.meta || {};
  const intent = (attempt) => {
    const plan = attempt.plans?.[0]?.body || {};
    return plan.summary || plan.write_consistency || plan.revision_notes || "No planner summary was recorded for this attempt.";
  };
  const stageIds = (attempt) => (attempt.plans?.[0]?.body?.stages || []).map((stage) => stage.id || stage.kind).filter(Boolean);
  const planDelta = (attempt, previous) => {
    if (!previous) return ["Initial recorded plan; there is no earlier attempt to compare."];
    const before = new Set(stageIds(previous)), after = new Set(stageIds(attempt));
    const added = [...after].filter((stage) => !before.has(stage));
    const removed = [...before].filter((stage) => !after.has(stage));
    const notes = attempt.plans?.[0]?.body?.revision_notes;
    const changes = [];
    if (notes) changes.push(notes);
    if (added.length) changes.push(`Added stage${added.length === 1 ? "" : "s"}: ${added.map((stage) => stageTitle({stage_id: stage})).join(", ")}.`);
    if (removed.length) changes.push(`Removed stage${removed.length === 1 ? "" : "s"}: ${removed.map((stage) => stageTitle({stage_id: stage})).join(", ")}.`);
    if (!changes.length && intent(attempt) !== intent(previous)) changes.push("The recorded planner summary changed; stage IDs stayed the same.");
    return changes.length ? changes : ["No structural plan change was recorded between these attempts."];
  };
  const assessment = (attempt, previous) => {
    if (attempt.ok === 1) return {tone: "verified", title: "Runner verified this attempt", detail: "All recorded checks for this attempt passed."};
    if (!previous) return {tone: "initial", title: "Initial runner result", detail: "Use this runner evidence as the baseline for later revisions."};
    if (attempt.checks_total !== previous.checks_total) return {tone: "coverage", title: "Different recorded check coverage", detail: "The runner executed a different number of checks, so failed-check counts are not directly comparable."};
    if (attempt.checks_failed < previous.checks_failed) return {tone: "partial", title: "Fewer recorded failed checks", detail: `The count changed from ${previous.checks_failed} to ${attempt.checks_failed}; inspect the named checks before treating this as a fix.`};
    if (attempt.checks_failed > previous.checks_failed) return {tone: "regressed", title: "More recorded failed checks", detail: `The count changed from ${previous.checks_failed} to ${attempt.checks_failed} with the same recorded check count.`};
    return {tone: "unchanged", title: "Same recorded failure count", detail: "The count did not change under the same recorded check coverage."};
  };
  const story = run.attempts.map((a, index) => {
    const s = short(a.attempt_id);
    const cause = a.claims.find((c) => c.role === "tester-report" && c.kind === "suspected_cause");
    const compare = assessment(a, run.attempts[index - 1]);
    const failures = (a.failed || []).slice(0, 5);
    const failedCount = (a.failed || []).length;
    return `<article class="revision-story-card ${compare.tone}"><header><div><span class="story-index">${index + 1}</span><span class="eyebrow">ATTEMPT ${esc(s)}</span><h3>${verdictBadge(a.status)} ${esc(compare.title)}</h3></div><div class="story-links"><a href="#/run/${enc(id)}/plan/${s}">Open plan</a><a href="#/run/${enc(id)}/matrix/${s}">Compatibility checks</a></div></header><div class="story-grid"><section><span>Planner proposed</span><p>${esc(intent(a))}</p><small>Saved planner artifact; this is a proposal, not runner evidence.</small></section><section><span>What changed</span><ul>${planDelta(a, run.attempts[index - 1]).map((change) => `<li>${esc(change)}</li>`).join("")}</ul></section><section class="runner-observed"><span>Runner observed</span><strong>${a.outcome_counts ? esc(countsText(a.outcome_counts)) : `${a.checks_total} checks · ${a.checks_failed} failed`}</strong><p>${esc(compare.detail)}</p>${a.halted ? `<small>Stopped: ${esc(a.halted)}</small>` : ""}</section></div>${failures.length ? `<footer><span>Failed checks</span>${failures.map((failure) => `<code>${esc(failure)}</code>`).join("")}${failedCount > failures.length ? `<small>+${failedCount - failures.length} more</small>` : ""}</footer>` : ""}${cause ? `<details class="story-diagnosis"><summary>Tester’s recorded diagnosis</summary><p>${esc(cause.text)}</p></details>` : ""}</article>`;
  }).join("");
  const man = run.manifest;
  return `${runHeader(run, "overview")}<section id="run-counterpart" class="run-counterpart"><strong>Looking for a matched topology comparison…</strong></section>
    <section class="revision-story-head"><span class="eyebrow">REVISION STORY</span><h2>How the proposed migration changed after runner feedback</h2><p>Follow the saved planner proposal, the exact runner result, and the next revision. The runner—not the planner—determines whether an attempt passed.</p><div><strong>Requested change</strong><span>${m.scenario ? esc(m.scenario) : unknown("not recorded in this run")}</span></div></section>
    <section class="revision-story" aria-label="Revision story">${story || `<p class="muted">No attempt records were saved for this run.</p>`}</section>
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
  crumbs([["Runs", "#/"], [id, `#/run/${enc(id)}`], [`Compatibility checks ${s}`]]);
  const a = findAttempt(run, s);
  if (!a) return `${runHeader(run, "matrix")}<p>${unknown("no such attempt")}</p>`;
  const m = await api(`/api/matrix?run=${enc(id)}&attempt=${enc(a.attempt_id)}`);
  const appName = (name) => name === "old" ? "Old app" : name === "new-staged" ? "New app" : name === "new-unsafe" ? "New app (unsafe)" : humanize(name);
  const readablePath = (column) => {
    const [writer, reader] = String(column || "").split(" -> ");
    return writer && reader ? `${appName(writer)} writes → ${appName(reader)} reads` : "Application path not recorded";
  };
  const readableCheck = (check) => humanize(String(check || "").replace(/^case\./, ""));
  const failures = m.rows.filter(isBad);
  const passes = m.rows.filter((row) => kindOf(row) === "PASS").length;
  const grid = (rows, title) => {
    if (!rows.length) return `<p class="muted">No app-level cases with step scope in this attempt.</p>`;
    let body = "", last;
    for (const r of rows) {
      if (r.stage_id !== last) {
        body += `<tr class="stage-row"><td colspan="${m.columns.length + 1}">${val(r.stage_id)}</td></tr>`;
        last = r.stage_id;
      }
      body += `<tr><td title="${esc(readablePath(r.column))}"><strong>${esc(readableCheck(r.check_name))}</strong><small>${esc(readablePath(r.column))}</small></td>${
        m.columns.map((c) => `<td class="cell">${c === r.column
          ? `<button class="mark ${esc(kindOf(r))}" data-obs="${esc(r.observation_id)}" title="${esc(r.outcome || r.status)}">${MARKS[kindOf(r)] || "?"}</button>`
          : `<span class="dot">·</span>`}</td>`).join("")}</tr>`;
    }
    return `<section class="compat-detail"><h2>${title}</h2><div class="table-wrap"><table class="matrix">
      <tr><th>Compatibility check</th>${m.columns.map((c) => `<th class="col">${esc(readablePath(c))}</th>`).join("")}</tr>
      ${body}</table>
      </div><p class="legend">Green ✓ worked. Red ✗ broke. Gray · was not tested for that exact application path. Click a result to inspect the runner evidence.</p></section>`;
  };
  const failureCards = failures.slice(0, 6).map((row) => `<button class="compat-failure" data-obs="${esc(row.observation_id)}"><span>What broke</span><strong>${esc(readablePath(row.column))}</strong><small>${esc(readableCheck(row.check_name))} · ${esc(stageTitle({stage_id: row.stage_id}))}</small><b>Open evidence →</b></button>`).join("");
  const dbBad = m.db_checks.filter(isBad);
  const dbPass = m.db_checks.filter((d) => kindOf(d) === "PASS").length;
  const dbSkip = m.db_checks.filter((d) => kindOf(d) === "SKIP").length;
  const outcome = a.status || run.meta?.verdict || "not recorded";
  const recordedFailures = a.failed || [];
  const nonInteractionFailures = dbBad.length || recordedFailures.length;
  const overview = `<section class="compat-overview"><header><span class="eyebrow">COMPATIBILITY SUMMARY · ATTEMPT ${esc(s)}</span><h2>Did this attempt pass its full runner check set?</h2><p>The interaction matrix below is only one part of that check set. A green matrix does not override a failed attempt outcome.</p></header><div class="compat-summary-grid"><article class="${outcome === "PASS" ? "success" : "problem"}"><span>Full attempt outcome</span><strong>${esc(outcome)}</strong><small>${outcome === "PASS" ? "All recorded runner checks for this attempt passed." : "The runner recorded a failure, refusal, or error in this attempt."}</small></article><article class="${failures.length ? "problem" : "success"}"><span>App interaction matrix</span><strong>${failures.length ? `${failures.length} broken` : "All shown paths passed"}</strong><small>${passes} app-level interaction${passes === 1 ? "" : "s"} shown; gray means not tested.</small></article><article class="${nonInteractionFailures ? "problem" : "success"}"><span>Other runner checks</span><strong>${dbBad.length ? `${dbBad.length} not passed` : recordedFailures.length ? "Failure recorded" : "No break recorded"}</strong><small>Schema gates, stage execution, preservation, and retirement checks are separate from app interactions.</small></article></div></section>`;
  const answer = failures.length ? `<section class="compat-answer"><header><span class="eyebrow">WHAT NEEDS ATTENTION · CROSS-VERSION INTERACTION</span><h2>The rollout broke a shown old/new application path</h2><p>Open a failed interaction to see the deterministic runner steps, expected result, and observed failure.</p></header><div class="compat-failure-grid">${failureCards}</div></section>` : nonInteractionFailures ? `<section class="compat-answer"><header><span class="eyebrow">WHAT NEEDS ATTENTION · OTHER RUNNER CHECK</span><h2>The shown app interactions passed, but another runner check did not</h2><p>The failure is not hidden in the green matrix. Review the runner checks below${recordedFailures.length ? `: ${esc(recordedFailures.join(", "))}` : "."}</p></header></section>` : `<section class="compat-answer good"><header><span class="eyebrow">WHAT THE RUNNER OBSERVED</span><h2>No break was recorded in this attempt’s displayed checks</h2><p>This applies only to the checks and application paths shown below. Gray cells remain untested, not passed.</p></header></section>`;
  const db = m.db_checks.length ? `<h2>Other checks</h2>
    <p class="muted">${dbPass} passed${dbSkip ? `, ${dbSkip} skipped` : ""}${dbBad.length ? `, ${dbBad.length} not passed:` : ""}</p>
    <div class="chips">${dbBad.map((d) =>
      `<a class="chip ${esc(kindOf(d))}" href="#/obs/${enc(d.observation_id)}">${MARKS[kindOf(d)] || ""} ${esc(d.check_name)} (${val(d.stage_id)})</a>`).join("")}</div>
    <details><summary>Show all</summary><div class="chips" style="margin-top:6px">${m.db_checks.map((d) =>
      `<a class="chip ${esc(kindOf(d))}" href="#/obs/${enc(d.observation_id)}">${esc(d.check_name)}</a>`).join("")}</div></details>` : "";
  const details = failures.length ? `<details class="compat-all"><summary>Show all compatibility checks (${m.rows.length})</summary>${grid(m.rows, "All recorded compatibility checks")}</details>` : grid(m.rows, "Recorded compatibility checks");
  return `${runHeader(run, "matrix")}${attemptPills(run, "matrix", s)}${overview}${answer}${failures.length ? grid(failures, "Failed compatibility checks") : ""}${details}${db}`;
}

async function pageAgents(id) {
  const run = await getRun(id);
  crumbs([["Runs", "#/"], [id, `#/run/${enc(id)}`], ["Agents"]]);
  const investigator = run.claims_without_attempt?.filter((claim) => claim.role === "investigator") || [];
  const claimText = (claims, fallback) => claims.length ? excerpt(claims[0].text) : fallback;
  const rawClaims = (claims) => claims.length ? `<details class="agent-raw"><summary>Read recorded role output (${claims.length})</summary>${claims.map((claim) => `<article><strong>${esc(claim.kind || "claim")}</strong><p>${esc(claim.text)}</p></article>`).join("")}</details>` : "";
  const roleFlow = `<section class="agent-role-flow" aria-label="Agent handoff flow"><article><b>1</b><div><span>Investigator</span><strong>Maps the change</strong><small>Findings are handed to the planner.</small></div></article><i>→</i><article><b>2</b><div><span>Planner</span><strong>Proposes a rollout</strong><small>A staged plan goes to the runner.</small></div></article><i>→</i><article><b>3</b><div><span>Tester</span><strong>Returns feedback</strong><small>Test proposals and diagnoses inform revisions.</small></div></article><i>→</i><article class="runner"><b>4</b><div><span>Runner</span><strong>Records the outcome</strong><small>Only runner observations determine pass/fail.</small></div></article></section>`;
  const discovery = `<section class="agent-discovery"><header><div><span class="eyebrow">BEFORE THE FIRST PLAN</span><h2>Investigator handoff</h2></div><span class="agent-artifact">${investigator.length ? `${investigator.length} finding${investigator.length === 1 ? "" : "s"} recorded` : "No structured findings recorded"}</span></header><p>${esc(claimText(investigator, "No structured investigator finding was saved for this run. The planner artifact below is still available."))}</p><small>Investigator output is an agent claim. It provides context; it does not decide the result.</small>${rawClaims(investigator)}</section>`;
  const handoffs = run.attempts.map((attempt, index) => {
    const label = short(attempt.attempt_id);
    const plan = attempt.plans?.[0]?.body || {};
    const plannerClaims = attempt.claims.filter((claim) => claim.role === "planner");
    const testerClaims = attempt.claims.filter((claim) => claim.role?.startsWith("tester"));
    const stages = plan.stages || [];
    const plannerSummary = plan.summary || claimText(plannerClaims, "No separate planner explanation was recorded; open the saved plan to inspect its stages.");
    const testerSummary = claimText(testerClaims, attempt.halted || "No structured tester feedback was saved for this attempt.");
    const outcome = attempt.status || (attempt.ok === 1 ? "PASS" : attempt.ok === 0 ? "FAIL" : "not recorded");
    return `<article class="agent-handoff"><header><div><span class="eyebrow">ATTEMPT ${esc(label)}</span><h2>Proposal → feedback → recorded result</h2></div>${verdictBadge(outcome)}</header><div class="agent-handoff-grid"><section class="agent-planner"><span>Planner produced</span><strong>${stages.length ? `${stages.length} staged step${stages.length === 1 ? "" : "s"}` : "Plan structure not recorded"}</strong><p>${esc(excerpt(plannerSummary, 360))}</p><a href="#/run/${enc(id)}/plan/${enc(label)}">Open saved plan →</a>${rawClaims(plannerClaims)}</section><section class="agent-tester"><span>Tester handed back</span><strong>${testerClaims.length ? `${testerClaims.length} recorded feedback item${testerClaims.length === 1 ? "" : "s"}` : "Runner feedback only"}</strong><p>${esc(excerpt(testerSummary, 360))}</p><a href="#/run/${enc(id)}/matrix/${enc(label)}">Open compatibility checks →</a>${rawClaims(testerClaims)}</section><section class="agent-runner"><span>Runner recorded</span><strong>${esc(outcome)}</strong><p>${attempt.outcome_counts ? esc(countsText(attempt.outcome_counts)) : `${attempt.checks_total} checks · ${attempt.checks_failed} failed`}</p>${attempt.halted ? `<small>Stopped: ${esc(attempt.halted)}</small>` : `<small>Deterministic observations are available in the compatibility checks.</small>`}</section></div>${index < run.attempts.length - 1 ? `<footer><span>Handoff to next revision</span><p>The next planner attempt receives explicit controller artifacts; roles do not share chat history directly.</p></footer>` : ""}</article>`;
  }).join("");
  return `${runHeader(run, "agents")}<section class="agent-story-head"><span class="eyebrow">AGENT HANDOFFS</span><h2>Who did what, and what did the runner confirm?</h2><p>Roles are isolated. The controller passes explicit artifacts between them; the runner remains the only authority for executed results.</p></section>${roleFlow}${discovery}<section class="agent-handoff-list"><header><span class="eyebrow">ATTEMPT HANDOFFS</span><h2>From proposal to observed outcome</h2></header>${handoffs || `<p class="muted">No attempt artifacts were saved for this run.</p>`}</section>`;
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
          ...(s ? [[`Compatibility checks ${s}`, `#/run/${enc(o.run_id)}/matrix/${s}`]] : []), [o.check_name]]);
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
  const failureTheme = (name) => {
    if (/^stage\./.test(name)) return "stage execution";
    if (/preservation|columns_agree|retirement_gate|preconditions/.test(name)) return "migration safeguards";
    if (/^case\./.test(name)) return "application compatibility";
    return "other recorded checks";
  };
  const closest = (attempts) => [...attempts].sort((x, y) =>
    (x.checks_failed ?? Infinity) - (y.checks_failed ?? Infinity))[0];
  const trajectory = (attempts) => attempts.map((x) => x.checks_failed).filter((n) => n != null);
  const trend = (attempts) => {
    const values = trajectory(attempts);
    if (values.length < 2) return "One recorded attempt; no revision trend to compare.";
    const first = values[0], best = Math.min(...values), last = values.at(-1);
    if (best < first && last > best) return `Failure count improved from ${first} to ${best}, then rose to ${last}.`;
    if (last < first) return `Failure count fell from ${first} to ${last} across recorded attempts.`;
    if (last > first) return `Failure count rose from ${first} to ${last} across recorded attempts.`;
    return `Failure count ended where it began (${last}).`;
  };
  const compactFailures = (names, limit = 3) => {
    if (!names?.length) return `<span class="muted">No failed checks recorded.</span>`;
    const shown = names.slice(0, limit);
    return `${shown.map((name) => `<code class="chip">${esc(name)}</code>`).join("")}${names.length > limit ? `<small>+${names.length - limit} more</small>` : ""}`;
  };
  const col = (run) => {
    const id = run.run_id;
    const best = closest(run.attempts);
    const finalVerdict = run.meta?.verdict || "not recorded";
    const final = run.attempts.at(-1);
    const groups = (best?.failed || []).reduce((out, name) => {
      const theme = failureTheme(name);
      (out[theme] ||= []).push(name);
      return out;
    }, {});
    return `<article class="compare-run card"><header><div><span class="eyebrow">RECORDED REVIEW</span><a href="#/run/${enc(id)}"><strong>${esc(id)}</strong></a></div>${verdictBadge(finalVerdict)}</header>
      <div class="row compare-context">${typeBadges(run)}</div>
      <section class="compare-takeaway">
        <span class="eyebrow">CLOSEST RECORDED ATTEMPT</span>
        ${best ? `<strong>${esc(short(best.attempt_id))} · ${best.checks_failed} failed check${best.checks_failed === 1 ? "" : "s"}</strong>
          <p>${esc(trend(run.attempts))} ${best.checks_total != null ? `This attempt ran ${best.checks_total} checks.` : ""}</p>` : `<p class="muted">No attempts were recorded.</p>`}
      </section>
      ${best?.failed?.length ? `<section class="compare-themes"><span>What still blocked that attempt</span>
        ${Object.entries(groups).map(([theme, names]) => `<div><strong>${esc(theme)}</strong><small>${names.length} failed check${names.length === 1 ? "" : "s"}</small></div>`).join("")}
        <div class="chips compare-failure-names">${compactFailures(best.failed, 99)}</div>
      </section>` : ""}
      <p class="compare-outcome"><strong>Final recorded outcome:</strong> ${verdictBadge(finalVerdict)}${final?.halted ? ` <span class="muted">${esc(final.halted)}</span>` : ""}</p>
      <section class="compare-path"><span class="eyebrow">RECORDED ATTEMPT PATH</span>
        ${run.attempts.map((x) => `<div><header><strong>${esc(short(x.attempt_id))}</strong> ${verdictBadge(x.status)}<span>${x.checks_failed} failed / ${x.checks_total} checks</span></header>
          ${x.halted ? `<p class="muted">Stopped: ${esc(x.halted)}</p>` : ""}<div class="chips">${compactFailures(x.failed)}</div></div>`).join("")}
      </section>
      <details class="compare-evidence"><summary>Show every named failed check</summary>
        ${run.attempts.map((x) => `<section><div class="row"><strong>${esc(short(x.attempt_id))}</strong> ${verdictBadge(x.status)}</div>
          ${(x.failed || []).length ? `<div class="chips">${compactFailures(x.failed, 99)}</div>` : `<p class="muted">No failed checks recorded for this attempt.</p>`}</section>`).join("")}
      </details>
    </article>`;
  };
  const [left, right, leftContext, rightContext] = await Promise.all([
    getRun(a), getRun(b), counterpartFor(a), counterpartFor(b),
  ]);
  const leftBest = closest(left.attempts), rightBest = closest(right.attempts);
  const shared = [...new Set((leftBest?.failed || []).filter((name) => (rightBest?.failed || []).includes(name)))];
  const result = left.meta?.verdict === "PASS" || right.meta?.verdict === "PASS"
    ? "At least one review passed its recorded checks; open the run evidence before making a human decision."
    : "Neither review passed its recorded checks. This page cannot choose a better approach from these two runs.";
  const leftProfile = leftContext.run || {}, rightProfile = rightContext.run || {};
  const fields = [["Scenario", "scenario"], ["Topology", "topology"], ["Batch", "batch_id"],
    ["Configuration", "configuration"], ["Repetition", "repetition"], ["Model", "model"],
    ["Thinking budget", "thinking_budget"], ["Token budget", "token_budget"], ["Wall budget", "wall_budget"]];
  const contextStrip = `<section class="compare-context-strip"><header><span class="eyebrow">RECORDED COMPARISON CONTEXT</span><p>Fields with a tinted background differ. Missing means the source did not record it.</p></header><div class="table-wrap"><table><tr><th>Field</th><th>${esc(a)}</th><th>${esc(b)}</th></tr>${fields.map(([label, key]) => {
    const different = String(leftProfile[key] ?? "") !== String(rightProfile[key] ?? "");
    const cell = (value) => value == null ? unknown("not recorded") : esc(value);
    return `<tr class="${different ? "context-diff" : ""}"><th>${label}</th><td>${cell(leftProfile[key])}</td><td>${cell(rightProfile[key])}</td></tr>`;
  }).join("")}</table></div><p class="compare-expected">Expected outcome: ${leftProfile.expected ? `${esc(leftProfile.expected.verdict)} · ${leftProfile.matches_expected === true ? "left matched" : leftProfile.matches_expected === false ? "left did not match" : "left not scored"}` : "not available"}${rightProfile.expected ? `; ${esc(rightProfile.expected.verdict)} · ${rightProfile.matches_expected === true ? "right matched" : rightProfile.matches_expected === false ? "right did not match" : "right not scored"}` : ""}. This evaluation label does not change either runner verdict.</p></section>`;
  const pairMatched = (leftContext.counterparts || []).some((item) => item.run_id === b);
  const warnings = [];
  if (leftProfile.scenario !== rightProfile.scenario) warnings.push("The recorded scenarios differ.");
  const hasRunnerEvidence = (run) => run.attempts.some((attempt) => (attempt.checks_total || 0) > 0);
  if (!hasRunnerEvidence(left) || !hasRunnerEvidence(right)) warnings.push("At least one review has no runner evidence (zero executed checks).");
  if (!pairMatched) warnings.push(leftContext.reason || "These runs are not a recorded same-batch matched pair.");
  const warning = warnings.length ? `<section class="compare-warning"><strong>Not a meaningful topology comparison</strong><span>${esc(warnings.join(" "))} The evidence columns are still shown for inspection.</span></section>` : "";
  return `<h1>Compare two reviews</h1><p class="muted">This page describes two recorded reviews only. It does not rank topologies or establish that one approach is better.</p>${contextStrip}${warning}
    <section class="compare-conclusion"><span class="eyebrow">WHAT THIS COMPARISON SAYS</span><strong>${esc(result)}</strong>
      <div><section><span>Left closest attempt</span><b>${leftBest ? `${esc(short(leftBest.attempt_id))} · ${leftBest.checks_failed}/${leftBest.checks_total} failed` : "not recorded"}</b></section><section><span>Right closest attempt</span><b>${rightBest ? `${esc(short(rightBest.attempt_id))} · ${rightBest.checks_failed}/${rightBest.checks_total} failed` : "not recorded"}</b></section><section><span>Shared closest-attempt blockers</span><b>${shared.length ? `${shared.length} shared` : "none recorded"}</b>${shared.length ? `<small>${compactFailures(shared, 4)}</small>` : ""}</section></div>
    </section><div class="compare">${col(left)}${col(right)}</div>`;
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

function batchPurpose(b) {
  const scenarios = [...new Set((b.counts || []).map((c) => c.scenario).filter(Boolean))];
  const topologies = [...new Set((b.counts || []).map((c) => c.topology).filter(Boolean))];
  const scope = `${scenarios.length ? scenarios.join(", ") : "the recorded migration activity"}${topologies.length ? ` using ${topologies.join(" and ")}` : ""}`;
  if (b.status === "running") return `This experiment is still collecting reviews for ${scope}. Recorded outcomes below are incomplete; no comparison conclusion should be drawn yet.`;
  return `This finished experiment recorded ${b.model_result_count} review${b.model_result_count === 1 ? "" : "s"} for ${scope}. Open an individual review to see its plan and runner evidence.`;
}

function batchOutcomeCards(b) {
  const groups = b.counts || [];
  return `<div class="batch-outcome-cards">${groups.map((c) => {
    const total = BATCH_VERDICTS.reduce((n, key) => n + (c[key] || 0), 0);
    const result = c.PASS ? `${c.PASS} passed` : c.INCOMPLETE ? `${c.INCOMPLETE} incomplete` : c.FAIL ? `${c.FAIL} failed` : total ? `${total} recorded` : "no completed reviews";
    return `<article><span>${esc(c.scenario || "activity not recorded")}</span><strong>${esc(c.topology || "approach not recorded")}</strong><b>${esc(result)}</b><small>${total} final verdict${total === 1 ? "" : "s"} · ${c.scored ? `${c.matched}/${c.scored} matched the evaluation label` : "not scored"}</small></article>`;
  }).join("") || `<p class="muted">No model-result rows were recorded.</p>`}</div>`;
}

function batchTakeaway(b) {
  if (b.status !== "finished") return "Wait for the supervisor to record DONE before interpreting the batch.";
  if (!b.model_result_count) return "The supervisor finished without a readable model result.";
  if (b.running_count) return "Some records are still in progress; treat the displayed results as partial.";
  return "These are recorded outcomes, not a ranking. Compare approaches only under the batch’s pre-registered evaluation rule.";
}

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
  crumbs([["Evaluation"], ["Experiment batches", "#/batches"], [id]]);
  const b = await api(`/api/batch?id=${enc(id)}`);
  const modelRuns = b.runs.filter((r) => !r.infrastructure_failure);
  return `<header class="batch-story-head"><a class="back-link" href="#/batches">← Experiment batches</a><span class="eyebrow">EXPERIMENT BATCH</span><h1>What this batch tells us</h1><p>${esc(batchPurpose(b))}</p><div>${batchStatusBadge(b)} <span>${b.model_result_count} recorded review${b.model_result_count === 1 ? "" : "s"}</span>${b.running_count ? `<span>${b.running_count} still running</span>` : ""}</div></header>
    <section class="batch-takeaway"><span>How to read this</span><strong>${esc(batchTakeaway(b))}</strong><small>A PASS covers only the data set, versions, sequential interleaving, and machine tested in its review.</small></section>
    <section class="batch-outcomes"><header><div><span class="eyebrow">RECORDED OUTCOMES</span><h2>What finished for each activity and approach?</h2></div><p>Final runner verdicts only. Failed attempts remain in each review.</p></header>${batchOutcomeCards(b)}</section>
    ${inProgressTable(b)}
    <details class="batch-technical"><summary>Inspect the experiment configuration and exact run records</summary><section><h2>Configuration notes</h2>${configNote(b)}<h2>Outcome table</h2>${batchCounts(b.counts)}<h2>Finished reviews</h2>${modelRuns.length ? batchRunsTable(modelRuns) + BATCH_LEGEND : `<p class="muted">No model-result runs.</p>`}</section></details>
    ${b.infrastructure_failures.length ? `<section class="infra-section"><h2>Infrastructure failures</h2>
      <p class="muted">Listed in INFRA-NOTE. These are not model results and are excluded from the counts above.</p>
      <pre class="batch-note">${esc(b.infra_note)}</pre>${batchRunsTable(b.infrastructure_failures)}</section>`
      : b.infra_note ? `<h2>INFRA-NOTE</h2><pre class="batch-note">${esc(b.infra_note)}</pre><p class="muted">No run in runs.jsonl matches a run named in this note.</p>` : ""}`;
}

// ---------- Roles versus single (recorded rename observations only) ----------

const topologyNumber = (value, suffix = "") => value == null ? unknown("not recorded") : `${fmt(value)}${suffix}`;
const topologyMedian = (value, unit) => value == null ? unknown("not recorded") : `${fmt(Math.round(value))} ${unit}`;
const topologyVerdicts = (setup) => BATCH_VERDICTS.filter((v) => setup.verdicts[v]).map((v) =>
  `<span class="badge ${v === "PASS" ? "pass" : v === "INCOMPLETE" ? "warn" : "fail"}">${setup.verdicts[v]} ${v}</span>`).join(" ") || `<span class="muted">No reviews</span>`;

function incompleteStops(setup) {
  const stops = setup.incomplete_stops;
  if (!setup.verdicts.INCOMPLETE) return "";
  const labels = [["token_budget", "token budget"], ["call_budget", "call budget"], ["other", "reason not recorded"]]
    .filter(([key]) => stops[key]).map(([key, label]) => `${stops[key]} ${label}`);
  return `<small>Incomplete: ${esc(labels.join(", "))}</small>`;
}

function topologySummaryCell(name, setup) {
  return `<section class="topology-summary-cell"><header><strong>${name}</strong><span>${setup.reviews} review${setup.reviews === 1 ? "" : "s"}</span></header>
    <div class="topology-verdicts">${topologyVerdicts(setup)}</div>${incompleteStops(setup)}
    <dl><div><dt>Median tokens</dt><dd>${topologyMedian(setup.median_tokens, "tokens")}</dd></div><div><dt>Median wall time</dt><dd>${topologyMedian(setup.median_seconds, "s")}</dd></div></dl></section>`;
}

function trajectory(run) {
  const values = run.failed_checks;
  if (!Array.isArray(values)) return `<span class="muted">Failed-check trajectory not recorded</span>`;
  const peak = Math.max(...values, 1);
  return `<div class="failure-trajectory" aria-label="Failed checks per attempt: ${values.join(", ")}">${values.map((value, index) =>
    `<span title="Attempt ${index + 1}: ${value} failed check${value === 1 ? "" : "s"}"><i style="--trajectory-height:${Math.max(12, Math.round((value / peak) * 100))}%"></i><b>${value}</b><small>a${index + 1}</small></span>`).join("") || `<span class="muted">No attempt executed</span>`}</div>`;
}

function topologyRunCard(run) {
  return `<article class="topology-run ${String(run.verdict).toLowerCase()}"><header><div><span class="eyebrow">${esc(run.topology)}</span><h3>${esc(run.verdict || "unknown")}</h3></div><a href="#/run/${enc(run.run_id)}">Replay</a></header>
    <p class="topology-run-id">${esc(run.run_id || "run ID not recorded")}</p>
    <div class="topology-trajectory"><span>Failed checks by attempt</span>${trajectory(run)}</div>
    <dl><div><dt>Tokens</dt><dd>${topologyNumber(run.total_tokens)}</dd></div><div><dt>Calls</dt><dd>${topologyNumber(run.model_calls)}</dd></div><div><dt>Wall time</dt><dd>${topologyNumber(run.wall_seconds, " s")}</dd></div></dl>
  </article>`;
}

function topologyMetricValue(run, field) {
  if (field === "tokens_per_call") {
    return typeof run.total_tokens === "number" && typeof run.model_calls === "number" && run.model_calls > 0
      ? run.total_tokens / run.model_calls : null;
  }
  return typeof run[field] === "number" ? run[field] : null;
}

function topologyMetricLabel(value, field) {
  if (value == null) return "not recorded";
  if (field === "wall_seconds") return `${fmt(Math.round(value))} s`;
  if (field === "tokens_per_call") return `${fmt(Math.round(value))} / call`;
  return fmt(Math.round(value));
}

function topologyResourceMetric(label, field, roles, single) {
  const roleValue = topologyMetricValue(roles, field);
  const singleValue = topologyMetricValue(single, field);
  const max = Math.max(roleValue || 0, singleValue || 0, 1);
  const bar = (value) => value == null ? "0" : `${Math.max(5, Math.round(value / max * 100))}`;
  return `<article class="resource-metric"><header><strong>${label}</strong><span>recorded pair</span></header>
    <div class="resource-row roles"><span>Roles</span><b>${topologyMetricLabel(roleValue, field)}</b><i><em style="width:${bar(roleValue)}%"></em></i></div>
    <div class="resource-row single"><span>Single</span><b>${topologyMetricLabel(singleValue, field)}</b><i><em style="width:${bar(singleValue)}%"></em></i></div>
  </article>`;
}

function topologyResourceProfile(roles, single) {
  return `<section class="topology-resource-profile" aria-label="Recorded resource profile for selected roles and single reviews">
    <header><div><span class="eyebrow">RECORDED RESOURCE PROFILE</span><h3>What each selected review consumed</h3></div><p>Bar length is relative only within this pair. It does not determine which approach is better.</p></header>
    <div class="resource-metric-grid">${topologyResourceMetric("Token footprint", "total_tokens", roles, single)}${topologyResourceMetric("Wall time", "wall_seconds", roles, single)}${topologyResourceMetric("Model calls", "model_calls", roles, single)}${topologyResourceMetric("Tokens per call", "tokens_per_call", roles, single)}</div>
  </section>`;
}

async function pageTopologies() {
  crumbs([["Runs", "#/"], ["Roles vs single"]]);
  const data = await api("/api/topology-comparison");
  const configurations = data.configurations || [];
  const pairs = configurations.flatMap((configuration) => {
    const roles = configuration.setups.roles.runs || [], single = configuration.setups.single.runs || [];
    return roles.flatMap((r) => single.map((s) => ({configuration: configuration.configuration, roles: r, single: s})));
  });
  const selected = pairs.find((pair) => `${pair.configuration}|${pair.roles.run_id}|${pair.single.run_id}` === state.topologyPair) || pairs[0];
  if (selected) state.topologyPair = `${selected.configuration}|${selected.roles.run_id}|${selected.single.run_id}`;
  const options = pairs.map((pair) => {
    const value = `${pair.configuration}|${pair.roles.run_id}|${pair.single.run_id}`;
    return `<option value="${esc(value)}" ${value === state.topologyPair ? "selected" : ""}>Config ${esc(pair.configuration)} · roles ${esc(pair.roles.verdict || "?")} / single ${esc(pair.single.verdict || "?")}</option>`;
  }).join("");
  const rows = configurations.map((configuration) => `<section class="topology-config"><header><div><span class="eyebrow">CONFIGURATION ${esc(configuration.configuration)}</span><h2>Same settings, separate recorded reviews</h2></div><details><summary>Configuration note</summary><pre>${esc(configuration.config_note || "not recorded")}</pre></details></header><div class="topology-summary-grid">${topologySummaryCell("roles", configuration.setups.roles)}${topologySummaryCell("single", configuration.setups.single)}</div></section>`).join("");
  const pair = selected ? `<section class="topology-pair"><header><div><span class="eyebrow">PRESENTER PAIR · CONFIGURATION ${esc(selected.configuration)}</span><h2>Inspect two recorded reviews side by side</h2><p>Each trajectory includes every saved attempt. The runner recorded the verdicts shown here.</p></div><label><span>Choose a pair</span><select id="topology-pair">${options}</select></label></header>${topologyResourceProfile(selected.roles, selected.single)}<div class="topology-pair-grid">${topologyRunCard(selected.roles)}${topologyRunCard(selected.single)}</div></section>` : `<section class="card"><p class="muted">No roles/single pair is available yet. Batches appear here once both setups have recorded rename reviews in the same configuration.</p></section>`;
  return `<header class="editorial-head topology-head"><div><span class="eyebrow">RECORDED MODEL REVIEWS</span><h1>Multi-agent vs single-agent</h1><p>Both setups use the same model, runner, and checks. Comparisons are only within each configuration.</p></div></header>
    <p class="topology-caption">One to three reviews per setup per configuration: observations, not rates. Neither setup is shown to be better.</p>
    <section class="topology-definition"><div><strong>roles</strong><span>Investigator, planner, and tester use separate calls with no chat history; the controller passes JSON between them.</span></div><div><strong>single</strong><span>One agent performs every step in one conversation with compacted history.</span></div></section>
    ${pair}
    ${rows || `<section class="card"><p class="muted">No completed configuration B–M batch records were found under runs/. This view reads batch folders directly and will populate when they are copied in.</p></section>`}`;
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
    : parts[0] === "review" ? "new-review"
    : parts[0] === "jobs" ? "jobs"
    : parts[0] === "ask" ? "ask"
    : parts[0] === "activities" ? "activities"
    : parts[0] === "batches" ? "batches"
    : parts[0] === "topologies" ? "topologies"
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
    else if (parts[0] === "ask" && parts.length === 1) html = pageAsk();
    else if (parts[0] === "activities" && parts.length <= 2) html = await pageActivities(parts[1]);
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
    else if (parts[0] === "topologies" && parts.length === 1) html = await pageTopologies();
    else if (parts[0] === "review" && parts.length === 1) html = await pageReview();
    else if (parts[0] === "jobs" && parts.length === 1) html = await pageJobs();
    else if (parts[0] === "jobs" && parts.length === 2) html = await pageJob(parts[1]);
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
  if (ev.target.id === "rv-start") { ev.preventDefault(); startReview(); return; }
  if (ev.target.id === "feedback-start") { ev.preventDefault(); startFeedbackRevision(); return; }
  const decision = ev.target.closest("[data-decision]");
  if (decision) { submitDecision(decision.dataset.decision); return; }
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
async function updateCompareSelection(picked) {
  const btn = $("#compare"), note = $("#compare-selection");
  if (!btn || !note) return;
  btn.disabled = picked.length !== 2;
  btn.onclick = () => { location.hash = `#/compare/${enc(picked[0])}/${enc(picked[1])}`; };
  if (picked.length !== 2) {
    note.textContent = "Select exactly two reviews. The comparison page will show whether they are a recorded matched pair.";
    return;
  }
  note.textContent = "Checking recorded comparison context…";
  try {
    const [left, right] = await Promise.all(picked.map(counterpartFor));
    if ([...document.querySelectorAll(".pick:checked")].map((box) => box.value).join("|") !== picked.join("|")) return;
    if (left.run?.scenario !== right.run?.scenario) {
      note.textContent = "These reviews have different recorded scenarios; the comparison page will flag that it is not meaningful.";
    } else if (!(left.counterparts || []).some((item) => item.run_id === picked[1])) {
      note.textContent = `These are not a recorded matched pair: ${left.reason || "batch, repetition, or counterpart scope differs"}.`;
    } else {
      note.textContent = "Matched pair: same recorded batch, scenario, and derived repetition; topology differs.";
    }
  } catch (err) {
    note.textContent = "Comparison context could not be read; you can still inspect the two recorded reviews.";
  }
}
document.addEventListener("change", (ev) => {
  if (ev.target.id === "rv-scenario") { const option = ev.target.selectedOptions[0]; const target = $("#scenario-description"); if (target) target.textContent = option?.dataset.description || ""; return; }
  if (ev.target.id === "topology-pair") { state.topologyPair = ev.target.value; route(); return; }
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
  updateCompareSelection(picked);
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
