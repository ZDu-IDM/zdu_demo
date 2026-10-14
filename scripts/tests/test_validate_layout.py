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
    # The shared route must be covered too. A check that silently covers only
    # two of the three roots is the exact failure this plan exists to avoid.
    # Not redundant with the team-route case above: the algorithm is
    # root-agnostic, so this asserts shared-workflows is actually in
    # PLUGIN_ROOTS. Dropping it from the tuple makes this fail.
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
    # foundation-wide keeps its own shape: manifest at the top, skills nested
    # under a category. It must not be flagged.
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
    # A root listed in PLUGIN_ROOTS with no corresponding directory on disk
    # must be skipped, not crash. Inject a bogus root that does not exist
    # under `root`, and restore the constant afterwards so a failure here
    # can't leak into later tests.
    original_roots = vs.PLUGIN_ROOTS
    vs.PLUGIN_ROOTS = (*original_roots, "does-not-exist-on-disk")
    try:
        check("a root in PLUGIN_ROOTS with no directory on disk is skipped, not fatal",
              vs.check_plugin_manifests(root), [])
    finally:
        vs.PLUGIN_ROOTS = original_roots

with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    # A SKILL.md with no `skills/` ancestor can never be referenced by any
    # plugin manifest -- it is unreachable the same way an unmanifested
    # skills/ tree is. It must be flagged, not silently dropped.
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
    # The zero-skill suppression has been reversed: the five scaffold
    # manifests it existed to protect were deleted from the real catalog, so
    # it had no remaining beneficiary and it widened the I6 gap (an
    # unregistered, unlisted skill could hide behind "it's just scaffolding").
    # An unregistered plugin now warns unconditionally, scaffold or not.
    make_manifest(root, "plugins/groups/idm/empty-scaffold", "idm-empty-scaffold")
    mk = marketplace(root, [])
    errs, warns = vs.check_marketplace_registration(root, mk)
    check("an unregistered plugin with no SKILL.md now warns too",
          len(warns), 1)
    check("and not an error", errs, [])

with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    # A manifest that fails to parse is unambiguously broken -- nothing can
    # install it -- and no other check in the file validates plugin.json's
    # syntax, so this must surface as an error here, not be swallowed.
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
    # A parent plugin manifest with no skills/ of its own, wrapping a nested
    # plugin that does have one. Both are unregistered, so (with the zero-skill
    # suppression reversed) both now warn -- one warning per manifest, not one
    # warning double-counting the nested plugin's skill against the parent.
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
    # "plugins/groups" is the whole namespace: it resolves, and it sits
    # inside a plugin root (it IS a plugin root), so every existing check is
    # satisfied. Only the new manifest-presence check catches it.
    make_skill(root, "plugins/groups/idm/software-tools/skills/reviewer")
    make_manifest(root, "plugins/groups/idm/software-tools", "idm-software-tools")
    mk = marketplace(root, [{"name": "everything", "source": "./plugins/groups"}])
    errs, warns = vs.check_marketplace_registration(root, mk)
    check("registering the whole groups namespace is exactly one error", len(errs), 1)
    check("the error names the namespace path", "plugins/groups" in errs[0], True)

with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    # "plugins/groups/idm" is a team namespace -- one level more specific,
    # same defect: it resolves and sits inside a plugin root.
    make_skill(root, "plugins/groups/idm/software-tools/skills/reviewer")
    make_manifest(root, "plugins/groups/idm/software-tools", "idm-software-tools")
    mk = marketplace(root, [{"name": "team", "source": "./plugins/groups/idm"}])
    errs, warns = vs.check_marketplace_registration(root, mk)
    check("registering a team namespace is exactly one error", len(errs), 1)
    check("the error names the team namespace path", "plugins/groups/idm" in errs[0], True)

with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    # A real task plugin whose manifest name matches the entry name -- clean.
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
    # A real task plugin whose manifest declares a different name than the
    # entry registers it under -- the registry and the manifest disagree.
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
    # The manifest exists but is malformed JSON. _plugin_manifests already
    # reports this as a parse error; the name-comparison check must skip it
    # rather than double-reporting one broken manifest as two errors.
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
    # Regression guard: an unregistered plugin directory with a skill must
    # still warn, not error -- registration staying a warning (not an error)
    # must survive the new namespace/name checks untouched.
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
    # The shared-route rule is now an exact match against the plugin's own
    # directory name -- no dependence on which groups/* directories exist.
    # "policy-review" is a legitimate task name (it is not "the policy team's
    # thing"), and must pass even though groups/policy/ exists: the old
    # heuristic wrongly rejected exactly this case.
    (root / "plugins/groups" / "policy").mkdir(parents=True)
    make_manifest(root, "plugins/shared-workflows/policy-review", "policy-review")
    check("a shared plugin whose name equals its directory passes, "
          "even when a same-named team directory exists",
          vs.check_plugin_names(root), [])

with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    # No groups/policy/ directory exists here at all -- the old heuristic
    # needed one to catch a smuggled prefix; the exact-match rule does not,
    # so it is not state-coupled to what other teams happen to exist.
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
    # shared-workflows/ itself is the same shape at a shallower depth: a
    # manifest directly there recreates namespace-as-plugin, the shape this
    # restructure abolishes. Before this rule it passed validation green.
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

def make_manifest_with_skills(root: Path, rel: str, name: str, skills: list[str]) -> Path:
    d = root / rel / ".claude-plugin"
    d.mkdir(parents=True, exist_ok=True)
    p = d / "plugin.json"
    p.write_text(json.dumps({"name": name, "skills": skills}), encoding="utf-8")
    return p


# --- skills[] must match what's on disk, in both directions ---
with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    # A skill exists on disk but was never added to plugin.json's skills[].
    # Every other gate (structure, evals, security scan) passes and an
    # Infosec card would announce it, but nothing installs it.
    make_skill(root, "plugins/groups/idm/software-tools/skills/reviewer")
    make_skill(root, "plugins/groups/idm/software-tools/skills/unlisted")
    make_manifest_with_skills(root, "plugins/groups/idm/software-tools", "idm-software-tools",
                              ["./skills/reviewer"])
    errs = vs.check_skills_listed(root)
    check("an on-disk skill missing from skills[] is one error", len(errs), 1)
    check("the error names the unlisted skill directory",
          "plugins/groups/idm/software-tools/skills/unlisted" in errs[0], True)

with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    # skills[] lists an entry with no matching SKILL.md -- a manifest
    # pointing at nothing (typo'd path, or a skill deleted without updating
    # the manifest).
    make_skill(root, "plugins/groups/idm/software-tools/skills/reviewer")
    make_manifest_with_skills(root, "plugins/groups/idm/software-tools", "idm-software-tools",
                              ["./skills/reviewer", "./skills/does-not-exist"])
    errs = vs.check_skills_listed(root)
    check("a skills[] entry pointing at nothing is one error", len(errs), 1)
    check("the error names the dangling entry",
          "does-not-exist" in errs[0], True)

with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    # skills[] matches the tree exactly -- clean.
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
    # A directory that exists on disk, just outside the checkout -- not
    # merely nonexistent. This is the case a bare .exists() check would let
    # through if it resolved before the escape was caught: nothing would
    # ever YARA-scan it or send it an Infosec card, yet it would stay
    # published.
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
    # Inside the repo, but outside every plugin root -- e.g. a source
    # pointing at scripts/ or docs/. Never YARA-scanned, no Infosec card.
    (root / "not-a-plugin-root").mkdir()
    mk = marketplace(root, [{"name": "orphan", "source": "./not-a-plugin-root"}])
    errs, _ = vs.check_marketplace_registration(root, mk)
    check("a source inside the repo but outside every plugin root is an error",
          len(errs), 1)
    check("the error names the plugin roots",
          "plugins/foundation-wide" in errs[0] and "plugins/groups" in errs[0], True)

with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    # A normal, valid, repo-relative source under a plugin root must still
    # pass cleanly -- the new checks must not over-trigger on the good case.
    make_skill(root, "plugins/groups/idm/software-tools/skills/reviewer")
    make_manifest(root, "plugins/groups/idm/software-tools", "idm-software-tools")
    mk = marketplace(root, [
        {"name": "idm-software-tools", "source": "./plugins/groups/idm/software-tools"},
    ])
    errs, warns = vs.check_marketplace_registration(root, mk)
    check("a valid, repo-relative source under a plugin root is clean",
          (errs, warns), ([], []))

# --- the all-staff high-power-tools rule ---
# This is the highest-risk line in the classify_path conversion: if
# is_foundation ever stops matching "plugins/foundation-wide", a
# foundation-wide skill requesting Bash would pass validation instead of
# erroring, and no other test would catch it.
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
# Where a skill works cannot be derived from allowed-tools: a Read/Grep/Glob
# skill that globs the user's project still does nothing on Claude.ai, whose
# sandbox cannot see the local filesystem. Only the author knows, so the author
# declares it -- and a typo must not read as a deliberate restriction.
SKILL_DIR = vs.REPO_ROOT / "plugins/foundation-wide/x" / "__test_fake_skill__"


def surfaces_result(fm):
    """Run validate_surfaces on a frontmatter dict.

    `surfaces` is lifted into `metadata:` for the caller, so each case below
    reads as the one value it is testing rather than a nested literal.
    """
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

# A typo would otherwise read as "works nowhere" and silently exclude the skill
# from every surface a packaging step consults, so it is an error not a warning.
errs, _ = surfaces_result({"surfaces": "claude-desktop"})
check("an unknown surface is an error", len(errs), 1)
check("the error names the bad value", "claude-desktop" in errs[0], True)
check("and lists the valid ones", "claude-code" in errs[0], True)

errs, _ = surfaces_result({"surfaces": ""})
check("an empty surfaces value is an error", len(errs), 1)

# surfaces is required, and absence is reported ONCE -- by validate_frontmatter
# via REQUIRED_METADATA. validate_surfaces defers so one mistake is not
# counted twice, which is why it returns silently here.
check("validate_surfaces stays silent on absence, leaving it to the required-field check",
      surfaces_result({"name": "x"}), ([], []))
check("surfaces is in the required-metadata list", "surfaces" in vs.REQUIRED_METADATA, True)

# The generic 'missing field' message is not enough to act on here, so the
# required-field check carries a hint explaining the sandbox.
vs.errors.clear()
vs.validate_frontmatter(SKILL_DIR, {"name": "x", "description": "d",
                                    "allowed-tools": "Read",
                                    "metadata": {"owner": "o", "version": "1.0.0"}})
missing = [e for e in vs.errors if "surfaces" in e]
check("a missing surfaces field is exactly one error", len(missing), 1)
check("and the error explains the Claude.ai sandbox", "sandbox" in missing[0], True)
vs.errors.clear()

# --- the metadata split itself ---
# The GF fields moved under `metadata:` because the Agent Skills standard
# reserves the top level and strict validators reject unknown keys there on
# upload. A file written the old way must say so specifically: "missing required
# field: owner" would send someone to add a second copy of a line already present.


def frontmatter_errors(fm):
    vs.errors.clear()
    vs.validate_frontmatter(SKILL_DIR, fm)
    out = list(vs.errors)
    vs.errors.clear()
    return out


GOOD_FM = {"name": "x", "description": "d", "allowed-tools": "Read",
           "metadata": {"owner": "o", "version": "1.0.0", "surfaces": "claude-code"}}
check("a correctly nested skill passes", frontmatter_errors(GOOD_FM), [])

LEGACY_FM = {"name": "x", "description": "d", "allowed-tools": "Read",
             "owner": "o", "version": "1.0.0", "surfaces": "claude-code"}
errs = frontmatter_errors(LEGACY_FM)
check("the pre-migration shape is rejected", len(errs), 1)
check("and the error names every misplaced field",
      all(f in errs[0] for f in ("owner", "version", "surfaces")), True)
check("and says where they go", "metadata:" in errs[0], True)
# One mistake, one error: without the skip, each misplaced field would also be
# reported as absent, tripling the count and contradicting the first message.
check("a misplaced field is not also reported as missing",
      any("Missing required" in e for e in errs), False)

# allowed-tools deliberately stays top-level -- it is a documented Claude Code
# field and the security boundary, not ours to relocate.
check("allowed-tools is still required at the top level",
      "allowed-tools" in vs.REQUIRED_TOP_LEVEL, True)
check("and is not treated as a metadata field",
      "allowed-tools" in vs.REQUIRED_METADATA, False)

# A non-dict `metadata:` must not crash; it reads as the fields being absent.
errs = frontmatter_errors({"name": "x", "description": "d",
                           "allowed-tools": "Read", "metadata": "oops"})
check("a non-dict metadata reports the fields as missing, not a crash", len(errs), 3)

# --- all-staff read + network = an egress path ---
# HIGH_POWER covers execution and filesystem writes, not data leaving the
# machine. A skill that can Read a local file and WebFetch it to a URL reaches
# every staff machine at once and nothing else flags it. Warning, not error:
# the combination is often legitimate, but it should never be silent.
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


# allowed-tools may be a YAML list as well as a comma string -- both are valid
# frontmatter. str() of a list split on commas produced "['Read'" and failed
# every tool, so a list-form skill could not pass at all.
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

# Each half alone is harmless: nothing local to send, or nowhere to send it.
check("WebFetch with no read tool does not warn",
      [w for w in tools_warnings(FW, "WebFetch")[1] if "read locally" in w], [])
check("read tools with no network access do not warn",
      [w for w in tools_warnings(FW, "Read, Grep, Glob")[1] if "read locally" in w], [])

# Scoped to all-staff. A group skill has a named owning team and a smaller
# blast radius, so the pair is theirs to judge.
check("a group skill with the same pair does not get this warning",
      [w for w in tools_warnings(GRP, "Read, Grep, WebFetch")[1] if "read locally" in w], [])

# WebSearch is the other half of EGRESS_TOOLS and must behave the same.
check("WebSearch counts as egress too",
      len([w for w in tools_warnings(FW, "Read, WebSearch")[1] if "read locally" in w]), 1)

# --- duplicate declared names ---
# The layout permits bare names on two routes, so foundation-wide/legal and
# shared-workflows/legal can both declare "legal". load_plugins keys on the
# name, so without this check one of them disappears with nothing reported.

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
    # Three colliding manifests are still ONE error, not three: the report is
    # per-name, so a contributor sees one line naming every offender.
    for r in ("plugins/foundation-wide/a", "plugins/shared-workflows/a",
              "plugins/groups/idm/a"):
        make_manifest(root, r, "dup")
    errs = vs.check_duplicate_plugin_names(root, marketplace(root, []))
    check("three colliding manifests report as one error", len(errs), 1)
    check("that error counts all three", "3 plugins declare" in errs[0], True)

with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    # A duplicate in the REGISTRY is a separate failure from a duplicate on
    # disk: two entries can name the same plugin even when every manifest is
    # unique, and the second is simply unreachable.
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
    # An unparseable manifest declares no name to collide with, and is already
    # reported elsewhere — it must not be double-counted here.
    d = root / "plugins/shared-workflows/broken/.claude-plugin"
    d.mkdir(parents=True)
    (d / "plugin.json").write_text("{not json", encoding="utf-8")
    make_manifest(root, "plugins/shared-workflows/ok", "ok")
    check("an unparseable manifest is skipped, not double-reported",
          vs.check_duplicate_plugin_names(root, marketplace(root, [])), [])

# --- a manifest that parses but is not an object ---
# This used to crash validate_skill.py with AttributeError on .get(), so CI
# failed with a traceback instead of a diagnostic and no other check ran.

with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    d = root / "plugins/shared-workflows/listy/.claude-plugin"
    d.mkdir(parents=True)
    (d / "plugin.json").write_text("[1, 2, 3]", encoding="utf-8")
    errs, _ = vs.check_marketplace_registration(root, marketplace(root, []))
    check("a JSON array manifest is one error, not a crash", len(errs), 1)
    check("the error says it must be an object", "must be a JSON object" in errs[0], True)
    check("and names the actual type", "list" in errs[0], True)
    # The other checks must survive it rather than raising.
    check("check_plugin_names survives a non-object manifest",
          vs.check_plugin_names(root), [])
    check("check_manifest_shape survives it", vs.check_manifest_shape(root), [])
    check("check_skills_listed survives it", vs.check_skills_listed(root), [])

with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    d = root / "plugins/shared-workflows/nully/.claude-plugin"
    d.mkdir(parents=True)
    (d / "plugin.json").write_text("null", encoding="utf-8")
    # `null` is treated as an empty object, so it falls through to the
    # existing nameless-manifest rule rather than the type error.
    errs = vs.check_manifest_shape(root)
    check("a 'null' manifest reports as nameless, not as a type error",
          len(errs) == 1 and "declares no 'name'" in errs[0], True)

# --- skills[] containment ---
# Without this, "../../../other/skills/x" resolved, its SKILL.md existed, and
# every check passed -- so two plugins could claim one skill silently. The
# on-disk scan is bounded to the plugin's own skills/, so an escaped entry was
# invisible in both directions.

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
# A Claude-Code-only plugin installs cleanly on Claude.ai and then does nothing.
# The admin deciding which groups get it sees a name and a description, so the
# description is the only place that warning can reach them.


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
# Present, but at the end — where a truncated admin list view drops it first.
# This is why the rule checks position and not merely presence.
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
    # Same plugin, not registered. Nobody reads the description yet, so the
    # omission cannot mislead anyone -- warn, do not block.
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
    # Unbracketed but still leading. The rule is about position, not punctuation.
    root = Path(td)
    surfaced_plugin(root, "plugins/groups/idm/tools", "idm-tools", "claude-code",
                    "Claude Code only - needs the local filesystem. Does a thing.")
    check("an unbracketed leading marker also satisfies it",
          vs.check_surface_marker(root, registry(root, [])), ([], []))

with tempfile.TemporaryDirectory() as td:
    # The case the position rule exists for: marker present, but last.
    root = Path(td)
    surfaced_plugin(root, "plugins/groups/idm/tools", "idm-tools", "claude-code", TRAILING)
    mk = registry(root, [("idm-tools", "./plugins/groups/idm/tools", TRAILING)])
    errs, warns = vs.check_surface_marker(root, mk)
    check("a TRAILING marker is rejected", (len(errs), len(warns)), (1, 0))
    check("and the message explains it is a position problem, not a missing one",
          "not at the front" in errs[0], True)

with tempfile.TemporaryDirectory() as td:
    # Case must not matter: authors write "Claude Code only" in prose.
    root = Path(td)
    surfaced_plugin(root, "plugins/groups/idm/tools", "idm-tools", "claude-code",
                    "[CLAUDE CODE ONLY] Does a thing.")
    check("the marker match is case-insensitive",
          vs.check_surface_marker(root, registry(root, [])), ([], []))

with tempfile.TemporaryDirectory() as td:
    # One portable skill means the plugin DOES work there. "Claude Code only"
    # would be the false statement, so this must stay silent.
    root = Path(td)
    surfaced_plugin(root, "plugins/shared-workflows/rev", "rev",
                    "claude-code, claude-ai", BARE)
    mk = registry(root, [("rev", "./plugins/shared-workflows/rev", BARE)])
    check("a plugin with any claude-ai skill is exempt",
          vs.check_surface_marker(root, mk), ([], []))

with tempfile.TemporaryDirectory() as td:
    # No skills: nothing to install on either surface, so nothing to warn about.
    root = Path(td)
    make_manifest(root, "plugins/foundation-wide/legal", "legal")
    mk = registry(root, [("legal", "./plugins/foundation-wide/legal", BARE)])
    check("a plugin with no skills is exempt",
          vs.check_surface_marker(root, mk), ([], []))

with tempfile.TemporaryDirectory() as td:
    # The half-done edit the agreement rule exists for: marker added to the
    # manifest, registry left stale, so the console still shows the bare text.
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
    # An entry pointing at nothing is check_marketplace_registration's to report;
    # reporting it here too would double-count one mistake.
    root = Path(td)
    (root / "plugins").mkdir()
    mk = registry(root, [("ghost", "./plugins/groups/idm/gone", BARE)])
    check("an unresolvable registry entry is left to the registration check",
          vs.check_description_agreement(root, mk), [])

print(f"\n{passed}/{passed + failed} passed")
sys.exit(1 if failed else 0)
