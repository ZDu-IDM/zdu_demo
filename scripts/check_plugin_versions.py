#!/usr/bin/env python3
"""check_plugin_versions.py — a plugin whose skills changed must bump its version.

If a PR changes any file under a plugin's skills/ (except evals/), that
plugin's plugin.json `version` must be valid semver and higher than on the
base branch. Deleted files count; editing plugin.json alone does not. New
plugins need no bump, but their version must still parse.

Whether the bump is the right size (PATCH vs MAJOR) is left to review.

Usage:  python scripts/check_plugin_versions.py [--base <ref>]
        BASE_SHA=<sha> python scripts/check_plugin_versions.py
"""

import json
import os
import subprocess
import sys
from pathlib import Path, PurePosixPath

# PLUGIN_ROOTS is unused here directly but re-exported: test_plugin_roots.py
# checks this module's attribute against utils.PLUGIN_ROOTS by identity.
from utils import (HARNESS_DIRS, PLUGIN_ROOTS, classify_path,  # noqa: F401
                   parse_version)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

MANIFEST = PurePosixPath(".claude-plugin/plugin.json")


def relevant(paths):
    """Keep changed paths that can require a version bump."""
    for p in paths:
        classified = classify_path(p)
        if classified is None:
            continue
        _root, below = classified
        if any(d in below for d in HARNESS_DIRS):
            continue
        yield p


def owning_plugin(rel, root):
    """Return (plugin_dir, skill_name) for a changed skill file, or None.

    Walks up to the nearest directory holding .claude-plugin/plugin.json, so a
    deleted file still resolves as long as its plugin exists. Returns None for
    a file that is not under that plugin's skills/ — plugin.json and anything
    else at the plugin root — and for a plugin that is itself gone.
    """
    path = PurePosixPath(str(rel).replace("\\", "/"))
    for plugin_dir in path.parents:
        if plugin_dir == PurePosixPath("."):
            return None
        if (root / plugin_dir / MANIFEST).is_file():
            below = path.relative_to(plugin_dir).parts
            if len(below) < 3 or below[0] != "skills":
                return None
            return plugin_dir, below[1]
    return None


def _version_in(text):
    """The raw `version` from plugin.json text, or None if it can't be read."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    return data.get("version") if isinstance(data, dict) else None


def _quote_hint(raw):
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return (f" — JSON read it as the number {raw!r}; quote it, "
                'e.g. "version": "1.1.0"')
    return ""


def check(changed, root=Path("."), read_base=None):
    """Return a list of error strings; empty means the rule is satisfied.

    `read_base(manifest_path)` returns the base branch's plugin.json text, or
    None when the plugin is new on this branch. Injected so tests need no git
    repo.
    """
    errors = []
    plugins: dict[PurePosixPath, set[str]] = {}
    for p in relevant(changed):
        owned = owning_plugin(p, root)
        if owned:
            plugin_dir, skill = owned
            plugins.setdefault(plugin_dir, set()).add(skill)

    for plugin_dir, skills in sorted(plugins.items()):
        manifest = (plugin_dir / MANIFEST).as_posix()
        text = (root / manifest).read_text(encoding="utf-8")
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            continue    # validate_skill.py reports an unparseable manifest
        name = (data.get("name") if isinstance(data, dict) else None) \
            or plugin_dir.name
        changed_skills = ", ".join(sorted(skills))

        cur_raw = _version_in(text)
        cur = parse_version(cur_raw)
        if cur is None:
            errors.append(f"[{name}] version {cur_raw!r} in {manifest} is not "
                          f"valid semver (expected MAJOR.MINOR.PATCH)"
                          f"{_quote_hint(cur_raw)}")
            continue

        base_text = read_base(manifest)
        if base_text is None:
            continue  # new plugin: nothing to bump from

        base = parse_version(_version_in(base_text))
        if base is None:
            # Base is malformed, so "greater than" is meaningless. Don't block
            # the PR that is probably fixing it; the format error above will
            # fire once this version is itself changed.
            continue

        if cur == base:
            errors.append(
                f"[{name}] skills changed ({changed_skills}) but {manifest} "
                f"version is still {'.'.join(map(str, base))} — bump it. If "
                "another PR already shipped that version, rebase and bump again.")
        elif cur < base:
            errors.append(
                f"[{name}] version in {manifest} went backwards: "
                f"{'.'.join(map(str, base))} -> {'.'.join(map(str, cur))}")

    return errors


def git(*args):
    return subprocess.run(["git", *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def main(argv):
    base = None
    if "--base" in argv:
        base = argv[argv.index("--base") + 1]
    base = base or os.environ.get("BASE_SHA") or "origin/main"

    diff = git("diff", "--name-only", f"{base}...HEAD")
    if diff.returncode != 0:
        print(f"Could not diff against '{base}': {diff.stderr.strip()}\n"
              "In CI this usually means the checkout lacks history — set "
              "fetch-depth: 0.", file=sys.stderr)
        return 2

    # Committed work, plus uncommitted changes to tracked files (staged new
    # files included, since the index then differs from HEAD). In CI the tree is
    # clean so the second call is a no-op -- but run locally before committing,
    # a commit-only diff would report "OK" for a skill the author just edited
    # and has not bumped, which is exactly when the reminder is useful.

    changed = set(diff.stdout.splitlines())
    changed |= set(git("diff", "--name-only", "HEAD").stdout.splitlines())
    changed = sorted(ln for ln in changed if ln.strip())

    def read_base(manifest):
        show = git("show", f"{base}:{manifest}")
        return show.stdout if show.returncode == 0 else None

    errors = check(changed, read_base=read_base)

    if errors:
        print("ERRORS:")
        for e in errors:
            print(f"  ✗  {e}")
        print(f"\n{len(errors)} error(s). See CONTRIBUTING.md for what MAJOR / "
              "MINOR / PATCH mean for a plugin.")
        return 1

    print("✓  Plugin versions OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
