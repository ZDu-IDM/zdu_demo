# Claude.ai Deployment Guide

How approved skills reach staff on **Claude.ai** (browser / Cowork). Both Claude.ai and
[Claude Code](claude-code-deployment.md) use the same `gf-agent-skills` marketplace, but only
Claude.ai assigns plugins to groups; on Claude Code anyone can install a registered plugin.

Claude.ai (Team / Enterprise) offers three paths:

| Path | Scope | Use for |
|---|---|---|
| **A. Org skills upload** | All members | Foundation-wide skills |
| **B. Org plugins → group** | A specific group | Group skills (e.g. `idm-software-tools`) |
| **C. Personal upload** | Just you | Testing, or a plugin not yet registered |

> **Prerequisite (all paths):** an admin/owner must enable **"Code execution and file creation"**
> and **"Skills"** in **Organization settings → Skills** (skills need code execution to run).

Only skills declared `surfaces: claude-code, claude-ai` are packaged for Claude.ai. A skill marked
`claude-code`, or with no `surfaces`, is left out: it could install cleanly and then do nothing,
because Claude.ai can't see the user's files.

---

## Path A — All-staff skills (org-wide upload)

1. Package the skill ZIPs: `python scripts/deploy.py --package --plugin <all-staff-plugin>`.
   They land in `dist/claude-ai/`, one ZIP per skill.
2. In **Organization settings → Plugins & skills → Add → Upload a skill**, upload each ZIP.
3. Uploaded skills are **on by default for all members**, who can turn them off.

---

## Path B — Group-scoped skills (plugins)

Path A reaches *everyone*. To give skills to one group (e.g. `idm-software-tools` to IDM), add the
plugin to the organization and set its access per group. Requires Owner or Primary Owner (or, on
Enterprise, a role that can manage libraries).

**1. Add the plugin** in **Organization settings → Plugins & skills → Add**:

- **Sync from GitHub (recommended):** connect the private `gf-agent-publish` repo. Every registered
  plugin appears, and merges to `main` re-sync automatically. While adding it, choose the
  **Default access** its plugins start with.
- **Upload a plugin:** a single plugin ZIP. Don't also sync the repo, or the plugin appears twice.

  ```bash
  python scripts/deploy.py --plugin-zip --plugin idm-software-tools
  python scripts/deploy.py --plugin-zip --path plugins/groups/<team>/<task>   # not yet registered
  ```

  This writes `dist/claude-ai/plugins/<plugin>.zip`, leaving out `evals/`, repo dot-files and any
  skill not declared for `claude-ai`. A plugin with a top-level `bin/` is refused, because Claude.ai
  rejects it.

**2. Set who gets it.** On the **Inventory** tab, open the plugin's menu:

- **Default access** applies to the whole organization;
- **Group access…** (Enterprise) overrides it for one group.

| Setting | Effect |
|---|---|
| Required | Installed and always on |
| Installed by default | On; members can turn it off |
| Available to install | Members install it if they want |
| Not available | Hidden |

A group's setting replaces the organization-wide one for its members. A member in several groups
gets the most permissive setting.

Groups are listed at **Organization settings → Groups**, created by hand or synced from Entra
(SCIM). Use one group per team, not per task: three plugins offered to one group as *Available to
install* already let each person choose their own.

> **Don't put group skills in Path A**, which reaches everyone. Bundle them as a plugin and set
> group access here.

### Won't syncing the whole repo give everyone every plugin?

Only if the **Default access** allows it. Syncing makes every plugin *assignable*; what members
see is set per plugin:

```
legal              → Default access: Installed by default
idm-software-tools → Default access: Not available;  IDM group: Required
finance-<task>     → Default access: Not available;  Finance group: Required
```

So sync once, and give each team's plugins **Not available** by default plus access for that
team's group. Don't split the repo per group.

---

## Path C — Personal (testing)

Build the skill ZIPs. A plugin not yet in `marketplace.json` can't be named with `--plugin`, so use
`--path`:

```bash
python scripts/deploy.py --package --path plugins/foundation-wide/new-plugin
python scripts/deploy.py --package --plugin idm-data            # once registered
```

Then:

1. Enable code execution in **Settings → Capabilities**.
2. **Customize → Skills** ([claude.ai/customize/skills](https://claude.ai/customize/skills)) →
   **"+" → "Create skill" → "Upload a skill"**, and select the ZIP.

To test a whole plugin instead, build it with `--plugin-zip` and upload it under
**Customize → Plugins → Add → Upload plugin**.

---

## Verify

Open a new Claude.ai conversation and type `/`. The skill should appear; test it with a prompt.

---

## Updating

- **Path A:** bump `version` in the plugin's `plugin.json`, re-package, and re-upload the ZIP.
  It replaces the previous version for all members.
- **Path B, synced:** merge to the repo. Claude.ai re-syncs automatically, with no re-upload.
- **Path B, uploaded:** build a new plugin ZIP, then use **Upload new version** on the plugin's
  menu.

---

## Troubleshooting

**Skill doesn't appear after upload**
- Confirm the ZIP has a `SKILL.md` at the top level of the skill folder.
- Run `python scripts/validate_skill.py`. Packaging runs it too, and stops if it fails.

**Skill behaves wrongly**
- Check the `## Instructions` section in `SKILL.md`; that's what Claude.ai loads.

**A group skill is visible to the whole org**
- It went in via Path A. Remove it from Organization skills, and deploy it via Path B instead.

**ZIP rejected by Claude.ai**
- A skill ZIP holds one skill. A plugin ZIP must have `.claude-plugin/plugin.json` at its root or
  inside one top-level folder, and no top-level `bin/`.
