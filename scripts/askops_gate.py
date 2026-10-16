#!/usr/bin/env python3
"""askops_gate.py — decide whether a PR's AI-AskOps request is due.

Rules decide. When no rule fires, the PR Brief's behaviour_change field can add
a request but never remove one. On a later push: rules only, and only if no
request was sent for the PR yet. See docs/specs/2026-10-14-askops-trigger-design.md.

Usage:
  python scripts/askops_gate.py --changed-files changed_files.txt --head-root head \\
      [--marketplace-head marketplace_head.json] [--brief brief-card.json] \\
      [--mode open|push] [--already-sent N] [--reasons-out askops-reasons.txt]
"""

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from askops_request import brief_fields, plugin_dir, present  # noqa: E402
from utils import classify_path, parse_version, skill_metadata  # noqa: E402

COMPONENT_DIRS = ("hooks", "agents", "commands")
COMPONENT_FILES = (".mcp.json",)
COMPONENT_KEYS = ("hooks", "mcpServers", "agents", "commands")
MANIFEST = PurePosixPath(".claude-plugin/plugin.json")


def _read_json(path):
    """A JSON object from `path`, or None if it is missing or not an object."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, TypeError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _frontmatter(path) -> dict:
    try:
        text = Path(path).read_text(encoding="utf-8")
        fm = yaml.safe_load(text.split("---", 2)[1]) or {}
    except (OSError, IndexError, yaml.YAMLError):
        return {}
    return fm if isinstance(fm, dict) else {}


def _names(value) -> set:
    """A comma string or YAML list as a set of names, so order and spacing don't matter."""
    items = value if isinstance(value, list) else str(value or "").split(",")
    return {str(v).strip() for v in items if str(v).strip()}


def _bump(base_raw, head_raw) -> str:
    b, h = parse_version(base_raw), parse_version(head_raw)
    if not b or not h:
        return ""
    if h[0] > b[0]:
        return "MAJOR"
    if h[0] == b[0] and h[1] > b[1]:
        return "MINOR"
    return ""


def registry_names(data) -> set:
    if not data:
        return set()
    return {e.get("name") for e in data.get("plugins", [])
            if isinstance(e, dict) and e.get("name")}


def rule_reasons(changed, base_root, head_root, base_registry, head_registry,
                 removed=frozenset()) -> list:
    """Reasons from rules 1–6; empty when no rule fires.

    `removed` holds the paths the PR deletes, so a deletion is named as one
    rather than as a possibly failed fetch."""
    base_root, head_root = Path(base_root), Path(head_root)
    reasons = []
    new_plugins = {}        # plugin dir -> name, for grouping its new skills
    new_skills = {}         # plugin dir -> [skill names]
    removed_plugins = {}    # likewise for deletions
    removed_skills = {}
    for line in changed:
        rel = line.strip()
        if not rel or classify_path(rel) is None:
            continue
        p = PurePosixPath(rel)
        base, head = base_root / rel, head_root / rel
        head_ok = present(head)      # a failed fetch can leave a 0-byte file
        is_manifest = p.parts[-2:] == MANIFEST.parts

        if (p.name == "SKILL.md" or is_manifest) and not head_ok:
            if rel in removed and p.name == "SKILL.md":
                owner = p.parent.parent.parent if p.parent.parent.name == "skills" else p.parent
                removed_skills.setdefault(str(owner), []).append(p.parent.name)
            elif rel in removed:
                removed_plugins[str(p.parent.parent)] = _plugin_name(
                    str(p.parent.parent), base_root, base_root)
            else:
                reasons.append(f"removed or not fetched: {rel}")              # send, never silent

        if p.name == "SKILL.md" and head_ok:
            if not base.is_file():                                            # rule 1
                owner = plugin_dir(p, base_root, head_root)
                if owner is None and p.parent.parent.name == "skills":
                    owner = p.parent.parent.parent
                new_skills.setdefault(str(owner or p.parent), []).append(p.parent.name)
            else:
                b, h = _frontmatter(base), _frontmatter(head)
                if _names(b.get("allowed-tools")) != _names(h.get("allowed-tools")):
                    reasons.append(f"allowed-tools changed: {p.parent}")      # rule 4
                if ("claude-ai" in _names(skill_metadata(h).get("surfaces"))
                        and "claude-ai" not in _names(skill_metadata(b).get("surfaces"))):
                    reasons.append(f"now declared for claude-ai: {p.parent}")  # rule 4

        if is_manifest:
            plugin = p.parent.parent
            hd, bd = _read_json(head), _read_json(base)
            if head_ok and hd is None:                                       # unreadable: send
                reasons.append(f"plugin.json unreadable: {plugin}")
            if base.is_file() and bd is None and hd is not None:
                reasons.append(f"plugin.json unreadable: {plugin}")
            if hd is not None and not base.is_file():                          # rule 2
                new_plugins[str(plugin)] = hd.get("name") or str(plugin)
            elif hd is not None and bd is not None:
                name = hd.get("name") or plugin
                bump = _bump(bd.get("version"), hd.get("version"))
                if bump:                                                       # rule 5
                    reasons.append(f"{bump} version bump: {name} "
                                   f"({bd.get('version')} -> {hd.get('version')})")
                keys = [k for k in COMPONENT_KEYS if bd.get(k) != hd.get(k)]
                if keys:                                                       # rule 6
                    reasons.append(f"plugin components changed ({', '.join(keys)}): {plugin}")

        plugin = plugin_dir(p, base_root, head_root)
        if plugin is not None:
            below = p.relative_to(plugin).parts
            if below and (below[0] in COMPONENT_DIRS or below[0] in COMPONENT_FILES):
                reasons.append(f"plugin component changed: {rel}")             # rule 6

    # Grouped by plugin, so a large new plugin is one reason, not one per skill.
    grouped = []
    for d, name in new_plugins.items():
        skills = new_skills.pop(d, [])
        grouped.append(f"new plugin: {name}" + (f" ({_count(skills)}: {', '.join(skills)})"
                                                if skills else ""))
    for d, skills in new_skills.items():
        grouped.append(f"{_count(skills)} in {_plugin_name(d, base_root, head_root)}: "
                       f"{', '.join(skills)}")
    for d, name in removed_plugins.items():
        skills = removed_skills.pop(d, [])
        grouped.append(f"plugin removed: {name}"
                       + (f" ({_count(skills, '')}: {', '.join(skills)})" if skills else ""))
    for d, skills in removed_skills.items():
        grouped.append(f"{_count(skills, 'removed ')} from "
                       f"{_plugin_name(d, base_root, base_root)}: {', '.join(skills)}")
    reasons = grouped + reasons

    registry_changed = any(line.strip() == ".claude-plugin/marketplace.json" for line in changed)
    if head_registry is not None:                                              # rule 3
        b, h = registry_names(base_registry), registry_names(head_registry)
        if h - b:
            reasons.append("published: " + ", ".join(sorted(h - b)))
        # A deleted plugin is already "plugin removed"; unpublishing it adds nothing.
        gone = (b - h) - set(removed_plugins.values())
        if gone:
            reasons.append("unpublished: " + ", ".join(sorted(gone)))
    elif registry_changed:                                                     # rule 3, fail-safe
        reasons.append("marketplace.json changed but unreadable or not fetched")
    return list(dict.fromkeys(reasons))


def _count(skills, kind="new ") -> str:
    return f"1 {kind}skill" if len(skills) == 1 else f"{len(skills)} {kind}skills"


def _plugin_name(d, base_root, head_root) -> str:
    """A plugin's manifest name, or its directory when the manifest can't be read."""
    for root in (head_root, base_root):
        data = _read_json(Path(root) / d / MANIFEST)
        if data and isinstance(data.get("name"), str):
            return data["name"]
    return d


NOT_REPORTED = "_(nothing reported)_"     # what teams_card shows for an empty field
PARTIAL_MARKER = "**Partial**"            # what teams_card shows on a partial brief


@dataclass
class Decision:
    send: bool
    reasons: list = field(default_factory=list)
    escalated: bool = False


def decide(reasons, behaviour_change, partial, mode="open", already_sent=False) -> Decision:
    """Apply the spec's decision table. The model can only add a request."""
    if mode == "push":
        if already_sent or not reasons:
            return Decision(False)
        return Decision(True, [f"{r} (rule fired on a later push)" for r in reasons])
    # A manual re-run (say, to refresh the brief) does not mail AskOps twice;
    # the workflow's resend_email input passes already_sent=False to send anyway.
    if already_sent:
        return Decision(False)
    if reasons:
        return Decision(True, list(reasons))
    value = behaviour_change.strip() if isinstance(behaviour_change, str) else ""
    if partial or not value or value == NOT_REPORTED:
        return Decision(True, ["model review unavailable"])
    if value.lower().rstrip(".") == "none":
        return Decision(False)
    return Decision(True, [f"escalated by model review: {value}"], escalated=True)


def read_brief(path):
    """(behaviour_change or None, partial) from the brief card or a flat brief."""
    if not path or not Path(path).is_file():
        return None, False
    partial = PARTIAL_MARKER in Path(path).read_text(encoding="utf-8", errors="replace")
    value = brief_fields(path).get("behaviour_change")
    return (value if isinstance(value, str) else None), partial


def not_due_note(mode, already_sent) -> str:
    if mode == "push":
        return ("request already sent for this PR" if already_sent
                else "no rule fired on this push (model not consulted on a push)")
    if already_sent:
        return "request already sent for this PR (run with resend_email to send again)"
    return "no rule fired, model found no behaviour change"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--changed-files", required=True)
    ap.add_argument("--removed-files", default=None,
                    help="The changed files the PR deletes, one per line")
    ap.add_argument("--head-root", required=True)
    ap.add_argument("--base-root", default=".")
    ap.add_argument("--marketplace-head", default=None)
    ap.add_argument("--brief", default=None)
    ap.add_argument("--mode", choices=["open", "push"], default="open")
    ap.add_argument("--already-sent", type=int, default=0)
    ap.add_argument("--reasons-out", default=None)
    args = ap.parse_args(argv)

    base_root = Path(args.base_root)
    changed = Path(args.changed_files).read_text(encoding="utf-8").splitlines()
    base_registry = _read_json(base_root / ".claude-plugin" / "marketplace.json")
    head_registry = _read_json(args.marketplace_head) if args.marketplace_head else None
    removed = frozenset()
    if args.removed_files and Path(args.removed_files).is_file():
        removed = frozenset(line.strip() for line in Path(args.removed_files)
                            .read_text(encoding="utf-8").splitlines() if line.strip())
    reasons = rule_reasons(changed, base_root, Path(args.head_root),
                           base_registry, head_registry, removed)
    value, partial = read_brief(args.brief)
    d = decide(reasons, value, partial, args.mode, args.already_sent > 0)

    if args.reasons_out:
        Path(args.reasons_out).write_text("".join(f"{r}\n" for r in d.reasons),
                                          encoding="utf-8", newline="\n")
    print(f"AskOps request {'due' if d.send else 'not due'}: "
          + ("; ".join(d.reasons) or not_due_note(args.mode, args.already_sent > 0)))
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as f:
            f.write(f"send={'true' if d.send else 'false'}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
