#!/usr/bin/env python3
"""Offline behavior test for run_evals.pair_changed_skills (no git / network / API).

The thing under test is the selection step that decides which changed skills get
evals run. Its failure mode was silence: a skill inside an unregistered plugin
was dropped with nothing recorded, and the check still passed green. That was
only reachable through a real `git diff` against a live marketplace, which is
why no test caught it — so the logic is now pure and injectable.

Run:  python scripts/tests/test_run_evals_selection.py
"""
import importlib.util
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("run_evals", SCRIPTS / "run_evals.py")
re_ = importlib.util.module_from_spec(spec)
try:
    spec.loader.exec_module(re_)
except SystemExit:
    pass  # the module exits if no API key is configured; we only need its functions

passed = failed = 0


def check(name, got, want):
    global passed, failed
    if got == want:
        passed += 1
        print(f"PASS - {name}")
    else:
        failed += 1
        print(f"FAIL - {name}\n        got:  {got!r}\n        want: {want!r}")


def make_skill(root: Path, rel: str) -> Path:
    """Create <rel>/SKILL.md and return the SKILL.md path."""
    d = root / rel
    d.mkdir(parents=True, exist_ok=True)
    md = d / "SKILL.md"
    md.write_text("---\nname: s\n---\nbody\n", encoding="utf-8")
    return md


PUBLISHED = "plugins/groups/idm/software-tools"
UNPUBLISHED = "plugins/groups/idm/general"

with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    live = make_skill(root, f"{PUBLISHED}/skills/idm-pkg-install")
    dark = make_skill(root, f"{UNPUBLISHED}/skills/new-skill")
    # Only software-tools is registered — general is built but not published.
    plugins = {"idm-software-tools": root / PUBLISHED}

    # --- the core split ---
    pairs, skipped = re_.pair_changed_skills([live], plugins, root)
    check("a skill in a registered plugin becomes a pair",
          pairs, [("idm-software-tools", "idm-pkg-install")])
    check("and reports nothing skipped", skipped, [])

    pairs, skipped = re_.pair_changed_skills([dark], plugins, root)
    check("a skill in an UNREGISTERED plugin yields no pair", pairs, [])
    check("but is reported as skipped, not dropped",
          skipped, [f"{UNPUBLISHED}/skills/new-skill"])

    # The mixed case is the dangerous one: evals run, the check goes green, and
    # before this change the unpublished skill's absence was unreported.
    pairs, skipped = re_.pair_changed_skills([live, dark], plugins, root)
    check("mixed PR still runs the registered skill",
          pairs, [("idm-software-tools", "idm-pkg-install")])
    check("mixed PR also reports the unregistered one",
          skipped, [f"{UNPUBLISHED}/skills/new-skill"])

    # --- a file deeper inside a skill still attributes to that skill ---
    ref = root / PUBLISHED / "skills/idm-pkg-install/reference/notes.md"
    ref.parent.mkdir(parents=True, exist_ok=True)
    ref.write_text("x", encoding="utf-8")
    pairs, skipped = re_.pair_changed_skills([ref], plugins, root)
    check("a supporting file maps to its owning skill",
          pairs, [("idm-software-tools", "idm-pkg-install")])

    # --- files that belong to no skill are neither run nor reported ---
    other = root / "README.md"
    other.write_text("x", encoding="utf-8")
    check("a file outside any skill is not a pair and not a skip",
          re_.pair_changed_skills([other], plugins, root), ([], []))

    manifest = root / PUBLISHED / ".claude-plugin/plugin.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text("{}", encoding="utf-8")
    check("a plugin manifest change is not attributed to a skill",
          re_.pair_changed_skills([manifest], plugins, root), ([], []))

    # --- a path outside the repo root is ignored rather than crashing ---
    with tempfile.TemporaryDirectory() as other_td:
        stray = Path(other_td) / "SKILL.md"
        stray.write_text("x", encoding="utf-8")
        check("a path outside the root is ignored",
              re_.pair_changed_skills([stray], plugins, root), ([], []))

    # --- duplicates collapse; two files in one skill are one pair ---
    pairs, _ = re_.pair_changed_skills([live, live, ref], plugins, root)
    check("several files in one skill produce one pair", len(pairs), 1)

with tempfile.TemporaryDirectory() as td:
    # --- no registered plugins at all: everything is reported, nothing runs ---
    root = Path(td)
    a = make_skill(root, f"{UNPUBLISHED}/skills/a")
    b = make_skill(root, "plugins/shared-workflows/workflow-b/skills/b")
    pairs, skipped = re_.pair_changed_skills([a, b], {}, root)
    check("with an empty registry nothing is eval'd", pairs, [])
    check("and every changed skill is reported",
          skipped, [f"{UNPUBLISHED}/skills/a",
                    "plugins/shared-workflows/workflow-b/skills/b"])

# --- fixture resolution ---
# docs/SKILL_FOLDER_TEMPLATE.md documents fixtures at evals/fixtures/synthetic/
# INSIDE the skill folder. That location used to be unreachable: load_evals
# searched REPO_ROOT/evals/fixtures/, a directory that has never existed here,
# and ignored the skill_dir it was handed.

with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    skill = root / "skills" / "demo"
    (skill / "evals" / "fixtures" / "synthetic").mkdir(parents=True)
    (skill / "evals" / "fixtures" / "real").mkdir(parents=True)
    body = '[{"name": "t", "input": "x", "expected_behavior": "y"}]'
    (skill / "evals" / "fixtures" / "synthetic" / "edge.json").write_text(
        body, encoding="utf-8")
    (skill / "evals" / "fixtures" / "real" / "live.json").write_text(
        body, encoding="utf-8")

    got = re_.load_evals(skill, fixture="edge.json")
    check("a bare filename resolves in the skill's own synthetic/",
          len(got["evals"]), 1)

    got = re_.load_evals(skill, fixture="live.json")
    check("and in the skill's own real/", len(got["evals"]), 1)

    explicit = skill / "evals" / "fixtures" / "synthetic" / "edge.json"
    check("an explicit path still wins",
          len(re_.load_evals(skill, fixture=str(explicit))["evals"]), 1)

    # The old failure told the reader "evals/fixtures/synthetic/" without saying
    # relative to what — the ambiguity that hid the bug. It must name real paths.
    try:
        re_.load_evals(skill, fixture="absent.json")
        check("a missing fixture raises", False, True)
    except FileNotFoundError as e:
        check("a missing fixture raises FileNotFoundError", True, True)
        check("the error names the skill-local synthetic path it tried",
              str(skill / "evals" / "fixtures" / "synthetic" / "absent.json") in str(e),
              True)
        check("and the skill-local real path",
              str(skill / "evals" / "fixtures" / "real" / "absent.json") in str(e),
              True)

print(f"\n{passed}/{passed + failed} passed")
sys.exit(1 if failed else 0)
