#!/usr/bin/env python3
"""Turn a governance-results.json into a Microsoft Teams Adaptive Card.

Print the card envelope (for piping), or POST it straight to a Teams
(Workflows) webhook — the latter avoids shell file-encoding pitfalls.

Usage:
  python scripts/teams_card.py [governance-results.json]            # print card JSON
  python scripts/teams_card.py [results.json] --post               # POST to $TEAMS_WEBHOOK
  python scripts/teams_card.py [results.json] --post --webhook URL  # POST to URL
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

from utils import (ROOT_FOUNDATION, ROOT_GROUPS, ROOT_SHARED, classify_path,
                   listed_text, skill_metadata)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
# stderr too, not just stdout: the error messages contain an em dash, and a
# caller reading this process's stderr as UTF-8 (which the preview scripts do)
# hit UnicodeDecodeError on the cp1252 bytes — so the real error was replaced by
# a decode failure, and the preview reported "failed: None".
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# overall status -> Adaptive Card container style (colored header bar)
STYLE = {
    "ready": "good",
    "awaiting_approval": "warning",
    "checks_pending": "warning",
    "changes_requested": "attention",
    "checks_failing": "attention",
    "draft": "accent",
}

# How many skills get a full block before the card summarizes the rest.
SKILL_BLOCK_CAP = 3

# governance card_stage -> (header text, container style, PR button label, subtitle)
#
# Headers may use {Subject}/{subject}, filled from whether the PR actually
# changes any skills. A tooling-only PR — scripts, workflows, docs — still gets
# a card, but calling it a "Skill" is wrong, and it
# also tells governance the wrong thing: their skill-risk lens does not apply to
# a PR that ships no skill.
STAGE = {
    # The only stage with no approval behind it — every other stage needs one to
    # exist, which is why the business owner was never told a PR was waiting.
    # Reached when all checks are green and the PR is not a draft, so the
    # headline is a statement of verified state, not of intent.
    "ready": ("🔵 {Subject} ready for review — all checks green", "accent", "Review PR",
              "Checks have passed and this is not a draft. Business owner reviews "
              "fit; governance reviews risk and conflicts."),
    # Both of these are reached on the same gate as "ready" (green, not a draft,
    # no current approval) but with review history behind them. Saying "ready for
    # review" to somebody who already reviewed hides the only thing they need to
    # know, which is what changed since they looked.
    # Worded to be true whenever the stage is reached, not only after a push.
    # The stage is computed on every run, including the moment a reviewer submits
    # "changes requested" while the previous commit's checks are still green —
    # so a subtitle claiming "new commits are in" would be false there. The
    # workflow does not post on that event today, but nothing stops it, and a
    # card that asserts a push that never happened is worse than a vague one.
    "ready_changes": ("🟠 Changes requested — checks green", "warning", "Re-review PR",
                      "A reviewer requested changes and every check passes. "
                      "Please take another look."),
    "ready_reapprove": ("🟠 Re-approval needed — checks green", "warning", "Re-approve PR",
                        "An earlier approval was dismissed, so this needs approving "
                        "again. Every check passes."),
    # No "approved" card. The sign-off is an OneTrust decision plus the IT
    # owner's attestation, and neither reaches GitHub -- so there is no state
    # this file can read that would make the claim true. The last card in the
    # sequence is therefore an intake, not a conclusion.
    "intake": ("🟡 New {subject} — business owner approved", "warning", "Review PR",
               "The business owner has approved. IT owner: take this to the AI "
               "Feature Enablement form in OneTrust."),
}
ICON = {"success": "✅", "failure": "❌", "timed_out": "❌", "cancelled": "❌",
        "action_required": "❌", "neutral": "➖", "skipped": "⏭️",
        "queued": "⏳", "in_progress": "⏳", None: "⏳"}


def card(rec):
    p, ap = rec["pr"], rec["approvals"]
    gov = rec.get("governance") or {}
    stage = gov.get("card_stage", "")
    groups = f" ({', '.join(rec['groups'])})" if rec.get("groups") else ""
    # Names, no count and no tick: there is no threshold to be at, and a tick
    # beside an approval that does not gate the merge is the specific thing
    # this card must not imply. "Approved by", not "reviewed by" -- these are
    # people whose latest review state is APPROVED, and the softer word
    # understates it. What the card must not claim is that their approval IS
    # the governance approval; saying they approved is just true.
    #
    # ONE row, every approver, each name once, the business owner annotated in
    # place. Two rows were tried both ways and each failed: a full roll call
    # beside a "Business owner" row printed that person twice, and filtering
    # them out of the roll call hid the answer whenever the owner was the only
    # approver. Annotating inline keeps "who approved this?" answerable from a
    # single line and still says which of them is the business owner.
    bo = str(gov.get("business_owner") or "")
    bo_note = ", ".join(["business owner"] + list(gov.get("business_owner_teams") or []))
    approvals = ", ".join(f"{a} ({bo_note})" if a.lower() == bo.lower() else a
                          for a in ap["approvers"]) or "nobody yet"
    checks = "\n".join(f"{ICON.get(c['conclusion'], '?')} {c['check']} — {c['conclusion'] or 'pending'}"
                       for c in rec["checks"]) or "_(no checks reported yet)_"

    # "Skill" only when the PR actually touches one; otherwise "PR". Both cases
    # carry their own lowercase form rather than calling .lower(): "PR" is an
    # initialism, so mid-sentence it stays "PR", not "pr".
    subject, subject_lc = ("Skill", "skill") if rec.get("changed_skills") else ("PR", "PR")
    # Stage-aware header/style/CTA; fall back to the generic overall-status card.
    header, style, cta, subtitle = STAGE.get(
        stage, (f"Governance — {rec['overall']}", STYLE.get(rec["overall"], "emphasis"), "Open PR", ""))
    # format_map, not format: a header may legitimately contain other braces, and
    # only these two keys should be substituted. A header without a placeholder
    # passes through untouched.
    header = header.format_map({"Subject": subject, "subject": subject_lc})

    facts = [
        {"title": "Author", "value": p["author"]},
        {"title": "Tier", "value": rec["tier"] + groups},
    ]
    # No separate "Business owner" row: the name is annotated inside
    # "Approved by" instead, so nobody is listed twice.
    # No "Governance" row. A board member's approval is not a governance
    # sign-off under the current process -- that is an OneTrust decision plus
    # the IT owner's attestation -- so labelling it one overstates what
    # happened. It shows up below as an approval, which is what it is.
    facts += [
        {"title": "Approved by", "value": approvals},
        {"title": "Overall", "value": rec["overall"]},
    ]

    body = [
        {"type": "Container", "style": style, "bleed": True,
         "items": [{"type": "TextBlock", "text": header,
                    "weight": "Bolder", "size": "Medium", "wrap": True}]},
    ]
    if subtitle:
        body.append({"type": "TextBlock", "text": subtitle, "wrap": True, "isSubtle": True})
    body += [
        {"type": "TextBlock", "wrap": True,
         "text": f"**[PR #{p['number']} — {p['title']}]({p['url']})**"},
        {"type": "FactSet", "facts": facts},
        {"type": "TextBlock", "text": "**Checks**", "wrap": True, "spacing": "Medium"},
        {"type": "TextBlock", "text": checks, "wrap": True},
    ]
    # One block per changed skill.
    changed = rec.get("changed_skills", [])
    if changed:
        lines = []
        # Capped: a card is a prompt to go and look, not the review itself. Two
        # entries already made it noticeably taller, and past about three the
        # detail stops being skimmable — which costs more than it adds, since
        # anything beyond triage means opening the PR anyway. The remainder is
        # counted, never silently dropped: a truncated list that looks complete
        # is worse than a short one that says it is short.
        shown, rest = changed[:SKILL_BLOCK_CAP], changed[SKILL_BLOCK_CAP:]
        for s in shown:
            name = s.get("name") or s["path"].rsplit("/", 1)[-1]
            where = s.get("group") or ("all staff" if s.get("tier") == "foundation-wide" else "")
            # "new" is worth saying; "modified" is the default and saying so on
            # every card is noise.
            what = "new" if s.get("status") == "added" else ""
            tag = ", ".join(x for x in (where, what) if x)
            lines.append(f"**{name}**" + (f"  _({tag})_" if tag else ""))
            if s.get("summary"):
                lines.append(s["summary"])
            facts = []
            if s.get("surfaces"):
                facts.append(f"Runs on: {s['surfaces']}")
            if facts:
                lines.append("_" + "  ·  ".join(facts) + "_")
            lines.append("")
        if rest:
            names = [s.get("name") or s["path"].rsplit("/", 1)[-1] for s in rest]
            # Named up to a point, then elided. An earlier version nested a
            # second "and N others" inside this line and produced "and 4 more:
            # a, b, c, and 1 others" — two conjunctions and a plural disagreeing
            # with its count. One list, one ellipsis.
            listed = ", ".join(names[:5]) + (", …" if len(names) > 5 else "")
            lines.append(f"_and {len(rest)} more: {listed}_")
        # separator draws a rule above the block, so the skill detail reads as
        # its own section rather than as a continuation of the check list. The
        # spacing goes with it: "Small" under a rule looks like a mistake.
        body.append({"type": "TextBlock", "text": "\n".join(lines).rstrip(),
                     "wrap": True, "spacing": "Medium", "separator": True})
    else:
        # Said out loud rather than left blank. An empty space below the checks
        # reads the same whether the PR genuinely touches no skill or the lookup
        # failed, and those need different reactions from a reviewer. It also
        # tells governance the blast radius up front: nothing here reaches staff.
        body.append({"type": "TextBlock",
                     "text": "_No skills changed — tooling, workflows or docs only._",
                     "wrap": True, "isSubtle": True,
                     "spacing": "Medium", "separator": True})

    return {
        "type": "message",
        "attachments": [{
            "contentType": "application/vnd.microsoft.card.adaptive",
            "content": {
                "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                "type": "AdaptiveCard",
                "version": "1.4",
                "body": body,
                "actions": [{"type": "Action.OpenUrl", "title": cta, "url": p["url"]}],
            },
        }],
    }


def post(card_obj, url):
    data = json.dumps(card_obj).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/json",
                 "User-Agent": "gf-agent-skills-governance"})
    try:
        # `url` is the TEAMS_WEBHOOK secret or an explicit --webhook argument:
        # operator-supplied, never derived from PR or skill content, so the
        # destination is not attacker-controlled and SSRF is not reachable here.
        #
        # Both ID forms are listed because Semgrep's SARIF reports this rule
        # with its last path segment doubled, and there is no way to verify
        # which form `nosemgrep` matches without running it (no native Windows
        # build; Docker Desktop blocked by WSL group policy). An ID that does
        # not match is ignored, so listing both is harmless.
        # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected, python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected
        with urllib.request.urlopen(req) as r:
            print(f"HTTP {r.status}")
            text = r.read().decode("utf-8", "replace").strip()
            if text:
                print(text[:1000])
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace").strip()
        print(f"HTTP {e.code} {e.reason}", file=sys.stderr)
        if body:
            print(body[:1000], file=sys.stderr)
        raise SystemExit(1)


def _frontmatter(text):
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}
    import yaml  # lazy: only the infosec card needs it
    try:
        return yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        return {}


def skill_card(fm, skill_path, link=None, security="", pr_url=None, headline="Skill published"):
    """Plain-language card for Infosec/Legal, built from a skill's frontmatter."""
    name = fm.get("name") or "(unnamed skill)"
    desc = fm.get("description") or "_(no description)_"
    owner = skill_metadata(fm).get("owner") or fm.get("owner") or "—"
    tools = listed_text(fm.get("allowed-tools"), "not declared (inherits all tools)")
    reviewed = fm.get("last_reviewed") or "—"
    classified = classify_path(skill_path)
    if classified is None:
        audience = "—"
    else:
        root, below = classified
        if root == ROOT_FOUNDATION:
            audience = "All staff (foundation-wide)"
        elif root == ROOT_GROUPS:
            audience = f"Group: {below[0]}"
        elif root == ROOT_SHARED:
            audience = "Shared across teams"
        else:
            audience = "—"
    sec = security or "passed required checks before merge"
    style = "attention" if ("find" in sec.lower() or "fail" in sec.lower()) else "good"
    body = [
        {"type": "Container", "style": style, "bleed": True,
         "items": [{"type": "TextBlock", "text": headline,
                    "weight": "Bolder", "size": "Medium", "wrap": True}]},
        {"type": "TextBlock", "text": f"**Skill name:** {name}", "wrap": True},
        {"type": "TextBlock", "text": f"**Description:** {desc}", "wrap": True},
        {"type": "FactSet", "facts": [
            {"title": "Audience", "value": audience},
            {"title": "Owner", "value": owner},
            {"title": "Tools it can use", "value": str(tools)},
            {"title": "Last reviewed", "value": str(reviewed)},
            {"title": "Security scan", "value": sec},
        ]},
    ]
    actions = []
    if pr_url:
        actions.append({"type": "Action.OpenUrl", "title": "View PR", "url": pr_url})
    if link:
        actions.append({"type": "Action.OpenUrl", "title": "View skill", "url": link})
    content = {"$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
               "type": "AdaptiveCard", "version": "1.4", "body": body}
    if actions:
        content["actions"] = actions
    return {"type": "message",
            "attachments": [{"contentType": "application/vnd.microsoft.card.adaptive",
                             "content": content}]}


def skill_card_from_file(md_path, repo="", ref="", security="", pr_url="", headline="Skill published"):
    rel = str(md_path).replace("\\", "/")
    fm = _frontmatter(Path(md_path).read_text(encoding="utf-8", errors="replace"))
    link = f"https://github.com/{repo}/blob/{ref}/{rel}" if repo and ref else None
    return skill_card(fm, rel, link, security, pr_url, headline)


BRIEF_HEADINGS = (
    ("what",        "What this PR does"),
    ("reads",       "Reads from"),
    ("writes",      "Writes / sends to"),
    ("tools",       "Tools requested"),
    ("closer_look", "Worth a closer look"),
)

BRIEF_FOOTER = ("_AI-generated, advisory only — the authoritative result is the "
                "`Scan skills for security red flags` check._")


def brief_card(pr, brief, partial=False):
    """Adaptive Card carrying a reviewer brief for an open PR.

    Fixed headings in a fixed order: reviewers scan the same shape every time,
    and a renderer driven by a constant list cannot grow a section because the
    model emitted one.
    """
    body = [
        {"type": "Container", "style": "accent", "bleed": True,
         "items": [{"type": "TextBlock", "text": "Reviewer brief",
                    "weight": "Bolder", "size": "Large", "wrap": True}]},
        {"type": "TextBlock", "wrap": True,
         "text": f"**[PR #{pr['number']} — {pr['title']}]({pr['url']})**"},
    ]
    if partial:
        body.append({"type": "TextBlock", "wrap": True, "color": "Attention",
                     "text": "⚠ **Partial** — the diff was too large to send in "
                             "full. Every changed file is listed, but some hunks "
                             "were not read."})
    for key, heading in BRIEF_HEADINGS:
        body.append({"type": "TextBlock", "text": f"**{heading}**", "wrap": True,
                     "spacing": "Medium"})
        body.append({"type": "TextBlock", "text": brief.get(key) or "_(nothing reported)_",
                     "wrap": True})
    body.append({"type": "TextBlock", "text": BRIEF_FOOTER, "wrap": True,
                 "isSubtle": True, "spacing": "Medium"})

    return {"type": "message", "attachments": [{
        "contentType": "application/vnd.microsoft.card.adaptive",
        "content": {"$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "type": "AdaptiveCard", "version": "1.4", "body": body,
                    "actions": [{"type": "Action.OpenUrl", "title": "Review PR",
                                 "url": pr["url"]}]},
    }]}


# Plugin components that are not skills: hooks, subagents, slash commands, MCP
# connector configs, plugin manifests. They carry no SKILL.md frontmatter, so
# skill_card() has nothing to describe them with -- but they ship to staff in the
# same plugin and can act on their behalf, so Infosec should still see them.


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", nargs="?", default="governance-results.json",
                    help="governance-results.json (governance) or a SKILL.md path (infosec)")
    ap.add_argument("--audience", choices=["governance", "infosec"],
                    default="governance",
                    help="governance = PR checks card; infosec = plain-language "
                         "skill summary")
    ap.add_argument("--security", default="", help="(infosec) security-scan result text")
    ap.add_argument("--repo", default=os.environ.get("REPO", ""), help="(infosec) owner/name for the skill link")
    ap.add_argument("--ref", default=os.environ.get("REF", ""), help="(infosec) git ref for the skill link")
    ap.add_argument("--pr-url", default="", help="(infosec) link to the PR that changed the skill")
    ap.add_argument("--headline", default="Skill published", help="(infosec) card headline")
    ap.add_argument("--post", action="store_true", help="POST the card to the webhook")
    ap.add_argument("--card-only", action="store_true",
                    help="print just the AdaptiveCard, not the Teams message envelope "
                         "— this is what https://adaptivecards.io/designer expects")
    ap.add_argument("--out", metavar="PATH",
                    help="write the JSON here as UTF-8 instead of stdout. Prefer this on "
                         "Windows: PowerShell 5.1 decodes native output using the OEM "
                         "codepage, turning '—' into 'ΓÇö', and Out-File/Set-Content add "
                         "a BOM that strict JSON parsers reject")
    ap.add_argument("--webhook", default=os.environ.get("TEAMS_WEBHOOK", ""),
                    help="Teams webhook URL (default: $TEAMS_WEBHOOK)")
    args = ap.parse_args()

    if args.audience == "infosec":
        c = skill_card_from_file(args.input, args.repo, args.ref, args.security, args.pr_url, args.headline)
    else:
        rec = json.loads(Path(args.input).read_bytes())  # tolerant of UTF-8/16/32

        # Only a real milestone warrants a Teams post: the PR became reviewable,
        # or its business owner approved. "none" -- a draft, red or unreported
        # checks, or an approval from somebody holding no role -- is skipped to
        # keep the channel uncluttered. Records with no governance block
        # (older/other producers) keep posting as before.
        stage = (rec.get("governance") or {}).get("card_stage")
        if args.post and stage == "none":
            print("card_stage=none — nothing to post (not a review milestone)")
            return 0

        c = card(rec)
    if args.post:
        if not args.webhook:
            print("ERROR: no webhook — set $TEAMS_WEBHOOK or pass --webhook URL", file=sys.stderr)
            return 1
        post(c, args.webhook)
    else:
        # The webhook needs the {"type":"message","attachments":[...]} envelope;
        # the Adaptive Cards designer needs only the card inside it, and rejects
        # the envelope with "Invalid or missing card type".
        out = c["attachments"][0]["content"] if args.card_only else c
        text = json.dumps(out, indent=2, ensure_ascii=False)
        if args.out:
            Path(args.out).write_text(text + "\n", encoding="utf-8")
            print(f"Wrote {args.out}")
        else:
            sys.stdout.write(text + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
