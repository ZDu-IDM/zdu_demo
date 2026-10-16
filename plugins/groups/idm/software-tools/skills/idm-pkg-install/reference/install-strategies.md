# IDM Package Install — Reference

Full bash commands, strategy playbooks, package tables, and failure mode fixes.
Referenced by [../SKILL.md](../SKILL.md).

---

## Resolving the repo path

```bash
# Local path — use as-is
REPO=/path/to/repo

# GitHub URL — clone first
git clone https://github.com/InstituteforDiseaseModeling/emodpy /tmp/idm-repo
REPO=/tmp/idm-repo

# Specific branch or tag
git clone --branch v2.1.0 https://github.com/org/repo /tmp/idm-repo

# No path given — use current directory
REPO=$(pwd)
```

---

## Repo inspection commands

```bash
# Check for a README
cat $REPO/README.md 2>/dev/null | head -100

# Check for install/dependency files
ls $REPO/setup.py \
   $REPO/setup.cfg \
   $REPO/pyproject.toml \
   $REPO/requirements*.txt \
   $REPO/environment*.yml \
   $REPO/Makefile \
   $REPO/INSTALL* 2>/dev/null
```

**Strategy priority:** `pyproject.toml` > `setup.py/setup.cfg` > `environment.yml` > `requirements.txt` > `Makefile` > manual

| File present | Strategy |
|---|---|
| `pyproject.toml` | Modern Python package (PEP 517/518) |
| `setup.py` or `setup.cfg` | Legacy Python package |
| `environment.yml` | Conda environment |
| `requirements.txt` | Pip dependencies only |
| `Makefile` with `install` target | Makefile-driven |
| None of the above | Script collection — infer deps manually |

---

## Environment isolation

Check required Python version first:

```bash
grep -E 'python_requires|python >=|python==' \
  $REPO/pyproject.toml $REPO/setup.py $REPO/setup.cfg $REPO/environment.yml 2>/dev/null
```

**Option A — venv:**
```bash
python -m venv $REPO/.venv
source $REPO/.venv/bin/activate       # Linux/macOS
# $REPO\.venv\Scripts\activate        # Windows
```

**Option B — conda:**
```bash
conda create -n idm-env python=3.9
conda activate idm-env
```

**Option C — use repo's environment.yml directly:**
```bash
conda env create -f $REPO/environment.yml
conda activate $(grep '^name:' $REPO/environment.yml | awk '{print $2}')
```

Confirm the environment is active:
```bash
which python    # should point inside .venv or the conda env, not system Python
```

---

## Install strategies

### pyproject.toml (modern IDM packages — emod-api, emodpy, laser, etc.)

```bash
cd $REPO
grep -A3 '\[build-system\]' pyproject.toml          # check build backend
grep '\[project.optional-dependencies\]' pyproject.toml -A 30  # check extras
```

**Dev install:**
```bash
pip install -e ".[dev]" --break-system-packages      # with dev extras
pip install -e . --break-system-packages             # without extras
```

**Prod install:**
```bash
pip install ".[dev]" --break-system-packages         # with dev extras
pip install . --break-system-packages                # without extras
```

**If build backend is `poetry`:**
```bash
pip install poetry --break-system-packages
poetry install           # dev (editable by default)
poetry install --no-dev  # prod
```

**If build backend is `hatch`:**
```bash
pip install hatch --break-system-packages
hatch env create && hatch shell      # dev
pip install . --break-system-packages               # prod
```

---

### setup.py / setup.cfg (older IDM repos — EMOD, FPsim, older emodpy)

```bash
cd $REPO
grep 'extras_require' setup.py setup.cfg 2>/dev/null
grep 'cmdclass\|install_requires\|dependency_links' setup.py | head -20
```

**Dev install:**
```bash
pip install -e . --break-system-packages
pip install -e ".[test]" --break-system-packages     # with extras
```

**Prod install:**
```bash
pip install . --break-system-packages
pip install ".[test]" --break-system-packages        # with extras
```

> ⚠ Never run `python setup.py install` — deprecated; bypasses pip's resolver.

---

### environment.yml (Conda — compiled C/Fortran dependencies)

```bash
cd $REPO
cat environment.yml

conda env create -f environment.yml                  # create fresh
# or:
conda env update -f environment.yml --prune          # update existing

conda activate $(grep '^name:' environment.yml | awk '{print $2}')
```

If repo also has `pyproject.toml` or `setup.py`, install the package inside the activated env:
```bash
pip install -e . --break-system-packages             # dev
pip install . --break-system-packages                # prod
```

If multiple environment files exist (`environment-dev.yml`, `environment-gpu.yml`), ask the user which to use.

---

### requirements.txt (script repos with no installable package)

```bash
cd $REPO
cat requirements*.txt

pip install -r requirements.txt --break-system-packages
# Multiple files:
pip install -r requirements.txt -r requirements-dev.txt --break-system-packages
```

---

### Makefile

```bash
cd $REPO
make help 2>/dev/null || grep -E '^[a-zA-Z_-]+:' Makefile | head -20
make install    # or: make dev / make setup
```

---

### No install file (script collection)

```bash
cd $REPO
grep -rh '^\s*import \|^\s*from ' --include="*.py" . \
  | sed 's/^\s*//' | sort -u | head -50

pip install pipreqs --break-system-packages
pipreqs . --savepath /tmp/inferred_requirements.txt 2>/dev/null
pip install -r /tmp/inferred_requirements.txt --break-system-packages
```

---

## PyPI install commands

```bash
pip install <package-name> --break-system-packages                # latest
pip install <package-name>==1.2.3 --break-system-packages         # specific version
pip install <pkg-a> <pkg-b> <pkg-c> --break-system-packages       # multiple
pip install "<package-name>[extras]" --break-system-packages      # with extras
pip install --upgrade <package-name> --break-system-packages      # upgrade

# Check available versions before installing
pip index versions <package-name> 2>/dev/null

# Verify after install
pip show <package-name>
python -c "import <package_name>; print(<package_name>.__version__)"
```

---

## IDM packages on PyPI

Always query PyPI at runtime rather than relying on a hardcoded list — packages are added and
deprecated frequently.

**Check if a package exists and see available versions:**
```bash
pip index versions <package-name> 2>/dev/null
```

If the command returns nothing, the package is not on PyPI — redirect to GitHub or local path
workflow instead.

**Not on PyPI — require GitHub or Artifactory:**

| Pattern | Source |
|---|---|
| `dtk-*` (legacy) | IDM internal Artifactory — requires IDM network access |
| Unreleased branches / forks | GitHub URL install |

---

## Checking for IDM-internal packages

```bash
grep -E 'git\+|artifactory|idm-bamboo|idm\.local' \
  requirements*.txt setup.py pyproject.toml 2>/dev/null
```

If found, flag to the user before proceeding — these require IDM network access or credentials.

---

## Common failure modes

| Symptom | Likely cause | Fix |
|---|---|---|
| `ModuleNotFoundError` after install | Wrong environment active | Activate correct venv/conda env and re-run |
| `Could not find a version that satisfies the requirement` | Package not on PyPI | Use GitHub/local install instead |
| `Microsoft Visual C++ required` (Windows) | C extension needs compiler | Install Build Tools for Visual Studio 2022 |
| `pip` installs but `import` fails | Installed to wrong Python | Use `python -m pip install` instead of bare `pip` |
| Dependency conflict | Conflicting version pins | Fresh env; install `--no-deps` then resolve manually |
| `git clone` auth error | Private IDM repo | Add SSH key or use PAT for HTTPS |
| `setup.py egg_info` fails | Missing `setuptools` | `pip install setuptools --upgrade --break-system-packages` |
| `pyproject.toml` build fails | Missing build backend | `pip install build wheel --break-system-packages` |
| `conda env create` hangs | Solver timeout | `conda env create -f environment.yml --solver=libmamba` |
