#!/usr/bin/env python3
"""Offline behavior test for semgrep_report (no semgrep, no network).

Two facts about Semgrep's SARIF: severity lives on the rule, not the result;
and `# nosemgrep` findings stay in the file, marked `suppressions`.

Run:  python scripts/tests/test_semgrep_report.py
"""
import importlib.util
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("semgrep_report", SCRIPTS / "semgrep_report.py")
sr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sr)

passed = failed = 0


def check(name, got, want):
    global passed, failed
    if got == want:
        passed += 1
        print(f"PASS - {name}")
    else:
        failed += 1
        print(f"FAIL - {name}\n        got:  {got!r}\n        want: {want!r}")


def sarif(rules, results) -> dict:
    """Build a SARIF doc. `rules` is {rule_id: level}."""
    return {
        "runs": [{
            "tool": {"driver": {"rules": [
                {"id": rid, "defaultConfiguration": {"level": lvl}}
                for rid, lvl in rules.items()
            ]}},
            "results": results,
        }]
    }


def result(rule_id, uri, line, suppressed=False) -> dict:
    """A result with NO `level` key — which is what Semgrep actually emits."""
    r = {
        "ruleId": rule_id,
        "locations": [{"physicalLocation": {
            "artifactLocation": {"uri": uri},
            "region": {"startLine": line},
        }}],
    }
    if suppressed:
        r["suppressions"] = [{"kind": "inSource"}]
    return r


def write(tmp: Path, doc: dict) -> Path:
    p = tmp / "semgrep.sarif"
    p.write_text(json.dumps(doc), encoding="utf-8")
    return p


with tempfile.TemporaryDirectory() as td:
    tmp = Path(td)

    # --- severity resolution ---
    p = write(tmp, sarif({"r.err": "error", "r.warn": "warning"},
                         [result("r.err", "a.py", 1), result("r.warn", "b.py", 2)]))
    active, suppressed = sr.load_findings(p)
    check("severity is resolved from the rule table, not the result",
          sorted(f["level"] for f in active), ["error", "warning"])
    check("no suppressed findings here", suppressed, [])
    check("counts bucket by resolved level",
          sr.counts_by_level(active), {"error": 1, "warning": 1})

    p = write(tmp, sarif({}, [result("r.missing", "a.py", 1)]))
    active, _ = sr.load_findings(p)
    check("a rule absent from the table resolves to 'none', not 'error'",
          active[0]["level"], "none")

    # --- suppression handling ---
    p = write(tmp, sarif({"r.err": "error"},
                         [result("r.err", "a.py", 1, suppressed=True)]))
    active, suppressed = sr.load_findings(p)
    check("a suppressed finding is not active", active, [])
    check("but is still reported as suppressed", len(suppressed), 1)

    # A suppressed error must not trip the gate.
    p = write(tmp, sarif({"r.err": "error"}, [
        result("r.err", "suppressed.py", 1, suppressed=True),
        result("r.err", "active.py", 2),
    ]))
    active, suppressed = sr.load_findings(p)
    check("only the unsuppressed error is active",
          [f["uri"] for f in active], ["active.py"])

    # --- location extraction ---
    p = write(tmp, sarif({"r.warn": "warning"}, [result("r.warn", "x/y.py", 42)]))
    active, _ = sr.load_findings(p)
    check("uri is extracted", active[0]["uri"], "x/y.py")
    check("line is extracted", active[0]["line"], 42)

    p = write(tmp, sarif({"r.warn": "warning"}, [{"ruleId": "r.warn"}]))
    active, _ = sr.load_findings(p)
    check("a result with no locations degrades rather than raising",
          (active[0]["uri"], active[0]["line"]), ("?", "?"))

    # --- multiple runs in one file ---
    doc = sarif({"r.err": "error"}, [result("r.err", "a.py", 1)])
    doc["runs"].append(sarif({"r.warn": "warning"},
                             [result("r.warn", "b.py", 2)])["runs"][0])
    p = write(tmp, doc)
    active, _ = sr.load_findings(p)
    check("findings from every run are collected", len(active), 2)
    check("and each keeps its own run's severity",
          sr.counts_by_level(active), {"error": 1, "warning": 1})

    # --- empty and rule-less documents ---
    check("an empty runs list yields nothing",
          sr.load_findings(write(tmp, {"runs": []})), ([], []))
    check("a run with no results yields nothing",
          sr.load_findings(write(tmp, sarif({"r.err": "error"}, []))), ([], []))

print(f"\n{passed}/{passed + failed} passed")
sys.exit(1 if failed else 0)
