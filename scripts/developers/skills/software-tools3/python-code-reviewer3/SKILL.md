---
name: python-code-reviewer3
description: "Reviews Python files for bugs, security issues, and code quality. Use when asked to review code, check before a PR, or audit .py files. Writes a structured REVIEW.md with findings."
license: MIT
allowed-tools: Read, Grep, Glob, Write, Bash, PowerShell
owner: gf-ai-skills@gatesfoundation.org
version: "1.0.0"
---

# Python Code Reviewer

You are a Python code reviewer. Your job is to run quick pattern-based checks
on Python files and produce a structured REVIEW.md report.

> **Companion skill:** The `REVIEW.md` produced here is consumed by `python-code-fixer` in Standalone Mode. Do not rename the file, change the finding ID format (`CR-NN` / `WR-NN` / `IN-NN`), or omit the `**File:**` / `**Issue:**` / `**Fix:**` fields — the fixer's parser depends on them.

> **Cross-platform note:** Prefer the Claude Code `Grep` and `Glob` tools over shelling out to `grep`/`find`/`rg`. POSIX `find` is unavailable on Windows PowerShell, and the Grep tool is faster than per-file shell loops on every platform.

---

## Instructions

### Step 1 — Find files to review

First, get Python files changed vs main (works on every platform — `git` is platform-neutral):
```
git diff main...HEAD --name-only -- '*.py'
```

If no git diff is available (e.g. user passed a path, or HEAD is on main), enumerate `.py` files using the **Glob tool** with the pattern `**/*.py` rooted at the target path. Do **not** shell out to `find` — it is unavailable on Windows.

**Always exclude these directories from the review set** (generated, vendored, or environment-managed):
- `.git/`, `venv/`, `.venv/`, `env/`, `.env/`
- `node_modules/`, `__pycache__/`, `.tox/`, `.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/`
- `build/`, `dist/`, `*.egg-info/`

**Honor project-specific ignores.** If the project root contains `.gitignore`, `.reviewignore`, or a `tool.python-code-reviewer.exclude` array in `pyproject.toml`, treat the listed paths as additional exclusions. Do **not** hardcode project-specific directory names in this skill.

If after filtering no files remain, write REVIEW.md with `status: skipped` and stop.

---

### Step 2 — Run pattern checks across the review set

Run each check **once across all in-scope files** using the Claude Code `Grep` tool with `glob: "**/*.py"`, `output_mode: "content"`, and `-n: true`. Do **not** loop per file — that is O(files × patterns) and slow.

After each Grep call, filter out hits from the directories excluded in Step 1 before classifying them.

| # | Check | Pattern (Grep `pattern` argument) |
|---|---|---|
| 1 | Hardcoded secrets | `(password\|secret\|api_key\|token\|apikey)\s*=\s*['"][^'"]{4,}['"]` (use `-i: true`) |
| 2 | Dangerous functions | `\beval\s*\(\|\bexec\s*\(\|\bpickle\.loads?\s*\(\|shell=True` |
| 3 | Bare except | `^\s*except\s*:` |
| 4 | Mutable default arguments | `def\s+\w+\s*\(.*=\s*[\[\{]` |
| 5 | Debug artifacts | `\bprint\s*\(\|#\s*(TODO\|FIXME\|HACK\|XXX)` |
| 6 | SQL injection risk | `(execute\|cursor)\s*\(\s*[f"'].*(%s\|\.format)` |
| 7 | Assert for validation | `^\s*assert\b` |
| 8 | `open()` without `with` | `[^=\s]\s*open\s*\(` then drop lines containing `with open` |
| 9 | `requests` without timeout | `requests\.(get\|post\|put\|delete\|patch)\s*\(` then drop lines containing `timeout` |

**Known false positives to suppress before classifying:**
- Pattern 1: lines beginning with `#` (comments like `# password = "..."`)
- Pattern 5 `print(`: skip if the file is a CLI/script entrypoint — detect via `if __name__ == "__main__":` in the file, or a shebang on line 1
- Pattern 5 TODO/FIXME: do not flag lines suffixed with `# noqa` or `# pragma: no cover`
- Pattern 8: skip stdlib-shadowed names like `webbrowser.open(`, `socket.open(`, `os.open(`
- Patterns 2/3 inside test files (path matches `test_*.py`, `*_test.py`, or any segment named `tests/`) are usually fine — flag only if the test would be unreliable

---

### Step 3 — Classify findings by severity

**Critical** (score = 0 if found):
- Hardcoded secrets
- `eval`/`exec` on user input
- `pickle.loads` on untrusted data
- `shell=True` with variable interpolation
- SQL injection patterns

**Warning:**
- Bare `except:` clauses
- Mutable default arguments
- `assert` used for input validation
- `open()` without `with`
- `requests` calls without timeout

**Info:**
- `print()` statements in non-script files
- TODO/FIXME/HACK comments
- Debug artifacts

---

### Step 4 — Write REVIEW.md

Create `REVIEW.md` in the project root using the structure defined in **Output Format** below.

---

### Step 5 — Print result to chat

After writing REVIEW.md, print a one-line summary. Evaluate in order — first match wins:

- `critical > 0` → `[X] BLOCKED - N critical issue(s) found. See REVIEW.md`
- `warning > 0` → `[!]  N warning(s) found. See REVIEW.md`
- `info > 0` → `[i]  N info-level note(s). See REVIEW.md`
- `total == 0` and files were reviewed → `[OK] Pre-PR check passed - no issues found.`
- No files reviewed (status: skipped) → `[skip]  Skipped - no Python files in scope.`

---

## Output Format

`REVIEW.md` written to the project root using this exact structure:

```markdown
---
reviewed: <ISO timestamp>
depth: quick
files_reviewed: <N>
files_reviewed_list:
  - path/to/file.py
findings:
  critical: <N>
  warning: <N>
  info: <N>
  total: <N>
status: clean | issues_found | skipped
---

# Python Code Review

**Reviewed:** <timestamp>
**Depth:** quick
**Files:** <N>
**Status:** clean | issues_found

## Summary

<2-3 sentences describing what was reviewed and overall health.>

## Critical Issues

### CR-01: <Short Title>

**File:** `path/to/file.py:<line>`
**Issue:** <What the problem is and why it matters.>
**Fix:**
```python
# corrected code
```

## Warnings

### WR-01: <Short Title>

**File:** `path/to/file.py:<line>`
**Issue:** <Description.>
**Fix:** <Suggestion.>

## Info

### IN-01: <Short Title>

**File:** `path/to/file.py:<line>`
**Issue:** <Description.>

---
*Reviewer: Claude (python-code-reviewer)*
*Depth: quick*
```

**Finding ID scheme** — the companion `python-code-fixer` skill parses this file directly:
- Critical findings: `CR-NN`
- Warning findings: `WR-NN`
- Info findings: `IN-NN`
- Every Critical and Warning must have a `**Fix:**` field with a fenced code block (not prose)
- Every finding must have a `**File:** path:line` field — never omit the line number

---

## Limitations

- **Read-only** — this skill never modifies source files. Only `REVIEW.md` is written.
- **Pattern-based only** — checks are regex pattern matches, not AST analysis. False positives are possible; use false positive suppression rules in Step 2 before filing a finding.
- **Quick depth only** — does not perform deep data-flow analysis, type checking, or full security audits. Use a dedicated SAST tool for comprehensive security review.
- **Does not flag test files** unless the issue would make tests unreliable.
- **`clean` and `skipped` are not the same** — only use `clean` when files were actually reviewed and nothing was found.
- **Overwrites, does not append** — if `REVIEW.md` already exists it is replaced entirely. Never merge with prior findings; the fixer treats the file as authoritative.
- **Use the Write tool to produce REVIEW.md** — do not shell out to `echo`/`cat`/`Set-Content`.
- **Always include line numbers** — never write "somewhere in the file."
