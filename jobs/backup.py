"""Commit the books in the data directory and push them to the private backup repository.

The data directory is its own Git repository (created on first run). Every run
commits whatever changed; when LEDGER_BACKUP_REMOTE is set, unpushed commits go
to that remote with the deploy key at LEDGER_BACKUP_KEY. Without a remote the
history still accumulates locally.
"""
from __future__ import annotations

import datetime as dt
import os
import re
import subprocess
from pathlib import Path
from typing import Any

from . import state

AUTHOR_NAME = "ledger backup"
AUTHOR_EMAIL = "ledger-backup@users.noreply.github.com"
DEFAULT_KEY = "/run/secrets/ledger_backup_key"
# The tooling repository is public: never let the books end up there.
PUBLIC_REPOSITORY = re.compile(r"[/:]xw7qwq/(?:ledger|cashflare)(?:\.git)?/?$", re.IGNORECASE)
UTC8 = dt.timezone(dt.timedelta(hours=8))


class BackupError(RuntimeError):
    pass


def data_directory() -> Path:
    configured = os.getenv("LEDGER_DATA_DIR", "").strip()
    if configured:
        return Path(configured)
    return Path(os.getenv("LEDGER_FILE", "/data/main.beancount")).parent


def _git(directory: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    command = [
        "git",
        "-c", f"safe.directory={directory}",
        "-c", f"user.name={AUTHOR_NAME}",
        "-c", f"user.email={AUTHOR_EMAIL}",
        "-C", str(directory),
        *args,
    ]
    environment = dict(os.environ)
    key = os.getenv("LEDGER_BACKUP_KEY", DEFAULT_KEY)
    known_hosts = state.directory() / "known_hosts"
    environment["GIT_SSH_COMMAND"] = (
        f"ssh -i {key} -o IdentitiesOnly=yes -o BatchMode=yes "
        f"-o StrictHostKeyChecking=accept-new -o UserKnownHostsFile={known_hosts}"
    )
    environment["GIT_TERMINAL_PROMPT"] = "0"
    result = subprocess.run(command, capture_output=True, text=True, env=environment)
    if check and result.returncode != 0:
        message = (result.stderr or result.stdout).strip().splitlines()
        raise BackupError(f"git {args[0]} failed: {message[-1] if message else result.returncode}")
    return result


def _revision(directory: Path, name: str) -> str | None:
    result = _git(directory, "rev-parse", "--verify", "--quiet", name, check=False)
    return result.stdout.strip() or None


def run(now: dt.datetime | None = None) -> dict[str, Any]:
    now = now or dt.datetime.now(dt.timezone.utc)
    directory = data_directory()
    if not directory.is_dir():
        raise BackupError(f"data directory {directory} does not exist")
    remote = os.getenv("LEDGER_BACKUP_REMOTE", "").strip()
    if remote and PUBLIC_REPOSITORY.search(remote):
        raise BackupError("LEDGER_BACKUP_REMOTE points at the public tooling repository; use the private ledger-data repository")

    if not (directory / ".git").exists():
        _git(directory, "init", "--quiet", "--initial-branch=main")
    _git(directory, "add", "--all")
    committed = _git(directory, "diff", "--cached", "--quiet", check=False).returncode == 1
    if committed:
        stamp = now.astimezone(UTC8).strftime("%Y-%m-%d %H:%M")
        _git(directory, "commit", "--quiet", "--message", f"backup: {stamp} (UTC+8)")
    head = _revision(directory, "HEAD")

    pushed = False
    if remote and head:
        current = _git(directory, "remote", "get-url", "origin", check=False).stdout.strip()
        if not current:
            _git(directory, "remote", "add", "origin", remote)
        elif current != remote:
            _git(directory, "remote", "set-url", "origin", remote)
        if _revision(directory, "refs/remotes/origin/main") != head:
            _git(directory, "push", "--quiet", "origin", "HEAD:refs/heads/main")
            _git(directory, "update-ref", "refs/remotes/origin/main", head)
            pushed = True

    record = {
        "last_run_at": now.isoformat(timespec="seconds"),
        "head": head,
        "remote": bool(remote),
        "last_commit_at": now.isoformat(timespec="seconds") if committed else state.load("backup").get("last_commit_at"),
        "last_push_at": now.isoformat(timespec="seconds") if pushed else state.load("backup").get("last_push_at"),
    }
    state.save("backup", record)
    return {"committed": committed, "pushed": pushed, "head": head}
