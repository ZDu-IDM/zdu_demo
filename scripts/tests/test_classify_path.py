#!/usr/bin/env python3
"""Behavior test for utils.classify_path.

classify_path exists so no consumer ever indexes parts[0] against a root name:
a plugin root is about to become several path components deep
("plugins/groups"), and parts[0] would then silently stop matching -- an
unscanned root, or the all-staff high-power-tools rule silently not firing,
both green in CI. This test is what proves the classifier (and therefore the
upcoming plugins/ move) actually works for a multi-component root.

Run:  python scripts/tests/test_classify_path.py
"""
import importlib.util
import sys
from pathlib import Path, PurePosixPath

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent

spec = importlib.util.spec_from_file_location("utils", SCRIPTS / "utils.py")
utils = importlib.util.module_from_spec(spec)
spec.loader.exec_module(utils)

passed = failed = 0


def check(name, got, want):
    global passed, failed
    if got == want:
        passed += 1
        print(f"PASS - {name}")
    else:
        failed += 1
        print(f"FAIL - {name}\n        got:  {got!r}\n        want: {want!r}")


classify_path = utils.classify_path
FOUNDATION = utils.ROOT_FOUNDATION
GROUPS = utils.ROOT_GROUPS
SHARED = utils.ROOT_SHARED

# Each of the three roots, at its own depth (nothing below the root).
check("foundation-wide at its own depth",
      classify_path(FOUNDATION), (FOUNDATION, ()))
check("groups at its own depth",
      classify_path(GROUPS), (GROUPS, ()))
check("shared-workflows at its own depth",
      classify_path(SHARED), (SHARED, ()))

# A path below a root.
check("a path below groups",
      classify_path(f"{GROUPS}/idm/software-tools"),
      (GROUPS, ("idm", "software-tools")))
check("a path below shared-workflows",
      classify_path(f"{SHARED}/x/skills/y"),
      (SHARED, ("x", "skills", "y")))

# A path outside every root.
check("a path outside every root is None",
      classify_path("scripts/deploy.py"), None)
check("an empty-ish unrelated path is None",
      classify_path("README.md"), None)

# A path equal to a root (Path form, not just str).
check("a Path equal to a root",
      classify_path(Path(GROUPS)), (GROUPS, ()))

# Backslash input (Windows-style paths must normalise to "/").
check("backslash input normalises correctly",
      classify_path(GROUPS.replace("/", "\\") + "\\idm\\software-tools"),
      (GROUPS, ("idm", "software-tools")))

# PurePosixPath input.
check("PurePosixPath input",
      classify_path(PurePosixPath(f"{GROUPS}/idm")), (GROUPS, ("idm",)))

# Prove the classifier still works if a root has two components -- this is
# what makes the upcoming plugins/ move a three-line change. Patch
# PLUGIN_ROOTS temporarily and restore it in a finally, so a failure here
# can't leak into other tests (or other processes importing this same file
# object, since PLUGIN_ROOTS is mutated in place on the module).
original_roots = utils.PLUGIN_ROOTS
utils.PLUGIN_ROOTS = ("plugins/groups",)
try:
    check("a two-component root classifies correctly",
          classify_path("plugins/groups/idm/x"),
          ("plugins/groups", ("idm", "x")))
    check("a two-component root path outside it is still None",
          classify_path("groups/idm/x"), None)
finally:
    utils.PLUGIN_ROOTS = original_roots

check("PLUGIN_ROOTS is restored after the patch test",
      utils.PLUGIN_ROOTS, original_roots)

print(f"\n{passed}/{passed + failed} passed")
sys.exit(1 if failed else 0)
