#!/usr/bin/env python3
"""Offline behavior test for split_plugin_changes (no network / gh).

Run:  python scripts/tests/test_split_plugin_changes.py
"""
import importlib.util
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location(
    "split_plugin_changes", HERE.parent / "split_plugin_changes.py")
sp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sp)

FOUNDATION, GROUPS, SHARED = sp.PLUGIN_ROOTS

passed = failed = 0


def check(name, got, want):
    global passed, failed
    if got == want:
        passed += 1
        print(f"PASS - {name}")
    else:
        failed += 1
        print(f"FAIL - {name}\n        got:  {got!r}\n        want: {want!r}")


def make_tree(root: Path, skill_dirs):
    """Create a fixture checkout with a SKILL.md in each of skill_dirs."""
    for d in skill_dirs:
        p = root / d
        p.mkdir(parents=True, exist_ok=True)
        (p / "SKILL.md").write_text("---\nname: x\n---\n", encoding="utf-8")


def read_loop(path: Path):
    """Mimic the workflow's bash `while read` loop, which drops an unterminated last line."""
    raw = path.read_text(encoding="utf-8")
    if not raw:
        return []
    lines = raw.split("\n")
    if lines[-1] == "":
        lines.pop()          # properly terminated: every line is consumed
    else:
        lines.pop()          # unterminated final line: bash drops it
    return [ln for ln in lines if ln]


with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    make_tree(root, [
        "groups/example-group/skills/example-skill-1",
        "groups/idm/skills/software-tools/python-code-reviewer",
    ])

    # --- attribution ---
    skills, others = sp.split_changed([
        "groups/example-group/skills/example-skill-1/SKILL.md",
        "groups/example-group/.claude-plugin/plugin.json",
        "groups/example-group/skills/example-skill-2/use_case1/README.md",
    ], root=root)
    check("SKILL.md is attributed to its own skill",
          skills, ["groups/example-group/skills/example-skill-1/SKILL.md"])
    check("manifest and orphan dir fall through to components",
          others, ["groups/example-group/.claude-plugin/plugin.json",
                   "groups/example-group/skills/example-skill-2/use_case1/README.md"])

    # A file nested under a skill belongs to that skill, whatever the depth.
    skills, others = sp.split_changed([
        "groups/idm/skills/software-tools/python-code-reviewer/references/style.md",
    ], root=root)
    check("nested file attributed to ancestor skill, regardless of depth",
          skills, ["groups/idm/skills/software-tools/python-code-reviewer/SKILL.md"])
    check("nested file leaves components empty", others, [])

    # --- every line ends in a newline, or bash drops the last (regression, PR #35) ---
    out = root / "skills.txt"

    sp.write_lines(out, ["groups/example-group/skills/example-skill-1/SKILL.md"])
    check("single skill survives the bash read loop (PR #35 regression)",
          read_loop(out), ["groups/example-group/skills/example-skill-1/SKILL.md"])

    sp.write_lines(out, ["a/SKILL.md", "b/SKILL.md", "c/SKILL.md"])
    check("last of several skills is not dropped",
          read_loop(out), ["a/SKILL.md", "b/SKILL.md", "c/SKILL.md"])

    out.write_text("\n".join(["a/SKILL.md", "b/SKILL.md"]), encoding="utf-8")
    check("the old writer really did drop the last entry",
          read_loop(out), ["a/SKILL.md"])

    # --- empty input keeps the workflow's `[ -s components.txt ]` guard honest ---
    empty = root / "components.txt"
    sp.write_lines(empty, [])
    check("empty list writes a 0-byte file", empty.stat().st_size, 0)

# --- root and evals filtering ---
check("keeps a file under a plugin root",
      list(sp.relevant([f"{GROUPS}/idm/software-tools/skills/x/SKILL.md"])),
      [f"{GROUPS}/idm/software-tools/skills/x/SKILL.md"])

check("keeps foundation-wide",
      list(sp.relevant([f"{FOUNDATION}/skills/research/lit/SKILL.md"])),
      [f"{FOUNDATION}/skills/research/lit/SKILL.md"])

# A root missing here means its PRs merge with no Infosec card.
check("keeps shared-workflows",
      list(sp.relevant([f"{SHARED}/grant-review/skills/x/SKILL.md"])),
      [f"{SHARED}/grant-review/skills/x/SKILL.md"])

check("drops paths outside every plugin root",
      list(sp.relevant([".github/workflows/validate.yml", "scripts/deploy.py",
                        "README.md", ".claude-plugin/marketplace.json"])),
      [])

check("drops anything under evals/",
      list(sp.relevant([f"{GROUPS}/idm/software-tools/skills/x/evals/evals.json"])),
      [])

check("evals as a mid-path component is still dropped",
      list(sp.relevant([f"{FOUNDATION}/skills/a/evals/README.md"])), [])

check("a file merely named evals.json outside evals/ is kept",
      list(sp.relevant([f"{GROUPS}/idm/software-tools/skills/x/evals.json"])),
      [f"{GROUPS}/idm/software-tools/skills/x/evals.json"])

check("blank and whitespace lines are dropped",
      list(sp.relevant(["", "   ", f"{GROUPS}/a/b/skills/c/SKILL.md"])),
      [f"{GROUPS}/a/b/skills/c/SKILL.md"])

print(f"\n{passed}/{passed + failed} passed")
sys.exit(1 if failed else 0)
