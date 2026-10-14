#!/usr/bin/env python3
"""Offline behavior test for governance_summary.build_record (no network / gh).

Run:  python scripts/tests/test_governance_summary.py
"""
import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location(
    "governance_summary", HERE.parent / "governance_summary.py")
gs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gs)

# governance_summary's own module-exec already put scripts/ on sys.path and
# cached "utils" in sys.modules, so this reuses that same module object.
from utils import ROOT_SHARED  # noqa: E402

# Imported for one assertion: that the stages this file proves unreachable are
# also absent from the card table. Two files have to agree for a stage to stop
# existing, and only one of them is under test here.
import teams_card as tc  # noqa: E402


def pr(draft=False):
    return {"number": 42, "title": "t", "html_url": "http://x/42",
            "user": {"login": "alice"}, "head": {"sha": "abc"},
            "base": {"ref": "main"}, "draft": draft}


def rv(user, state):
    return {"user": {"login": user}, "state": state}


def run(name, conclusion, status="completed"):
    return {"name": name, "conclusion": conclusion, "status": status}


# Every check in CHECK_MAP, all green. It must be complete: checks_passed now
# requires every tracked check to have reported, so a fixture missing one is
# "not green" and silently stops exercising the paths it was written for.
OK = [run("Validate skill structure and metadata", "success"),
      run("Run evals for changed skills", "success"),
      run("Scan skills for security red flags", "success"),
      run("YARA rule tests", "success"),
      run("Lint Python", "success")]

FOUNDATION_SKILL = f"{gs.ROOT_FOUNDATION}/skills/communications/grant-writing/SKILL.md"
GROUP_SKILL = f"{gs.ROOT_GROUPS}/idm/skills/software-tools/idm-pkg-install/SKILL.md"
SHARED_SKILL = f"{ROOT_SHARED}/grant-review/skills/reviewer/SKILL.md"

FW = [{"filename": FOUNDATION_SKILL}]
GRP = [{"filename": GROUP_SKILL}]

results = []
def check(desc, cond):
    results.append(cond)
    print(("PASS" if cond else "FAIL"), "-", desc)


def main():
    # No approval count anywhere in the record: nothing enforces one, so
    # "awaiting_approval" now means "no business owner has approved" and
    # nothing else. These pass no owner sets, so none can resolve.
    r = gs.build_record("o/r", pr(), FW, [rv("bob", "APPROVED")], OK)
    check("no owners resolved -> awaiting_approval, whoever approved",
          r["tier"] == "foundation-wide" and "required_approvals" not in r
          and r["overall"] == "awaiting_approval")

    r = gs.build_record("o/r", pr(), GRP,
                        [rv("carol", "APPROVED"), rv("dave", "APPROVED")], OK,
                        "", {"dave"}, {"carol"}, ["idm-leads"])
    check("business owner approved + checks pass -> ready",
          r["groups"] == ["idm"] and r["overall"] == "ready")

    r = gs.build_record("o/r", pr(), GRP, [rv("dave", "APPROVED")], OK,
                        "", {"dave"}, {"carol"}, ["idm-leads"])
    check("board approval alone is not the business owner's -> awaiting_approval",
          r["overall"] == "awaiting_approval")

    check("the record carries no approval threshold at all",
          "required_approvals" not in r and "satisfied" not in r["approvals"])

    bad = OK[:2] + [run("Scan skills for security red flags", "failure")]
    r = gs.build_record("o/r", pr(), GRP, [rv("carol", "APPROVED")], bad)
    check("failing security check -> checks_failing",
          r["overall"] == "checks_failing" and r["failed_checks"] == ["security-scan"])

    pend = OK[:2] + [run("Scan skills for security red flags", None, status="in_progress")]
    r = gs.build_record("o/r", pr(), GRP, [rv("carol", "APPROVED")], pend)
    check("in-progress check -> checks_pending", r["overall"] == "checks_pending")

    r = gs.build_record("o/r", pr(), GRP, [rv("carol", "CHANGES_REQUESTED")], OK)
    check("changes requested -> changes_requested",
          r["overall"] == "changes_requested" and r["approvals"]["changes_requested_by"] == ["carol"])

    r = gs.build_record("o/r", pr(draft=True), GRP, [], OK)
    check("draft PR -> draft", r["overall"] == "draft")

    r = gs.build_record("o/r", pr(), GRP, [rv("alice", "APPROVED")], OK)
    check("author self-approval excluded",
          r["approvals"]["count"] == 0 and r["overall"] == "awaiting_approval")

    md = gs.markdown(gs.build_record("o/r", pr(), FW, [rv("bob", "APPROVED")], OK))
    check("markdown renders", isinstance(md, str) and "Governance summary" in md)

    # ── Card stage (business owner vs governance board) ──────────────────────
    BOARD = {"dave"}          # governance board (skills-working-group)
    DOMAIN = {"carol"}        # domain team for plugins/groups/idm (idm-leads); erin is neither
    TEAMS = ["idm-leads"]

    def grp(reviews, domain=DOMAIN, teams=TEAMS, board=BOARD):
        return gs.build_record("o/r", pr(), GRP, reviews, OK, "", board, domain, teams)

    g = grp([rv("carol", "APPROVED")])["governance"]
    check("domain-team member sponsors -> intake (team labeled)",
          g["card_stage"] == "intake" and g["business_owner"] == "carol"
          and g["business_owner_teams"] == ["idm-leads"] and g["board_approvers"] == [])

    # grp() uses OK (all checks green) on a non-draft PR, so the derived ready
    # signal is on. The property under test is unchanged -- a non-domain
    # approval is NOT a sponsorship -- but the absence of intake now surfaces
    # as "ready" rather than "none", because the PR genuinely is ready to look
    # at. Asserting the stage is not intake keeps the original intent.
    g = grp([rv("erin", "APPROVED")])["governance"]
    check("strict: non-domain writer approves -> not a sponsorship",
          g["card_stage"] != "intake" and g["business_owner"] == "")

    # The pair of approvals that used to mean "approved". GitHub cannot see
    # the OneTrust decision or the attestation, so the furthest any GitHub
    # state can carry a PR is the business owner's approval -> intake.
    g = grp([rv("carol", "APPROVED"), rv("dave", "APPROVED")])["governance"]
    check("domain + board -> intake, never approved",
          g["card_stage"] == "intake" and g["board_approvers"] == ["dave"]
          and g["business_owner"] == "carol")

    g = grp([rv("dave", "APPROVED"), rv("carol", "APPROVED")])["governance"]
    check("order independence: board-first still -> intake",
          g["card_stage"] == "intake")

    # A board approval on its own used to be "awaiting_second" -- a progress
    # bar toward a count. With no count, the PR is simply still waiting on its
    # business owner, which is what "ready" says.
    g = grp([rv("dave", "APPROVED")])["governance"]
    check("board approval alone -> ready, not a stage of its own",
          g["card_stage"] == "ready" and g["board_approvers"] == ["dave"])

    # Foundation-wide: no distinct domain team -> business owner = any non-board
    g = gs.build_record("o/r", pr(), FW, [rv("erin", "APPROVED")], OK, "", BOARD,
                        frozenset(), [])["governance"]
    check("foundation-wide (no domain team) falls back to non-board -> intake",
          g["card_stage"] == "intake" and g["business_owner"] == "erin")

    g = gs.build_record("o/r", pr(), GRP, [rv("carol", "APPROVED")], OK)["governance"]  # nothing resolved
    check("nothing resolved -> no sponsorship claimed (fails safe)",
          g["card_stage"] in ("ready", "none") and g["business_owner"] == "")

    # The fallback needs a resolved board to subtract from. Without one it used
    # to hand the business-owner label to any approver, and nothing downstream
    # caught it once "approved" (which required a board approver) was gone.
    check("fallback requires a resolved board",
          gs.card_stage(["someone"], [], [], True)[0] == "ready"
          and gs.card_stage(["someone"], ["gov1"], [], True)[0] == "intake")

    check("'approved' and 'awaiting_second' are gone from the card table",
          not {"approved", "awaiting_second"} & set(tc.STAGE))

    # ── the "ready" stage: derived from state, not from a trigger ────────────
    # Reached when every tracked check is green and the PR is not a draft. It
    # is the only stage with no approval behind it, so it must never mask one,
    # and it must never fire while CI is red — a trigger-based signal could.
    BOARD_R, DOMAIN_R = ["gov1", "gov2"], ["biz1"]

    def stage_of(approvers, ready):
        return gs.card_stage(approvers, BOARD_R, DOMAIN_R, ready)[0]

    check("no approvals, checks not green -> none", stage_of([], False) == "none")
    check("no approvals, checks green -> ready", stage_of([], True) == "ready")
    check("a business-owner approval outranks ready",
          stage_of(["biz1"], True) == "intake")
    check("a board approval does NOT outrank ready any more",
          stage_of(["gov1"], True) == "ready")
    check("board plus business owner stops at intake",
          stage_of(["gov1", "biz1"], True) == "intake")
    check("a non-owner approval is no stage, so ready still wins",
          stage_of(["random"], True) == "ready")
    check("non-owner approval with red checks is still none",
          stage_of(["random"], False) == "none")
    check("ready_signal defaults off, so existing callers are unaffected",
          gs.card_stage([], BOARD_R, DOMAIN_R)[0] == "none")

    # End to end through build_record: the signal is derived there, so these
    # prove the wiring, not just the ladder.
    def stage_e2e(checks, draft, reviews=()):
        return gs.build_record("r", pr(draft), GRP, list(reviews), checks, "",
                               BOARD, DOMAIN, ("idm-leads",))["governance"]["card_stage"]

    check("green + not draft -> ready", stage_e2e(OK, False) == "ready")
    check("green + DRAFT -> none (author is still working)",
          stage_e2e(OK, True) == "none")
    check("no checks reported -> none, never ready",
          stage_e2e([], False) == "none")

    # ── the two "ready" refinements ──────────────────────────────────────────
    # Same gate as ready (green, not draft, no current approval) but with review
    # history behind it. Saying "ready for review" to somebody who already
    # reviewed hides the only thing they need: what changed since they looked.
    check("requested changes + green -> ready_changes",
          stage_e2e(OK, False, [rv("carol", "CHANGES_REQUESTED")]) == "ready_changes")
    check("dismissed approval + green -> ready_reapprove",
          stage_e2e(OK, False, [rv("carol", "DISMISSED")]) == "ready_reapprove")
    check("changes outrank a dismissal (an explicit block is more urgent)",
          stage_e2e(OK, False, [rv("carol", "CHANGES_REQUESTED"),
                                rv("dave", "DISMISSED")]) == "ready_changes")
    check("a current approval still outranks both",
          stage_e2e(OK, False, [rv("carol", "APPROVED")]) == "intake")

    # The refinements must not resurrect a card while CI is red or on a draft:
    # they are refinements of ready, not escapes from its gate.
    RED = [run("Validate skill structure and metadata", "failure")]
    check("requested changes + RED checks -> none",
          stage_e2e(RED, False, [rv("carol", "CHANGES_REQUESTED")]) == "none")
    check("dismissed approval + RED checks -> none",
          stage_e2e(RED, False, [rv("carol", "DISMISSED")]) == "none")
    check("requested changes on a DRAFT -> none",
          stage_e2e(OK, True, [rv("carol", "CHANGES_REQUESTED")]) == "none")

    # dismissed_by is surfaced so the card and summary can name the reviewer.
    rec = gs.build_record("o/r", pr(), GRP, [rv("carol", "DISMISSED")], OK, "",
                          BOARD, DOMAIN, ("idm-leads",))
    check("dismissed_by names the reviewer whose approval was dropped",
          rec["approvals"]["dismissed_by"] == ["carol"])
    check("a dismissal is not counted as an approval",
          rec["approvals"]["count"] == 0)

    # ── tier_of / groups_of / changed_skills, one path per root ──────────────
    # These call the classify_path-based helpers directly against the current
    # (prefixed) ROOT_* values. Before this commit these functions hardcoded
    # the pre-move "foundation-wide/" and "groups/" strings, so every one of
    # these would have misclassified once the roots moved under plugins/:
    # tier_of would always return "group", groups_of would always be empty,
    # and changed_skills would report tier "group" / group None for every PR.
    check("tier_of: a foundation-wide path is tier foundation-wide",
          gs.tier_of([FOUNDATION_SKILL]) == "foundation-wide")
    check("tier_of: a groups path is tier group",
          gs.tier_of([GROUP_SKILL]) == "group")
    check("tier_of: a shared-workflows path is tier group",
          gs.tier_of([SHARED_SKILL]) == "group")

    check("groups_of: a foundation-wide path has no group",
          gs.groups_of([FOUNDATION_SKILL]) == [])
    check("groups_of: a groups path names its team",
          gs.groups_of([GROUP_SKILL]) == ["idm"])
    check("groups_of: a shared-workflows path has no group",
          gs.groups_of([SHARED_SKILL]) == [])

    # Routing fields, compared as a subset: the entry also carries the fields the
    # card needs to describe the skill, and asserting the whole dict here would
    # make every future card field a test failure in a routing test.
    def routing(path):
        e = gs.changed_skills([path])[0]
        return {k: e[k] for k in ("path", "tier", "group")}

    check("changed_skills: foundation-wide skill is tier foundation-wide, group None",
          routing(FOUNDATION_SKILL) ==
          {"path": FOUNDATION_SKILL.rsplit("/", 1)[0], "tier": "foundation-wide", "group": None})
    check("changed_skills: groups skill is tier group, group idm",
          routing(GROUP_SKILL) ==
          {"path": GROUP_SKILL.rsplit("/", 1)[0], "tier": "group", "group": "idm"})
    check("changed_skills: shared-workflows skill is tier group, group None",
          routing(SHARED_SKILL) ==
          {"path": SHARED_SKILL.rsplit("/", 1)[0], "tier": "group", "group": None})

    # --- what the card needs to describe a skill ---
    # The governance reviewer is judging risk and fit. A path alone supports
    # neither, so the entry carries the name, whether it is new, the first
    # sentence of the description, and where it works. allowed-tools is
    # deliberately absent -- see the note in teams_card.card().

    SKILL_MD = """---
name: document-folder-summary
description: Use when someone points at a folder of documents and wants to know
  what is in it. Triggers include 'summarise these docs', 'what's in this folder'.
allowed-tools: Read, Grep, Glob, Bash
metadata:
  owner: idm
  version: "1.0.0"
  surfaces: claude-code
---

body
"""
    e = gs.changed_skills([GROUP_SKILL], {GROUP_SKILL: "added"},
                          lambda _p: SKILL_MD)[0]
    check("changed_skills: carries the skill's directory name",
          e["name"] == GROUP_SKILL.rsplit("/", 2)[-2])
    check("changed_skills: says a new skill is added",
          e["status"] == "added")
    check("changed_skills: summary is the description's first sentence only",
          e["summary"].startswith("Use when someone points at a folder")
          and "Triggers include" not in e["summary"])
    check("changed_skills: carries surfaces from metadata",
          e["surfaces"] == "claude-code")

    # surfaces may be a YAML list. str() of it rendered "['claude-code', ...]"
    # on the card instead of the comma list the reviewer reads elsewhere.
    e = gs.changed_skills([GROUP_SKILL], {}, lambda _p: SKILL_MD.replace(
        "surfaces: claude-code", "surfaces: [claude-code, claude-ai]"))[0]
    check("changed_skills: a YAML-list surfaces reads as a comma list",
          e["surfaces"] == "claude-code, claude-ai")

    # No fetcher, or a fetcher that finds nothing: the routing fields still
    # resolve and the descriptive ones stay empty. A card missing a summary is
    # a worse card; a governance run that failed over it is a worse outcome.
    e = gs.changed_skills([GROUP_SKILL], {}, lambda _p: None)[0]
    check("changed_skills: an unreadable SKILL.md degrades, not fails",
          (e["summary"], e["surfaces"]) == ("", ""))
    check("and routing still resolves without it", e["group"] == "idm")

    # Malformed frontmatter is validate_skill.py's error to report.
    e = gs.changed_skills([GROUP_SKILL], {}, lambda _p: "no frontmatter here")[0]
    check("changed_skills: frontmatter-less file does not raise", e["summary"] == "")

    # --- first_sentence ---
    check("first_sentence: stops at the first sentence",
          gs.first_sentence("Does a thing. And then another thing.") ==
          "Does a thing.")
    check("first_sentence: is not fooled by 'e.g.' mid-sentence",
          gs.first_sentence("Use when installing, e.g. pip install foo, a package.")
          .endswith("a package."))
    check("first_sentence: collapses the newlines YAML folding leaves behind",
          gs.first_sentence("Use when\n  someone asks. More.") == "Use when someone asks.")
    check("first_sentence: long single sentences are cut on a word boundary",
          gs.first_sentence("x " * 200).endswith("…"))
    check("first_sentence: empty stays empty", gs.first_sentence(None) == "")

    print(f"\n{sum(results)}/{len(results)} passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
