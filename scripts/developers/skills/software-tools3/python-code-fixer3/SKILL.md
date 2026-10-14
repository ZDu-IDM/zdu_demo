---
name: python-code-fixer3
description: "Use this skill when applying fixes to Python 3 code review findings from REVIEW.md. Covers reading source files, applying intelligent fixes, committing each fix atomically, and producing a REVIEW-FIX.md report. Python 3 only - do NOT use for other languages, initial code review, test running, or deployment tasks."
allowed-tools: Read, Edit, Write, Grep, Glob, Bash, PowerShell
owner: gf-ai-skills@gatesfoundation.org
version: "1.0.0"
---

# Python Code Fixer (Python 3)

Applies fixes to Python 3 code review findings from `REVIEW.md`. Produces a `REVIEW-FIX.md` artifact next to the source review file.

**Usage:** Load this skill in Claude Code to apply fixes to Python 3 code review findings from `REVIEW.md`.

---

## Instructions

### Modes

This skill runs in one of two modes:

- **Standalone Mode (default)** - invoked directly by a user. Reads `./REVIEW.md` from the project root (matching the output of the companion `python-code-reviewer` skill), writes `./REVIEW-FIX.md`, edits files in the current working tree, and does **not** create a worktree. Skip Step 1 entirely.
- **Phase Mode** - invoked by an orchestrator that supplies a `<config>` block with `phase_dir`, `padded_phase`, `review_path`, `fix_report_path`. Runs the full worktree-based flow in Step 1.

Detect the mode in Step 2 by checking whether a `<config>` block is present in the prompt.

> **Scope:** Python 3 source files (`.py`) only. Skip any finding that targets a non-Python file and mark it as "skipped: non-Python file, out of scope".

**Job:** Read `REVIEW.md` findings → fix source code intelligently (not blindly) → commit each fix atomically → produce `REVIEW-FIX.md` report.

> **CRITICAL - Mandatory Initial Read:** If the prompt contains a `<required_reading>` block, use the `Read` tool to load every file listed there before any other action. This is primary context.

---

### Project Context

Before fixing code, discover project context:

**Project instructions:** Read `./CLAUDE.md` if it exists. Follow all project-specific guidelines, security requirements, and coding conventions.

**Project skills:** Check `.claude/skills/` or `.agents/skills/` if either exists:
1. List available skills (subdirectories)
2. Read `SKILL.md` for each skill (lightweight index ~130 lines)
3. Load specific `rules/*.md` files as needed
4. Do NOT load full `AGENTS.md` files (100KB+ context cost)
5. Follow skill rules relevant to fix tasks

---

### Fix Strategy

The REVIEW.md fix suggestion is **guidance**, not a patch to apply blindly.

**For each finding:**
1. **Read the actual source file** at the cited line (plus surrounding context - at least +/- 10 lines)
2. **Understand the current code state** - check if code matches what reviewer saw
3. **Adapt the fix suggestion** to actual code if it has changed or differs from review context
4. **Apply the fix** using Edit tool (preferred) for targeted changes, or Write tool for full rewrites
5. **Verify the fix** using the 3-tier verification strategy (see Verification Strategy below)

**If the source file has changed significantly** and the fix no longer applies cleanly:
- Mark finding as "skipped: code context differs from review"
- Continue with remaining findings
- Document in REVIEW-FIX.md

**If multiple files are referenced in the Fix section:**
- Collect ALL file paths mentioned in the finding
- Apply fix to each file
- Include all modified files in the atomic commit

---

### Rollback Strategy

Before editing **any** file for a finding, establish safe rollback capability.

**Protocol:**
1. **Record files to touch:** Note each file path in `touched_files` before editing anything.
2. **Apply fix:** Use Edit tool (preferred) for targeted changes.
3. **Verify fix:** Apply 3-tier verification strategy.
4. **On verification failure:**
   - Run `git checkout -- {file}` for EACH file in `touched_files`.
   - This is safe: the fix has NOT been committed yet. `git checkout --` reverts only the uncommitted in-progress change and does not affect commits from prior findings.
   - **DO NOT use Write tool for rollback** - a partial write on tool failure leaves the file corrupted with no recovery path.
5. **After rollback:**
   - Re-read the file and confirm it matches pre-fix state.
   - Mark finding as "skipped: fix caused errors, rolled back".
   - Document failure details in skip reason.
   - Continue with next finding.

**Rollback scope:** Per-finding only. Files modified by prior (already committed) findings are NOT touched during rollback.

---

### Verification Strategy (3-Tier)

After applying each fix, verify correctness in 3 tiers.

**Tier 1 - Minimum (ALWAYS REQUIRED)**
- Re-read the modified file section (at least the lines affected by the fix)
- Confirm the fix text is present
- Confirm surrounding code is intact (no corruption)

**Tier 2 - Preferred (when available)**

Run the Python 3 syntax check on the modified `.py` file. Try `python3` first, then `python`:

POSIX shells:
```bash
PY=$(command -v python3 || command -v python)
"$PY" -m py_compile {file} && echo "OK"
```

PowerShell:
```powershell
$py = (Get-Command python3 -ErrorAction SilentlyContinue) ?? (Get-Command python -ErrorAction SilentlyContinue)
if ($py) { & $py.Source -m py_compile {file}; if ($LASTEXITCODE -eq 0) { "OK" } }
```

Scoping rules:
- Only fail if the syntax error is in the file you just modified. Errors in other files are pre-existing - ignore them.
- If the check fails with errors that existed before your edit: proceed to commit.
- If neither `python3` nor `python` is available: fall back to Tier 3 - do NOT rollback.

**Tier 3 - Fallback**

If no Python interpreter is on PATH (e.g., restricted environment):
- Accept Tier 1 result
- Note in REVIEW-FIX.md: "syntax check skipped: python interpreter not available"

**Logic bug limitation:** Tier 1 and Tier 2 verify syntax/structure only, NOT semantic correctness. For findings where REVIEW.md classifies the issue as a logic error (incorrect condition, wrong algorithm, bad state handling), set the commit status in REVIEW-FIX.md as `"fixed: requires human verification"` rather than `"fixed"`.

---

### Finding Parser

**Structure**

Each finding starts with:
```
### {ID}: {Title}
```

ID matches: `CR-\d+` or `BL-\d+` (Critical), `WR-\d+` (Warning), or `IN-\d+` (Info)

**Required Fields**
- **File:** primary file path - format: `path/to/file.ext:42` or `path/to/file.ext`
- **Issue:** problem description
- **Fix:** section extends from `**Fix:**` to next `### ` heading or end of file

**Fix Content Variants**

The **Fix:** section may contain:

1. **Inline code or code fences:**
   ````
   ```language
   code snippet
   ```
   ````
   Extract code from triple-backtick fences.

   > **IMPORTANT:** Code fences may contain markdown-like syntax (headings, horizontal rules). Always track fence open/close state when scanning for section boundaries. Content between ``` delimiters is opaque - never parse it as finding structure.

2. **Multiple file references:**
   "In `module_a.py`, change X; in `module_b.py`, change Y" - parse ALL file references into the finding's `files` array.

3. **Prose-only descriptions:**
   "Add null check before accessing property" - interpret intent and apply fix.

**Parsing Rules**
- Trim whitespace from extracted values
- Handle missing line numbers gracefully (line: null)
- If Fix section is empty or just says "see above", use Issue description as guidance
- Stop parsing at next `### ` heading or `---` footer
- When scanning for `### ` boundaries, treat content inside triple-backtick fences as opaque - do NOT match `### ` or `---` inside fenced blocks
- `### ` headings inside a code fence (e.g., example markdown output) are NOT finding boundaries

---

### Execution Flow

**Step 1 - Setup Worktree (Phase Mode only - skip in Standalone)**

> **Standalone Mode:** Skip this entire step. Operate on the current working tree, in the current branch. Proceed directly to Step 2.

This skill, when run in Phase Mode, runs as a background process that makes commits. Operating on the main working tree would race the foreground session. Every Phase-Mode instance runs in its own isolated worktree.

> The bash block below is POSIX-only. Do **not** attempt to translate it to PowerShell for direct user runs - Standalone Mode skips this step entirely.

```bash
branch=$(git branch --show-current)
test -n "$branch" || { echo "Detached HEAD is not supported for review-fix (#2686)"; exit 1; }

sentinel="${phase_dir}/.review-fix-recovery-pending.json"
if [ -f "$sentinel" ]; then
  echo "Detected pre-existing recovery sentinel from a prior interrupted run: $sentinel"
  prior_recovery=$(node -e '
    const fs = require("fs");
    try {
      const parsed = JSON.parse(fs.readFileSync(process.argv[1], "utf-8"));
      process.stdout.write((parsed.worktree_path || "") + "\n" + (parsed.reviewfix_branch || ""));
    } catch (err) {
      process.stderr.write(`Warning: malformed recovery sentinel ${process.argv[1]}: ${err.message}\n`);
      process.stdout.write("\n");
    }
  ' "$sentinel")
  prior_wt="$(printf '%s' "$prior_recovery" | sed -n '1p')"
  prior_branch="$(printf '%s' "$prior_recovery" | sed -n '2p')"
  if [ -n "$prior_wt" ] && git worktree list --porcelain | grep -q "^worktree $prior_wt$"; then
    git worktree remove "$prior_wt" --force || true
  fi
  if [ -n "$prior_branch" ]; then
    git branch -D "$prior_branch" 2>/dev/null || true
  fi
  rm -f "$sentinel"
fi

wt=$(mktemp -d "/tmp/sv-${padded_phase}-reviewfix-XXXXXX")
reviewfix_branch="python-reviewfix/${padded_phase}-$$"
git worktree add -b "$reviewfix_branch" "$wt" "$branch"

node -e '
  const fs = require("fs");
  const [sentinelPath, worktree_path, branch, reviewfix_branch, padded_phase] = process.argv.slice(1);
  fs.writeFileSync(sentinelPath, JSON.stringify({
    worktree_path, branch, reviewfix_branch, padded_phase,
    started_at: new Date().toISOString()
  }, null, 2));
' "$sentinel" "$wt" "$branch" "$reviewfix_branch" "$padded_phase"

cd "$wt"
```

Cleanup tail (always run, even on failure):
```bash
main_repo="$(git worktree list --porcelain | awk '/^worktree / { sub(/^worktree /, ""); print; exit }')"
ff_status=0
if git -C "$main_repo" merge --ff-only "$reviewfix_branch" 2>&1; then
  ff_status=0
else
  ff_status=$?
fi
git worktree remove "$wt" --force
if [ "$ff_status" -eq 0 ]; then
  git -C "$main_repo" branch -D "$reviewfix_branch" || true
fi
rm -f "$sentinel"
```

**Step 2 - Load Context**

1. Read mandatory files from `<required_reading>` block if present.
2. Detect mode and resolve config:
   - *Phase Mode* (`<config>` block present): extract `phase_dir`, `padded_phase`, `review_path`, `fix_scope`, `fix_report_path`.
   - *Standalone Mode*: use defaults — `review_path = ./REVIEW.md`, `fix_report_path = ./REVIEW-FIX.md`, `fix_scope = "critical_warning"`.
3. Read REVIEW.md using the `Read` tool (do not use `cat`).
4. If frontmatter `status` is `"clean"` or `"skipped"`, exit: "No issues to fix." Do NOT create REVIEW-FIX.md.
5. Load project context: read `./CLAUDE.md`, check for `.claude/skills/` or `.agents/skills/`.

**Step 3 - Parse Findings**

1. Extract findings using the Finding Parser rules above.
2. Filter by `fix_scope`: `"critical_warning"` includes CR-*, BL-*, WR-* only; `"all"` includes IN-* too.
3. Sort by severity: Critical → Warning → Info. Within same severity, maintain document order.
4. Record `findings_in_scope` for REVIEW-FIX.md frontmatter.

**Step 4 - Apply Fixes**

For each finding in sorted order:
1. Read ALL source files referenced by the finding (+/- 10 lines around cited line for primary file).
2. Record `touched_files` before editing anything.
3. Determine if fix applies — adapt if code has minor changes but fix still logically applies.
4. Apply fix (Edit tool preferred) or mark as skipped if code context differs significantly.
5. Verify using 3-tier strategy.
6. Commit atomically: `fix({padded_phase}): {ID} {description}` listing ALL modified files.
7. Record result: `{finding_id, status, files_modified, commit_hash, skip_reason}`.

Safe counter arithmetic:
```bash
FIXED_COUNT=$((FIXED_COUNT + 1))   # correct
# ((FIXED_COUNT++))                # WRONG — fails under set -e
```

**Step 5 - Write Fix Report**

Create `REVIEW-FIX.md` at `fix_report_path` with YAML frontmatter and body:

```yaml
---
phase: {phase}
fixed_at: {ISO timestamp}
review_path: {path}
iteration: {N}
findings_in_scope: {count}
fixed: {count}
skipped: {count}
status: all_fixed | partial | none_fixed
---
```

```markdown
# Phase {X}: Code Review Fix Report

**Summary:**
- Findings in scope: {count}
- Fixed: {count}
- Skipped: {count}

## Fixed Issues

### {finding_id}: {title}
**Files modified:** `file1`, `file2`
**Commit:** {hash}
**Applied fix:** {brief description}

## Skipped Issues

### {finding_id}: {title}
**File:** `path/to/file.ext:{line}`
**Reason:** {skip_reason}
**Original issue:** {issue description}

---
_Fixed: {timestamp}_
_Fixer: Claude (python-code-fixer)_
_Iteration: {N}_
```

> **DO NOT commit REVIEW-FIX.md** — the orchestrator handles that commit.

---

## Output Format

**`REVIEW-FIX.md`** produced at `fix_report_path` (default: `./REVIEW-FIX.md`).

The report contains:
- YAML frontmatter with counts and status (`all_fixed` / `partial` / `none_fixed`)
- **Fixed Issues** section: one entry per fixed finding with commit hash and description of what changed
- **Skipped Issues** section: one entry per skipped finding with specific skip reason

Each fix is also committed individually to git with message format:
```
fix({padded_phase}): {ID} {description}
```

---

## Limitations

- **Python 3 only** — do not use for other languages, initial code review, test running, or deployment tasks
- Does not run the full test suite — syntax verification only (Tier 2). Semantic correctness for logic errors requires human verification; these are committed with status `"fixed: requires human verification"`
- Blind application of REVIEW.md suggestions is not performed — if the code has changed significantly since review, the finding is skipped rather than forced
- Phase Mode worktree setup is POSIX-only (uses `mktemp`, `awk`, `sed`, `node`) — not supported on Windows; Standalone Mode works cross-platform
- Does not create new files unless the fix explicitly requires it
- Does not modify files unrelated to the finding being fixed
