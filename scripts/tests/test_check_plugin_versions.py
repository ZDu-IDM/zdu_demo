#!/usr/bin/env python3
"""Offline behavior test for check_plugin_versions.check (no git / network).

Run:  python scripts/tests/test_check_plugin_versions.py
"""
import importlib.util
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent
sys.path.insert(0, str(SCRIPTS))  # so the module can `from utils import ...`
spec = importlib.util.spec_from_file_location(
    "check_plugin_versions", SCRIPTS / "check_plugin_versions.py")
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


def manifest(version):
    """plugin.json text with the given version (any JSON value, or absent)."""
    data = {"name": "demo-plugin"}
    if version is not None:
        data["version"] = version
    return json.dumps(data)


PLUGIN = "plugins/groups/idm/demo"
MANIFEST = f"{PLUGIN}/.claude-plugin/plugin.json"
SKILL = f"{PLUGIN}/skills/demo-skill"
MD = f"{SKILL}/SKILL.md"


def make_plugin(root, version):
    (root / PLUGIN / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (root / MANIFEST).write_text(manifest(version), encoding="utf-8")
    (root / SKILL).mkdir(parents=True, exist_ok=True)
    (root / MD).write_text("---\nname: demo-skill\n---\n\nbody\n", encoding="utf-8")


def base(version, new=False):
    """A read_base reporting the given base plugin.json, or None for a new plugin."""
    return lambda p: None if new else manifest(version)


with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    make_plugin(root, "1.2.0")

    # --- the core rule ---
    check("bumped plugin version passes",
          cv.check([MD], root=root, read_base=base("1.1.0")), [])

    errs = cv.check([MD], root=root, read_base=base("1.2.0"))
    check("unchanged plugin version is rejected", len(errs), 1)
    check("unchanged version explains the concurrent-PR case",
          "rebase" in errs[0], True)
    check("the error names the plugin.json to edit", MANIFEST in errs[0], True)
    check("and the skill that changed", "demo-skill" in errs[0], True)

    errs = cv.check([MD], root=root, read_base=base("1.3.0"))
    check("backwards version is rejected", len(errs), 1)
    check("backwards version says so", "backwards" in errs[0], True)

    # Ordering must be numeric, not lexical: 1.10.0 > 1.9.0.
    make_plugin(root, "1.10.0")
    check("1.10.0 counts as newer than 1.9.0",
          cv.check([MD], root=root, read_base=base("1.9.0")), [])

    # Two skills in one plugin need one bump, not two.
    make_plugin(root, "1.0.0")
    other = f"{PLUGIN}/skills/other-skill/SKILL.md"
    (root / other).parent.mkdir(parents=True, exist_ok=True)
    (root / other).write_text("---\nname: other-skill\n---\n", encoding="utf-8")
    errs = cv.check([MD, other], root=root, read_base=base("1.0.0"))
    check("two changed skills in one plugin are one error", len(errs), 1)
    check("which names both skills",
          "demo-skill" in errs[0] and "other-skill" in errs[0], True)

    # --- new plugins are exempt from the bump, but not from the format ---
    make_plugin(root, "1.0.0")
    check("new plugin needs no bump",
          cv.check([MD], root=root, read_base=base(None, new=True)), [])

    make_plugin(root, 1.1)                 # a JSON number, not a string
    errs = cv.check([MD], root=root, read_base=base(None, new=True))
    check("a numeric version is rejected", len(errs), 1)
    check("the error tells the author to quote it", "quote it" in errs[0], True)

    make_plugin(root, None)
    errs = cv.check([MD], root=root, read_base=base("1.0.0"))
    check("a missing version is rejected, not skipped", len(errs), 1)

    # --- scope: what does and does not require a bump ---
    make_plugin(root, "1.0.0")
    check("evals-only change requires no bump",
          cv.check([f"{SKILL}/evals/evals.json"], root=root, read_base=base("1.0.0")), [])

    errs = cv.check([f"{SKILL}/references/style.md"], root=root,
                    read_base=base("1.0.0"))
    check("a reference file change does require a bump", len(errs), 1)

    # A deleted skill's files are gone, but its plugin is not: still a bump.
    errs = cv.check([f"{PLUGIN}/skills/deleted-skill/SKILL.md"], root=root,
                    read_base=base("1.0.0"))
    check("deleting a skill requires a bump", len(errs), 1)

    check("a plugin.json-only change requires no bump",
          cv.check([MANIFEST], root=root, read_base=base("1.0.0")), [])

    check("files outside the plugin roots are ignored",
          cv.check(["scripts/deploy.py", "README.md"], root=root,
                   read_base=base("1.0.0")), [])

    check("files in a deleted plugin are ignored",
          cv.check(["plugins/groups/idm/gone/skills/x/SKILL.md"], root=root,
                   read_base=base("1.0.0")), [])

    # --- malformed base must not block the PR that is fixing it ---
    make_plugin(root, "2.0.0")
    check("malformed base version is skipped, not blocked",
          cv.check([MD], root=root, read_base=base("not-a-version")), [])
    check("unparseable base JSON is skipped, not blocked",
          cv.check([MD], root=root, read_base=lambda p: "{oops"), [])

# --- parse_version unit checks (the JSON/YAML number trap) ---
check("parse '1.2.3'", cv.parse_version("1.2.3"), (1, 2, 3))
check("parse '1.10.0' numerically", cv.parse_version("1.10.0"), (1, 10, 0))
check("reject float 1.1 (unquoted 1.10)", cv.parse_version(1.1), None)
check("reject int 1", cv.parse_version(1), None)
check("reject two-part '1.0'", cv.parse_version("1.0"), None)
check("reject prerelease '1.0.0-rc1'", cv.parse_version("1.0.0-rc1"), None)

print(f"\n{passed}/{passed + failed} passed")
sys.exit(1 if failed else 0)
