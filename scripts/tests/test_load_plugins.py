#!/usr/bin/env python3
"""Offline behavior test for utils.load_plugins (no network).

Run:  python scripts/tests/test_load_plugins.py
"""
import importlib.util
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("utils", HERE.parent / "utils.py")
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


def marketplace(tmp: Path, plugins) -> Path:
    p = tmp / "marketplace.json"
    p.write_text(json.dumps({"plugins": plugins}), encoding="utf-8")
    return p


with tempfile.TemporaryDirectory() as td:
    tmp = Path(td)

    # Same task basename under two teams must not collide.
    mk = marketplace(tmp, [
        {"name": "idm-data", "source": "./groups/idm/data"},
        {"name": "finance-data", "source": "./groups/finance/data"},
    ])
    keys = sorted(utils.load_plugins(mk).keys())
    check("declared names are the keys", keys, ["finance-data", "idm-data"])

    plugins = utils.load_plugins(mk)
    check("path resolves under the repo root",
          plugins["idm-data"].as_posix().endswith("groups/idm/data"), True)

    # An entry with no source is skipped; an entry with no name is skipped too,
    # since without a name it cannot be addressed.
    mk = marketplace(tmp, [
        {"name": "ok", "source": "./groups/a/b"},
        {"name": "no-source"},
        {"source": "./groups/c/d"},
    ])
    check("entries missing name or source are skipped",
          sorted(utils.load_plugins(mk).keys()), ["ok"])

print(f"\n{passed}/{passed + failed} passed")
sys.exit(1 if failed else 0)
