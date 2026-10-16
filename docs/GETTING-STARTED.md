# Getting Started with Foundation Skills for Claude Code

Foundation-approved Claude skills are published in this **`gf-agent-skills`** marketplace.
This guide shows you how to add them to Claude Code in about a minute.

> **Which Claude am I using?** These steps apply to **Claude Code** (the CLI, and the Code
> experience in the desktop/IDE app). The Claude.ai chat/Cowork web experience is managed
> separately — see [claude-ai-deployment.md](claude-ai-deployment.md).

---

## Prerequisites

- **Claude Code installed** — see the [Claude Code setup guide](https://code.claude.com/docs)
  (or your foundation IT onboarding if there's a managed install).
- **Claude Code access + plugins enabled for your account.** These are provisioned by role/group.
  If the commands below return *"plugins are not enabled"* (or `/plugin` is unavailable), your
  account doesn't have the capability yet — request it through your foundation Claude admin / IT
  service desk.

---

Note: An individual user may not need to complete step 1 and step 2 if the company has already rolled out the foundation-wide and your-group skills.

## Step 1 — Add the marketplace

In Claude Code, run:

```
/plugin marketplace add gatesfoundation/gf-agent-publish
```

This registers the marketplace for **your** installation only (it doesn't affect anyone else).
Note the marketplace is referenced by its declared name, **`gf-agent-skills`**, in the
install commands below.

## Step 2 — Install the plugins for your group

Add your group's task plugin(s) — a group can have more than one.

```
/plugin install idm-software-tools@gf-agent-skills
```

**Available now:**

| Plugin | What's in it | Command |
|---|---|---|
| **IDM software tools** | idm-pkg-install | `/plugin install idm-software-tools@gf-agent-skills` |
| **IDM data** | assess-dataset | `/plugin install idm-data@gf-agent-skills` |
| **authoring** (shared) | skill-authoring | `/plugin install authoring@gf-agent-skills` |
| **Emerging Technology communications** | pirate-speak | `/plugin install emerging-technology-communications@gf-agent-skills` |

Other groups have no plugins yet. To add your group's first, see "Contribute a skill" below.

## Step 3 — Verify

```
/plugin
```

The plugins you installed should be listed as enabled. Claude uses their skills when relevant:
ask about installing an IDM package, for example, and `idm-pkg-install` kicks in.

---

## Keeping skills up to date

When new skills are approved and published, refresh your local copy:

```
/plugin marketplace update gf-agent-skills
```

---

## Contribute a skill (new or an update)

Skills live in this repo — you add or change one via a pull request. For a group skill (e.g. IDM):

**1. Create the skill** under your group's task plugin (see [Which route does my plugin
take?](../CONTRIBUTING.md#which-route-does-my-plugin-take) if you're not sure which task):

```
plugins/groups/<group>/<task>/skills/<skill-name>/
  SKILL.md            # required: name, description, allowed-tools + a "## Instructions" section; metadata.surfaces optional
  evals/evals.json    # required: >=1 real eval case (empty / {} / [] fails validation)
  reference/          # optional supporting files
```

See [SKILL_TEMPLATE.md](SKILL_TEMPLATE.md) and [EVALS.md](EVALS.md) for the skill formats.

**If `<task>` doesn't exist yet, create its manifest**, `.claude-plugin/plugin.json` beside
`skills/` (more in [PLUGIN_TEMPLATE.md](PLUGIN_TEMPLATE.md)):

```json
{
  "name": "<team>-<task>",
  "version": "1.0.0",
  "description": "Skills for the <team> team's <task>."
}
```

The name is `<team>-<task>` under `plugins/groups/<team>/<task>/` (e.g. `idm-software-tools`),
and the bare `<task>` under `plugins/shared-workflows/<task>/`.

**Add the new plugin's `.claude-plugin/marketplace.json` entry in the same PR**, so it's ready
once the PR merges. See [CONTRIBUTING.md § Register the plugin in the same
PR](../CONTRIBUTING.md#register-the-plugin-in-the-same-pr).

> **Don't want to write evals by hand?** Draft them with Claude, then review them:
> ```bash
> export ANTHROPIC_API_KEY=...
> python scripts/generate_evals.py --skill plugins/groups/<group>/<task>/skills/<skill-name>/SKILL.md
> ```
> It writes `evals/evals.json` next to the skill. The cases are a starting point, not a finished set.

**2. Validate locally before you push.** Both commands work before your plugin is registered:

```bash
python scripts/validate_skill.py                        # structure + metadata
python scripts/run_evals.py --changed-from origin/main  # evals for your changed skills, as CI runs them
```

`--plugin <name>` only works once the plugin is in `marketplace.json`.

**3. Open a PR, as a draft.** Fill in the ASKOPS block in the PR template. The automated gates run
on the PR: skill validation, evals and the security scan. Once they pass, click **Ready for
review**. That sends your request to AI-AskOps, which starts the approval process:

- AI-AskOps triage names a **business owner** and an **IT owner**, and both approve.
- **IT AI x InfoSec x Legal Review** approves through the AI Feature Enablement OneTrust form.
- The IT owner attests that the approvals are complete, and a **repo admin** merges the PR.

See [CONTRIBUTING.md § Approval process](../CONTRIBUTING.md#approval-process).

**4. Merging ships it.** Because the plugin is registered in the same PR, merging puts it in front
of staff. Claude Code users pick it up with `/plugin marketplace update gf-agent-skills`. On
Claude.ai, an Owner assigns the plugin to groups (see
[claude-ai-deployment.md](claude-ai-deployment.md)).

### Updating an existing skill

Same flow, plus: **bump `version` in the plugin's `.claude-plugin/plugin.json`** (skills have no
version of their own; see [CONTRIBUTING.md § Versioning](../CONTRIBUTING.md#versioning)), and
update `evals/evals.json` if the behaviour changed.

> Have an idea but don't want to build it yourself, or found a bug? Just
> [open an issue](https://github.com/gatesfoundation/gf-agent-publish/issues).

---

## FAQ

**Q: Do other people see the plugins I install?**
No. Adding a marketplace/plugin is local to your machine. A foundation-wide automated rollout is
coming; for now each person adds it themselves using this guide.

**Q: Can I use these in the Claude.ai web app?**
Not through this flow — the web experience is centrally managed. This guide is for Claude Code.
See [claude-ai-deployment.md](claude-ai-deployment.md).

**Q: The commands don't work / I get a permissions error.**
Your account likely doesn't have Claude Code plugins enabled yet — access is provisioned by
role/group. Request it through your foundation Claude admin / IT service desk.

**Q: Can I add my own or other marketplaces?**
Today, yes (Claude Code is open). As the governed rollout matures, general staff will be limited to
the approved marketplace; builders will retain flexibility. Prefer the approved marketplace so your
skills are the reviewed, supported ones.
