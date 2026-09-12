#!/usr/bin/env python3
"""Show, set, or bump the OpenCode Multiuser Semantic Version.

Examples:
    python3 scripts/version.py show
    python3 scripts/version.py bump patch
    python3 scripts/version.py bump minor
    python3 scripts/version.py bump major
    python3 scripts/version.py set 6.9.0
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION_FILE = ROOT / "app" / "version.py"
SEMVER_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
ASSIGNMENT_RE = re.compile(r'^__version__\s*=\s*"([^"]+)"\s*$', re.MULTILINE)


def read_version() -> str:
    text = VERSION_FILE.read_text(encoding="utf-8")
    match = ASSIGNMENT_RE.search(text)
    if not match:
        raise SystemExit(f"Could not find __version__ in {VERSION_FILE}")
    version = match.group(1)
    if not SEMVER_RE.fullmatch(version):
        raise SystemExit(f"Invalid Semantic Version in {VERSION_FILE}: {version}")
    return version


def validate(version: str) -> str:
    if not SEMVER_RE.fullmatch(version):
        raise SystemExit("Version must use MAJOR.MINOR.PATCH, for example 6.8.0")
    return version


def write_version(version: str) -> None:
    version = validate(version)
    text = VERSION_FILE.read_text(encoding="utf-8")
    updated, count = ASSIGNMENT_RE.subn(f'__version__ = "{version}"', text, count=1)
    if count != 1:
        raise SystemExit(f"Could not update __version__ in {VERSION_FILE}")
    VERSION_FILE.write_text(updated, encoding="utf-8")


def bump(version: str, part: str) -> str:
    major, minor, patch = (int(x) for x in validate(version).split("."))
    if part == "major":
        return f"{major + 1}.0.0"
    if part == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage the OpenCode Multiuser Semantic Version")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("show", help="Print the current version")
    set_parser = sub.add_parser("set", help="Set an explicit MAJOR.MINOR.PATCH version")
    set_parser.add_argument("version")
    bump_parser = sub.add_parser("bump", help="Increment part of the Semantic Version")
    bump_parser.add_argument("part", choices=("major", "minor", "patch"))
    args = parser.parse_args()

    command = args.command or "show"
    current = read_version()
    if command == "show":
        print(current)
        return
    if command == "set":
        new = validate(args.version)
    else:
        new = bump(current, args.part)
    write_version(new)
    print(f"{current} -> {new}")


if __name__ == "__main__":
    main()
