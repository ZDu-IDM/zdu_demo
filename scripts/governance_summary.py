#!/usr/bin/env python3
"""Aggregate the governance signals for a PR into one payload.

Produces a single consolidated record so the governance group (e.g. a Teams
forwarder) has one thing to consume instead of scraping logs and artifacts:

  - governance-results.json  — the machine-readable record
  - a Markdown summary on stdout — redirect into $GITHUB_STEP_SUMMARY

It combines: changed skills + tier, who has reviewed on GitHub, and each
governance check's conclusion (Checks API on the PR head SHA).

A GitHub approval is NOT the merge gate. Approval runs outside GitHub -- see
CONTRIBUTING.md, Approval process -- so this file counts nobody and declares
nothing approved. What it can say truthfully is: the checks are green, the PR
is not a draft, and here is who has looked.

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

# No approval count lives here any more. Branch protection no longer requires
# one, so "N of 2" would be a progress bar against a target nothing enforces --
# and a card reading "2/2 OK" beside a PR whose real approvals are an unseen
# OneTrust decision and an attestation form is worse than no number at all.
# Approvers are still listed, as who-has-looked, never as a threshold met.

# check-run name (lowercased) substring -> friendly key for the checks we track
# Substring -> label, matched case-insensitively against each check run's name.
# This list MUST cover every check the branch ruleset requires, or "all checks
# green" is a lie: an untracked required check can be red while checks_passed
# stays True, and the ready card would announce a PR that cannot merge. Semgrep
# is deliberately absent — it is advisory, so it must not gate the card.
# Cross-check against docs/BRANCH-PROTECTION.md and the live ruleset.
CHECK_MAP = [
    ("validate skill", "validate"),
    ("run evals", "evals"),
    ("scan skills", "security-scan"),
    ("yara rule", "yara-rules"),
    ("lint python", "lint"),
]
# Every label above must have reported before anything counts as green. A check
# that has not created its run yet is invisible, not passing — without this, the
# ready card could fire in the window before the last workflow starts.
EXPECTED_CHECKS = {label for _, label in CHECK_MAP}

# Two roles, both resolved from GitHub:
#   - Governance board: a SINGLE team (GOVERNANCE_TEAM) applied to every skill.
#     Its approval is the governance sign-off.
#   - Business / domain owner: the path's OTHER CODEOWNERS owner(s) — i.e. the
#     owning team(s) minus the governance board (idm-leads for a plugins/groups/idm PR).
#     Derived per-path from CODEOWNERS so the business owner is auto-attributed
#     by filepath. Empty for foundation-wide (domain == board).
# Resolving team membership needs an organization token with Members:read (the
# repo-scoped GITHUB_TOKEN cannot) -- see GOVERNANCE_ORG_TOKEN in
# _gh_team_members. GOVERNANCE_BOARD / GOVERNANCE_DOMAIN (comma-separated
# logins) short-circuit the lookups for tests.
#
# This default is the ONLY place the board's team slug is written down for the
# workflow's purposes. governance-summary.yml used to set GOVERNANCE_TEAM to
# this same string, which made two copies with nothing comparing them: rename
# the team and update one, and the board silently resolves empty -- which now
# means every CODEOWNERS owner looks like a business owner, so the board's own
# approval reads as the sponsoring one. The env override stays for forks, test
# orgs and a future rename.
#
# Keep it in step with the board listed in .github/CODEOWNERS. load_domain
# compares parsed CODEOWNERS owners against this value to work out which of
# them is the BUSINESS owner rather than the board, so a mismatch makes every
# owner look like a domain team.
GOVERNANCE_TEAM = os.environ.get("GOVERNANCE_TEAM", "skills-working-group")
CODEOWNERS_PATH = Path(__file__).resolve().parent.parent / ".github" / "CODEOWNERS"


def _gh_team_members(owner: str) -> set:
    """Members (lowercased logins) of an 'org/slug' team; {} on any failure.

    Uses GOVERNANCE_ORG_TOKEN, not GH_TOKEN, and the two are not
    interchangeable. This is the only call in this file that reads the
    ORGANIZATION; every other call reads the repository. The governance App
    holds exactly one permission -- Organization > Members: Read-only -- and no
    repository permissions at all, so its token cannot serve the repo calls, and
    the repo-scoped github.token cannot serve this one. Handing either token
    both jobs breaks half of them.

    Falls back to the ambient GH_TOKEN when GOVERNANCE_ORG_TOKEN is unset. The
    repo token cannot read team membership, so both sets resolve empty: no
    business owner is attributed, the stage stays at "ready", and nothing is
    claimed about who approved. Quiet rather than wrong.
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
    """Governance-board logins (lowercased) from the GOVERNANCE_TEAM team.

    Empty on any failure — which fails SAFE: no 'approved' card is ever posted,
    since that stage requires a board member.
    """
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
    """Business-owner (domain) members + team slugs for a PR's changed paths.

    Domain = the CODEOWNERS owners of the paths MINUS the governance board.
    Returns (members frozenset, [team_slug, ...]). Empty when there is no
    distinct domain team (foundation-wide, where domain == board) or on failure
    — in which case business-owner detection falls back to 'any non-board
    approver'. GOVERNANCE_DOMAIN env (comma-separated logins) is a test override.
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

    Keyed off *who* has approved, never on how many and never on order. Two
    stages went when approval moved out of GitHub, and both removals matter:
      - "approved" asserted a governance sign-off from GitHub approvals. The
        sign-off is now an OneTrust decision plus the IT owner's attestation,
        neither of which GitHub can see, so no state here can honestly claim
        it. A card reading "approved" on the strength of two clicks would be
        taken for the real thing by the one audience that must not be misled.
      - "awaiting_second" existed only to count up to the branch-protection
        minimum of two. There is no minimum now.

    What remains:
      - "intake":          a business owner has approved — the one approval
                           the process does ask for in GitHub. The next action
                           is the IT owner's: take it to the enablement form.
      - "ready_changes":   as "ready", but a reviewer has requested changes. Shown
                             to someone who already engaged, so it must say what
                             changed rather than repeat "ready for review".
      - "ready_reapprove": as "ready", but an earlier approval was dismissed — with
                             dismiss_stale_reviews_on_push enabled, that means a
                             push invalidated it. The reviewer needs to know their
                             approval is gone, not that the PR is newly ready.
      - "ready":           no qualifying approval yet, every tracked check is
                             green, and the PR is not a draft. This is the
                             step-1 -> step-2 handoff: every other stage needs an
                             approval to exist, so without it the business owner
                             is never told to look. Never overrides a real
                             approval stage.
      - "none":            anything else (a non-owner writer's approval, zero
                           approvals) — don't post, avoid clutter.

    Business owner = strictly a member of the path's domain team (from CODEOWNERS).
    If no distinct domain team resolved (foundation-wide, where domain == board,
    or lookup failed), fall back to any non-board approver. The board is still
    resolved for exactly that subtraction; it no longer selects a stage.
    """
    board = {m.lower() for m in board_members}
    domain = {m.lower() for m in domain_members}
    board_approvers = [a for a in approvers if a.lower() in board]
    if domain:
        business = [a for a in approvers if a.lower() in domain]
    elif board:
        # Foundation-wide: domain == board, so any non-board approver is the
        # sponsor. Requires a RESOLVED board -- subtracting an empty set leaves
        # every approver looking like a business owner.
        business = [a for a in approvers if a.lower() not in board]
    else:
        # Neither set resolved (no org token, renamed team, fork). Claim
        # nobody. This used to fail safe further down, where "approved"
        # required a board approver; with that stage gone, an unchecked
        # fallback here would put a passing writer's name on the card as the
        # business owner, which is the one field a governance reader trusts.
        business = []
    # A board member's approval no longer selects a stage of its own: the board
    # does not approve in GitHub under the current process, so an approval from
    # one is a reviewer looking, and the PR is still waiting on its business
    # owner. Falling through to "ready" says exactly that.
    if business:                                 # a business owner sponsored it
        stage = "intake"
    elif ready_signal:                           # checks green, not draft, no approvals
        # Refine by review history. Requested changes rank above a dismissed
        # approval: it is an explicit block, so it is the more urgent thing to
        # say if somehow both are true.
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
    """Parse a SKILL.md's YAML frontmatter, or {} if it is absent or malformed.

    Malformed frontmatter is validate_skill.py's to report as an error; here it
    just means the card shows no summary rather than failing the whole run.
    """
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
    # encoding is explicit because text=True alone decodes with the locale codec.
    # GitHub's JSON carries emoji and non-Latin characters in titles, bodies and
    # commit messages, which cp1252 cannot decode — so this raised
    # UnicodeDecodeError on any Windows run while working fine on the Linux
    # runner. That difference made the script effectively CI-only.
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
    """The first sentence of a description, for a card that must stay skimmable.

    A skill's `description` is written as a TRIGGER, not a summary -- it carries
    the phrases a user might type, often as a long quoted list. The opening
    clause is usually the only part that reads as prose, so that is what the
    card shows. Truncation is by sentence first and length second, because
    cutting mid-word looks like a bug rather than a choice.
    """
    # Collapses the newlines YAML block folding leaves in a long description.
    s = " ".join(str(text or "").split())
    if not s:
        return ""
    # A sentence ends at ". " only when the next word starts a new sentence --
    # i.e. begins with a capital or a quote. Descriptions are full of
    # abbreviations ("e.g. pip install", "i.e. the owning team") whose period is
    # also followed by a space, and an earlier version of this split on those.
    # Requiring the token before the period to hold no internal period rules out
    # "e.g."/"i.e." directly; requiring the next character to be upper-case or a
    # quote rules out the rest, since an abbreviation continues mid-sentence.
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
    """Describe each changed skill well enough for a reviewer to triage it.

    `statuses` maps path -> the PR file status ("added"/"modified"/...), so the
    card can say whether this is a new skill or an edit. `read_skill(path)`
    returns that path's SKILL.md text, or None.

    Deliberately does not carry allowed-tools. It is the sharpest risk signal a
    skill has, but the card is triage and a reviewer judging tool access opens
    the file regardless -- see the note in teams_card.card().

    Both are injected rather than fetched here, for two reasons. build_record is
    documented as pure aggregation with no network, and -- more importantly --
    this runs under `workflow_run`, which checks out MAIN's code, not the PR's.
    Reading SKILL.md off disk would describe the wrong version, and for a NEW
    skill there is nothing on disk at all. The caller fetches from the PR's head.
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
                entry["surfaces"] = listed_text(meta.get("surfaces"), "")
            out.append(entry)
    return out


def build_record(repo, pr, files, reviews, check_runs, approved_by="",
                 board_members=frozenset(), domain_members=frozenset(), domain_teams=(),
                 read_skill=None):
    """Pure aggregation — no network. Given raw API objects, return the record.

    `read_skill(path) -> text | None` is the one injected side effect, and it
    stays injected so this remains testable without a repo or a network: see
    changed_skills for why the content cannot simply be read off disk.
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
    # A dismissed review means someone approved and the approval was invalidated.
    # With dismiss_stale_reviews_on_push on, that is a push by the author.
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

    # The "ready for review" signal is DERIVED, not passed in: every tracked
    # check is green and the author has left draft. Deriving it means the card
    # cannot claim a PR is ready while CI is red — which a trigger-based signal
    # could, since a PR opened non-draft fires before any check has run. Draft
    # is what the author controls; green is what decides the timing.
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
        # Named for what it waits on - the business owner's approval, which the
        # process does ask for in GitHub - rather than for a count.
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
    # "Approved by", not "reviewed by": these are people whose latest review
    # state is APPROVED. The claim this file must avoid is that their approval
    # gates the merge, not the plain fact that they approved.
    #
    # Every approver on one line, the business owner annotated in place rather
    # than given a line of their own -- which printed that person twice, three
    # lines apart, and read as two separate facts.
    bo = str(gov.get("business_owner") or "")
    approved = ", ".join(
        f"@{a} (business owner)" if a.lower() == bo.lower() else "@" + a
        for a in ap["approvers"])
    L += ["**Approved by:** " + (approved or "_nobody yet_"), ""]
    # Its own line, so it cannot disappear with the one above it. A requested
    # change is the most consequential thing on this summary.
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
    # Read SKILL.md from the PR's head, never from the checkout: under
    # workflow_run the checkout is MAIN, so a new skill is absent and a changed
    # one is stale. Failures are swallowed -- a missing summary costs a line on
    # a card, and is not worth failing a governance run over.
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
