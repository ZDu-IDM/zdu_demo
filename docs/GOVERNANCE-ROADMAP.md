# Governance roadmap — plugin components beyond skills

The catalog's governance was built for **skills**, and today that is all the catalog contains:
every plugin manifest, across all three roots, declares `skills` and
nothing else. No `hooks/`, no `agents/`, no `commands/`, no `.mcp.json`.

A Claude plugin can carry four other component types, and each is a different kind of risk:

| Component | What it is | Why it's a different judgement |
|---|---|---|
| **Hooks** | shell commands the harness runs on tool events | executes on the user's machine, with the model out of the loop entirely |
| **MCP connectors** (`.mcp.json`) | a server granting access to an external service | standing credentials and data egress, not instructions |
| **Subagents** (`agents/`) | scoped agent definitions with their own tool grants | closest to a skill, but claims tools independently |
| **Slash commands** (`commands/`) | prompt templates invoked by name | lowest risk; prompt content, no new capability |

This document is the honest account of what covers them today, what doesn't, and the order
we intend to close the gaps. It is the long-form companion to the "Known limitations" section
of [GROUP-ONBOARDING.md](GROUP-ONBOARDING.md).

---

## What already covers them

Three of the controls are component-agnostic and apply to non-skill files today.

**Security scanning — the whole tree.** `cisco-ai-skill-scanner` only discovers content via
`SKILL.md`, so hooks, subagents, commands and MCP configs are invisible to the off-the-shelf
tool. [`scanner/scan_plugin_tree.py`](../scanner/scan_plugin_tree.py) exists to close exactly
that gap: our custom YARA rules run over **every** file under all three plugin roots —
`plugins/foundation-wide/`, `plugins/groups/` and `plugins/shared-workflows/` — Cisco's bundled
core rules run over the non-skill files, findings are written as SARIF into GitHub code scanning,
and a match exits non-zero and blocks the merge. The root list comes from `utils.PLUGIN_ROOTS`
rather than being repeated here, so a new root is covered without editing the scanner.

**Human review.** [`.github/CODEOWNERS`](../.github/CODEOWNERS) routes by path, so a PR adding
`plugins/groups/idm/hooks/` pulls in the same reviewers as a PR adding a skill there. The routing
works; what's missing is a standard for those reviewers to apply — see gap 3.

**Audit record.** `infosec-notify.yml` posts one summary card to Infosec/Legal on merge covering
non-skill plugin components (hooks, connectors, manifests), alongside the per-skill cards.

**Manifest validation — plugin level, not component level.** `validate_skill.py` checks every
`plugin.json` across all three roots: that it parses, declares a `name`, sits at a plugin
directory rather than a namespace root, lists only skills that exist, does not collide with
another plugin's name, and that its marketplace entry (if any) resolves and agrees with it. This
constrains the *container* a hook or connector would ship in; it says nothing about the component
itself — that is gap 1.

**Branch protection and lint** are repo-wide and need no extension.

---

## What does not

| # | Gap | Why it matters |
|---|---|---|
| 1 | **No structural validation of non-skill components.** `scripts/validate_skill.py` now validates skills *and* plugin manifests (shape, naming, duplicate names, registration), but nothing schema-checks `hooks.json`, subagent frontmatter, or `.mcp.json`. | A malformed hook fails at the user's machine, not in CI. |
| 2 | **No behavioural testing.** Evals are per-skill. There is no equivalent for a hook or a subagent. | A component can be well-formed, scan clean, and still do the wrong thing. |
| 3 | **No written review standard.** A reviewer approving an `.mcp.json` has no rubric — no required questions about auth model, data egress, or scope. | The review gate is only as good as what the reviewer knows to ask. |
| 4 | **No least-privilege rule.** Foundation-wide skills may not request `Bash`, `PowerShell`, `Write`, `Edit` or `NotebookEdit`. There is no analogous constraint on what a hook may execute or what tools a subagent may claim. | The audience-scaled blast radius rule stops at the skill boundary. |

---

## Order of work, and why

**1. Hooks.** Highest risk, weakest coverage. A hook runs a shell command on the user's machine
on every matching tool event — no model judgement in the path, no prompt to inspect. Needs both
a schema check (gap 1) and a least-privilege standard for what a hook may execute (gap 4).

**2. MCP connectors.** Standing credentials and outbound data flow. The binding constraint here
is gap 3, not automation: a schema check on `.mcp.json` is cheap, but the real control is a
written rubric — what service, whose credentials, what data leaves, who revokes it — applied by
a reviewer with security standing.

**3. Subagents.** Structurally closest to skills, so the existing validation and eval machinery
extends most cheaply. Main open question is gap 4: whether a subagent's tool grants inherit the
audience tier of the plugin that ships it.

**4. Slash commands.** Prompt content, no new capability. Structural validation is sufficient;
no separate review standard needed.

---

## Until then

The gate is human, and stated in onboarding: **if your group is bringing hooks or connectors,
talk to the Skills Working Group before you open the PR.** Non-skill components are scanned and
announced, but "scanned and announced" is not the same bar as skills clear, and we would rather
say so than imply coverage we don't have.

Skills need no such conversation — that path is fully governed. See
[CONTRIBUTING.md](../CONTRIBUTING.md).
