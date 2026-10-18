"""Run Saleor app versions (manage.py commands and uvicorn servers) and talk GraphQL."""

import base64
import os
import socket
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import requests

from . import db
from .config import ADMIN_EMAIL, ADMIN_PASSWORD, CACHE_URL, VENV, VENV_PY, WORKTREES

TOKEN_TTL_S = 240
_TOKEN_LOCK = threading.Lock()


def saleor_env(dbname: str) -> dict:
    env = dict(os.environ)
    env.update(
        DATABASE_URL=db.url(dbname),
        SECRET_KEY="migproto-local-only",
        CACHE_URL=CACHE_URL,
        DEBUG="True",
        PYTHONUNBUFFERED="1",
        PATH=f"{VENV / 'bin'}:{env.get('PATH', '')}",
    )
    for key in ("CELERY_BROKER_URL", "DATABASE_URL_REPLICA", "VIRTUAL_ENV"):
        env.pop(key, None)
    return env


def manage(version: str, args: list[str], dbname: str, log_path: Path | None = None,
           timeout: int = 900) -> subprocess.CompletedProcess:
    cmd = [str(VENV_PY), "manage.py", *args]
    proc = subprocess.run(
        cmd, cwd=WORKTREES[version], env=saleor_env(dbname),
        capture_output=True, text=True, timeout=timeout,
    )
    if log_path:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(
            f"$ (cd {WORKTREES[version]} && {' '.join(cmd)})\n"
            f"# DATABASE_URL={db.url(dbname)}\n# exit={proc.returncode}\n"
            f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}\n"
        )
    return proc


def port_open(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def gid(pk: int) -> str:
    return base64.b64encode(f"User:{pk}".encode()).decode()


def pk_from_gid(value: str) -> int:
    return int(base64.b64decode(value).decode().split(":", 1)[1])


@dataclass
class OpResult:
    ok: bool
    value: object = None
    error: str | None = None
    raw: dict | None = None

    def as_dict(self) -> dict:
        return {"ok": self.ok, "value": self.value, "error": self.error}


@dataclass
class AppServer:
    """One Saleor version served by uvicorn on a fixed port."""

    role: str  # "old" or "new"
    version: str  # worktree key
    port: int
    dbname: str
    log_path: Path
    proc: subprocess.Popen | None = None
    _token: str | None = None
    _token_at: float = 0.0
    _log_fh: object = field(default=None, repr=False)

    @property
    def endpoint(self) -> str:
        return f"http://127.0.0.1:{self.port}/graphql/"

    def start(self, timeout: int = 90) -> None:
        if port_open(self.port):
            raise RuntimeError(f"port {self.port} already in use; stop stray servers first")
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log_fh = self.log_path.open("a")
        self.proc = subprocess.Popen(
            [str(VENV / "bin" / "uvicorn"), "saleor.asgi:application",
             "--host", "127.0.0.1", "--port", str(self.port)],
            cwd=WORKTREES[self.version], env=saleor_env(self.dbname),
            stdout=self._log_fh, stderr=subprocess.STDOUT,
        )
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(f"{self.role} server exited early, see {self.log_path}")
            if port_open(self.port):
                return
            time.sleep(0.5)
        raise RuntimeError(f"{self.role} server did not start within {timeout}s")

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        if self._log_fh:
            self._log_fh.close()
            self._log_fh = None
        self._token = None

    def alive(self) -> bool:
        return bool(self.proc and self.proc.poll() is None) or port_open(self.port)

    def log_tail(self, lines: int = 80) -> str:
        if not self.log_path.exists():
            return ""
        return "\n".join(self.log_path.read_text(errors="replace").splitlines()[-lines:])

    def _post(self, query: str, variables: dict | None, token: str | None) -> dict:
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        resp = requests.post(self.endpoint, json={"query": query, "variables": variables or {}},
                             headers=headers, timeout=60)
        try:
            body = resp.json()
        except ValueError:
            body = {"errors": [{"message": f"HTTP {resp.status_code}: {resp.text[:500]}"}]}
        body["_http_status"] = resp.status_code
        return body

    def token(self) -> tuple[str | None, str | None]:
        # Load threads (migproto/load.py) share one client; serialize token refreshes.
        with _TOKEN_LOCK:
            return self._token_locked()

    def _token_locked(self) -> tuple[str | None, str | None]:
        if self._token and time.monotonic() - self._token_at < TOKEN_TTL_S:
            return self._token, None
        body = self._post(
            "mutation($e:String!,$p:String!){tokenCreate(email:$e,password:$p)"
            "{token errors{field message}}}",
            {"e": ADMIN_EMAIL, "p": ADMIN_PASSWORD}, None,
        )
        err = _first_error(body)
        data = (body.get("data") or {}).get("tokenCreate") or {}
        if err or not data.get("token"):
            return None, err or str(data.get("errors"))
        self._token, self._token_at = data["token"], time.monotonic()
        return self._token, None

    def gql(self, query: str, variables: dict | None = None) -> dict:
        token, err = self.token()
        if err:
            return {"errors": [{"message": f"tokenCreate failed: {err}"}]}
        body = self._post(query, variables, token)
        if token_expired(body):
            # Saleor checks JWT expiry against the wall clock; a clock jump (e.g. WSL2 resyncing
            # after sleep) can expire a token we just fetched. Log in again once and retry.
            self._token = None
            token, err = self.token()
            if err:
                return {"errors": [{"message": f"tokenCreate failed: {err}"}]}
            body = self._post(query, variables, token)
        return body

    def read_note(self, pk: int) -> OpResult:
        body = self.gql("query($id:ID!){user(id:$id){id email note}}", {"id": gid(pk)})
        err = _first_error(body)
        user = (body.get("data") or {}).get("user")
        if err or user is None:
            return OpResult(False, error=err or "user not found", raw=body)
        return OpResult(True, value=user["note"], raw=body)

    def write_note(self, pk: int, note: str | None) -> OpResult:
        body = self.gql(
            "mutation($id:ID!,$note:String){customerUpdate(id:$id,input:{note:$note})"
            "{user{id note} errors{field message code}}}",
            {"id": gid(pk), "note": note},
        )
        err = _first_error(body, "customerUpdate")
        if err:
            return OpResult(False, error=err, raw=body)
        return OpResult(True, value=body["data"]["customerUpdate"]["user"]["note"], raw=body)

    def write_last_name(self, pk: int, value: str) -> OpResult:
        """customerUpdate(lastName); Saleor also records a NAME_ASSIGNED CustomerEvent row."""
        body = self.gql(
            "mutation($id:ID!,$v:String){customerUpdate(id:$id,input:{lastName:$v})"
            "{user{id lastName} errors{field message code}}}", {"id": gid(pk), "v": value})
        err = _first_error(body, "customerUpdate")
        if err:
            return OpResult(False, error=err, raw=body)
        return OpResult(True, value=body["data"]["customerUpdate"]["user"]["lastName"], raw=body)

    def read_staff_events(self) -> OpResult:
        """Number of CustomerEvent rows of the acting staff user (reads the events table).

        Saleor records NAME_ASSIGNED events against the staff member who made the change, not
        against the renamed customer, so name changes are visible on `me { events }`.
        """
        body = self.gql("query{me{id events{id type}}}")
        err = _first_error(body)
        me = (body.get("data") or {}).get("me")
        if err or me is None:
            return OpResult(False, error=err or "no current user", raw=body)
        return OpResult(True, value=len(me["events"] or []), raw=body)

    def write_language(self, pk: int, code: str) -> OpResult:
        body = self.gql(
            "mutation($id:ID!,$l:LanguageCodeEnum){customerUpdate(id:$id,input:{languageCode:$l})"
            "{user{id languageCode} errors{field message code}}}", {"id": gid(pk), "l": code})
        err = _first_error(body, "customerUpdate")
        if err:
            return OpResult(False, error=err, raw=body)
        return OpResult(True, value=body["data"]["customerUpdate"]["user"]["languageCode"], raw=body)

    def read_language(self, pk: int) -> OpResult:
        body = self.gql("query($id:ID!){user(id:$id){id languageCode}}", {"id": gid(pk)})
        err = _first_error(body)
        user = (body.get("data") or {}).get("user")
        if err or user is None:
            return OpResult(False, error=err or "user not found", raw=body)
        return OpResult(True, value=user["languageCode"], raw=body)

    def create_customer(self, email: str, note: str | None, include_note: bool = True) -> OpResult:
        body = self.gql(*create_customer_request(email, note, include_note))
        err = _first_error(body, "customerCreate")
        if err:
            return OpResult(False, error=err, raw=body)
        return OpResult(True, value=pk_from_gid(body["data"]["customerCreate"]["user"]["id"]),
                        raw=body)


def token_expired(body: dict) -> bool:
    return any("Signature has expired" in str(e.get("message", ""))
               for e in (body.get("errors") or []) if isinstance(e, dict))


def create_customer_request(email: str, note: str | None, include_note: bool = True):
    """GraphQL customerCreate. Without the note field for app versions whose input lacks it."""
    if include_note:
        return ("mutation($email:String!,$note:String){customerCreate(input:{email:$email,note:$note})"
                "{user{id note} errors{field message code}}}", {"email": email, "note": note})
    return ("mutation($email:String!){customerCreate(input:{email:$email})"
            "{user{id} errors{field message code}}}", {"email": email})


def _first_error(body: dict, mutation: str | None = None) -> str | None:
    if body.get("errors"):
        e = body["errors"][0]
        exc = (e.get("extensions") or {}).get("exception") or {}
        detail = exc.get("code") or ""
        return f"{detail}: {e.get('message')}" if detail else str(e.get("message"))
    if mutation:
        payload = (body.get("data") or {}).get(mutation) or {}
        if payload.get("errors"):
            return str(payload["errors"])
    return None
