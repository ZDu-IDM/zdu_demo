#!/usr/bin/env python3
"""Every consumer of the plugin-root list must see the same roots, from utils.

A consumer with its own copy can miss a new root silently.

Run:  python scripts/tests/test_plugin_roots.py
"""
import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent
REPO = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))

passed = failed = 0


def check(name, got, want):
    global passed, failed
    if got == want:
        passed += 1
        print(f"PASS - {name}")
    else:
        failed += 1
        print(f"FAIL - {name}\n        got:  {got!r}\n        want: {want!r}")


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    # Register before exec_module, so consumers' `from utils import ...` reuses
    # this utils module; a second copy would break the identity checks below.
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


utils = load("utils", SCRIPTS / "utils.py")
expected = set(utils.PLUGIN_ROOTS)

check("shared-workflows is a plugin root", utils.ROOT_SHARED in expected, True)
check("all three roots are present", expected,
      {utils.ROOT_FOUNDATION, utils.ROOT_GROUPS, utils.ROOT_SHARED})

check("utils declares a non-empty root list", len(expected) > 0, True)

csv_ = load("check_plugin_versions", SCRIPTS / "check_plugin_versions.py")
check("check_plugin_versions sees the same roots", set(csv_.PLUGIN_ROOTS), expected)

pic = load("preview_infosec_cards", SCRIPTS / "preview_infosec_cards.py")
check("preview_infosec_cards sees the same roots",
      {r.rstrip("/") for r in pic.PLUGIN_ROOTS}, expected)

vs = load("validate_skill", SCRIPTS / "validate_skill.py")
check("validate_skill sees the same roots", set(vs.SKILL_ROOTS), expected)

spt = load("scan_plugin_tree", REPO / "scanner" / "scan_plugin_tree.py")
check("scan_plugin_tree sees the same roots",
      {p.relative_to(REPO).as_posix() for p in spt.ROOTS}, expected)

spc = load("split_plugin_changes", SCRIPTS / "split_plugin_changes.py")
check("split_plugin_changes sees the same roots", set(spc.PLUGIN_ROOTS), expected)

check("check_plugin_versions imports the constant, not a copy",
      csv_.PLUGIN_ROOTS is utils.PLUGIN_ROOTS, True)
check("validate_skill imports the constant, not a copy",
      vs.SKILL_ROOTS is utils.PLUGIN_ROOTS, True)
check("split_plugin_changes imports the constant, not a copy",
      spc.PLUGIN_ROOTS is utils.PLUGIN_ROOTS, True)

print(f"\n{passed}/{passed + failed} passed")
sys.exit(1 if failed else 0)
