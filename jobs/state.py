"""Small JSON records under LEDGER_STATE_DIR (default /state), one file per job."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def directory() -> Path:
    return Path(os.getenv("LEDGER_STATE_DIR", "/state"))


def load(name: str) -> dict[str, Any]:
    try:
        record = json.loads((directory() / f"{name}.json").read_text(encoding="utf-8"))
        return record if isinstance(record, dict) else {}
    except (OSError, ValueError):
        return {}


def save(name: str, record: dict[str, Any]) -> None:
    folder = directory()
    folder.mkdir(parents=True, exist_ok=True)
    descriptor, temp_path = tempfile.mkstemp(dir=folder, prefix=f".{name}.", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(record, handle, ensure_ascii=False, indent=2)
        os.replace(temp_path, folder / f"{name}.json")
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)
