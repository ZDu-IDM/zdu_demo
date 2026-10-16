#!/usr/bin/env python3
"""What deploy.py --plugin-zip puts in a plugin ZIP (no packaging run, no network).

Run:  python scripts/tests/test_deploy_plugin_zip.py
"""
import importlib.util
import json
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


def write(root, rel, text="x"):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def plugin(root, skills_list=None):
    manifest = {"name": "demo", "version": "1.0.0"}
    if skills_list is not None:
        manifest["skills"] = skills_list
    write(root, ".claude-plugin/plugin.json", json.dumps(manifest))
    write(root, "skills/web-ok/SKILL.md")
    write(root, "skills/web-ok/references/notes.md")
    write(root, "skills/web-ok/evals/evals.json")
    write(root, "skills/local-only/SKILL.md")
    write(root, ".mcp.json", "{}")
    write(root, ".github/workflow.yml")
    write(root, "skills/web-ok/__pycache__/x.pyc")
    write(root, "README.md")


def names(entries):
    return sorted(rel.as_posix() for _, rel in entries)


with tempfile.TemporaryDirectory() as td:
    root = Path(td) / "demo"
    plugin(root)
    got = names(deploy.plugin_zip_entries(root, [root / "skills/local-only"]))
    check("the ZIP holds the manifest, .mcp.json, README and the claude-ai skill",
          got, [".claude-plugin/plugin.json", ".mcp.json", "README.md",
                "skills/web-ok/SKILL.md", "skills/web-ok/references/notes.md"])
    check("evals/ is left out", any("evals" in n for n in got), False)
    check("repo dot-folders are left out", any(n.startswith(".github") for n in got), False)
    check("caches are left out", any("__pycache__" in n for n in got), False)
    check("an excluded skill is left out", any("local-only" in n for n in got), False)

    check("with nothing excluded, every skill is in",
          "skills/local-only/SKILL.md" in names(deploy.plugin_zip_entries(root)), True)

    # claude.ai rejects any plugin with a top-level bin/.
    write(root, "bin/tool")
    try:
        deploy.plugin_zip_entries(root)
        check("a top-level bin/ is refused", "no error", "ValueError")
    except ValueError as e:
        check("a top-level bin/ is refused", "bin/" in str(e), True)

# --- the manifest's skills list must not name skills the ZIP leaves out ---
with tempfile.TemporaryDirectory() as td:
    root = Path(td) / "demo"
    plugin(root, ["./skills/web-ok", "./skills/local-only"])
    data = json.loads(deploy.plugin_zip_manifest(root, [root / "skills/local-only"]))
    check("an excluded skill's entry is dropped", data["skills"], ["./skills/web-ok"])
    check("the rest of the manifest is kept", (data["name"], data["version"]),
          ("demo", "1.0.0"))

    data = json.loads(deploy.plugin_zip_manifest(root, [root / "skills/web-ok",
                                                        root / "skills/local-only"]))
    check("an emptied skills list is removed, not left empty", "skills" in data, False)

with tempfile.TemporaryDirectory() as td:
    root = Path(td) / "demo"
    plugin(root)
    original = (root / ".claude-plugin/plugin.json").read_text(encoding="utf-8")
    check("a manifest with no skills list is copied unchanged",
          deploy.plugin_zip_manifest(root, [root / "skills/local-only"]), original)

print(f"\n{passed}/{passed + failed} passed")
sys.exit(1 if failed else 0)
