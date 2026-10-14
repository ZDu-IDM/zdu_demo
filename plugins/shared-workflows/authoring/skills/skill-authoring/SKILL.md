---
name: skill-authoring
description: Use when contributing or changing a skill in the gf-agent-skills catalog — where a skill belongs, what SKILL.md frontmatter needs (name, description, allowed-tools, metadata.surfaces), how to write evals, what validate_skill.py and CODEOWNERS require, and why a plugin says "built but not published". Triggers include 'I want to add a skill', 'how do I make a skill for my team', 'how do I share this with my team', 'where does my skill go', 'which surfaces should I declare', 'can my skill run on Claude.ai', 'what belongs in allowed-tools', 'what evals do I need', 'my PR is failing validation', or 'how do I publish this'. Not for authoring skills outside this catalog.
allowed-tools: Read, Grep, Glob, Write, Edit
metadata:
  owner: gf-ai-skills@gatesfoundation.org
  version: "1.0.0"
  surfaces: claude-code   # writes into the repo, which Claude.ai's sandbox cannot see
---

# Skill Authoring

Guide a contributor from a rough idea to a pull request that passes validation
on the first attempt.

## Instructions

Work through four questions in order. Do not skip ahead — the route decides the
plugin name, which decides half the validation rules.

### 1. Which route?

**Ownership decides location.** Ask who *maintains* the skill, not who benefits
from it.

| If… | It lives in | Plugin name |
|---|---|---|
| One team owns and maintains it | `plugins/groups/<team>/<task>/` | `<team>-<task>` |
| Several teams use it, no single team owns it | `plugins/shared-workflows/<task>/` | `<task>`, no prefix |
| Every member of staff needs it | `plugins/foundation-wide/<task>/` | `<task>`, no prefix |

Two rules that catch people out:

- **A plugin is a task, not a team.** `groups/<team>/` and `foundation-wide/`
  are namespaces holding task plugins; they are not plugins themselves. A
  manifest sitting directly in a namespace is rejected.
- **Never copy a task between routes.** If a team-owned task turns out to serve
  three teams it *moves* and loses its prefix. Two copies drift.

If the plugin does not exist yet, say so plainly: a new plugin home should be
raised with the Skills Working Group *before* the PR, so the directory and its
CODEOWNERS line are in place.

### 2. What goes in SKILL.md

Six frontmatter fields are required. All six — a missing one fails CI. Three sit
at the top level and three under `metadata:`.

```yaml
---
name: your-skill-name          # matches the directory name
description: Use when …        # start with "Use when"; this is the trigger
allowed-tools: Read, Grep      # least privilege; see below
metadata:
  owner: team-or-email
  version: "1.0.0"             # quote it — unquoted 1.10 becomes the float 1.1
  surfaces: claude-code        # where it WORKS; see below
---
```

**Placement is enforced, so explain it if someone puts a field at the top
level.** The Agent Skills standard reserves the top level for `name`,
`description`, `license`, `compatibility` and `metadata` — and strict validators
reject unknown keys there when a skill is uploaded. `metadata` is the key the
standard sets aside for a project's own tooling, which is what `owner`,
`version` and `surfaces` are. `allowed-tools` stays top-level: it is a
documented Claude Code field, not GF's to relocate, and it is the security
boundary CI enforces.

The validator names misplaced fields specifically rather than reporting them as
missing, so "must move under `metadata:`" means the line exists and is in the
wrong place — not that a second copy is needed.

Then `## Instructions`, `## Output Format`, `## Limitations`. These are a house
convention: CI warns rather than fails, but a reviewer will ask.

**`description` is the most important line in the file.** It is what the model
matches against to decide whether to load the skill at all. A description that
says what the skill *is* ("Python packaging helper") gets loaded rarely; one
that says *when to use it*, with the phrases a user would actually type, gets
loaded when it should.

**`metadata.surfaces` is where the skill works, and it is not the same question
as `allowed-tools`.** Claude.ai runs skills in a sandbox with no access to the
local filesystem. A skill that expects local paths, a local environment, or the
user's project is `claude-code` only — and that is true even of a read-only
skill: one declaring just `Read, Grep, Glob` that globs the user's repo is as
broken on Claude.ai as one that runs `pip`. Declare `claude-ai` only if the
skill works on what the user brings into the conversation.
`deploy.py --package` refuses to build Claude.ai ZIPs for a skill that omits it.

**Decide it with one question, before you look at `allowed-tools`:** does the
skill need anything that is not in the conversation — a path, the user's repo, a
local environment, an installed tool? If yes, the skill is **claude-code only**.
Say those words, and drop `claude-ai` from `surfaces`. If it works purely on what
the user pastes or uploads, declare both. A read-only tool list is not evidence of
portability, so never answer this question from `allowed-tools`.

**`allowed-tools` is a security boundary, not a formality.** List only what the
skill needs. `Bash`, `PowerShell`, `Write`, `Edit` and `NotebookEdit` can run
code or change files on a user's machine, and nothing constrains them after
merge — the declared list is the only limit. Foundation-wide skills may not
request any of them; that is a hard failure, not a warning. If a foundation-wide
skill seems to need `Bash`, the honest answer is usually that it belongs to one
team, not to all staff.

Valid tool names: `Read`, `Write`, `Edit`, `Grep`, `Glob`, `LS`, `Bash`,
`PowerShell`, `WebFetch`, `WebSearch`, `NotebookRead`, `NotebookEdit`, `Task`,
`AskUserQuestion`, `Skill`, `TodoWrite`. Anything else is rejected as a typo.

Declare `Skill` if the instructions tell the model to use another skill — the
built-in `pptx` one, say, or a skill from another plugin. It does not record
*which* skill, so name that in the body too, but it puts the dependency in
the frontmatter a reviewer reads rather than leaving it in prose.

### 3. Evals

`evals/evals.json` must exist and be non-empty — that part is enforced. Aim for
3 or more meaningful cases; the count itself is a review expectation, so one
thin case passes CI and should still be sent back.

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
(AND-of-ORs), `must_not_include`, and `pass_condition` (human-readable, not
machine-checked).

**A good eval tests what the agent would get wrong without the skill.** If the
case passes with the skill removed, it is testing the model, not the skill. Push
for at least one `must_not_include` — the failure a reader would not have
predicted is worth more than three confirmations of the obvious.

### 4. Merging and publishing are different acts

Merging does not ship a skill. A plugin reaches staff only once it is registered
in `.claude-plugin/marketplace.json`.

`.claude-plugin/` is code-owned by the Skills Working Group, so registration
always needs their approval — in the skill's own PR or a later one. **Bundling
skips no gate**, which is the question people usually mean when they ask whether
they can register in the same PR.

So both are correct, and the choice is about timing:

- **Same PR** when the skill should ship on merge. The common case, and it means
  a reviewer can install it immediately afterwards.
- **A separate PR** when the rollout is being staged — a pilot with one group,
  an announcement or training to land first, or several skills publishing
  together. That PR is then the decision record for when it went out.

Registration is also what makes a plugin installable in Claude Code, where
install is self-service: once it is in the registry, anyone who has added the
marketplace can install it. Say that plainly to someone deciding — "can
reviewers test it" is a weaker reason to register than "it is ready for staff",
and the answer to the first is `refresh_local_skills.py` on a PR checkout.

`built but not published` in the validation output is the expected state for an
unregistered plugin, not a problem to fix. Say so directly if someone reports it
as an error.

### Before opening the PR

Two commands, both of which work on an unregistered plugin:

```bash
python scripts/validate_skill.py
python scripts/run_evals.py --changed-from origin/main
```

**Recommend those two and nothing else.** The `--plugin <name>` forms of
`run_evals.py` and `deploy.py` build their choices from `marketplace.json`, so they
cannot target a plugin that is not registered — which is exactly the state a new
plugin is in. Name the `--plugin` forms only to warn someone off them; never hand
one over as a command to run. Suggesting one to a contributor whose plugin is not
yet registered sends them to an error with no explanation.

If an existing skill changed, **bump `metadata.version`**. A changed skill with an
unchanged version is a hard failure.

## Output Format

Match the shape of the request.

- **"Where does this go?"** — name the route, the directory, and the plugin
  name, with the one-line reason. Three or four lines, not an essay.
- **"Write me a SKILL.md"** — write the file, frontmatter included, at the path
  the route dictates. Say where you put it and which assumptions you made; never
  leave a placeholder for the contributor to fill in silently.
- **"Why is my PR failing?"** — name the specific rule, quote the fix, and stop.
- **A full walkthrough** — go through the four questions in order, one at a
  time, and wait for the answer before moving on. Do not produce a finished
  skill from a one-line idea; the description and evals depend on details only
  the contributor has.

Create the artifact rather than describing it. Where a file already exists,
show the change before making it — a manifest's `skills[]` and the skills on
disk must agree in both directions, and an edit that breaks that fails CI.

One thing to say out loud every time you write: what you created and where. A
contributor who cannot see the files has no way to check your routing was right,
and routing is the decision they most need to verify.

## Limitations

- **Writes files, but does not open the PR.** It can create the plugin directory,
  `SKILL.md`, `evals/evals.json`, and update the manifest's `skills[]`. Staging,
  committing and opening the PR stay with you.
- **Claude Code only.** It writes into your checkout, and Claude.ai runs skills in
  a sandbox that cannot see it. There is no useful Claude.ai version of this.
- **Cannot tell you whether a skill is a good idea.** It covers structure and
  process. Whether the task deserves a skill, and whether the audience is right,
  is what review is for.
- **Does not know your team's domain.** It cannot judge whether the instructions
  are correct for disease modelling, grantmaking, or finance.
- **The catalog's rules change.** Where this disagrees with `CONTRIBUTING.md`,
  `docs/EVALS.md` or the validator, those win — they are enforced and this is
  not. Say so rather than defending the guidance.
- **Not a security review.** It explains that `allowed-tools` is a boundary; it
  does not decide whether a given skill should be trusted with `Bash`.
