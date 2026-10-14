#!/usr/bin/env python3
"""split_plugin_changes.py — split a PR's changed plugin files into skills vs components.

Used by .github/workflows/infosec-notify.yml to decide which Teams card each
changed file belongs to: a file that some parent directory owns via SKILL.md is
attributed to that skill (one card per skill, with its scan result); everything
else is a non-skill plugin component (one summary card for all of them).

Walking up the tree beats a path regex — it does not assume skills live at a
fixed depth, and category nesting varies (skills/<category>/<skill>/).

Lives here rather than inline in the workflow so the logic is unit-testable:
see scripts/tests/test_split_plugin_changes.py.

Usage:  python scripts/split_plugin_changes.py changed.txt skills.txt components.txt
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
# PLUGIN_ROOTS is unused here directly but re-exported: test_plugin_roots.py
# checks this module's attribute against utils.PLUGIN_ROOTS by identity.
from utils import HARNESS_DIRS, PLUGIN_ROOTS, classify_path  # noqa: E402,F401

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def relevant(paths):
    """Keep changed paths that belong to a plugin, dropping evals/.

    Previously two `grep` calls in infosec-notify.yml. Moved here so the root
    list has exactly one definition and the filter is testable: the shell copy
    was the one place no test could see, and a root missing from it would have
    merged that root's plugins with no Infosec card, silently.

    evals/ is excluded because the harness is not the payload -- a rubric can
    change without the skill's content, tools, or behavior changing at all.
    """
    for p in paths:
        rel = p.strip()
        if not rel:
            continue
        classified = classify_path(rel)
        if classified is None:
            continue
        _root, below = classified
        if any(d in below for d in HARNESS_DIRS):
            continue
        yield rel


def split_changed(lines, root=Path(".")):
    """Split changed paths into (sorted skill SKILL.md paths, other paths).

    `root` is the checkout the paths are relative to; injectable so tests can
    build a fixture tree instead of depending on the real repo layout.
    """
    skills, others = set(), []
    for line in lines:
        rel = line.strip()
        if not rel:
            continue
        cur, owner = Path(rel).parent, None
        while cur != Path("."):
            if (root / cur / "SKILL.md").exists():
                owner = cur / "SKILL.md"
                break
            cur = cur.parent
        if owner:
            skills.add(owner.as_posix())
        else:
            others.append(rel)
    return sorted(skills), others


def write_lines(path: Path, items):
    """Write one item per line, each newline-terminated.

    The trailing newline matters: the workflow consumes skills.txt with a bash
    `while IFS= read -r` loop, and `read` returns non-zero on an unterminated
    final line — so a file written without it silently drops its last entry.
    That ate the only skill card on PR #35. An empty list still yields a 0-byte
    file, so the workflow's `[ -s components.txt ]` guard behaves as before.
    """
    path.write_text("".join(f"{item}\n" for item in items), encoding="utf-8")


def main(argv):
    if len(argv) != 4:
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        return 2
    changed, skills_out, components_out = (Path(a) for a in argv[1:])

    lines = relevant(changed.read_text(encoding="utf-8").splitlines())
    skills, others = split_changed(lines)

    write_lines(skills_out, skills)
    write_lines(components_out, others)
    print(f"{len(skills)} skill(s), {len(others)} non-skill plugin file(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
