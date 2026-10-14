#!/usr/bin/env python3
"""
refresh_local_skills.py — copy catalog skills into .claude/skills/ so Claude
Code loads them in this repo.

Claude Code reads skills from `.claude/skills/`, not from `plugins/`. Testing a
skill you are writing therefore means keeping a copy in both places, and the
copy is what actually runs. A stale copy is the failure this script exists to
prevent: it reports no error, the skill loads, and you test the version you had
an hour ago. That has already happened here once -- a copy predating the
`metadata:` migration was still carrying top-level `owner`/`version`/`surfaces`,
so a test run would have exercised frontmatter the validator now rejects.

Skills are found the way validate_skill.py finds them (every SKILL.md under the
plugin roots), so a newly added skill cannot be missed.

Orphans -- directories in .claude/skills/ with no matching source -- are
reported but never deleted without --prune. Hand-written scratch skills live
there too, and silently removing someone's work to tidy a cache is not a trade
this script gets to make.

Usage:
  python scripts/refresh_local_skills.py           # copy, report what changed
  python scripts/refresh_local_skills.py --check   # report only; exit 1 if stale
  python scripts/refresh_local_skills.py --prune   # also remove orphaned copies
"""
import argparse
import pathlib
import shutil
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from utils import HARNESS_DIRS, PLUGIN_ROOTS  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
DEST_ROOT = REPO_ROOT / ".claude" / "skills"


def source_skills(root: pathlib.Path) -> dict[str, pathlib.Path]:
    """Map skill directory name -> its source path, erroring on collisions.

    `.claude/skills/` is a flat namespace, so two skills sharing a directory
    name in different plugins would land on top of each other and one would
    silently stop existing locally. The catalog allows this today -- plugin
    names are unique, skill directory names are not checked across plugins --
    so the collision is caught here rather than discovered as a skill that
    "stopped working" on someone's machine.
    """
    found: dict[str, pathlib.Path] = {}
    collisions: list[str] = []
    for plugin_root in PLUGIN_ROOTS:
        for skill_md in sorted((root / plugin_root).rglob("SKILL.md")):
            skill_dir = skill_md.parent
            name = skill_dir.name
            if name in found:
                collisions.append(
                    f"  {name}\n"
                    f"      {found[name].relative_to(root).as_posix()}\n"
                    f"      {skill_dir.relative_to(root).as_posix()}")
                continue
            found[name] = skill_dir
    if collisions:
        print("Two skills share a directory name, so one would overwrite the "
              "other in .claude/skills/:", file=sys.stderr)
        print("\n".join(collisions), file=sys.stderr)
        sys.exit(2)
    return found


# Skipped by both the copy and the comparison. See utils.HARNESS_DIRS for why.
#
# snapshot() and the copy MUST agree on this. If only one of them skipped the
# harness, a copy would compare as permanently stale and refreshing would never
# settle it.
EXCLUDED = HARNESS_DIRS


def snapshot(d: pathlib.Path) -> dict[str, bytes]:
    """Every file under `d` except EXCLUDED subtrees, keyed by relative path."""
    if not d.is_dir():
        return {}
    return {rel.as_posix(): p.read_bytes()
            for p in sorted(d.rglob("*")) if p.is_file()
            for rel in [p.relative_to(d)]
            if rel.parts[0] not in EXCLUDED}


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true",
                    help="report without copying; exit 1 if any copy is stale")
    ap.add_argument("--prune", action="store_true",
                    help="remove copies whose source no longer exists")
    args = ap.parse_args()

    sources = source_skills(REPO_ROOT)
    stale, added, current = [], [], []

    for name, src in sources.items():
        dest = DEST_ROOT / name
        if not dest.exists():
            added.append(name)
        elif snapshot(src) != snapshot(dest) or any(
                (dest / x).exists() for x in EXCLUDED):
            # The second test makes the exclusion self-healing. snapshot() skips
            # EXCLUDED, so a copy made before a directory joined that list would
            # compare as current and keep the leftover forever.
            stale.append(name)
        else:
            current.append(name)
        if not args.check and (name in added or name in stale):
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(src, dest, ignore=shutil.ignore_patterns(*EXCLUDED))

    orphans = sorted(d.name for d in DEST_ROOT.iterdir()
                     if d.is_dir() and d.name not in sources) if DEST_ROOT.is_dir() else []

    verb = "would update" if args.check else "updated"
    for name in sorted(added):
        print(f"  + {'would add' if args.check else 'added':<12} {name}")
    for name in sorted(stale):
        print(f"  ~ {verb:<12} {name}  (local copy was out of date)")
    for name in sorted(current):
        print(f"  = {'current':<12} {name}")

    if orphans:
        print()
        if args.prune and not args.check:
            for name in orphans:
                shutil.rmtree(DEST_ROOT / name)
                print(f"  - pruned       {name}")
        else:
            print("  Orphaned copies with no source under plugins/ "
                  "(--prune removes them):")
            for name in orphans:
                print(f"      {name}")
            print("  A hand-written scratch skill looks identical to a deleted "
                  "one from here,\n  so these are left alone by default.")

    if args.check and (added or stale):
        # stdout is buffered and stderr is not, so without this the summary
        # prints above the per-skill lines it summarises.
        sys.stdout.flush()
        print(f"\n{len(added) + len(stale)} local copy/copies are out of date — "
              "run: python scripts/refresh_local_skills.py", file=sys.stderr)
        return 1

    print(f"\n{len(sources)} skill(s) in the catalog; .claude/skills/ is current.")
    print("Claude Code picks newly copied skills up mid-session — no restart "
          "needed.\nIf an edit does not seem to take effect, start a new "
          "session before assuming\nthe skill itself is at fault.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
