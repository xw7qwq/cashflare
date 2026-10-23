"""Command line for the ledger jobs.

  python -m jobs snapshot [--output FILE]   build the panel snapshot and summarize it
  python -m jobs push [--force]             push the snapshot to the panel if it changed
  python -m jobs backup                     commit the books and push them to the backup remote
  python -m jobs run                        do push and backup every LEDGER_JOBS_INTERVAL seconds
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import signal
import sys
import threading
from pathlib import Path

from . import backup, panel, snapshot

UTC8 = dt.timezone(dt.timedelta(hours=8))


def log(job: str, message: str, level: str = "INFO") -> None:
    print(f"[{dt.datetime.now():%Y-%m-%d %H:%M:%S}] [{job}] [{level}] {message}", flush=True)


def ledger_file() -> str:
    return os.getenv("LEDGER_FILE", "/data/main.beancount")


def build(now: dt.datetime) -> dict:
    entries, errors, options = snapshot.load(ledger_file())
    as_of = now.astimezone(UTC8).date()
    generated_at = now.astimezone(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    result = snapshot.build_snapshot(entries, options, as_of, generated_at, errors)
    if result["error_count"]:
        log("snapshot", f"the ledger has {result['error_count']} errors; run bean-check", "WARN")
    if result["unconverted_currencies"]:
        log("snapshot", f"no price to {result['currency']} for {', '.join(result['unconverted_currencies'])}; left out of totals", "WARN")
    return result


def push_job(now: dt.datetime, force: bool = False) -> dict | None:
    config = panel.settings()
    if not config:
        return None
    result = panel.push(build(now), config, now, force=force)
    if result is not None:
        log("panel", f"pushed {result.get('bytes')} bytes ({'stored' if result.get('stored') else 'unchanged'})")
    return result


def backup_job(now: dt.datetime) -> None:
    result = backup.run(now)
    if result["committed"] or result["pushed"]:
        log("backup", f"{'committed' if result['committed'] else 'up to date'}, {'pushed' if result['pushed'] else 'not pushed'} ({(result['head'] or '')[:12]})")


def run_forever(interval: int) -> None:
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    log("jobs", f"running every {interval}s; panel push {'on' if os.getenv('LEDGER_PANEL_INGEST_TOKEN') else 'off'}, "
        f"backup remote {'set' if os.getenv('LEDGER_BACKUP_REMOTE') else 'not set'}")
    while not stop.is_set():
        now = dt.datetime.now(dt.timezone.utc)
        for name, job in (("panel", push_job), ("backup", backup_job)):
            try:
                job(now)
            except Exception as error:  # one failing job must not stop the other or the loop
                log(name, str(error), "ERROR")
        stop.wait(interval)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m jobs", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    shot = commands.add_parser("snapshot", help="build the panel snapshot and summarize it")
    shot.add_argument("--output", metavar="FILE", help="also write the snapshot JSON to FILE")
    send = commands.add_parser("push", help="push the snapshot to the panel if it changed")
    send.add_argument("--force", action="store_true", help="push even if nothing changed")
    commands.add_parser("backup", help="commit the books and push them to the backup remote")
    loop = commands.add_parser("run", help="run push and backup in a loop")
    loop.add_argument("--interval", type=int, default=int(os.getenv("LEDGER_JOBS_INTERVAL", "900")))
    args = parser.parse_args(argv)

    now = dt.datetime.now(dt.timezone.utc)
    try:
        if args.command == "snapshot":
            result = build(now)
            if args.output:
                Path(args.output).write_bytes(panel.encode(result))
            print(f"{result['as_of']}: {len(result['balances'])} balances, {len(result['monthly'])} monthly rows, "
                  f"{len(result['net_worth'])} net worth points, {len(result['transactions'])} transactions, "
                  f"{len(panel.encode(result))} bytes")
        elif args.command == "push":
            if not panel.settings():
                raise panel.PanelPushError("LEDGER_PANEL_INGEST_TOKEN is not set")
            if push_job(now, force=args.force) is None:
                print("nothing changed since the last push (or waiting after a failure); --force sends anyway")
        elif args.command == "backup":
            backup_job(now)
        else:
            run_forever(max(60, args.interval))
    except (panel.PanelPushError, backup.BackupError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
