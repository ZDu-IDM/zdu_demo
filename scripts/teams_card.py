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

from utils import (ROOT_FOUNDATION, ROOT_GROUPS, ROOT_SHARED, author_text,
                   classify_path, find_plugin_manifest, listed_text)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
# stderr too: the preview scripts read it as UTF-8.
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
# {Subject}/{subject} becomes "Skill" or "PR", depending on whether skills changed.
STAGE = {
    # Checks green, not a draft, no approval yet.
    "ready": ("🔵 {Subject} ready for review — all checks green", "accent", "Review PR",
              "Checks have passed and this is not a draft. Business owner reviews "
              "fit; governance reviews risk and conflicts."),
    # Same gate as "ready", but with review history. Worded so it stays true
    # even when no new commit has been pushed.
    "ready_changes": ("🟠 Changes requested — checks green", "warning", "Re-review PR",
                      "A reviewer requested changes and every check passes. "
                      "Please take another look."),
    "ready_reapprove": ("🟠 Re-approval needed — checks green", "warning", "Re-approve PR",
                        "An earlier approval was dismissed, so this needs approving "
                        "again. Every check passes."),
    # No "approved" card: the real sign-off happens in OneTrust, which GitHub
    # cannot see. The last card is an intake.
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
    # One "Approved by" row listing every approver once, with the business
    # owner annotated in place. No count or tick: these approvals do not gate
    # the merge.
    bo = str(gov.get("business_owner") or "")
    bo_note = ", ".join(["business owner"] + list(gov.get("business_owner_teams") or []))
    approvals = ", ".join(f"{a} ({bo_note})" if a.lower() == bo.lower() else a
                          for a in ap["approvers"]) or "nobody yet"
    checks = "\n".join(f"{ICON.get(c['conclusion'], '?')} {c['check']} — {c['conclusion'] or 'pending'}"
                       for c in rec["checks"]) or "_(no checks reported yet)_"

    # "PR" stays uppercase mid-sentence, so no .lower().
    subject, subject_lc = ("Skill", "skill") if rec.get("changed_skills") else ("PR", "PR")
    # Stage-aware header/style/CTA; fall back to the generic overall-status card.
    header, style, cta, subtitle = STAGE.get(
        stage, (f"Governance — {rec['overall']}", STYLE.get(rec["overall"], "emphasis"), "Open PR", ""))
    header = header.format_map({"Subject": subject, "subject": subject_lc})

    facts = [
        {"title": "Author", "value": p["author"]},
        {"title": "Tier", "value": rec["tier"] + groups},
    ]
    # No "Governance" row: a board member's GitHub approval is not the sign-off.
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
        # Capped to stay skimmable; the rest are counted, never dropped silently.
        shown, rest = changed[:SKILL_BLOCK_CAP], changed[SKILL_BLOCK_CAP:]
        for s in shown:
            name = s.get("name") or s["path"].rsplit("/", 1)[-1]
            where = s.get("group") or ("all staff" if s.get("tier") == "foundation-wide" else "")
            # "modified" is the default, so only "new" is shown.
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
            listed = ", ".join(names[:5]) + (", …" if len(names) > 5 else "")
            lines.append(f"_and {len(rest)} more: {listed}_")
        # A rule above sets the skill detail apart from the checks.
        body.append({"type": "TextBlock", "text": "\n".join(lines).rstrip(),
                     "wrap": True, "spacing": "Medium", "separator": True})
    else:
        # Stated, not left blank, so it can't be mistaken for a failed lookup.
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
        # `url` is operator-supplied (secret or --webhook), never from PR content,
        # so this is not SSRF. Both rule-ID forms are listed because Semgrep
        # reports the ID with its last segment doubled.
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


def skill_card(fm, skill_path, link=None, security="", pr_url=None, headline="Skill published",
               author=None):
    """Plain-language card for Infosec/Legal, built from a skill's frontmatter.

    `author` is the owning plugin's optional plugin.json `author`.
    """
    name = fm.get("name") or "(unnamed skill)"
    desc = fm.get("description") or "_(no description)_"
    owner = author_text(author)
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
    author = None
    manifest = find_plugin_manifest(Path(md_path).parent)
    if manifest:
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
            author = data.get("author") if isinstance(data, dict) else None
        except (OSError, json.JSONDecodeError):
            pass    # the card still sends; validate_skill.py reports the manifest
    return skill_card(fm, rel, link, security, pr_url, headline, author)


BRIEF_HEADINGS = (
    ("what",             "What this PR does"),
    ("reads",            "Reads from"),
    ("writes",           "Writes / sends to"),
    ("tools",            "Tools requested"),
    ("behaviour_change", "Behaviour change"),
    ("closer_look",      "Worth a closer look"),
)

BRIEF_FOOTER = ("_AI-generated, advisory only — the authoritative result is the "
                "`Scan skills for security red flags` check._")


def brief_card(pr, brief, partial=False):
    """Adaptive Card carrying a reviewer brief for an open PR.

    Headings come from BRIEF_HEADINGS, so the model cannot add a section.
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

        # Post only at a milestone; stage "none" is skipped.
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
        # The webhook wants the message envelope; the designer wants the bare card.
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
