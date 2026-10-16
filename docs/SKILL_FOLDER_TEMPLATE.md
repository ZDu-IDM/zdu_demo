# Skill Folder Template

The layout of a skill directory. Only `SKILL.md` and `evals/evals.json` are required. Everything else is optional and included only when the skill needs it.

---

## Full layout

```
your-skill-name/
│
├── SKILL.md                        ← required: skill definition and instructions
│
├── evals/
│   ├── evals.json                  ← required: eval test cases (aim for 3+)
│   └── fixtures/
│       ├── synthetic/              ← synthetic test inputs safe to commit
│       │   └── example.json
│       └── real/                   ← real data fixtures — do NOT commit (add to .gitignore)
│           └── .gitkeep
│
├── reference/                      ← optional: supporting reference material the skill reads
│   ├── glossary.md
│   ├── taxonomy.md
│   └── style-guide.md
│
├── assets/                         ← optional: images, diagrams, or other static files
│   └── diagram.png
│
├── scripts/                        ← optional: helper scripts used by the skill at runtime
│   └── fetch_data.py
│
└── rules/                          ← optional: modular rule files loaded selectively by the skill
    ├── formatting.md
    └── validation.md
```

---

## What each folder is for

| Folder | Purpose |
|---|---|
| `evals/` | Test cases that CI runs to gate PRs. Always required. |
| `evals/fixtures/synthetic/` | Canned inputs for `--fixture` testing. Safe to commit. |
| `evals/fixtures/real/` | Real user data for local testing only. Never commit — add to `.gitignore`. |
| `reference/` | Structured reference material (glossaries, taxonomies, style guides) the skill loads as context. |
| `assets/` | Images, diagrams, or other static files referenced by `SKILL.md`. |
| `scripts/` | Helper scripts the skill invokes at runtime (e.g. data fetchers, formatters). |
| `rules/` | Modular rule files loaded selectively — useful for large skills to keep `SKILL.md` focused. |

---

## Minimal layout (most skills)

Most skills only need two files:

```
your-skill-name/
├── SKILL.md
└── evals/
    └── evals.json
```

Add folders only when the skill genuinely needs them. See [SKILL_TEMPLATE.md](SKILL_TEMPLATE.md) for the `SKILL.md` contents and [EVALS.md](EVALS.md) for the `evals.json` schema.
