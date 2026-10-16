#!/usr/bin/env python3
"""Tests for the Teams cards: the Infosec skill card and the governance card.

Run:  python scripts/tests/test_teams_card.py
"""
import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent
sys.path.insert(0, str(SCRIPTS))  # so the module can `from utils import ...`

spec = importlib.util.spec_from_file_location("teams_card", SCRIPTS / "teams_card.py")
teams_card = importlib.util.module_from_spec(spec)
spec.loader.exec_module(teams_card)

passed = failed = 0


def check(name, got, want):
    global passed, failed
    if got == want:
        passed += 1
        print(f"PASS - {name}")
    else:
        failed += 1
        print(f"FAIL - {name}\n        got:  {got!r}\n        want: {want!r}")


def audience_of(skill_path):
    fm = {"name": "s", "description": "d"}
    c = teams_card.skill_card(fm, skill_path)
    # Find the FactSet by type, not index: the body layout changes.
    body = c["attachments"][0]["content"]["body"]
    facts = next(b["facts"] for b in body if b["type"] == "FactSet")
    return next(f["value"] for f in facts if f["title"] == "Audience")


def tools_of(fm):
    c = teams_card.skill_card(dict({"name": "s", "description": "d"}, **fm),
                              "plugins/groups/idm/x/skills/s/SKILL.md")
    body = c["attachments"][0]["content"]["body"]
    facts = next(b["facts"] for b in body if b["type"] == "FactSet")
    return next(f["value"] for f in facts if f["title"] == "Tools it can use")


check("a YAML-list allowed-tools reads as a comma list",
      tools_of({"allowed-tools": ["Read", "Grep"]}), "Read, Grep")
check("a comma-string allowed-tools is unchanged",
      tools_of({"allowed-tools": "Read, Grep"}), "Read, Grep")
check("absent allowed-tools still says it inherits all tools",
      tools_of({}), "not declared (inherits all tools)")

def owner_of(author=None, md_path=None, fm=None):
    if md_path:
        c = teams_card.skill_card_from_file(md_path)
    else:
        c = teams_card.skill_card(fm or {"name": "s", "description": "d"},
                                  "plugins/groups/idm/x/skills/s/SKILL.md", author=author)
    body = c["attachments"][0]["content"]["body"]
    facts = next(b["facts"] for b in body if b["type"] == "FactSet")
    return next(f["value"] for f in facts if f["title"] == "Owner")


# Owner comes from the plugin's optional author.
check("Owner shows the plugin author's name and email",
      owner_of({"name": "IDM", "email": "idm@x.org"}), "IDM <idm@x.org>")
check("Owner says not declared without an author", owner_of(None), "not declared")
check("a SKILL.md owner is no longer read",
      owner_of(fm={"name": "s", "description": "d", "metadata": {"owner": "legacy"}}),
      "not declared")

import json  # noqa: E402
import tempfile  # noqa: E402

with tempfile.TemporaryDirectory() as td:
    plugin = Path(td) / "plugins/groups/idm/demo"
    (plugin / ".claude-plugin").mkdir(parents=True)
    (plugin / ".claude-plugin/plugin.json").write_text(
        json.dumps({"name": "idm-demo", "author": {"name": "IDM"}}), encoding="utf-8")
    md = plugin / "skills/s/SKILL.md"
    md.parent.mkdir(parents=True)
    md.write_text("---\nname: s\ndescription: d\n---\nbody\n", encoding="utf-8")
    check("a card built from a file finds its plugin's author",
          owner_of(md_path=md), "IDM")

check("foundation-wide skill path",
      audience_of("plugins/foundation-wide/skills/demo/SKILL.md"),
      "All staff (foundation-wide)")

check("team skill path extracts the team name",
      audience_of("plugins/groups/idm/skills/software-tools/SKILL.md"),
      "Group: idm")

check("shared-workflows skill path",
      audience_of("plugins/shared-workflows/workflow-a/skills/cross-team-review/SKILL.md"),
      "Shared across teams")

check("a path outside every root",
      audience_of("scripts/teams_card.py"),
      "—")

# --- every governance card_stage must have a STAGE entry ---
for stage in ("ready", "ready_changes", "ready_reapprove", "intake"):
    check(f"STAGE has an entry for {stage!r}", stage in teams_card.STAGE, True)

# Removed stages must not linger: "approved" would claim a sign-off GitHub can't see.
for gone in ("awaiting_second", "approved"):
    check(f"STAGE has no entry for the removed {gone!r}",
          gone in teams_card.STAGE, False)

# Each entry must be the 4-tuple card() unpacks: header, style, button, subtitle.
for stage, entry in teams_card.STAGE.items():
    check(f"{stage!r} entry is a 4-tuple of non-empty strings",
          len(entry) == 4 and all(isinstance(x, str) and x for x in entry[:2] + entry[2:3]),
          True)

# --- headers say "Skill" only when the PR changes one ---

def _rec(stage, skills):
    return {
        "pr": {"number": 1, "title": "t", "url": "u", "author": "a",
               "base": "main", "head_sha": "s", "draft": False},
        "approvals": {"count": 0, "approvers": [],
                      "changes_requested_by": [], "dismissed_by": []},
        "tier": "group", "groups": ["idm"],
        "checks": [{"check": "validate", "conclusion": "success"}],
        "checks_passed": True, "overall": "awaiting_approval",
        "changed_skills": skills,
        "governance": {"card_stage": stage, "board_approvers": [],
                       "business_owner": ""},
    }


def _header(stage, skills):
    c = teams_card.card(_rec(stage, skills))
    return c["attachments"][0]["content"]["body"][0]["items"][0]["text"]


SKILL = [{"path": "plugins/groups/idm/x/skills/y", "tier": "group", "group": "idm"}]

check("a skill PR says Skill", "Skill ready for review" in _header("ready", SKILL), True)
check("a tooling-only PR says PR", "PR ready for review" in _header("ready", []), True)
check("and never says Skill", "Skill" not in _header("ready", []), True)

check("intake lowercases Skill", "New skill" in _header("intake", SKILL), True)
check("intake keeps PR uppercase", "New PR" in _header("intake", []), True)

# --- "Approved by" lists every approver once, the business owner annotated ---

def _facts(approvers, business="", board=(), teams=()):
    rec = _rec("intake", SKILL)
    rec["approvals"]["approvers"] = list(approvers)
    rec["approvals"]["count"] = len(approvers)
    rec["governance"]["business_owner"] = business
    rec["governance"]["board_approvers"] = list(board)
    rec["governance"]["business_owner_teams"] = list(teams)
    for b in teams_card.card(rec)["attachments"][0]["content"]["body"]:
        if b["type"] == "FactSet":
            return {f["title"]: f["value"] for f in b["facts"]}
    return {}

check("every approver is listed, the business owner annotated in place",
      _facts(["biz", "gov1"], business="biz", board=["gov1"])["Approved by"],
      "biz (business owner), gov1")

check("the sole approver still shows when they are the business owner",
      _facts(["biz"], business="biz")["Approved by"], "biz (business owner)")

check("no separate row repeats the business owner's name",
      "Business owner" in _facts(["biz"], business="biz"), False)

check("the owner's team rides in the annotation when one resolved",
      _facts(["biz"], business="biz", teams=["idm-leads"])["Approved by"],
      "biz (business owner, idm-leads)")

check("an annotation needs an approval -- an unresolved owner adds nothing",
      _facts(["gov1"], business="")["Approved by"], "gov1")

check("no approvals says so rather than going blank",
      _facts([])["Approved by"], "nobody yet")

check("no count and no tick in the value",
      any(c in _facts(["biz", "gov1"], business="biz")["Approved by"]
          for c in ("/", "✅", "❌")), False)

check("case difference still matches the owner for annotation",
      _facts(["Biz"], business="biz")["Approved by"], "Biz (business owner)")

check("no 'Governance' row claims a sign-off GitHub cannot see",
      "Governance" in _facts(["gov1"], board=["gov1"]), False)
check("never renders 'New pr'", "New pr" not in _header("intake", []), True)

check("a placeholderless header is unchanged",
      _header("ready_changes", []) == teams_card.STAGE["ready_changes"][0], True)


# --- the changed-skills block, and its cap ---

NL = chr(10)


def skills_block(n, **over):
    """card()'s changed-skills text for a PR touching `n` skills."""
    skills = []
    for k in range(1, n + 1):
        s = {"path": f"plugins/groups/idm/x/skills/skill-{k:02d}",
             "name": f"skill-{k:02d}", "tier": "group", "group": "idm",
             "status": "modified", "summary": f"Does thing {k}.",
             "surfaces": "claude-code"}
        s.update(over)
        skills.append(s)
    rec = {"pr": {"number": 1, "title": "t", "url": "u", "author": "a",
                  "base": "main", "head_sha": "0" * 40, "draft": False},
           "repo": "o/r", "tier": "group", "groups": ["idm"],
           "approvals": {"count": 0, "approvers": []},
           "checks": [], "overall": "ready", "changed_skills": skills,
           "governance": {"card_stage": "ready"}}
    body = teams_card.card(rec)["attachments"][0]["content"]["body"]
    return next(b["text"] for b in body if "skill-01" in str(b.get("text", "")))


def skills_element(n=1):
    rec_body = None
    for b in teams_card.card({
            "pr": {"number": 1, "title": "t", "url": "u", "author": "a",
                   "base": "main", "head_sha": "0" * 40, "draft": False},
            "repo": "o/r", "tier": "group", "groups": ["idm"],
            "approvals": {"count": 0, "approvers": []},
            "checks": [], "overall": "ready",
            "changed_skills": [{"path": "p/skills/s", "name": "s", "tier": "group",
                                "group": "idm", "status": "modified",
                                "summary": "Does a thing.", "surfaces": "claude-code"}],
            "governance": {"card_stage": "ready"},
    })["attachments"][0]["content"]["body"]:
        if "**s**" in str(b.get("text", "")):
            rec_body = b
    return rec_body


check("the skill block is separated from the checks above it",
      skills_element().get("separator"), True)


def no_skill_body():
    return teams_card.card({
        "pr": {"number": 1, "title": "t", "url": "u", "author": "a",
               "base": "main", "head_sha": "0" * 40, "draft": False},
        "repo": "o/r", "tier": "group", "groups": [],        "approvals": {"count": 0, "approvers": []},
        "checks": [], "overall": "ready", "changed_skills": [],
        "governance": {"card_stage": "ready"},
    })["attachments"][0]["content"]["body"]


# The header sits inside a Container's items, so scan those too.
texts = []
for b in no_skill_body():
    texts.append(str(b.get("text", "")))
    texts += [str(i.get("text", "")) for i in b.get("items", [])]
check("a PR with no skills says so explicitly",
      any("No skills changed" in x for x in texts), True)
check("and the header calls it a PR, not a Skill",
      any("PR ready for review" in x for x in texts), True)
check("and it is separated like the skill block is",
      next(b for b in no_skill_body() if "No skills changed" in str(b.get("text", "")))
      .get("separator"), True)

blk = skills_block(1)
check("the block carries the summary", "Does thing 1." in blk, True)
check("but not allowed-tools", "Tools:" in blk, False)
check("and the surface it runs on", "Runs on: claude-code" in blk, True)
check("one skill needs no remainder line", "and 1 more" in blk, False)

check("SKILL_BLOCK_CAP skills still all get a block",
      sum(1 for l in skills_block(3).split(NL) if l.startswith("**skill-")), 3)
check("and no remainder line at exactly the cap",
      "_and " in skills_block(3), False)

blk = skills_block(4)
check("past the cap, only the cap gets blocks",
      sum(1 for l in blk.split(NL) if l.startswith("**skill-")), 3)
check("the remainder is counted, not dropped", "_and 1 more: skill-04_" in blk, True)

blk = skills_block(20)
check("a large remainder still states the true count", "_and 17 more:" in blk, True)
check("and elides the names rather than listing 17", blk.rstrip().endswith(", …_"), True)
check("no doubled conjunction in the remainder line",
      "and 17 more" in blk and "others" not in blk, True)

check("an added skill is marked new", "_(idm, new)_" in skills_block(1, status="added"), True)
check("a modified skill is not labelled", "_(idm)_" in skills_block(1), True)

blk = skills_block(1, summary="", surfaces="")
check("a skill with no readable frontmatter still names itself",
      "**skill-01**" in blk and "Runs on:" not in blk, True)

print(f"\n{passed}/{passed + failed} passed")
sys.exit(1 if failed else 0)
