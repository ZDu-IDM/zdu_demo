#!/usr/bin/env python3
"""askops_request.py — assemble the AI-AskOps skill/plugin request email.

The author answers users, urgency and (optionally) justification in the
ASKOPS block of the PR description. Name, link, owning team and systems/data
are derived from the repo and the PR Brief, which is more reliable than asking.

Usage:
  python scripts/askops_request.py --pr-body body.md --pr-number 61 \\
      --pr-url https://... --pr-author sharonch --changed-files files.txt \\
      [--brief brief-card.json] [--output request.json] [--post]

A missing ASKOPS block or a blank answer still sends, each shown as not provided.
"""

import argparse
import html
import json
import os
import re
import sys
from pathlib import Path, PurePosixPath

BLOCK = re.compile(r"<!--\s*ASKOPS:START\s*-->(.*?)<!--\s*ASKOPS:END\s*-->", re.S)
COMMENT = re.compile(r"<!--.*?-->", re.S)
SKILL_PATH = re.compile(r"(plugins/.+?/skills/[^/]+)/")

# (key, leading words of the template question).
QUESTIONS = (
    ("users", "Intended users"),
    ("urgency", "Target date"),
)
OPTIONAL = (("justification", "Business justification"),)

# Where a skill lives decides who it reaches.
TIER_AUDIENCE = {
    "plugins/foundation-wide/": "All Foundation staff",
    "plugins/shared-workflows/": "Several teams (no single owning team)",
}

NOT_PROVIDED = "_(answers not provided)_"
MANIFEST = PurePosixPath(".claude-plugin/plugin.json")

NEWLINE = "\n"


def present(path):
    """True for a non-empty file: a failed head fetch can leave a 0-byte one."""
    p = Path(path)
    return p.is_file() and p.stat().st_size > 0


def plugin_dir(rel, base_root, head_root=None):
    """The nearest ancestor of `rel` holding .claude-plugin/plugin.json on either side."""
    for d in rel.parents:
        if d == PurePosixPath("."):
            return None
        if (Path(base_root) / d / MANIFEST).is_file() or (
                head_root and (Path(head_root) / d / MANIFEST).is_file()):
            return d
    return None


def _heading(line):
    """(question key, any answer written on the same line), or (None, "").

    Matches bold, markdown-heading, list or plain-text headings alike, and
    keeps an answer written on the heading line ("Target date: Oct 1").
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
    """Return the author's answers; {} if the block is missing. Blanks are left to the caller."""
    match = BLOCK.search(body or "")
    if not match:
        return {}

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


def registry_changes(base_marketplace, head_marketplace):
    """(added, removed) registry entries: plugins this PR publishes and unpublishes.

    Compares base and head, since the checkout is the base commit under
    pull_request_target. Removed entries come from the base copy.
    """
    if not head_marketplace or not present(head_marketplace):
        return [], []
    base = _plugin_entries(base_marketplace)
    head = _plugin_entries(head_marketplace)
    return ([head[n] for n in head if n not in base],
            [base[n] for n in base if n not in head])


def frontmatter(skill_md):
    """SKILL.md frontmatter, or {} if it cannot be read."""
    import yaml
    try:
        return yaml.safe_load(skill_md.read_text(encoding="utf-8").split("---", 2)[1]) or {}
    except (OSError, IndexError, yaml.YAMLError):
        return {}


def listed_text(value, absent):
    """A comma-string or YAML-list value as display text. Mirrors utils (standalone script)."""
    if isinstance(value, list):
        value = ", ".join(str(v).strip() for v in value if str(v).strip())
    return " ".join(str(value or "").split()) or absent


def audience_for(skill_dir):
    for prefix, label in TIER_AUDIENCE.items():
        if skill_dir.startswith(prefix):
            return label
    match = re.match(r"plugins/groups/([^/]+)/", skill_dir)
    return f"The {match.group(1)} group" if match else "not determined from the path"


def manifest_for(skill_dir, head_root=None):
    """The owning plugin's plugin.json, or {}. Prefers the PR head's copy."""
    manifest = Path(skill_dir).parents[1] / ".claude-plugin" / "plugin.json"
    if head_root:
        head_manifest = Path(head_root) / manifest
        if present(head_manifest):
            manifest = head_manifest
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def author_text(author, absent):
    """plugin.json's optional `author` as "Name <email>", "Name", or `absent`. Mirrors utils."""
    if not isinstance(author, dict):
        return absent
    name = str(author.get("name") or "").strip()
    email = str(author.get("email") or "").strip()
    if name and email:
        return f"{name} <{email}>"
    return name or email or absent


def describe_skills(skill_dirs, head_root=None):
    """Describe each skill from its frontmatter.

    Prefers the PR head's copy (`head_root`): the checkout is the base
    commit, where a new skill does not exist yet.
    """
    entries = []
    for skill_dir in skill_dirs:
        md = Path(skill_dir) / "SKILL.md"
        if head_root:
            head_md = Path(head_root) / skill_dir / "SKILL.md"
            if present(head_md):
                md = head_md
        fm = frontmatter(md)
        meta = fm.get("metadata") or {}
        manifest = manifest_for(skill_dir, head_root)
        entries.append({
            "name": fm.get("name") or Path(skill_dir).name,
            "path": skill_dir,
            "tools": listed_text(fm.get("allowed-tools"), "not declared (inherits all tools)"),
            # Absent means Claude Code only, not "anywhere".
            "surfaces": listed_text(meta.get("surfaces"),
                                    "claude-code (not declared; the default)"),
            "owner": author_text(manifest.get("author"), "not declared"),
            "audience": audience_for(skill_dir),
            "plugin": manifest.get("name") or "",
        })
    return entries


def describe_plugin(plugin, head_root=None):
    """A plugin-level entry, for a request that changes a plugin with no skills."""
    manifest = manifest_for(plugin + "/skills/x", head_root)
    return {
        "name": manifest.get("name") or Path(plugin).name,
        "path": plugin,
        "tools": "no skills in this plugin",
        "surfaces": "not applicable",
        "owner": author_text(manifest.get("author"), "not declared"),
        "audience": audience_for(plugin + "/"),
        "plugin": manifest.get("name") or "",
        "plugin_only": True,
    }


def plugins_for_request(paths, published, removed, base_root, head_root):
    """Plugin dirs owning the changed paths, plus those added to or removed from the registry."""
    found = []
    for raw in paths:
        d = plugin_dir(PurePosixPath(raw.replace("\\", "/")), base_root, head_root)
        if d is not None and str(d) not in found:
            found.append(str(d))
    for entry in published + removed:
        source = str(entry.get("source") or "").replace("\\", "/")
        source = (source[2:] if source.startswith("./") else source).rstrip("/")
        if source and source not in found:
            found.append(source)
    return found


def brief_fields(path):
    """The brief's fields, from a flat dict or the Teams card pr_brief.py writes."""
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
              "**Writes / sends to**": "writes",
              "**Behaviour change**": "behaviour_change"}
    fields = {}
    for i, block in enumerate(body):
        key = wanted.get(str(block.get("text", "")).strip())
        if key and i + 1 < len(body):
            fields[key] = str(body[i + 1].get("text", "")).strip()
    return fields


def reasons_line(path) -> str:
    """The email's 'Why this request' value, from askops_gate's reasons file."""
    if not path or not Path(path).is_file():
        return "not recorded"
    lines = [ln.strip() for ln in Path(path).read_text(encoding="utf-8").splitlines()
             if ln.strip()]
    return "; ".join(lines) or "not recorded"


def data_access_lines(entries, brief):
    """The 'systems/data it will access' field: declared tools plus the brief's reads/writes."""
    def name(e):
        return e["name"] + (" (removed)" if e.get("removed") else "")

    lines = [f"- {name(e)}: no skills, plugin-level change" if e.get("plugin_only")
             else f"- {name(e)}: declared tools {e['tools']} (runs on: {e['surfaces']})"
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
    published, removed = registry_changes(".claude-plugin/marketplace.json",
                                          args.marketplace_head)

    # No SKILL.md changed: describe the skills of the plugins that did, from
    # the head copy if fetched, else the base checkout; a skill-less plugin
    # gets a plugin-level entry.
    plugin_only = []
    if not skill_dirs:
        for plugin in plugins_for_request(paths, published, removed, ".", args.head_root):
            names = {md.parent.name
                     for root in (args.head_root, ".") if root
                     for md in (Path(root) / plugin / "skills").glob("*/SKILL.md")}
            if names:
                skill_dirs += [f"{plugin}/skills/{n}" for n in sorted(names)]
            else:
                plugin_only.append(plugin)

    entries = describe_skills(skill_dirs, args.head_root)
    entries += [describe_plugin(p, args.head_root) for p in plugin_only]

    # A deleted skill or plugin is going, not being requested: mark it.
    deleted = set()
    if getattr(args, "removed_files", None) and Path(args.removed_files).is_file():
        deleted = {ln.strip() for ln in Path(args.removed_files)
                   .read_text(encoding="utf-8").splitlines() if ln.strip()}
    for e in entries:
        path = e.get("path") or ""
        plugin = path.split("/skills/")[0] if "/skills/" in path else path
        e["removed"] = bool(path) and (f"{path}/SKILL.md" in deleted
                                       or f"{plugin}/{MANIFEST}" in deleted)
    if not entries:
        # Nothing to name, but a request is due: send it rather than drop it.
        entries = [{"name": "not identified (no skill or plugin path changed)", "path": "",
                    "tools": "unknown", "surfaces": "unknown", "owner": "not declared",
                    "audience": "not determined from the path", "plugin": "",
                    "plugin_only": True}]

    brief = brief_fields(args.brief)

    # Justification (why, from the author) and description (what, from the
    # brief) are kept separate; the description is always included.
    if answers.get('justification'):
        justification = answers['justification']
    elif brief.get('what'):
        justification = ('[not stated by the requester - see the derived description below]')
    else:
        justification = ('[not stated by the requester, and no brief available to derive it from]')

    described = brief.get('what') or 'not analysed (no brief available for this PR)'

    def _label(entry):
        plugin = entry.get("plugin")
        notes = [f"plugin: {plugin}"] if plugin and plugin != entry["name"] else []
        notes += ["removed"] if entry.get("removed") else []
        return entry["name"] + (f" ({', '.join(notes)})" if notes else "")

    names = ", ".join(_label(e) for e in entries)
    owners = ", ".join(dict.fromkeys(e["owner"] for e in entries))
    # A removed skill's group is losing it, so it is no longer an intended user.
    kept = [e for e in entries if not e.get("removed")]
    audiences = (", ".join(dict.fromkeys(e["audience"] for e in kept))
                 or "none (everything listed is being removed)")
    access = NEWLINE.join(data_access_lines(entries, brief))

    body = (
        "Hi AI Ask Ops team,\n\n"
        "I'd like to request review and enablement of the following Skill/Plugin:\n\n"
        f"Skill/Plugin name & source/link: {names} — {args.pr_url}\n\n"
        f"Why this request: {reasons_line(args.reasons)}\n\n"
        f"Requesting team/individual: {args.pr_author} — {owners}\n\n"
        "Business justification (what problem does this solve?): "
        f"{justification}\n\n"
        "What the change does (derived from the diff): "
        f"{described}{NEWLINE}{NEWLINE}"
        f"Systems/data it will access:\n{access}\n\n"
        f"Intended users (who, how many): {audiences} — {answers.get('users') or NOT_PROVIDED}\n\n"
        f"Target date/urgency: {answers.get('urgency') or NOT_PROVIDED}\n\n"
        f"Thanks, {args.pr_author}\n\n"
        "--\n"
        "Name, link, team, audience scope, the description of the change and "
        "the systems and data it touches were derived from the PR's code and "
        "frontmatter. Intended users, target date, and any business "
        "justification above were written by the requester." + NEWLINE
        + "PR: " + args.pr_url + NEWLINE
    )

    subject = f"Skill/Plugin Request - {names}"

    # Only "content" is POSTed; the underscored keys are for the artifact.
    return {
        "content": body,
        "_subject": subject,
        "_pr": {"number": args.pr_number, "url": args.pr_url, "author": args.pr_author},
        "_skills": entries,
    }


# Text before the first colon on a line is a label, bolded in HTML. Capped at
# 60 characters so a sentence with a colon is not mistaken for one.
LABEL = re.compile(r"^(- )?([A-Za-z][^:]{2,60}):")


def to_html(text):
    """Wrap the plain-text request in minimal HTML, labels in bold.

    Escapes before adding markup, so skill text cannot inject HTML.
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


def fallback(args, error):
    """A minimal request for when build() fails on what the PR holds."""
    try:
        why = reasons_line(args.reasons)
    except Exception:  # noqa: BLE001
        why = "not recorded"
    content = (
        "Hi AI Ask Ops team,\n\n"
        "A request is due for this PR, but its details could not be derived "
        f"from the code ({type(error).__name__}: {error}).\n\n"
        f"PR: {args.pr_url}\n\n"
        f"Why this request: {why}\n\n"
        f"Requesting team/individual: {args.pr_author}\n\n"
        f"Thanks, {args.pr_author}\n"
    )
    return {"content": content}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pr-body", required=True, help="File holding the PR description")
    ap.add_argument("--pr-number", required=True)
    ap.add_argument("--pr-url", required=True)
    ap.add_argument("--pr-author", required=True)
    ap.add_argument("--changed-files", required=True,
                    help="File with one changed path per line")
    ap.add_argument("--removed-files", default=None,
                    help="The changed files the PR deletes, one per line, so they "
                         "are marked as removed")
    ap.add_argument("--head-root", default=None,
                    help="Directory holding the PR head's copies of changed "
                         "SKILL.md files, mirroring their repo paths")
    ap.add_argument("--marketplace-head", default=None,
                    help="marketplace.json as it stands on the PR head, when the "
                         "PR touches the registry — used to isolate what it publishes")
    ap.add_argument("--brief", default=None,
                    help="brief-card.json from the PR Brief step, for reads/writes")
    ap.add_argument("--reasons", default=None,
                    help="askops_gate.py's reasons file, for the 'Why this request' line")
    ap.add_argument("--output", default=None, help="Write the payload JSON here")
    ap.add_argument("--plain", action="store_true",
                    help="POST the text as-is instead of wrapping it in HTML "
                         "(use if the flow sends a plain-text email)")
    ap.add_argument("--post", action="store_true", help="POST to $ASKOPS_WEBHOOK")
    args = ap.parse_args(argv)

    try:
        payload = build(args)
    except Exception as e:  # noqa: BLE001 - a due request is sent whatever the PR holds
        print(f"AskOps request details not derived: {e!r}", file=sys.stderr)
        payload = fallback(args, e)

    if args.output:
        Path(args.output).write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                                     encoding="utf-8", newline="\n")
    if args.post:
        import urllib.request
        # The flow needs both headers: it rejects a request without the secret,
        # and does not parse JSON without the content type.
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
