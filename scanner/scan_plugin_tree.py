#!/usr/bin/env python3
"""Sweep the full plugin package with custom + cisco YARA rules.

cisco-ai-skill-scanner only inspects skills it discovers via SKILL.md, so the
other plugin components — hooks, subagents (agents/), slash commands (commands/),
and MCP connector configs (.mcp.json / plugin.json) — are never scanned. This
runs YARA rules (via yara-x, the engine the scanner uses) over the plugin trees
to close that gap:

  * our custom rules (scanner/custom-yara) run over EVERY file — tight, low-FP.
  * cisco's bundled core YARA rules (loaded from the installed skill-scanner
    package) run over NON-SKILL files only. Skills already get cisco's full
    engine via `skill-scanner scan-all`, so re-running raw rules over them would
    be redundant and could false-positive on legitimate code-heavy skills.

Exit non-zero on any match so it can gate merges. If the scanner isn't installed
(e.g. a bare local run), it falls back to the custom rules only.

With --sarif PATH it also writes SARIF 2.1.0, so findings reach GitHub code
scanning the same way `skill-scanner scan-all --format sarif` does: inline PR
annotations, an entry in the Security tab, history across runs, and dismissal
with a recorded reason. Printing `::error::` alone gets none of that.

Run:  pip install yara-x cisco-ai-skill-scanner && python scanner/scan_plugin_tree.py
      python scanner/scan_plugin_tree.py --sarif plugin-tree.sarif
"""
import argparse
import json
import os
import sys
from pathlib import Path

import yara_x

REPO = Path(__file__).resolve().parent.parent
CUSTOM_RULES = REPO / "scanner" / "custom-yara" / "gf_agent_safety.yara"
sys.path.insert(0, str(REPO / "scripts"))
from utils import PLUGIN_ROOTS  # noqa: E402

ROOTS = [REPO / r for r in PLUGIN_ROOTS]
SKIP_DIRS = {".git", ".venv", "node_modules", "__pycache__"}
MAX_BYTES = 2_000_000


def load_cisco_core_yara():
    """Combined source of the scanner's bundled core YARA rules + count, or None."""
    """Location: C:\\Users\\username\\AppData\\Roaming\\Python\\Python314\\site-packages\\skill_scanner\\data\\packs\\core\\yara"""
    try:
        import skill_scanner
    except Exception:
        return None
    yd = Path(os.path.dirname(skill_scanner.__file__)) / "data" / "packs" / "core" / "yara"
    if not yd.is_dir():
        return None
    srcs = [(yd / f).read_text(encoding="utf-8")
            for f in sorted(os.listdir(yd)) if f.endswith(".yara")]
    return ("\n\n".join(srcs), len(srcs)) if srcs else None


def skill_dirs():
    dirs = set()
    for root in ROOTS:
        if root.exists():
            for p in root.rglob("SKILL.md"):
                dirs.add(p.parent)
    return dirs


def iter_files():
    for root in ROOTS:
        if not root.exists():
            continue
        for p in root.rglob("*"):
            if p.is_dir() or any(part in SKIP_DIRS for part in p.parts):
                continue
            yield p


def match_line(data: bytes, rule) -> int:
    """1-based line of the rule's first pattern match, or 1 if unavailable.

    yara-x exposes rule.patterns[].matches[].offset. Degrade to line 1 rather
    than raising: a findings report that lands on the wrong line is still
    actionable, one that crashes the scan is not.
    """
    try:
        offsets = [m.offset for p in rule.patterns for m in p.matches]
    except Exception:
        return 1
    return data[:min(offsets)].count(b"\n") + 1 if offsets else 1


def write_sarif(path: Path, findings: list[dict]) -> None:
    """Write SARIF 2.1.0, including when there are no findings.

    The empty case matters: code scanning treats an upload as the complete
    current state for its category, so an empty run is what CLOSES alerts that
    have since been fixed. Skipping the write on a clean scan would leave stale
    alerts open forever and record no analysis for the branch.
    """
    rule_ids = sorted({f["rule"] for f in findings})
    sarif = {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [{
            "tool": {"driver": {
                "name": "Plugin-Tree-YARA",
                "informationUri": "https://github.com/gatesfoundation/gf-agent-publish",
                "rules": [{
                    "id": rid,
                    "shortDescription": {"text": f"YARA rule {rid} matched"},
                    "defaultConfiguration": {"level": "error"},
                } for rid in rule_ids],
            }},
            "results": [{
                "ruleId": f["rule"],
                "level": "error",
                "message": {"text": f"YARA rule {f['rule']} matched ({f['engine']} rules)"},
                "locations": [{"physicalLocation": {
                    "artifactLocation": {"uri": f["path"]},
                    "region": {"startLine": f["line"]},
                }}],
            } for f in findings],
        }],
    }
    path.write_text(json.dumps(sarif, indent=2), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sarif", metavar="PATH",
                    help="also write SARIF 2.1.0 here (for code scanning upload)")
    args = ap.parse_args()

    custom = yara_x.compile(CUSTOM_RULES.read_text(encoding="utf-8"))

    cisco = None
    loaded = load_cisco_core_yara()
    if loaded:
        cisco_src, cisco_n = loaded
        cisco = yara_x.compile(cisco_src)
        print(f"Loaded {cisco_n} cisco core YARA rules (applied to non-skill files).")
    else:
        print("cisco-ai-skill-scanner not installed — non-skill files get the custom rules only.")

    sdirs = skill_dirs()
    def in_skill(p: Path) -> bool:
        return any(d in p.parents for d in sdirs)

    findings, scanned, cisco_scanned = [], 0, 0
    for p in iter_files():
        try:
            data = p.read_bytes()
        except OSError:
            continue
        if len(data) > MAX_BYTES:
            continue
        scanned += 1
        rel = p.relative_to(REPO).as_posix()
        for r in yara_x.Scanner(custom).scan(data).matching_rules:
            findings.append({"path": rel, "rule": r.identifier,
                             "line": match_line(data, r), "engine": "custom"})
        if cisco is not None and not in_skill(p):
            cisco_scanned += 1
            for r in yara_x.Scanner(cisco).scan(data).matching_rules:
                findings.append({"path": rel, "rule": r.identifier,
                                 "line": match_line(data, r), "engine": "cisco core"})

    # Deduplicate on (path, rule, line) while keeping insertion order.
    seen, unique = set(), []
    for f in findings:
        key = (f["path"], f["rule"], f["line"])
        if key not in seen:
            seen.add(key)
            unique.append(f)

    if args.sarif:
        write_sarif(Path(args.sarif), unique)
        print(f"Wrote {len(unique)} finding(s) to {args.sarif}")

    if unique:
        print(f"::error::rule findings in plugin tree ({len(unique)}):")
        for f in sorted(unique, key=lambda x: (x["path"], x["rule"], x["line"])):
            # file=/line= makes this an inline annotation on the PR diff, not
            # just a message at the top of the run.
            print(f"::error file={f['path']},line={f['line']}::"
                  f"{f['rule']} ({f['engine']} rules)")
        return 1
    print(f"No findings. Scanned {scanned} files with custom rules; "
          f"{cisco_scanned} non-skill files also scanned with cisco core rules.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
