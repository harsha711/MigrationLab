# Submission kit (due 2026-10-18, 11:00 ET)

## Checklist status

| Item | Status | Where |
|---|---|---|
| Team name | TODO (team) | form |
| Project description | draft below | form |
| Challenge selected | draft below; team decides | form |
| 3-5 min demo video, unlisted YouTube/Vimeo, camera on | TODO: record from the script below | form |
| Repo link with README | repo is public: https://github.com/harsha711/MigrationLab. README needs the final merge to `main` | `README.md` |
| README: quick start | done | `README.md` → Quick start |
| README: tech stack and architecture diagram | done (Mermaid, renders on GitHub) | `README.md` → Architecture |
| README: reproduce the demo (env vars, keys, sample .env) | done; `.env.example` is committed | `README.md` → How to reproduce |
| README: datasets/synthetic data and provenance | done | `README.md` → Data and provenance |
| README: known limitations and next steps | done | `README.md` |
| Deployed URL or short screen capture | no deployment; attach a 30-60 s capture of the UI (it can be cut from the video) | form |
| Team roster (names, roles, contacts) | TODO: fill names and contacts | `README.md` → Team, and form |

## Project description (paste into the form)

**Short (one line):** Small-model agents that write staged database migrations and prove them
against the old and new versions of a real app running side by side.

**Long:**

> A schema change can pass every test and still take production down: during a rolling
> deploy, the old app version is still running against the new schema. MigrationLab turns
> that gap into a loop. Agents running on NVIDIA Nemotron 3.5 Lightning, served by vLLM on a
> Brev H100, investigate the code, propose a staged migration plan (expand, backfill, sync
> trigger, deploy, retire, contract) and propose extra tests. A deterministic runner then
> executes every stage on a fresh copy of the database while the old and new versions of
> Saleor, a production-grade Django e-commerce app, serve requests against it. It checks
> every cross-version read and write and data preservation, and it refuses to drop the old
> schema while the old app is alive. Failed checks go back to the agents, which revise the
> plan. Only the runner decides pass or fail. The model never grades itself.
>
> We tested 14 hand-built migration scenarios and ran pre-registered experiments comparing
> multi-agent and single-agent topologies on the same budget. Every recorded review, failed
> attempts included, can be replayed in a review UI, where a human can add feedback and
> request a revision. The same runner is wrapped as a NeMo Gym resources server, so its
> verdicts can serve as rewards.

## Challenge selected (team decision)

Event tracks (from the event page): Best Use of NVIDIA Nemotron 3.5 Lightning, Best Use of
Red Hat vLLM, Best Use of NVIDIA NeMo Gym. What we can show for each:

- **Lightning:** every agent role runs on it. There are 190 recorded Lightning reviews,
  smoke tests included; the measured configurations are in `docs/results.md`.
- **vLLM:** one vLLM 0.27.1 server per H100 served up to 8 concurrent reviews. The full
  serving record (flags, driver/CUDA workarounds) is in `docs/serving.md`.
- **NeMo Gym:** the resources server is implemented and tested with scripted plans, but **not
  run with a trainer**. Mention it as planned work, not a result.

The written track requirements were never verified (`docs/bounties.md`). Do not claim to
qualify for any track.

## Demo video script (about 4 min 30 s)

Setup before recording:
- Run `uv run migproto ui --port 8765 --db /tmp/demo.sqlite`, then open
  `http://127.0.0.1:8765/`.
- Keep `docs/results.md` and the README diagram open in other tabs.
- Camera on.

**What "the core loop live" can mean here.** The safest live loop is **New review → scripted
proposals** (about 65 s, no GPU): the runner really executes, and attempt 1 fails while
attempt 2 passes. A live Lightning review from the UI needs the vLLM tunnel and takes 6-15
minutes, and LLM mode from the UI has never been exercised. If you show Lightning, use a
recorded Lightning run, and say that it is recorded.

### 1. Team (0:00-0:25)
"We're [team name]: [names]. We built MigrationLab, a migration reviewer that uses small-model
agents and proves its plans against a real app."

### 2. Pitch (0:25-1:05)
- "Database migrations break production in a way tests can't see. During a rolling deploy,
  the old version of your app is still running against the new schema."
- "Our agents, on Nemotron 3.5 Lightning served by vLLM, write a staged migration plan. Then a
  deterministic runner executes it while the old and new app versions run side by side,
  checks every cross-version read and write, and sends the failures back for a revision."
- "The model never grades itself. The runner does."

### 3. Live demo (1:05-2:15)
1. **The problem, about 15 s:** open the run `unsafe-rename` and its compatibility matrix.
   Say: "Saleor's 110 account tests pass, and the migration exits 0. But the old app's read,
   write and create all fail: `column account_user.note does not exist`."
2. **The loop live, about 40 s:** open **New review**, pick `rename-note`, scripted mode,
   topology `roles`, and start it. Narrate the timeline as the stages and checks stream in:
   attempt 1 fails 9 checks (old-app writes never reach the new column), then the runner
   sends that back and attempt 2 passes.
3. **A real Lightning review, about 20 s:** open `20261017-214321-agent-loop-llm` and show
   its revision story: 17 → 1 → 0 failed checks. The fix was adding the missing two-way sync
   trigger stage. Show the dry-run panel and "Starting database" card on a configuration K
   run if you have one ingested.

### 4. How we built it (2:15-3:45)
- **Architecture** (README diagram): controller → roles → vLLM. The plan goes to the runner,
  which clones Postgres and starts both Saleor versions. Evidence goes back to the planner,
  and every run goes into the evidence store behind the UI.
- **Model choice:** Lightning, 30B-A3B NVFP4, 32.9B total parameters counted from the
  checkpoint. That is under the 33B budget, so there's no room for a judge model. "That's why
  our judge is deterministic code."
- **Hardware:** one H100 per vLLM server, with up to 8 reviews running at once on one server.
  Throughput was the bottleneck: one review makes 4-40 model calls.
- **Challenges, each one found in our own run records:**
  - *Uncapped reasoning:* single calls produced 18-24k tokens. We capped reasoning with
    vLLM's thinking-token budget.
  - *Backfills that never finish:* this was the most common first-attempt failure. We added a
    pre-check, backtested on 203 recorded plans: it flags 18 of 18 failing plans and 0 of 25
    working ones.
  - *Planning blind:* the planner never saw the live table. We added a read-only profile of
    the starting database, and a dry run with a write probe on a throwaway copy.
  - *Driver mismatch:* the Brev image's driver was too old for vLLM's CUDA 13. We fixed it
    with CUDA compat packages and pinned JIT toolchains (`docs/serving.md`).
- **Honest evaluation:** we pre-registered the multi-agent vs single-agent test. Result: 21/36
  vs 19/36, p = 0.81, so no difference is established. "We report that rather than
  cherry-pick."

### 5. So what (3:45-4:15)
- "Every team ships migrations. The dangerous ones look fine in CI."
- "We showed that a 30B-class open model on one GPU can write and repair staged migrations
  when it works against execution evidence, and that the evidence keeps it honest."
- "Every claim we make is a replayable run, failures included. And the runner is a NeMo Gym
  environment, so the next step is training on its verdicts."

Do not say any of these: "zero downtime", "concurrency-safe", "multi-agent is better",
"qualifies for the bounty".
