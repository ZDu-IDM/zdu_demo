#!/usr/bin/env python3
"""
preview_infosec_cards.py — render the Infosec cards for a PR without posting.

Builds the same cards infosec-notify.yml posts on merge and writes them to disk,
so you can check them first. Only the merge trigger itself can't be tested here.

Usage:
  python scripts/preview_infosec_cards.py 19
  python scripts/preview_infosec_cards.py 19 --out-dir preview --card-only
  python scripts/preview_infosec_cards.py 19 --post --webhook https://...

--post sends one message per card. Point it at a TEST channel.

Needs an authenticated `gh`. Check out the PR first (`gh pr checkout <n>`):
skills are found by SKILL.md on disk.
"""
import argparse
import json
import pathlib
import subprocess
import sys

# Card text has em dashes and emoji, which Windows' default codepage mangles.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from utils import PLUGIN_ROOTS as _ROOTS  # noqa: E402

# This module compares against path prefixes, so it needs the trailing slash.
PLUGIN_ROOTS = tuple(f"{r}/" for r in _ROOTS)


def gh_json(*args):
    out = subprocess.run(["gh", "api", *args], capture_output=True, text=True,
                         encoding="utf-8")
    if out.returncode != 0:
        sys.exit(f"gh api failed: {out.stderr.strip()}")
    return out.stdout


def is_evals(rel: str) -> bool:
    """evals/ is test harness, not skill content, so it gets no card.

    Must match split_plugin_changes.relevant(), which the workflow uses.
    """
    return rel.startswith("evals/") or "/evals/" in rel


def changed_plugin_files(repo: str, pr: int) -> list[str]:
    raw = gh_json(f"repos/{repo}/pulls/{pr}/files", "--paginate", "--jq", ".[].filename")
    return [ln.strip() for ln in raw.splitlines()
            if ln.strip().startswith(PLUGIN_ROOTS) and not is_evals(ln.strip())]


def split(paths: list[str]) -> tuple[list[str], list[str]]:
    """Attribute each path to its nearest SKILL.md, or call it a component."""
    skills, others = set(), []
    for rel in paths:
        cur, owner = pathlib.Path(rel).parent, None
        while cur != pathlib.Path("."):
            if (cur / "SKILL.md").exists():
                owner = cur / "SKILL.md"
                break
            cur = cur.parent
        if owner:
            skills.add(owner.as_posix())
        else:
            others.append(rel)
    return sorted(skills), others


SCRIPTS = pathlib.Path(__file__).resolve().parent


def render(args_list: list[str], out_path: pathlib.Path, post: bool = False,
           webhook: str = "") -> None:
    """Write the card to out_path, and POST it too when `post` is set.

    teams_card.py is called by absolute path, so this works from any directory.
    """
    base = [sys.executable, str(SCRIPTS / "teams_card.py"), *args_list]
    r = subprocess.run(base + ["--out", str(out_path)],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    if r.returncode != 0:
        sys.exit(f"teams_card.py failed: {r.stderr.strip()}")
    if post:
        # A separate run, so the written file's shape doesn't depend on posting.
        r = subprocess.run(base + ["--post"] + (["--webhook", webhook] if webhook else []),
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace")
        if r.returncode != 0:
            sys.exit(f"teams_card.py --post failed: {r.stdout}{r.stderr}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pr", type=int, help="PR number")
    ap.add_argument("--repo", default="", help="owner/name (default: current repo)")
    ap.add_argument("--out-dir", default="card-preview", help="where to write the cards")
    ap.add_argument("--card-only", action="store_true",
                    help="emit bare AdaptiveCards for https://adaptivecards.io/designer "
                         "rather than the Teams message envelope")
    ap.add_argument("--post", action="store_true",
                    help="also POST each card — point this at a TEST channel")
    ap.add_argument("--webhook", default="",
                    help="webhook URL; defaults to $TEAMS_WEBHOOK. Use a TEST channel.")
    args = ap.parse_args()

    repo = args.repo
    if not repo:
        r = subprocess.run(["gh", "repo", "view", "--json", "nameWithOwner",
                            "--jq", ".nameWithOwner"],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace")
        repo = r.stdout.strip() or sys.exit("could not determine repo; pass --repo")

    pr_meta = json.loads(gh_json(f"repos/{repo}/pulls/{args.pr}"))
    ref = pr_meta.get("merge_commit_sha") or pr_meta["head"]["sha"]
    pr_url = pr_meta["html_url"]
    merged = pr_meta.get("merged", False)

    paths = changed_plugin_files(repo, args.pr)
    print(f"PR #{args.pr}  merged={merged}  ref={ref[:10]}")
    print(f"  plugin files changed: {len(paths)}")
    if not paths:
        roots = " or ".join(PLUGIN_ROOTS)
        print(f"  nothing under {roots} — the workflow would post nothing.")
        return 0

    skills, components = split(paths)
    out = pathlib.Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    common = ["--repo", repo, "--ref", ref, "--pr-url", pr_url]
    if args.card_only:
        common.append("--card-only")

    for skill_md in skills:
        name = pathlib.Path(skill_md).parent.name
        dest = out / f"skill-{name}.json"
        render([skill_md, "--audience", "infosec",
                "--security", "(not run locally)",
                "--headline", "Skill merged — now available to staff", *common], dest,
               post=args.post, webhook=args.webhook)
        print(f"  skill card    -> {dest}")

    print()
    if args.post:
        print(f"Posted to the webhook. Cards also written to {out}/ for inspection.")
    else:
        print("Nothing was posted. Paste a card into "
              "https://adaptivecards.io/designer to preview.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
