// Replay Recorded Run: read-only rendering of /api/* data. No execution paths.
"use strict";

const $ = (sel, root = document) => root.querySelector(sel);
const esc = (v) => String(v).replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const unknown = (label = "unknown") => `<span class="unknown">${label}</span>`;
const val = (v, label) => (v === null || v === undefined || v === "") ? unknown(label) : esc(v);
const nr = (v) => val(v, "not recorded");
const api = async (path) => {
  const r = await fetch(path);
  if (!r.ok) throw new Error(`${path}: ${r.status}`);
  return r.json();
};

function verdictBadge(v) {
  if (!v) return `<span class="badge">verdict ${unknown()}</span>`;
  const cls = ["PASS", "REPRODUCED"].includes(v) ? "pass" : (v === "FAIL" || v === "ERROR") ? "fail" : "";
  return `<span class="badge ${cls}">${esc(v)}</span>`;
}

function runBadges(run) {
  const m = run.meta || {};
  const b = [`<span class="badge">Recorded run (replay)</span>`];
  if (run.source_kind === "fixture") b.push(`<span class="badge">recorded fixture</span>`);
  else b.push(`<span class="badge">local run</span>`);
  const prov = m.provenance || "";
  if (m.dev_only_model) b.push(`<span class="badge warn">development run, outside submission budget</span>`);
  else if (m.model) b.push(`<span class="badge">live model: ${esc(m.model)}</span>`);
  else if (prov === "scripted" || m.source === "scripted") b.push(`<span class="badge">scripted (no model)</span>`);
  else if (!m.model) b.push(`<span class="badge">no model in run</span>`);
  b.push(`<span class="badge">${esc(m.contract_version || "migproto.events/v0")}</span>`);
  return b.join("");
}

function kvTable(rows) {
  return `<table class="kv">${rows.map(([k, v]) => `<tr><th>${esc(k)}</th><td>${v}</td></tr>`).join("")}</table>`;
}

// ---------- sections ----------

function headerCard(run) {
  const m = run.meta || {}, man = run.manifest;
  let manifest = "";
  if (man) {
    manifest = `<h4>Fixture manifest</h4>
      ${man.note ? `<p>${esc(man.note)}</p>` : ""}
      ${kvTable([["fixture kind", val(man.kind)], ["source run", val(man.source_run_id)]])}
      ${(man.sanitization || []).length ? `<details><summary>Sanitization notes (${man.sanitization.length})</summary>
        <ul>${man.sanitization.map((s) => `<li>${esc(s)}</li>`).join("")}</ul></details>` : ""}`;
  }
  return `<section class="card">
    <div class="run-title"><h2>${esc(run.run_id)}</h2>${verdictBadge(m.verdict)}</div>
    <div class="badges">${runBadges(run)}</div>
    <h4>Requested change</h4>
    <p>${m.scenario ? esc(m.scenario)
      : unknown("not recorded in this run (the scenario is hard-coded in the runner; contracts gap G6)")}</p>
    ${manifest}
  </section>`;
}

function resultsCard(run) {
  const r = run.results || {};
  const perAttempt = (r.per_attempt || []).map((a) =>
    `<tr><td>${esc(a.attempt_id.split("/").pop())}</td><td>${verdictBadge(a.status)}</td>
      <td>${a.checks}</td><td>${a.failed_or_refused}</td></tr>`).join("");
  const calls = (r.model_calls || []).map((c) =>
    `<tr><td>${val(c.role)}</td><td>${nr(c.seconds)}</td><td>${nr(c.total_tokens)}</td><td>${c.error ? esc(c.error) : ""}</td></tr>`).join("");
  return `<section class="card">
    <h2>Results (from saved measurements only)</h2>
    ${kvTable([
      ["final verdict", verdictBadge(r.verdict)],
      ["run wall time (s)", nr(r.elapsed_s)],
      ["model", nr(r.model)],
      ["endpoint", nr(r.base_url)],
      ["checkpoint revision", nr(r.checkpoint_revision)],
      ["serving backend", nr(r.serving_backend)],
      ["hardware", nr(r.hardware)],
      ["model calls", nr(r.model_call_count)],
      ["model time (s, summed)", nr(r.model_seconds)],
      ["tokens (prompt / completion / total)", r.total_tokens == null ? nr(null)
        : `${r.prompt_tokens} / ${r.completion_tokens} / ${r.total_tokens}`],
    ])}
    <h4>Verification per attempt</h4>
    <div class="table-wrap"><table><tr><th>attempt</th><th>status</th><th>checks</th><th>failed or refused (v0 FAIL)</th></tr>${perAttempt}</table></div>
    ${calls ? `<details><summary>Per-call latency and usage (${r.model_calls.length})</summary>
      <div class="table-wrap"><table><tr><th>role</th><th>seconds</th><th>total tokens</th><th>error</th></tr>${calls}</table></div></details>` : ""}
    <h4>NeMo Gym evaluation</h4>
    <div class="placeholder">not connected (no <code>gym/results/</code> yet)</div>
  </section>`;
}

function planBlock(plan) {
  if (!plan) return `<p>${unknown("no plan recorded for this attempt")}</p>`;
  const b = plan.body || {};
  const stages = (b.stages || []).map((s) => `<li>
      <code>${esc(s.id)}</code> <span class="badge">${esc(s.kind)}${s.phase ? " · " + esc(s.phase) : ""}</span>
      ${s.repeat_until_zero_rows ? `<span class="badge">repeat until 0 rows</span>` : ""}
      ${s.description ? `<div class="muted">${esc(s.description)}</div>` : ""}
      ${(s.sql || []).length ? `<details><summary>SQL (${s.sql.length} statement${s.sql.length > 1 ? "s" : ""})</summary>
        <pre>${esc(s.sql.join("\n\n"))}</pre></details>` : ""}
    </li>`).join("");
  return `<p><strong>Plan <code>${esc(plan.plan_id)}</code></strong>${b.provenance ? ` <span class="badge">${esc(b.provenance)}</span>` : ""}</p>
    ${b.summary ? `<p>${esc(b.summary)}</p>` : ""}
    ${b.write_consistency ? `<p class="muted"><strong>Write consistency:</strong> ${esc(b.write_consistency)}</p>` : ""}
    ${b.revision_notes ? `<p><strong>Revision notes:</strong> ${esc(b.revision_notes)}</p>` : ""}
    <ol class="stages">${stages}</ol>`;
}

function matrixBlock(m) {
  if (!m.rows.length) {
    return `<p class="muted">No app-level cases with step scope in this attempt.</p>`;
  }
  const head = `<tr><th>case</th>${m.columns.map((c) =>
    `<th>${esc(c.replace(" -> ", " → "))}<div class="muted" style="font-weight:400">${
      c === "mixed" ? "crosses versions" : "writer → reader"}</div></th>`).join("")}</tr>`;
  let body = "", lastStage = null;
  for (const r of m.rows) {
    if (r.stage_id !== lastStage) {
      body += `<tr class="stage-row"><td colspan="${m.columns.length + 1}">stage ${val(r.stage_id)}</td></tr>`;
      lastStage = r.stage_id;
    }
    const cells = m.columns.map((c) => c === r.column
      ? `<td class="cell"><button class="cell-btn ${esc(r.status)}" data-obs="${esc(r.observation_id)}">${esc(r.status)}</button></td>`
      : `<td class="cell"><span class="cell-empty">${esc(m.empty_cell)}</span></td>`).join("");
    body += `<tr><td title="${esc(r.path.join(" → "))}"><code>${esc(r.check_name)}</code>
      ${r.definition_provenance ? `<span class="muted">(${esc(r.definition_provenance)})</span>` : ""}</td>${cells}</tr>`;
  }
  return `<div class="table-wrap"><table class="matrix">${head}${body}</table></div>`;
}

function dbChecksBlock(m) {
  if (!m.db_checks.length) return "";
  const byStage = {};
  for (const d of m.db_checks) (byStage[d.stage_id || "unknown"] ||= []).push(d);
  return `<h4>Checks without step-level app scope (database-level, runner gates, single-op checks)</h4>
    ${Object.entries(byStage).map(([stage, list]) => `<div style="margin:6px 0">
      <span class="muted">stage ${esc(stage)}:</span>
      <div class="chips">${list.map((d) =>
        `<button class="chip ${esc(d.status)}" data-obs="${esc(d.observation_id)}">${esc(d.check_name)} · ${esc(d.status)}</button>`).join("")}</div>
    </div>`).join("")}`;
}

function claimsBlock(claims, title) {
  if (!claims.length) return "";
  return `<h4>${esc(title)}</h4>${claims.map((c) => `<div class="claim">
      <span class="role">${esc(c.role)}</span> <span class="muted">${esc(c.kind || "")}</span>
      ${c.structured ? "" : `<span class="badge">unstructured claim (v0)</span>`}
      ${c.provenance ? `<span class="badge">${esc(c.provenance)}</span>` : ""}
      <div>${esc(c.text)}</div>
      <div class="muted">cited evidence: ${unknown("none (v0 claims carry no evidence IDs)")}</div>
    </div>`).join("")}`;
}

async function attemptBlock(run, a) {
  const m = await api(`/api/matrix?run=${encodeURIComponent(run.run_id)}&attempt=${encodeURIComponent(a.attempt_id)}`);
  const cls = a.ok === 1 ? "ok" : a.ok === 0 ? "bad" : "";
  const failed = a.failed || [];
  const executed = (a.stages || []).map((s) => esc(s.stage_id)).join(" → ") || unknown("no stage events");
  return `<div class="attempt ${cls}">
    <h3>Attempt ${esc(a.attempt)} ${verdictBadge(a.status)}
      <span class="muted">${a.checks_total} checks, ${a.checks_failed} FAIL</span></h3>
    ${a.halted ? `<p><span class="badge fail">halted</span> ${esc(a.halted)}</p>` : ""}
    ${failed.length ? `<p>Failed checks: ${failed.map((f) => `<code>${esc(f)}</code>`).join(", ")}</p>` : ""}
    ${a.plans.map(planBlock).join("") || planBlock(null)}
    <h4>Stages executed (from runner events)</h4><p>${executed}</p>
    <h4>Old/new compatibility matrix</h4>
    ${matrixBlock(m)}
    ${dbChecksBlock(m)}
    ${claimsBlock(a.claims, "Agent findings for this attempt")}
  </div>`;
}

async function renderPane(runId) {
  const run = await api(`/api/run?id=${encodeURIComponent(runId)}`);
  const attempts = [];
  for (const a of run.attempts) attempts.push(await attemptBlock(run, a));
  const notes = (run.runner_notes || []).filter((n) => n.kind === "observation");
  const pane = document.createElement("div");
  pane.innerHTML = `${headerCard(run)}
    <section class="card">
      <h2>Attempts (${run.attempts.length})</h2>
      <p class="muted">All attempts are shown, failed ones included, in execution order. v0 <code>FAIL</code>
        can mean a failed assertion, a runner refusal or an execution error (contracts G2): open the evidence to see which.</p>
      ${attempts.join("")}
    </section>
    ${run.claims_without_attempt.length ? `<section class="card"><details><summary>Investigator findings before any attempt (${run.claims_without_attempt.length}, attempt unknown)</summary>
      ${claimsBlock(run.claims_without_attempt, "Investigator")}</details></section>` : ""}
    ${notes.length ? `<section class="card"><h2>Runner notes (not pass/fail)</h2>
      ${notes.map((n) => `<details><summary><code>${esc(n.name || n.kind)}</code> ${n.msg ? esc(n.msg) : ""}</summary><pre>${esc(JSON.stringify(n, null, 2))}</pre></details>`).join("")}</section>` : ""}
    ${resultsCard(run)}`;
  return pane;
}

// ---------- evidence drawer ----------

async function openEvidence(id) {
  const o = await api(`/api/observation?id=${encodeURIComponent(id)}`);
  const schema = o.schema_state
    ? `<pre>${esc(JSON.stringify(o.schema_state, null, 2))}</pre>`
    : unknown("unknown (no schema event recorded for this stage)");
  const steps = o.steps.length ? `<div class="table-wrap"><table>
      <tr><th>#</th><th>app version</th><th>op</th><th>user</th><th>ok</th><th>value</th><th>error</th></tr>
      ${o.steps.map((s) => `<tr><td>${s.step}</td><td>${val(s.app_version)}</td><td>${val(s.op)}</td>
        <td>${val(s.user)}</td><td>${s.ok === 1 ? "yes" : s.ok === 0 ? "no" : unknown()}</td>
        <td>${s.value == null ? "" : `<code>${esc(s.value)}</code>`}</td><td>${s.error ? esc(s.error) : ""}</td></tr>`).join("")}
    </table></div>` : `<p class="muted">No steps: database-level or runner-level check, no app version.</p>`;
  $("#drawer-body").innerHTML = `
    <p><code>${esc(o.observation_id)}</code></p>
    <p>${verdictBadge(o.status)} <code>${esc(o.check_name)}</code></p>
    ${kvTable([
      ["run", val(o.run_id)], ["attempt", val(o.attempt_id)], ["plan", val(o.plan_id)],
      ["stage", val(o.stage_id)],
      ["app versions in steps", o.app_versions.length ? esc(o.app_versions.join(", ")) : unknown("none / not applicable")],
      ["outcome type", o.outcome ? esc(o.outcome) : unknown("unknown in v0 (fail / refused / error not distinguished)")],
      ["check definition from", val(o.definition_provenance)], ["executed by", val(o.executed_by)],
      ["scope source", val(o.scope_source)],
    ])}
    <h4>Schema state</h4>${schema}
    <h4>Steps</h4>${steps}
    ${o.error ? `<h4>Error</h4><pre>${esc(o.error)}</pre>` : ""}
    <h4>Recorded line (events.jsonl)</h4><pre>${esc(JSON.stringify(o.raw, null, 2))}</pre>
    <p><button data-history="${esc(o.check_name)}">Show this check in other runs and attempts</button></p>
    <div id="history"></div>`;
  $("#drawer").hidden = false;
}

async function showHistory(check) {
  const h = await api(`/api/history?check=${encodeURIComponent(check)}`);
  $("#history").innerHTML = `<h4>History (one row per scope, never merged)</h4>
    <div class="table-wrap"><table><tr><th>run</th><th>attempt</th><th>stage</th><th>status</th></tr>
    ${h.rows.map((r) => `<tr><td>${esc(r.run_id)}</td><td>${val(r.attempt_id && r.attempt_id.split("/").pop())}</td>
      <td>${val(r.stage_id)}</td><td><button class="chip ${esc(r.status)}" data-obs="${esc(r.observation_id)}">${esc(r.status)}</button></td></tr>`).join("")}
    </table></div>`;
}

// ---------- wiring ----------

async function render() {
  const a = $("#run-a").value, b = $("#run-b").value;
  const panes = $("#panes");
  panes.className = b ? "panes two" : "panes";
  panes.innerHTML = `<p class="muted">Loading…</p>`;
  try {
    const nodes = [await renderPane(a)];
    if (b) nodes.push(await renderPane(b));
    panes.replaceChildren(...nodes);
  } catch (err) {
    panes.innerHTML = `<div class="card">Failed to load: ${esc(err.message)}</div>`;
  }
  try { localStorage.setItem("replay.runs", JSON.stringify([a, b])); } catch (_) { /* optional */ }
}

document.addEventListener("click", (ev) => {
  const obs = ev.target.closest("[data-obs]");
  if (obs) return openEvidence(obs.dataset.obs);
  const hist = ev.target.closest("[data-history]");
  if (hist) return showHistory(hist.dataset.history);
});
$("#drawer-close").addEventListener("click", () => { $("#drawer").hidden = true; });
document.addEventListener("keydown", (ev) => { if (ev.key === "Escape") $("#drawer").hidden = true; });

(async function init() {
  const { runs, controller_available } = await api("/api/runs");
  $("#run-review").disabled = !controller_available;
  const opt = (r) => `<option value="${esc(r.run_id)}">${esc(r.run_id)} · ${esc(r.verdict || "?")}${r.source_kind === "fixture" ? " · fixture" : ""}</option>`;
  $("#run-a").innerHTML = runs.map(opt).join("");
  $("#run-b").innerHTML = `<option value="">(none)</option>` + runs.map(opt).join("");
  let saved = [];
  try { saved = JSON.parse(localStorage.getItem("replay.runs") || "[]"); } catch (_) { /* optional */ }
  const ids = new Set(runs.map((r) => r.run_id));
  if (saved[0] && ids.has(saved[0])) $("#run-a").value = saved[0];
  if (saved[1] && ids.has(saved[1])) $("#run-b").value = saved[1];
  $("#run-a").addEventListener("change", render);
  $("#run-b").addEventListener("change", render);
  if (runs.length) render();
  else $("#panes").innerHTML = `<div class="card">No runs found in examples/ or runs/.</div>`;
})();
