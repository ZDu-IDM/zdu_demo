#!/usr/bin/env python3
"""Which skills deploy.py packages for Claude.ai (no packaging, no network).

Only an explicit `claude-ai` is packaged; no `surfaces` means Claude Code only.

Run:  python scripts/tests/test_deploy_surfaces.py
"""
import importlib.util
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent
sys.path.insert(0, str(SCRIPTS))  # so the module can `from utils import ...`
spec = importlib.util.spec_from_file_location("deploy", SCRIPTS / "deploy.py")
deploy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy)

passed = failed = 0


def check(name, got, want):
    global passed, failed
    if got == want:
        passed += 1
        print(f"PASS - {name}")
    else:
        failed += 1
        print(f"FAIL - {name}\n        got:  {got!r}\n        want: {want!r}")


def packs(metadata_block):
    """Write a SKILL.md with the given metadata block; would it be packaged?"""
    with tempfile.TemporaryDirectory() as td:
        d = Path(td) / "s"
        d.mkdir()
        (d / "SKILL.md").write_text(
            f"---\nname: s\ndescription: d\n{metadata_block}---\nbody\n",
            encoding="utf-8")
        return deploy.packs_for_claude_ai(deploy.read_surfaces(d))


check("no surfaces field is not packaged -- Claude Code only by default",
      packs(""), False)
check("a metadata block without surfaces is not packaged either",
      packs("metadata:\n  note: x\n"), False)
check("claude-code alone is not packaged", packs("metadata:\n  surfaces: claude-code\n"), False)
check("claude-code, claude-ai is packaged",
      packs("metadata:\n  surfaces: claude-code, claude-ai\n"), True)
check("a YAML-list claude-ai is packaged",
      packs("metadata:\n  surfaces: [claude-ai]\n"), True)

print(f"\n{passed}/{passed + failed} passed")
sys.exit(1 if failed else 0)
