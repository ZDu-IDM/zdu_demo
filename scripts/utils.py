"""
scripts/utils.py

Shared utilities for skill evals:
  - skill_metadata        : reads the GF fields out of SKILL.md's `metadata:` map
  - load_plugins          : builds the {plugin-key: path} map from marketplace.json
  - SensitiveDataMasker   : masks PII fields before logging/reporting
  - EnvSecrets            : reads secrets from environment, never from files
"""

import json
import os
import re
from pathlib import Path, PurePosixPath
from typing import Any

REPO_ROOT = Path(__file__).parent.parent
MARKETPLACE_FILE = REPO_ROOT / ".claude-plugin" / "marketplace.json"

# The catalog's plugin roots — the single source of truth. Every consumer
# imports this rather than restating it: the set was once hardcoded in six
# places in five formats, and a missed edit failed silently (an unscanned
# root, or a merge with no Infosec card, both green in CI).
#
# Names are relative to REPO_ROOT, with no trailing slash. Consumers that need
# another form derive it — see preview_infosec_cards.py.
ROOT_FOUNDATION = "plugins/foundation-wide"
ROOT_GROUPS = "plugins/groups"
ROOT_SHARED = "plugins/shared-workflows"

# Order matters only for readability; membership is by exact root match.
PLUGIN_ROOTS: tuple[str, ...] = (ROOT_FOUNDATION, ROOT_GROUPS, ROOT_SHARED)

# GF-specific SKILL.md fields. These live under `metadata:` rather than at the
# top level because the Agent Skills standard defines only name, description,
# license, compatibility and metadata there -- and `metadata` is the one key
# reserved for "data read by your own tooling", which is exactly what these are.
#
# Nesting is not cosmetic. Strict validators reject unknown top-level keys when
# a skill is uploaded (anthropics/claude-code#25380), so a packaged skill
# carrying `owner:` at the top level can fail on arrival. It also keeps the
# catalog clear of a name the standard might later claim for itself.
#
# `allowed-tools` deliberately stays top-level: it is a documented Claude Code
# field, not ours to relocate, and it is the security boundary CI enforces.
GF_METADATA_FIELDS: tuple[str, ...] = ("owner", "version", "surfaces")

# Subdirectories of a skill that are test harness, not skill content. Claude
# never reads them: they must not be copied to .claude/skills/, and must not be
# packaged into a Claude.ai upload -- shipping rubrics and pass conditions out
# of the org is not something anyone asked for.
#
# This names the directories; each caller matches at whatever depth its own base
# implies, and those genuinely differ. deploy.py and refresh_local_skills.py walk
# a skill directory, so evals/ is at parts[0]. split_plugin_changes.relevant() and
# check_skill_versions.relevant() walk paths relative to a PLUGIN root, where the
# same directory sits at skills/<skill>/evals/ -- they test membership anywhere
# below. Do not "simplify" them to one idiom; they would stop matching.
HARNESS_DIRS: tuple[str, ...] = ("evals",)


def skill_metadata(fm) -> dict:
    """Return a SKILL.md's `metadata:` map, or {} when it has none.

    Always returns a dict so callers can `.get()` without guarding. A non-dict
    `metadata:` (someone writing a string or list) yields {}, which surfaces as
    the required fields being missing -- the message a reader can act on.
    """
    if not isinstance(fm, dict):
        return {}
    meta = fm.get("metadata")
    return meta if isinstance(meta, dict) else {}


def listed_text(value, absent: str) -> str:
    """A list-or-string frontmatter value as display text.

    `allowed-tools` and `surfaces` are valid as a comma string or a YAML list.
    """
    if isinstance(value, list):
        value = ", ".join(str(v).strip() for v in value if str(v).strip())
    return " ".join(str(value or "").split()) or absent


def legacy_top_level(fm) -> list[str]:
    """GF fields still sitting at the top level, in canonical order.

    Distinguishing "in the wrong place" from "absent" matters: the two have the
    same symptom under skill_metadata() but opposite fixes, and a bare "missing
    required field: owner" sends someone to add a second copy of a line already
    in the file.
    """
    if not isinstance(fm, dict):
        return []
    return [f for f in GF_METADATA_FIELDS if f in fm]


def classify_path(rel) -> tuple[str, tuple[str, ...]] | None:
    """Return (plugin_root, parts_below_that_root) for a repo-relative path.

    Returns None when the path is not under any plugin root. The point is that
    callers never index parts[0] against a root name: a root may be several
    path components deep (ROOT_GROUPS is "plugins/groups"), so the only safe
    test is an exact match on the whole root prefix.

        "plugins/groups/idm/software-tools"  -> ("plugins/groups", ("idm", "software-tools"))
        "plugins/foundation-wide"            -> ("plugins/foundation-wide", ())
        "plugins/shared-workflows/x/skills/y" -> ("plugins/shared-workflows", ("x", "skills", "y"))
        "scripts/deploy.py"                  -> None

    Accepts a str, Path, or PurePosixPath. Windows backslashes are normalised
    to "/" before matching, so a caller on Windows and a caller in CI (POSIX)
    classify the same string identically.

    Roots are matched longest-first, so if a future root were ever a prefix of
    another (none is today), the more specific root would still win rather
    than swallowing the longer one's paths by accident.
    """
    posix = str(rel).replace("\\", "/")
    parts = tuple(p for p in PurePosixPath(posix).parts if p not in ("", "."))

    for root_name in sorted(PLUGIN_ROOTS, key=len, reverse=True):
        root_parts = tuple(root_name.split("/"))
        if parts[: len(root_parts)] == root_parts:
            return root_name, parts[len(root_parts):]
    return None


# ---------------------------------------------------------------------------
# 0. Plugin registry
# ---------------------------------------------------------------------------

def load_plugins(marketplace_file: Path | None = None) -> dict[str, Path]:
    """Build the {plugin-name: path} map from .claude-plugin/marketplace.json.

    marketplace.json is the single source of truth for which plugins exist.
    The key is the entry's declared `name` ("idm-software-tools"), not the
    directory basename: task directories collide across teams
    ("plugins/groups/idm/data" and "plugins/groups/finance/data" are both
    "data"), and the declared name is what admins already see in the
    Claude.ai console, so keying on it gives contributors and admins one
    vocabulary.

    Only plugins registered in the marketplace are targetable; an unregistered
    plugin directory is built but not published, which is a deliberate state.
    Registration IS the act of publishing, which is why it can lag merging: a
    plugin can merge and wait unregistered while a rollout is staged.
    validate_skill.py reports that as a warning, never an error.

    `marketplace_file` is injectable for tests; callers pass nothing.
    """
    path_ = marketplace_file or MARKETPLACE_FILE
    data = json.loads(path_.read_text(encoding="utf-8"))
    plugins: dict[str, Path] = {}
    for entry in data.get("plugins", []):
        name, source = entry.get("name"), entry.get("source")
        if not name or not source:
            continue
        plugins[name] = REPO_ROOT / Path(source)
    return plugins


# ---------------------------------------------------------------------------
# 1. Sensitive field registry
#    Add any field name your skills handle that could carry sensitive data.
# ---------------------------------------------------------------------------

SENSITIVE_FIELDS = {
    # Identity
    "ssn", "social_security_number", "sin", "national_id",
    "dob", "date_of_birth", "birthdate",
    "passport_number", "drivers_license",

    # Employment
    "employer_id", "employee_id", "payroll_id", "tax_id", "ein",

    # Credentials
    "password", "passwd", "secret", "api_key", "api_secret",
    "token", "access_token", "refresh_token", "private_key",

    # Financial
    "credit_card", "card_number", "cvv", "bank_account", "routing_number",
    "iban", "swift",

    # Contact
    "email", "phone", "mobile", "address", "zip", "postal_code",
}

# Patterns for detecting sensitive values even when field names aren't flagged
SENSITIVE_PATTERNS = [
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "SSN"),                          # SSN
    (re.compile(r"\b\d{4}[- ]?\d{4}[- ]?\d{4}[- ]?\d{4}\b"), "CARD"),      # Credit card
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"), "EMAIL"),  # Email
    (re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"), "API_KEY"),                    # API key
    (re.compile(r"\b\d{4}-\d{2}-\d{2}\b"), "DATE"),                         # ISO date
]


# ---------------------------------------------------------------------------
# 2. SensitiveDataMasker
# ---------------------------------------------------------------------------

class SensitiveDataMasker:
    """
    Masks sensitive values in dicts/strings before they reach logs or reports.

    Usage:
        masker = SensitiveDataMasker()
        safe = masker.mask(record)          # returns masked copy
        print(masker.mask_string(raw_str))  # masks a plain string
    """

    def __init__(self, mask_char: str = "X", show_length: bool = True):
        self.mask_char = mask_char
        self.show_length = show_length

    def _mask_value(self, value: str) -> str:
        if not isinstance(value, str) or not value:
            return value
        length = len(value)
        if self.show_length:
            return f"[MASKED:{length}]"
        return self.mask_char * min(length, 8)

    def mask(self, data: Any, _depth: int = 0) -> Any:
        """Recursively mask sensitive fields in a dict or list."""
        if _depth > 20:
            return data  # guard against infinite recursion

        if isinstance(data, dict):
            return {
                k: self._mask_value(v) if k.lower() in SENSITIVE_FIELDS
                else self.mask(v, _depth + 1)
                for k, v in data.items()
            }
        if isinstance(data, list):
            return [self.mask(item, _depth + 1) for item in data]
        return data

    def mask_string(self, text: str) -> str:
        """Mask sensitive patterns found in a plain string."""
        for pattern, label in SENSITIVE_PATTERNS:
            text = pattern.sub(f"[{label}:MASKED]", text)
        return text

    def safe_repr(self, data: Any) -> str:
        """Return a JSON string safe for logging."""
        return json.dumps(self.mask(data), indent=2, default=str)


# ---------------------------------------------------------------------------
# 3. EnvSecrets
# ---------------------------------------------------------------------------

class EnvSecrets:
    """
    Reads secrets from environment variables only — never from files.
    Raises a clear error if a required secret is missing.

    Usage:
        secrets = EnvSecrets()
        key = secrets.require("TEST_API_KEY")
        key = secrets.get("OPTIONAL_KEY", default="fallback")
    """

    def require(self, name: str) -> str:
        value = os.environ.get(name)
        if not value:
            raise EnvironmentError(
                f"Required secret '{name}' is not set in environment.\n"
                f"See .env.example for setup instructions."
            )
        return value

    def get(self, name: str, default: str = "") -> str:
        return os.environ.get(name, default)

    def anthropic_key(self) -> str:
        return self.require("ANTHROPIC_API_KEY")
