#!/usr/bin/env python3
"""unpublished_skill_targets.py — list the changed skills that got no evals.

Used by .github/workflows/pr-evals.yml. `run_evals.py --changed-from` only
evals skills whose plugin is registered in .claude-plugin/marketplace.json;
everything else lands in its `skipped` list, is warned about, and the check
still exits 0. That is correct policy — registration is a separate act from
merging — but it means a brand-new plugin merges on a green check with none
of its evals ever run. This script turns that skipped list into the
`--path`/`--skill` arguments that reach exactly those skills.

It reuses `pair_changed_skills`'s own output rather than re-deriving "changed
plugin" from a git diff. A second definition would drift from the first, and
the set that must be covered here is by definition the set the other step
dropped — same reasoning as split_plugin_changes.py's single root list.

Targets are (plugin directory, skill name) pairs, not bare plugin directories:
`--path DIR --skill NAME` filters to one skill, so a PR touching one skill of
a ten-skill plugin does not pay for the other nine.

Usage:  python scripts/unpublished_skill_targets.py --changed-from origin/main \
            --output unpublished.txt
"""

import argparse
import sys
from pathlib import Path, PurePosixPath

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# A directory is a plugin iff it carries this manifest. Same test run_evals.py
# --path applies when it validates the directory you point it at, so "what
# counts as a plugin" has one answer on both sides of the handoff.
PLUGIN_MANIFEST = Path(".claude-plugin") / "plugin.json"


def owning_plugin_dir(skill_rel, root: Path) -> str | None:
    """Return the nearest ancestor of skill_rel holding a plugin manifest.

    Walks up rather than slicing at a fixed depth: category nesting varies
    (skills/<skill>/ and skills/<category>/<skill>/ both occur), and plugin
    roots sit at different depths ("plugins/foundation-wide/design" vs
    "plugins/groups/idm/reporting"). Returns None when no ancestor qualifies,
    which means the skill is not inside a plugin at all.

    `root` is the checkout the path is relative to; injectable so tests build a
    fixture tree instead of depending on the real catalog.
    """
    current = PurePosixPath(str(skill_rel).replace("\\", "/"))
    while str(current) not in (".", ""):
        if (root / current / PLUGIN_MANIFEST).exists():
            return current.as_posix()
        current = current.parent
    return None


def targets(skipped, root: Path = Path(".")):
    """Return (pairs, orphans) for a list of repo-relative skill directories.

    `pairs` is the sorted, deduped [(plugin_dir, skill_name), ...] to feed
    run_evals.py. `orphans` is every skipped skill with no plugin manifest
    above it — unreachable by --path, so it is returned rather than dropped:
    the caller's whole job is to stop skipped skills from being invisible, and
    swapping one silent drop for another would defeat that.

    Dedupe is on the pair. Two skills sharing a basename inside one plugin
    collapse to a single target because --skill matches on directory basename
    (run_evals.get_skills), so one invocation already runs both. Emitting the
    pair twice would just run both twice.
    """
    pairs, orphans = set(), set()
    for entry in skipped:
        rel = str(entry).strip()
        if not rel:
            continue
        plugin_dir = owning_plugin_dir(rel, root)
        if plugin_dir is None:
            orphans.add(PurePosixPath(rel.replace("\\", "/")).as_posix())
            continue
        pairs.add((plugin_dir, PurePosixPath(rel.replace("\\", "/")).name))
    return sorted(pairs), sorted(orphans)


def write_lines(path: Path, pairs) -> None:
    """Write one tab-separated `<plugin_dir>\\t<skill_name>` per line.

    The trailing newline is load-bearing: the workflow reads this with a bash
    `while IFS=$'\\t' read -r` loop, and `read` returns non-zero on an
    unterminated final line, silently dropping that entry. That cost PR #35 its
    only Infosec card via the same mistake in split_plugin_changes.py.

    newline="\\n" is equally load-bearing, and less obvious. Without it,
    write_text uses text mode's default translation and emits CRLF on Windows,
    so bash `read` hands the trailing CR to the last field and the skill name
    becomes "lease-triage\\r". get_skills matches basenames exactly, finds
    nothing, and run_evals exits 0 having run no evals -- the precise silent
    green check this script exists to prevent. CI runs on Linux where the
    default is already "\\n", so only a local run on Windows would show it.

    An empty list still writes a 0-byte file so the workflow's `[ -s ... ]`
    guard can tell "nothing to do" from "something to do".
    """
    path.write_text("".join(f"{d}\t{s}\n" for d, s in pairs),
                    encoding="utf-8", newline="\n")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--changed-from", required=True, metavar="REF",
                        help="Base ref to diff against (e.g. origin/main)")
    parser.add_argument("--output", required=True, metavar="FILE",
                        help="File to write targets to, one per line")
    args = parser.parse_args(argv)

    # Imported here, not at module scope: run_evals imports the anthropic SDK
    # eagerly and sys.exit(1)s without it, which would make this lister — and
    # its offline unit test — impossible to run on a machine that has no SDK.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from run_evals import REPO_ROOT, get_changed_skills

    _pairs, skipped = get_changed_skills(args.changed_from)
    found, orphans = targets(skipped, root=REPO_ROOT)

    write_lines(Path(args.output), found)

    # stdout is the data channel only in spirit here (the workflow reads the
    # file), but keep the human report on stderr regardless so redirecting
    # either one never mixes the two.
    for plugin_dir, skill in found:
        print(f"unpublished: {plugin_dir}  --skill {skill}", file=sys.stderr)
    for orphan in orphans:
        print(f"WARNING: {orphan} is not inside any plugin directory "
              f"(no {PLUGIN_MANIFEST.as_posix()} above it) — "
              "cannot eval it with --path", file=sys.stderr)
    print(f"{len(found)} unpublished skill target(s), {len(orphans)} orphan(s)",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
