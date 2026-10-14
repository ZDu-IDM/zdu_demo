---
name: idm-pkg-install
description: |
  Use this skill when installing, setting up, or troubleshooting any IDM package or repository.
  Triggers include: installing a released IDM package from PyPI, setting up an IDM project environment from a GitHub
  repo or local path, running pip install or conda install against an IDM codebase, resolving dependency conflicts in
  scientific Python environments, or any request like 'install emodpy', 'install this repo', 'set up the environment
  for', or 'get this running'. Use this skill before writing any install commands - it determines the correct source,
  strategy, and mode based on what the user wants.
argument-hint: "[pypi|github-url|local-path] [dev|prod|requirements]"
allowed-tools: Bash, Read, Edit, Glob, Grep
metadata:
  owner: gf-ai-skills@gatesfoundation.org
  version: "1.0.2"
  surfaces: claude-code   # installs into the user's own Python environment
---

# IDM Package Installation Skill

Installs IDM packages from PyPI, a GitHub repo, or a local path. Always confirm source and mode
with the user before running anything.

For all bash commands, strategy details, PyPI package list, and failure mode fixes, see
[reference/install-strategies.md](reference/install-strategies.md).

---

## Instructions

### Step 1 — Confirm install source and environment

If the install source is not already specified, ask:

> "Where should I install from?
> 1. **PyPI** — install a released version by package name (e.g. `emodpy==2.1.0`)
> 2. **GitHub repo** — clone and install from a GitHub URL
> 3. **Local path** — install from a directory on this machine (default: current directory)"

At the same time (or immediately after), if no environment has been specified, ask — using **venv** and **conda** by name:

> "Should I create a **venv** or a **conda** environment? Or do you have an existing environment to activate?"

Do not use generic phrasing like "set up an environment" — the user needs to see the option names. Do not run any install command until both questions are answered and an isolated environment is ready.

- **PyPI** → run `pip index versions <package-name>` to verify the package exists before proceeding.
  If the command returns nothing, the package is not on PyPI — redirect to GitHub or local path workflow instead.
  Then skip to Step 3 (no repo inspection needed).
- **GitHub / local** → continue to Step 2

---

### Step 2 — Inspect the repo

Read `README.md` and check for install files in this priority order:
`pyproject.toml` > `setup.py/setup.cfg` > `environment.yml` > `requirements.txt` > `Makefile`

See [reference/install-strategies.md](reference/install-strategies.md) for the inspection
commands and what each file means for the install strategy.

---

### Step 3 — Create an isolated environment (MANDATORY — never skip)

**Do not run any install command until an isolated environment is created and activated.**

Environment type should already be confirmed from Step 1. If not, ask before proceeding:
> "Should I create a **venv** or a **conda** environment? Or do you have an existing environment to activate?"

See [reference/install-strategies.md](reference/install-strategies.md) for environment
creation commands and how to check the required Python version first.

---

### Step 4 — Confirm install mode (GitHub / local only)

Not applicable for PyPI — always installs a fixed release.

- If mode was passed as an argument (`dev`, `prod`, `requirements`) → use it directly, do not ask.
- If only `requirements.txt` found (no `pyproject.toml`, no `setup.py`) → auto-select `requirements`, no prompt.
- If an installable package found → ask: *"**Dev install** (editable, `pip install -e .`) or **prod install** (`pip install .`)?"*

| Mode | Command | When to use |
|---|---|---|
| **Dev** | `pip install -e .` | Actively modifying the package |
| **Prod** | `pip install .` | Using the package as a consumer |
| **Requirements** | `pip install -r requirements.txt` | No installable package, dependencies only |

---

### Step 5 — Check for IDM-internal packages

Before installing, scan for internal dependencies that require IDM network access or credentials.
See [reference/install-strategies.md](reference/install-strategies.md) for the scan command
and which packages require Artifactory or SSH.

Flag any found to the user before proceeding.

---

### Step 6 — Run the install

Use the strategy matching the files found in Step 2 and the mode confirmed in Step 4.
Full bash commands for each strategy are in [reference/install-strategies.md](reference/install-strategies.md).

---

### Step 7 — Verify

```bash
python -c "import <package_name>; print(<package_name>.__version__)"

pytest tests/ -x -q 2>/dev/null \
  || python -m pytest tests/ -x -q 2>/dev/null \
  || make test 2>/dev/null \
  || echo "No test runner found - manual check only"
```

---

## Output Format

Report the following after a successful install:

```
✓  Install complete

Source:       [PyPI | GitHub | local path]
Strategy:     [pyproject.toml | setup.py | environment.yml | requirements.txt | Makefile]
Mode:         [dev (editable) | prod | requirements]
Environment:  [venv at .venv/ | conda env: <name>]
Package:      <package-name> <version>

Caveats:      [any IDM-internal deps flagged, any extras not installed, etc.]
```

If the install fails, report the strategy attempted, the exact error, and the recommended fix
from [reference/install-strategies.md](reference/install-strategies.md).

---

## Limitations

- **Never installs into system Python** — an isolated environment is mandatory before any install command
- **Never runs `python setup.py install`** — deprecated; bypasses pip's resolver and can silently corrupt environments
- Does not handle IDM-internal Artifactory packages automatically — flags them and stops for user confirmation
- Does not run the full test suite by default — runs a quick smoke test only
- For packages not on PyPI (legacy `dtk-*`, unreleased branches), redirects to GitHub or local path workflow
- On Windows, does not use POSIX `find` — uses the Claude Code Glob tool instead
