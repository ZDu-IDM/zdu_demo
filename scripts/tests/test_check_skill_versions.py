#!/usr/bin/env python3
"""Offline behavior test for check_skill_versions.check (no git / network).

Run:  python scripts/tests/test_check_skill_versions.py
"""
import importlib.util
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent
sys.path.insert(0, str(SCRIPTS))  # so the module can `from split_plugin_changes import ...`
spec = importlib.util.spec_from_file_location(
    "check_skill_versions", SCRIPTS / "check_skill_versions.py")
cv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cv)

passed = failed = 0


def check(name, got, want):
    global passed, failed
    if got == want:
        passed += 1
        print(f"PASS - {name}")
    else:
        failed += 1
        print(f"FAIL - {name}\n        got:  {got!r}\n        want: {want!r}")


def skill_md(version):
    """A SKILL.md body with the given raw version line."""
    return f"---\nname: s\ndescription: d\nversion: {version}\n---\n\nbody\n"


def make_skill(root, rel, version):
    d = root / rel
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(skill_md(version), encoding="utf-8")
    return f"{rel}/SKILL.md"


SKILL = "plugins/groups/idm/skills/demo"

with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    md = make_skill(root, SKILL, '"1.2.0"')

    def base(version):
        """A read_base that reports the given base version for our one skill."""
        return lambda p: skill_md(version) if version is not None else None

    # --- the core rule ---
    check("bumped version passes",
          cv.check([md], root=root, read_base=base('"1.1.0"')), [])

    errs = cv.check([md], root=root, read_base=base('"1.2.0"'))
    check("unchanged version is rejected", len(errs), 1)
    check("unchanged version explains the concurrent-PR case",
          "rebase" in errs[0], True)

    errs = cv.check([md], root=root, read_base=base('"1.3.0"'))
    check("backwards version is rejected", len(errs), 1)
    check("backwards version says so", "backwards" in errs[0], True)

    # Ordering must be numeric, not lexical: 1.10.0 > 1.9.0.
    make_skill(root, SKILL, '"1.10.0"')
    check("1.10.0 counts as newer than 1.9.0",
          cv.check([md], root=root, read_base=base('"1.9.0"')), [])

    # --- new skills are exempt from the bump, but not from the format ---
    make_skill(root, SKILL, '"1.0.0"')
    check("new skill needs no bump",
          cv.check([md], root=root, read_base=base(None)), [])

    make_skill(root, SKILL, "1.10")          # unquoted -> YAML float 1.1
    errs = cv.check([md], root=root, read_base=base(None))
    check("new skill with unquoted 1.10 is rejected", len(errs), 1)
    check("the error tells the author to quote it", "quote it" in errs[0], True)

    # --- scope: what does and does not require a bump ---
    make_skill(root, SKILL, '"1.0.0"')
    check("evals-only change requires no bump",
          cv.check([f"{SKILL}/evals/evals.json"], root=root, read_base=base('"1.0.0"')), [])

    errs = cv.check([f"{SKILL}/references/style.md"], root=root,
                    read_base=base('"1.0.0"'))
    check("a reference file change does require a bump", len(errs), 1)

    check("files outside the plugin roots are ignored",
          cv.check(["scripts/deploy.py", "README.md"], root=root,
                   read_base=base('"1.0.0"')), [])

    check("a plugin manifest change requires no skill bump",
          cv.check(["plugins/groups/idm/.claude-plugin/plugin.json"], root=root,
                   read_base=base('"1.0.0"')), [])

    # --- malformed base must not block the PR that is fixing it ---
    make_skill(root, SKILL, '"2.0.0"')
    check("malformed base version is skipped, not blocked",
          cv.check([md], root=root, read_base=base("not-a-version")), [])

# --- parse_version unit checks (the YAML float trap) ---
check("parse '1.2.3'", cv.parse_version("1.2.3"), (1, 2, 3))
check("parse '1.10.0' numerically", cv.parse_version("1.10.0"), (1, 10, 0))
check("reject float 1.1 (unquoted 1.10)", cv.parse_version(1.1), None)
check("reject int 1", cv.parse_version(1), None)
check("reject two-part '1.0'", cv.parse_version("1.0"), None)
check("reject prerelease '1.0.0-rc1'", cv.parse_version("1.0.0-rc1"), None)

# --- version_of reads both frontmatter shapes ---
# version_of parses BOTH sides of the comparison: the working tree (nested under
# `metadata:`) and the base branch (top-level, on every commit predating the
# migration). If the top-level fallback is dropped, base versions all read as
# None, check() takes its "base is malformed, don't block" branch, and the rule
# stops firing while still reporting green -- this repo's signature failure.
NESTED = '---\nname: s\nmetadata:\n  version: "2.0.0"\n---\n\nbody\n'
LEGACY = '---\nname: s\nversion: "1.0.0"\n---\n\nbody\n'
BOTH = '---\nname: s\nversion: "1.0.0"\nmetadata:\n  version: "2.0.0"\n---\n\nbody\n'

check("reads metadata.version", cv.version_of(NESTED), "2.0.0")
check("falls back to a top-level version", cv.version_of(LEGACY), "1.0.0")
check("metadata wins when a file carries both", cv.version_of(BOTH), "2.0.0")
check("no version anywhere is None",
      cv.version_of('---\nname: s\n---\n\nbody\n'), None)

# The comparison this protects: a nested working tree against a legacy base.
check("a bump across the migration is still seen as a bump",
      cv.parse_version(cv.version_of(NESTED)) > cv.parse_version(cv.version_of(LEGACY)),
      True)

print(f"\n{passed}/{passed + failed} passed")
sys.exit(1 if failed else 0)
