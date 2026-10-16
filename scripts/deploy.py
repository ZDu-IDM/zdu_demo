#!/usr/bin/env python3
"""
deploy.py — Package GF Foundation Skills as ZIPs for Claude.ai upload

  python scripts/deploy.py --package --plugin idm-software-tools
  python scripts/deploy.py --package --plugin idm-software-tools --skill idm-pkg-install
  python scripts/deploy.py --plugin-zip --plugin idm-software-tools

--package writes one ZIP per skill (Organization skills or a personal upload).
--plugin-zip writes one ZIP for the whole plugin ("Upload a plugin").

Validation is validate_skill.py's job; both modes run it before packaging.
"""

import argparse
import json
import subprocess
import sys
import yaml
import zipfile
from pathlib import Path

from utils import HARNESS_DIRS, load_plugins, skill_metadata

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO_ROOT = Path(__file__).parent.parent

# Registered plugins, the valid --plugin targets. Use --path for others.
PLUGINS = load_plugins()

# ── Helpers ────────────────────────────────────────────────────────────────

def get_skills(plugin_path: Path, skill_filter: str = None) -> list[Path]:
    """Skill directories (holding SKILL.md) under a plugin's skills/, at any depth."""
    skills_root = plugin_path / "skills"
    if not skills_root.exists():
        return []

    found = []
    for path in sorted(skills_root.rglob("SKILL.md")):
        skill_dir = path.parent
        if skill_dir not in found:
            found.append(skill_dir)

    if skill_filter:
        found = [s for s in found if s.name == skill_filter]

    return found


def read_surfaces(skill_dir: Path):
    """The skill's declared surfaces, or None if it declares none (Claude Code only)."""
    md = skill_dir / "SKILL.md"
    try:
        fm = yaml.safe_load(md.read_text(encoding="utf-8").split("---", 2)[1]) or {}
    except (OSError, IndexError, yaml.YAMLError):
        return None
    raw = skill_metadata(fm).get("surfaces")
    if raw is None:
        return None
    values = ([s.strip() for s in raw.split(",")] if isinstance(raw, str)
              else [str(s).strip() for s in (raw or [])])
    return [v for v in values if v]


def packs_for_claude_ai(surfaces) -> bool:
    """Whether a skill goes into a Claude.ai ZIP: only with an explicit `claude-ai`."""
    return surfaces is not None and "claude-ai" in surfaces


def ok(msg):   print(f"  ✓  {msg}")


# ── Claude.ai packaging ────────────────────────────────────────────────────

def run_package(plugin_name: str, plugin_path: Path, skill_filter: str = None):
    """Package a plugin's skills, one ZIP each."""
    skills = get_skills(plugin_path, skill_filter)

    if not skills:
        print(f"No skills found in plugin '{plugin_name}' at {plugin_path}")
        sys.exit(1)

    validate_catalog()
    skills = claude_ai_skills(skills)

    output_dir = REPO_ROOT / "dist" / "claude-ai"
    output_dir.mkdir(parents=True, exist_ok=True)

    for old_zip in output_dir.glob("*.zip"):
        old_zip.unlink()

    print(f"Packaging {len(skills)} skill(s) → {output_dir}\n")

    for skill_dir in skills:
        zip_path = output_dir / f"{skill_dir.name}.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for file_path in skill_dir.rglob("*"):
                if not file_path.is_file():
                    continue
                rel = file_path.relative_to(skill_dir)
                if rel.parts[0] in HARNESS_DIRS:
                    continue  # evals/ is our test harness, not the shipped skill
                zf.write(file_path, skill_dir.name / rel)
        size_kb = zip_path.stat().st_size / 1024
        ok(f"{skill_dir.name}.zip  ({size_kb:.1f} KB)")

    print(f"""
── Upload to Claude.ai ─────────────────────────────────────────────────────

Personal:
  First enable code execution: claude.ai → Settings → Capabilities
  Then: claude.ai → Customize → Skills → "+" → "Create skill" → "Upload a skill"
  Upload each .zip from: {output_dir}

Team / Enterprise (org-wide, admin/owner):
  First enable "Code execution and file creation" + "Skills" in
    Organization settings → Skills
  Then: Organization settings → Skills → Organization skills → "+ Add"
  Upload each .zip. Provisioned skills are ON by default for all members
    (members can toggle off).

────────────────────────────────────────────────────────────────────────────
""")


# The only dot-names kept in a plugin ZIP, and names always left out.
PLUGIN_ZIP_DOT_ALLOWED = {".claude-plugin", ".mcp.json"}
PLUGIN_ZIP_SKIP_NAMES = {"__pycache__"}


def plugin_zip_entries(plugin_dir: Path, excluded_skills=()):
    """(file, path-in-plugin) pairs for a plugin ZIP.

    Leaves out other dot-files, caches, evals/ and `excluded_skills`. Raises
    ValueError for a top-level bin/, which claude.ai rejects.
    """
    if (plugin_dir / "bin").is_dir():
        raise ValueError(
            f"{plugin_dir} has a top-level bin/ directory, and claude.ai rejects "
            "any plugin with one. Move executables to scripts/ and reference them "
            "as ${CLAUDE_PLUGIN_ROOT}/scripts/<name>.")
    excluded = {Path(d).resolve() for d in excluded_skills}
    entries = []
    for file_path in sorted(plugin_dir.rglob("*")):
        if not file_path.is_file():
            continue
        rel = file_path.relative_to(plugin_dir)
        if any(p.startswith(".") and p not in PLUGIN_ZIP_DOT_ALLOWED for p in rel.parts):
            continue
        if any(p in PLUGIN_ZIP_SKIP_NAMES for p in rel.parts):
            continue
        if rel.parts[0] == "skills" and any(d in rel.parts[1:] for d in HARNESS_DIRS):
            continue
        if any(a in excluded for a in file_path.resolve().parents):
            continue
        entries.append((file_path, rel))
    return entries


def plugin_zip_manifest(plugin_dir: Path, excluded_skills=()) -> str:
    """plugin.json text for the ZIP, minus `skills` entries for excluded skills."""
    manifest = plugin_dir / ".claude-plugin" / "plugin.json"
    text = manifest.read_text(encoding="utf-8")
    data = json.loads(text)
    entries = data.get("skills")
    if not excluded_skills or not isinstance(entries, list):
        return text
    excluded = {Path(d).resolve() for d in excluded_skills}
    kept = [e for e in entries
            if not (isinstance(e, str) and (plugin_dir / e).resolve() in excluded)]
    if kept == entries:
        return text
    if kept:
        data["skills"] = kept
    else:
        del data["skills"]
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def run_plugin_zip(plugin_name: str, plugin_path: Path):
    """Package a whole plugin as one ZIP for claude.ai's "Upload a plugin"."""
    skills = get_skills(plugin_path)
    if not skills:
        print(f"No skills found in plugin '{plugin_name}' at {plugin_path}")
        sys.exit(1)

    validate_catalog()
    included = claude_ai_skills(skills)
    excluded = [s for s in skills if s not in included]

    try:
        entries = plugin_zip_entries(plugin_path, excluded)
    except ValueError as e:
        print(f"Not packaging: {e}")
        sys.exit(1)
    manifest_arc = Path(".claude-plugin") / "plugin.json"
    manifest_text = plugin_zip_manifest(plugin_path, excluded)

    output_dir = REPO_ROOT / "dist" / "claude-ai" / "plugins"
    output_dir.mkdir(parents=True, exist_ok=True)
    zip_path = output_dir / f"{plugin_name}.zip"

    # One top-level folder named for the plugin.
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for file_path, rel in entries:
            arc = (Path(plugin_name) / rel).as_posix()
            if rel == manifest_arc:
                zf.writestr(arc, manifest_text)
            else:
                zf.write(file_path, arc)

    size_kb = zip_path.stat().st_size / 1024
    print(f"Packaging plugin '{plugin_name}' with {len(included)} skill(s) → {output_dir}\n")
    for s in included:
        ok(s.name)
    ok(f"{zip_path.name}  ({size_kb:.1f} KB)")

    print("""
── Upload to Claude.ai ─────────────────────────────────────────────────────

Organization (Owner or Primary Owner):
  Organization settings → Plugins & skills → Add → Upload a plugin
  Then set its availability: Inventory → the plugin's menu → Default access
  (or Group access… on Enterprise). To update later: the plugin's menu →
  Upload new version.

Your own account (testing):
  Customize → Plugins → Add → Upload plugin

If the organization already syncs this repo from GitHub, the plugin is there
already — don't upload it as well.

────────────────────────────────────────────────────────────────────────────
""")


def validate_catalog():
    """Exit unless validate_skill.py passes on the whole catalog."""
    print("\nValidating before packaging (scripts/validate_skill.py)...\n")
    result = subprocess.run(
        [sys.executable, str(Path(__file__).with_name("validate_skill.py"))],
        capture_output=True, text=True,
        # The report has ✓/✗/⚠, which Windows' default codepage garbles.
        encoding="utf-8", errors="replace",
    )
    if result.returncode != 0:
        print(result.stdout or result.stderr)
        print("Fix the errors above before packaging.")
        sys.exit(1)
    print("  All skills valid.\n")


def claude_ai_skills(skills):
    """The skills declared for claude-ai, listing the rest; exits if there are none."""
    included, excluded = [], []
    for skill_dir in skills:
        surfaces = read_surfaces(skill_dir)
        (included if packs_for_claude_ai(surfaces) else excluded).append((skill_dir, surfaces))
    if excluded:
        print("Not packaging (not declared for claude-ai):")
        for skill_dir, surfaces in excluded:
            shown = ", ".join(surfaces) if surfaces else "not declared, so Claude Code only"
            print(f"  -  {skill_dir.name}  (surfaces: {shown})")
        print("   If a skill works on what the user pastes or uploads, add "
              "`metadata.surfaces: claude-code, claude-ai`.")
        print()
    if not included:
        print("Nothing left to package for Claude.ai.")
        sys.exit(1)
    return [skill_dir for skill_dir, _ in included]


# ── CLI ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Package GF Foundation Skills as ZIPs for Claude.ai upload")

    parser.add_argument("--package", action="store_true",
                        help="Package skills as ZIPs for Claude.ai upload")
    parser.add_argument("--plugin-zip", action="store_true",
                        help="Package the whole plugin as one ZIP for Claude.ai's "
                             "\"Upload a plugin\"")
    parser.add_argument("--validate", action="store_true",
                        help=argparse.SUPPRESS)   # retired; see below
    parser.add_argument("--plugin", choices=list(PLUGINS.keys()),
                        help="Published plugin to package, by name")
    parser.add_argument("--path", default=None, metavar="DIR",
                        help="Plugin directory to package, bypassing the registry — "
                             "use this for a plugin that is not published yet")
    parser.add_argument("--skill", default=None,
                        help="Package a single skill by name")

    args = parser.parse_args()

    # The retired --validate, or no mode at all, gets usage instead of an error.
    if args.validate or not (args.package or args.plugin_zip):
        print("\n".join([
            "deploy.py only packages ZIPs for Claude.ai now.",
            "To validate, run the script CI runs:",
            "  python scripts/validate_skill.py",
            "To package:",
            "  python scripts/deploy.py --package --plugin <plugin-name>      (one ZIP per skill)",
            "  python scripts/deploy.py --plugin-zip --plugin <plugin-name>   (one ZIP for the plugin)",
            "  ...or --path plugins/<root>/<plugin> instead of --plugin, for an unpublished plugin",
        ]), file=sys.stderr)
        sys.exit(2)

    if args.package and args.plugin_zip:
        parser.error("give --package or --plugin-zip, not both")
    if args.plugin_zip and args.skill:
        parser.error("--skill works with --package only; --plugin-zip packages the whole plugin")
    if args.plugin and args.path:
        parser.error("give --plugin or --path, not both")
    if not args.plugin and not args.path:
        parser.error("--package and --plugin-zip need --plugin or --path")

    if args.path:
        # The declared name, not the directory name (groups/idm/data is idm-data).
        plugin_path = Path(args.path).resolve()
        manifest = plugin_path / ".claude-plugin" / "plugin.json"
        if not manifest.is_file():
            parser.error(f"no .claude-plugin/plugin.json under {plugin_path} — "
                         "--path takes a plugin directory, not a skill or a namespace")
        try:
            plugin_name = (json.loads(manifest.read_text(encoding="utf-8")) or {}).get("name")
        except json.JSONDecodeError as e:
            parser.error(f"{manifest} is not valid JSON: {e}")
        if not plugin_name:
            parser.error(f"{manifest} declares no 'name'")
        print(f"Packaging from {plugin_path} (plugin '{plugin_name}', not via the registry)")
    else:
        plugin_name, plugin_path = args.plugin, PLUGINS[args.plugin]

    if args.plugin_zip:
        run_plugin_zip(plugin_name, plugin_path)
    else:
        run_package(plugin_name, plugin_path, args.skill)


if __name__ == "__main__":
    main()
