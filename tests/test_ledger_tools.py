"""Behavior checks for the private ledger lifecycle using only synthetic data."""

import importlib.util
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parent.parent


class LedgerToolsTest(unittest.TestCase):
    def setUp(self):
        self.sandbox = tempfile.TemporaryDirectory()
        self.addCleanup(self.sandbox.cleanup)
        # Resolve macOS /var -> /private/var before testing intentional symlinks.
        self.root = Path(self.sandbox.name).resolve()
        self.repo = self.root / "cashflare"
        (self.repo / "scripts").mkdir(parents=True)
        for name in ("init-ledger.py", "backup-ledger.py"):
            shutil.copyfile(ROOT / "scripts" / name, self.repo / "scripts" / name)
        self.template = self.repo / "examples" / "blank-ledger"
        (self.template / "imports").mkdir(parents=True)
        (self.template / "main.beancount").write_text('option "title" "Example ledger"\n')
        (self.template / "imports" / "README.txt").write_text("Synthetic fixture.\n")

    def run_tool(self, name, *arguments):
        return subprocess.run([sys.executable, str(self.repo / "scripts" / name),
                               *map(str, arguments)], text=True, capture_output=True,
                              cwd=self.root, check=False)

    def create_ledger(self):
        result = self.run_tool("init-ledger.py")
        self.assertEqual(result.returncode, 0, result.stderr)
        return self.repo / "data"

    def test_initialize_copies_template_privately_and_preserves_env(self):
        environment = self.repo / ".env"
        environment.write_text("CASHFLARE_UID=1234\n")
        target = self.create_ledger()
        for relative in ("main.beancount", "imports/README.txt"):
            self.assertEqual((target / relative).read_bytes(), (self.template / relative).read_bytes())
            self.assertEqual(stat.S_IMODE((target / relative).stat().st_mode), 0o600)
        for directory in (target, target / "imports"):
            self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)
        self.assertEqual(environment.read_text(), "CASHFLARE_UID=1234\n")

    def test_initialize_custom_destination_and_refuses_existing_directory(self):
        target = self.root / "private ledger"
        result = self.run_tool("init-ledger.py", target)
        self.assertEqual(result.returncode, 0, result.stderr)
        sentinel = target / "main.beancount"
        sentinel.write_text("Already edited\n")
        result = self.run_tool("init-ledger.py", target)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(sentinel.read_text(), "Already edited\n")
        empty = self.root / "empty"
        empty.mkdir()
        self.assertNotEqual(self.run_tool("init-ledger.py", empty).returncode, 0)

    def test_initialize_rejects_symlink_template(self):
        (self.template / "linked").symlink_to(self.template / "main.beancount")
        result = self.run_tool("init-ledger.py")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.repo / "data").exists())

    def test_backup_round_trip_and_private_permissions(self):
        ledger = self.create_ledger()
        output = self.root / "backup.tar.gz"
        result = self.run_tool("backup-ledger.py", ledger, output)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
        with tarfile.open(output, "r:gz") as archive:
            self.assertEqual(set(archive.getnames()), {"data", "data/main.beancount",
                                                      "data/imports", "data/imports/README.txt"})
            self.assertEqual(archive.extractfile("data/main.beancount").read(),
                             (ledger / "main.beancount").read_bytes())
            self.assertEqual(archive.extractfile("data/imports/README.txt").read(),
                             (ledger / "imports/README.txt").read_bytes())
            self.assertTrue(all(member.mode == (0o700 if member.isdir() else 0o600)
                                for member in archive.getmembers()))

    def test_backup_refuses_existing_output_and_inside_source(self):
        ledger = self.create_ledger()
        output = self.root / "existing.tar.gz"
        output.write_bytes(b"Keep this backup")
        self.assertNotEqual(self.run_tool("backup-ledger.py", ledger, output).returncode, 0)
        self.assertEqual(output.read_bytes(), b"Keep this backup")
        inside = ledger / "backup.tar.gz"
        self.assertNotEqual(self.run_tool("backup-ledger.py", ledger, inside).returncode, 0)
        self.assertFalse(inside.exists())

    def test_backup_refuses_links_without_touching_destinations(self):
        ledger = self.create_ledger()
        output = self.root / "backup.tar.gz"
        external = self.root / "outside.txt"
        external.write_text("Leave untouched")
        (ledger / "link").symlink_to(external)
        self.assertNotEqual(self.run_tool("backup-ledger.py", ledger, output).returncode, 0)
        self.assertFalse(output.exists())
        (ledger / "link").unlink()
        linked_directory = self.root / "linked-directory"
        linked_directory.symlink_to(ledger, target_is_directory=True)
        self.assertNotEqual(self.run_tool("backup-ledger.py", linked_directory, output).returncode, 0)
        output.symlink_to(external)
        self.assertNotEqual(self.run_tool("backup-ledger.py", ledger, output).returncode, 0)
        self.assertEqual(external.read_text(), "Leave untouched")
        self.assertEqual(list(self.root.glob(".cashflare-backup-*")), [])

    def test_failed_verification_does_not_publish_or_leave_partial_backup(self):
        ledger = self.create_ledger()
        spec = importlib.util.spec_from_file_location("backup_tool", self.repo / "scripts" / "backup-ledger.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        output = self.root / "backup.tar.gz"
        with mock.patch.object(module, "verify_archive", side_effect=ValueError("Corrupt archive")):
            with self.assertRaisesRegex(ValueError, "Corrupt archive"):
                module.backup(ledger, output)
        self.assertFalse(output.exists())
        self.assertEqual(list(self.root.glob(".cashflare-backup-*")), [])

    def test_ledger_change_during_backup_is_reported_without_publishing(self):
        ledger = self.create_ledger()
        spec = importlib.util.spec_from_file_location("backup_tool", self.repo / "scripts" / "backup-ledger.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        original_verify = module.verify_archive

        def change_ledger(contents, expected):
            original_verify(contents, expected)
            with (ledger / "main.beancount").open("a") as writer:
                writer.write("; Changed during backup\n")

        output = self.root / "backup.tar.gz"
        with mock.patch.object(module, "verify_archive", side_effect=change_ledger):
            with self.assertRaisesRegex(ValueError, "Ledger changed"):
                module.backup(ledger, output)
        self.assertFalse(output.exists())
        self.assertEqual(list(self.root.glob(".cashflare-backup-*")), [])


if __name__ == "__main__":
    unittest.main()
