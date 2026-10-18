"""Reproducible environment setup: services, pinned clone, worktrees, venv."""

import os
import shutil
import subprocess
import time

from . import db
from .config import (BRANCHES, PATCHES, ROOT, SALEOR_CLONE, SALEOR_COMMIT, SALEOR_REPO, VENDOR,
                     VENV, WORKTREES)
from .runlog import MANUAL, RunLog


def sh(log: RunLog, cmd: list[str], cwd=None, env=None, check=True) -> subprocess.CompletedProcess:
    t0 = time.monotonic()
    proc = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True)
    log.event("command", cmd=" ".join(cmd), cwd=str(cwd or ROOT), exit=proc.returncode,
              seconds=round(time.monotonic() - t0, 1),
              stderr_tail=proc.stderr[-800:] if proc.returncode else "")
    if check and proc.returncode:
        raise RuntimeError(f"command failed ({proc.returncode}): {' '.join(cmd)}\n{proc.stderr[-2000:]}")
    return proc


def docker_ready() -> bool:
    return subprocess.run(["docker", "info"], capture_output=True).returncode == 0


def ensure_services(log: RunLog) -> None:
    if not docker_ready():
        sh(log, ["systemctl", "--user", "start", "docker-desktop"], check=False)
        for _ in range(60):
            if docker_ready():
                break
            time.sleep(3)
        else:
            raise RuntimeError("BLOCKER: docker daemon not reachable after starting docker-desktop")
    sh(log, ["docker", "compose", "up", "-d", "--wait"], cwd=ROOT)
    for _ in range(30):
        try:
            db.exists("postgres")
            return
        except Exception:
            time.sleep(1)
    raise RuntimeError("BLOCKER: postgres not reachable on 127.0.0.1:55432")


def ensure_clone(log: RunLog) -> None:
    VENDOR.mkdir(exist_ok=True)
    if not (SALEOR_CLONE / ".git").exists():
        sh(log, ["git", "init", "-q", str(SALEOR_CLONE)])
        sh(log, ["git", "remote", "add", "origin", SALEOR_REPO], cwd=SALEOR_CLONE)
        sh(log, ["git", "fetch", "-q", "--depth", "1", "origin", SALEOR_COMMIT], cwd=SALEOR_CLONE)
        sh(log, ["git", "checkout", "-q", "-b", "pinned", "FETCH_HEAD"], cwd=SALEOR_CLONE)
        sh(log, ["git", "config", "user.email", "migproto@local"], cwd=SALEOR_CLONE)
        sh(log, ["git", "config", "user.name", "migproto"], cwd=SALEOR_CLONE)
    head = sh(log, ["git", "rev-parse", "pinned"], cwd=SALEOR_CLONE).stdout.strip()
    if head != SALEOR_COMMIT:
        raise RuntimeError(f"pinned branch is at {head}, expected {SALEOR_COMMIT}")


def ensure_worktrees(log: RunLog) -> None:
    if not WORKTREES["old"].exists():
        sh(log, ["git", "worktree", "add", "-q", "--detach", str(WORKTREES["old"]), "pinned"],
           cwd=SALEOR_CLONE)
    from . import scenarios  # registers scenario variant worktrees in WORKTREES

    branches = dict(BRANCHES)
    for version in scenarios.variant_versions():
        branches.setdefault(version, f"exp/{scenarios.file_safe(version)}")
    for version, branch in branches.items():
        path = WORKTREES[version]
        if path.exists():
            continue
        patches = sorted((PATCHES / version).glob("*.patch"))
        if not patches:
            log.event("patch_missing", provenance=MANUAL, name=version,
                      msg=f"no patches in patches/{version}/; scenario variant not built")
            continue
        sh(log, ["git", "worktree", "add", "-q", "-b", branch, str(path), "pinned"], cwd=SALEOR_CLONE)
        sh(log, ["git", "am", "-q", *map(str, patches)], cwd=path)
        log.event("patch", provenance=MANUAL, name=version, patches=[p.name for p in patches])
    for version, path in WORKTREES.items():
        if not path.exists():
            continue
        rev = sh(log, ["git", "log", "--oneline", "-1"], cwd=path).stdout.strip()
        log.event("worktree", name=version, path=str(path), head=rev)


def ensure_venv(log: RunLog) -> None:
    env = dict(os.environ, UV_PROJECT_ENVIRONMENT=str(VENV))
    uv = shutil.which("uv") or "uv"
    sh(log, [uv, "sync", "--locked", "--python", "3.12"], cwd=SALEOR_CLONE, env=env)


def run(log: RunLog) -> dict:
    t0 = time.monotonic()
    ensure_services(log)
    ensure_clone(log)
    ensure_worktrees(log)
    ensure_venv(log)
    return {"setup_seconds": round(time.monotonic() - t0, 1), "saleor_commit": SALEOR_COMMIT}
