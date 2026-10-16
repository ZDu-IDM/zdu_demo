# Security Scanning

Every skill is scanned for security red flags before it can reach users. This implements the
foundation's requirement that "downloaded skills are vetted for security."

## The tool

We use [`cisco-ai-skill-scanner`](https://github.com/cisco-ai-defense/skill-scanner) — a
purpose-built scanner for Agent Skills. It detects red flags such as: data exfiltration,
credential/token requests, reading agent memory/identity files, network calls to raw IPs, silent
package installs, writing outside the workspace, obfuscated code, and browser-cookie access.

It has multiple engines:
- **Static analysis** (regex/AST) — fast, no key.
- **Behavioral dataflow** (`--use-behavioral`) — traces tainted data to sinks.
- **LLM semantic analysis** (optional) — needs `SKILL_SCANNER_LLM_API_KEY`.

## Two-tier model

| Tier | When | Engines | Gate |
|---|---|---|---|
| **Automated (CI)** | Every PR touching a skill | Static + behavioral | `security-scan.yml` — **fails on HIGH**, uploads SARIF to Code Scanning |
| **Deep review (human)** | Foundation-wide + anything the panel flags | + LLM semantic + manual read | Security/compliance reviewer on the Skills Working Group |

The CI tier is a fast, keyless gate that blocks obvious problems. The deep tier is part of the
human review for higher-tier skills (workstream owned with InfoSec).

## Run it locally (before opening a PR)

```bash
pip install cisco-ai-skill-scanner==2.0.13   # pinned to match CI

# scan one skill
skill-scanner scan plugins/groups/idm/software-tools/skills/idm-pkg-install

# scan everything, like CI does
skill-scanner scan-all . --recursive --use-behavioral --fail-on-severity high

# optional LLM semantic pass
export SKILL_SCANNER_LLM_API_KEY=...   # and SKILL_SCANNER_LLM_MODEL
skill-scanner scan <path> --use-llm
```

Findings appear as inline PR annotations (via SARIF → GitHub Code Scanning) and in the Actions log.

## Where findings show up

The CI job uploads SARIF, so results appear under the repo's **Security → Code scanning** tab and
as inline comments on the PR diff.

## Finding categories

Each finding is tagged with a category. Use these when triaging scan output and when assigning a
`category` to any custom rule you add:

| category | Meaning |
|----------|---------|
| `prompt_injection` | Prompt injection and instruction override |
| `command_injection` | Command and code injection |
| `data_exfiltration` | Data exfiltration and privacy violations |
| `unauthorized_tool_use` | Unauthorized tool use and permission abuse |
| `obfuscation` | Code obfuscation and malware indicators |
| `hardcoded_secrets` | Hardcoded secrets and credential leakage |
| `social_engineering` | Social engineering and misleading metadata |
| `resource_abuse` | Resource abuse and denial of service |
| `policy_violation` | Policy violation |

## Custom foundation rules

The built-in rules cover most red flags. We add a small set of foundation-specific YARA rules in
`scanner/custom-yara/gf_agent_safety.yara`, loaded via `--custom-rules scanner/custom-yara` (see
the workflow). They cover three checks that are especially relevant to us and not in the built-in
set:

- **`vetter_agent_memory_theft`** — a skill reading agent memory/identity files (`MEMORY.md`,
  `.claude/memory`, `claude_desktop_config.json`, …). Claude-specific and high value.
- **`vetter_ip_exfiltration`** — network calls to raw IPs (bypasses DNS logging), excluding private ranges.
- **`vetter_browser_data_theft`** — access to browser cookies / saved-login / profile databases.

These were derived from the [skill-vetter](https://clawhub.ai/spclaudehome/skill-vetter) RED FLAGS.

### Why only YARA (not the regex signatures)

The CLI's `--custom-rules` flag loads **YARA** rules only; custom regex signatures use a separate
`--rule-packs` mechanism (packaged packs, not loose YAML). The remaining skill-vetter regex rules
(curl/wget, credential prompts, system-file writes, silent installs, base64/eval/exec, sudo,
obfuscation) overlap with the scanner's **built-in** signature + behavioral engines, so we rely on
those rather than maintaining our own regexes.

> Before adding more custom rules, confirm the built-in set doesn't already cover the gap (run the
> scanner on a test skill). Add YARA rules for genuine gaps only, each with a comment explaining the
> threat it addresses.

## First-run checklist

- [ ] Confirm the SARIF output flag with `skill-scanner scan-all --help` (some versions use
      `--output <file>`, others write to stdout — adjust `security-scan.yml` accordingly).
- [ ] Confirm `--fail-on-severity` threshold matches policy (default `high`).
- [ ] Add **"Scan skills for security red flags"** to branch protection's required checks
      (see [BRANCH-PROTECTION.md](BRANCH-PROTECTION.md)).


## Trust boundary: merge-time vs runtime

The gates in this repo (validation, evals, this security scan, review) run **at merge
time** — they vet a skill as text/files before it is published. They do **not** constrain
what a skill's instructions cause the agent to do once it is installed and invoked.

Runtime safety has one layer today:

1. **Per-skill `allowed-tools` (declared, checked at merge)** — every skill declares the
   minimal tools it needs. `validate_skill.py` blocks foundation-wide (all-staff) skills
   from requesting high-power tools (`Bash`, `PowerShell`, `Write`, `Edit`, `NotebookEdit`)
   and warns when group skills request them.

There is no second, host-enforced layer on top of it. An earlier design pushed a host
`managed-settings.json` (`deploy/managed-settings/`) to Claude Code machines via MDM, scoping
high-power tools per group at runtime and denying dangerous patterns
(`curl`/`wget`/`sudo`/`rm -rf`, secret-file reads) with precedence over user settings. That
directory has been removed: Claude Code is not centrally managed here — see
[claude-code-deployment.md](claude-code-deployment.md) — so there was no consumer for it, and a
policy file nobody enforces is worse than no policy file. The keys it would have set
(`strictKnownMarketplaces`, `disableSideloadFlags`, `strictPluginOnlyCustomization`, and the
per-tool `allow`/`ask`/`deny` block) still exist in Claude Code and remain available if the
foundation brings that surface under management later.

Until then, a skill's `allowed-tools` is enforced only by the model honoring it and by whatever
permissions the individual user's own Claude Code settings grant — not by anything this repo
pushes to their machine. "Scanned + evals-passed" means the skill *text* was vetted — it does
**not** mean a group skill's high-power tools are constrained by anything beyond the user's own
local configuration.
