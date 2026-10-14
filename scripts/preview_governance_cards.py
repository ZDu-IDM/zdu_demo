#!/usr/bin/env python3
"""
preview_governance_cards.py — render every governance card, from one PR's data.

Companion to preview_infosec_cards.py. That one previews the Infosec cards
posted on merge; this one previews the cards governance-summary.yml posts during
review.

A real PR only ever renders the ONE stage it is actually in, so checking wording
and layout PR-by-PR would take six differently-shaped PRs. This takes a single
PR's real data and re-renders it once per stage, overriding the review state each
time so the card matches the stage it claims.

Six stages have a card. The seventh, "none", posts nothing by design and so has
nothing to preview.

Usage:
  # write all six to disk, post nothing
  python scripts/preview_governance_cards.py 41

  # send them to a TEST channel to see them as Teams renders them
  python scripts/preview_governance_cards.py 41 --post --webhook https://...

  # no PR / no network: synthetic data
  python scripts/preview_governance_cards.py --synthetic

PowerShell note: prefer --out-dir over shell redirection. `>` writes a UTF-8 BOM
that json.loads rejects, and Get-Content needs -Encoding utf8 to show the glyphs.

--post sends one message per stage. Point it at a test channel, never the real
governance channel.
"""
import argparse
import json
import pathlib
import subprocess
import sys

SCRIPTS = pathlib.Path(__file__).resolve().parent
# Card text contains em dashes and emoji; Windows stdout defaults to cp1252,
# which mangles both. Same guard as validate_skill.py and teams_card.py.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(SCRIPTS))

import teams_card  # noqa: E402

# Review state that makes each stage truthful. card_stage() derives the stage
# from these, so the preview goes through the same code path as production
# rather than just forcing a label onto an unrelated record.
#   approvers  -> currently approving (non-author)
#   changes    -> reviewers with changes requested outstanding
#   dismissed  -> reviewers whose approval a push invalidated
BOARD = "gov-member"
BIZ = "biz-owner"

# "awaiting_second" and "approved" are gone from teams_card.STAGE: both asserted
# something about an approval count or a governance sign-off that GitHub no
# longer holds. BOARD still appears below because a dismissed board approval is
# a real state the "ready_reapprove" card is written for.
STAGE_STATE = {
    "ready":            {"approvers": [],      "changes": [],    "dismissed": []},
    "ready_changes":    {"approvers": [],      "changes": [BIZ], "dismissed": []},
    "ready_reapprove":  {"approvers": [],      "changes": [],    "dismissed": [BOARD]},
    "intake":           {"approvers": [BIZ],   "changes": [],    "dismissed": []},
}


def gh_json(path: str, paginate: bool = False):
    cmd = ["gh", "api", path] + (["--paginate"] if paginate else [])
    # encoding pinned for the same reason governance_summary.gh() pins it: PR
    # titles and commit messages carry characters cp1252 cannot decode, so the
    # locale default raises UnicodeDecodeError on Windows.
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        sys.exit(f"gh api {path} failed:\n{r.stderr.strip()}")
    return json.loads(r.stdout)


def current_repo() -> str:
    return gh_json("repos/{owner}/{repo}")["full_name"]


def real_record(repo: str, pr_num: int, out_dir: pathlib.Path) -> dict:
    """Build a record from a live PR, via the same script CI uses."""
    # Written inside out_dir rather than the cwd: this script is run from
    # wherever the user happens to be, and dropping governance-results.json
    # into scripts/ or a home directory is surprising.
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "governance-results.json"
    env_note = f"REPO={repo} PR_NUMBER={pr_num}"
    r = subprocess.run(
        [sys.executable, str(SCRIPTS / "governance_summary.py"),
         "--output", str(out)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env={**__import__("os").environ, "REPO": repo, "PR_NUMBER": str(pr_num)},
    )
    if r.returncode != 0:
        sys.exit(f"governance_summary.py failed ({env_note}):\n{r.stdout}\n{r.stderr}")
    return json.loads(out.read_text(encoding="utf-8"))


def synthetic_record() -> dict:
    """A record with no network and no PR — enough for wording and layout."""
    # Shape must match build_record's output exactly — card() indexes into
    # changed_skills[]["path"], so a plain list of strings raises TypeError.
    return {
        "pr": {"number": 0, "title": "Add a demo skill",
               "url": "https://example.invalid/pull/0", "author": "author",
               "base": "main", "head_sha": "0" * 40, "draft": False},
        "repo": "org/repo",
        "tier": "group",
        "groups": ["idm"],
        "approved_by": "",
        "approvals": {"count": 0, "approvers": [],
                      "changes_requested_by": [], "dismissed_by": []},
        "checks": [{"check": c, "conclusion": "success"} for c in
                   ("evals", "lint", "security-scan", "validate", "yara-rules")],
        "checks_passed": True,
        "failed_checks": [],
        "pending_checks": [],
        # Populated the way changed_skills() populates it from real frontmatter,
        # so the preview exercises the whole block. Left as bare paths, the
        # preview showed only a bold name and could not check the thing it
        # exists to check -- the summary, tool list and surface line that a
        # governance reviewer actually reads.
        #
        # Two entries on purpose: one new group skill, one edit to a skill
        # that also runs on Claude.ai. Those are the two shapes a reviewer
        # triages differently, and two entries are what makes a layout problem
        # visible -- a single-entry card always looks fine.
        "changed_skills": [
            {"path": "plugins/groups/idm/software-tools/skills/demo",
             "name": "demo", "tier": "group", "group": "idm", "status": "added",
             "summary": ("Use when someone needs a demonstration of the "
                         "governance card layout."),
             "surfaces": "claude-code"},
            {"path": "plugins/shared-workflows/example-task/skills/example-review",
             "name": "example-review", "tier": "group", "group": None,
             "status": "modified",
             "summary": ("Use when reviewing a document that several teams must "
                         "sign off on, to produce a single consolidated review."),
             "surfaces": "claude-code, claude-ai"},
        ],
        "overall": "ready",
        "generated_at": "1970-01-01T00:00:00Z",
        "governance": {"card_stage": "ready", "board_approvers": [],
                       "business_owner": "", "business_owner_teams": []},
    }


def restage(rec: dict, stage: str) -> dict:
    """Return a copy of rec whose review state matches `stage`."""
    st = STAGE_STATE[stage]
    out = json.loads(json.dumps(rec))          # deep copy
    out["approvals"] = {
        "count": len(st["approvers"]),
        "approvers": list(st["approvers"]),
        "changes_requested_by": list(st["changes"]),
        "dismissed_by": list(st["dismissed"]),
    }
    board = [a for a in st["approvers"] if a == BOARD]
    business = [a for a in st["approvers"] if a == BIZ]
    out["governance"] = {
        **(out.get("governance") or {}),
        "card_stage": stage,
        "board_approvers": board,
        "business_owner": business[0] if business else "",
        "business_owner_teams": ["idm-leads"] if business else [],
    }
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pr", nargs="?", type=int, help="PR number to take real data from")
    ap.add_argument("--repo", default="", help="owner/name (default: current repo)")
    ap.add_argument("--synthetic", action="store_true",
                    help="use synthetic data instead of a PR (no gh, no network)")
    ap.add_argument("--out-dir", default="card-preview", help="where to write the cards")
    ap.add_argument("--post", action="store_true",
                    help="POST each card — point this at a TEST channel")
    ap.add_argument("--webhook", default="",
                    help="webhook URL; defaults to $TEAMS_WEBHOOK. Use a TEST channel.")
    ap.add_argument("--stage", action="append",
                    help="only this stage; repeatable (default: all)")
    args = ap.parse_args()

    if not args.synthetic and args.pr is None:
        ap.error("give a PR number, or --synthetic")

    out_dir = pathlib.Path(args.out_dir)
    rec = synthetic_record() if args.synthetic else real_record(
        args.repo or current_repo(), args.pr, out_dir)

    stages = args.stage or list(STAGE_STATE)
    unknown = [s for s in stages if s not in STAGE_STATE]
    if unknown:
        ap.error(f"unknown stage(s): {', '.join(unknown)}; "
                 f"choose from {', '.join(STAGE_STATE)}")

    out_dir.mkdir(parents=True, exist_ok=True)

    failures: list[str] = []

    for stage in stages:
        staged = restage(rec, stage)
        rec_path = out_dir / f"{stage}.record.json"
        rec_path.write_text(json.dumps(staged, indent=2), encoding="utf-8")

        # Write the bare Adaptive Card, not the webhook envelope card() returns.
        # Same unwrapping as teams_card.py --card-only: the Adaptive Cards
        # designer rejects the {"type":"message","attachments":[...]} wrapper
        # with "Invalid or missing card type", and the wrapper also buries the
        # body one level deeper for anyone inspecting the file.
        envelope = teams_card.card(staged)
        card = envelope["attachments"][0]["content"]
        card_path = out_dir / f"{stage}.card.json"
        card_path.write_text(json.dumps(card, indent=2, ensure_ascii=False),
                             encoding="utf-8")

        header = teams_card.STAGE[stage][0]
        print(f"  {stage:<17} {header}")
        print(f"  {'':<17} -> {card_path}")

        if args.post:
            # Semgrep flags args.webhook reaching subprocess as tainted input.
            # Not reachable as command injection: this is the list form with no
            # shell=True, so an argument VALUE cannot become a command, and the
            # only "taint" source is this script's own --webhook flag — anyone
            # who can set it already controls the whole command line. sys.
            # executable and the script path are not user-supplied.
            #
            # Note the identical construct in preview_infosec_cards.py's
            # render() is NOT flagged, because the value arrives as a function
            # parameter and the taint tracker loses it. Same risk, different
            # traceability — which is what tells you this is about the analysis,
            # not the code.
            #
            # The nosemgrep must sit directly above the line Semgrep reports,
            # which is the argument list below — not above the subprocess.run(
            # call. Placed one line higher it is ignored silently.
            r = subprocess.run(
                # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-tainted-env-args.dangerous-subprocess-use-tainted-env-args
                [sys.executable, str(SCRIPTS / "teams_card.py"), str(rec_path), "--post"]
                + (["--webhook", args.webhook] if args.webhook else []),
                capture_output=True, text=True, encoding="utf-8", errors="replace")
            if r.returncode == 0:
                print(f"  {'':<17} -> posted")
            else:
                failures.append(stage)
                print(f"  {'':<17} -> FAILED: " + (r.stdout + r.stderr).strip())

    print(f"\n{len(stages)} card(s) in {out_dir}/")
    if not args.post:
        print("Nothing was posted. Add --post (and a TEST webhook) to send them.")
    if failures:
        # Exit non-zero: a run that swallowed a failed POST would look
        # identical to one that reached the channel.
        print("POST failed for: " + ", ".join(failures), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
