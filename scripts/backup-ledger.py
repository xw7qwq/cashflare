#!/usr/bin/env python3
"""Create a private, verified ledger tar.gz. Stop the service before running."""

import argparse
import gzip
import os
from pathlib import Path
import secrets
import stat
import tarfile


DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


def open_directory(path):
    """Open each path component without following symbolic links (Linux/macOS)."""
    fd = os.open(path.anchor, DIRECTORY_FLAGS)
    try:
        for component in path.parts[1:]:
            next_fd = os.open(component, DIRECTORY_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        return fd
    except BaseException:
        os.close(fd)
        raise


def fingerprint(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns)


def snapshot(directory_fd, prefix="data"):
    result = {prefix: fingerprint(os.fstat(directory_fd))}
    for name in sorted(os.listdir(directory_fd)):
        info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        relative = f"{prefix}/{name}"
        if stat.S_ISDIR(info.st_mode):
            child_fd = os.open(name, DIRECTORY_FLAGS, dir_fd=directory_fd)
            try:
                if fingerprint(os.fstat(child_fd)) != fingerprint(info):
                    raise ValueError("Ledger changed while preparing the backup")
                result.update(snapshot(child_fd, relative))
            finally:
                os.close(child_fd)
        elif stat.S_ISREG(info.st_mode):
            result[relative] = fingerprint(info)
        else:
            raise ValueError(f"Symlinks and special files cannot be backed up: {relative}")
    return result


def add_tree(archive, directory_fd, expected, prefix="data"):
    info = os.fstat(directory_fd)
    if fingerprint(info) != expected.get(prefix):
        raise ValueError("Ledger changed while creating the backup")
    header = tarfile.TarInfo(prefix)
    header.type = tarfile.DIRTYPE
    header.mode = 0o700
    header.mtime = int(info.st_mtime)
    archive.addfile(header)
    for name in sorted(os.listdir(directory_fd)):
        relative = f"{prefix}/{name}"
        info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        if fingerprint(info) != expected.get(relative):
            raise ValueError(f"Ledger changed while creating the backup: {relative}")
        if stat.S_ISDIR(info.st_mode):
            child_fd = os.open(name, DIRECTORY_FLAGS, dir_fd=directory_fd)
            try:
                add_tree(archive, child_fd, expected, relative)
            finally:
                os.close(child_fd)
        elif stat.S_ISREG(info.st_mode):
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_fd)
            with os.fdopen(fd, "rb") as contents:
                if fingerprint(os.fstat(fd)) != expected[relative]:
                    raise ValueError(f"Ledger changed while opening a file: {relative}")
                header = tarfile.TarInfo(relative)
                header.size = info.st_size
                header.mode = 0o600
                header.mtime = int(info.st_mtime)
                archive.addfile(header, contents)
                if fingerprint(os.fstat(fd)) != expected[relative]:
                    raise ValueError(f"Ledger changed while reading a file: {relative}")
        else:
            raise ValueError(f"Symlinks and special files cannot be backed up: {relative}")


def verify_archive(contents, expected):
    # Read through the gzip footer as tar readers can stop before its CRC check.
    contents.seek(0)
    with gzip.GzipFile(fileobj=contents, mode="rb") as compressed:
        while compressed.read(1024 * 1024):
            pass
    contents.seek(0)
    with tarfile.open(fileobj=contents, mode="r:gz") as archive:
        members = archive.getmembers()
        if {member.name for member in members} != set(expected) or len(members) != len(expected):
            raise ValueError("Archive verification failed: contents differ from the ledger")
        for member in members:
            mode, size = expected[member.name][2:4]
            if stat.S_ISDIR(mode):
                if not member.isdir():
                    raise ValueError("Archive verification failed: invalid directory")
            elif not member.isfile() or member.size != size:
                raise ValueError("Archive verification failed: invalid file")


def backup(source, output):
    source = Path(os.path.abspath(source.expanduser()))
    output = Path(os.path.abspath(output.expanduser()))
    if source == output or source in output.parents:
        raise ValueError("Store backups outside the ledger directory")
    source_fd = open_directory(source)
    parent_fd = None
    temporary_name = None
    try:
        parent_fd = open_directory(output.parent)
        try:
            os.stat(output.name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise FileExistsError(f"Refusing to overwrite an existing backup: {output}")
        expected = snapshot(source_fd)
        # Operate through the held descriptor so a renamed ancestor cannot turn
        # the output parent into a symlink between validation and file creation.
        temporary_name = f".cashflare-backup-{secrets.token_hex(16)}"
        temporary_fd = os.open(temporary_name, os.O_RDWR | os.O_CREAT | os.O_EXCL,
                               0o600, dir_fd=parent_fd)
        try:
            with os.fdopen(temporary_fd, "w+b") as destination:
                os.fchmod(destination.fileno(), 0o600)
                with tarfile.open(fileobj=destination, mode="w:gz") as archive:
                    add_tree(archive, source_fd, expected)
                destination.flush()
                os.fsync(destination.fileno())
                verify_archive(destination, expected)
            if snapshot(source_fd) != expected:
                raise ValueError("Ledger changed during backup; stop all writers and retry")
            # Unlike replace/rename, a hard link never overwrites an existing name.
            os.link(temporary_name, output.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd,
                    follow_symlinks=False)
        finally:
            os.unlink(temporary_name, dir_fd=parent_fd)
            temporary_name = None
    finally:
        if parent_fd is not None:
            os.close(parent_fd)
        os.close(source_fd)
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ledger", type=Path, help="Ledger directory; stop all writers first")
    parser.add_argument("output", type=Path, help="New .tar.gz file outside the ledger directory")
    args = parser.parse_args()
    try:
        output = backup(args.ledger, args.output)
    except (OSError, ValueError, tarfile.TarError, EOFError) as error:
        parser.exit(1, f"Backup failed: {error}\n")
    print(f"Verified private backup: {output}")


if __name__ == "__main__":
    main()
