#!/usr/bin/env python3
"""
deploy.py — Package GF Foundation Skills as ZIPs for Claude.ai upload

  python scripts/deploy.py --package --plugin idm-software-tools
  python scripts/deploy.py --package --plugin idm-software-tools --skill idm-pkg-install

Claude.ai's organization-skills upload takes a ZIP per skill through the UI,
with no API to push to, which is why this exists.

VALIDATION LIVES IN scripts/validate_skill.py, which is what CI runs.
This script used to carry its own copy of the rules; it drifted from CI three
separate times (stricter on sections, looser on allowed-tools and evals.json,
and a different audience test), because a second implementation of a rule set
drifts by default. Run:

  python scripts/validate_skill.py
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

# PLUGINS is built dynamically from .claude-plugin/marketplace.json — see
# load_plugins() in scripts/utils.py. Only plugins registered there are valid
# --plugin targets.
#
# Legacy hard-coded map, kept for reference only:
# PLUGINS = {
#     "foundation-wide":      REPO_ROOT / "foundation-wide",
#     "idm":                  REPO_ROOT / "groups" / "idm",
#     "global-health":        REPO_ROOT / "groups" / "global-health",
#     "global-development":   REPO_ROOT / "groups" / "global-development",
#     "policy":               REPO_ROOT / "groups" / "policy",
#     "finance":              REPO_ROOT / "groups" / "finance",
#     "it":                   REPO_ROOT / "groups" / "it",
# }
PLUGINS = load_plugins()



# ── Helpers ────────────────────────────────────────────────────────────────

def get_skills(plugin_path: Path, skill_filter: str = None) -> list[Path]:
    """
    Find all skill dirs (containing SKILL.md) under a plugin's skills/ folder.
    Handles both flat layout (skills/my-skill/) and
    category layout (skills/research/my-skill/).
    """
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
    """Return the skill's declared surfaces, or None if it declares none.

    None and [] mean different things: None is a skill written before the field
    existed, which packaging still allows; an explicit list omitting claude-ai is
    a deliberate statement that the skill does not work there.
    """
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


def ok(msg):   print(f"  ✓  {msg}")


# ── Validation ─────────────────────────────────────────────────────────────

# ── Claude.ai packaging ────────────────────────────────────────────────────

def run_package(plugin_name: str, plugin_path: Path, skill_filter: str = None):
    """Package a plugin's skills. `plugin_path` is resolved by the caller.

    Taking the path rather than looking it up in PLUGINS is what lets --path
    work: the registry only knows about published plugins, and the whole point
    of a local package is testing one that is not published yet.
    """
    skills = get_skills(plugin_path, skill_filter)

    if not skills:
        print(f"No skills found in plugin '{plugin_name}' at {plugin_path}")
        sys.exit(1)

    # Gate on the real validator rather than a local copy of the rules. This
    # used to call a validate_skill() defined in this file, which is exactly how
    # the two rule sets drifted apart. Delegating means a skill CI would reject
    # can never be packaged for upload, and there is nothing here left to fall
    # out of sync. It validates the whole catalog rather than just this plugin —
    # packaging from a repo with errors is not a thing to allow either.
    print("\nValidating before packaging (scripts/validate_skill.py)...\n")
    result = subprocess.run(
        [sys.executable, str(Path(__file__).with_name("validate_skill.py"))],
        capture_output=True, text=True,
        # Explicit, because validate_skill.py prints ✓/✗/⚠ and the child's
        # stdout is a pipe here, not a console — without this the report comes
        # back mojibake on Windows, where the default is the ANSI codepage.
        encoding="utf-8", errors="replace",
    )
    if result.returncode != 0:
        print(result.stdout or result.stderr)
        print("Fix the errors above before packaging.")
        sys.exit(1)
    print("  All skills valid.\n")

    # These ZIPs are for Claude.ai, which runs skills in a sandbox with no access
    # to the user's filesystem. A skill declaring only claude-code depends on
    # local paths or a local environment, so uploading it yields a skill that
    # looks installed and silently does nothing useful. Refuse at the one step
    # that exists solely to put a skill on that surface.
    excluded = []
    for skill_dir in list(skills):
        surfaces = read_surfaces(skill_dir)
        if surfaces is not None and "claude-ai" not in surfaces:
            skills.remove(skill_dir)
            excluded.append((skill_dir.name, surfaces))
    if excluded:
        print("Not packaging (not declared for claude-ai):")
        for name, surfaces in excluded:
            print(f"  -  {name}  (surfaces: {', '.join(surfaces)})")
        print("   Change the skill's `metadata.surfaces` if that is wrong.")
        print()
    if not skills:
        print("Nothing left to package for Claude.ai.")
        sys.exit(1)

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


# ── CLI ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Package GF Foundation Skills as ZIPs for Claude.ai upload")

    parser.add_argument("--package", action="store_true",
                        help="Package skills as ZIPs for Claude.ai upload")
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

    # --validate is accepted only so the retired invocation gets an answer
    # rather than silently packaging, or failing with an unrelated argparse
    # error. Several docs pointed here for validation for a long time.
    if args.validate or not args.package:
        print("\n".join([
            "deploy.py only packages ZIPs for Claude.ai now.",
            "To validate, run the script CI runs:",
            "  python scripts/validate_skill.py",
            "To package:",
            "  python scripts/deploy.py --package --plugin <plugin-name>",
            "  python scripts/deploy.py --package --path plugins/<root>/<plugin>  (unpublished)",
        ]), file=sys.stderr)
        sys.exit(2)

    if args.plugin and args.path:
        parser.error("give --plugin or --path, not both")
    if not args.plugin and not args.path:
        parser.error("--package requires --plugin or --path")

    if args.path:
        # Resolve the name from the manifest rather than the directory: the
        # directory name and the declared plugin name differ on the team route
        # (groups/idm/documents -> idm-documents), and the declared name is the
        # one every other tool uses.
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

    run_package(plugin_name, plugin_path, args.skill)


if __name__ == "__main__":
    main()
