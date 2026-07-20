# Purge Automation

Config-driven filesystem cleanup automation for DevOps/SRE hosts.

This utility removes old files and old directory trees based on rules defined in an INI config file. It is useful for cleanup jobs where different host types need different retention rules.

The script is dry-run first. It only deletes files or directories when `--execute` is explicitly passed.

## What This Script Does

`ops_files_purge.py` reads a config file, detects the current host type, then applies cleanup rules to each configured path.

It can:

- delete files older than a configured number of hours
- skip files that match configured exclusion patterns
- delete old directory trees based on directory names and age rules
- skip protected directory trees
- apply different rules for control hosts and non-control hosts
- run in dry-run mode so users can preview what would be deleted

## Repository Files

```text
purge-automation/
  ops_files_purge.py
  ops_files_purge_exceptions.cfg
  examples/
    ops_files_purge_exceptions.sample.cfg
  tests/
    test_ops_files_purge.py
```

File purpose:

- `ops_files_purge.py`: main cleanup script
- `ops_files_purge_exceptions.cfg`: generic local test config using `/tmp/purge-automation-lab`
- `examples/ops_files_purge_exceptions.sample.cfg`: smaller sample config
- `tests/test_ops_files_purge.py`: automated local test cases using Python `unittest`

## Safety Model

Default mode is dry-run.

This command only logs what would be deleted:

```bash
python3 purge-automation/ops_files_purge.py \
  --config purge-automation/ops_files_purge_exceptions.cfg \
  --dry-run
```

This command actually deletes eligible files and directory trees:

```bash
python3 purge-automation/ops_files_purge.py \
  --config purge-automation/ops_files_purge_exceptions.cfg \
  --execute
```

Important: directory cleanup uses recursive deletion for eligible directory trees. Always check dry-run logs before using `--execute`.

## How Host Type Selection Works

The config has a `CTRL_HOSTS` section:

```ini
[CTRL_HOSTS]
hosts = ["ctrl-test-1", "ctrl-test-2"]
```

If the current hostname is listed there, the script uses `:ctrl` rules.

If the current hostname is not listed there, the script uses `:nonctrl` rules.

For local testing, you can override the detected hostname:

```bash
--hostname worker-test-1
```

`worker-test-1` is not a directory. It is only a fake hostname used to test non-control host behavior.

To test control host behavior:

```bash
--hostname ctrl-test-1
```

## Config File Format

Each cleanup section has this structure:

```ini
[PATH:/absolute/path:ctrl]
```

or:

```ini
[PATH:/absolute/path:nonctrl]
```

The path must be absolute.

Example:

```ini
[PATH:/tmp/purge-automation-lab/home:nonctrl]
file_exclude = ["tf_*.env", "tool.py", "cronjobs", ".*"]
file_cleanup_age_hours = 48
delete_dirs = false
```

This means:

- on non-control hosts
- inspect `/tmp/purge-automation-lab/home`
- delete files older than `48` hours
- do not delete files matching `tf_*.env`, `tool.py`, `cronjobs`, or hidden files
- do not delete directories under this path

## Config Keys

`file_exclude`

List of filename patterns to preserve.

```ini
file_exclude = ["*.env", "keep.json", ".*"]
```

Pattern examples:

- `*.env` preserves files ending in `.env`
- `.*` preserves hidden files
- `tool.py` preserves that exact filename
- `keep-*.sh` preserves matching shell scripts

`file_cleanup_age_hours`

Minimum file age before deletion.

```ini
file_cleanup_age_hours = 48
```

A file must be at least 48 hours old and must not match `file_exclude` before it is eligible for deletion.

`delete_dirs`

Enables or disables directory cleanup.

```ini
delete_dirs = true
```

If this is `false`, directory deletion is skipped even when `dir_delete_rules` exists.

`dir_exclude`

List of directory names or patterns to preserve.

```ini
dir_exclude = ["scripts", "stable", "templates"]
```

When a directory matches this list, the script skips that directory tree.

`del_dir_with_alpha`

Allows deletion rules to apply to alphanumeric directory names.

```ini
del_dir_with_alpha = true
```

If this is `false`, directories like `build-cache`, `test123`, or `alpha` are skipped unless they are excluded earlier.

`dir_delete_rules`

Directory age rules in days.

```ini
dir_delete_rules = {"3": 365, "4": 365, "other_digit_age": 30, "alphanum_age": 7}
```

Rule meaning:

- `"3": 365`: numeric directory names starting with `3` are eligible after 365 days
- `"4": 365`: numeric directory names starting with `4` are eligible after 365 days
- `"other_digit_age": 30`: other numeric directory names are eligible after 30 days
- `"alphanum_age": 7`: alphanumeric directory names are eligible after 7 days when `del_dir_with_alpha = true`

The config values for `file_exclude`, `dir_exclude`, and `dir_delete_rules` must be JSON-style values.

## Current Generic Config

The checked-in config intentionally uses local test paths:

```text
/tmp/purge-automation-lab/home
/tmp/purge-automation-lab/artifacts
/tmp/purge-automation-lab/jobs
/tmp/purge-automation-lab/jobs/scripts
```

These are safe example paths. Users can create them locally to understand script behavior before adapting the config to real hosts.

## Run Automated Tests

From the repo root:

```bash
cd /Users/rrsolomo/Documents/SRE
python3 -m unittest discover -s purge-automation/tests
```

Expected output:

```text
....
----------------------------------------------------------------------
Ran 4 tests

OK
```

The tests create temporary directories under `/tmp`, write their own temporary config file, run the script, and verify expected behavior.

The tests verify:

- dry-run logs eligible deletions but does not remove anything
- execute mode deletes old eligible files
- new files are preserved
- excluded files are preserved
- old eligible directory trees are deleted
- excluded directory trees are preserved
- control host and non-control host sections behave differently

## Manual Local Test

Use this when you want to see the script behavior yourself.

Start from the repo root:

```bash
cd /Users/rrsolomo/Documents/SRE
```

Create a local test directory and an old file:

```bash
mkdir -p /tmp/purge-automation-lab/home
touch /tmp/purge-automation-lab/home/old.tmp
touch -t 202401010000 /tmp/purge-automation-lab/home/old.tmp
```

Run dry-run mode:

```bash
python3 purge-automation/ops_files_purge.py \
  --config purge-automation/ops_files_purge_exceptions.cfg \
  --hostname worker-test-1 \
  --dry-run \
  --log-file /tmp/ops_files_purge_manual.log
```

Read the log:

```bash
cat /tmp/ops_files_purge_manual.log
```

Expected log line:

```text
Would delete file: /tmp/purge-automation-lab/home/old.tmp because it is older than 48 hours
```

Confirm the file still exists:

```bash
ls -l /tmp/purge-automation-lab/home/old.tmp
```

Now run execute mode:

```bash
python3 purge-automation/ops_files_purge.py \
  --config purge-automation/ops_files_purge_exceptions.cfg \
  --hostname worker-test-1 \
  --execute \
  --log-file /tmp/ops_files_purge_manual.log
```

Expected log line:

```text
Deleted file: /tmp/purge-automation-lab/home/old.tmp because it is older than 48 hours
```

Confirm the file was deleted:

```bash
ls -l /tmp/purge-automation-lab/home/old.tmp
```

The `ls` command should report that the file does not exist.

## Manual Directory Cleanup Test

Create an old alphanumeric directory under the test `scripts` path:

```bash
mkdir -p /tmp/purge-automation-lab/jobs/scripts/build-cache
touch /tmp/purge-automation-lab/jobs/scripts/build-cache/payload.txt
touch -t 202401010000 /tmp/purge-automation-lab/jobs/scripts/build-cache
touch -t 202401010000 /tmp/purge-automation-lab/jobs/scripts/build-cache/payload.txt
```

Run dry-run:

```bash
python3 purge-automation/ops_files_purge.py \
  --config purge-automation/ops_files_purge_exceptions.cfg \
  --hostname worker-test-1 \
  --dry-run \
  --log-file /tmp/ops_files_purge_manual.log
```

Expected log line:

```text
Would delete directory tree: /tmp/purge-automation-lab/jobs/scripts/build-cache because it is older than 7 days
```

Run execute mode:

```bash
python3 purge-automation/ops_files_purge.py \
  --config purge-automation/ops_files_purge_exceptions.cfg \
  --hostname worker-test-1 \
  --execute \
  --log-file /tmp/ops_files_purge_manual.log
```

Confirm the directory is gone:

```bash
ls -ld /tmp/purge-automation-lab/jobs/scripts/build-cache
```

## Log File Behavior

Logs are appended to the selected log file.

If you want a clean log for each manual test:

```bash
rm -f /tmp/ops_files_purge_manual.log
```

Then rerun the script.

## Common Commands

Validate Python syntax:

```bash
python3 -m py_compile purge-automation/ops_files_purge.py purge-automation/tests/test_ops_files_purge.py
```

Run tests:

```bash
python3 -m unittest discover -s purge-automation/tests
```

Check Git status:

```bash
git status
```

Review changes before commit:

```bash
git diff
```

Commit:

```bash
git add .gitignore README.md purge-automation
git commit -m "Add testable purge automation utility"
```
