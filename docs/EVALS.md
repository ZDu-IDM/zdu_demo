# Skill Evals

How to write and run a skill's evals.

---

## Quick start

```bash
pip install "anthropic>=0.50,<1" pyyaml   # <1: 1.0 dropped `temperature`, which run_evals.py uses
export ANTHROPIC_API_KEY=sk-ant-...        # PowerShell: $env:ANTHROPIC_API_KEY = "sk-ant-..."

# Your changed skills, as CI runs them (works before your plugin is registered)
python scripts/run_evals.py --changed-from origin/main

# One skill in a registered plugin
python scripts/run_evals.py --plugin idm-software-tools --skill idm-pkg-install

# With your own fixture file: a bare filename is looked up in the skill's
# evals/fixtures/synthetic/ or evals/fixtures/real/; a path is used as given
python scripts/run_evals.py --plugin idm-software-tools --skill idm-pkg-install --fixture edge_cases.json

# With real data, masking PII in the saved results
TEST_REAL_FIXTURES_PATH=/path/to/real.json \
  python scripts/run_evals.py --plugin idm-software-tools --skill idm-pkg-install --real --mask-output --output results.json
```

`--plugin` only accepts plugins registered in `marketplace.json`.

---

## evals.json

Every skill needs `evals/evals.json`: an object with a non-empty `evals` array. A blank file, `{}`,
`{"evals": []}` or a bare top-level array fails the `validate` check.

```json
{
  "skill_name": "<skill-name>",
  "evals": [ ...cases... ]
}
```

### Eval types

Each case has an optional `type`:

| Type | What it is | Required fields | Gates the PR? |
|---|---|---|---|
| `single_shot` (default) | One prompt, one response, graded by keyword `rubric` | `id`, `name`, `prompt`, `rubric` | Yes |
| `workflow` | A multi-turn conversation graded by an LLM judge against `pass_condition` | `id`, `name`, `type: "workflow"`, `steps`, `pass_condition` | No, unless `--strict-workflow` |

Any case can also have a `description` explaining what it tests and why. Use `<skill-prefix>-NNN`
for `id`s; `name` is the label shown in CI.

### Rubric fields (single-shot)

| Field | Type | Passes when |
|---|---|---|
| `must_include` | `string[]` | every string appears (case-insensitive substring) |
| `must_include_one_of` | `string[]` | at least one string appears |
| `must_include_groups` | `string[][]` | each group has at least one match (AND-of-ORs) |
| `must_not_include` | `string[]` | none of the strings appear |
| `pass_condition` | `string` | not checked; shown to humans in CI |

### Example

```json
{
  "skill_name": "meeting-summary",
  "evals": [
    {
      "id": "ms-001",
      "name": "Asks clarifying questions for a vague request",
      "description": "Vague request — agent should ask for missing context, not produce output immediately.",
      "prompt": "Summarize this meeting for me.",
      "rubric": {
        "must_include_one_of": ["transcript", "notes", "could you share", "which meeting"],
        "must_not_include": ["action items", "attendees"],
        "pass_condition": "Agent asks for the meeting transcript or notes before producing a summary"
      }
    },
    {
      "id": "ms-002",
      "name": "Produces all required sections for a complete brief",
      "description": "Fully-specified request — agent should produce a complete summary with all required sections.",
      "prompt": "Summarize this meeting transcript for the Q3 budget review: [attendees: Priya, Sam, Dana. Decisions: approve $150K for the data pipeline. Action items: Sam to draft the budget memo by Friday.]",
      "rubric": {
        "must_include": ["Attendees", "Decisions", "Action Items"],
        "must_include_groups": [
          ["Owner", "assigned to", "responsible"],
          ["Due", "deadline", "by Friday"]
        ],
        "pass_condition": "Summary contains Attendees, Decisions, and Action Items sections plus owners and due dates"
      }
    },
    {
      "id": "ms-003",
      "name": "Declines to summarize content outside scope",
      "description": "Request outside the skill's scope — agent should decline rather than attempt it.",
      "prompt": "Summarize this quarterly earnings report as a meeting summary.",
      "rubric": {
        "must_include_one_of": ["out of scope", "not a meeting", "not able to", "meeting transcript"],
        "pass_condition": "Agent declines or redirects — this skill is for meeting transcripts, not other document types"
      }
    }
  ]
}
```

A workflow case:

```json
{
  "id": "wf-install-flow",
  "name": "Install flow asks source then isolates env",
  "type": "workflow",
  "steps": [
    "Install somepkg for me.",
    "Use PyPI, into a fresh venv."
  ],
  "pass_condition": "Across the turns the agent asks which source to install from and sets up an isolated environment (venv/conda) before installing."
}
```

---

## Writing meaningful evals

**A good eval tests what the agent would get wrong without the skill.** If a case passes with the
skill removed, it's testing the model. Aim for 3 or more cases, and at least one
`must_not_include`.

- **Weak:** `"must_include_one_of": ["the", "a", "is"]`
- **Strong:** does the agent ask for missing context instead of inventing it? Decline out-of-scope
  requests? Include every required section when given a complete brief?

---

## Workflow evals: seeing what happened

Each workflow result includes the judge's reason, on a pass as well as a fail:

- **default output:** the status line, plus a one-line `judge:` reason;
- **`--verbose`:** also the full `USER:` / `ASSISTANT:` transcript;
- **`--output results.json`:** everything, including the full `transcript`, `judge_reason`,
  `pass_condition`, `status` and `steps`.

```
  pkg-workflow-001  End-to-end install flow across multiple turns [workflow·advisory]  (12.3s)
       judge: the agent asked for the source, verified on PyPI, used a venv, and confirmed the install
```

Run one workflow eval while iterating (`--judge-model <model>` picks the judge):

```bash
python scripts/run_evals.py --plugin idm-software-tools --skill idm-pkg-install \
  --eval-id pkg-workflow-001 --verbose --output wf.json
```

---

## Sensitive data

| Data | Where it lives | Committed? |
|---|---|---|
| Synthetic fixtures | `evals/fixtures/synthetic/` | ✅ Yes |
| Real fixtures | `evals/fixtures/real/` | ❌ No (git-ignored) |
| API keys / passwords | `.env` | ❌ No (git-ignored) |
| Real fixture path | `TEST_REAL_FIXTURES_PATH` env var | ❌ No |

- **Never** put real SSNs, dates of birth, employee IDs, passwords or API keys in fixture files.
- **Always** mask results with `SensitiveDataMasker` (`scripts/utils.py`) before logging or saving
  them.
- **Always** load secrets with `EnvSecrets.require()` (`scripts/utils.py`); never hard-code them.
