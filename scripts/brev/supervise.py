"""Supervise a Lightning test batch on a Brev instance from the runner machine.

Run with `uv run python scripts/brev/supervise.py --instance NAME --runs 1` (smoke test),
then again with `--runs 10` (measurement); the second call reuses the running server.

What it does, in order:
  1. Keeps the laptop awake for its lifetime (systemd-inhibit), because the runner runs here.
  2. Copies serve_lightning.sh to the instance and starts it, unless vLLM is already running.
     HF_TOKEN, if set locally, is sent over ssh stdin, never on a command line.
  3. Holds an SSH tunnel localhost:8000 -> instance 127.0.0.1:8000 and restarts it if it drops.
  4. Waits for /v1/models, copies the serving record (migproto-serving.env, environment.txt)
     and runs scripts/brev/check_endpoint.sh.
  5. Runs `agent-loop --proposals llm`, alternating roles and single, with one shared request
     and budget, until each topology has --runs runs. A failed run never stops the batch.
  6. Stops launching runs when the estimated spend reaches --stop-usd, when `STOP` exists in
     the batch directory, or when vLLM dies on the instance.
  7. Copies vllm.log back. With --delete-when-done it then runs `brev delete NAME`.

Everything is logged to runs/brev-batch-<ts>/supervisor.log as `<time> <LEVEL> <message>`,
with LEVEL one of INFO, RUN, WARN, ALERT, DONE; runs.jsonl has one line per review.
Spend is an estimate (elapsed hours since --started-at times --rate); check Brev billing.

`--local-url URL` skips ssh, the tunnel and the server, for dry runs on this machine.
"""

import argparse
import datetime as dt
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SERVE = ROOT / "scripts" / "brev" / "serve_lightning.sh"
CHECK = ROOT / "scripts" / "brev" / "check_endpoint.sh"
REMOTE_DIR = "migproto-serving"
SSH_OPTS = ["-o", "BatchMode=yes", "-o", "ServerAliveInterval=15",
            "-o", "ServerAliveCountMax=3", "-o", "ConnectTimeout=20"]


class Supervisor:
    def __init__(self, args):
        self.a = args
        self.started = (dt.datetime.fromisoformat(args.started_at).astimezone()
                        if args.started_at else dt.datetime.now().astimezone())
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        self.dir = ROOT / "runs" / f"brev-batch-{stamp}"
        self.dir.mkdir(parents=True)
        self.log_path = self.dir / "supervisor.log"
        self.tunnel = None
        self.stopping = False
        self.env = dict(os.environ)
        self.env.pop("UV_PROJECT_ENVIRONMENT", None)  # AGENTS.md: never leak into uv run
        self.env["MIGPROTO_LLM_BASE_URL"] = args.local_url or f"http://localhost:{args.port}/v1"
        self.env.setdefault("MIGPROTO_LLM_API_KEY", "unused")
        if args.instance_type:
            self.env["MIGPROTO_BREV_INSTANCE"] = args.instance_type

    # ---- logging and cost ----

    def log(self, level, msg):
        line = f"{dt.datetime.now().strftime('%H:%M:%S')} {level:<5} {msg}"
        print(line, flush=True)
        with self.log_path.open("a") as f:
            f.write(line + "\n")

    def spend(self) -> float:
        hours = (dt.datetime.now().astimezone() - self.started).total_seconds() / 3600
        return round(hours * self.a.rate, 2)

    # ---- remote helpers ----

    def ssh(self, command, stdin=None, timeout=120):
        return subprocess.run(["ssh", *SSH_OPTS, self.a.instance, command], input=stdin,
                              capture_output=True, text=True, timeout=timeout)

    def fetch(self, remote_name):
        r = subprocess.run(["scp", *SSH_OPTS, f"{self.a.instance}:{REMOTE_DIR}/{remote_name}",
                            str(self.dir / remote_name)], capture_output=True, text=True,
                           timeout=300)
        if r.returncode:
            self.log("WARN", f"could not copy {remote_name}: {r.stderr.strip()[:200]}")
        return r.returncode == 0

    # Bracketed patterns so pgrep never matches the ssh shell that runs it.
    def server_alive(self) -> bool:
        """vLLM itself is running (after install and download)."""
        r = self.ssh("pgrep -f '[v]llm serve' >/dev/null && echo up || echo down", timeout=60)
        return r.stdout.strip() == "up"

    def setup_alive(self) -> bool:
        """serve_lightning.sh is still running: installing, downloading or serving."""
        r = self.ssh("pgrep -f '[s]erve_lightning.sh' >/dev/null && echo up || echo down",
                     timeout=60)
        return r.stdout.strip() == "up"

    def describe_instance(self):
        r = self.ssh("nvidia-smi -L; df -h ~ | tail -1; nproc; free -g | sed -n 2p", timeout=60)
        if r.returncode:
            raise RuntimeError(f"cannot ssh to {self.a.instance}: {r.stderr.strip()[:300]}")
        self.log("INFO", "instance: " + " | ".join(r.stdout.split("\n")).strip(" |"))

    def start_server(self):
        if self.setup_alive():
            self.log("INFO", "vLLM already running on the instance; reusing it")
            return
        r = subprocess.run(["scp", *SSH_OPTS, str(SERVE), f"{self.a.instance}:serve_lightning.sh"],
                           capture_output=True, text=True, timeout=120)
        if r.returncode:
            raise RuntimeError(f"scp serve_lightning.sh failed: {r.stderr.strip()}")
        script = ""
        for name in ("HF_TOKEN", "CUDA_COMPAT", "EXTRA_FLAGS"):
            if os.environ.get(name):
                script += f"export {name}={shlex.quote(os.environ[name])}\n"
        script += (f"mkdir -p {REMOTE_DIR}\n"
                   f"nohup setsid bash serve_lightning.sh > {REMOTE_DIR}/serve.out 2>&1 "
                   "< /dev/null &\necho started\n")
        r = self.ssh("bash -s", stdin=script)
        if "started" not in r.stdout:
            raise RuntimeError(f"could not start vLLM: {r.stderr.strip()}")
        self.log("INFO", "started serve_lightning.sh on the instance (install + download first)")

    def remote_tail(self, n=40) -> str:
        r = self.ssh(f"tail -n {n} {REMOTE_DIR}/vllm.log 2>/dev/null || "
                     f"tail -n {n} {REMOTE_DIR}/serve.out", timeout=60)
        return r.stdout

    # ---- tunnel ----

    def _tunnel_loop(self):
        while not self.stopping:
            self.tunnel = subprocess.Popen(
                # Own connection: Brev's ssh_config sets ControlMaster auto, which would hand
                # the forward to the shared master and exit immediately.
                ["ssh", *SSH_OPTS, "-N", "-o", "ExitOnForwardFailure=yes",
                 "-o", "ControlMaster=no", "-o", "ControlPath=none", "-o", "RequestTTY=no",
                 "-L", f"{self.a.port}:127.0.0.1:8000", self.a.instance],
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
            self.tunnel.wait()
            if self.stopping:
                return
            err = (self.tunnel.stderr.read() or "").strip()[:200]
            self.log("WARN", f"tunnel exited ({self.tunnel.returncode}) {err}; restarting in 5 s")
            time.sleep(5)

    def start_tunnel(self):
        threading.Thread(target=self._tunnel_loop, daemon=True).start()

    # ---- endpoint ----

    def healthy(self) -> bool:
        try:
            with urllib.request.urlopen(self.env["MIGPROTO_LLM_BASE_URL"] + "/models",
                                        timeout=10) as r:
                ids = [m["id"] for m in json.load(r)["data"]]
            return self.env["MIGPROTO_LLM_MODEL"] in ids
        except Exception:
            return False

    def wait_ready(self):
        deadline = time.monotonic() + self.a.ready_timeout
        last_note = 0.0
        while time.monotonic() < deadline:
            if self.healthy():
                self.log("INFO", f"endpoint ready (spend so far ~${self.spend()})")
                return
            if not self.a.local_url and not self.setup_alive():
                raise RuntimeError("serve_lightning.sh exited on the instance:\n"
                                   + self.remote_tail())
            if time.monotonic() - last_note > 300:
                last_note = time.monotonic()
                tail = self.remote_tail(3).strip().splitlines() if not self.a.local_url else []
                self.log("INFO", f"waiting for vLLM (~${self.spend()}): "
                         + (tail[-1][:160] if tail else "no log yet"))
            time.sleep(15)
        raise RuntimeError(f"endpoint not ready after {self.a.ready_timeout} s:\n"
                           + ("" if self.a.local_url else self.remote_tail()))

    def load_serving_env(self):
        if self.a.local_url:
            return
        if not self.fetch("migproto-serving.env"):
            raise RuntimeError("no migproto-serving.env on the instance")
        self.fetch("environment.txt")
        for line in (self.dir / "migproto-serving.env").read_text().splitlines():
            m = re.match(r"export (MIGPROTO_\w+)=(.*)$", line)
            if m:
                value = shlex.split(m.group(2))
                self.env[m.group(1)] = value[0] if value else ""
        self.log("INFO", f"serving record: vLLM {self.env.get('MIGPROTO_SERVING_VERSION')}, "
                 f"{self.env.get('MIGPROTO_SERVING_HARDWARE')}, "
                 f"driver {self.env.get('MIGPROTO_SERVING_DRIVER')}")

    # ---- batch ----

    def write_request(self) -> Path:
        sys.path.insert(0, str(ROOT))
        from migproto.agents.request import DEFAULT_REQUEST
        request = json.loads(json.dumps(DEFAULT_REQUEST))
        request["request_id"] = f"brev-batch-{self.dir.name[len('brev-batch-'):]}"
        request["budget"] = {"max_revisions": 1, "max_model_calls": self.a.max_calls,
                             "max_total_tokens": self.a.max_tokens,
                             "max_wall_seconds": self.a.max_wall}
        path = self.dir / "request.json"
        path.write_text(json.dumps(request, indent=2) + "\n")
        self.log("INFO", f"request {path.name}: budget {request['budget']}")
        return path

    def run_one(self, n, topology, request) -> dict:
        out = self.dir / f"{n:02d}-{topology}.out"
        t0 = time.monotonic()
        cmd = ["uv", "run", "migproto", "agent-loop", "--proposals", self.a.proposals,
               "--topology", topology, "--request", str(request)]
        with out.open("w") as f:
            code = subprocess.run(cmd, cwd=ROOT, env=self.env, stdout=f,
                                  stderr=subprocess.STDOUT).returncode
        text = out.read_text()
        m = re.findall(r"summary\s+(\S+)\s+-> (\S+)", text)
        verdict, run_dir = m[-1] if m else ("UNKNOWN", None)
        rec = {"n": n, "topology": topology, "verdict": verdict, "exit": code,
               "run_id": Path(run_dir).name if run_dir else None,
               "seconds": round(time.monotonic() - t0, 1), "spend_usd": self.spend()}
        if run_dir and (Path(run_dir) / "summary.json").is_file():
            s = json.loads((Path(run_dir) / "summary.json").read_text())
            totals = s.get("agent_totals") or {}
            rec.update({k: totals.get(k) for k in ("model_calls", "total_tokens",
                                                     "wall_seconds", "exhausted_reason")})
            rec["checks_total"] = s.get("checks_total")
            rec["checks_failed"] = len(s.get("checks_failed") or [])
            rec["reason"] = s.get("reason")
        with (self.dir / "runs.jsonl").open("a") as f:
            f.write(json.dumps(rec) + "\n")
        return rec

    def batch(self):
        request = self.write_request()
        order = [t for _ in range(self.a.runs) for t in self.a.topologies]
        results = []
        for n, topology in enumerate(order, 1):
            if (self.dir / "STOP").exists():
                self.log("WARN", "STOP file found; not starting more runs")
                break
            if self.spend() >= self.a.stop_usd:
                self.log("ALERT", f"estimated spend ${self.spend()} reached --stop-usd "
                         f"{self.a.stop_usd}; not starting more runs")
                break
            if not self.a.local_url and not self.server_alive():
                self.log("ALERT", "vLLM is not running on the instance:\n" + self.remote_tail())
                break
            for _ in range(12):
                if self.healthy():
                    break
                time.sleep(10)
            else:
                self.log("ALERT", "endpoint unhealthy for 2 min; stopping the batch")
                break
            self.log("RUN", f"{n}/{len(order)} {topology} starting (~${self.spend()})")
            rec = self.run_one(n, topology, request)
            results.append(rec)
            self.log("RUN", f"{n}/{len(order)} {topology} {rec['verdict']} run={rec['run_id']} "
                     f"{rec['seconds']} s, calls={rec.get('model_calls')} "
                     f"tokens={rec.get('total_tokens')} "
                     f"stopped={rec.get('exhausted_reason')} (~${rec['spend_usd']})")
        return results

    # ---- main ----

    def main(self) -> int:
        self.log("INFO", f"batch dir {self.dir}; instance {self.a.instance or 'local'}; "
                 f"rate ${self.a.rate}/h from {self.started:%H:%M}; stop at ${self.a.stop_usd}")
        code = 0
        try:
            if not self.a.local_url:
                if subprocess.run(["ss", "-ltn", f"sport = :{self.a.port}"], capture_output=True,
                                  text=True).stdout.count("LISTEN"):
                    raise RuntimeError(f"local port {self.a.port} is already in use")
                self.describe_instance()
                self.start_server()
                self.start_tunnel()
            self.wait_ready()
            self.load_serving_env()
            r = subprocess.run([str(CHECK)], env=self.env, capture_output=True, text=True,
                               timeout=600)
            (self.dir / "check_endpoint.out").write_text(r.stdout + r.stderr)
            if r.returncode:
                raise RuntimeError("check_endpoint.sh failed:\n" + (r.stdout + r.stderr)[-1500:])
            self.log("INFO", "check_endpoint.sh OK")
            results = self.batch()
            counts = {}
            for rec in results:
                key = f"{rec['topology']}:{rec['verdict']}"
                counts[key] = counts.get(key, 0) + 1
            self.log("DONE", f"{len(results)} runs {counts}; estimated spend ~${self.spend()}")
        except Exception as exc:
            code = 1
            self.log("ALERT", f"supervisor stopped: {exc}")
        finally:
            self.stopping = True
            if not self.a.local_url:
                self.fetch("vllm.log")
                if self.a.delete_when_done:
                    r = subprocess.run(["brev", "delete", self.a.instance], capture_output=True,
                                       text=True, stdin=subprocess.DEVNULL, timeout=300)
                    self.log("DONE" if r.returncode == 0 else "ALERT",
                             f"brev delete {self.a.instance}: exit {r.returncode} "
                             f"{(r.stdout + r.stderr).strip()[-300:]}")
                else:
                    self.log("DONE", f"instance {self.a.instance} is STILL RUNNING and billing "
                             f"~${self.a.rate}/h. Delete it: brev delete {self.a.instance}")
            if self.tunnel and self.tunnel.poll() is None:
                self.tunnel.terminate()
        return code


def parse(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--instance", help="Brev instance name (ssh host alias)")
    p.add_argument("--instance-type", help="recorded as MIGPROTO_BREV_INSTANCE")
    p.add_argument("--started-at", help="instance creation time (ISO), default now")
    p.add_argument("--rate", type=float, default=3.96, help="USD per hour")
    p.add_argument("--stop-usd", type=float, default=18.0,
                   help="stop launching runs at this estimated spend (default leaves a reserve)")
    p.add_argument("--runs", type=int, default=1, help="runs per topology")
    p.add_argument("--topologies", default="roles,single")
    p.add_argument("--proposals", choices=["llm", "scripted"], default="llm")
    p.add_argument("--max-calls", type=int, default=10)
    p.add_argument("--max-tokens", type=int, default=40000)
    p.add_argument("--max-wall", type=float, default=900)
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--ready-timeout", type=int, default=3600)
    p.add_argument("--delete-when-done", action="store_true",
                   help="run `brev delete INSTANCE` after the batch (irreversible)")
    p.add_argument("--local-url", help="dry run against a local endpoint; no ssh or tunnel")
    p.add_argument("--model", help="override MIGPROTO_LLM_MODEL (dry runs)")
    a = p.parse_args(argv)
    a.topologies = [t for t in a.topologies.split(",") if t]
    if not a.local_url and not a.instance:
        p.error("--instance is required unless --local-url is given")
    return a


if __name__ == "__main__":
    args = parse()
    if not os.environ.get("MIGPROTO_INHIBITED") and shutil.which("systemd-inhibit"):
        os.environ["MIGPROTO_INHIBITED"] = "1"
        os.execvp("systemd-inhibit", ["systemd-inhibit", "--what=sleep:idle",
                                      "--who=migproto", "--why=Brev batch: runner is local",
                                      sys.executable, *sys.argv])
    os.environ.setdefault("MIGPROTO_LLM_MODEL", "nemotron-3.5-lightning")
    if args.model:
        os.environ["MIGPROTO_LLM_MODEL"] = args.model
    sys.exit(Supervisor(args).main())
