#!/usr/bin/env python3
"""
check_pinned_installs.py — every package installed in a workflow must be pinned.

Real incident (2026-08-20): anthropic 1.0.0 dropped the temperature kwarg from
messages.create() and broke run_evals.py on an unpinned install, with no commit
to this repo. The stakes are higher for cisco-ai-skill-scanner: a floating
security scanner silently changes what "passing the security gate" means.

No Semgrep registry pack covers this -- reproducibility is a policy, not a CVE
class -- so it is checked here.

Why a YAML parse instead of a regex over the raw file: this replaced a Semgrep
`languages: generic` rule that matched raw text and so could not tell a command
from prose. It needed four corrections, twice for flagging things that were not
commands at all (a sentence in a comment, and its own test fixture). Parsing
removes that whole class: comments are discarded by the parser, and only `run:`
values are inspected.

Usage:
  python scripts/check_pinned_installs.py                  # .github/workflows
  python scripts/check_pinned_installs.py path/to/file.yml  # specific files
"""
import re
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).parent.parent
WORKFLOW_DIR = REPO_ROOT / ".github" / "workflows"

# PEP 440 operators, plus ${VAR}/$VAR indirection (the version is pinned in env).
CONSTRAINTS = ("==", "~=", "!=", ">=", "<=", "<", ">", "===", "@", "$")

# `pip install`, `pip3 install`, `python -m pip install`, `uv pip install`, ...
INSTALL_RE = re.compile(r"\b(?:pip|pip3)\s+install\b")

# Options that consume the NEXT token as their value. Without this, `-r` is
# skipped as a flag but `requirements.txt` is then read as an unpinned package.
# (The `--opt=value` form needs no entry here: it starts with `-`, so the whole
# token is skipped.)
VALUE_OPTS = {
    "-r", "--requirement", "-c", "--constraint", "-e", "--editable",
    "-i", "--index-url", "--extra-index-url", "-f", "--find-links",
    "-t", "--target", "--python", "--prefix", "--root",
}


def is_option(token: str) -> bool:
    return token.startswith("-")


def join_continuations(text: str) -> list[str]:
    """Fold `foo \\` line continuations into one logical line.

    Without this, `pip install \\` followed by packages on the next line would
    only ever see an empty package list and pass silently.
    """
    logical, buf = [], ""
    for raw in text.splitlines():
        stripped = raw.rstrip()
        if stripped.endswith("\\"):
            buf += stripped[:-1] + " "
            continue
        logical.append(buf + stripped)
        buf = ""
    if buf:
        logical.append(buf)
    return logical


def iter_run_blocks(doc):
    """Yield every `run:` string in a parsed workflow, at any nesting depth."""
    if isinstance(doc, dict):
        for key, value in doc.items():
            if key == "run" and isinstance(value, str):
                yield value
            else:
                yield from iter_run_blocks(value)
    elif isinstance(doc, list):
        for item in doc:
            yield from iter_run_blocks(item)


def unpinned_in(text: str) -> list[str]:
    """Return the unpinned package tokens in one shell snippet."""
    bad = []
    for line in join_continuations(text):
        m = INSTALL_RE.search(line)
        if not m:
            continue
        rest = line[m.end():]
        # Stop at a shell separator -- `pip install x && something-else`.
        rest = re.split(r"[;&|]", rest, maxsplit=1)[0]
        tokens = rest.split()
        i = 0
        while i < len(tokens):
            token = tokens[i]
            if is_option(token):
                # Consume this option's value too, when it takes one.
                i += 2 if token in VALUE_OPTS else 1
                continue
            if not any(op in token for op in CONSTRAINTS):
                bad.append(token)
            i += 1
    return bad


def check(path: Path) -> list[str]:
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        return [f"{path}: could not parse as YAML: {e}"]
    problems = []
    for block in iter_run_blocks(doc):
        for token in unpinned_in(block):
            problems.append(f"{path}: unpinned package {token!r} — "
                            f"add a version constraint (==, ~=, >=, <)")
    return problems


def main() -> int:
    targets = [Path(a) for a in sys.argv[1:]] or sorted(
        list(WORKFLOW_DIR.glob("*.yml")) + list(WORKFLOW_DIR.glob("*.yaml")))
    if not targets:
        print("No workflow files found.")
        return 0

    problems = []
    for path in targets:
        problems.extend(check(path))

    if problems:
        for p in problems:
            print(f"::error::{p}")
        print(f"\n{len(problems)} unpinned install(s) found.")
        return 1

    print(f"All installs pinned across {len(targets)} workflow file(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
