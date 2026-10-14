#!/usr/bin/env python3
"""askops_request.py — assemble the AI-AskOps skill/plugin request email.

Six fields go to AI-AskOps. Three come from the author, via the ASKOPS block
in the PR description. Three are derived from the repository, because deriving
them is more reliable than asking:

  name & link        SKILL.md frontmatter + the PR URL
  requesting team    PR author + the owner declared in the skill
  systems and data   the skill's declared tools, plus what the PR Brief found
                     the skill reads and writes

That last field is the reason to derive rather than ask. An author describes a
skill's purpose; the code describes its egress. On one PR this week, five
skills declared `Bash, Read, Write, Glob` while their instructions fetched
currency rates from four external sites — something a hand-filled form would
plausibly have omitted with no intent to hide anything.

Usage:
  python scripts/askops_request.py --pr-body body.md --pr-number 61 \\
      --pr-url https://... --pr-author sharonch --changed-files files.txt \\
      [--brief brief-card.json] [--output request.json] [--post]

Exits 1 with a clear message when the ASKOPS block is missing or any answer is
blank, so a skill PR cannot quietly email governance an empty form.
"""

import argparse
import html
import json
import os
import re
import sys
from pathlib import Path

BLOCK = re.compile(r"<!--\s*ASKOPS:START\s*-->(.*?)<!--\s*ASKOPS:END\s*-->", re.S)
COMMENT = re.compile(r"<!--.*?-->", re.S)
SKILL_PATH = re.compile(r"(plugins/.+?/skills/[^/]+)/")

# (key, the leading words of the question as written in the template)
# Required of the author. Business justification is NOT here: it is taken
# from the brief's "What this PR does", and the author may override it by
# answering the optional question in the template.
QUESTIONS = (
    ("users", "Intended users"),
    ("urgency", "Target date"),
)
OPTIONAL = (("justification", "Business justification"),)

# Where a skill lives decides who it reaches — the same tiers the validator uses.
TIER_AUDIENCE = {
    "plugins/foundation-wide/": "All Foundation staff",
    "plugins/shared-workflows/": "Several teams (no single owning team)",
}

NEWLINE = "\n"


def _heading(line):
    """(question key, any answer written on the same line), or (None, "").

    Formatting is stripped before matching, so a heading counts whether it
    is bold, a markdown heading, a list item, or plain text -- requiring
    bold made formatting load-bearing, and formatting is what gets lost when
    someone edits a description in a plain text box.

    The inline answer matters because writing it on the heading line is a
    natural way to fill a form:

        **Intended users - which roles?**: about 20
        Target date: Oct 1

    An earlier version recognised the heading and then collected the lines
    BELOW it, so an inline answer was silently dropped and the run reported
    the field empty while the answer sat on screen. Seen on PR #65.
    """
    core = line.strip().lstrip('#*->').strip()
    low = core.lower()
    for key, prefix in QUESTIONS + OPTIONAL:
        if not low.startswith(prefix.lower()):
            continue
        # Everything after the closing ** is the inline answer; without bold,
        # everything after the first colon that follows the question text.
        if '**' in core:
            tail = core.rsplit('**', 1)[1]
        else:
            after = core[len(prefix):]
            tail = after.split(':', 1)[1] if ':' in after else ''
        return key, tail.lstrip(':').strip()
    return None, ""


def parse_block(body):
    """Return the author answers from the PR description.

    Raises ValueError if the block is absent or a required answer is empty.
    The failure has to be loud: a blank form still looks like an answer to
    whoever reads the email.
    """
    match = BLOCK.search(body or "")
    if not match:
        raise ValueError(
            "no ASKOPS block in the PR description — keep the "
            "ASKOPS:START / ASKOPS:END markers from the PR template and "
            "answer the questions between them (intended users and target date "
            "are required)"
        )

    # Walk the block line by line: a recognised heading opens a field, and
    # everything until the next heading is its answer.
    answers = {}
    current = None
    collected = {}
    for line in COMMENT.sub('', match.group(1)).splitlines():
        key, inline = _heading(line)
        if key:
            current = key
            collected.setdefault(current, [])
            if inline:
                collected[current].append(inline)
        elif current:
            collected[current].append(line)
    for key, chunk in collected.items():
        answers[key] = ' '.join(s.strip() for s in chunk if s.strip()).strip()

    empty = [prefix for key, prefix in QUESTIONS if not answers.get(key)]
    if empty:
        raise ValueError("these answers are empty: " + "; ".join(empty))
    return answers


def changed_skills(paths):
    """Skill directories touched by this PR, in order, no duplicates."""
    found = []
    for raw in paths:
        match = SKILL_PATH.match(raw.replace("\\", "/"))
        if match and match.group(1) not in found:
            found.append(match.group(1))
    return found


def _plugin_entries(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {e.get("name"): e for e in data.get("plugins", []) if e.get("name")}


def newly_registered(base_marketplace, head_marketplace):
    """Plugins this PR adds to the registry -- the ones it publishes.

    Registration is the act of publication, so a PR that only adds a
    marketplace entry is the one that puts a skill in front of staff, and it
    carries no SKILL.md for changed_skills() to find.

    Both versions are needed. Under pull_request_target the checkout is the
    BASE commit, so the registry on disk is the one without the new entry;
    comparing it against the head version is what isolates what this PR adds.
    Without the comparison the request names every registered plugin, which
    tells governance nothing about the change in front of them.
    """
    if not head_marketplace or not Path(head_marketplace).is_file():
        return []
    base = _plugin_entries(base_marketplace)
    head = _plugin_entries(head_marketplace)
    return [head[name] for name in head if name not in base]


def frontmatter(skill_md):
    """SKILL.md frontmatter, or {} if it cannot be read."""
    import yaml
    try:
        return yaml.safe_load(skill_md.read_text(encoding="utf-8").split("---", 2)[1]) or {}
    except (OSError, IndexError, yaml.YAMLError):
        return {}


def listed_text(value, absent):
    """A list-or-string frontmatter value as display text.

    Mirrors utils.listed_text; this script stays standalone. `allowed-tools`
    and `surfaces` may be YAML lists, and str() of one reads "['Read', 'Grep']".
    """
    if isinstance(value, list):
        value = ", ".join(str(v).strip() for v in value if str(v).strip())
    return " ".join(str(value or "").split()) or absent


def audience_for(skill_dir):
    for prefix, label in TIER_AUDIENCE.items():
        if skill_dir.startswith(prefix):
            return label
    match = re.match(r"plugins/groups/([^/]+)/", skill_dir)
    return f"The {match.group(1)} group" if match else "not determined from the path"


def plugin_name_for(skill_dir, head_root=None):
    """The plugin that owns a skill directory.

    Governance enables plugins, not skills -- `authoring` is what someone
    assigns in the admin console, while `skill-authoring` is what the model
    loads. The request needs both. Prefers the head copy for the same reason
    SKILL.md does: a new plugin's manifest is not on the base commit.
    """
    manifest = Path(skill_dir).parents[1] / ".claude-plugin" / "plugin.json"
    if head_root:
        head_manifest = Path(head_root) / manifest
        if head_manifest.is_file():
            manifest = head_manifest
    try:
        return json.loads(manifest.read_text(encoding="utf-8")).get("name") or ""
    except (OSError, json.JSONDecodeError):
        return ""


def describe_skills(skill_dirs, head_root=None):
    """Describe each skill from its frontmatter.

    Prefers the head copy when one was fetched. Under pull_request_target the
    checkout is the BASE commit, so a PR that ADDS a skill has no SKILL.md on
    disk -- reading only the checkout made every derived field "not declared"
    for exactly the common case. head_root holds the PR's own copies, fetched
    as data.
    """
    entries = []
    for skill_dir in skill_dirs:
        md = Path(skill_dir) / "SKILL.md"
        if head_root:
            head_md = Path(head_root) / skill_dir / "SKILL.md"
            if head_md.is_file():
                md = head_md
        fm = frontmatter(md)
        meta = fm.get("metadata") or {}
        entries.append({
            "name": fm.get("name") or Path(skill_dir).name,
            "path": skill_dir,
            "tools": listed_text(fm.get("allowed-tools"), "not declared (inherits all tools)"),
            "surfaces": listed_text(meta.get("surfaces"), "not declared"),
            "owner": str(meta.get("owner") or "not declared"),
            "audience": audience_for(skill_dir),
            "plugin": plugin_name_for(skill_dir, head_root),
        })
    return entries


def brief_fields(path):
    """The brief's five fields, from either shape it is stored in.

    pr_brief.py --output writes the Teams card ENVELOPE, not a flat dict, so a
    naive brief.get("reads") finds nothing and the field vanishes silently --
    no reads line and no "not analysed" note either. Accept both: a flat
    mapping, or the card, whose body alternates a bold heading TextBlock and
    its value.
    """
    if not path or not Path(path).is_file():
        return {}
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}

    if isinstance(data, dict) and ("reads" in data or "writes" in data):
        return data

    try:
        body = data["attachments"][0]["content"]["body"]
    except (KeyError, IndexError, TypeError):
        return {}

    wanted = {"**What this PR does**": "what",
              "**Reads from**": "reads",
              "**Writes / sends to**": "writes"}
    fields = {}
    for i, block in enumerate(body):
        key = wanted.get(str(block.get("text", "")).strip())
        if key and i + 1 < len(body):
            fields[key] = str(body[i + 1].get("text", "")).strip()
    return fields


def data_access_lines(entries, brief):
    """The 'systems/data it will access' field.

    Declared tools come from frontmatter. Reads and writes come from the PR
    Brief, which derived them from the diff — reused rather than guessed a
    second time. With no brief available, say so rather than imply the skill
    touches nothing.
    """
    lines = [f"- {e['name']}: declared tools {e['tools']} (runs on: {e['surfaces']})"
             for e in entries]
    if brief.get("reads"):
        lines.append(f"- Reads: {brief['reads']}")
    if brief.get("writes"):
        lines.append(f"- Writes or sends to: {brief['writes']}")
    if not brief:
        lines.append("- Reads/writes: not analysed (no PR Brief available for this PR)")
    return lines


def build(args):
    answers = parse_block(Path(args.pr_body).read_text(encoding="utf-8", errors="replace"))

    paths = [line.strip() for line
             in Path(args.changed_files).read_text(encoding="utf-8").splitlines()
             if line.strip()]
    skill_dirs = changed_skills(paths)
    published = newly_registered(".claude-plugin/marketplace.json", args.marketplace_head)

    # A registration-only PR changes no SKILL.md, so fall back to the skills
    # listed under each registered plugin's source directory. Publication is
    # exactly when governance wants to hear about a skill, so this must not
    # come back empty just because the diff held no frontmatter.
    if not skill_dirs and published:
        for plugin in published:
            source = Path(plugin["source"].lstrip("./"))
            skill_dirs += [str(d.parent).replace("\\", "/")
                           for d in sorted((source / "skills").glob("*/SKILL.md"))]

    if not skill_dirs:
        raise ValueError(
            "this PR neither changes a skill nor publishes one — nothing to request"
        )

    entries = describe_skills(skill_dirs, args.head_root)

    brief = brief_fields(args.brief)

    # Two separate things, kept separate. The author's justification answers
    # WHY the skill is needed; the brief's description answers WHAT the change
    # does. Governance asked the first, and only a person can answer it -- but
    # the second is an independent read of the code, which is what caught a
    # skill declaring four tools while fetching from four external sites. So
    # the description ships on every request, labelled, whether or not the
    # author wrote anything.
    if answers.get('justification'):
        justification = answers['justification']
    elif brief.get('what'):
        justification = ('[not stated by the requester - see the derived description below]')
    else:
        justification = ('[not stated by the requester, and no brief available to derive it from]')

    described = brief.get('what') or 'not analysed (no brief available for this PR)'

    def _label(entry):
        plugin = entry.get("plugin")
        if plugin and plugin != entry["name"]:
            return entry["name"] + " (plugin: " + plugin + ")"
        return entry["name"]

    names = ", ".join(_label(e) for e in entries)
    owners = ", ".join(dict.fromkeys(e["owner"] for e in entries))
    audiences = ", ".join(e["audience"] for e in entries)
    access = NEWLINE.join(data_access_lines(entries, brief))

    body = (
        "Hi AI Ask Ops team,\n\n"
        "I'd like to request review and enablement of the following Skill/Plugin:\n\n"
        f"Skill/Plugin name & source/link: {names} — {args.pr_url}\n\n"
        f"Requesting team/individual: {args.pr_author} — {owners}\n\n"
        "Business justification (what problem does this solve?): "
        f"{justification}\n\n"
        "What the change does (derived from the diff): "
        f"{described}{NEWLINE}{NEWLINE}"
        f"Systems/data it will access:\n{access}\n\n"
        f"Intended users (who, how many): {audiences} — {answers['users']}\n\n"
        f"Target date/urgency: {answers['urgency']}\n\n"
        f"Thanks, {args.pr_author}\n\n"
        "--\n"
        "Name, link, team, audience scope, the description of the change and "
        "the systems and data it touches were derived from the PR's code and "
        "frontmatter. Intended users, target date, and any business "
        "justification above were written by the requester." + NEWLINE
        + "PR: " + args.pr_url + NEWLINE
    )

    # The Power Automate flow expects exactly {"content": "..."} and builds the
    # email itself, so the subject line rides at the top of the content. The
    # extra keys are kept for the artifact only -- they are stripped before the
    # POST, because the flow ignores anything else.
    subject = f"Skill/Plugin Request - {names}"

    return {
        "content": body,
        # Kept for the artifact only. The flow composes its own subject and
        # rendered ours as the first line of the body, so it is not sent --
        # but it is the string to pass if the contract grows a subject field.
        "_subject": subject,
        "_pr": {"number": args.pr_number, "url": args.pr_url, "author": args.pr_author},
        "_skills": entries,
    }


# A label is the run of text up to the first colon on a line. Bolding is done
# by rule rather than from a list of the labels, so adding a field to the email
# cannot leave it unbolded and nobody has to keep two lists in step. The
# 60-character ceiling is what stops a sentence with a mid-line colon from
# being treated as a label -- the longest real label is 48. Lowercase is
# allowed because skill names are kebab-case, and an unbolded skill name
# beside two bolded siblings in the same list reads as a bug.
LABEL = re.compile(r"^(- )?([A-Za-z][^:]{2,60}):")


def to_html(text):
    """Wrap the plain-text request in minimal HTML, labels in bold.

    The flow sends an HTML email, where a newline is whitespace -- the first
    real send arrived as one unbroken paragraph. Escaping happens before any
    markup is added, so a skill description containing < or & cannot inject
    into an email governance reads while forming a judgement.
    """
    out = []
    for line in html.escape(text).split(NEWLINE):
        match = LABEL.match(line)
        if match:
            bullet = match.group(1) or ""
            label = match.group(2)
            rest = line[match.end():]
            line = bullet + "<strong>" + label + ":</strong>" + rest
        out.append(line)
    body = ("<br>" + NEWLINE).join(out)
    return ('<div style="font-family:Segoe UI,Arial,sans-serif;font-size:14px;'
            'line-height:1.5">' + NEWLINE + body + NEWLINE + "</div>")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pr-body", required=True, help="File holding the PR description")
    ap.add_argument("--pr-number", required=True)
    ap.add_argument("--pr-url", required=True)
    ap.add_argument("--pr-author", required=True)
    ap.add_argument("--changed-files", required=True,
                    help="File with one changed path per line")
    ap.add_argument("--head-root", default=None,
                    help="Directory holding the PR head's copies of changed "
                         "SKILL.md files, mirroring their repo paths")
    ap.add_argument("--marketplace-head", default=None,
                    help="marketplace.json as it stands on the PR head, when the "
                         "PR touches the registry — used to isolate what it publishes")
    ap.add_argument("--brief", default=None,
                    help="brief-card.json from the PR Brief step, for reads/writes")
    ap.add_argument("--output", default=None, help="Write the payload JSON here")
    ap.add_argument("--plain", action="store_true",
                    help="POST the text as-is instead of wrapping it in HTML "
                         "(use if the flow sends a plain-text email)")
    ap.add_argument("--post", action="store_true", help="POST to $ASKOPS_WEBHOOK")
    args = ap.parse_args(argv)

    try:
        payload = build(args)
    except ValueError as e:
        print(f"AskOps request not sent: {e}", file=sys.stderr)
        return 1

    if args.output:
        Path(args.output).write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                                     encoding="utf-8", newline="\n")
    if args.post:
        import urllib.request
        # Only "content" goes on the wire: the flow reads that key and ignores
        # the rest. Both headers are required -- the flow rejects a request
        # without X-Flow-Secret, and without the explicit content type it does
        # not parse the body as JSON.
        wire = payload["content"] if args.plain else to_html(payload["content"])
        headers = {
            "Content-Type": "application/json",
            "X-Flow-Secret": os.environ["ASKOPS_FLOW_SECRET"],
            "User-Agent": "gf-agent-skills-askops",
        }
        request = urllib.request.Request(
            os.environ["ASKOPS_WEBHOOK"],
            data=json.dumps({"content": wire}).encode("utf-8"),
            method="POST",
            headers=headers,
        )
        # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected
        with urllib.request.urlopen(request) as response:
            print(f"HTTP {response.status}", file=sys.stderr)
        print("AskOps request posted", file=sys.stderr)
    else:
        print(payload["content"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
