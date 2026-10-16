---
marp: true
paginate: true
---

<!--
Presentation deck: introducing the gf-agent-skills marketplace to team leads.

Renders as slides with Marp (`marp docs/OVERVIEW-SLIDES.md`, or the Marp VS Code
extension), and reads fine as plain markdown without it — `---` separates slides.

Companion to docs/GROUP-ONBOARDING.md, which is the doc to hand a lead who says yes.
Keep the two in step: this deck is the argument, that doc is the instructions.
-->

# gf-agent-skills

### A governed marketplace for AI skills

<!-- The marketplace name, not the repo slug (`gatesfoundation/gf-agent-publish`) — leads
     install from the marketplace; the repo is an implementation detail to them. -->


One catalog · one review standard · per-group distribution

---

## Where we are today

**4 plugins, 4 skills**

| Plugin | Skills |
|---|---|
| `idm-software-tools` | idm-pkg-install |
| `idm-data` | assess-dataset |
| `authoring` | skill-authoring |

Contributed by PR. Reviewed by code owners. Distributed per group.

---

## The problem it solves

Without a catalog, AI skills spread the way scripts do:

- copied between people, no owner, no review
- no way to know what staff are actually running
- a skill that reads credentials looks like any other markdown file
- no way to reach *just* one team

**The catalog exists so that a skill reaching a staff member has been reviewed by
someone accountable — and so we know what shipped.**

---

## Design decision 1

### One catalog, not one repo per team

Everything is contributed by PR into a single repo.

- one set of quality gates
- one security scan
- one review standard

**Cost:** groups share a pipeline.
**Benefit:** a skill reaching foundation staff cleared the same bar regardless of author.

---

## Design decision 2

### Plugins are the unit of scoping

Each group is a namespace holding task plugins — e.g. IDM's `idm-software-tools`, with room for
more as they're contributed — plus `plugins/shared-workflows/` for tasks no single team owns.
People install the ones they need.

One catalog, but IDM staff don't receive finance skills.

> *"Won't syncing the repo give everyone every plugin?"* — No. Sync makes plugins
> **available to the admin**; assignment is a separate, per-plugin step.

---

## Design decision 3

### Registration ≠ provisioning

```
register the marketplace   →  plugins become available to the admin
assign a plugin to a group →  people actually receive it
```

Two different decisions, two different owners.

Publishing to the catalog is not the same as pushing to laptops.

---

## Design decision 4

### Least privilege, by audience

Every skill declares `allowed-tools`.

| Tier | High-power tools (`Bash`, `Write`, `Edit`, …) |
|---|---|
| Foundation-wide (all staff) | **blocked** — CI fails |
| Group-scoped | allowed, flagged for security review |

**The wider the audience, the narrower the blast radius.**

---

## Design decision 5

### Behaviour is tested, not just structure

Every skill ships `evals/evals.json` — test cases run against a live Claude model.

- on every PR, for changed skills
- full suite nightly

A skill can be perfectly well-formed and still not do what it claims.
Structure checks won't catch that. Evals will.

---

## What happens to a contribution

```
  author writes SKILL.md + evals
            ↓
        opens a PR
            ↓
  ┌── three required checks ──────────────────┐
  │  structure & metadata   frontmatter, evals │
  │  security scan          fails on HIGH      │
  │  evals                  live model         │
  └────────────────────────────────────────────┘
            ↓
  AI-AskOps triage → business owner + IT owner
            ↓
  approvals: business owner, IT owner,
             IT AI x InfoSec x Legal Review (OneTrust form)
            ↓
  IT owner attests → repo admin merges
            ↓
  Infosec/Legal notified of what shipped
            ↓
     distributed per group
```

---

## None of this is advisory

`main` is protected by an active ruleset:

- required status checks
- only repo admins can merge, and only after the IT owner's attestation
- no direct pushes
- no force-push, no deletion

If a check fails or an approval is missing, the PR does not merge.

---

## Review — and where a lead fits

Same approval for every tier:

| Approver | How they're identified |
|---|---|
| Business owner | AI-AskOps triage |
| IT owner | AI-AskOps triage — also collects the approvals and attests |
| IT AI x InfoSec x Legal Review | AI Feature Enablement OneTrust form (automatic, or joint office hours) |

Your leads are still routed to your group's PRs by file path, so they see what's coming. Their
GitHub review is not a merge gate.

---

## Distribution — two surfaces, one source, different models

| | Claude Code | Claude.ai |
|---|---|---|
| Mechanism | self-service — each person adds the marketplace | assignment in the admin console |
| Targeted by | nobody centrally — not managed here | a group, optionally SCIM from Entra |
| Tool permissions | each user's own local settings | — |

Not parallel pipelines: only Claude.ai has an admin/targeting step. On Claude Code, publishing a
plugin to `marketplace.json` *is* the distribution decision — anyone who adds the marketplace can
install anything published, including group-scoped plugins.

---

## Bringing a group in — three steps

| # | Where | What |
|---|---|---|
| 1 | this repo | `plugins/groups/<name>/<task>/` + `plugin.json` + marketplace entry + CODEOWNERS |
| 2 | GitHub | a `<name>-leads` team with **write** access |
| 3 | Claude.ai | a group with your plugin assigned |

Steps 1–2: this week. Claude Code needs no step — it's self-service by design, not a phase we
haven't gotten to yet.

**Step 3 is not needed to start.**

---

## Starting small

1. Do Steps 1 and 2
2. A few people self-install on Claude Code
3. Prove the skills are worth distributing
4. *Then* provision Claude.ai (Step 3) if your group uses it

Self-install on Claude Code isn't a temporary Phase-1 stopgap — it's the permanent model on that
surface, because it isn't centrally managed here. The keys that *could* lock it down
(`strictKnownMarketplaces`, `disableSideloadFlags`, `strictPluginOnlyCustomization`) exist and are
deliberately unused; adopting them would be a deliberate future decision, not an automatic phase.

---

## What we haven't finished

Being straight about it, because this is where your input matters.

**The catalog is skills-only** — and that's what the governance is built for. Plugins can also
carry hooks, subagents, slash commands and MCP connectors. Today those are **scanned and
announced, but not validated, not eval-covered, and have no written review standard.**

**Group membership is maintained by hand.** Backing it with Entra is the plan.

---

## The non-skill gap, precisely

Three controls already cover every component type:

| Covered today | How |
|---|---|
| Security scan | our own YARA sweep of the **whole** plugin tree — the vendor scanner only sees `SKILL.md` |
| Human review | CODEOWNERS routes by path, whatever the file |
| Infosec record | a summary card on merge |

| Not covered | Consequence |
|---|---|
| Structural validation | a malformed hook fails on a laptop, not in CI |
| Evals | well-formed, scans clean, still wrong |
| Review rubric | the gate is only as good as what the reviewer knows to ask |
| Least-privilege rule | our audience-scaled tool limits stop at the skill boundary |

---

## Order we'll close it — and why

**1. Hooks** — a shell command on the user's machine, on every tool event, with the model
out of the loop. Highest risk, weakest coverage.

**2. Connectors** — standing credentials and outbound data. The binding constraint is a
*rubric*, not a validator: what service, whose credentials, what data leaves, who revokes it.

**3. Subagents** — closest to skills, so our existing checks extend most cheaply.

**4. Slash commands** — prompt content, no new capability. Structure check is enough.

Detail: **[docs/GOVERNANCE-ROADMAP.md](GOVERNANCE-ROADMAP.md)**

---

## What we need from you

1. **Who are your approvers?** A few leads — not the whole group
2. **Which surfaces does your group use?** Claude Code, Claude.ai, or both
3. **Are you bringing connectors or hooks?** If so, talk to us first — that governance
   isn't written yet

---

## Next step

**[docs/GROUP-ONBOARDING.md](GROUP-ONBOARDING.md)** — the three steps, with the actual file
formats and the traps worth knowing.

Questions → Skills Working Group
