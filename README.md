# GF Foundation Skills

A **governed marketplace** of reusable AI skills for the Gates Foundation. Skills are contributed
by PR, checked by automated quality + security gates, reviewed by code owners, then published for
staff to install into Claude Code or Claude.ai.

> **Compatible with:** Claude.ai · Claude Code · any tool supporting [Agent Skills](https://agentskills.io)

**New here?** Start with the [Getting Started guide](docs/GETTING-STARTED.md).

---

## Skill Catalog

The marketplace (`gf-agent-skills`) currently ships four plugins: **`idm-software-tools`**,
**`idm-data`**, **`authoring`**, and **`emerging-technology-communications`**. Other task plugins
are added as their skills are contributed and reviewed.

### Foundation-Wide Skills
Available to all GF staff regardless of team or domain.

`plugins/foundation-wide/` holds task plugins (`legal`, `design`) rather than a single
all-staff plugin. Both are empty placeholders — no foundation-wide skills ship today.

### Group Skills

| Skill | Group | Category | Owner |
|---|---|---|---|
| [idm-pkg-install](plugins/groups/idm/software-tools/skills/idm-pkg-install/) | IDM | Software Tools | IDM |
| [assess-dataset](plugins/groups/idm/data/skills/assess-dataset/) | IDM | Data | IDM |
| [pirate-speak](plugins/groups/emerging-technology/communications/skills/pirate-speak/) | Emerging Technology | Communications | ET |

### Shared Workflows

Tasks that several teams use, owned centrally rather than by any one team.

| Skill | Plugin | Owner |
|---|---|---|
| [skill-authoring](plugins/shared-workflows/authoring/skills/skill-authoring/) | `authoring` | Skills Working Group |

---

## Governance & quality gates

Every change goes through a PR. Before a skill can merge and reach users, it must pass the
automated gates **and** the approval process. This is what makes the catalog "governed."

### Automated checks (GitHub Actions)

**Blocking** — a merge is refused until these pass:

| Check | Workflow | What it enforces |
|---|---|---|
| **Skill structure & metadata** | `validate.yml` | Required frontmatter (`name`, `description`, `allowed-tools`; `metadata.surfaces` optional, defaulting to Claude Code only), `evals/evals.json` present, least-privilege tools (foundation-wide skills may **not** request `Bash`, `PowerShell`, `Write`, `Edit` or `NotebookEdit`), and a higher plugin `version` whenever a skill changes |
| **Evals** | `pr-evals.yml` | Runs each changed skill's evals against a live Claude model, including multi-turn workflow evals (full suite runs nightly via `run-evals.yml`) |
| **Security scan** | `security-scan.yml` | `cisco-ai-skill-scanner` + foundation YARA rules, failing on HIGH — plus `scanner/scan_plugin_tree.py`, which sweeps the **whole plugin tree** (hooks, subagents, slash commands, MCP configs), since the scanner only discovers skills via `SKILL.md`. See [SECURITY-SCANNING.md](docs/SECURITY-SCANNING.md) |
| **Lint** | `lint.yml` | Text I/O must pass `encoding="utf-8"` (the Windows default corrupts `SKILL.md`), and every workflow `pip install` must be pinned |
| **YARA rule tests** | `yara-rules-test.yml` | The custom YARA rules the security scan depends on compile and still match their fixtures |

**Advisory** — these report but do not block:

| Check | Workflow | What it reports |
|---|---|---|
| **Static analysis** | `semgrep.yml` | Semgrep over the repo's Python: code *structure* (injection, unsafe deserialization), where the security scan covers skill *content*. Fails on error-severity findings; not yet a required check |

> A green tick is a floor, not a verdict. Nothing checks whether an eval is *meaningful*, or
> whether a group skill's `allowed-tools` is genuinely minimal — that's what review is for.

These gates govern **skills**, which is all the catalog ships today. Hooks, subagents, slash
commands and MCP connectors are security-scanned and reported to Infosec, but are not validated,
not eval-covered, and have no written review standard yet — see
[docs/GOVERNANCE-ROADMAP.md](docs/GOVERNANCE-ROADMAP.md).

### Notifications

| When | Workflow | Who it tells |
|---|---|---|
| Run manually (Actions → Governance summary) | `governance-summary.yml` | IT AI x InfoSec x Legal Review — PR status, approvals and check results in one Teams card |
| Run manually for a merged PR (Actions → Infosec skill notification) | `infosec-notify.yml` | Infosec/Legal — a plain-language card per changed skill |

Both cards are paused from posting automatically for now; run the workflow by hand to post one.
`evals/` changes post no Infosec card.

### Approval

For a PR that sends an AI-AskOps request, approval happens outside GitHub and is the same for
every tier:

1. A maintainer runs the PR Brief workflow for the PR, which sends its ASKOPS answers to
   AI-AskOps **when a request is due** (a new skill or plugin, or a functional change; other PRs
   need code review only, a path pending governance agreement). Triage names a
   **business owner** and an **IT owner**.
2. The business owner and the IT owner approve.
3. **IT AI x InfoSec x Legal Review** approves through the AI Feature Enablement OneTrust form (either
   automatically or at the joint office hours).
4. The IT owner submits the [attestation form](https://forms.cloud.microsoft/r/AcXZk2qrkh).
5. A **repo admin** merges the PR.

See [CONTRIBUTING.md § Approval process](CONTRIBUTING.md#approval-process).

Enforcement lives in a single **repository ruleset** scoped to `main`: required checks, merges
restricted to repo admins, no direct pushes, no force-push, no branch deletion. See
[docs/BRANCH-PROTECTION.md](docs/BRANCH-PROTECTION.md). Testing the gates without the full org
setup is covered in [docs/TESTING.md](docs/TESTING.md).

**Bringing a whole group into the catalog** is a different path from contributing a skill —
see [docs/GROUP-ONBOARDING.md](docs/GROUP-ONBOARDING.md).

---

## Deployment

Pick one path from below — the step-by-step lives in the linked guides:

| You want to… | How | Guide |
|---|---|---|
| **Use skills in Claude Code** (yourself) | `/plugin marketplace add gatesfoundation/gf-agent-publish` → `/plugin install <plugin>@gf-agent-skills` — self-service; Claude Code is not centrally managed here | [Getting Started](docs/GETTING-STARTED.md) |
| **Deploy on Claude.ai** (admin) | All-staff skill upload, or per-group plugins (Cowork) | [claude-ai-deployment.md](docs/claude-ai-deployment.md) |
| **Pull skills into your own repo** | Git submodule under `.claude/skills/` | ↓ below |

> **One source, two models:** on Claude.ai an Owner assigns each plugin to groups. Claude Code has
> no assignment step: registering a plugin in `marketplace.json` makes it installable by anyone.
> See [claude-code-deployment.md](docs/claude-code-deployment.md).

### Git submodule (optional — pull skills into your own repo)

*Optional, advanced.* For Claude Code users who want specific skills vendored into user's local project repo:

```bash
git submodule add https://github.com/gatesfoundation/gf-agent-publish.git .claude/skills/gf-agent-skills
git submodule update --remote --merge   # later, to pull updates
```

Then reference the specific `SKILL.md` files you need in your project's `CLAUDE.md`. (Dependabot
can automate submodule updates — `package-ecosystem: "gitsubmodules"`.)

---

## Contributing

1. Add your skill under the right location (see below) with a `SKILL.md` and `evals/evals.json`.
2. Validate locally, then open a PR. The automated gates run on it, then the approval process.

```bash
# Validate all skills (default — no flags needed)
python scripts/validate_skill.py

# Package for Claude.ai upload: one ZIP per skill, or one ZIP for the plugin
python scripts/deploy.py --package --plugin idm-software-tools
python scripts/deploy.py --plugin-zip --plugin idm-software-tools
```

See [CONTRIBUTING.md](CONTRIBUTING.md), [docs/SKILL_TEMPLATE.md](docs/SKILL_TEMPLATE.md), and
[docs/EVALS.md](docs/EVALS.md).

**Bringing a whole group in?** That's a different path — see
[docs/GROUP-ONBOARDING.md](docs/GROUP-ONBOARDING.md).

### Where does my skill go?

See [CONTRIBUTING.md § Which route does my plugin take?](CONTRIBUTING.md#which-route-does-my-plugin-take) —
**ownership decides location.** In short:

**Foundation-wide** (`plugins/foundation-wide/<task>/skills/`) — useful to all GF staff. Like the
other two roots, this is a namespace of task plugins (`legal/`, `design/`), not one big plugin.

**Team-owned** (`plugins/groups/<your-group>/<task>/skills/`) — scoped to one team. There's no fixed
list of groups; your team's directory is created by the PR that adds its first task plugin (see
[docs/PLUGIN_TEMPLATE.md](docs/PLUGIN_TEMPLATE.md)).

**Shared** (`plugins/shared-workflows/<task>/skills/`) — no single team owns it.

All three go through the same [approval process](CONTRIBUTING.md#approval-process).

---

## Repository Structure

```
gf-agent-publish/
├── .claude-plugin/
│   └── marketplace.json              ← marketplace registry (lists the plugins)
├── .github/
│   ├── CODEOWNERS                    ← routes required reviewers per path
│   └── workflows/
│       ├── validate.yml              ← skill structure + metadata (required check)
│       ├── pr-evals.yml              ← per-PR evals for changed skills (required)
│       ├── run-evals.yml             ← full eval suite (nightly + manual)
│       ├── security-scan.yml         ← cisco scanner + YARA over the whole plugin tree (required)
│       ├── lint.yml                  ← ruff encoding check + workflow install pinning (required)
│       ├── semgrep.yml               ← Semgrep SAST over our Python (report-only)
│       ├── yara-rules-test.yml       ← the YARA rules compile + still match fixtures (required)
│       ├── governance-summary.yml    ← Teams card for IT AI x InfoSec x Legal Review, on review
│       ├── infosec-notify.yml        ← Teams card for Infosec/Legal, manual run
│       └── pages.yml                 ← skills browsing site
│
├── plugins/                           ← parent of all three plugin roots
│   ├── foundation-wide/               ← namespace for all-staff task plugins
│   │   ├── legal/.claude-plugin/plugin.json
│   │   └── design/.claude-plugin/plugin.json
│   │
│   ├── groups/                        ← one namespace per team that has shipped a plugin
│   │   └── idm/                       ← namespace, NOT a plugin — can hold more than one task plugin
│   │       └── software-tools/        ← plugin: idm-software-tools (idm's only task plugin today)
│   │           ├── .claude-plugin/plugin.json
│   │           └── skills/<skill>/    ← SKILL.md + evals/evals.json
│   │   └── <team>/                    ← created by the PR that adds that team's first task plugin
│   │
│   └── shared-workflows/              ← namespace: tasks no single team owns
│       └── <task>/                    ← plugin: <task> (no team prefix)
│
├── scanner/
│   ├── custom-yara/gf_agent_safety.yara   ← foundation security rules (loaded by security-scan)
│   ├── scan_plugin_tree.py                ← sweeps hooks/subagents/commands/MCP the scanner misses
│   └── tests/                             ← fixtures proving the YARA rules still match
│
├── scripts/
│   ├── deploy.py                     ← package skill or plugin ZIPs for Claude.ai upload
│   ├── validate_skill.py             ← structure + metadata validation (CI)
│   ├── check_plugin_versions.py      ← changed skills must bump their plugin's version (CI)
│   ├── run_evals.py                  ← run evals against a live Claude model
│   ├── generate_evals.py             ← draft evals for a new skill
│   ├── check_pinned_installs.py      ← every workflow `pip install` must be pinned (CI)
│   ├── governance_summary.py         ← build the IT AI x InfoSec x Legal Review PR summary
│   ├── teams_card.py                 ← render/post the Teams cards
│   ├── preview_infosec_cards.py      ← render a PR's Infosec cards locally, without posting
│   ├── scan_repo_skills.py           ← generate the skills browsing UI
│   └── utils.py                      ← PII masking + fixture/secret helpers
│
└── docs/
    ├── GETTING-STARTED.md            ← how staff install & use the skills
    ├── GROUP-ONBOARDING.md           ← how a team lead brings a group in
    ├── OVERVIEW-SLIDES.md            ← deck: the design, for team leads
    ├── claude-code-deployment.md     ← Claude Code: self-service, not centrally managed
    ├── claude-ai-deployment.md       ← Claude.ai org-wide deployment (admin)
    ├── BRANCH-PROTECTION.md          ← how the gates are enforced
    ├── SECURITY-SCANNING.md          ← the security model + custom rules
    ├── GOVERNANCE-ROADMAP.md         ← hooks/connectors/subagents: what's covered, what isn't
    ├── TESTING.md                    ← testing the governed PR flow
    ├── EVALS.md
    ├── SKILL_TEMPLATE.md
    ├── SKILL_FOLDER_TEMPLATE.md
    └── PLUGIN_TEMPLATE.md
```
