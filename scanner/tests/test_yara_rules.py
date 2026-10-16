#!/usr/bin/env python3
"""Behavior contract for the GF custom YARA rules.

Uses yara-x — the SAME engine cisco-ai-skill-scanner runs — so this test mirrors
CI exactly. Compiles scanner/custom-yara/gf_agent_safety.yara and asserts each
malicious fixture matches its rule (including a browser-theft sample wrapped in a
``` code fence — the regression the old whole-file exclusions silenced) and that
benign fixtures match nothing.

Run:  pip install yara-x && python scanner/tests/test_yara_rules.py
"""
import sys
from pathlib import Path

import yara_x

HERE = Path(__file__).parent
RULES = HERE.parent / "custom-yara" / "gf_agent_safety.yara"
FX = HERE / "fixtures"

EXPECT_MATCH = {
    "malicious/agent_memory.md": "vetter_agent_memory_theft",
    "malicious/ip_exfil.md": "vetter_ip_exfiltration",
    "malicious/browser_theft_codefence.md": "vetter_browser_data_theft",
}
EXPECT_CLEAN = ["benign/normal_skill.md", "benign/cookie_policy_doc.md"]


def matching(rules, data: bytes) -> set:
    return {r.identifier for r in yara_x.Scanner(rules).scan(data).matching_rules}


def main() -> int:
    rules = yara_x.compile(RULES.read_text(encoding="utf-8"))
    failures = []

    for rel, expected in EXPECT_MATCH.items():
        hits = matching(rules, (FX / rel).read_bytes())
        if expected not in hits:
            failures.append(f"{rel}: expected {expected!r}, got {sorted(hits) or 'no match'}")

    for rel in EXPECT_CLEAN:
        hits = matching(rules, (FX / rel).read_bytes())
        if hits:
            failures.append(f"{rel}: expected no match, got {sorted(hits)}")

    if failures:
        print("YARA RULE TEST FAILURES:")
        for f in failures:
            print("  -", f)
        return 1
    print(f"All YARA rule tests passed ({len(EXPECT_MATCH)} match, {len(EXPECT_CLEAN)} clean).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
