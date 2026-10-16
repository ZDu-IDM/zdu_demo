# Group Onboarding

How a team lead brings a group into the catalog.

For writing a skill, see [CONTRIBUTING.md](../CONTRIBUTING.md) instead.

---

## What you get, and what you own

**You get** task plugins of your own, each named `<your-group>-<task>`, through the same
automated gates and [approval process](../CONTRIBUTING.md#approval-process) as everyone else.
On Claude.ai only your group receives them; on Claude Code anyone can install them.

A task no single team owns belongs in `plugins/shared-workflows/<task>/` instead (see [Which
route does my plugin take?](../CONTRIBUTING.md#which-route-does-my-plugin-take)).

**You own currency.** Set `author` in each `plugin.json` to whoever maintains it, so the
AI-AskOps request and the Infosec card name them; it's optional, but without it both say "not
declared".

---

## Before you start — two decisions

**1. Who are your approvers?**

Two or three people qualified to judge a skill for your domain, not the whole group. If
everyone is an approver, everyone is a code owner and the review means nothing. See
[Identity](#identity-which-group-backs-which-role).

**2. Which surfaces does your group actually use?**

| Surface | What it is | Needs |
|---|---|---|
| **Claude Code** | the CLI / desktop app | Nothing from you — self-service, see below |
| **Claude.ai** | the web app / Cowork | Step 3 (admin console) |

Claude Code has no admin step: people install your plugin themselves. Claude.ai needs Step 3,
but a pilot can start without it.

---

## Step 1 — Register in the repo

`plugins/groups/<your-group>/` is a **namespace, not a plugin**: each task is its own plugin one
level down. Step 1a creates it if it doesn't exist yet.

**1a. Create the directory**

```
plugins/groups/<your-group>/<task>/
├── .claude-plugin/plugin.json
└── skills/
```

**1b. Write the plugin manifest** — `plugins/groups/<your-group>/<task>/.claude-plugin/plugin.json`:

```json
{
  "name": "<your-group>-<task>",
  "version": "1.0.0",
  "description": "Skills for the <your-group> group's <task>."
}
```

Skills in `skills/<name>/` load without being listed; `skills` and `author` are optional (see
[PLUGIN_TEMPLATE.md](PLUGIN_TEMPLATE.md)). A second task is a separate plugin with its own
manifest; see [When to create a task plugin](../CONTRIBUTING.md#when-to-create-a-task-plugin).

**1c. Add your CODEOWNERS line**, commented out, beside the existing group lines in
`.github/CODEOWNERS`:

```
# /plugins/groups/<your-group>/<task>/  @gatesfoundation/<your-group>-leads  @gatesfoundation/skills-working-group
```

Leave it commented until the team from Step 2 exists. **Do not uncomment it before the team
exists and has write access** — an unknown owner silently disables the whole line, so the path
falls through to the `/plugins/groups/` catch-all and your leads stop being required reviewers.
(The catch-all means the path is never left with *no* reviewer; it routes to the Skills Working
Group until your line is live.)

**1d. Register the plugin in the same PR as its first skill**, in
`.claude-plugin/marketplace.json` (see [Register the plugin in the same
PR](../CONTRIBUTING.md#register-the-plugin-in-the-same-pr)):

```json
{
  "name": "<your-group>-<task>",
  "description": "…",
  "source": "./plugins/groups/<your-group>/<task>"
}
```

Open one PR with 1a–1d plus your first skill.

---

## Step 2 — Create the GitHub team

A team named `<your-group>-leads` in the `gatesfoundation` org, containing your approvers,
with **write** access to this repository.

Write access is required: CODEOWNERS ignores a team without it. To back membership with Entra,
see [Identity](#identity-which-group-backs-which-role).

---

## Step 3 — Claude.ai distribution (optional to start)

In **Organization settings → Plugins & skills**, give each task plugin access for your group
(**Group access…**, once per plugin). Groups can be made by hand or synced from Entra. See
[claude-ai-deployment.md](claude-ai-deployment.md).

Claude Code has no equivalent step; see [claude-code-deployment.md](claude-code-deployment.md).

---

## Starting small

A reasonable pilot skips Step 3 entirely:

1. Do Steps 1 and 2.
2. Have a few people self-install:
   `/plugin marketplace add gatesfoundation/gf-agent-publish`, then
   `/plugin install <your-group>-<task>@gf-agent-skills`
3. Prove the skills are worth distributing.
4. Then do Step 3 if your group uses Claude.ai.

Self-install is the permanent model on Claude Code, not a pilot workaround (see
[SECURITY-SCANNING.md](SECURITY-SCANNING.md) for why it isn't restricted).

---

## Identity: which group backs which role

Group membership does two jobs, which need **different** memberships:

| Use | Question it answers | Membership |
|---|---|---|
| Claude.ai groups (Step 3) | who *receives* the skills | everyone in the group |
| Approval gate (Step 2) | who can *sign off* | a few leads |

One division-wide Entra group for both would make every member a code owner, with the team's
repository permission. Use a separate, small group for the approvers.

---

## Known limitations

**Plugin components beyond skills are not fully governed yet.** Hooks, subagents, slash
commands and MCP connectors are scanned, but not validated, eval-covered or reviewed against a
written standard. If your group is bringing connectors, talk to the Skills Working Group first.
See [GOVERNANCE-ROADMAP.md](GOVERNANCE-ROADMAP.md).

---

## Checklist

- [ ] Approvers identified (a few leads, not the whole group)
- [ ] Surfaces decided (Claude Code / Claude.ai / both)
- [ ] Step 1 — `plugins/groups/<group>/<task>/`, `plugin.json`, CODEOWNERS line, `marketplace.json` entry, first skill — one PR
- [ ] Step 2 — `<group>-leads` team created with **write** access
- [ ] CODEOWNERS line uncommented *after* the team exists
- [ ] Step 3 — Claude.ai group + plugin assignment (if using Claude.ai)
- [ ] Approval process complete and PR merged by a repo admin — see [CONTRIBUTING.md](../CONTRIBUTING.md#approval-process)
