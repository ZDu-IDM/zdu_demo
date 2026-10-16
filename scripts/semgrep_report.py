#!/usr/bin/env python3
"""
semgrep_report.py — summarize a Semgrep SARIF file, and optionally gate on it.

  python scripts/semgrep_report.py semgrep.sarif                   # report, always exit 0
  python scripts/semgrep_report.py semgrep.sarif --fail-on-error   # exit 1 on error-level

Two things about Semgrep's SARIF drive this whole file, and both were previously
gotten wrong by an inline heredoc in semgrep.yml that no test could see:

1. Severity is on the RULE, not the result. Every result's own `level` key is
   absent, so `result.get("level")` is always None. The old summary bucketed
   every finding as "none", and a gate written the obvious way
   (`result["level"] == "error"`) would have matched nothing — a required check
   that can never fail.

2. Suppressed findings stay in the SARIF, tagged with a `suppressions` array,
   rather than being dropped. They must be filtered out or a correctly
   `# nosemgrep`-ed line reads as an active finding.
"""
import argparse
import json
import sys
from pathlib import Path

# Same guard as validate_skill.py and run_evals.py: this prints ✓/✗ and an em
# dash, and Windows defaults to cp1252, which raises UnicodeEncodeError on all
# three. Without it the gate exits non-zero on a crash rather than on a finding
# — right code, wrong reason, and it would have looked like it worked.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# SARIF levels Semgrep emits, worst first.
LEVELS = ("error", "warning", "note")


def load_findings(sarif_path: Path):
    """Return (active, suppressed) finding dicts, each with a resolved severity.

    Each returned item is {"level", "uri", "line", "rule"}. Severity is looked
    up through the run's rule table, which is where Semgrep actually records it.
    """
    data = json.loads(sarif_path.read_text(encoding="utf-8"))
    active, suppressed = [], []
    for run in data.get("runs", []):
        levels = {
            r["id"]: (r.get("defaultConfiguration") or {}).get("level", "none")
            for r in (run.get("tool", {}).get("driver", {}).get("rules") or [])
        }
        for r in run.get("results", []):
            locs = r.get("locations") or [{}]
            phys = locs[0].get("physicalLocation", {})
            item = {
                "level": levels.get(r.get("ruleId"), "none"),
                "uri": phys.get("artifactLocation", {}).get("uri", "?"),
                "line": (phys.get("region") or {}).get("startLine", "?"),
                "rule": r.get("ruleId", "?"),
            }
            (suppressed if r.get("suppressions") else active).append(item)
    return active, suppressed


def counts_by_level(findings) -> dict:
    out = {}
    for f in findings:
        out[f["level"]] = out.get(f["level"], 0) + 1
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("sarif", help="path to the SARIF file")
    ap.add_argument("--fail-on-error", action="store_true",
                    help="exit 1 if any unsuppressed finding is error-level")
    args = ap.parse_args()

    path = Path(args.sarif)
    if not path.exists():
        # A missing SARIF means the scan step did not get far enough to write
        # one. That step's own exit code is what reports it; do not double-fail.
        print(f"No SARIF at {path} — nothing to report.")
        return 0

    active, suppressed = load_findings(path)
    by_level = counts_by_level(active)
    errors = [f for f in active if f["level"] == "error"]

    if args.fail_on_error:
        if errors:
            print(f"✗  {len(errors)} error-severity Semgrep finding(s):")
            for f in errors:
                print(f"     {f['uri']}:{f['line']}  {f['rule']}")
            print("\nFix them, or add an inline `# nosemgrep: <rule-id>` with a "
                  "comment saying why it is safe.")
            return 1
        print(f"✓  No error-severity Semgrep findings "
              f"({len(active)} lower-severity, {len(suppressed)} suppressed).")
        return 0

    print(f"Semgrep findings: {len(active)} active, {len(suppressed)} suppressed")
    for level in LEVELS:
        if level in by_level:
            print(f"  {level}: {by_level[level]}")
    for level, n in sorted(by_level.items()):
        if level not in LEVELS:
            print(f"  {level}: {n}")
    for f in active:
        print(f"  -> {f['uri']}:{f['line']}  {f['rule']}  [{f['level']}]")
    if suppressed:
        print("suppressed (reviewed, see inline reason):")
        for f in suppressed:
            print(f"  -- {f['uri']}:{f['line']}  {f['rule']}  [{f['level']}]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
