#!/usr/bin/env python3
"""split_plugin_changes.py — split a PR's changed plugin files into skills vs components.

For infosec-notify.yml: a file under a directory holding SKILL.md belongs to
that skill (one card each); everything else is a plugin component (one card).

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
    """Keep changed paths under a plugin root, dropping evals/ (test harness, not skill content)."""
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

    Walks up to the nearest SKILL.md, so any nesting depth works. `root` is for tests.
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

    The workflow's bash `while read` drops an unterminated last line (PR #35).
    An empty list writes a 0-byte file, for the `[ -s ]` guard.
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
