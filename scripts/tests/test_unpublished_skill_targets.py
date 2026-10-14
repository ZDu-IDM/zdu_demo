#!/usr/bin/env python3
"""Offline behavior test for unpublished_skill_targets (no git / no network).

The module under test must import WITHOUT pulling in run_evals.py, because
run_evals imports `anthropic` at module scope and sys.exit(1)s when it is
missing. This test therefore doubles as a guard on that lazy-import boundary:
if someone hoists the run_evals import to module level, this file stops
running anywhere the SDK is not installed.

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
    """Mimic the workflow's `while IFS=$'\\t' read -r dir skill` loop.

    Reads BYTES, not text. An earlier version used read_text, whose universal-
    newline translation turned CRLF back into "\\n" on the way in -- so a file
    written with CRLF round-tripped clean here while bash saw a trailing CR
    glued to the last field. That hid a real silent-no-op bug (skill name
    "x\\r" matches no skill, run_evals runs nothing and exits 0). bash does no
    such translation, so neither may this.

    Same `read` semantics split_plugin_changes.py was bitten by on PR #35: an
    unterminated final line leaves the loop condition false, so that entry is
    silently never processed.
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
    # Over-running (both skills eval'd under one target) is acceptable;
    # emitting the pair twice would run the same pair twice for nothing.
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

    # --- REGRESSION GUARD, inherited from PR #35 (split_plugin_changes.py) ---
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

    # --- REGRESSION: CRLF made the step a silent no-op on Windows ---
    # write_text without newline="\n" emits CRLF on Windows. bash `read` then
    # puts the CR in the last field, `--skill "lease-triage\r"` matches no
    # skill basename, and run_evals exits 0 having run nothing. Asserted on
    # raw bytes because text-mode reads translate the CR away and hide it.
    ust.write_lines(out, [("p/a", "one"), ("p/b", "two")])
    check("output is LF-only on every platform (no CR smuggled into --skill)",
          b"\r" in out.read_bytes(), False)
    check("...so the skill field arrives clean",
          [skill for _dir, skill in read_loop(out)], ["one", "two"])

    # A tab separates the fields, so a target must split into exactly two.
    ust.write_lines(out, [("plugins/groups/acctops/lease-review", "lease-triage")])
    check("each line splits into exactly (plugin_dir, skill)",
          [len(t) for t in read_loop(out)], [2])

print(f"\n{passed}/{passed + failed} passed")
sys.exit(1 if failed else 0)
