#!/usr/bin/env python3
"""Offline behavior test for unpublished_skill_targets (no git / no network).

Needs the module to import without run_evals.py (which needs the anthropic
SDK), so keep that import lazy.

Run:  python scripts/tests/test_unpublished_skill_targets.py
"""
import importlib.util
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location(
    "unpublished_skill_targets", HERE.parent / "unpublished_skill_targets.py")
ust = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ust)

passed = failed = 0


def check(name, got, want):
    global passed, failed
    if got == want:
        passed += 1
        print(f"PASS - {name}")
    else:
        failed += 1
        print(f"FAIL - {name}\n        got:  {got!r}\n        want: {want!r}")


def make_plugin(root: Path, plugin_dir: str, skill_dirs):
    """Create a plugin manifest plus a SKILL.md for each of skill_dirs."""
    manifest = root / plugin_dir / ".claude-plugin"
    manifest.mkdir(parents=True, exist_ok=True)
    (manifest / "plugin.json").write_text('{"name": "x"}\n', encoding="utf-8")
    for d in skill_dirs:
        p = root / d
        p.mkdir(parents=True, exist_ok=True)
        (p / "SKILL.md").write_text("---\nname: x\n---\n", encoding="utf-8")


def read_loop(path: Path):
    """Mimic the workflow's tab-separated bash `while read` loop.

    Reads bytes, so a stray CR is seen as bash sees it, and drops an
    unterminated last line as bash does.
    """
    raw = path.read_bytes().decode("utf-8")
    if not raw:
        return []
    lines = raw.split("\n")
    lines.pop()   # terminated -> drops the empty tail; unterminated -> bash drops it
    return [tuple(ln.split("\t")) for ln in lines if ln]


with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    make_plugin(root, "plugins/shared-workflows/budget-review", [
        "plugins/shared-workflows/budget-review/skills/budget-review",
        "plugins/shared-workflows/budget-review/skills/cost-policy-check",
    ])
    make_plugin(root, "plugins/groups/idm/reporting", [
        # Category nesting: skills/<category>/<skill>, not a fixed depth.
        "plugins/groups/idm/reporting/skills/analysis/weekly-report",
    ])

    # --- the core case: a new plugin nobody has registered yet ---
    pairs, orphans = ust.targets(
        ["plugins/shared-workflows/budget-review/skills/budget-review"], root=root)
    check("skill maps to its owning plugin dir and skill name",
          pairs, [("plugins/shared-workflows/budget-review", "budget-review")])
    check("no orphans for a well-formed plugin", orphans, [])

    # --- several skills in one plugin stay separate --skill targets ---
    pairs, _ = ust.targets([
        "plugins/shared-workflows/budget-review/skills/budget-review",
        "plugins/shared-workflows/budget-review/skills/cost-policy-check",
    ], root=root)
    check("two skills in one plugin yield two targets, one plugin dir",
          pairs, [("plugins/shared-workflows/budget-review", "budget-review"),
                  ("plugins/shared-workflows/budget-review", "cost-policy-check")])

    # --- category nesting must not be assumed away ---
    pairs, _ = ust.targets(
        ["plugins/groups/idm/reporting/skills/analysis/weekly-report"], root=root)
    check("nested category skill still finds the plugin root",
          pairs, [("plugins/groups/idm/reporting", "weekly-report")])

    # --- a skill with no plugin.json ancestor is not a --path target ---
    (root / "plugins/foundation-wide/skills/stray").mkdir(parents=True)
    (root / "plugins/foundation-wide/skills/stray/SKILL.md").write_text(
        "---\nname: x\n---\n", encoding="utf-8")
    pairs, orphans = ust.targets(
        ["plugins/foundation-wide/skills/stray"], root=root)
    check("skill outside any plugin emits no target",
          pairs, [])
    check("...and is reported as an orphan instead of vanishing",
          orphans, ["plugins/foundation-wide/skills/stray"])

    # --- duplicate basenames collapse: --skill matches on basename ---
    make_plugin(root, "plugins/groups/idm/documents", [
        "plugins/groups/idm/documents/skills/a/summary",
        "plugins/groups/idm/documents/skills/b/summary",
    ])
    pairs, _ = ust.targets([
        "plugins/groups/idm/documents/skills/a/summary",
        "plugins/groups/idm/documents/skills/b/summary",
    ], root=root)
    check("same basename in one plugin dedupes to a single target",
          pairs, [("plugins/groups/idm/documents", "summary")])

    # --- nothing skipped: the common case, and the loop must stay quiet ---
    check("empty input yields no targets", ust.targets([], root=root), ([], []))

    # --- every line ends in a newline, or bash drops the last (PR #35) ---
    out = root / "unpublished.txt"

    ust.write_lines(out, [("plugins/shared-workflows/budget-review", "budget-review")])
    check("a single target survives the bash read loop",
          read_loop(out),
          [("plugins/shared-workflows/budget-review", "budget-review")])

    ust.write_lines(out, [("p/a", "one"), ("p/b", "two"), ("p/c", "three")])
    check("the last of several targets is not dropped",
          read_loop(out), [("p/a", "one"), ("p/b", "two"), ("p/c", "three")])

    ust.write_lines(out, [])
    check("no targets writes a 0-byte file, keeping `[ -s ]` honest",
          out.stat().st_size, 0)

    # --- LF only: a CR would end up in --skill and match nothing ---
    ust.write_lines(out, [("p/a", "one"), ("p/b", "two")])
    check("output is LF-only on every platform (no CR smuggled into --skill)",
          b"\r" in out.read_bytes(), False)
    check("...so the skill field arrives clean",
          [skill for _dir, skill in read_loop(out)], ["one", "two"])

    ust.write_lines(out, [("plugins/groups/acctops/lease-review", "lease-triage")])
    check("each line splits into exactly (plugin_dir, skill)",
          [len(t) for t in read_loop(out)], [2])

print(f"\n{passed}/{passed + failed} passed")
sys.exit(1 if failed else 0)
