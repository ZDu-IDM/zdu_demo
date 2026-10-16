#!/usr/bin/env python3
"""Tests for askops_request's parsing and display helpers (no network, no PyYAML).

Run:  python scripts/tests/test_askops_request.py
"""
import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("askops_request",
                                              HERE.parent / "askops_request.py")
ar = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ar)

passed = failed = 0


def check(name, got, want):
    global passed, failed
    if got == want:
        passed += 1
        print(f"PASS - {name}")
    else:
        failed += 1
        print(f"FAIL - {name}\n        got:  {got!r}\n        want: {want!r}")


def block(inner):
    return "Some prose.\n\n<!-- ASKOPS:START -->\n" + inner + "\n<!-- ASKOPS:END -->\n"


def answers(inner):
    return ar.parse_block(block(inner))


# --- the shape the template produces: answer on the line below the heading ---
TEMPLATE = """**Intended users -- which roles, and roughly how many?**
About 20.

**Target date or urgency**
Oct 1.
"""
check("template shape, answer below the heading",
      answers(TEMPLATE), {"users": "About 20.", "urgency": "Oct 1."})

# --- the answer written on the heading line itself (regression, PR #65) ---
INLINE_BOLD = """**Intended users -- which roles, and roughly how many?**: None
**Target date or urgency**: Oct 1, 2026
"""
check("inline answer after bold heading",
      answers(INLINE_BOLD), {"users": "None", "urgency": "Oct 1, 2026"})

INLINE_PLAIN = """Intended users: about 20 contributors
Target date: mid-October
"""
check("inline answer, no bold at all",
      answers(INLINE_PLAIN),
      {"users": "about 20 contributors", "urgency": "mid-October"})

# --- headings count whatever their formatting ---
HEADINGS = """### Intended users
About 20.

### Target date or urgency
Oct 1.
"""
check("markdown headings count as headings",
      answers(HEADINGS), {"users": "About 20.", "urgency": "Oct 1."})

check("case does not matter",
      answers("- intended users: everyone\n- TARGET DATE: today"),
      {"users": "everyone", "urgency": "today"})

# --- an inline answer plus continuation lines is one answer, not two ---
CONTINUED = """**Intended users**: about 20,
mostly IDM
**Target date**: Oct 1
"""
check("inline answer joins its continuation lines",
      answers(CONTINUED), {"users": "about 20, mostly IDM", "urgency": "Oct 1"})

# --- the optional third answer ---
check("business justification is picked up when written",
      answers("Business justification: Saves a manual step.\n"
              "Intended users: 20\nTarget date: Oct 1")["justification"],
      "Saves a manual step.")

check("business justification is optional, not required",
      sorted(answers("Intended users: 20\nTarget date: Oct 1")),
      ["urgency", "users"])

# --- blank or missing answers do not raise: the email shows them as not provided ---
check("an empty form parses to blank answers",
      answers("**Intended users**\n\n**Target date or urgency**\n"),
      {"users": "", "urgency": ""})

check("one blank field is blank on its own",
      answers("Intended users: 20\nTarget date:"), {"users": "20", "urgency": ""})

check("a missing block parses to no answers",
      ar.parse_block("A PR description with no markers at all."), {})

# --- instructions inside the HTML comments are not answers ---
COMMENTED = """**Intended users**
<!-- e.g. "about 30 analysts on the finance team" -->
Six economists.

**Target date**
<!-- a date or "no deadline" -->
Oct 1.
"""
check("comment guidance is stripped, not collected",
      answers(COMMENTED), {"users": "Six economists.", "urgency": "Oct 1."})

# --- list-or-string frontmatter reads the same in the email ---
check("a YAML list reads as a comma list",
      ar.listed_text(["Read", "Grep"], "x"), "Read, Grep")
check("a comma string is unchanged",
      ar.listed_text("Read, Grep", "x"), "Read, Grep")
check("absent falls back to the given text",
      ar.listed_text(None, "not declared"), "not declared")
check("an empty list falls back too",
      ar.listed_text([], "not declared"), "not declared")

# --- the owner comes from plugin.json's optional author ---
check("author with name and email",
      ar.author_text({"name": "IDM", "email": "idm@x.org"}, "nd"), "IDM <idm@x.org>")
check("author with only a name", ar.author_text({"name": "IDM"}, "nd"), "IDM")
check("no author says not declared", ar.author_text(None, "not declared"), "not declared")
check("a string author is not trusted as a name",
      ar.author_text("IDM <idm@x.org>", "not declared"), "not declared")

import json  # noqa: E402
import tempfile  # noqa: E402

with tempfile.TemporaryDirectory() as td:
    skill_dir = "plugins/groups/idm/demo/skills/s"
    manifest = Path(td) / "plugins/groups/idm/demo/.claude-plugin/plugin.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({"name": "idm-demo", "author": {"name": "IDM"}}),
                        encoding="utf-8")
    # Only the PR head has a new plugin's manifest.
    got = ar.manifest_for(skill_dir, head_root=td)
    check("the head copy of plugin.json is read", got.get("name"), "idm-demo")
    check("and carries the author", ar.author_text(got.get("author"), "nd"), "IDM")
    check("a plugin with no manifest anywhere reads as {}",
          ar.manifest_for("plugins/groups/idm/none/skills/s", head_root=td), {})

# --- the gate's reasons become the email's "Why this request" line ---
with tempfile.TemporaryDirectory() as td:
    f = Path(td) / "reasons.txt"
    f.write_text("new skill: plugins/groups/idm/x/skills/y\nMINOR version bump: idm-x (1.0.0 -> 1.1.0)\n",
                 encoding="utf-8")
    check("reasons are joined into one line",
          ar.reasons_line(str(f)),
          "new skill: plugins/groups/idm/x/skills/y; MINOR version bump: idm-x (1.0.0 -> 1.1.0)")
    f.write_text("escalated by model review: Now rejects files over 10 MB.\n", encoding="utf-8")
    check("an escalation keeps its label",
          ar.reasons_line(str(f)).startswith("escalated by model review"), True)
check("no reasons file says so", ar.reasons_line(None), "not recorded")

# --- build(): a request that is due is sent, whatever the PR touches ---
import argparse  # noqa: E402
import os  # noqa: E402

BODY = ("<!-- ASKOPS:START -->\n**Intended users**\nAbout 20.\n\n"
        "**Target date or urgency**\nOct 1.\n<!-- ASKOPS:END -->\n")
PLUGIN = "plugins/groups/idm/demo"


def run_build(changed, base_files, head_files=None, registry_head=None, body=BODY,
              removed=()):
    """build() in a temp checkout; returns the payload, or the ValueError text."""
    old = os.getcwd()
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        for rel, text in base_files.items():
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text(text, encoding="utf-8")
        for rel, text in (head_files or {}).items():
            (root / "head" / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / "head" / rel).write_text(text, encoding="utf-8")
        (root / "pr.md").write_text(body, encoding="utf-8")
        (root / "changed.txt").write_text("\n".join(changed) + "\n", encoding="utf-8")
        if registry_head is not None:
            (root / "reg-head.json").write_text(json.dumps(registry_head), encoding="utf-8")
        (root / "removed.txt").write_text("".join(f"{r}\n" for r in removed), encoding="utf-8")
        args = argparse.Namespace(
            pr_body="pr.md", pr_number="7", pr_url="https://x/7", pr_author="sc",
            changed_files="changed.txt", head_root="head",
            marketplace_head="reg-head.json" if registry_head is not None else None,
            brief=None, reasons=None, removed_files="removed.txt")
        os.chdir(root)
        try:
            return ar.build(args)
        except ValueError as e:
            return str(e)
        finally:
            os.chdir(old)


MANIFEST = f"{PLUGIN}/.claude-plugin/plugin.json"
SKILL = f"{PLUGIN}/skills/s/SKILL.md"
SKILL_TEXT = "---\nname: s\ndescription: d\nallowed-tools: Read\n---\n"
PJ = json.dumps({"name": "idm-demo", "version": "1.0.0", "author": {"name": "IDM"}})
BASE = {MANIFEST: PJ, SKILL: SKILL_TEXT}


def has(payload, text):
    return isinstance(payload, dict) and text in payload["content"]


got = run_build([f"{PLUGIN}/hooks/hooks.json"], BASE)
check("a hooks-only PR builds a request for the plugin's skills",
      (has(got, "Skill/Plugin name & source/link: s (plugin: idm-demo)"),
       has(got, "The idm group")), (True, True))

got = run_build([MANIFEST], BASE, {MANIFEST: json.dumps(
    {"name": "idm-demo", "version": "1.1.0", "author": {"name": "IDM"}})})
check("a plugin.json-only MINOR bump builds a request", has(got, "s (plugin: idm-demo)"), True)

REG = {"plugins": [{"name": "idm-demo", "source": f"./{PLUGIN}"}]}
got = run_build([".claude-plugin/marketplace.json"],
                {**BASE, ".claude-plugin/marketplace.json": json.dumps(REG)},
                registry_head={"plugins": []})
check("a registry removal builds a request from the base checkout",
      has(got, "s (plugin: idm-demo)"), True)

NEW = f"{PLUGIN}2/.claude-plugin/plugin.json"
got = run_build([NEW], {}, {NEW: json.dumps({"name": "idm-demo2", "author": {"name": "IDM"}})})
check("a skill-less new plugin builds a plugin-level request",
      (has(got, "Skill/Plugin name & source/link: idm-demo2 "),
       has(got, "idm-demo2: no skills, plugin-level change"),
       got["_skills"][0]["path"] if isinstance(got, dict) else got),
      (True, True, f"{PLUGIN}2"))

got = run_build([NEW], {}, {NEW: ""})
check("a 0-byte head manifest does not raise either", isinstance(got, dict), True)

got = run_build(["README.md"], BASE)
check("a PR owned by no plugin still builds, naming nothing",
      has(got, "not identified"), True)

got = run_build([SKILL], BASE, {SKILL: ""})
check("a 0-byte head SKILL.md is read from the base copy",
      has(got, "Skill/Plugin name & source/link: s "), True)

got = run_build([SKILL, MANIFEST], BASE, removed=[SKILL, MANIFEST])
check("a deleted plugin's skill is marked removed in the name and the tools lines",
      (has(got, "Skill/Plugin name & source/link: s (plugin: idm-demo, removed) — "),
       has(got, "- s (removed): declared tools Read")), (True, True))
check("a PR that only removes leaves no intended users",
      has(got, "Intended users (who, how many): none (everything listed is being removed)"), True)
OTHER = "plugins/groups/acctops/x/skills/u/SKILL.md"
OTHER_PJ = "plugins/groups/acctops/x/.claude-plugin/plugin.json"
got = run_build([SKILL, MANIFEST, OTHER],
                {**BASE, OTHER_PJ: json.dumps({"name": "acctops-x", "version": "1.0.0"})},
                {OTHER: SKILL_TEXT.replace("name: s", "name: u")}, removed=[SKILL, MANIFEST])
check("a removed skill's group is left out of intended users",
      has(got, "Intended users (who, how many): The acctops group — "), True)
got = run_build([SKILL], BASE, {SKILL: SKILL_TEXT + "x\n"})
check("a changed skill that still exists is not marked removed",
      (has(got, "s (plugin: idm-demo) — "), has(got, "removed")), (True, False))

SKILL2 = f"{PLUGIN}/skills/t/SKILL.md"
got = run_build([SKILL, SKILL2], {**BASE, SKILL2: SKILL_TEXT.replace("name: s", "name: t")},
                {SKILL: SKILL_TEXT, SKILL2: SKILL_TEXT.replace("name: s", "name: t")})
check("two skills from one group name the group once",
      has(got, "Intended users (who, how many): The idm group — "), True)

got = run_build([f"{PLUGIN}/hooks/hooks.json"], BASE, body="no block here")
check("a missing ASKOPS block still sends, answers marked not provided",
      (has(got, "Intended users (who, how many): The idm group — _(answers not provided)_"),
       has(got, "Target date/urgency: _(answers not provided)_")), (True, True))

got = run_build([f"{PLUGIN}/hooks/hooks.json"], BASE,
                body="<!-- ASKOPS:START -->\n**Intended users**\n\n**Target date**\nOct 1.\n"
                     "<!-- ASKOPS:END -->")
check("a blank answer is marked, the other kept",
      (has(got, "— _(answers not provided)_"), has(got, "Target date/urgency: Oct 1.")),
      (True, True))

# --- main(): content build() cannot read still sends a minimal request ---
with tempfile.TemporaryDirectory() as td:
    root, old = Path(td), os.getcwd()
    (root / "head" / SKILL).parent.mkdir(parents=True)
    (root / "head" / SKILL).write_text("---\n- a\n- list\n---\n", encoding="utf-8")
    (root / "pr.md").write_text(BODY, encoding="utf-8")
    (root / "changed.txt").write_text(SKILL + "\n", encoding="utf-8")
    (root / "reasons.txt").write_text(f"new skill: {PLUGIN}/skills/s\n", encoding="utf-8")
    os.chdir(root)
    try:
        rc = ar.main(["--pr-body", "pr.md", "--pr-number", "7", "--pr-url", "https://x/7",
                      "--pr-author", "sc", "--changed-files", "changed.txt",
                      "--head-root", "head", "--reasons", "reasons.txt", "--output", "out.json"])
        content = json.loads((root / "out.json").read_text(encoding="utf-8"))["content"]
    finally:
        os.chdir(old)
check("frontmatter that is a YAML list still sends a minimal request",
      (rc, "could not be derived" in content, f"Why this request: new skill: {PLUGIN}/skills/s"
       in content), (0, True, True))

print(f"\n{passed}/{passed + failed} passed")
sys.exit(1 if failed else 0)
