#!/usr/bin/env python3
"""Copy the blank ledger into a new private directory (Python standard library)."""

import argparse
import os
from pathlib import Path
import shutil
import stat


REPO = Path(__file__).resolve().parent.parent


def check_path(path):
    """Reject symlinks in the supplied path, including ancestor directories."""
    for part in reversed((path, *path.parents)):
        if part.is_symlink():
            raise ValueError(f"Symlink paths are not supported: {part}")


def validate_template(source):
    check_path(source)
    if not source.is_dir():
        raise ValueError(f"Blank ledger template not found: {source}")
    for directory, dirs, files in os.walk(source, followlinks=False):
        for name in dirs + files:
            entry = Path(directory) / name
            mode = entry.lstat().st_mode
            if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
                raise ValueError(f"Template contains a symlink or special file: {entry}")


def copy_private(source, target):
    for entry in sorted(source.iterdir()):
        destination = target / entry.name
        if entry.is_symlink():
            raise ValueError(f"Template contains a symlink: {entry}")
        if entry.is_dir():
            destination.mkdir(mode=0o700)
            destination.chmod(0o700)
            copy_private(entry, destination)
        else:
            fd = os.open(entry, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(fd, "rb") as incoming:
                if not stat.S_ISREG(os.fstat(incoming.fileno()).st_mode):
                    raise ValueError(f"Template contains a special file: {entry}")
                out_fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(out_fd, "wb") as outgoing:
                    os.fchmod(outgoing.fileno(), 0o600)
                    shutil.copyfileobj(incoming, outgoing)


def initialize(target):
    source = REPO / "examples" / "blank-ledger"
    target = Path(os.path.abspath(target.expanduser()))
    check_path(target)
    validate_template(source)
    if not target.parent.is_dir():
        raise ValueError(f"Create the destination parent directory first: {target.parent}")
    if target == source or source in target.parents:
        raise ValueError("The destination must be outside the blank ledger template")
    # mkdir is exclusive: even an existing empty directory must never be overwritten.
    target.mkdir(mode=0o700)
    try:
        target.chmod(0o700)
        copy_private(source, target)
    except BaseException:
        shutil.rmtree(target)
        raise
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", nargs="?", type=Path, default=REPO / "data")
    args = parser.parse_args()
    try:
        target = initialize(args.target)
    except (OSError, ValueError) as error:
        parser.exit(1, f"Initialization failed: {error}\n")
    print(f"Created private ledger: {target}")
    print("Set CASHFLARE_UID and CASHFLARE_GID in .env as described in README before starting Docker.")


if __name__ == "__main__":
    main()
