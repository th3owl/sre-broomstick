#!/usr/bin/env python3
"""
Config-driven filesystem purge automation for DevOps/SRE cleanup jobs.

The script deletes aged files and directories based on INI sections of the form:

    [PATH:/some/path:ctrl]
    [PATH:/some/path:nonctrl]

It defaults to dry-run mode. Pass --execute to perform deletions.
"""

import argparse
import configparser
import fnmatch
import json
import logging
import os
import shutil
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path


SEPARATOR = "-" * 100
DEFAULT_LOG_FILE = "/tmp/ops_files_purge.log"

SAMPLE_CONFIG = """
[CTRL_HOSTS]
hosts = ["ctrl-1", "ctrl-3"]

[PATH:/home/oracle/:ctrl]
file_exclude = ["service.json", "adwcli.py", ".*", "*.env", "env"]
file_cleanup_age_hours = 48
delete_dirs = false

[PATH:/adbadmin:nonctrl]
file_exclude = []
file_cleanup_age_hours = 48
delete_dirs = true
dir_exclude = ["scripts"]
del_dir_with_alpha = false
dir_delete_rules = {"3": 365, "4": 365, "other_digit_age": 30}
""".strip()


logger = logging.getLogger("ops_files_purge")


def setup_logging(log_file):
    logging.basicConfig(
        filename=log_file,
        level=logging.INFO,
        format="%(asctime)s - %(levelname)-8s - %(message)s",
    )


def parse_json_value(value, field_name, expected_type):
    try:
        parsed = json.loads(value.replace("'", '"'))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{field_name} is not valid JSON: {exc}") from exc

    if not isinstance(parsed, expected_type):
        raise ValueError(f"{field_name} must be a {expected_type.__name__}")
    return parsed


def parse_json_list(value, field_name):
    return parse_json_value(value, field_name, list)


def parse_json_dict(value, field_name):
    return parse_json_value(value, field_name, dict)


def load_config(config_file):
    config = configparser.ConfigParser()
    config.optionxform = str
    read_files = config.read(config_file)

    if not read_files:
        raise FileNotFoundError(f"Config file not found or empty: {config_file}")

    if not config.sections():
        raise ValueError(f"Config file has no sections: {config_file}")

    if "CTRL_HOSTS" in config and "hosts" in config["CTRL_HOSTS"]:
        parse_json_list(config["CTRL_HOSTS"]["hosts"], "CTRL_HOSTS.hosts")

    path_sections = [section for section in config.sections() if section.startswith("PATH:")]
    if not path_sections:
        raise ValueError("Config must contain at least one PATH:<path>:<ctrl|nonctrl> section")

    for section in path_sections:
        parse_path_section(section)
        if "file_exclude" in config[section]:
            parse_json_list(config[section]["file_exclude"], f"{section}.file_exclude")
        if "dir_exclude" in config[section]:
            parse_json_list(config[section]["dir_exclude"], f"{section}.dir_exclude")
        if "dir_delete_rules" in config[section]:
            parse_json_dict(config[section]["dir_delete_rules"], f"{section}.dir_delete_rules")

    return config


def parse_path_section(section):
    try:
        path_part, host_type = section.removeprefix("PATH:").rsplit(":", 1)
    except ValueError as exc:
        raise ValueError(f"Invalid section name '{section}'. Expected PATH:<path>:<ctrl|nonctrl>") from exc

    if host_type not in {"ctrl", "nonctrl"}:
        raise ValueError(f"Invalid host type '{host_type}' in section '{section}'")
    if not path_part.startswith("/"):
        raise ValueError(f"PATH section must use an absolute path: '{section}'")

    return path_part, host_type


def is_ctrl_host(hostname, ctrl_hosts):
    return hostname in ctrl_hosts


def section_for_path(path, hostname, ctrl_hosts):
    host_type = "ctrl" if is_ctrl_host(hostname, ctrl_hosts) else "nonctrl"
    return f"PATH:{path}:{host_type}"


def get_ctrl_hosts(config):
    if "CTRL_HOSTS" in config and "hosts" in config["CTRL_HOSTS"]:
        return parse_json_list(config["CTRL_HOSTS"]["hosts"], "CTRL_HOSTS.hosts")
    return []


def get_base_paths(config):
    return sorted({parse_path_section(section)[0] for section in config.sections() if section.startswith("PATH:")})


def get_json_list(config, section, key, default=None):
    if section not in config or key not in config[section]:
        return [] if default is None else default
    return parse_json_list(config[section][key], f"{section}.{key}")


def get_json_dict(config, section, key):
    if section not in config or key not in config[section]:
        return {}
    return parse_json_dict(config[section][key], f"{section}.{key}")


def get_bool(config, section, key, default=False):
    if section not in config:
        return default
    return config[section].get(key, str(default)).lower() == "true"


def get_int(config, section, key):
    if section not in config or key not in config[section]:
        return None
    try:
        return int(config[section][key])
    except ValueError as exc:
        raise ValueError(f"{section}.{key} must be an integer") from exc


def matches_any(name, patterns):
    return any(fnmatch.fnmatch(name, pattern) for pattern in patterns)


def age_hours(path):
    mtime = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
    return (datetime.now(timezone.utc) - mtime).total_seconds() / 3600


def age_days(path):
    mtime = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
    return (datetime.now(timezone.utc) - mtime).days


def delete_file(path, threshold_hours, dry_run):
    action = "Would delete" if dry_run else "Deleted"
    if not dry_run:
        path.unlink()
    logger.info("%s file: %s because it is older than %s hours", action, path, threshold_hours)


def delete_tree(path, threshold_days, dry_run):
    action = "Would delete" if dry_run else "Deleted"
    if not dry_run:
        shutil.rmtree(path)
    logger.info("%s directory tree: %s because it is older than %s days", action, path, threshold_days)


def directory_threshold_days(dirname, allow_alpha_dirs, rules):
    if dirname.startswith("."):
        return None

    first_char = dirname[0]
    if dirname.isdigit():
        threshold = rules.get(first_char, rules.get(dirname, rules.get("other_digit_age")))
    elif allow_alpha_dirs:
        threshold = rules.get(dirname, rules.get("alphanum_age"))
    else:
        threshold = None

    if threshold is None:
        return None

    try:
        return int(threshold)
    except ValueError as exc:
        raise ValueError(f"Invalid directory age threshold for '{dirname}': {threshold}") from exc


def cleanup_files(base_path, section, config, dry_run):
    file_age_limit = get_int(config, section, "file_cleanup_age_hours")
    if file_age_limit is None:
        logger.info("Skipping file cleanup for %s: file_cleanup_age_hours is not set", base_path)
        return

    if "file_exclude" not in config[section]:
        logger.warning("Skipping file cleanup for %s: file_exclude is not defined", base_path)
        return

    exclude_patterns = get_json_list(config, section, "file_exclude")
    if not exclude_patterns:
        logger.warning("file_exclude is empty for %s; every file older than %s hours is eligible", base_path, file_age_limit)

    for entry in sorted(base_path.iterdir()):
        if not entry.is_file():
            continue
        if matches_any(entry.name, exclude_patterns):
            logger.info("Skipping excluded file: %s", entry)
            continue
        if age_hours(entry) >= file_age_limit:
            delete_file(entry, file_age_limit, dry_run)


def cleanup_directories(base_path, section, config, dry_run):
    delete_dirs = get_bool(config, section, "delete_dirs")
    rules = get_json_dict(config, section, "dir_delete_rules")
    if not delete_dirs:
        if rules:
            logger.warning("dir_delete_rules is set for %s but delete_dirs is false; skipping directories", base_path)
        return
    if not rules:
        logger.warning("delete_dirs is true for %s but dir_delete_rules is missing; skipping directories", base_path)
        return

    allow_alpha_dirs = get_bool(config, section, "del_dir_with_alpha")
    exclude_patterns = get_json_list(config, section, "dir_exclude")

    for root, dirnames, _ in os.walk(base_path, topdown=True):
        root_path = Path(root)
        if root_path == base_path:
            continue

        dirname = root_path.name
        if matches_any(dirname, exclude_patterns):
            logger.info("Skipping excluded directory tree: %s", root_path)
            dirnames.clear()
            continue

        threshold_days = directory_threshold_days(dirname, allow_alpha_dirs, rules)
        if threshold_days is None:
            logger.info("Skipping directory tree without matching rule: %s", root_path)
            dirnames.clear()
            continue

        current_age_days = age_days(root_path)
        if current_age_days >= threshold_days:
            delete_tree(root_path, threshold_days, dry_run)
            dirnames.clear()
        else:
            logger.info("Skipping directory tree: %s age=%s days threshold=%s days", root_path, current_age_days, threshold_days)


def cleanup_path(base_path_text, config, hostname, ctrl_hosts, dry_run):
    section = section_for_path(base_path_text, hostname, ctrl_hosts)
    if section not in config:
        logger.info("Skipping %s on host %s: section %s is not defined", base_path_text, hostname, section)
        return

    base_path = Path(base_path_text)
    if not base_path.is_dir():
        logger.warning("Skipping missing or non-directory path: %s", base_path)
        return

    logger.info("%s", SEPARATOR)
    logger.info("Processing %s with section %s", base_path, section)
    cleanup_files(base_path, section, config, dry_run)
    cleanup_directories(base_path, section, config, dry_run)


def parse_args(argv):
    parser = argparse.ArgumentParser(description="Config-driven filesystem purge automation.")
    parser.add_argument("--config", required=True, help="Path to purge config file.")
    parser.add_argument("--log-file", default=DEFAULT_LOG_FILE, help=f"Log file path. Default: {DEFAULT_LOG_FILE}")
    parser.add_argument("--hostname", default=socket.gethostname().split(".")[0], help="Override hostname for testing.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Preview eligible deletions. This is the default.")
    mode.add_argument("--execute", action="store_true", help="Actually delete eligible files/directories.")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv or sys.argv[1:])
    setup_logging(args.log_file)
    dry_run = not args.execute

    logger.info("%s", "=" * 100)
    logger.info("Starting purge automation on host=%s mode=%s", args.hostname, "dry-run" if dry_run else "execute")

    try:
        config = load_config(args.config)
        ctrl_hosts = get_ctrl_hosts(config)
        for base_path in get_base_paths(config):
            cleanup_path(base_path, config, args.hostname, ctrl_hosts, dry_run)
    except Exception as exc:
        logger.exception("Purge automation failed: %s", exc)
        print(f"ERROR: {exc}", file=sys.stderr)
        print(f"Sample config:\n{SAMPLE_CONFIG}", file=sys.stderr)
        return 1
    finally:
        logger.info("Finished purge automation")
        logger.info("%s", "=" * 100)

    return 0


if __name__ == "__main__":
    sys.exit(main())
