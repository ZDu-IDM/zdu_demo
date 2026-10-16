# Claude Code Deployment

How the `gf-agent-skills` marketplace reaches staff on **Claude Code** (desktop / IDE / CLI).
Unlike [Claude.ai](claude-ai-deployment.md), this surface is **not centrally managed** — every
section below is a consequence of that.

## Installing is self-service

Each person adds the marketplace and installs plugins on their own machine:

```
/plugin marketplace add gatesfoundation/gf-agent-publish
/plugin install <plugin-name>@gf-agent-skills
```

There is no MDM (Intune / Jamf) push, no org-wide rollout, and no admin console showing who has
added it.

## Registration is exposure

On Claude.ai, an admin must assign a plugin to a group before anyone sees it. Claude Code has no
such boundary: once a plugin is listed in
[`.claude-plugin/marketplace.json`](../.claude-plugin/marketplace.json), **any** staff member who
adds the marketplace can install it — including a plugin meant for one team.

So here, publishing to the catalog *is* the distribution decision; there is no later "who gets it"
step. Weigh that when reviewing what enters `marketplace.json` — see
[CONTRIBUTING.md § Register the plugin in the same PR](../CONTRIBUTING.md#register-the-plugin-in-the-same-pr).

## Related

- [claude-ai-deployment.md](claude-ai-deployment.md) — the Claude.ai path, which *is* administered.
- [GROUP-ONBOARDING.md](GROUP-ONBOARDING.md) — what a team lead should know about the asymmetry.
