#!/usr/bin/env python3
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "purge-automation" / "ops_files_purge.py"


class PurgeAutomationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="purge-automation-test-")
        self.root = Path(self.tmp.name)
        self.home = self.root / "home"
        self.jobs = self.root / "jobs"
        self.scripts = self.jobs / "scripts"
        self.home.mkdir()
        self.scripts.mkdir(parents=True)
        self.log_file = self.root / "purge.log"
        self.config_file = self.root / "purge.cfg"
        self.write_config()

    def tearDown(self):
        self.tmp.cleanup()

    def write_config(self):
        self.config_file.write_text(
            f"""
[CTRL_HOSTS]
hosts = ["ctrl-test-1"]

[PATH:{self.home}:ctrl]
file_exclude = ["keep.json", "*.env", ".*"]
file_cleanup_age_hours = 48
delete_dirs = false

[PATH:{self.home}:nonctrl]
file_exclude = ["tf_*.env", "worker-only.keep", ".*"]
file_cleanup_age_hours = 48
delete_dirs = false

[PATH:{self.jobs}:nonctrl]
file_exclude = []
file_cleanup_age_hours = 48
delete_dirs = true
dir_exclude = ["scripts"]
del_dir_with_alpha = false
dir_delete_rules = {{"3": 365, "4": 365, "other_digit_age": 30}}

[PATH:{self.scripts}:nonctrl]
file_exclude = ["runner.sh"]
file_cleanup_age_hours = 48
delete_dirs = true
dir_exclude = ["stable"]
del_dir_with_alpha = true
dir_delete_rules = {{"3": 7, "4": 7, "other_digit_age": 7, "alphanum_age": 7}}
""".strip()
            + "\n",
            encoding="utf-8",
        )

    def make_file(self, path, age_hours):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("test\n", encoding="utf-8")
        old_time = time.time() - age_hours * 3600
        os.utime(path, (old_time, old_time))
        return path

    def make_dir(self, path, age_days):
        path.mkdir(parents=True, exist_ok=True)
        (path / "payload.txt").write_text("test\n", encoding="utf-8")
        old_time = time.time() - age_days * 86400
        os.utime(path / "payload.txt", (old_time, old_time))
        os.utime(path, (old_time, old_time))
        return path

    def run_purge(self, *mode_args, hostname="worker-test-1"):
        cmd = [
            sys.executable,
            str(SCRIPT),
            "--config",
            str(self.config_file),
            "--log-file",
            str(self.log_file),
            "--hostname",
            hostname,
            *mode_args,
        ]
        return subprocess.run(cmd, cwd=REPO_ROOT, text=True, capture_output=True, check=False)

    def test_dry_run_does_not_delete_eligible_items(self):
        old_file = self.make_file(self.home / "old.tmp", 72)
        old_dir = self.make_dir(self.scripts / "build-cache", 10)

        result = self.run_purge("--dry-run")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(old_file.exists())
        self.assertTrue(old_dir.exists())
        log_text = self.log_file.read_text(encoding="utf-8")
        self.assertIn("Would delete file", log_text)
        self.assertIn("Would delete directory tree", log_text)

    def test_execute_deletes_old_files_and_preserves_excluded_files(self):
        old_file = self.make_file(self.home / "old.tmp", 72)
        new_file = self.make_file(self.home / "new.tmp", 1)
        excluded_file = self.make_file(self.home / "tf_keep.env", 72)
        hidden_file = self.make_file(self.home / ".hidden", 72)

        result = self.run_purge("--execute")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(old_file.exists())
        self.assertTrue(new_file.exists())
        self.assertTrue(excluded_file.exists())
        self.assertTrue(hidden_file.exists())

    def test_execute_deletes_matching_old_directories_and_preserves_excluded_dirs(self):
        old_alpha_dir = self.make_dir(self.scripts / "build-cache", 10)
        new_alpha_dir = self.make_dir(self.scripts / "recent-cache", 1)
        excluded_dir = self.make_dir(self.scripts / "stable", 30)
        numeric_dir = self.make_dir(self.jobs / "3001", 31)
        excluded_scripts_tree = self.scripts

        result = self.run_purge("--execute")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(old_alpha_dir.exists())
        self.assertTrue(new_alpha_dir.exists())
        self.assertTrue(excluded_dir.exists())
        self.assertTrue(numeric_dir.exists())
        self.assertTrue(excluded_scripts_tree.exists())

    def test_ctrl_hostname_uses_ctrl_section(self):
        old_ctrl_deleted = self.make_file(self.home / "old.log", 72)
        ctrl_excluded = self.make_file(self.home / "keep.json", 72)
        nonctrl_excluded_only = self.make_file(self.home / "worker-only.keep", 72)

        result = self.run_purge("--execute", hostname="ctrl-test-1")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(old_ctrl_deleted.exists())
        self.assertTrue(ctrl_excluded.exists())
        self.assertFalse(nonctrl_excluded_only.exists())


if __name__ == "__main__":
    unittest.main()
