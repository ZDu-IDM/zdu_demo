#!/usr/bin/env python3
"""
generate_evals.py — Auto-generate evals.json for a skill using Claude.

Usage:
    python generate_evals.py --skill path/to/SKILL.md
    python generate_evals.py --skill path/to/SKILL.md --count 6
    python generate_evals.py --skill path/to/SKILL.md --output path/to/evals.json
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

import anthropic


def read_skill(skill_path: str) -> str:
    path = Path(skill_path)
    if not path.exists():
        print(f"Error: SKILL.md not found at {skill_path}")
        sys.exit(1)
    return path.read_text(encoding="utf-8")


def extract_skill_name(skill_content: str, skill_path: str) -> str:
    """Extract skill name from frontmatter, fallback to folder name."""
    match = re.search(r"^name:\s*(.+)$", skill_content, re.MULTILINE)
    if match:
        return match.group(1).strip()
    # Fallback: use parent folder name
    return Path(skill_path).parent.name


def generate_evals(skill_content: str, skill_name: str, count: int) -> dict:
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

    prompt = f"""You are an expert at writing high-quality evals for AI skills.

Given the following SKILL.md, generate {count} meaningful eval test cases in JSON format.

SKILL.md content:
---
{skill_content}
---

Generate {count} eval cases that:
1. Test real behavior described in the skill's Instructions section
2. Cover a range of scenarios: normal cases, edge cases, and failure/limitation cases
3. Are specific enough that a rubric can objectively pass or fail them
4. Are NOT trivially passing (don't just check if the word "yes" appears)

Return ONLY a valid JSON object with this exact schema, no preamble or markdown:
{{
  "skill_name": "{skill_name}",
  "evals": [
    {{
      "id": "XXX-001",
      "name": "Short descriptive name of what is being tested",
      "description": "What this eval tests and why it matters",
      "prompt": "The exact prompt to send to the agent",
      "rubric": {{
        "must_include_one_of": ["word1", "phrase2"],
        "must_include": ["required_term1", "required_term2"],
        "must_not_include": ["forbidden_term1"],
        "pass_condition": "Plain English description of what a passing response looks like"
      }}
    }}
  ]
}}

Rules:
- Use a 3-letter prefix for IDs based on the skill name (e.g. "rls" for release-process → RLS-001)
- rubric fields are optional — only include the ones that make sense for each eval
- must_include: every item must appear in the response
- must_include_one_of: at least one item must appear
- must_not_include: none of these should appear
- Write at least one eval that tests a clean/happy path
- Write at least one eval that tests an edge case or limitation
- Make prompts realistic — write them as a real user would"""

    message = client.messages.create(
        model="claude-sonnet-5",
        max_tokens=4000,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = message.content[0].text.strip()

    # Strip markdown code fences if present
    raw = re.sub(r"^```json\s*", "", raw)
    raw = re.sub(r"^```\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)

    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"Error: Claude returned invalid JSON: {e}")
        print("Raw response:")
        print(raw)
        sys.exit(1)


def write_evals(evals: dict, skill_path: str, output_path: str = None) -> str:
    if output_path:
        out = Path(output_path)
    else:
        # Default: write next to SKILL.md in evals/evals.json
        skill_dir = Path(skill_path).parent
        out = skill_dir / "evals" / "evals.json"

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(evals, indent=2), encoding="utf-8")
    return str(out)


def main():
    parser = argparse.ArgumentParser(
        description="Generate evals.json for a skill using Claude."
    )
    parser.add_argument(
        "--skill",
        required=True,
        help="Path to the SKILL.md file (e.g. plugins/groups/idm/software-tools/skills/idm-pkg-install/SKILL.md)",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=5,
        help="Number of eval cases to generate (default: 5, minimum: 3)",
    )
    parser.add_argument(
        "--output",
        help="Output path for evals.json (default: evals/evals.json next to SKILL.md)",
    )
    parser.add_argument(
        "--print",
        action="store_true",
        help="Print the generated evals to stdout instead of writing to file",
    )
    args = parser.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("Error: ANTHROPIC_API_KEY environment variable is not set.")
        sys.exit(1)

    count = max(3, args.count)  # Enforce minimum of 3

    print(f"Reading skill from: {args.skill}")
    skill_content = read_skill(args.skill)
    skill_name = extract_skill_name(skill_content, args.skill)
    print(f"Skill name: {skill_name}")
    print(f"Generating {count} evals...")

    evals = generate_evals(skill_content, skill_name, count)

    actual_count = len(evals.get("evals", []))
    print(f"Generated {actual_count} evals.")

    if args.print:
        print(json.dumps(evals, indent=2))
    else:
        out_path = write_evals(evals, args.skill, args.output)
        print(f"Written to: {out_path}")


if __name__ == "__main__":
    main()
