# Skill Template

Copy the frontmatter and section headers below into your new skill's `SKILL.md` and fill in the placeholders.

---

```markdown
---
name: your-skill-name
description: Use when [specific triggering condition — start with "Use when"]
allowed-tools: Read, Grep, Glob   # least-privilege: list ONLY the tools this skill needs
metadata:                         # optional - only to opt into Claude.ai
  surfaces: claude-code, claude-ai  # where it WORKS - see below; leave out for Claude Code only
---

# Skill Title

## Instructions

[What the skill does and how the agent should behave. Be specific about actions, tone, and scope.]

## Output Format

[Describe the expected structure of responses: sections, length, format, examples.]

## Limitations

[What this skill does NOT do. Be explicit so users don't get surprised.]
```

---

## Why the fields sit where they do

- **Required, at the top level:** `name`, `description`, `allowed-tools`. `allowed-tools` is
  the security boundary CI enforces.
- **Optional, under `metadata:`:** `surfaces`. GF's own fields go there because strict validators
  reject unknown top-level keys on upload.
- **Not here:** `version` and `owner`. They belong to the plugin's `.claude-plugin/plugin.json`
  (`version`, and an optional `author`); either one left in `SKILL.md` fails validation. See
  [CONTRIBUTING.md § Versioning](../CONTRIBUTING.md#versioning) and
  [PLUGIN_TEMPLATE.md](PLUGIN_TEMPLATE.md#author-optional).

`surfaces` is where the skill **works**. Leaving it out means Claude Code only. Claude.ai runs
skills in a sandbox that can't see the user's files, so declare `claude-code, claude-ai` only if
the skill works on what the user pastes or uploads.

---

## Before opening a pull request

Run these two first. Both work before your plugin is registered (the `--plugin` forms don't):

```bash
python scripts/validate_skill.py
python scripts/run_evals.py --changed-from origin/main
```

CI enforces the required fields, field placement, plugin version bumps, tool names, the
all-staff tool restriction and a non-empty `evals.json`. Review covers what CI can't:

- [ ] **Routing is by ownership, not usefulness.** Who *maintains* it decides where it lives.
      Moving a plugin later renames it.
- [ ] **The description is a trigger, not a summary.** Does it contain the phrases someone
      would actually type?
- [ ] **The evals fail without the skill.** Aim for 3+ meaningful cases and at least one
      `must_not_include` (see [EVALS.md](EVALS.md)).
- [ ] **`allowed-tools` is minimal.** CI can't tell whether a skill that asked for `Bash`
      needed it.
- [ ] **`## Instructions`, `## Output Format` and `## Limitations` are present.** CI only warns.
