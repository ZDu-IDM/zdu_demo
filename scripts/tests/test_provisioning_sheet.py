#!/usr/bin/env python3
"""Offline behavior test for provisioning_sheet.rows (no gh, no network).

The sheet exists so the Claude.ai console step is derived rather than
remembered. What must not happen is a published, claude-code-only plugin
appearing as anything other than "Not available" — that is the case where an
admin assigns it to a group and it installs cleanly while doing nothing.

Run:  python scripts/tests/test_provisioning_sheet.py
"""
import importlib.util
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("provisioning_sheet",
                                              SCRIPTS / "provisioning_sheet.py")
ps = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ps)

passed = failed = 0


def check(name, got, want):
    global passed, failed
    if got == want:
        passed += 1
        print(f"PASS - {name}")
    else:
        failed += 1
        print(f"FAIL - {name}\n        got:  {got!r}\n        want: {want!r}")


def make_plugin(root: Path, rel: str, name: str, skills: dict):
    """skills maps skill-name -> the value of its `surfaces` field (or None)."""
    d = root / rel
    (d / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (d / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": name, "skills": [f"./skills/{s}" for s in skills]}),
        encoding="utf-8")
    for skill, surfaces in skills.items():
        sd = d / "skills" / skill
        sd.mkdir(parents=True, exist_ok=True)
        block = f"metadata:\n  surfaces: {surfaces}\n" if surfaces else ""
        (sd / "SKILL.md").write_text(
            f"---\nname: {skill}\n{block}---\n\nbody\n", encoding="utf-8")


def settings(root: Path, registered):
    return {r["plugin"]: r["setting"] for r in ps.rows(root, registered)}


with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    make_plugin(root, "plugins/groups/idm/tools", "idm-tools",
                {"local-thing": "claude-code"})
    make_plugin(root, "plugins/shared-workflows/review", "review",
                {"portable-thing": "claude-code, claude-ai"})
    make_plugin(root, "plugins/foundation-wide/legal", "legal", {})
    make_plugin(root, "plugins/groups/idm/mixed", "idm-mixed",
                {"local-thing": "claude-code", "portable-thing": "claude-ai"})

    # Nothing registered: every plugin is invisible on both surfaces, so there is
    # no console action to recommend for any of them.
    s = settings(root, {})
    check("an unpublished claude-code-only plugin needs no action",
          s["idm-tools"], ps.UNPUBLISHED)
    check("an unpublished portable plugin needs no action either",
          s["review"], ps.UNPUBLISHED)

    # The case the sheet exists for.
    reg = {"idm-tools": root / "plugins/groups/idm/tools",
           "review": root / "plugins/shared-workflows/review",
           "legal": root / "plugins/foundation-wide/legal",
           "idm-mixed": root / "plugins/groups/idm/mixed"}
    s = settings(root, reg)
    check("a published claude-code-only plugin is Not available",
          s["idm-tools"], ps.NOT_AVAILABLE)
    check("a published portable plugin is left to a human",
          s["review"], ps.HUMAN)

    # An empty published plugin has nothing to install, so it should not be
    # offered either — but for a different reason, which the sheet states.
    check("a published plugin with no skills is Not available",
          s["legal"], ps.NOT_AVAILABLE)
    legal = next(r for r in ps.rows(root, reg) if r["plugin"] == "legal")
    check("and says it is because there are no skills",
          "no skills" in legal["why"], True)

    # One claude-ai skill is enough: the plugin can do something there, so the
    # group decision returns to the human. Treating "any" as "all" would wrongly
    # hide a plugin that half works.
    check("a plugin with one portable skill is not blanket-blocked",
          s["idm-mixed"], ps.HUMAN)

    # The reason has to name the surface constraint, since the sheet is read by
    # someone deciding in a console with no other context.
    tools = next(r for r in ps.rows(root, reg) if r["plugin"] == "idm-tools")
    check("the reason explains the sandbox", "sandbox" in tools["why"], True)

with tempfile.TemporaryDirectory() as td:
    # A skill with no surfaces field must not read as claude-ai-capable.
    # validate_skill.py rejects it, but this script must not guess generously
    # in the window before that runs.
    root = Path(td)
    make_plugin(root, "plugins/groups/idm/old", "idm-old", {"legacy": None})
    reg = {"idm-old": root / "plugins/groups/idm/old"}
    check("a skill declaring no surfaces is not assumed portable",
          settings(root, reg)["idm-old"], ps.NOT_AVAILABLE)

with tempfile.TemporaryDirectory() as td:
    # Rendering must survive an empty catalog rather than raising.
    root = Path(td)
    (root / "plugins").mkdir()
    out = ps.render(ps.rows(root, {}))
    check("an empty catalog still renders a sheet", "Claude.ai provisioning" in out, True)

# --- the sheet must not grow with the catalog ---
# It tracks PUBLISHED plugins, because an unpublished one needs no console
# action. Listing everything churned the file on every merge, which is what
# stops reviewers reading a diff, and it buried the two lines that matter.
with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    reg = {}
    for i in range(200):
        make_plugin(root, f"plugins/groups/team{i}/task", f"team{i}-task",
                    {"s": "claude-code, claude-ai"})
        if i < 12:
            reg[f"team{i}-task"] = root / f"plugins/groups/team{i}/task"
    text = ps.render(ps.rows(root, reg))
    table = [l for l in text.splitlines() if l.startswith("| `")]
    check("only published plugins get a table row", len(table), 12)
    check("the 188 unpublished ones add no rows",
          all("team50-task" not in l for l in table), True)

    # Naming every unpublished plugin produced one 3,000-character line.
    check("no line blows up as the catalog grows",
          max(len(l) for l in text.splitlines()) < 400, True)
    check("the unpublished count is still stated", "188 plugin(s)" in text, True)
    check("and the list truncates rather than naming all of them",
          "and 178 more" in text, True)

print(f"\n{passed}/{passed + failed} passed")
sys.exit(1 if failed else 0)
