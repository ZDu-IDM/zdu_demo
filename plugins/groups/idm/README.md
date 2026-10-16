# IDM

Namespace for Institute for Disease Modeling (IDM) task plugins. `plugins/groups/idm/` is a
**namespace, not a plugin** — it can hold more than one task plugin; each task is its own plugin.
Today IDM ships `idm-software-tools` and `idm-data`, with room for more (e.g. disease modeling,
epidemiology research) as they're contributed.

## Install

```
/plugin install idm-software-tools@gf-agent-skills
/plugin install idm-data@gf-agent-skills
```

## Task plugins

| Plugin | Skill | Description |
|---|---|---|
| `idm-software-tools` | `idm-pkg-install` | Install IDM packages from PyPI, GitHub, or local paths |
| `idm-data` | `assess-dataset` | Assess dataset quality and analytical fitness |

## Ownership

| Role | Contact |
|---|---|
| Group owner | gf-ai-skills@gatesfoundation.org |
| Reviewer / team lead | *(to be assigned)* |

## Contributing

1. Read the repo-level [CONTRIBUTING.md](../../../CONTRIBUTING.md) first, including [Which route
   does my plugin take?](../../../CONTRIBUTING.md#which-route-does-my-plugin-take).
2. Add your skill under the right task plugin's `skills/<skill-name>/` (e.g.
   `plugins/groups/idm/software-tools/skills/<skill-name>/`), following the [skill folder
   template](../../../docs/SKILL_FOLDER_TEMPLATE.md). A new task gets its own plugin at
   `plugins/groups/idm/<task>/`.
3. Include `SKILL.md` and `evals/evals.json` — PRs without evals will not be merged. Aim for 3+
   test cases; reviewers look for that, validation only requires a non-empty `evals` array.
4. Open a PR and follow the [approval process](../../../CONTRIBUTING.md#approval-process).

IDM-specific skills that prove stable and broadly useful may be nominated for promotion to `plugins/foundation-wide/` through the Skills Working Group.
