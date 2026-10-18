# Agent prompt: run the second half of measurement batch H on this machine

You are a coding agent working on a teammate's Linux machine (32 GB RAM) for a hackathon team.
The demo is at 11:00 today (2026-10-18). Your one job: set up this machine as a second runner
for an already-running measurement batch, run 36 reviews with settings identical to the first
machine, and hand back the results. You are not developing features. Do not change any code.

Report to the person at this keyboard after each numbered phase below (one or two lines: what
you did, what you saw). If anything in a "STOP and report" line happens, stop and report;
do not improvise a fix that changes code, settings or the shared GPU server.

## Background (read once)

- Repo: https://github.com/harsha711/MigrationLab (project "migproto"). It is a migration
  reviewer: AI agents (the model Nemotron 3.5 Lightning) write a staged database migration
  plan for a Saleor (Django) app, and a deterministic runner executes the plan against old and
  new app versions running side by side on a local Postgres, then decides PASS/FAIL.
- The model is served by vLLM on one shared Brev H100 instance named `horizontal-jade-beetle`
  (event credits). vLLM listens only on 127.0.0.1:8000 on that instance; each runner machine
  reaches it through an SSH tunnel that the supervisor script opens itself.
- "Configuration H" is a pre-registered experiment: topology `parallel` (3 planner agents per
  round) versus `single` (one agent), on 9 scenarios, 4 reviews each per topology = 72 reviews.
  Harsha's laptop runs out of memory beyond 5 at once, so the batch is split by review index:
  the laptop does reviews 1-2 of every scenario x topology (36), this machine does reviews 3-4
  (36). Because the result is a statistical comparison, every setting must match exactly:
  same commit, same budgets, same thinking budget, same sampling. The only allowed differences
  are the machine and the worker count.
- Read `AGENTS.md` in the repo (sections "Commands", "Brev Lightning quick start",
  "Evidence rules", "Development rules") before phase 2. It is the project's rulebook.

## Hard rules (never break)

1. Never run `brev delete`, `brev stop`, or anything that restarts, reconfigures or kills vLLM
   on the instance. Never pass `--delete-when-done`. Read-only commands on the instance
   (`nvidia-smi`, `curl localhost:8000/...`) are fine.
2. Do not edit, commit, push, or stash anything in the repo. Stay detached at commit `19dd71f`.
   If `git status` ever shows modified tracked files, STOP and report.
3. Do not change any flag in the batch command except `--workers` (see phase 4). Do not set or
   change `MIGPROTO_LLM_*` variables beyond what the command shows.
4. Never print, copy or send SSH private keys, `~/.brev` contents, or any `.env` file. Use this
   person's own `brev login`; never someone else's key.
5. Do not run load tests or extra model calls against the endpoint. One `curl .../v1/models`
   check is fine.
6. Do not export `UV_PROJECT_ENVIRONMENT` in the shell (it breaks the runner's venv).
7. Stop a running batch only by `touch <batch dir>/STOP` (it stops after the current reviews).
   Never kill the supervisor mid-review unless the person tells you to.

## Phase 0: this machine is Windows + WSL2 (do this first)

Everything below runs inside the WSL2 Linux distro (Ubuntu), not in PowerShell.

- **Maximize memory for the runner.** Memory is the only thing limiting how many reviews this
  machine can run; the GPU is shared and has spare capacity.
  1. WSL2 gets only half the host RAM by default (16 GB of 32). Ask the person to create or edit
     `C:\Users\<them>\.wslconfig` on Windows with:
     ```
     [wsl2]
     memory=28GB
     swap=8GB
     ```
     (28 GB leaves about 4 GB for Windows itself.) Then `wsl --shutdown` in PowerShell and reopen
     the Ubuntu terminal. Verify with `free -g` inside WSL (total about 27-28).
  2. Free memory on the Windows side: ask the person to close everything they do not need for
     the next few hours (browsers with many tabs, Teams/Slack/Discord, IDEs, games, other
     launchers, Android emulators, other VMs). Windows memory pressure makes WSL swap.
  3. Free memory inside WSL: list what is running with
     `ps aux --sort=-rss | head -20` and `docker ps`. Show the person anything unrelated that
     uses more than ~200 MB (other containers, dev servers, Jupyter, old Python processes) and
     stop it **only with their OK** (`docker stop <name>`, `kill <pid>`). Never stop system
     processes, Docker itself, or this project's containers (`migproto-*` Postgres/Valkey).
  4. Report the final `free -m` "available" number before phase 4.
- **Docker:** either Docker Desktop with "Use the WSL 2 based engine" and WSL integration
  enabled for this distro, or Docker Engine installed inside WSL. `docker info` must work
  inside WSL without sudo. (The `systemctl --user start docker-desktop` hint in AGENTS.md
  is for native Linux; ignore it here.)
- **Filesystem:** clone into the Linux home (`~/migrationlab`), never under `/mnt/c/...`
  (that is 10x slower and breaks file permissions for Postgres and uv).
- **Packages inside WSL:** `sudo apt-get update && sudo apt-get install -y git tmux jq
  libmagic1 curl openssh-client`. Install `uv` with
  `curl -LsSf https://astral.sh/uv/install.sh | sh`, and the Brev CLI inside WSL
  (`brev` from https://docs.nvidia.com/brev, Linux install), not the Windows one: the
  supervisor calls `ssh horizontal-jade-beetle`, which needs Brev's SSH config in the WSL
  `~/.ssh/config` (written by `brev login` run inside WSL).
- **Sleep:** `systemd-inhibit` inside WSL does not stop Windows from sleeping. Ask the person to
  set Windows power settings to never sleep while plugged in (or run PowerToys Awake) for the
  duration, and keep the laptop on power. If Windows sleeps, the tunnel drops and reviews in
  flight are lost.
- **Keep WSL alive:** keep the Ubuntu terminal (with tmux) open; do not run `wsl --shutdown`
  while the batch runs.

## Phase 1: prerequisites

Check and report versions/availability of: Linux (`uname -a`), Docker running
(`docker info` succeeds without sudo; if Docker Desktop, `systemctl --user start docker-desktop`),
`git`, `uv` (install with `curl -LsSf https://astral.sh/uv/install.sh | sh` if missing), `tmux`,
`jq`, libmagic (`ldconfig -p | grep libmagic`; Debian/Ubuntu package `libmagic1`), and free
memory (`free -g`) and disk (`df -h ~`, need about 20 GB). Check these local ports are free:
55432, 56379, 8001-8042, 8300 (`ss -ltn`). If one is taken by something unrelated, STOP and
report which.

## Phase 2: exact code and local services

```bash
git clone https://github.com/harsha711/MigrationLab.git ~/migrationlab
cd ~/migrationlab
git checkout 19dd71f            # detached HEAD is expected
git log -1 --oneline            # must start with 19dd71f
uv sync
uv run migproto setup           # Docker Postgres+Valkey, pinned Saleor clone, worktrees (5-15 min)
uv run migproto baseline --skip-pytest   # seed template (a few minutes)
uv run migproto agent-loop --proposals scripted   # no model; ~1 min; must end PASS (exit 0)
uv run pytest -q                # must pass (about 344 tests, under a minute)
```

STOP and report if: `setup` or `baseline` fails twice, the scripted loop is not PASS, or tests
fail. Paste the last 30 lines of output.

## Phase 3: Brev access (read-only check)

```bash
brev login                      # this person's account; must be in the hackathon's Brev org
brev ls                         # must list horizontal-jade-beetle as RUNNING
ssh horizontal-jade-beetle 'curl -s localhost:8000/v1/models'   # must include nemotron-3.5-lightning
```

If `brev ls` does not show the instance, the person is probably not in the event org (or the
org must be selected with `brev set <org>`); STOP and report. Do not create any instance.

## Phase 4: run the batch

Worker count from the "available" memory in phase 0 step 4. Each worker runs two Saleor app
servers plus a database clone, about 2.2 GB (on the laptop 5 workers used about 11 GB), and you
must keep about 3 GB spare:
- available >= 14 GB: `--workers 5` (the maximum; more would slow the shared GPU for everyone);
- 11-14 GB: `--workers 4`;
- 9-11 GB: `--workers 3`;
- below 9 GB: STOP and report (free more memory first).
Use that number in place of `4` in the command below.

```bash
cd ~/migrationlab
tmux new -s brevH2
# inside tmux, one command (copy exactly):
MIGPROTO_LLM_THINKING_BUDGET=16384 CUDA_COMPAT=13-0 uv run python scripts/brev/supervise.py \
  --instance horizontal-jade-beetle \
  --instance-type 'scaleway_H100 (Scaleway via Shadeform, 1x H100 80GB PCIe, 24 vCPU, 240 GB RAM, $3.96/h)' \
  --started-at 2026-10-17T17:50 \
  --max-revisions 5 --max-calls 40 --max-tokens 450000 --max-wall 3000 --stop-usd 60 \
  --runs 2 --topologies parallel,single \
  --scenarios rename-note,raw-sql-rename,shorten-note,irreversible-data-migration,foreign-key-orphans,unique-last-name,drop-column,backfill-then-required,add-required-column \
  --workers <N from above> --port 8300 2>&1 | tee runs/brev-supervisor-console-H2.log
# detach with Ctrl-b d
```

Within 2 minutes `runs/brev-batch-<timestamp>/supervisor.log` must show
"vLLM already running on the instance; reusing it", "endpoint ready", "check_endpoint.sh OK",
then "RUN   1/36 rename-note parallel starting on worker 0". If instead it says it started `serve_lightning.sh`, or the endpoint check
fails, `touch runs/brev-batch-<timestamp>/STOP`, then STOP and report (it must never start its
own server).

Tell the person the batch directory name immediately; Harsha needs it.

Note: `--stop-usd` is an estimate from instance age, not real billing; 60 is intentional.
Ignore the dollar figures.

## Phase 5: watch (until all 36 finish, roughly 3-4 hours)

Every ~10 minutes check, without disturbing the run:
- `tail -n 5 runs/brev-batch-<ts>/supervisor.log` and `wc -l runs/brev-batch-<ts>/runs.jsonl`
  (one line per finished review).
- `free -m`: if "available" falls below 2000 MB, `touch runs/brev-batch-<ts>/STOP` and report.
- Lines containing ALERT, Traceback, EXCEPTION, or "endpoint unhealthy": report them at once.
  Verdicts PASS, FAIL, INCOMPLETE and ERROR are all normal results; do not "fix" a FAIL.
- Do not let the machine sleep (the supervisor inhibits sleep itself; keep the lid open and
  power connected).

Each review takes 8-50 minutes. That is expected: the model thinks for up to 16k tokens per call.

## Phase 6: hand back

The final DONE line will say the instance is "STILL RUNNING ... Delete it: brev delete ...".
IGNORE that line: the instance is shared and Harsha's batch is still using it. Never delete it.

When `runs.jsonl` has 36 lines and supervisor.log says DONE (or after a STOP, once no review
is running):

```bash
cd ~/migrationlab
B=runs/brev-batch-<ts>
tar czf ~/H-part2.tgz "$B" runs/brev-supervisor-console-H2.log $(jq -r '"runs/" + .run_id' "$B/runs.jsonl")
ls -lh ~/H-part2.tgz
```

Send `~/H-part2.tgz` to Harsha (it holds prompts, model replies and runner records; no keys).
Then give a final report: batch dir, number of reviews per verdict per topology
(`jq -r '[.topology,.verdict]|@tsv' $B/runs.jsonl | sort | uniq -c`), anything unusual, and
worker count used. Leave Docker services running unless the person asks otherwise; never touch
the Brev instance.
