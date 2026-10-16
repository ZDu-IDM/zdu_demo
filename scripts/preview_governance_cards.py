#!/usr/bin/env python3
"""
preview_governance_cards.py — render every governance card, from one PR's data.

Previews the cards governance-summary.yml posts during review. A real PR shows
only its current stage, so this re-renders one PR's data once per stage. Four
stages have a card; "none" posts nothing.

Usage:
  # write all four to disk, post nothing
  python scripts/preview_governance_cards.py 41

  # send them to a TEST channel to see them as Teams renders them
  python scripts/preview_governance_cards.py 41 --post --webhook https://...

  # no PR / no network: synthetic data
  python scripts/preview_governance_cards.py --synthetic

In PowerShell use --out-dir, not `>`, which adds a BOM that json.loads rejects.

--post sends one message per stage. Point it at a test channel, never the real
governance channel.
"""
import argparse
import json
import pathlib
import subprocess
import sys

SCRIPTS = pathlib.Path(__file__).resolve().parent
# Card text has em dashes and emoji, which Windows' default codepage mangles.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(SCRIPTS))

import teams_card  # noqa: E402

# The review state that produces each stage: current approvers, reviewers who
# requested changes, and reviewers whose approval was dismissed.
BOARD = "gov-member"
BIZ = "biz-owner"

STAGE_STATE = {
    "ready":            {"approvers": [],      "changes": [],    "dismissed": []},
    "ready_changes":    {"approvers": [],      "changes": [BIZ], "dismissed": []},
    "ready_reapprove":  {"approvers": [],      "changes": [],    "dismissed": [BOARD]},
    "intake":           {"approvers": [BIZ],   "changes": [],    "dismissed": []},
}


def gh_json(path: str, paginate: bool = False):
    cmd = ["gh", "api", path] + (["--paginate"] if paginate else [])
    # Explicit UTF-8: the Windows locale codec cannot decode GitHub's text.
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        sys.exit(f"gh api {path} failed:\n{r.stderr.strip()}")
    return json.loads(r.stdout)


def current_repo() -> str:
    return gh_json("repos/{owner}/{repo}")["full_name"]


def real_record(repo: str, pr_num: int, out_dir: pathlib.Path) -> dict:
    """Build a record from a live PR, via the same script CI uses."""
    # Written to out_dir, not the current directory.
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
    # Must match build_record's output shape.
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
        # Two full entries (one new, one edited), so layout problems show.
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

        # The bare card, which the Adaptive Cards designer accepts.
        envelope = teams_card.card(staged)
        card = envelope["attachments"][0]["content"]
        card_path = out_dir / f"{stage}.card.json"
        card_path.write_text(json.dumps(card, indent=2, ensure_ascii=False),
                             encoding="utf-8")

        header = teams_card.STAGE[stage][0]
        print(f"  {stage:<17} {header}")
        print(f"  {'':<17} -> {card_path}")

        if args.post:
            # Not command injection: list form, no shell, and --webhook is this
            # script's own flag. The nosemgrep must stay directly above the
            # argument list, or Semgrep ignores it.
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
        print("POST failed for: " + ", ".join(failures), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
