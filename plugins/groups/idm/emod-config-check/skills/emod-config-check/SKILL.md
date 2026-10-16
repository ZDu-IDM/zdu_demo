---
name: emod-config-check
description: Use when checking an IDM EMOD config file for common mistakes before a simulation run. Triggers include 'check my EMOD config', 'why won't my simulation start', 'validate config.json', 'is my Simulation_Duration right', or reviewing an EMOD config.json / campaign.json for missing or inconsistent parameters.
allowed-tools: Read, Grep, Glob
metadata:
  owner: idm@gatesfoundation.org
  version: "0.1.0"
  surfaces: claude-code   # reads config files from the user's local EMOD project
---

# EMOD Config Check

Review an EMOD `config.json` (and related `campaign.json` / demographics
references) for the mistakes that most often make a run fail late or behave
wrong, and report them before the simulation is launched.

## Instructions

Locate the config first. Ask for the path if it was not given, then `Read` it.
EMOD config is JSON with the run parameters under a top-level `parameters`
object; check inside that object, not at the top level.

Work through these classes of problem and report each finding with the exact
parameter name and the file it is in:

1. **Missing required parameters.** Confirm the parameters the run depends on
   are present — e.g. `Simulation_Duration`, `Simulation_Timestep`, and the
   demographics and campaign file references the config points to.
2. **Enable/dependency mismatches.** Many `Enable_*` flags require companion
   parameters. If an `Enable_X` is `1`, check the parameters it implies are set;
   if it is `0`, flag dependent parameters that were set anyway (they are
   silently ignored, which usually means the author expected them to take
   effect).
3. **Type and range errors.** Numbers given as strings, booleans given as
   `true`/`false` where EMOD expects `1`/`0`, negative durations or timesteps,
   probabilities outside `[0, 1]`.
4. **Cross-file consistency.** If a `campaign.json` is present, check that its
   event timing fits inside `Simulation_Duration`, and that demographics files
   named in the config actually exist relative to it (`Glob` to confirm).

Report what is wrong and why it matters; do not rewrite the config unless asked.

## Output Format

A short list, one finding per line: the parameter, the file, what is wrong, and
the fix. Lead with the errors that stop a run, then the ones that change results
silently, then style/redundancy. If nothing is wrong, say so plainly and name
what you checked.

## Limitations

- **Read-only.** It reports problems; it does not edit the config.
- **Structure, not science.** It catches missing, mistyped and inconsistent
  parameters. Whether the *values* are right for your model is yours to judge.
- **Claude Code only.** It reads files from your local EMOD project; Claude.ai's
  sandbox cannot see them.
- **Not exhaustive.** EMOD's parameter set is large and version-dependent; the
  authoritative check is still the schema for your EMOD build.
