#!/usr/bin/env python3
"""check_skill_versions.py — a changed skill must ship a higher version.

Enforces the mechanics of the versioning policy in CONTRIBUTING.md: if a PR
changes any file under a skill directory, that skill's `version` must be valid
semver and strictly greater than the version on the base branch.

What it does NOT check: whether the bump is the *right* size. CI cannot tell a
PATCH from a MAJOR — that is what review is for. The value here is that the
author has to make a conscious claim about the size of the change, which gives
the reviewer something concrete to disagree with.

Scope decisions, kept consistent with .github/workflows/infosec-notify.yml:
  - only files under a plugin root (utils.PLUGIN_ROOTS: plugins/foundation-wide/,
    plugins/groups/, plugins/shared-workflows/) count;
  - evals/ is excluded, because the harness is not the payload (a rubric can
    change without the skill's content or behavior changing at all);
  - any other file in a skill directory counts, not just SKILL.md — references/
    and assets are content the agent loads at runtime.

New skills are exempt from the bump (there is nothing to bump from) but their
version must still parse, so `version: 1.10` cannot sneak in on day one.

Usage:  python scripts/check_skill_versions.py [--base <ref>]
        BASE_SHA=<sha> python scripts/check_skill_versions.py
"""

import os
import re
import subprocess
import sys
from pathlib import Path

import yaml

from split_plugin_changes import split_changed
# PLUGIN_ROOTS is unused here directly but re-exported: test_plugin_roots.py
# checks this module's attribute against utils.PLUGIN_ROOTS by identity.
from utils import (HARNESS_DIRS, PLUGIN_ROOTS, classify_path,  # noqa: F401
                   skill_metadata)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


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


def parse_version(raw):
    """Return (major, minor, patch), or None if `raw` is not valid semver.

    A non-string is always None: unquoted `version: 1.10` is parsed by YAML as
    the float 1.1, silently losing the trailing 0, so it must be rejected with
    a message telling the author to quote it.
    """
    if raw is None or isinstance(raw, (int, float, bool)):
        return None
    m = SEMVER.match(str(raw).strip())
    return tuple(int(g) for g in m.groups()) if m else None


def version_of(text):
    """Pull the raw `version` value out of a SKILL.md's frontmatter.

    Reads `metadata.version`, falling back to a top-level `version`. The fallback
    is load-bearing, not politeness: this function parses BOTH sides of the
    comparison, and every commit before the metadata migration has the top-level
    form. Without it every base version reads as None, `check()` takes its
    "base is malformed, don't block" branch, and the version rule silently stops
    firing -- a required check that never fires being the failure mode this repo
    has already shipped once.
    """
    parts = text.split("---", 2)
    if len(parts) < 3:
        return None
    try:
        fm = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        return None
    meta = skill_metadata(fm)
    return meta.get("version", fm.get("version"))


def _quote_hint(raw):
    if isinstance(raw, (int, float)):
        return (f" — YAML parsed it as the number {raw!r}; quote it, "
                'e.g. version: "1.1.0"')
    return ""


def check(changed, root=Path("."), read_base=None):
    """Return a list of error strings; empty means the rule is satisfied.

    `read_base(skill_md_path)` returns the base branch's SKILL.md text, or None
    when the file is new on this branch. Injected so tests need no git repo.
    """
    errors = []
    skills, _ = split_changed(relevant(changed), root=root)

    for skill_md in skills:
        name = Path(skill_md).parent.name
        cur_raw = version_of((root / skill_md).read_text(encoding="utf-8"))
        cur = parse_version(cur_raw)
        if cur is None:
            errors.append(f"[{name}] version {cur_raw!r} is not valid semver "
                          f"(expected MAJOR.MINOR.PATCH){_quote_hint(cur_raw)}")
            continue

        base_text = read_base(skill_md)
        if base_text is None:
            continue  # new skill: nothing to bump from

        base_raw = version_of(base_text)
        base = parse_version(base_raw)
        if base is None:
            # Base is malformed, so "greater than" is meaningless. Don't block
            # the PR that is probably fixing it; the format error above will
            # fire once this version is itself changed.
            continue

        if cur == base:
            errors.append(
                f"[{name}] changed but version is still "
                f"{'.'.join(map(str, base))} — bump it. If another PR already "
                "shipped that version, rebase and bump again.")
        elif cur < base:
            errors.append(
                f"[{name}] version went backwards: "
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

    def read_base(skill_md):
        show = git("show", f"{base}:{skill_md}")
        return show.stdout if show.returncode == 0 else None

    errors = check(changed, read_base=read_base)

    if errors:
        print("ERRORS:")
        for e in errors:
            print(f"  ✗  {e}")
        print(f"\n{len(errors)} error(s). See CONTRIBUTING.md for what MAJOR / "
              "MINOR / PATCH mean for a skill.")
        return 1

    print("✓  Skill versions OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
