#!/usr/bin/env python3
"""
preview_infosec_cards.py — render the Infosec cards for a PR without posting.

Mirrors what .github/workflows/infosec-notify.yml does on merge: take the files
a PR changed, split them into skills (some parent has SKILL.md) and non-skill
plugin components, and build a card for each. It writes the cards to disk
instead of POSTing them, so you can check wording and layout before anything
reaches a Teams channel.

Everything except the trigger can be tested this way. The trigger itself
(pull_request / closed / merged) needs a real merge -- and `act` is not an
option here, since Docker Desktop cannot start with WSL2 blocked by policy.

Usage:
  python scripts/preview_infosec_cards.py 19
  python scripts/preview_infosec_cards.py 19 --out-dir preview --card-only
  python scripts/preview_infosec_cards.py 19 --post --webhook https://...

--post sends one message per card. Point it at a TEST channel.

Requires the `gh` CLI, authenticated. Check out the PR's merged state first
(`gh pr checkout <n>`, or the base branch after merge) -- the split tests for
SKILL.md on disk, so on the wrong branch a skill can be misfiled as a
component.
"""
import argparse
import json
import pathlib
import subprocess
import sys

# Card text contains em dashes and emoji; Windows stdout defaults to cp1252,
# which mangles both. Same guard as validate_skill.py and teams_card.py.
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
    """evals/ is the test harness, not payload.

    An eval rubric can change without the skill's content, tools or behaviour
    changing at all -- PR #31 changed only python-code-fixer/evals/evals.json.
    Posting "Skill merged - now available to staff" for that is noise, and noise
    is what stops people reading the cards that matter. Everything else in a
    skill directory (SKILL.md, references/, assets) is loadable at runtime and
    still notifies. Must match the grep in infosec-notify.yml.
    """
    return rel.startswith("evals/") or "/evals/" in rel


def changed_plugin_files(repo: str, pr: int) -> list[str]:
    raw = gh_json(f"repos/{repo}/pulls/{pr}/files", "--paginate", "--jq", ".[].filename")
    return [ln.strip() for ln in raw.splitlines()
            if ln.strip().startswith(PLUGIN_ROOTS) and not is_evals(ln.strip())]


def split(paths: list[str]) -> tuple[list[str], list[str]]:
    """Attribute each path to its owning skill, or call it a component.

    Walks up looking for SKILL.md rather than matching a path pattern: skills
    sit at varying depths (skills/<skill>/ and skills/<category>/<skill>/), and
    a depth-assuming regex misses one shape.
    """
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

    teams_card.py is addressed absolutely: it used to be invoked as
    "scripts/teams_card.py", which only resolved when the cwd happened to be the
    repo root — from scripts/ it became scripts/scripts/teams_card.py.
    """
    base = [sys.executable, str(SCRIPTS / "teams_card.py"), *args_list]
    r = subprocess.run(base + ["--out", str(out_path)],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    if r.returncode != 0:
        sys.exit(f"teams_card.py failed: {r.stderr.strip()}")
    if post:
        # A separate run: --post sends the message envelope, while --out with
        # --card-only writes the bare card. Asking for both in one call would
        # make the file shape depend on whether you posted.
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
