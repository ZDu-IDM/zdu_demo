#!/usr/bin/env python3
"""Aggregate the governance signals for a PR into one payload.

Writes governance-results.json and prints a Markdown summary (for
$GITHUB_STEP_SUMMARY): changed skills and tier, who has approved on GitHub,
and each tracked check's result. GitHub approvals are not the merge gate, so
nothing here is declared "approved".

Env: REPO (owner/name), PR_NUMBER, GH_TOKEN.  Optional: OUTPUT (default
governance-results.json).
"""
import argparse
import base64
import fnmatch
import json
import os
import subprocess
import sys

import yaml
from datetime import datetime, timezone
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from utils import (ROOT_FOUNDATION, ROOT_GROUPS, classify_path,  # noqa: E402
                   listed_text, skill_metadata)

# Check-run name substring (case-insensitive) -> label. Must cover every check
# the branch ruleset requires, or "all checks green" can be false. Semgrep is
# advisory, so it is left out. See docs/BRANCH-PROTECTION.md.
CHECK_MAP = [
    ("validate skill", "validate"),
    ("run evals", "evals"),
    ("scan skills", "security-scan"),
    ("yara rule", "yara-rules"),
    ("lint python", "lint"),
]
# All must have reported: a check that has not started yet is not passing.
EXPECTED_CHECKS = {label for _, label in CHECK_MAP}

# Two roles, resolved from GitHub:
#   - governance board: the GOVERNANCE_TEAM team;
#   - business owner: the path's CODEOWNERS owners minus the board (e.g.
#     idm-leads for plugins/groups/idm). None for foundation-wide.
# Team lookups need an org token (GOVERNANCE_ORG_TOKEN). GOVERNANCE_BOARD and
# GOVERNANCE_DOMAIN (comma-separated logins) override them for tests.
#
# The board slug is defined only here; keep it matching .github/CODEOWNERS, or
# every owner looks like a business owner.
GOVERNANCE_TEAM = os.environ.get("GOVERNANCE_TEAM", "skills-working-group")
CODEOWNERS_PATH = Path(__file__).resolve().parent.parent / ".github" / "CODEOWNERS"


def _gh_team_members(owner: str) -> set:
    """Members (lowercased logins) of an 'org/slug' team; empty on any failure.

    Uses GOVERNANCE_ORG_TOKEN (org Members:read only), not the repo GH_TOKEN;
    neither token can do the other's job. Without it the lookup returns empty,
    so no business owner is attributed.
    """
    org, _, slug = owner.partition("/")
    if not (org and slug):
        return set()
    env = os.environ.copy()
    org_token = env.get("GOVERNANCE_ORG_TOKEN", "").strip()
    if org_token:
        env["GH_TOKEN"] = org_token
    r = subprocess.run(  # nosemgrep: dangerous-subprocess-use-tainted-env-args
        ["gh", "api", "--paginate", f"orgs/{org}/teams/{slug}/members"],
        capture_output=True, text=True, env=env)
    if r.returncode != 0:
        print(f"::warning::could not read team {owner} — needs an organization "
              f"token with Members:read (GOVERNANCE_ORG_TOKEN): "
              f"{r.stderr.strip()}", file=sys.stderr)
        return set()
    try:
        data = json.loads(r.stdout)
    except json.JSONDecodeError:
        print(f"::warning::unexpected response reading team {owner}", file=sys.stderr)
        return set()
    return {m["login"].lower() for m in data if m.get("login")}


def load_board_members(repo: str = "") -> frozenset:
    """Governance-board logins (lowercased) from GOVERNANCE_TEAM; empty on failure."""
    env = os.environ.get("GOVERNANCE_BOARD", "").strip()
    if env:
        return frozenset(n.strip().lower() for n in env.split(",") if n.strip())
    org = os.environ.get("GOVERNANCE_ORG") or (repo.split("/")[0] if "/" in repo else "")
    if not (org and GOVERNANCE_TEAM):
        print("::warning::governance board not resolved (missing org or team slug); "
              "no 'approved' cards will post", file=sys.stderr)
        return frozenset()
    members = _gh_team_members(f"{org}/{GOVERNANCE_TEAM}")
    if not members:
        print("::warning::governance board resolved empty (check team + read:org "
              "token); no 'approved' cards will post", file=sys.stderr)
    return frozenset(members)


def parse_codeowners(text: str):
    """Return ordered [(pattern, [owners]), ...] from CODEOWNERS text."""
    rules = []
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) >= 2:
            rules.append((parts[0], parts[1:]))
    return rules


def _co_match(pattern: str, path: str) -> bool:
    """Pragmatic CODEOWNERS matcher (gitignore-ish subset)."""
    pat = pattern.strip()
    if pat == "*":
        return True
    rooted = pat.startswith("/")
    p = pat.lstrip("/")
    if p.endswith("/"):                                   # directory -> everything under it
        return path == p.rstrip("/") or path.startswith(p)
    if fnmatch.fnmatch(path, p) or fnmatch.fnmatch(path, p + "/*"):
        return True
    if not rooted and (fnmatch.fnmatch(path, "*/" + p)   # unrooted matches anywhere in the tree
                       or fnmatch.fnmatch(path, "*/" + p + "/*")):
        return True
    return False


def owning_owners(paths, rules):
    """CODEOWNERS owners for the changed paths (last match wins per path).

    Returns (team_owners, user_logins): team_owners like {"org/slug"}, users
    like {"login"}.
    """
    teams, users = set(), set()
    for path in paths:
        matched = None
        for pattern, owners in rules:      # last matching rule wins, per CODEOWNERS
            if _co_match(pattern, path):
                matched = owners
        for o in (matched or []):
            o = o.lstrip("@")
            (teams if "/" in o else users).add(o)
    return teams, users


def load_domain(paths, repo: str = ""):
    """(business-owner logins, [team slugs]) for the changed paths.

    The paths' CODEOWNERS owners minus the board; empty for foundation-wide or
    on failure.
    """
    env = os.environ.get("GOVERNANCE_DOMAIN", "").strip()
    if env:
        return frozenset(n.strip().lower() for n in env.split(",") if n.strip()), []
    if not CODEOWNERS_PATH.exists():
        return frozenset(), []
    rules = parse_codeowners(CODEOWNERS_PATH.read_text(encoding="utf-8"))
    teams, users = owning_owners(paths, rules)
    domain_teams = sorted(t for t in teams if t.partition("/")[2] != GOVERNANCE_TEAM)
    members = {u.lower() for u in users}
    for t in domain_teams:
        members |= _gh_team_members(t)
    return frozenset(members), [t.partition("/")[2] for t in domain_teams]


def card_stage(approvers, board_members, domain_members,
               ready_signal=False, changes_requested=(), dismissed=()):
    """Decide which Teams card (if any) this review state warrants.

    Based on who approved, not how many. Stages, highest first:
      - "intake":          a business owner approved.
      - "ready_changes":   checks green, not a draft, a reviewer requested changes.
      - "ready_reapprove": checks green, not a draft, an approval was dismissed.
      - "ready":           checks green, not a draft, no business-owner approval.
      - "none":            anything else; nothing is posted.

    There is no "approved" stage: the real sign-off is outside GitHub.
    Business owner = a member of the path's domain team; with no domain team
    (foundation-wide), any non-board approver.
    """
    board = {m.lower() for m in board_members}
    domain = {m.lower() for m in domain_members}
    board_approvers = [a for a in approvers if a.lower() in board]
    if domain:
        business = [a for a in approvers if a.lower() in domain]
    elif board:
        # Only with a resolved board: subtracting an empty set would make
        # every approver a business owner.
        business = [a for a in approvers if a.lower() not in board]
    else:
        # Nothing resolved (no org token, fork): name nobody.
        business = []
    # A board approval alone selects no stage; the PR still awaits its owner.
    if business:                                 # a business owner sponsored it
        stage = "intake"
    elif ready_signal:                           # checks green, not draft, no approvals
        # Requested changes outrank a dismissed approval.
        if changes_requested:
            stage = "ready_changes"
        elif dismissed:
            stage = "ready_reapprove"
        else:
            stage = "ready"
    else:
        stage = "none"
    return stage, board_approvers, (business[0] if business else "")


def _frontmatter(text: str) -> dict:
    """Parse a SKILL.md's YAML frontmatter, or {} if it is absent or malformed."""
    parts = str(text).split("---", 2)
    if len(parts) < 3:
        return {}
    try:
        fm = yaml.safe_load(parts[1])
    except yaml.YAMLError:
        return {}
    return fm if isinstance(fm, dict) else {}


def gh(path: str, paginate: bool = False):
    cmd = ["gh", "api", path] + (["--paginate"] if paginate else [])
    # Explicit UTF-8: the Windows locale codec cannot decode GitHub's emoji.
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        print(f"::error::gh api {path} failed: {r.stderr.strip()}", file=sys.stderr)
        sys.exit(2)
    return json.loads(r.stdout)


def tier_of(paths):
    return "foundation-wide" if any(
        (classify_path(p) or (None,))[0] == ROOT_FOUNDATION for p in paths
    ) else "group"


def groups_of(paths):
    gs = set()
    for p in paths:
        classified = classify_path(p)
        if classified and classified[0] == ROOT_GROUPS and classified[1]:
            gs.add(classified[1][0])
    return sorted(gs)


def first_sentence(text: str, limit: int = 180) -> str:
    """The first sentence of a description, cut at a word boundary past `limit`."""
    s = " ".join(str(text or "").split())
    if not s:
        return ""
    # A sentence ends at ". " followed by a capital or quote, and not after an
    # abbreviation like "e.g.".
    for i, ch in enumerate(s):
        if ch not in ".?!" or i + 1 >= len(s) or s[i + 1] != " ":
            continue
        nxt = s[i + 2] if i + 2 < len(s) else ""
        if not (nxt.isupper() or nxt in "\"'“‘"):
            continue
        token = s[:i].rsplit(" ", 1)[-1]
        if "." in token:                    # e.g. / i.e. / U.S.
            continue
        s = s[: i + 1]
        break
    if len(s) > limit:
        cut = s.rfind(" ", 0, limit)
        s = s[: cut if cut > 0 else limit].rstrip(" ,;:") + "…"
    return s


def changed_skills(paths, statuses=None, read_skill=None):
    """Describe each changed skill for triage.

    `statuses` maps path -> PR file status ("added", ...). `read_skill(path)`
    returns the PR head's SKILL.md text, or None. It is injected because this
    runs under workflow_run, where the checkout is main, not the PR.
    """
    statuses = statuses or {}
    out = []
    for p in paths:
        if "/skills/" in p and p.endswith("/SKILL.md"):
            classified = classify_path(p)
            group = (classified[1][0]
                     if classified and classified[0] == ROOT_GROUPS and classified[1]
                     else None)
            tier = "foundation-wide" if classified and classified[0] == ROOT_FOUNDATION else "group"
            entry = {
                "path": p.rsplit("/", 1)[0],
                "name": p.rsplit("/", 2)[-2],
                "tier": tier,
                "group": group,
                "status": statuses.get(p, ""),
                "summary": "",
                "surfaces": "",
            }
            text = read_skill(p) if read_skill else None
            if text:
                fm = _frontmatter(text)
                meta = skill_metadata(fm)
                entry["summary"] = first_sentence(fm.get("description"))
                # Absent means Claude Code only.
                entry["surfaces"] = listed_text(meta.get("surfaces"),
                                                "claude-code (default)")
            out.append(entry)
    return out


def build_record(repo, pr, files, reviews, check_runs, approved_by="",
                 board_members=frozenset(), domain_members=frozenset(), domain_teams=(),
                 read_skill=None):
    """Pure aggregation, no network: raw API objects in, the record out.

    `read_skill(path) -> text | None` is the one injected side effect.
    """
    paths = [f["filename"] for f in files]
    statuses = {f["filename"]: f.get("status", "") for f in files}
    author = pr["user"]["login"]
    tier = tier_of(paths)

    latest = {}
    for rv in reviews:
        if rv["state"] in ("APPROVED", "CHANGES_REQUESTED", "DISMISSED"):
            latest[rv["user"]["login"]] = rv["state"]
    approvers = sorted(u for u, s in latest.items() if s == "APPROVED" and u != author)
    changes_by = sorted(u for u, s in latest.items() if s == "CHANGES_REQUESTED")
    # Dismissed = an approval invalidated, usually by a new push.
    dismissed_by = sorted(u for u, s in latest.items() if s == "DISMISSED" and u != author)

    # latest conclusion per tracked governance check
    seen = {}
    for cr in check_runs:
        low = (cr.get("name") or "").lower()
        key = next((k for sub, k in CHECK_MAP if sub in low), None)
        if key:
            seen[key] = cr.get("conclusion") or cr.get("status")  # success/failure/queued/...
    checks = [{"check": k, "conclusion": v} for k, v in sorted(seen.items())]
    failed = [c["check"] for c in checks if c["conclusion"] in ("failure", "timed_out", "cancelled", "action_required")]
    pending = [c["check"] for c in checks if c["conclusion"] in ("queued", "in_progress", None)]
    checks_passed = (set(seen) >= EXPECTED_CHECKS
                     and not failed and not pending)

    # Derived, not passed in, so "ready" can never be claimed while CI is red.
    ready_signal = checks_passed and not pr.get("draft")
    stage, board_approvers, business_owner = card_stage(
        approvers, board_members, domain_members, ready_signal,
        changes_by, dismissed_by)

    if pr.get("draft"):
        overall = "draft"
    elif changes_by:
        overall = "changes_requested"
    elif failed:
        overall = "checks_failing"
    elif pending:
        overall = "checks_pending"
    elif not business_owner:
        overall = "awaiting_approval"
    else:
        overall = "ready"

    return {
        "repo": repo,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "approved_by": approved_by,
        "pr": {
            "number": pr["number"], "title": pr["title"], "url": pr["html_url"],
            "author": author, "head_sha": pr["head"]["sha"],
            "base": pr["base"]["ref"], "draft": bool(pr.get("draft")),
        },
        "tier": tier,
        "groups": groups_of(paths),
        "changed_skills": changed_skills(paths, statuses, read_skill),
        "approvals": {
            "count": len(approvers), "approvers": approvers,
            "changes_requested_by": changes_by,
            "dismissed_by": dismissed_by,
        },
        "governance": {
            "card_stage": stage,            # ready | ready_changes | ready_reapprove | intake | none
            "board_approvers": board_approvers,
            "business_owner": business_owner,
            "business_owner_teams": list(domain_teams),
        },
        "checks": checks,
        "checks_passed": checks_passed,
        "failed_checks": failed,
        "pending_checks": pending,
        "overall": overall,
    }


def markdown(rec):
    icon = {"success": "✅", "failure": "❌", "timed_out": "❌", "cancelled": "❌",
            "action_required": "❌", "neutral": "➖", "skipped": "⏭️",
            "queued": "⏳", "in_progress": "⏳", None: "⏳"}
    p = rec["pr"]
    ap = rec["approvals"]
    gov = rec.get("governance") or {}
    L = [
        f"## Governance summary — PR #{p['number']}",
        f"**[{p['title']}]({p['url']})** by @{p['author']} · tier **{rec['tier']}**"
        + (f" ({', '.join(rec['groups'])})" if rec["groups"] else ""),
        "",
        f"**Overall:** `{rec['overall']}`",
        "",
    ]
    # Every approver on one line, the business owner annotated in place.
    bo = str(gov.get("business_owner") or "")
    approved = ", ".join(
        f"@{a} (business owner)" if a.lower() == bo.lower() else "@" + a
        for a in ap["approvers"])
    L += ["**Approved by:** " + (approved or "_nobody yet_"), ""]
    if ap["changes_requested_by"]:
        L += ["**Changes requested by:** "
              + ", ".join("@" + a for a in ap["changes_requested_by"]), ""]
    if gov.get("card_stage") == "ready_changes":
        who = ", ".join("@" + a for a in ap.get("changes_requested_by", []))
        L.append(f"**Teams card:** changes pushed — checks green again; {who} "
                 "requested changes and needs to re-review")
        L.append("")
    elif gov.get("card_stage") == "ready_reapprove":
        who = ", ".join("@" + a for a in ap.get("dismissed_by", []))
        L.append(f"**Teams card:** re-approval needed — a push dismissed {who}'s "
                 "approval; checks are green again")
        L.append("")
    elif gov.get("card_stage") == "ready":
        L.append("**Teams card:** ready for review — all checks green and not a "
                 "draft; business owner review requested")
        L.append("")
    elif gov.get("card_stage") == "intake":
        owner = gov.get("business_owner") or rec.get("approved_by")
        teams = gov.get("business_owner_teams") or []
        tstr = f" [{', '.join(teams)}]" if teams else ""
        L.append(f"**Teams card:** intake — @{owner}{tstr} (business owner) approved; "
                 "IT owner takes it to the enablement form")
        L.append("")
    elif rec.get("approved_by"):
        L.append(f"**Approved by:** @{rec['approved_by']} (business owner)")
        L.append("")
    if rec["changed_skills"]:
        L.append("**Changed skills:** " + ", ".join(f"`{s['path']}`" for s in rec["changed_skills"]))
        L.append("")
    L += ["| Check | Result |", "|---|---|"]
    for c in rec["checks"]:
        L.append(f"| {c['check']} | {icon.get(c['conclusion'], '?')} {c['conclusion'] or 'pending'} |")
    if not rec["checks"]:
        L.append("| _(no governance checks reported yet)_ | ⏳ |")
    return "\n".join(L) + "\n"


def _load_fixtures(directory):
    """Offline inputs: pr.json, files.json, reviews.json, check-runs.json.
    Dump them once with `gh api` (see --help) and iterate without the network."""
    d = Path(directory)
    load = lambda name: json.loads((d / name).read_bytes())  # bytes -> json auto-detects UTF-8/16/32
    pr, files, reviews = load("pr.json"), load("files.json"), load("reviews.json")
    raw = load("check-runs.json")
    checks = raw.get("check_runs", []) if isinstance(raw, dict) else raw
    return pr, files, reviews, checks


def main():
    ap = argparse.ArgumentParser(
        description="Consolidated governance summary for a PR.",
        epilog=(
            "Offline dump for --fixtures (run once, then: --fixtures DIR):\n"
            "  gh api repos/O/R/pulls/N > DIR/pr.json\n"
            "  gh api repos/O/R/pulls/N/files --paginate > DIR/files.json\n"
            "  gh api repos/O/R/pulls/N/reviews --paginate > DIR/reviews.json\n"
            "  gh api 'repos/O/R/commits/SHA/check-runs?per_page=100' > DIR/check-runs.json"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fixtures", metavar="DIR",
                    help="read the API responses from DIR instead of calling gh (offline)")
    ap.add_argument("--output", default=os.environ.get("OUTPUT", "governance-results.json"))
    args = ap.parse_args()

    if args.fixtures:
        pr, files, reviews, checks = _load_fixtures(args.fixtures)
        repo = os.environ.get("REPO") or pr.get("base", {}).get("repo", {}).get("full_name", "org/repo")
    else:
        repo = os.environ["REPO"]
        pr_num = os.environ["PR_NUMBER"]
        pr = gh(f"repos/{repo}/pulls/{pr_num}")
        files = gh(f"repos/{repo}/pulls/{pr_num}/files", paginate=True)
        reviews = gh(f"repos/{repo}/pulls/{pr_num}/reviews", paginate=True)
        checks = gh(f"repos/{repo}/commits/{pr['head']['sha']}/check-runs?per_page=100").get("check_runs", [])

    approved_by = os.environ.get("APPROVED_BY", "").strip()
    board = load_board_members(repo)
    domain_members, domain_teams = load_domain([f["filename"] for f in files], repo)
    # Read SKILL.md from the PR head, not the checkout (main). Failures only
    # cost a card its summary line.
    def read_skill(path):
        try:
            blob = gh(f"repos/{repo}/contents/{path}?ref={pr['head']['sha']}")
        except SystemExit:
            return None
        if not isinstance(blob, dict) or blob.get("encoding") != "base64":
            return None
        try:
            return base64.b64decode(blob.get("content", "")).decode("utf-8", "replace")
        except (ValueError, TypeError):
            return None

    rec = build_record(repo, pr, files, reviews, checks, approved_by,
                       board, domain_members, domain_teams, read_skill)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(rec, f, indent=2)
    print(markdown(rec))
    return 0


if __name__ == "__main__":
    sys.exit(main())
