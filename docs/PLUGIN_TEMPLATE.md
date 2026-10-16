# Plugin Template

A **plugin** is a collection of skills bundled together for a specific audience. Each plugin lives in its own directory with a `plugin.json` registry file.

---

## Folder structure

### Foundation-wide plugin

```
plugins/foundation-wide/<task>/
├── .claude-plugin/
│   └── plugin.json          "name": "<task>"   ← no prefix
└── skills/
    └── <skill-name>/
        ├── SKILL.md
        └── evals/
            └── evals.json
```

`plugins/foundation-wide/` holds task plugins (`legal/`, `design/`), like the other two roots.

### Team-owned and shared plugins

```
Team-owned                              Shared (centrally owned)
plugins/groups/<team>/<task>/           plugins/shared-workflows/<task>/
├── .claude-plugin/                     ├── .claude-plugin/
│   └── plugin.json                     │   └── plugin.json
│       "name": "<team>-<task>"         │       "name": "<task>"     ← no prefix
└── skills/                             └── skills/
    └── <skill>/                            └── <skill>/
        ├── SKILL.md                            ├── SKILL.md
        └── evals/evals.json                    └── evals/evals.json
```

`plugins/groups/<team>/`, `plugins/shared-workflows/` and `plugins/foundation-wide/` are all
**namespaces, not plugins** — none has a `.claude-plugin/` of its own. See
[CONTRIBUTING.md](../CONTRIBUTING.md) for which route to use.

Two plugins may not declare the same name. `foundation-wide/legal` and `shared-workflows/legal`
would both be `legal`, so `validate_skill.py` rejects that.

There's no fixed list of group names: a team's first task plugin creates `plugins/groups/<team>/`.

---

## plugin.json

### Foundation-wide

```json
{
  "name": "legal",
  "version": "1.0.0",
  "description": "Foundation-wide legal skills, available to all GF staff."
}
```

### Team-owned

```json
{
  "name": "<team>-<task>",
  "version": "1.0.0",
  "description": "Skills for the [Your Group] team's [task]."
}
```

### Shared (centrally owned)

```json
{
  "name": "<task>",
  "version": "1.0.0",
  "description": "Skills for [task], used by more than one team."
}
```

**Rules:**
- `name` must be unique across all plugins and use kebab-case
- `version` versions every skill in the plugin (`SKILL.md` has none). A quoted semver string:
  bump it for any change under `skills/` except `evals/` — minor for a new skill, patch for
  fixes. See [CONTRIBUTING.md § Versioning](../CONTRIBUTING.md#versioning)

### The `skills` list (optional)

You don't need one: every `skills/<name>/SKILL.md` loads automatically. If you add a list, for
example as an inventory:

```json
  "skills": [
    "./skills/your-skill-name"
  ]
```

- It adds to the automatic loading, so removing an entry doesn't remove the skill. To retire a
  skill, delete its folder.
- Each entry must point at a real `SKILL.md` inside this plugin's own `skills/`.
- A skill nested deeper than `skills/<name>/` (e.g. `skills/data/clean-csv/`) **must** be listed,
  or it never loads.

### Author (optional)

Names who maintains the plugin. It replaces the old per-skill `owner`, and the AI-AskOps request
and the Infosec card show it (or "not declared").

```json
  "author": {
    "name": "IDM Software Tools team",
    "email": "idm-tools@gatesfoundation.org"
  }
```

`email` is optional, but an `author` **must have a `name`** and must be an object, not a
`"Jane <jane@x.org>"` string. Claude Code won't load a plugin that breaks either rule, so the
validator checks both.

---

## Adding a new skill to an existing plugin

1. Create the skill directory and files at `skills/<skill-name>/` (see
   [SKILL_TEMPLATE.md](SKILL_TEMPLATE.md))
2. Leave `skills` in `plugin.json` alone — unless the plugin already keeps a `skills` list, in
   which case add the new path so the inventory stays complete
3. Bump `version` (minor bump)
4. Run validation: `python scripts/validate_skill.py`
