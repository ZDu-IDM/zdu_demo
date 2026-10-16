---
name: skill-authoring
description: Use when contributing or changing a skill in the gf-agent-skills catalog — where a skill belongs, what SKILL.md frontmatter needs (name, description, allowed-tools, metadata.surfaces), how to write evals, what validate_skill.py and CODEOWNERS require, and why a plugin says "built but not published". Triggers include 'I want to add a skill', 'how do I make a skill for my team', 'how do I share this with my team', 'where does my skill go', 'which surfaces should I declare', 'can my skill run on Claude.ai', 'what belongs in allowed-tools', 'what evals do I need', 'my PR is failing validation', or 'how do I publish this'. Not for authoring skills outside this catalog.
allowed-tools: Read, Grep, Glob, Write, Edit
metadata:
  surfaces: claude-code   # writes into the repo, which Claude.ai's sandbox cannot see
---

# Skill Authoring

Guide a contributor from a rough idea to a pull request that passes validation
on the first attempt.

## Instructions

Work through four questions in order. The route decides the plugin name, which
decides half the validation rules.

### 1. Which route?

**Ownership decides location.** Ask who *maintains* the skill, not who benefits
from it.

| If… | It lives in | Plugin name |
|---|---|---|
| One team owns and maintains it | `plugins/groups/<team>/<task>/` | `<team>-<task>` |
| Several teams use it, no single team owns it | `plugins/shared-workflows/<task>/` | `<task>`, no prefix |
| Every member of staff needs it | `plugins/foundation-wide/<task>/` | `<task>`, no prefix |

- **A plugin is a task, not a team.** `groups/<team>/` and `foundation-wide/`
  are namespaces of task plugins; a manifest directly in one is rejected.
- **Never copy a task between routes.** A team task that comes to serve several
  teams *moves* and loses its prefix; two copies drift.

If the plugin doesn't exist yet, say so: raise it with the Skills Working Group
*before* the PR, so the directory and its CODEOWNERS line are ready.

### 2. What goes in SKILL.md

Three top-level fields are required; a missing one fails CI. `metadata.surfaces`
is optional and opts a skill into Claude.ai.

```yaml
---
name: your-skill-name          # matches the directory name
description: Use when …        # start with "Use when"; this is the trigger
allowed-tools: Read, Grep      # least privilege; see below
metadata:                      # optional — only to opt into Claude.ai
  surfaces: claude-code, claude-ai
---
```

Don't present `surfaces` as required: leaving it out means Claude Code only.

**There is no `version` or `owner` in SKILL.md, and a leftover one fails CI.**
Both live in the plugin's `.claude-plugin/plugin.json`: `version` (a quoted
semver string, required) and `author` (`{"name": "...", "email": "..."}`,
optional, but it needs a `name`). Most SKILL.md examples elsewhere carry these
lines, so whenever you write frontmatter, say in one sentence where they went.
"Remove it" from the validator means delete the line, not move it.

**Placement:** GF's own fields go under `metadata:`, because strict validators
reject unknown top-level keys on upload. `allowed-tools` stays top-level. "Must
move under `metadata:`" means the line is in the wrong place, not missing.

Then `## Instructions`, `## Output Format`, `## Limitations`. CI only warns if
they're missing, but a reviewer will ask.

**`description` is the most important line.** The model matches against it to
decide whether to load the skill. Say *when to use it*, with phrases a user
would actually type, not what the skill *is*.

**`metadata.surfaces` is where the skill works.** Claude.ai runs skills in a
sandbox with no access to the local filesystem. Decide it with one question,
before you look at `allowed-tools`: does the skill need anything not in the
conversation — a path, the user's repo, a local environment, an installed tool?

- **Yes:** the skill is **claude-code only**. Say those words, and either leave
  `surfaces` out or declare `claude-code` alone. Never `claude-ai`. A read-only
  tool list (`Read, Grep, Glob`) is not evidence of portability.
- **No, it works on what the user pastes or uploads:** declare
  `claude-code, claude-ai`. That line is what gets it packaged for Claude.ai.

**`allowed-tools` is a security boundary.** List only what the skill needs.
`Bash`, `PowerShell`, `Write`, `Edit` and `NotebookEdit` run code or change
files, and nothing limits them after merge. **Foundation-wide skills may not
request any of them**: that's a hard failure. If a foundation-wide skill seems to
need `Bash`, it usually belongs to one team, as a group plugin.

Valid tool names: `Read`, `Write`, `Edit`, `Grep`, `Glob`, `LS`, `Bash`,
`PowerShell`, `WebFetch`, `WebSearch`, `NotebookRead`, `NotebookEdit`, `Task`,
`AskUserQuestion`, `Skill`, `TodoWrite`. Anything else is rejected as a typo.
Declare `Skill` if the instructions use another skill, and name which one in the
body.

### 3. Evals

`evals/evals.json` must exist and be non-empty. Aim for 3 or more meaningful
cases; one thin case passes CI but should be sent back.

```json
{
  "skill_name": "your-skill-name",
  "evals": [
    {
      "id": "ys-001",
      "name": "short label shown in CI",
      "prompt": "what the user actually types",
      "rubric": { "must_include": ["a phrase the answer must contain"] }
    }
  ]
}
```

Rubric fields: `must_include`, `must_include_one_of`, `must_include_groups`
(AND-of-ORs), `must_not_include`, and `pass_condition` (for humans, not checked).

**A good eval tests what the agent would get wrong without the skill.** If it
passes with the skill removed, it tests the model. Push for at least one
`must_not_include`.

### 4. Merging and publishing are different acts

A plugin reaches staff only once it is registered in
`.claude-plugin/marketplace.json`. The Skills Working Group code-owns
`.claude-plugin/`, so registration always needs their approval. **Bundling skips
no gate**, so both timings are fine:

- **Same PR** when the skill should ship on merge (the common case).
- **A separate PR** when the rollout is staged: a pilot, an announcement first,
  or several skills publishing together.

On Claude Code, registration makes the plugin installable by anyone. "Can
reviewers test it" is a weak reason to register; `refresh_local_skills.py` on a
PR checkout covers that.

`built but not published` in the validation output is the expected state for an
unregistered plugin, not an error. Say so directly if someone reports it.

### Before opening the PR

Two commands, both of which work on an unregistered plugin:

```bash
python scripts/validate_skill.py
python scripts/run_evals.py --changed-from origin/main
```

**Recommend those two and nothing else.** The `--plugin <name>` forms of
`run_evals.py` and `deploy.py` only accept registered plugins, so they fail on a
new one. Mention them only to warn someone off; never give one as a command to
run.

If an existing skill changed, **bump `version` in its plugin's `plugin.json`**.
Any change under the plugin's `skills/` counts, including references and deleted
files, except `evals/`. One bump covers the whole PR, sized by the largest
change: `allowed-tools` gaining a capability is MAJOR, a new skill MINOR,
wording PATCH. A new plugin needs no bump.

## Output Format

Match the shape of the request.

- **"Where does this go?"** — the route, directory and plugin name, with a
  one-line reason. Three or four lines.
- **"Write me a SKILL.md"** — write the file at the path the route dictates.
  Say where you put it and what you assumed; never leave a silent placeholder.
- **"Why is my PR failing?"** — name the rule, quote the fix, and stop.
- **A full walkthrough** — the four questions in order, one at a time, waiting
  for each answer. Don't produce a finished skill from a one-line idea.

Create the artifact rather than describing it. Where a file already exists,
show the change before making it.

Every time you write a file, say what you created and where, so the contributor
can check the routing.

## Limitations

- **Writes files, but does not open the PR.** It can create the plugin directory,
  `plugin.json`, `SKILL.md` and `evals/evals.json`. Committing and opening the PR
  stay with you.
- **Claude Code only.** It writes into your checkout, which Claude.ai's sandbox
  cannot see.
- **Cannot tell you whether a skill is a good idea**, or whether the audience is
  right. That's what review is for.
- **Does not know your team's domain.**
- **The catalog's rules change.** Where this disagrees with `CONTRIBUTING.md`,
  `docs/EVALS.md` or the validator, those win. Say so rather than defending this.
- **Not a security review.** It explains that `allowed-tools` is a boundary; it
  does not decide whether a skill should be trusted with `Bash`.
