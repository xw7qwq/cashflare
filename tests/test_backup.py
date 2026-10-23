"""Backup contracts with a temporary data directory and a local bare repository."""
import datetime as dt
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jobs import backup, state

NOW = dt.datetime(2026, 10, 23, 16, 0, tzinfo=dt.timezone.utc)


def git(*args, cwd=None):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


class BackupTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.data = self.root / "data"
        self.data.mkdir()
        (self.data / "main.beancount").write_text('option "operating_currency" "CNY"\n', encoding="utf-8")
        self.remote = self.root / "ledger-data.git"
        git("init", "--quiet", "--bare", str(self.remote))
        environment = patch.dict(os.environ, {"LEDGER_DATA_DIR": str(self.data), "LEDGER_STATE_DIR": str(self.root / "state")})
        environment.start()
        self.addCleanup(environment.stop)
        os.environ.pop("LEDGER_BACKUP_REMOTE", None)

    def test_commits_locally_without_a_remote(self):
        result = backup.run(NOW)
        self.assertEqual((result["committed"], result["pushed"]), (True, False))
        self.assertEqual(git("log", "--format=%s", cwd=self.data), "backup: 2026-10-24 00:00 (UTC+8)")
        self.assertEqual(backup.run(NOW)["committed"], False)

    def test_pushes_new_commits_only(self):
        with patch.dict(os.environ, {"LEDGER_BACKUP_REMOTE": str(self.remote)}):
            self.assertEqual(backup.run(NOW), {"committed": True, "pushed": True, "head": git("rev-parse", "HEAD", cwd=self.data)})
            self.assertEqual(backup.run(NOW)["pushed"], False)
            (self.data / "main.beancount").write_text('option "operating_currency" "CNY"\n2026-01-01 open Assets:Cash\n', encoding="utf-8")
            result = backup.run(NOW + dt.timedelta(minutes=15))
        self.assertEqual((result["committed"], result["pushed"]), (True, True))
        self.assertEqual(git("rev-parse", "main", cwd=self.remote), result["head"])
        self.assertEqual(state.load("backup")["head"], result["head"])

    def test_refuses_the_public_tooling_repository(self):
        for remote in ("git@github.com:xw7qwq/ledger.git", "https://github.com/xw7qwq/cashflare"):
            with patch.dict(os.environ, {"LEDGER_BACKUP_REMOTE": remote}), self.assertRaises(backup.BackupError):
                backup.run(NOW)
        self.assertFalse((self.data / ".git").exists())

    def test_missing_data_directory_is_an_error(self):
        with patch.dict(os.environ, {"LEDGER_DATA_DIR": str(self.root / "missing")}), self.assertRaises(backup.BackupError):
            backup.run(NOW)


if __name__ == "__main__":
    unittest.main()
