#!/usr/bin/env python3
"""Offline behavior test for the layout checks in validate_skill.py.

Run:  python scripts/tests/test_validate_layout.py
"""
import importlib.util
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location(
    "validate_skill", HERE.parent / "validate_skill.py")
vs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vs)

passed = failed = 0


def check(name, got, want):
    global passed, failed
    if got == want:
        passed += 1
        print(f"PASS - {name}")
    else:
        failed += 1
        print(f"FAIL - {name}\n        got:  {got!r}\n        want: {want!r}")


def make_skill(root: Path, rel: str):
    d = root / rel
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text("---\nname: s\n---\nbody\n", encoding="utf-8")


def make_manifest(root: Path, rel: str, name: str):
    d = root / rel / ".claude-plugin"
    d.mkdir(parents=True, exist_ok=True)
    (d / "plugin.json").write_text(
        '{"name": "%s", "skills": []}' % name, encoding="utf-8")


with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    make_skill(root, "plugins/groups/idm/software-tools/skills/reviewer")
    make_manifest(root, "plugins/groups/idm/software-tools", "idm-software-tools")
    check("a team task plugin with a manifest passes",
          vs.check_plugin_manifests(root), [])

with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    make_skill(root, "plugins/groups/idm/software-tools/skills/reviewer")
    errs = vs.check_plugin_manifests(root)
    check("team route: skills with no manifest is one error", len(errs), 1)
    check("the error names the directory",
          "plugins/groups/idm/software-tools" in errs[0], True)

with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    # Proves shared-workflows is in PLUGIN_ROOTS, not just that the check works.
    make_skill(root, "plugins/shared-workflows/grant-review/skills/reviewer")
    errs = vs.check_plugin_manifests(root)
    check("shared route: skills with no manifest is also an error", len(errs), 1)
    check("the shared-route error names the directory",
          "plugins/shared-workflows/grant-review" in errs[0], True)

with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    make_skill(root, "plugins/shared-workflows/grant-review/skills/reviewer")
    make_manifest(root, "plugins/shared-workflows/grant-review", "grant-review")
    check("shared route with a manifest passes",
          vs.check_plugin_manifests(root), [])

with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    # foundation-wide: manifest at the top, skills under a category.
    make_skill(root, "plugins/foundation-wide/skills/research/literature-review")
    make_manifest(root, "plugins/foundation-wide", "foundation-wide-skills")
    check("foundation-wide's shape is accepted",
          vs.check_plugin_manifests(root), [])

with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    (root / "plugins/groups" / "policy").mkdir(parents=True)
    check("empty scaffolding is not an error",
          vs.check_plugin_manifests(root), [])

with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    # A root with no directory on disk is skipped. Restored afterwards.
    original_roots = vs.PLUGIN_ROOTS
    vs.PLUGIN_ROOTS = (*original_roots, "does-not-exist-on-disk")
    try:
        check("a root in PLUGIN_ROOTS with no directory on disk is skipped, not fatal",
              vs.check_plugin_manifests(root), [])
    finally:
        vs.PLUGIN_ROOTS = original_roots

with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    # A SKILL.md with no skills/ ancestor can never be installed.
    make_skill(root, "plugins/groups/idm")
    errs = vs.check_plugin_manifests(root)
    check("a SKILL.md with no skills/ ancestor is one error", len(errs), 1)

import json


def marketplace(root: Path, plugins) -> Path:
    d = root / ".claude-plugin"
    d.mkdir(parents=True, exist_ok=True)
    p = d / "marketplace.json"
    p.write_text(json.dumps({"plugins": plugins}), encoding="utf-8")
    return p


# --- registration ---
with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    make_skill(root, "plugins/groups/idm/software-tools/skills/reviewer")
    make_manifest(root, "plugins/groups/idm/software-tools", "idm-software-tools")
    mk = marketplace(root, [
        {"name": "idm-software-tools", "source": "./plugins/groups/idm/software-tools"},
    ])
    check("a registered, existing plugin is clean",
          vs.check_marketplace_registration(root, mk), ([], []))

with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    mk = marketplace(root, [{"name": "gone", "source": "./plugins/groups/idm/vanished"}])
    errs, warns = vs.check_marketplace_registration(root, mk)
    check("a source that does not resolve is an error", len(errs), 1)
    check("the error names the missing source",
          "plugins/groups/idm/vanished" in errs[0], True)
    check("it is not also a warning", warns, [])

with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    make_skill(root, "plugins/shared-workflows/grant-review/skills/reviewer")
    make_manifest(root, "plugins/shared-workflows/grant-review", "grant-review")
    mk = marketplace(root, [])
    errs, warns = vs.check_marketplace_registration(root, mk)
    check("an unregistered plugin is a WARNING, not an error", errs, [])
    check("and there is exactly one warning", len(warns), 1)
    check("the warning says built but not published",
          "not published" in warns[0], True)

with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    # An unregistered plugin warns even with no skills.
    make_manifest(root, "plugins/groups/idm/empty-scaffold", "idm-empty-scaffold")
    mk = marketplace(root, [])
    errs, warns = vs.check_marketplace_registration(root, mk)
    check("an unregistered plugin with no SKILL.md now warns too",
          len(warns), 1)
    check("and not an error", errs, [])

with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    d = root / "plugins/groups" / "idm" / "broken" / ".claude-plugin"
    d.mkdir(parents=True, exist_ok=True)
    (d / "plugin.json").write_text('{ "name": "idm-broken", broken json here',
                                    encoding="utf-8")
    mk = marketplace(root, [])
    errs, warns = vs.check_marketplace_registration(root, mk)
    check("a manifest that fails to parse is exactly one error", len(errs), 1)
    check("the error names the broken manifest file",
          "plugins/groups/idm/broken/.claude-plugin/plugin.json" in errs[0], True)

with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    # A wrapper manifest around a nested plugin: one warning each.
    make_manifest(root, "plugins/groups/it", "it-skills")
    make_skill(root, "plugins/groups/it/probe/skills/p")
    make_manifest(root, "plugins/groups/it/probe", "it-probe")
    mk = marketplace(root, [])
    errs, warns = vs.check_marketplace_registration(root, mk)
    check("every unregistered manifest warns, wrapper and nested alike",
          len(warns), 2)
    check("one warning names the nested plugin",
          any("plugins/groups/it/probe" in w for w in warns), True)
    check("the other names the wrapping parent",
          any("plugins/groups/it" in w and "plugins/groups/it/probe" not in w for w in warns), True)

# --- marketplace source must point at a plugin, not a namespace ---
with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    # A namespace passes every other check; only the manifest check catches it.
    make_skill(root, "plugins/groups/idm/software-tools/skills/reviewer")
    make_manifest(root, "plugins/groups/idm/software-tools", "idm-software-tools")
    mk = marketplace(root, [{"name": "everything", "source": "./plugins/groups"}])
    errs, warns = vs.check_marketplace_registration(root, mk)
    check("registering the whole groups namespace is exactly one error", len(errs), 1)
    check("the error names the namespace path", "plugins/groups" in errs[0], True)

with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    make_skill(root, "plugins/groups/idm/software-tools/skills/reviewer")
    make_manifest(root, "plugins/groups/idm/software-tools", "idm-software-tools")
    mk = marketplace(root, [{"name": "team", "source": "./plugins/groups/idm"}])
    errs, warns = vs.check_marketplace_registration(root, mk)
    check("registering a team namespace is exactly one error", len(errs), 1)
    check("the error names the team namespace path", "plugins/groups/idm" in errs[0], True)

with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    make_skill(root, "plugins/groups/idm/software-tools/skills/reviewer")
    make_manifest(root, "plugins/groups/idm/software-tools", "idm-software-tools")
    mk = marketplace(root, [
        {"name": "idm-software-tools", "source": "./plugins/groups/idm/software-tools"},
    ])
    errs, warns = vs.check_marketplace_registration(root, mk)
    check("a real plugin whose manifest name matches the entry is clean",
          (errs, warns), ([], []))

with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    make_skill(root, "plugins/groups/idm/software-tools/skills/reviewer")
    make_manifest(root, "plugins/groups/idm/software-tools", "idm-software-tools")
    mk = marketplace(root, [
        {"name": "totally-different-name", "source": "./plugins/groups/idm/software-tools"},
    ])
    errs, warns = vs.check_marketplace_registration(root, mk)
    check("a manifest name mismatch is exactly one error", len(errs), 1)
    check("the mismatch error names both names",
          "totally-different-name" in errs[0] and "idm-software-tools" in errs[0], True)

with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    d = root / "plugins/groups/idm/broken/.claude-plugin"
    d.mkdir(parents=True, exist_ok=True)
    (d / "plugin.json").write_text('{ "name": "idm-broken", broken json here',
                                    encoding="utf-8")
    mk = marketplace(root, [{"name": "idm-broken", "source": "./plugins/groups/idm/broken"}])
    errs, warns = vs.check_marketplace_registration(root, mk)
    check("a registered plugin with a malformed manifest is exactly one error "
          "(the parse error), not two", len(errs), 1)

with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    make_skill(root, "plugins/groups/idm/software-tools/skills/reviewer")
    make_manifest(root, "plugins/groups/idm/software-tools", "idm-software-tools")
    mk = marketplace(root, [])
    errs, warns = vs.check_marketplace_registration(root, mk)
    check("regression: an unregistered plugin with a skill is still exactly one warning",
          len(warns), 1)
    check("regression: and still zero errors", len(errs), 0)

# --- naming ---
with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    make_manifest(root, "plugins/groups/idm/software-tools", "idm-software-tools")
    make_manifest(root, "plugins/shared-workflows/grant-review", "grant-review")
    make_manifest(root, "plugins/foundation-wide", "foundation-wide-skills")
    check("correctly named plugins pass", vs.check_plugin_names(root), [])

with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    make_manifest(root, "plugins/groups/idm/software-tools", "software-tools")
    errs = vs.check_plugin_names(root)
    check("a team plugin missing its team prefix is an error", len(errs), 1)
    check("the error names the expected prefix", "idm-" in errs[0], True)

with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    # Shared names must equal the directory name, whatever groups/* exist.
    (root / "plugins/groups" / "policy").mkdir(parents=True)
    make_manifest(root, "plugins/shared-workflows/policy-review", "policy-review")
    check("a shared plugin whose name equals its directory passes, "
          "even when a same-named team directory exists",
          vs.check_plugin_names(root), [])

with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    make_manifest(root, "plugins/shared-workflows/grant-review", "policy-grant-review")
    errs = vs.check_plugin_names(root)
    check("a shared plugin whose name diverges from its directory is an error",
          len(errs), 1)
    check("the error names the expected (directory) name",
          "grant-review" in errs[0], True)

# --- manifest shape: a namespace root (groups/<team>/ or shared-workflows/) is not a plugin ---
with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    make_manifest(root, "plugins/groups/idm", "idm-skills")
    errs = vs.check_manifest_shape(root)
    check("a plugin manifest directly under groups/<team>/ is the old shape", len(errs), 1)
    check("the old-shape error names the directory", "plugins/groups/idm" in errs[0], True)
    check("the remediation names moving skills/ too, not just the manifest",
          "skills/" in errs[0], True)

with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    make_manifest(root, "plugins/shared-workflows", "shared-workflows-skills")
    errs = vs.check_manifest_shape(root)
    check("a plugin manifest directly at shared-workflows/ is also the old shape",
          len(errs), 1)
    check("the shared-root error names shared-workflows",
          "plugins/shared-workflows" in errs[0], True)

with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    make_manifest(root, "plugins/groups/idm/software-tools", "idm-software-tools")
    make_manifest(root, "plugins/shared-workflows/grant-review", "grant-review")
    make_manifest(root, "plugins/foundation-wide", "foundation-wide-skills")
    check("task plugins, shared plugins, and foundation-wide at depth 1 are all accepted",
          vs.check_manifest_shape(root), [])

# --- manifest shape: a plugin with no declared name cannot be addressed ---
with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    d = root / "plugins/groups/idm/nameless/.claude-plugin"
    d.mkdir(parents=True, exist_ok=True)
    (d / "plugin.json").write_text('{"version": "1.0.0", "skills": []}', encoding="utf-8")
    errs = vs.check_manifest_shape(root)
    check("a manifest with no name is an error", len(errs), 1)
    check("the nameless error explains it cannot be installed",
          "cannot be installed" in errs[0] or "cannot be addressed" in errs[0], True)

# --- plugin.json version: required, quoted semver ---


def plugin_version_errors(version_json):
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        d = root / "plugins/groups/idm/demo/.claude-plugin"
        d.mkdir(parents=True, exist_ok=True)
        body = '{"name": "idm-demo"%s}' % (
            "" if version_json is None else f', "version": {version_json}')
        (d / "plugin.json").write_text(body, encoding="utf-8")
        return vs.check_plugin_version(root)


check("a semver plugin version passes", plugin_version_errors('"1.2.3"'), [])
errs = plugin_version_errors(None)
check("a plugin with no version is an error", len(errs), 1)
check("and the error names the manifest",
      "plugins/groups/idm/demo/.claude-plugin/plugin.json" in errs[0], True)
errs = plugin_version_errors("1.1")
check("a numeric plugin version is an error", len(errs), 1)
check("and the error says to quote it", "quote it" in errs[0], True)
check("a two-part plugin version is an error", len(plugin_version_errors('"1.0"')), 1)

# --- plugin.json author: optional, but Claude Code's shape when present ---


def plugin_author_errors(author_json):
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        d = root / "plugins/groups/idm/demo/.claude-plugin"
        d.mkdir(parents=True, exist_ok=True)
        body = '{"name": "idm-demo", "version": "1.0.0"%s}' % (
            "" if author_json is None else f', "author": {author_json}')
        (d / "plugin.json").write_text(body, encoding="utf-8")
        return vs.check_plugin_author(root)


check("no author at all passes", plugin_author_errors(None), [])
check("an author with name and email passes",
      plugin_author_errors('{"name": "IDM", "email": "idm@gatesfoundation.org"}'), [])
check("an author with only a name passes", plugin_author_errors('{"name": "IDM"}'), [])
errs = plugin_author_errors('{"email": "idm@gatesfoundation.org"}')
check("an author with only an email is an error", len(errs), 1)
check("and the error says name is required", "no 'name'" in errs[0], True)
errs = plugin_author_errors('"IDM <idm@gatesfoundation.org>"')
check("an npm-style author string is an error", len(errs), 1)
check("and the error shows the object shape", '"author": {' in errs[0], True)
check("an email with no @ is an error",
      len(plugin_author_errors('{"name": "IDM", "email": "idm"}')), 1)
check("a non-string email is an error",
      len(plugin_author_errors('{"name": "IDM", "email": 42}')), 1)

def make_manifest_with_skills(root: Path, rel: str, name: str, skills: list[str]) -> Path:
    d = root / rel / ".claude-plugin"
    d.mkdir(parents=True, exist_ok=True)
    p = d / "plugin.json"
    p.write_text(json.dumps({"name": name, "skills": skills}), encoding="utf-8")
    return p


# --- every skill must load; skills[] is optional but must point at real ones ---
with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    make_skill(root, "plugins/groups/idm/software-tools/skills/reviewer")
    make_skill(root, "plugins/groups/idm/software-tools/skills/unlisted")
    make_manifest_with_skills(root, "plugins/groups/idm/software-tools", "idm-software-tools",
                              ["./skills/reviewer"])
    check("an unlisted skill at skills/<name>/ is not an error -- the default scan loads it",
          vs.check_skills_listed(root), [])

with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    make_skill(root, "plugins/groups/kmi/grant-budget-review/skills/grant-budget-review")
    m = root / "plugins/groups/kmi/grant-budget-review/.claude-plugin/plugin.json"
    m.parent.mkdir(parents=True)
    m.write_text('{"name": "grant-budget-review"}', encoding="utf-8")
    check("a plugin with no 'skills' key at all passes", vs.check_skills_listed(root), [])

with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    make_skill(root, "plugins/groups/idm/software-tools/skills/reviewer")
    make_skill(root, "plugins/groups/idm/software-tools/skills/group/deep")
    make_manifest_with_skills(root, "plugins/groups/idm/software-tools", "idm-software-tools",
                              [])
    errs = vs.check_skills_listed(root)
    check("an unlisted skill nested deeper than skills/<name>/ is one error", len(errs), 1)
    check("the error names it and says the default scan misses it",
          ("skills/group/deep" in errs[0], "never reaches it" in errs[0]), (True, True))
    make_manifest_with_skills(root, "plugins/groups/idm/software-tools", "idm-software-tools",
                              ["./skills/group/deep"])
    check("listing the nested skill is enough", vs.check_skills_listed(root), [])

with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    make_skill(root, "plugins/groups/idm/software-tools/skills/reviewer")
    make_manifest_with_skills(root, "plugins/groups/idm/software-tools", "idm-software-tools",
                              ["./skills/reviewer", "./skills/does-not-exist"])
    errs = vs.check_skills_listed(root)
    check("a skills[] entry pointing at nothing is one error", len(errs), 1)
    check("the error names the dangling entry",
          "does-not-exist" in errs[0], True)

with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    make_skill(root, "plugins/groups/idm/software-tools/skills/reviewer")
    make_skill(root, "plugins/groups/idm/software-tools/skills/fixer")
    make_manifest_with_skills(root, "plugins/groups/idm/software-tools", "idm-software-tools",
                              ["./skills/reviewer", "./skills/fixer"])
    check("a manifest whose skills[] matches the tree exactly passes",
          vs.check_skills_listed(root), [])

# --- marketplace source must stay repo-relative and inside a plugin root ---
with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    mk = marketplace(root, [{"name": "escaped", "source": "/etc/passwd"}])
    errs, warns = vs.check_marketplace_registration(root, mk)
    check("an absolute (root-rooted) source is exactly one error", len(errs), 1)
    check("the error says the source must be repo-relative",
          "repo-relative" in errs[0], True)
    check("it is not also a warning", warns, [])

with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    mk = marketplace(root, [{"name": "drive-rooted", "source": "C:/Windows/win.ini"}])
    errs, _ = vs.check_marketplace_registration(root, mk)
    check("a drive-rooted source is exactly one error", len(errs), 1)
    check("the drive-rooted error also says repo-relative",
          "repo-relative" in errs[0], True)

with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    # A real directory just outside the checkout, so .exists() alone would pass.
    outside = root.parent / f"{root.name}-outside-escape-target"
    outside.mkdir(exist_ok=True)
    try:
        mk = marketplace(root, [{"name": "escapee", "source": "../" + outside.name}])
        errs, warns = vs.check_marketplace_registration(root, mk)
        check("a '../' escape out of the repo is exactly one error", len(errs), 1)
        check("the escape error explains the source must stay inside the repo",
              "repository" in errs[0] or "inside" in errs[0], True)
        check("it is not also a warning", warns, [])
    finally:
        outside.rmdir()

with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    (root / "not-a-plugin-root").mkdir()
    mk = marketplace(root, [{"name": "orphan", "source": "./not-a-plugin-root"}])
    errs, _ = vs.check_marketplace_registration(root, mk)
    check("a source inside the repo but outside every plugin root is an error",
          len(errs), 1)
    check("the error names the plugin roots",
          "plugins/foundation-wide" in errs[0] and "plugins/groups" in errs[0], True)

with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    make_skill(root, "plugins/groups/idm/software-tools/skills/reviewer")
    make_manifest(root, "plugins/groups/idm/software-tools", "idm-software-tools")
    mk = marketplace(root, [
        {"name": "idm-software-tools", "source": "./plugins/groups/idm/software-tools"},
    ])
    errs, warns = vs.check_marketplace_registration(root, mk)
    check("a valid, repo-relative source under a plugin root is clean",
          (errs, warns), ([], []))

# --- the all-staff high-power-tools rule ---
vs.errors.clear()
vs.warnings.clear()
fake_skill_dir = vs.REPO_ROOT / "plugins/foundation-wide" / "__test_fake_skill__"
vs.validate_allowed_tools(fake_skill_dir, {"allowed-tools": "Bash, Read"})
check("a foundation-wide skill requesting Bash is exactly one error",
      len(vs.errors), 1)
check("the error says foundation-wide (all-staff) skills may not request it",
      "Foundation-wide" in vs.errors[0] and "Bash" in vs.errors[0], True)
vs.errors.clear()
vs.warnings.clear()

# --- the surfaces field ---
SKILL_DIR = vs.REPO_ROOT / "plugins/foundation-wide/x" / "__test_fake_skill__"


def surfaces_result(fm):
    """Run validate_surfaces; a top-level `surfaces` key is moved under `metadata:`."""
    if "surfaces" in fm:
        fm = dict(fm)
        fm["metadata"] = {"surfaces": fm.pop("surfaces")}
    vs.errors.clear()
    vs.warnings.clear()
    vs.validate_surfaces(SKILL_DIR, fm)
    out = (list(vs.errors), list(vs.warnings))
    vs.errors.clear()
    vs.warnings.clear()
    return out


errs, warns = surfaces_result({"surfaces": "claude-code, claude-ai"})
check("both surfaces is clean", (errs, warns), ([], []))
check("one surface is clean",
      surfaces_result({"surfaces": "claude-code"}), ([], []))
check("a YAML list works as well as a comma string",
      surfaces_result({"surfaces": ["claude-ai"]}), ([], []))

errs, _ = surfaces_result({"surfaces": "claude-desktop"})
check("an unknown surface is an error", len(errs), 1)
check("the error names the bad value", "claude-desktop" in errs[0], True)
check("and lists the valid ones", "claude-code" in errs[0], True)

errs, _ = surfaces_result({"surfaces": ""})
check("an empty surfaces value is an error", len(errs), 1)

# surfaces is optional; absent means Claude Code only.
check("validate_surfaces accepts a skill with no surfaces",
      surfaces_result({"name": "x"}), ([], []))
vs.errors.clear()
vs.validate_frontmatter(SKILL_DIR, {"name": "x", "description": "d",
                                    "allowed-tools": "Read"})
check("a skill with no metadata at all passes the frontmatter check", vs.errors, [])
vs.errors.clear()

# --- GF fields belong under metadata: ---


def frontmatter_errors(fm):
    vs.errors.clear()
    vs.validate_frontmatter(SKILL_DIR, fm)
    out = list(vs.errors)
    vs.errors.clear()
    return out


GOOD_FM = {"name": "x", "description": "d", "allowed-tools": "Read",
           "metadata": {"surfaces": "claude-code"}}
check("a correctly nested skill passes", frontmatter_errors(GOOD_FM), [])

LEGACY_FM = {"name": "x", "description": "d", "allowed-tools": "Read",
             "surfaces": "claude-code"}
errs = frontmatter_errors(LEGACY_FM)
check("the pre-migration shape is rejected", len(errs), 1)
check("and the error names the misplaced field", "surfaces" in errs[0], True)
check("and says where it goes", "metadata:" in errs[0], True)
check("a misplaced field is not also reported as missing",
      any("Missing required" in e for e in errs), False)

# --- owner moved to plugin.json's optional author ---
errs = frontmatter_errors({**GOOD_FM, "metadata": {**GOOD_FM["metadata"],
                                                   "owner": "o"}})
check("a metadata.owner is rejected", len(errs), 1)
check("and the error points to plugin.json's author",
      "plugin.json" in errs[0] and "author" in errs[0], True)
errs = frontmatter_errors({**GOOD_FM, "owner": "o"})
check("a top-level owner is rejected the same way, not as misplaced",
      (len(errs), "must move" in errs[0]), (1, False))
errs = frontmatter_errors({**GOOD_FM, "owner": "o", "version": "1.0.0"})
check("an owner and a version are one error each", len(errs), 2)

# --- version moved to plugin.json ---
errs = frontmatter_errors({**GOOD_FM, "metadata": {**GOOD_FM["metadata"],
                                                   "version": "1.0.0"}})
check("a metadata.version is rejected", len(errs), 1)
check("and the error points to plugin.json", "plugin.json" in errs[0], True)
check("and says to remove it, not move it", "remove it" in errs[0], True)
errs = frontmatter_errors({**GOOD_FM, "version": "1.0.0"})
check("a top-level version is rejected the same way", len(errs), 1)
check("and is not reported as misplaced under metadata:",
      "must move" in errs[0], False)

check("allowed-tools is still required at the top level",
      "allowed-tools" in vs.REQUIRED_TOP_LEVEL, True)

errs = frontmatter_errors({"name": "x", "description": "d",
                           "allowed-tools": "Read", "metadata": "oops"})
check("a non-dict metadata is one error, not a crash", len(errs), 1)
check("and the error says it must be a mapping", "mapping" in errs[0], True)

# --- all-staff read + network = an egress path (warning, not error) ---
FW = vs.REPO_ROOT / "plugins/foundation-wide" / "__test_fake_skill__"
GRP = vs.REPO_ROOT / "plugins/groups/idm/x" / "__test_fake_skill__"


def tools_warnings(skill_dir, tools):
    vs.errors.clear()
    vs.warnings.clear()
    vs.validate_allowed_tools(skill_dir, {"allowed-tools": tools})
    out = (list(vs.errors), list(vs.warnings))
    vs.errors.clear()
    vs.warnings.clear()
    return out


# allowed-tools may be a YAML list or a comma string.
check("a YAML list of known tools is clean",
      tools_warnings(GRP, ["Read", "Grep"]), ([], []))
errs, _ = tools_warnings(GRP, ["Read", "Nope"])
check("a YAML list still flags an unknown tool, by its bare name",
      errs, ["[__test_fake_skill__] Unknown tool in allowed-tools: 'Nope'"])
check("a YAML list is still subject to the all-staff high-power rule",
      len(tools_warnings(FW, ["Bash", "Read"])[0]), 1)

errs, warns = tools_warnings(FW, "Read, Grep, WebFetch")
egress = [w for w in warns if "read locally" in w]
check("all-staff read + WebFetch warns", len(egress), 1)
check("and does not error -- the pair is allowed, just visible", errs, [])
check("the warning names it as egress", "egress" in egress[0], True)
check("and says nothing else catches it", "nothing else flags this" in egress[0], True)

check("WebFetch with no read tool does not warn",
      [w for w in tools_warnings(FW, "WebFetch")[1] if "read locally" in w], [])
check("read tools with no network access do not warn",
      [w for w in tools_warnings(FW, "Read, Grep, Glob")[1] if "read locally" in w], [])

check("a group skill with the same pair does not get this warning",
      [w for w in tools_warnings(GRP, "Read, Grep, WebFetch")[1] if "read locally" in w], [])

check("WebSearch counts as egress too",
      len([w for w in tools_warnings(FW, "Read, WebSearch")[1] if "read locally" in w]), 1)

# --- duplicate declared names ---

with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    make_manifest(root, "plugins/foundation-wide/legal", "legal")
    make_manifest(root, "plugins/shared-workflows/legal", "legal")
    errs = vs.check_duplicate_plugin_names(root, marketplace(root, []))
    check("two plugins declaring the same name is one error", len(errs), 1)
    check("the error names both directories",
          "plugins/foundation-wide/legal" in errs[0]
          and "plugins/shared-workflows/legal" in errs[0], True)
    check("the error explains that one would vanish", "vanish" in errs[0], True)

with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    make_manifest(root, "plugins/foundation-wide/legal", "legal")
    make_manifest(root, "plugins/foundation-wide/design", "design")
    make_manifest(root, "plugins/shared-workflows/workflow-a", "workflow-a")
    check("distinct names across all routes pass",
          vs.check_duplicate_plugin_names(root, marketplace(root, [])), [])

with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    for r in ("plugins/foundation-wide/a", "plugins/shared-workflows/a",
              "plugins/groups/idm/a"):
        make_manifest(root, r, "dup")
    errs = vs.check_duplicate_plugin_names(root, marketplace(root, []))
    check("three colliding manifests report as one error", len(errs), 1)
    check("that error counts all three", "3 plugins declare" in errs[0], True)

with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    make_manifest(root, "plugins/shared-workflows/workflow-a", "workflow-a")
    mk = marketplace(root, [
        {"name": "workflow-a", "source": "./plugins/shared-workflows/workflow-a"},
        {"name": "workflow-a", "source": "./plugins/shared-workflows/workflow-a"},
    ])
    errs = vs.check_duplicate_plugin_names(root, mk)
    check("a name registered twice is one error", len(errs), 1)
    check("the error says it is registered twice", "2 times" in errs[0], True)

with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    d = root / "plugins/shared-workflows/broken/.claude-plugin"
    d.mkdir(parents=True)
    (d / "plugin.json").write_text("{not json", encoding="utf-8")
    make_manifest(root, "plugins/shared-workflows/ok", "ok")
    check("an unparseable manifest is skipped, not double-reported",
          vs.check_duplicate_plugin_names(root, marketplace(root, [])), [])

# --- a manifest that parses but is not an object ---

with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    d = root / "plugins/shared-workflows/listy/.claude-plugin"
    d.mkdir(parents=True)
    (d / "plugin.json").write_text("[1, 2, 3]", encoding="utf-8")
    errs, _ = vs.check_marketplace_registration(root, marketplace(root, []))
    check("a JSON array manifest is one error, not a crash", len(errs), 1)
    check("the error says it must be an object", "must be a JSON object" in errs[0], True)
    check("and names the actual type", "list" in errs[0], True)
    check("check_plugin_names survives a non-object manifest",
          vs.check_plugin_names(root), [])
    check("check_manifest_shape survives it", vs.check_manifest_shape(root), [])
    check("check_skills_listed survives it", vs.check_skills_listed(root), [])

with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    d = root / "plugins/shared-workflows/nully/.claude-plugin"
    d.mkdir(parents=True)
    (d / "plugin.json").write_text("null", encoding="utf-8")
    # `null` reads as an empty object, so it is reported as nameless.
    errs = vs.check_manifest_shape(root)
    check("a 'null' manifest reports as nameless, not as a type error",
          len(errs) == 1 and "declares no 'name'" in errs[0], True)

# --- skills[] entries must stay inside the plugin's own skills/ ---

def make_manifest_skills(root: Path, rel: str, name: str, skills):
    d = root / rel / ".claude-plugin"
    d.mkdir(parents=True, exist_ok=True)
    (d / "plugin.json").write_text(
        json.dumps({"name": name, "skills": skills}), encoding="utf-8")

with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    make_skill(root, "plugins/shared-workflows/victim/skills/shared-one")
    make_manifest_skills(root, "plugins/shared-workflows/victim", "victim",
                         ["./skills/shared-one"])
    make_manifest_skills(root, "plugins/groups/idm/thief", "idm-thief",
                         ["../../../shared-workflows/victim/skills/shared-one"])
    errs = vs.check_skills_listed(root)
    escape = [e for e in errs if "resolves outside" in e]
    check("a skills[] entry escaping the plugin is an error", len(escape), 1)
    check("the error names the offending entry",
          "../../../shared-workflows/victim/skills/shared-one" in escape[0], True)
    check("and explains two plugins would claim one skill",
          "claim the same skill" in escape[0], True)

with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    make_skill(root, "plugins/shared-workflows/ok/skills/a")
    make_manifest_skills(root, "plugins/shared-workflows/ok", "ok", ["./skills/a"])
    check("a normal in-plugin entry still passes", vs.check_skills_listed(root), [])

with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    make_manifest_skills(root, "plugins/shared-workflows/abs", "abs", ["/etc/passwd"])
    errs = vs.check_skills_listed(root)
    check("an absolute skills[] entry is rejected",
          any("absolute path" in e for e in errs), True)

with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    make_manifest_skills(root, "plugins/shared-workflows/bad", "bad", [{"path": "x"}])
    errs = vs.check_skills_listed(root)
    check("a non-string skills[] entry is rejected, not a crash",
          any("non-string entry" in e for e in errs), True)


# --- surface marker + description agreement ---


def surfaced_plugin(root, rel, name, surfaces, desc):
    """A plugin whose single skill declares `surfaces`, with `desc` in both copies."""
    d = root / rel / ".claude-plugin"
    d.mkdir(parents=True, exist_ok=True)
    (d / "plugin.json").write_text(
        json.dumps({"name": name, "description": desc,
                    "skills": ["./skills/s"]}), encoding="utf-8")
    sd = root / rel / "skills" / "s"
    sd.mkdir(parents=True, exist_ok=True)
    block = ["---", "name: s"]
    if surfaces:
        block += ["metadata:", f"  surfaces: {surfaces}"]
    block += ["---", "", "body", ""]
    (sd / "SKILL.md").write_text("\n".join(block), encoding="utf-8")


def registry(root, entries):
    """entries: list of (name, source, description)."""
    d = root / ".claude-plugin"
    d.mkdir(parents=True, exist_ok=True)
    f = d / "marketplace.json"
    f.write_text(json.dumps({"plugins": [
        {"name": n, "source": s, "description": desc} for n, s, desc in entries]}),
        encoding="utf-8")
    return f


BARE = "Does a thing."
MARKED = "[Claude Code only] Does a thing."
# Present but last, where a truncated list view drops it.
TRAILING = "Does a thing. Claude Code only - needs local filesystem access."

with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    surfaced_plugin(root, "plugins/groups/idm/tools", "idm-tools", "claude-code", BARE)
    mk = registry(root, [("idm-tools", "./plugins/groups/idm/tools", BARE)])
    errs, warns = vs.check_surface_marker(root, mk)
    check("a REGISTERED claude-code-only plugin without the marker is an error",
          (len(errs), len(warns)), (1, 0))
    check("and the message names the plugin", "idm-tools" in errs[0], True)

with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    surfaced_plugin(root, "plugins/groups/idm/tools", "idm-tools", "claude-code", BARE)
    mk = registry(root, [])
    errs, warns = vs.check_surface_marker(root, mk)
    check("an UNREGISTERED one is only a warning", (len(errs), len(warns)), (0, 1))

with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    surfaced_plugin(root, "plugins/groups/idm/tools", "idm-tools", "claude-code", MARKED)
    mk = registry(root, [("idm-tools", "./plugins/groups/idm/tools", MARKED)])
    check("a bracketed leading marker satisfies it",
          vs.check_surface_marker(root, mk), ([], []))

with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    surfaced_plugin(root, "plugins/groups/idm/tools", "idm-tools", "claude-code",
                    "Claude Code only - needs the local filesystem. Does a thing.")
    check("an unbracketed leading marker also satisfies it",
          vs.check_surface_marker(root, registry(root, [])), ([], []))

with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    surfaced_plugin(root, "plugins/groups/idm/tools", "idm-tools", "claude-code", TRAILING)
    mk = registry(root, [("idm-tools", "./plugins/groups/idm/tools", TRAILING)])
    errs, warns = vs.check_surface_marker(root, mk)
    check("a TRAILING marker is rejected", (len(errs), len(warns)), (1, 0))
    check("and the message explains it is a position problem, not a missing one",
          "not at the front" in errs[0], True)

with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    surfaced_plugin(root, "plugins/groups/idm/tools", "idm-tools", "claude-code",
                    "[CLAUDE CODE ONLY] Does a thing.")
    check("the marker match is case-insensitive",
          vs.check_surface_marker(root, registry(root, [])), ([], []))

with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    surfaced_plugin(root, "plugins/shared-workflows/rev", "rev",
                    "claude-code, claude-ai", BARE)
    mk = registry(root, [("rev", "./plugins/shared-workflows/rev", BARE)])
    check("a plugin with any claude-ai skill is exempt",
          vs.check_surface_marker(root, mk), ([], []))

with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    make_manifest(root, "plugins/foundation-wide/legal", "legal")
    mk = registry(root, [("legal", "./plugins/foundation-wide/legal", BARE)])
    check("a plugin with no skills is exempt",
          vs.check_surface_marker(root, mk), ([], []))

with tempfile.TemporaryDirectory() as td:
    # Marker added to the manifest only; the registry is stale.
    root = Path(td)
    surfaced_plugin(root, "plugins/groups/idm/tools", "idm-tools", "claude-code", MARKED)
    mk = registry(root, [("idm-tools", "./plugins/groups/idm/tools", BARE)])
    errs = vs.check_description_agreement(root, mk)
    check("descriptions that disagree are an error", len(errs), 1)
    check("and both texts are quoted so the diff is visible",
          MARKED in errs[0] and BARE in errs[0], True)

with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    surfaced_plugin(root, "plugins/groups/idm/tools", "idm-tools", "claude-code", MARKED)
    mk = registry(root, [("idm-tools", "./plugins/groups/idm/tools", MARKED)])
    check("matching descriptions pass", vs.check_description_agreement(root, mk), [])

with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    (root / "plugins").mkdir()
    mk = registry(root, [("ghost", "./plugins/groups/idm/gone", BARE)])
    check("an unresolvable registry entry is left to the registration check",
          vs.check_description_agreement(root, mk), [])

print(f"\n{passed}/{passed + failed} passed")
sys.exit(1 if failed else 0)
