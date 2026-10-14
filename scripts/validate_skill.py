#!/usr/bin/env python3
"""
validate_skill.py — Validate all SKILL.md files in the repo.

Checks:
  - SKILL.md exists (recommended body sections warn, not fail)
  - Frontmatter has required fields with valid values
  - evals/evals.json present for all skills (errors if missing)
"""

import json
import re
import sys
import yaml
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO_ROOT = Path(__file__).parent.parent
MARKETPLACE_FILE = REPO_ROOT / ".claude-plugin" / "marketplace.json"

# Top-level fields. `name` and `description` are the Agent Skills standard's own
# required pair; `allowed-tools` is a documented Claude Code field and the
# security boundary the foundation-wide rule below enforces.
REQUIRED_TOP_LEVEL = ["name", "description", "allowed-tools"]
# REQUIRED_METADATA is defined after the utils import below, from
# GF_METADATA_FIELDS -- see there for why these fields are nested.
# Fields whose absence needs more than "you forgot this" to act on.
FIELD_HINTS = {
    "surfaces": ("state where the skill works, e.g. `metadata.surfaces: claude-code, "
                 "claude-ai`. Claude.ai runs skills in a sandbox with no access to the "
                 "local filesystem, so anything expecting local paths or a local "
                 "environment is claude-code only. It cannot be inferred from "
                 "allowed-tools."),
}
# House template convention only — Agent Skills require just the frontmatter
# (name + description); the body is freeform. Missing sections warn, never fail,
# so valid or imported skills aren't rejected on structure.
RECOMMENDED_SECTIONS = [
    "## Instructions",
    "## Output Format",
    "## Limitations",
]

# Recognized Claude Code tools — anything else in allowed-tools is a typo/error.
KNOWN_TOOLS = {
    "Read", "Write", "Edit", "Grep", "Glob", "LS", "Bash", "PowerShell",
    "WebFetch", "WebSearch", "NotebookRead", "NotebookEdit", "Task",
    # Three tools this list predated. None are high-power -- none execute code
    # or write to disk -- but skills already reach for them, and a tool a skill
    # uses but cannot declare makes allowed-tools understate what the skill
    # does, which defeats the only boundary this catalog has.
    #
    #   AskUserQuestion  asks the user a question mid-run
    #   Skill            invokes another skill; the one that makes a
    #                    cross-plugin dependency visible in the declaration
    #                    rather than buried in prose
    #   TodoWrite        tracks steps in a multi-step skill
    "AskUserQuestion", "Skill", "TodoWrite",
}
# Tools that can execute code or mutate the filesystem — the runtime blast radius.
# Declaring them is the only limit: the deploy/managed-settings/ layer that once
# scoped them per group at runtime was removed when Claude Code stopped being
# centrally managed, so review at merge is the sole gate. See docs/SECURITY-SCANNING.md.
HIGH_POWER = {"Bash", "PowerShell", "Write", "Edit", "NotebookEdit"}
# HIGH_POWER is about execution and filesystem mutation; it says nothing about
# data leaving the machine. A skill holding both halves of the pair below can
# read local content and send it to an arbitrary URL — egress, with no code
# execution involved, so the all-staff rule above does not catch it. Reaching
# every member of staff is what makes that worth a reviewer's attention.
# Where a skill actually works. This cannot be derived from allowed-tools: a
# skill declaring only Read/Grep/Glob still fails on Claude.ai if it expects to
# glob the user's project, because Claude.ai runs in a sandbox that cannot see
# the local filesystem. Only the author knows, so the author declares it.
SURFACES = {"claude-code", "claude-ai"}
# Text a plugin's description must LEAD WITH when none of its skills works on
# Claude.ai. Matched case-insensitively, and an opening bracket is allowed, so
# "[Claude Code only] IDM tooling..." and "Claude Code only -- needs the local
# filesystem. IDM tooling..." both satisfy it.
#
# Position is part of the rule, not style. Admin list views truncate, and a
# trailing marker is the first thing lost: on the real idm-software-tools
# description the marker began at character 88 and survived only past ~120
# characters of display width. A rule that accepted it anywhere would permit
# exactly the invisibility it exists to prevent.
SURFACE_MARKER = "claude code only"
# How it is written in prose. `.title()` would give "Claude Code Only".
SURFACE_MARKER_DISPLAY = "Claude Code only"

READ_TOOLS = {"Read", "Grep", "Glob", "LS", "NotebookRead"}
EGRESS_TOOLS = {"WebFetch", "WebSearch"}

errors = []
warnings = []


# Only the catalog's skill roots are governed — never scan .venv, dist, or other
# vendored trees (an installed dependency may ship its own .agents/skills/*/SKILL.md).
sys.path.insert(0, str(REPO_ROOT / "scripts"))
from utils import (  # noqa: E402
    GF_METADATA_FIELDS,
    PLUGIN_ROOTS,
    ROOT_FOUNDATION,
    ROOT_GROUPS,
    ROOT_SHARED,
    classify_path,
    legacy_top_level,
    skill_metadata,
)

# GF fields, required under `metadata:`. Derived from the one definition in
# utils.py so the required set and the misplacement check can never disagree.
REQUIRED_METADATA = list(GF_METADATA_FIELDS)

# rglob from each plugin root finds every SKILL.md at any depth, which is what
# the two task routes need. Previously this named "foundation-wide/skills"
# explicitly; rglobbing the root is equivalent for the current tree and does
# not assume where inside a root the skills live.
SKILL_ROOTS = PLUGIN_ROOTS


def find_skill_dirs():
    dirs = []
    for root in (REPO_ROOT / r for r in SKILL_ROOTS):
        if root.exists():
            dirs.extend(p.parent for p in root.rglob("SKILL.md"))
    return sorted(dirs)


def check_plugin_manifests(root: Path) -> list[str]:
    """Every directory that owns a skills tree must declare a plugin manifest.

    A skills tree with no `.claude-plugin/plugin.json` is skills that no
    manifest claims: nothing installs them, on either surface, and no other
    check notices. Walks up from each SKILL.md to the nearest ancestor holding
    a `skills/` directory and requires a manifest beside it.

    Iterates PLUGIN_ROOTS rather than naming roots, so a new root is covered
    the moment it is added. `root` is injectable so tests can build fixtures.
    """
    errors: list[str] = []
    owners: set[Path] = set()

    for root_name in PLUGIN_ROOTS:
        base = root / root_name
        if not base.exists():
            continue            # an unused root is not an error
        for skill_md in sorted(base.rglob("SKILL.md")):
            cur = skill_md.parent
            owner = None
            while cur != root:
                if cur.name == "skills":
                    owner = cur.parent
                    break
                cur = cur.parent
            if owner is None:
                # No `skills/` ancestor: no plugin manifest can ever reference
                # this file by listing a skills/ tree, so it is just as
                # unreachable as an unmanifested skills/ tree is.
                rel = skill_md.relative_to(root).as_posix()
                errors.append(
                    f"[{skill_md.parent.name}] {rel} is not under a skills/ "
                    "directory, so no plugin manifest can reference it. Move "
                    "it to <plugin>/skills/<skill-name>/SKILL.md."
                )
            else:
                owners.add(owner)

    for owner in sorted(owners):
        if not (owner / ".claude-plugin" / "plugin.json").exists():
            rel = owner.relative_to(root).as_posix()
            classified = classify_path(owner.relative_to(root))
            # A manifest can never be added at `rel` itself when `rel` is a
            # namespace root (plugins/groups/<team> or plugins/shared-workflows)
            # -- check_manifest_shape rejects that shape. Pointing there anyway
            # would send the author in a loop: add the manifest here, get
            # told by the other check to move it, move only the manifest,
            # trip this check again because skills/ never moved. Name the
            # task subdirectory move up front instead.
            at_namespace_root = classified is not None and (
                (classified[0] == ROOT_GROUPS and len(classified[1]) == 1)
                or (classified[0] == ROOT_SHARED and len(classified[1]) == 0)
            )
            if at_namespace_root:
                fix = (
                    f"Move both {rel}/skills/ and a new manifest into a task "
                    f"subdirectory: {rel}/<task>/skills/ and "
                    f"{rel}/<task>/.claude-plugin/plugin.json — {rel}/ is a "
                    "namespace, not a plugin, so a manifest can't live here."
                )
            else:
                fix = "Add the manifest, or move the skills under an existing plugin."
            errors.append(
                f"[{owner.name}] {rel}/skills/ holds skills but there is no "
                f"{rel}/.claude-plugin/plugin.json — nothing can install them. "
                f"{fix}"
            )
    return errors


def _plugin_manifests(root: Path):
    """Yield (plugin_dir, declared_name, parse_error) for every manifest under a plugin root.

    `parse_error` is None unless the manifest's JSON failed to parse, in which
    case `declared_name` is None and `parse_error` is a ready-to-report
    message naming the file and the problem. A parse failure is reported as
    an error by check_marketplace_registration, not swallowed here — an
    unparseable manifest is unambiguously broken (nothing can install the
    plugin), and no other check in this file validates plugin.json's syntax.
    """
    for root_name in PLUGIN_ROOTS:
        base = root / root_name
        if not base.exists():
            continue
        for manifest in sorted(base.rglob(".claude-plugin/plugin.json")):
            plugin_dir = manifest.parent.parent
            rel = manifest.relative_to(root).as_posix()
            try:
                data = json.loads(manifest.read_text(encoding="utf-8"))
            except json.JSONDecodeError as e:
                name = None
                parse_error = (
                    f"[{plugin_dir.name}] {rel} is not valid JSON: {e}. "
                    "Nothing can install this plugin until it parses."
                )
            else:
                if data is None:
                    data = {}
                if isinstance(data, dict):
                    name = data.get("name")
                    parse_error = None
                else:
                    # A manifest that parses but is not an object crashed every
                    # caller with AttributeError on .get(), so CI failed with a
                    # traceback instead of a diagnostic and no other check ran.
                    # It is reported like a parse failure because it fails for
                    # the same reason: nothing can read it.
                    name = None
                    parse_error = (
                        f"[{plugin_dir.name}] {rel} must be a JSON object, but its "
                        f"top level is a {type(data).__name__}. Nothing can install "
                        "this plugin: every reader expects an object with a 'name' "
                        "and a 'skills' array."
                    )
            yield plugin_dir, name, parse_error


def _rel_or_str(path: Path, root: Path) -> str:
    """`path` relative to `root` as posix, or the raw string if it isn't under root."""
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def check_marketplace_registration(root: Path, marketplace_file: Path):
    """Return (errors, warnings) for the marketplace registry.

    Error, in order of how a bad `source` is caught:
      1. `source` is absolute or drive-rooted (e.g. "/etc/passwd", "C:/x") —
         rejected outright. `Path.is_absolute()` alone misses a POSIX-style
         root-rooted string on Windows (`Path("/etc/passwd").is_absolute()` is
         False there, yet joining it onto `root` discards `root` entirely via
         drive inheritance), so this checks `.root` instead, which is set for
         both forms on every platform.
      2. `source` resolves outside the repository (e.g. "../../x") — rejected.
      3. `source` resolves inside the repo but outside every entry in
         `utils.PLUGIN_ROOTS` — rejected. A plugin living outside the plugin
         roots is never YARA-scanned and gets no Infosec card, yet would stay
         published if this weren't caught.
      4. `source` resolves inside a plugin root but the directory doesn't
         exist — the catalog advertises a plugin that is not in the tree.
      5. `source` resolves to a real directory that has no
         `.claude-plugin/plugin.json` — it is a namespace (e.g.
         `plugins/groups` or `plugins/groups/<team>`), not a plugin. Every
         other check here only examines directories that already declare a
         manifest, so a namespace registered as a plugin passes every one of
         them: it "resolves" (check 4) and it sits inside a plugin root
         (check 3). Registering it anyway advertises the whole namespace as
         one installable plugin, and — because the registry no longer points
         at the real plugin beneath it — makes that plugin read as "built but
         not published" (the warning below), which silently unpublishes it
         behind a message that looks like a normal in-progress state.
      6. `source` resolves to a plugin whose manifest declares a `name`
         different from the entry's own `name` — the registry publishes the
         plugin under a name its own manifest disagrees with. Skipped when
         the manifest fails to parse: `_plugin_manifests` already reports
         that as a parse error, and reporting it again here would double-
         count one broken manifest as two errors.

    Warning, deliberately NOT an error: any plugin directory absent from the
    registry. Registration is the publication act, so "built but not yet
    published" is a legitimate state — it is how a team's skills merge under
    their own review before the Working Group publishes them. Failing it
    would force publication at merge time and destroy that gate. This also
    covers an empty scaffold (a manifest with no SKILL.md anywhere under it):
    an unlisted `skills[]` entry is exactly what check_skills_listed and this
    warning together are supposed to surface, so scaffolds are not special-
    cased out of it.

    Parses the registry directly rather than via utils.load_plugins, which
    joins sources onto the real REPO_ROOT and would ignore `root`.
    """
    errors: list[str] = []
    warnings: list[str] = []

    root_resolved = root.resolve()
    plugin_root_dirs = [(root_resolved / r) for r in PLUGIN_ROOTS]

    data = json.loads(marketplace_file.read_text(encoding="utf-8"))
    registered: dict[str, Path] = {}
    for entry in data.get("plugins", []):
        name, source = entry.get("name"), entry.get("source")
        if not (name and source):
            continue

        src = Path(source)
        if src.root:
            errors.append(
                f"[{name}] marketplace source '{source}' is absolute or "
                "drive-rooted — source must be repo-relative."
            )
            continue

        resolved = (root_resolved / src).resolve()
        try:
            resolved.relative_to(root_resolved)
        except ValueError:
            errors.append(
                f"[{name}] marketplace source '{source}' resolves outside "
                "the repository — source must stay inside it."
            )
            continue

        if not any(resolved == r or r in resolved.parents for r in plugin_root_dirs):
            errors.append(
                f"[{name}] marketplace source '{source}' resolves outside every "
                f"plugin root ({', '.join(PLUGIN_ROOTS)}) — it would never be "
                "YARA-scanned or reported to Infosec, yet would stay published."
            )
            continue

        registered[name] = resolved

    manifests_by_dir = {
        plugin_dir.resolve(): (declared_name, parse_error)
        for plugin_dir, declared_name, parse_error in _plugin_manifests(root)
    }

    for name, path in sorted(registered.items()):
        if not path.exists():
            errors.append(
                f"[{name}] marketplace source does not resolve: "
                f"{_rel_or_str(path, root_resolved)} "
                "— the catalog advertises a plugin that is not in the tree."
            )
            continue

        manifest = path / ".claude-plugin" / "plugin.json"
        if not manifest.exists():
            errors.append(
                f"[{name}] marketplace source '{_rel_or_str(path, root_resolved)}' has no "
                f"{_rel_or_str(manifest, root_resolved)} — a marketplace entry must point at "
                "a plugin directory, one holding .claude-plugin/plugin.json, not at a "
                "namespace such as plugins/groups or plugins/groups/<team>."
            )
            continue

        declared_name, parse_error = manifests_by_dir.get(path.resolve(), (None, None))
        if parse_error:
            continue  # already reported once, below, by the parse-error loop
        if declared_name != name:
            errors.append(
                f"[{name}] marketplace entry registers "
                f"'{_rel_or_str(path, root_resolved)}' as '{name}', but its manifest "
                f"declares name '{declared_name}' — the registry and the manifest must "
                "agree on the plugin's name."
            )

    registered_paths = {p.resolve() for p in registered.values()}
    for plugin_dir, _, parse_error in _plugin_manifests(root):
        if parse_error:
            errors.append(parse_error)
            continue
        if plugin_dir.resolve() not in registered_paths:
            rel = plugin_dir.relative_to(root).as_posix()
            warnings.append(
                f"[{plugin_dir.name}] {rel} is built but not published — no "
                "entry in .claude-plugin/marketplace.json. Add one when the "
                "Working Group is ready to publish it."
            )
    return errors, warnings


def check_plugin_names(root: Path) -> list[str]:
    """A plugin's declared name must match its root's convention.

    Team route: `<team>-<task>`, checked by prefix. Shared route: the declared
    name must equal the plugin directory's own name, exactly.

    The shared-route check used to flag any name whose first hyphen segment
    matched an existing `groups/*` directory — which rejected legitimate
    names (`shared-workflows/policy-review` declaring `policy-review` is
    correct, ownership-wise, but was flagged solely because `groups/policy/`
    happens to exist), and was state-coupled: creating `groups/data/` later
    would retroactively invalidate an already-correct `shared-workflows/data-review`.
    An exact match against the plugin's own directory name has neither
    problem, and still catches a smuggled team prefix — a smuggled prefix is
    exactly what makes the declared name diverge from the directory name.
    """
    errors: list[str] = []

    for plugin_dir, name, _ in _plugin_manifests(root):
        if not name:
            continue            # missing or unparseable name is reported by other checks
        classified = classify_path(plugin_dir.relative_to(root))
        if classified is None:
            continue
        plugin_root, below = classified

        if plugin_root == ROOT_GROUPS and len(below) >= 1:
            team = below[0]
            if not name.startswith(f"{team}-"):
                errors.append(
                    f"[{plugin_dir.name}] plugin name '{name}' must start with "
                    f"'{team}-' because it lives under {ROOT_GROUPS}/{team}/. The "
                    "prefix is what keeps team-owned and shared plugin names from "
                    "colliding."
                )

        elif plugin_root == ROOT_SHARED:
            if name != plugin_dir.name:
                errors.append(
                    f"[{plugin_dir.name}] plugin name '{name}' must equal its "
                    f"directory name '{plugin_dir.name}' — a shared-workflows "
                    "plugin is owned centrally and takes no prefix, so the "
                    "declared name and the directory name must match exactly."
                )

    return errors


def check_duplicate_plugin_names(root: Path, marketplace_file: Path) -> list[str]:
    """No two manifests may declare the same name, and no two registry entries
    may share one.

    `utils.load_plugins` keys on the declared name, so a duplicate does not
    collide loudly — the later entry overwrites the earlier one and a plugin
    simply disappears from every name-addressed path: `--plugin`, eval
    selection, `/plugin install`. Nothing else here catches it, because every
    other check examines one manifest at a time and a duplicate is only
    visible across the whole set.

    The layout deliberately permits unprefixed names on two routes —
    `plugins/foundation-wide/<task>` and `plugins/shared-workflows/<task>`
    each declare a bare task name — so `foundation-wide/legal` and
    `shared-workflows/legal` is a collision the structure allows by design.
    Prefixing was considered and declined; this check is what makes that
    choice safe, by turning a silent disappearance into a merge-blocking
    error.

    A manifest that fails to parse is skipped: `_plugin_manifests` already
    reports it, and it declares no name to collide with.
    """
    errors: list[str] = []

    by_name: dict[str, list[str]] = {}
    for plugin_dir, name, parse_error in _plugin_manifests(root):
        if parse_error or not name:
            continue        # reported by check_marketplace_registration / check_manifest_shape
        by_name.setdefault(name, []).append(plugin_dir.relative_to(root).as_posix())

    for name, dirs in sorted(by_name.items()):
        if len(dirs) > 1:
            errors.append(
                f"[{name}] {len(dirs)} plugins declare the name '{name}': "
                + ", ".join(sorted(dirs))
                + ". Names address plugins, so load_plugins keeps only one and the "
                "rest vanish silently — rename all but one."
            )

    try:
        data = json.loads(marketplace_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return errors       # registry problems are check_marketplace_registration's to report

    seen: dict[str, int] = {}
    for entry in data.get("plugins", []):
        entry_name = entry.get("name")
        if entry_name:
            seen[entry_name] = seen.get(entry_name, 0) + 1
    for entry_name, count in sorted(seen.items()):
        if count > 1:
            errors.append(
                f"[{entry_name}] the marketplace registers '{entry_name}' {count} times. "
                "Installers resolve a name to one entry, so the duplicates are "
                "unreachable — remove all but one."
            )

    return errors


def _manifest_description(plugin_dir: Path) -> str:
    """The plugin's own declared description, or "" if unreadable."""
    mf = plugin_dir / ".claude-plugin" / "plugin.json"
    try:
        data = json.loads(mf.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""           # reported by _plugin_manifests via its parse_error
    return data.get("description", "") if isinstance(data, dict) else ""


def _plugin_surfaces(plugin_dir: Path) -> tuple[int, set[str]]:
    """(skill count, union of the surfaces its skills declare)."""
    surfaces: set[str] = set()
    skills = sorted(plugin_dir.rglob("SKILL.md"))
    for s in skills:
        try:
            fm = yaml.safe_load(s.read_text(encoding="utf-8").split("---")[1]) or {}
        except (OSError, IndexError, yaml.YAMLError):
            continue        # malformed frontmatter is validate_skill_md's to report
        raw = skill_metadata(fm).get("surfaces")
        values = ([x.strip() for x in raw.split(",")] if isinstance(raw, str)
                  else [str(x).strip() for x in (raw or [])])
        surfaces |= {v for v in values if v}
    return len(skills), surfaces


def check_surface_marker(root: Path, marketplace_file: Path):
    """Return (errors, warnings): a Claude-Code-only plugin must say so.

    An admin assigning plugins in the Claude.ai console sees a name and a
    description, and nothing else. A plugin whose skills all require the local
    filesystem installs cleanly there and then does nothing -- the failure is
    silent on both sides, which is why it has to be visible in the one field
    that travels to that screen.

    Registration decides severity, not whether to publish. `marketplace.json`
    is the registry for BOTH surfaces, so a Claude-Code-only plugin belongs in
    it -- that is how Claude Code users get it at all, and a newly synced
    plugin defaults to "Not available" on Claude.ai regardless. What changes on
    registration is that a human now reads the description while deciding who
    gets it, so an unmarked one can mislead. Before that, nobody reads it.

    Exempt: a plugin with no skills (nothing to install either way) and any
    plugin with at least one claude-ai skill -- it does work there, and
    "Claude Code only" would be the false claim.
    """
    errors: list[str] = []
    warnings: list[str] = []

    try:
        data = json.loads(marketplace_file.read_text(encoding="utf-8"))
        registered = {e.get("name") for e in data.get("plugins", []) if e.get("name")}
    except (OSError, json.JSONDecodeError):
        registered = set()  # registry problems are check_marketplace_registration's

    for plugin_dir, name, parse_error in _plugin_manifests(root):
        if parse_error or not name:
            continue
        count, surfaces = _plugin_surfaces(plugin_dir)
        if count == 0 or "claude-ai" in surfaces:
            continue
        desc = _manifest_description(plugin_dir)
        if desc.lower().lstrip().lstrip("[").startswith(SURFACE_MARKER):
            continue
        trailing = SURFACE_MARKER in desc.lower()
        where = (plugin_dir / ".claude-plugin/plugin.json").relative_to(root).as_posix()
        msg = (
            f"[{name}] every skill is Claude Code only, so the description must "
            f"lead with '[{SURFACE_MARKER_DISPLAY}]' — in {where} and in the "
            "matching marketplace.json entry. In the Claude.ai admin console the "
            "description is all a human has when deciding which groups get this, "
            "and assigning it there installs a plugin that cannot work."
            + (" The marker is present but not at the front, where a truncated "
               "list view drops it first." if trailing else "")
        )
        (errors if name in registered else warnings).append(msg)

    return errors, warnings


def check_description_agreement(root: Path, marketplace_file: Path) -> list[str]:
    """The registry entry and the plugin's manifest must describe it the same way.

    Two copies of one sentence, on two surfaces, with nothing comparing them.
    They agree today by discipline alone. It matters more now that the surface
    marker lives in the description: add it to one copy and the other still
    shows the unmarked text, which is the exact reader this is meant to warn.
    """
    errors: list[str] = []
    try:
        data = json.loads(marketplace_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return errors

    by_name = {name: plugin_dir
               for plugin_dir, name, parse_error in _plugin_manifests(root)
               if name and not parse_error}

    for entry in data.get("plugins", []):
        name = entry.get("name")
        plugin_dir = by_name.get(name)
        if not name or plugin_dir is None:
            continue        # unresolvable entries are check_marketplace_registration's
        registry_desc = entry.get("description", "")
        manifest_desc = _manifest_description(plugin_dir)
        if registry_desc != manifest_desc:
            errors.append(
                f"[{name}] marketplace.json and plugin.json describe this plugin "
                f"differently, so the two surfaces show different text. "
                f"registry: {registry_desc!r} — manifest: {manifest_desc!r}"
            )
    return errors


def check_manifest_shape(root: Path) -> list[str]:
    """Two rules about where a manifest may sit and what it must declare.

    A manifest directly at a namespace root — `plugins/groups/<team>/` or
    `plugins/shared-workflows/` itself — is the pre-restructure shape: the
    namespace holds task plugins now, and a manifest there would nest every
    task plugin beneath it (or, for `plugins/shared-workflows/`, recreate the
    very namespace-as-plugin shape this restructure abolishes).
    `plugins/foundation-wide/` stays legitimate at depth 1 — it is the one
    namespace that is also a plugin — so the rule is specific to
    `plugins/groups/<team>/` (exactly one path component below the groups
    root) and `plugins/shared-workflows/` alone (exactly zero below its root).

    The remediation names BOTH the manifest and the `skills/` tree: an
    earlier version only said "move it [the manifest] to
    <rel>/<task>/...plugin.json", which re-triggers check_plugin_manifests
    the moment it's followed, because `skills/` never moved — the manifest
    would then sit in the task subdirectory while the skills stayed at the
    namespace root, unclaimed by anything. Whichever of the two checks a
    contributor reads first must land them on a valid layout, not send them
    to the other check to trip again.

    A manifest with no `name` cannot be addressed: `--plugin` takes the
    declared name, `utils.load_plugins` drops a nameless entry outright, and
    nothing can install it. Before this rule it passed validation green.
    """
    errors: list[str] = []
    for plugin_dir, name, parse_error in _plugin_manifests(root):
        if parse_error:
            continue        # reported by check_marketplace_registration
        rel = plugin_dir.relative_to(root).as_posix()
        classified = classify_path(plugin_dir.relative_to(root))

        at_namespace_root = classified is not None and (
            (classified[0] == ROOT_GROUPS and len(classified[1]) == 1)
            or (classified[0] == ROOT_SHARED and len(classified[1]) == 0)
        )
        if at_namespace_root:
            errors.append(
                f"[{plugin_dir.name}] {rel}/.claude-plugin/plugin.json sits "
                f"directly at a namespace root, but {rel}/ holds task plugins, "
                "not skills itself. Move BOTH the manifest AND "
                f"{rel}/skills/ (if present) into a task subdirectory: "
                f"{rel}/<task>/.claude-plugin/plugin.json and "
                f"{rel}/<task>/skills/. Or delete the manifest if nothing ships "
                "from here yet."
            )
        if not name:
            errors.append(
                f"[{plugin_dir.name}] {rel}/.claude-plugin/plugin.json declares no "
                "'name', so it cannot be installed — --plugin takes the declared "
                "name and load_plugins drops a nameless entry."
            )
    return errors


def check_skills_listed(root: Path) -> list[str]:
    """A plugin's `skills[]` must exactly match what's on disk under its own `skills/`.

    Two failure directions, both silent otherwise:
      - A skill added on disk but never added to `skills[]`. Every other gate
        passes — validate_skill's SKILL.md checks, evals, the security scan —
        and an Infosec card announces "now available to staff", but the
        plugin installs nothing for that skill: `skills[]` is what Claude Code
        and Claude.ai actually read to populate the install.
      - A `skills[]` entry with no matching `SKILL.md` — a manifest pointing
        at nothing, e.g. a typo'd path or a skill that was deleted without
        updating the manifest.

    Bounded to `plugin_dir/skills` only (not an unbounded rglob under
    plugin_dir), for the same reason check_marketplace_registration bounds
    its scan: an unbounded walk would let a wrapping manifest see a nested
    plugin's skills as its own.
    """
    errors: list[str] = []
    for plugin_dir, _, parse_error in _plugin_manifests(root):
        if parse_error:
            continue        # reported by check_marketplace_registration
        manifest = plugin_dir / ".claude-plugin" / "plugin.json"
        data = json.loads(manifest.read_text(encoding="utf-8")) or {}
        label = data.get("name") or plugin_dir.name
        rel_manifest = manifest.relative_to(root).as_posix()

        skills_dir = plugin_dir / "skills"
        on_disk = {p.parent.resolve() for p in skills_dir.rglob("SKILL.md")} \
            if skills_dir.is_dir() else set()

        listed: set[Path] = set()
        skills_root = skills_dir.resolve()
        for entry in data.get("skills") or []:
            # Containment, before anything else. Without it a manifest could
            # list "../../../other-plugin/skills/x": the path resolves, its
            # SKILL.md exists, so every check below passed and TWO plugins
            # claimed one skill with nothing reported. The on-disk scan is
            # bounded to this plugin's own skills/, so an escaped entry was
            # invisible in both directions.
            if not isinstance(entry, str):
                errors.append(
                    f"[{label}] {rel_manifest} has a non-string entry in 'skills' "
                    f"({entry!r}). Each entry must be a path string like "
                    '"./skills/<skill-name>".'
                )
                continue
            if Path(entry).is_absolute() or Path(entry).root or Path(entry).drive:
                errors.append(
                    f"[{label}] {rel_manifest} lists an absolute path '{entry}' in "
                    "'skills'. Entries must be relative to the plugin directory, "
                    'like "./skills/<skill-name>".'
                )
                continue
            entry_path = (plugin_dir / entry).resolve()
            if not entry_path.is_relative_to(skills_root):
                errors.append(
                    f"[{label}] {rel_manifest} lists '{entry}' in 'skills', which "
                    f"resolves outside {rel_manifest.rsplit('/', 2)[0]}/skills/ to "
                    f"{_rel_or_str(entry_path, root)}. A plugin may only claim skills "
                    "inside its own skills/ directory — otherwise two plugins can "
                    "claim the same skill and neither reports it."
                )
                continue
            listed.add(entry_path)
            if not (entry_path / "SKILL.md").is_file():
                errors.append(
                    f"[{label}] {rel_manifest} lists '{entry}' in 'skills', but "
                    f"{_rel_or_str(entry_path, root)}/SKILL.md does not exist — "
                    "the manifest points at nothing."
                )

        for skill_dir in sorted(on_disk - listed):
            errors.append(
                f"[{label}] {_rel_or_str(skill_dir, root)} has a SKILL.md but is "
                f"not listed in {rel_manifest}'s 'skills' array — nothing will "
                "install it, even though every other check passes."
            )
    return errors


def parse_frontmatter(path: Path):
    text = path.read_text(encoding="utf-8")
    parts = text.split("---", 2)
    if len(parts) < 3:
        errors.append(f"[{path.parent.name}] SKILL.md missing frontmatter (no --- delimiters)")
        return None
    try:
        return yaml.safe_load(parts[1])
    except yaml.YAMLError as e:
        errors.append(f"[{path.parent.name}] Frontmatter YAML parse error: {e}")
        return None


def validate_frontmatter(skill_dir: Path, fm: dict):
    # Misplacement is reported on its own terms and the metadata check is then
    # skipped, because a file with `owner:` at the top level would otherwise draw
    # two errors for one mistake -- and the second ("missing required field:
    # owner") reads as an instruction to add a line that is already there.
    misplaced = legacy_top_level(fm)
    if misplaced:
        errors.append(
            f"[{skill_dir.name}] {', '.join(repr(f) for f in misplaced)} must move "
            f"under `metadata:` — the Agent Skills standard reserves the top level, "
            f"and strict validators reject unknown keys there on upload. Indent them "
            f"two spaces under a `metadata:` key; see docs/SKILL_TEMPLATE.md."
        )

    for field in REQUIRED_TOP_LEVEL:
        if not fm.get(field):
            hint = FIELD_HINTS.get(field)
            errors.append(
                f"[{skill_dir.name}] Missing required frontmatter field: '{field}'"
                + (f" — {hint}" if hint else "")
            )

    meta = skill_metadata(fm)
    for field in REQUIRED_METADATA:
        if field in misplaced:
            continue
        if not meta.get(field):
            hint = FIELD_HINTS.get(field)
            errors.append(
                f"[{skill_dir.name}] Missing required frontmatter field: "
                f"'metadata.{field}'" + (f" — {hint}" if hint else "")
            )



def validate_skill_md(skill_dir: Path):
    path = skill_dir / "SKILL.md"
    content = path.read_text(encoding="utf-8")

    for section in RECOMMENDED_SECTIONS:
        if section not in content:
            warnings.append(f"[{skill_dir.name}] SKILL.md missing recommended section '{section}' "
                            "(GF template convention, not required by Agent Skills)")

    if len(content.strip()) < 300:
        warnings.append(f"[{skill_dir.name}] SKILL.md seems very short — is it complete?")


def validate_evals(skill_dir: Path, fm: dict):
    evals_path = skill_dir / "evals" / "evals.json"

    if not evals_path.exists():
        errors.append(
            f"[{skill_dir.name}] Missing evals/evals.json — required for all skills. "
            "See docs/EVALS.md for the schema."
        )
        return

    try:
        data = json.loads(evals_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        errors.append(f"[{skill_dir.name}] evals/evals.json parse error: {e}")
        return

    if not isinstance(data.get("evals"), list) or len(data["evals"]) == 0:
        errors.append(f"[{skill_dir.name}] evals/evals.json must have a non-empty 'evals' array")
    else:
        for i, ev in enumerate(data["evals"]):
            etype = ev.get("type", "single_shot")
            if etype not in ("single_shot", "workflow"):
                errors.append(f"[{skill_dir.name}] evals[{i}] invalid type '{etype}' "
                              "(expected 'single_shot' or 'workflow')")
                continue
            required = (["id", "name", "prompt", "rubric"] if etype == "single_shot"
                        else ["id", "name", "steps", "pass_condition"])
            for req in required:
                if req not in ev:
                    errors.append(f"[{skill_dir.name}] evals[{i}] ({etype}) missing field '{req}'")
            if etype == "workflow" and not (isinstance(ev.get("steps"), list) and ev.get("steps")):
                errors.append(f"[{skill_dir.name}] evals[{i}] (workflow) 'steps' must be a non-empty list")


def validate_surfaces(skill_dir: Path, fm: dict):
    """Check the `surfaces` field: where this skill is claimed to work.

    Absence is an error, raised by validate_frontmatter — `surfaces` is a required
    field. A skill published to a surface it cannot function on fails silently
    there, and silence is the whole problem: nothing else in the pipeline can tell
    that a Read/Grep/Glob skill expects the user's filesystem.

    An unknown value IS an error: a typo like "claude-desktop" would otherwise
    read as a deliberate restriction and quietly exclude the skill from every
    surface a packaging step consults.
    """
    # Absence is reported once, by validate_frontmatter via REQUIRED_METADATA
    # and FIELD_HINTS. Repeating it here would double-count one mistake.
    meta = skill_metadata(fm)
    if "surfaces" not in meta:
        return

    raw = meta["surfaces"]
    declared = ([s.strip() for s in raw.split(",")] if isinstance(raw, str)
                else [str(s).strip() for s in (raw or [])])
    declared = [s for s in declared if s]

    if not declared:
        errors.append(
            f"[{skill_dir.name}] 'metadata.surfaces' is empty — a skill that works nowhere "
            f"cannot be published. Use one or both of {sorted(SURFACES)}."
        )
        return

    unknown = [s for s in declared if s not in SURFACES]
    if unknown:
        errors.append(
            f"[{skill_dir.name}] Unknown surface(s) {unknown} in 'metadata.surfaces'. "
            f"Valid values are {sorted(SURFACES)}."
        )


def validate_allowed_tools(skill_dir: Path, fm: dict):
    """Enforce least-privilege tool declarations. 'allowed-tools' is REQUIRED;
    foundation-wide (all-staff) skills may not request high-power tools, and
    group skills requesting them are warned for security review.
    """
    classified = classify_path(skill_dir.relative_to(REPO_ROOT))
    is_foundation = classified is not None and classified[0] == ROOT_FOUNDATION
    raw = fm.get("allowed-tools")

    if not raw:
        # 'allowed-tools' is a REQUIRED_TOP_LEVEL field (checked above); when it's
        # absent that check reports it, so there's nothing further to validate here.
        return

    # A YAML list is as valid as a comma string. str() of a list split on
    # commas yields "['Read'" and fails every tool, list-form or not.
    tools = ([t.strip() for t in raw.split(",")] if isinstance(raw, str)
             else [str(t).strip() for t in (raw if isinstance(raw, list) else [raw])])
    tools = [t for t in tools if t]

    for t in tools:
        if t not in KNOWN_TOOLS:
            errors.append(f"[{skill_dir.name}] Unknown tool in allowed-tools: '{t}'")

    high = [t for t in tools if t in HIGH_POWER]
    if is_foundation and high:
        errors.append(
            f"[{skill_dir.name}] Foundation-wide (all-staff) skills may not request "
            f"high-power tools {high}. Keep all-staff skills read/research-only, or "
            "move this to a group skill."
        )
    elif high:
        warnings.append(
            f"[{skill_dir.name}] Requests high-power tools {high} — needs security "
            "review at merge. Nothing constrains them afterwards: this catalog does "
            "not manage Claude Code, so the declared allowed-tools list is the only "
            "limit on what this skill can do on a user's machine."
        )

    # Warning, not an error: reading and fetching are each legitimate, and the
    # combination often is too — checking a local file against a public source,
    # say. What it is not is invisible. An all-staff skill that can read local
    # content and send it somewhere reaches every machine at once, which is the
    # scale that makes it worth a named look rather than a silent pass.
    reads = sorted(t for t in tools if t in READ_TOOLS)
    egress = sorted(t for t in tools if t in EGRESS_TOOLS)
    if is_foundation and reads and egress:
        warnings.append(
            f"[{skill_dir.name}] All-staff skill can both read locally {reads} and "
            f"reach the network {egress} — that pair is a data-egress path and needs "
            "security review at merge. It is not blocked: the high-power rule covers "
            "execution and filesystem writes, not egress, so nothing else flags this."
        )


def main():
    skill_dirs = find_skill_dirs()

    if not skill_dirs:
        print("No SKILL.md files found.")
        sys.exit(0)

    print(f"Validating {len(skill_dirs)} skill(s)...\n")

    for skill_dir in skill_dirs:
        skill_md = skill_dir / "SKILL.md"
        fm = parse_frontmatter(skill_md)
        if fm:
            validate_frontmatter(skill_dir, fm)
            validate_evals(skill_dir, fm)
            validate_allowed_tools(skill_dir, fm)
            validate_surfaces(skill_dir, fm)
        validate_skill_md(skill_dir)

    errors.extend(check_plugin_manifests(REPO_ROOT))

    mk_errors, mk_warnings = check_marketplace_registration(REPO_ROOT, MARKETPLACE_FILE)
    errors.extend(mk_errors)
    warnings.extend(mk_warnings)
    errors.extend(check_plugin_names(REPO_ROOT))
    errors.extend(check_duplicate_plugin_names(REPO_ROOT, MARKETPLACE_FILE))
    sm_errors, sm_warnings = check_surface_marker(REPO_ROOT, MARKETPLACE_FILE)
    errors.extend(sm_errors)
    warnings.extend(sm_warnings)
    errors.extend(check_description_agreement(REPO_ROOT, MARKETPLACE_FILE))
    errors.extend(check_manifest_shape(REPO_ROOT))
    errors.extend(check_skills_listed(REPO_ROOT))

    if warnings:
        print("WARNINGS:")
        for w in warnings:
            print(f"  ⚠  {w}")
        print()

    if errors:
        print("ERRORS:")
        for e in errors:
            print(f"  ✗  {e}")
        print(f"\n{len(errors)} error(s). Fix before merging.")
        sys.exit(1)
    else:
        print(f"✓  All {len(skill_dirs)} skill(s) passed validation.")
        if warnings:
            print(f"   {len(warnings)} warning(s) to review.")


if __name__ == "__main__":
    main()
