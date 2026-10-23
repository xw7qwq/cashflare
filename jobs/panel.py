"""Push the ledger snapshot to the lucius7.dev panel (POST /api/v1/ingest/ledger).

The panel never connects to this server. A push happens when the snapshot
content changes (as_of moves every day in UTC+8, so at least daily); the
panel itself skips the write when the body is identical.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from typing import Any, Callable
from urllib.parse import urlsplit

from . import state

DEFAULT_URL = "https://lucius7.dev/api/v1/ingest/ledger"
USER_AGENT = "ledger-panel-push/1 (+https://github.com/xw7qwq/ledger)"
RETRY_BASE_SECONDS = 300
RETRY_MAX_SECONDS = 6 * 3600
STATE_NAME = "panel"


class PanelPushError(RuntimeError):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    # A redirect means a wrong URL; following it would turn the POST into a GET.
    def redirect_request(self, *args, **kwargs):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def settings() -> dict[str, str] | None:
    """The ingest URL and token, or None when pushing is not configured."""
    token = os.getenv("LEDGER_PANEL_INGEST_TOKEN", "").strip()
    if not token:
        return None
    url = os.getenv("LEDGER_PANEL_INGEST_URL", "").strip() or DEFAULT_URL
    parts = urlsplit(url)
    local = parts.scheme == "http" and parts.hostname in {"127.0.0.1", "localhost"}
    if not parts.hostname or (parts.scheme != "https" and not local):
        raise PanelPushError("LEDGER_PANEL_INGEST_URL must be an https URL")
    return {"url": url, "token": token}


def encode(snapshot: dict[str, Any]) -> bytes:
    return json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(snapshot: dict[str, Any]) -> str:
    """Content hash that ignores generated_at, which changes on every build."""
    content = {key: value for key, value in snapshot.items() if key != "generated_at"}
    return hashlib.sha256(encode(content)).hexdigest()


def _error_detail(error: urllib.error.HTTPError) -> str:
    try:
        detail = json.loads(error.read().decode("utf-8"))["error"]
        return f"{detail.get('code')}: {detail.get('message')}"[:200]
    except Exception:
        return str(error.reason)[:200]


def send(
    snapshot: dict[str, Any],
    url: str,
    token: str,
    *,
    timeout: float = 30,
    attempts: int = 3,
    sleep: Callable[[float], None] | None = None,
) -> dict[str, Any]:
    """POST the snapshot; retry rate limits, server errors and network failures."""
    request = urllib.request.Request(
        url,
        data=encode(snapshot),
        method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json", "User-Agent": USER_AGENT},
    )
    for attempt in range(1, attempts + 1):
        try:
            with _OPENER.open(request, timeout=timeout) as response:
                text = response.read().decode("utf-8", errors="replace")
            try:
                payload = json.loads(text or "{}")
            except ValueError:
                raise PanelPushError("panel answered with a body that is not JSON") from None
            data = payload.get("data") if isinstance(payload, dict) else None
            return data if isinstance(data, dict) else {}
        except urllib.error.HTTPError as error:
            if not (error.code == 429 or error.code >= 500) or attempt == attempts:
                raise PanelPushError(f"panel answered {error.code} ({_error_detail(error)})") from None
        except (urllib.error.URLError, OSError) as error:
            if attempt == attempts:
                raise PanelPushError(f"panel unreachable ({getattr(error, 'reason', error)})") from None
        (sleep or time.sleep)(2 ** attempt)
    raise PanelPushError("panel push was not attempted")


def due(record: dict[str, Any], content: str, now: dt.datetime) -> bool:
    """Changed content since the last successful push; failures back off."""
    failures = int(record.get("failures") or 0)
    if failures:
        wait = min(RETRY_BASE_SECONDS * 2 ** (failures - 1), RETRY_MAX_SECONDS)
        if now.timestamp() - float(record.get("last_failure_ts") or 0) < wait:
            return False
    return record.get("digest") != content


def push(snapshot: dict[str, Any], config: dict[str, str], now: dt.datetime, *, force: bool = False) -> dict[str, Any] | None:
    """Send the snapshot when due (or forced) and record the outcome. None means skipped."""
    record = state.load(STATE_NAME)
    content = digest(snapshot)
    if not force and not due(record, content, now):
        return None
    try:
        result = send(snapshot, config["url"], config["token"])
    except Exception as error:
        record.update(
            failures=int(record.get("failures") or 0) + 1,
            last_failure_at=now.isoformat(timespec="seconds"),
            last_failure_ts=now.timestamp(),
            last_error=str(error)[:300],
        )
        state.save(STATE_NAME, record)
        raise
    state.save(
        STATE_NAME,
        {
            "digest": content,
            "last_success_at": now.isoformat(timespec="seconds"),
            "as_of": snapshot.get("as_of"),
            "failures": 0,
            "stored": result.get("stored"),
            "bytes": result.get("bytes"),
        },
    )
    return result
