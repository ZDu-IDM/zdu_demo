#!/usr/bin/env python3
"""
validate_skill.py — validate every skill and plugin manifest in the catalog.

Errors fail CI; warnings are printed for review.
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

# The only required SKILL.md fields. Nothing under `metadata:` is required.
REQUIRED_TOP_LEVEL = ["name", "description", "allowed-tools"]
# House convention: missing sections warn, never fail.
RECOMMENDED_SECTIONS = [
    "## Instructions",
    "## Output Format",
    "## Limitations",
]

# Valid allowed-tools names; anything else is a typo.
KNOWN_TOOLS = {
    "Read", "Write", "Edit", "Grep", "Glob", "LS", "Bash", "PowerShell",
    "WebFetch", "WebSearch", "NotebookRead", "NotebookEdit", "Task",
    "AskUserQuestion", "Skill", "TodoWrite",
}
# Tools that run code or change files. Foundation-wide skills may not request
# them; others are warned for review, since nothing limits them after merge.
HIGH_POWER = {"Bash", "PowerShell", "Write", "Edit", "NotebookEdit"}
# Valid `metadata.surfaces` values. Absent means Claude Code only.
SURFACES = {"claude-code", "claude-ai"}
# A plugin with no claude-ai skill must start its description with this, so an
# admin sees it in the Claude.ai console. Matched case-insensitively; it must
# lead, because list views truncate.
SURFACE_MARKER = "claude code only"
SURFACE_MARKER_DISPLAY = "Claude Code only"

# Read + network together is a data-egress path, warned on all-staff skills.
READ_TOOLS = {"Read", "Grep", "Glob", "LS", "NotebookRead"}
EGRESS_TOOLS = {"WebFetch", "WebSearch"}

errors = []
warnings = []


# Only the plugin roots are scanned, never .venv, dist or other vendored trees.
sys.path.insert(0, str(REPO_ROOT / "scripts"))
from utils import (  # noqa: E402
    PLUGIN_ROOTS,
    RETIRED_SKILL_FIELDS,
    ROOT_FOUNDATION,
    ROOT_GROUPS,
    ROOT_SHARED,
    classify_path,
    legacy_top_level,
    parse_version,
    skill_metadata,
)

SKILL_ROOTS = PLUGIN_ROOTS


def find_skill_dirs():
    dirs = []
    for root in (REPO_ROOT / r for r in SKILL_ROOTS):
        if root.exists():
            dirs.extend(p.parent for p in root.rglob("SKILL.md"))
    return sorted(dirs)


def check_plugin_manifests(root: Path) -> list[str]:
    """Every SKILL.md must sit under a `skills/` directory with a plugin.json beside it.

    Otherwise nothing installs it. `root` is injectable for tests.
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
            # At a namespace root a manifest is not allowed (check_manifest_shape),
            # so tell the author to move skills/ into a task directory instead.
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

    `parse_error` is a ready-to-report message when the manifest is not a JSON
    object, else None. check_marketplace_registration reports it; other checks
    skip such manifests.
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
                    name = None
                    parse_error = (
                        f"[{plugin_dir.name}] {rel} must be a JSON object, but its "
                        f"top level is a {type(data).__name__}. Nothing can install "
                        "this plugin: every reader expects an object with a 'name'."
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

    Each entry's `source` must be repo-relative, inside a plugin root, exist,
    hold a plugin.json, and declare the same `name` as the entry. Checks
    `.root` rather than `is_absolute()`, which misses "/x" on Windows.

    A plugin missing from the registry is only a warning: "built but not
    published" is a normal state until the Working Group publishes it.
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
    """A plugin's declared name must match its route.

    Team plugins: `<team>-<task>`. Shared plugins: exactly the directory name.
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
    """No two manifests, and no two registry entries, may share a name.

    A duplicate silently overwrites the other, so one plugin disappears.
    foundation-wide and shared-workflows both use bare task names, so they can
    collide.
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

    The description is all an admin sees in the Claude.ai console. An error
    once the plugin is registered, a warning before. Plugins with no skills, or
    with any claude-ai skill, are exempt.
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
            f"[{name}] every skill is Claude Code only — declared, or by leaving "
            "out `metadata.surfaces`, which defaults to it — so the description must "
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
    """marketplace.json and plugin.json must carry the same description."""
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
    """A manifest may not sit at a namespace root, and must declare a `name`.

    Namespace roots are `plugins/groups/<team>/` and `plugins/shared-workflows/`.
    The fix names both the manifest and `skills/`, so following it does not
    trip check_plugin_manifests instead.
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


def check_plugin_version(root: Path) -> list[str]:
    """Every plugin.json must have a quoted semver `version`.

    It versions every skill in the plugin, and check_plugin_versions.py
    cannot compare a missing one.
    """
    errors: list[str] = []
    for plugin_dir, _, parse_error in _plugin_manifests(root):
        if parse_error:
            continue        # reported by check_marketplace_registration
        manifest = plugin_dir / ".claude-plugin" / "plugin.json"
        data = json.loads(manifest.read_text(encoding="utf-8")) or {}
        label = data.get("name") or plugin_dir.name
        rel_manifest = manifest.relative_to(root).as_posix()
        raw = data.get("version")
        if parse_version(raw) is None:
            got = "no 'version'" if raw is None else f"'version' {raw!r}"
            hint = (" — JSON read it as a number; quote it"
                    if isinstance(raw, (int, float)) and not isinstance(raw, bool)
                    else "")
            errors.append(
                f"[{label}] {rel_manifest} has {got}, but needs a semver string "
                f'like "1.0.0" (MAJOR.MINOR.PATCH){hint}. It is the version for '
                "every skill in the plugin."
            )
    return errors


def check_plugin_author(root: Path) -> list[str]:
    """`author` is optional; if present it must be {"name", "email"?, "url"?}.

    Claude Code will not load a plugin whose author has the wrong shape.
    """
    errors: list[str] = []
    for plugin_dir, _, parse_error in _plugin_manifests(root):
        if parse_error:
            continue        # reported by check_marketplace_registration
        manifest = plugin_dir / ".claude-plugin" / "plugin.json"
        data = json.loads(manifest.read_text(encoding="utf-8")) or {}
        if "author" not in data:
            continue
        label = data.get("name") or plugin_dir.name
        rel_manifest = manifest.relative_to(root).as_posix()
        author = data["author"]
        shape = ('"author": {"name": "...", "email": "..."} — name required, '
                 "email optional")
        if not isinstance(author, dict):
            errors.append(
                f"[{label}] {rel_manifest} has 'author' as a "
                f"{type(author).__name__}, but Claude Code needs an object: "
                f"{shape}. Otherwise the plugin fails to load."
            )
            continue
        name = author.get("name")
        if not isinstance(name, str) or not name.strip():
            errors.append(
                f"[{label}] {rel_manifest} has an 'author' with no 'name'. Claude "
                f"Code requires one whenever 'author' is present: {shape}. Add a "
                "name, or remove 'author' entirely."
            )
        for field in ("email", "url"):
            if field in author and not isinstance(author[field], str):
                errors.append(
                    f"[{label}] {rel_manifest} has 'author.{field}' as a "
                    f"{type(author[field]).__name__}; it must be a string."
                )
        email = author.get("email")
        if isinstance(email, str) and "@" not in email:
            errors.append(
                f"[{label}] {rel_manifest} has 'author.email' {email!r}, which is "
                "not an email address."
            )
    return errors


def check_skills_listed(root: Path) -> list[str]:
    """Every skill must load, and every `skills[]` entry must be real.

    `skills[]` is optional: Claude always loads `skills/<name>/SKILL.md`, and
    entries add to that. Errors: an unlisted skill nested deeper than
    `skills/<name>/`, an entry with no SKILL.md, or an entry outside this
    plugin's own `skills/`.
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
            # Otherwise two plugins could claim one skill.
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

        # The default scan loads skills/<name>/SKILL.md and nothing deeper.
        scanned = {d for d in on_disk if d.parent == skills_root}
        for skill_dir in sorted(on_disk - scanned - listed):
            errors.append(
                f"[{label}] {_rel_or_str(skill_dir, root)} has a SKILL.md nested "
                "deeper than skills/<name>/, so Claude's default scan never "
                "reaches it and nothing will install it. Move it to "
                f"skills/{skill_dir.name}/, or list it in {rel_manifest}'s "
                "'skills' array."
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
    # A top-level `surfaces` is never read, so its Claude.ai opt-in would be lost.
    misplaced = legacy_top_level(fm)
    if misplaced:
        errors.append(
            f"[{skill_dir.name}] {', '.join(repr(f) for f in misplaced)} must move "
            f"under `metadata:` — the Agent Skills standard reserves the top level, "
            f"and strict validators reject unknown keys there on upload. Indent them "
            f"two spaces under a `metadata:` key; see docs/SKILL_TEMPLATE.md."
        )

    meta = skill_metadata(fm)
    for field, instead in RETIRED_SKILL_FIELDS.items():
        if field in fm or field in meta:
            errors.append(
                f"[{skill_dir.name}] '{field}' no longer belongs in SKILL.md — "
                f"remove it, and {instead}. See docs/PLUGIN_TEMPLATE.md."
            )

    for field in REQUIRED_TOP_LEVEL:
        if not fm.get(field):
            errors.append(
                f"[{skill_dir.name}] Missing required frontmatter field: '{field}'"
            )

    # Nothing else would catch a malformed `metadata:`.
    if "metadata" in fm and not isinstance(fm["metadata"], dict):
        errors.append(
            f"[{skill_dir.name}] `metadata:` must be a mapping of keys, like "
            "`metadata:` followed by an indented `surfaces: claude-code, claude-ai`, "
            f"but it is a {type(fm['metadata']).__name__}."
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
    """Check the optional `surfaces` field.

    Absent means Claude Code only. Present, it must be non-empty and use only
    known values — a typo would silently exclude the skill from a surface.
    """
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
    """Check `allowed-tools`: known names only, no high-power tools on all-staff skills."""
    classified = classify_path(skill_dir.relative_to(REPO_ROOT))
    is_foundation = classified is not None and classified[0] == ROOT_FOUNDATION
    raw = fm.get("allowed-tools")

    if not raw:
        return              # reported as missing by validate_frontmatter

    # A comma string or a YAML list.
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

    # A warning, not an error: read + fetch is often legitimate, but on an
    # all-staff skill it deserves a reviewer's look.
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
    errors.extend(check_plugin_version(REPO_ROOT))
    errors.extend(check_plugin_author(REPO_ROOT))
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
