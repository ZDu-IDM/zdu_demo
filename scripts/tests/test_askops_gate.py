#!/usr/bin/env python3
"""Offline tests for askops_gate (no network, no gh).

Run:  python scripts/tests/test_askops_gate.py
"""
import importlib.util
import json
import sys
import tempfile
from pathlib import Path, PurePosixPath

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("askops_gate", SCRIPTS / "askops_gate.py")
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)

passed = failed = 0


def check(name, got, want):
    global passed, failed
    if got == want:
        passed += 1
        print(f"PASS - {name}")
    else:
        failed += 1
        print(f"FAIL - {name}\n        got:  {got!r}\n        want: {want!r}")


PLUGIN = "plugins/groups/idm/demo"
MD = f"{PLUGIN}/skills/s/SKILL.md"
MF = f"{PLUGIN}/.claude-plugin/plugin.json"
SKILL_DIR = f"{PLUGIN}/skills/s"


def skill(tools="Read", surfaces=None, body="body"):
    meta = f"metadata:\n  surfaces: {surfaces}\n" if surfaces else ""
    return f"---\nname: s\ndescription: Use when testing.\nallowed-tools: {tools}\n{meta}---\n\n{body}\n"


def manifest(version="1.0.0", **extra):
    return json.dumps({"name": "idm-demo", "version": version, **extra})


def tree(root, files):
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")


def reasons(changed, base_files, head_files, base_reg=None, head_reg=None, removed=frozenset()):
    with tempfile.TemporaryDirectory() as td:
        base, head = Path(td) / "base", Path(td) / "head"
        base.mkdir()
        head.mkdir()
        tree(base, base_files)
        tree(head, head_files)
        return gate.rule_reasons(changed, base, head, base_reg, head_reg, removed)


BASE = {MF: manifest("1.0.0"), MD: skill()}

# --- silent: a PATCH-bumped wording fix ---
check("a PATCH-bumped typo fix fires no rule",
      reasons([MD, MF], BASE, {MD: skill(body="bodyy"), MF: manifest("1.0.1")}), [])

# --- rule 1 and 2: new skill, new plugin ---
SKILL_NAME = PurePosixPath(SKILL_DIR).name
check("a new skill fires rule 1, named with its plugin",
      reasons([MD], {MF: manifest()}, {MD: skill()}), [f"1 new skill in idm-demo: {SKILL_NAME}"])
check("a new plugin fires rule 2, with its skills grouped in one reason",
      reasons([MF, MD], {}, {MF: manifest(), MD: skill()}),
      [f"new plugin: idm-demo (1 new skill: {SKILL_NAME})"])
check("grouping does not depend on file order",
      reasons([MD, MF], {}, {MF: manifest(), MD: skill()}),
      [f"new plugin: idm-demo (1 new skill: {SKILL_NAME})"])
MD2 = MD.replace(f"/{SKILL_NAME}/", "/second/")
check("several new skills in one existing plugin are one reason",
      reasons([MD, MD2], {MF: manifest()}, {MD: skill(), MD2: skill()}),
      [f"2 new skills in idm-demo: {SKILL_NAME}, second"])

# --- rule 3: registry entries added or removed ---
A = {"plugins": [{"name": "a"}]}
AB = {"plugins": [{"name": "a"}, {"name": "b"}]}
check("a registry addition fires rule 3", reasons([], {}, {}, A, AB), ["published: b"])
check("a registry removal fires rule 3", reasons([], {}, {}, AB, A), ["unpublished: b"])
check("several publications are one reason",
      reasons([], {}, {}, {"plugins": []}, AB), ["published: a, b"])
check("an unchanged registry (head None) fires nothing", reasons([], {}, {}, A, None), [])
check("a changed but unreadable registry fires rule 3 fail-safe",
      reasons([".claude-plugin/marketplace.json"], {}, {}, A, None),
      ["marketplace.json changed but unreadable or not fetched"])

# --- rule 4: allowed-tools and surfaces ---
check("an allowed-tools change fires rule 4",
      reasons([MD, MF], BASE, {MD: skill(tools="Read, Bash"), MF: manifest("1.0.1")}),
      [f"allowed-tools changed: {SKILL_DIR}"])
check("reordering allowed-tools is not a change",
      reasons([MD], {MF: manifest(), MD: skill(tools="Read, Grep")},
              {MD: skill(tools="Grep,Read")}), [])
check("a YAML list equal to the comma string is not a change",
      reasons([MD], {MF: manifest(), MD: skill(tools="Read, Grep")},
              {MD: skill(tools="[Read, Grep]")}), [])
check("gaining claude-ai fires rule 4",
      reasons([MD], BASE, {MD: skill(surfaces="claude-code, claude-ai")}),
      [f"now declared for claude-ai: {SKILL_DIR}"])
check("keeping claude-ai fires nothing",
      reasons([MD], {MF: manifest(), MD: skill(surfaces="claude-ai")},
              {MD: skill(surfaces="claude-ai", body="b2")}), [])

# --- rule 5: MINOR or MAJOR bump ---
check("a MINOR bump fires rule 5",
      reasons([MF], BASE, {MF: manifest("1.1.0")}),
      ["MINOR version bump: idm-demo (1.0.0 -> 1.1.0)"])
check("a MAJOR bump fires rule 5",
      reasons([MF], BASE, {MF: manifest("2.0.0")}),
      ["MAJOR version bump: idm-demo (1.0.0 -> 2.0.0)"])
check("an unparseable version gives no reason and no crash",
      reasons([MF], {MF: manifest("1.0"), MD: skill()}, {MF: manifest(1.1)}), [])

# --- rule 6: non-skill components ---
check("a hooks file change fires rule 6",
      reasons([f"{PLUGIN}/hooks/hooks.json"], BASE, {}),
      [f"plugin component changed: {PLUGIN}/hooks/hooks.json"])
check("an .mcp.json change fires rule 6",
      reasons([f"{PLUGIN}/.mcp.json"], BASE, {}),
      [f"plugin component changed: {PLUGIN}/.mcp.json"])
check("an mcpServers change in plugin.json fires rule 6",
      reasons([MF], BASE, {MF: manifest("1.0.1", mcpServers={"x": {"command": "x"}})}),
      [f"plugin components changed (mcpServers): {PLUGIN}"])
check("a description-only plugin.json edit fires nothing",
      reasons([MF], BASE, {MF: manifest("1.0.0", description="new words")}), [])

# --- fix round 1: unreadable manifests and removed/unfetched heads send ---
check("a BOM-prefixed head plugin.json still reads for a MINOR bump",
      reasons([MF], BASE, {MF: "﻿" + manifest("1.1.0")}),
      ["MINOR version bump: idm-demo (1.0.0 -> 1.1.0)"])
check("a malformed head plugin.json fires unreadable",
      reasons([MF], BASE, {MF: "{not json"}),
      [f"plugin.json unreadable: {PLUGIN}"])
check("a malformed base plugin.json fires unreadable",
      reasons([MF], {MF: "{not json", MD: skill()}, {MF: manifest("1.0.1")}),
      [f"plugin.json unreadable: {PLUGIN}"])
check("a changed SKILL.md present in base but absent at head fires",
      reasons([MD], BASE, {}), [f"removed or not fetched: {MD}"])
check("a changed plugin.json present in base but absent at head fires",
      f"removed or not fetched: {MF}" in reasons([MF], BASE, {}), True)

# --- a 0-byte head file is a failed fetch, not an empty file ---
check("a 0-byte head SKILL.md fires removed-or-not-fetched",
      reasons([MD], BASE, {MD: ""}), [f"removed or not fetched: {MD}"])
check("a 0-byte head SKILL.md fires even for a new skill",
      reasons([MD], {MF: manifest()}, {MD: ""}), [f"removed or not fetched: {MD}"])

# --- a deletion the PR reports is named as one ---
check("a deleted SKILL.md is named as a removed skill, with its plugin",
      reasons([MD], BASE, {}, removed={MD}), [f"1 removed skill from idm-demo: {SKILL_NAME}"])
check("a deleted plugin.json is named as a removed plugin",
      "plugin removed: idm-demo" in reasons([MF], BASE, {}, removed={MF}), True)
check("a deleted plugin and its skills are one reason",
      reasons([MD, MF], BASE, {}, removed={MD, MF}),
      [f"plugin removed: idm-demo (1 skill: {SKILL_NAME})"])
DEMO = {"plugins": [{"name": "idm-demo"}, {"name": "other"}]}
check("a deleted plugin's registry removal is not repeated as unpublished",
      reasons([MD, MF, ".claude-plugin/marketplace.json"], BASE, {}, DEMO,
              {"plugins": [{"name": "other"}]}, removed={MD, MF}),
      [f"plugin removed: idm-demo (1 skill: {SKILL_NAME})"])
check("a plugin unpublished but kept in the repo is still reported",
      reasons([".claude-plugin/marketplace.json"], BASE, {}, DEMO,
              {"plugins": [{"name": "idm-demo"}]}), ["unpublished: other"])
check("a missing head file not reported as deleted stays a possible failed fetch",
      reasons([MD], BASE, {}, removed={MF}), [f"removed or not fetched: {MD}"])

# --- paths outside the plugin roots ---
check("a scripts/ change fires nothing", reasons(["scripts/x.py"], BASE, {}), [])

# --- decide: the decision table ---
D = gate.decide
check("rules fire -> send with their reasons",
      (D(["new skill: x"], "none", False).send, D(["new skill: x"], "none", False).reasons),
      (True, ["new skill: x"]))
check("rules silent, model 'none' -> no email", D([], "none", False).send, False)
for v in ("None", "None.", "NONE", "  none  "):
    check(f"rules silent, model {v!r} -> no email", D([], v, False).send, False)
d = D([], "Now rejects files over 10 MB.", False)
check("rules silent, model describes a change -> escalated send",
      (d.send, d.escalated, d.reasons),
      (True, True, ["escalated by model review: Now rejects files over 10 MB."]))
for v in (None, "", "   ", "_(nothing reported)_", 42):
    check(f"rules silent, model value {v!r} -> unavailable, send",
          (D([], v, False).send, D([], v, False).reasons),
          (True, ["model review unavailable"]))
check("a partial brief's 'none' cannot silence it",
      (D([], "none", True).send, D([], "none", True).reasons),
      (True, ["model review unavailable"]))

# --- decide: push mode, rules only ---
check("push: a rule with no earlier request -> send, labelled",
      D(["MINOR version bump: x (1.0.0 -> 1.1.0)"], None, False, "push", False).reasons,
      ["MINOR version bump: x (1.0.0 -> 1.1.0) (rule fired on a later push)"])
check("push: a rule after an earlier request -> no email",
      D(["new skill: x"], None, False, "push", True).send, False)
check("push: no rule -> no email, and the model is ignored",
      (D([], "Big change.", False, "push", False).send,
       D([], None, False, "push", False).send), (False, False))

# --- decide: a manual run after a request was sent sends nothing more ---
check("open: a rule after an earlier request -> no email",
      D(["new skill: x"], "none", False, "open", True).send, False)
check("open: a model escalation after an earlier request -> no email",
      D([], "Now rejects files over 10 MB.", False, "open", True).send, False)
check("open: model unavailable after an earlier request -> no email",
      D([], None, True, "open", True).send, False)

# --- read_brief: from the card the brief job uploads ---
sys.path.insert(0, str(SCRIPTS))
import teams_card  # noqa: E402

PR = {"number": 1, "title": "t", "url": "https://example.invalid/pull/1"}
FIELDS = {"what": "w", "reads": "r", "writes": "x", "tools": "t",
          "behaviour_change": "none", "closer_look": "c"}
with tempfile.TemporaryDirectory() as td:
    card = Path(td) / "brief-card.json"
    card.write_text(json.dumps(teams_card.brief_card(PR, FIELDS)), encoding="utf-8")
    check("read_brief finds the field on the card", gate.read_brief(str(card)), ("none", False))
    card.write_text(json.dumps(teams_card.brief_card(PR, FIELDS, partial=True)), encoding="utf-8")
    check("read_brief reports a partial brief", gate.read_brief(str(card))[1], True)
    empty = dict(FIELDS, behaviour_change="")
    card.write_text(json.dumps(teams_card.brief_card(PR, empty)), encoding="utf-8")
    check("an empty field reads as unavailable once decided",
          gate.decide([], *gate.read_brief(str(card))).reasons, ["model review unavailable"])
check("no brief file reads as unavailable", gate.read_brief(None), (None, False))

# --- main: the command line the workflow calls ---
import os  # noqa: E402

with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    tree(root, {MF: manifest("1.0.0"), MD: skill()})
    tree(root / "head", {MD: skill(body="bodyy"), MF: manifest("1.0.1")})
    (root / "changed.txt").write_text(f"{MD}\n{MF}\n", encoding="utf-8")
    card = root / "brief-card.json"
    card.write_text(json.dumps(teams_card.brief_card(PR, FIELDS)), encoding="utf-8")
    out = root / "gh_output"
    os.environ["GITHUB_OUTPUT"] = str(out)
    rc = gate.main(["--changed-files", str(root / "changed.txt"),
                    "--head-root", str(root / "head"), "--base-root", str(root),
                    "--marketplace-head", str(root / "missing.json"),
                    "--brief", str(card), "--reasons-out", str(root / "reasons.txt")])
    check("main: a typo PR with model 'none' writes send=false",
          (rc, out.read_text(encoding="utf-8")), (0, "send=false\n"))
    check("main: and an empty reasons file", (root / "reasons.txt").read_text(encoding="utf-8"), "")
    (root / "bad.json").write_text("{not json", encoding="utf-8")
    (root / "changed-reg.txt").write_text(
        f"{MD}\n{MF}\n.claude-plugin/marketplace.json\n", encoding="utf-8")
    out.write_text("", encoding="utf-8")
    rc = gate.main(["--changed-files", str(root / "changed-reg.txt"),
                    "--head-root", str(root / "head"), "--base-root", str(root),
                    "--marketplace-head", str(root / "bad.json"),
                    "--brief", str(card), "--reasons-out", str(root / "reasons.txt")])
    check("main: a changed but unreadable registry sends even when the model says none",
          (rc, out.read_text(encoding="utf-8")), (0, "send=true\n"))
    check("main: and the reasons file names the registry",
          "marketplace.json changed but unreadable or not fetched"
          in (root / "reasons.txt").read_text(encoding="utf-8").splitlines(), True)
    del os.environ["GITHUB_OUTPUT"]

check("not-due note on open", gate.not_due_note("open", False),
      "no rule fired, model found no behaviour change")
check("not-due note on a push", "model not consulted" in gate.not_due_note("push", False), True)
check("not-due note once sent", gate.not_due_note("push", True),
      "request already sent for this PR")
check("not-due note once sent, on a manual run", gate.not_due_note("open", True),
      "request already sent for this PR (run with resend_email to send again)")

print(f"\n{passed}/{passed + failed} passed")
sys.exit(1 if failed else 0)
