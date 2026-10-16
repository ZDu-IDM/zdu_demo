"""
scripts/utils.py — shared constants and helpers for the catalog scripts.
"""

import json
import os
import re
from pathlib import Path, PurePosixPath
from typing import Any

REPO_ROOT = Path(__file__).parent.parent
MARKETPLACE_FILE = REPO_ROOT / ".claude-plugin" / "marketplace.json"

# The plugin roots, relative to REPO_ROOT. Import these; never restate them.
ROOT_FOUNDATION = "plugins/foundation-wide"
ROOT_GROUPS = "plugins/groups"
ROOT_SHARED = "plugins/shared-workflows"

PLUGIN_ROOTS: tuple[str, ...] = (ROOT_FOUNDATION, ROOT_GROUPS, ROOT_SHARED)

# GF's own SKILL.md fields, all optional. They go under `metadata:` because
# strict validators reject unknown top-level keys on upload.
GF_METADATA_FIELDS: tuple[str, ...] = ("surfaces",)

# Fields that moved to plugin.json and now fail if left in SKILL.md, mapped to
# the fix. Checked at the top level and under `metadata:`.
RETIRED_SKILL_FIELDS: dict[str, str] = {
    "version": "bump `version` in the plugin's .claude-plugin/plugin.json instead",
    "owner": ("name the maintainer in the plugin's .claude-plugin/plugin.json "
              '`author` instead, if at all — e.g. "author": {"name": "...", '
              '"email": "..."}'),
}

SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def parse_version(raw):
    """Return (major, minor, patch), or None if `raw` is not valid semver.

    Numbers are rejected: `1.10` as a number is already `1.1`.
    """
    if raw is None or isinstance(raw, (int, float, bool)):
        return None
    m = SEMVER.match(str(raw).strip())
    return tuple(int(g) for g in m.groups()) if m else None

# Test-harness directories inside a skill: never installed or packaged. Callers
# match them at different depths on purpose — don't unify how they check.
HARNESS_DIRS: tuple[str, ...] = ("evals",)


def skill_metadata(fm) -> dict:
    """Return a SKILL.md's `metadata:` map, or {} when it is absent or not a map."""
    if not isinstance(fm, dict):
        return {}
    meta = fm.get("metadata")
    return meta if isinstance(meta, dict) else {}


def listed_text(value, absent: str) -> str:
    """A comma-string or YAML-list frontmatter value as display text."""
    if isinstance(value, list):
        value = ", ".join(str(v).strip() for v in value if str(v).strip())
    return " ".join(str(value or "").split()) or absent


def author_text(author, absent: str = "not declared") -> str:
    """plugin.json's optional `author` as "Name <email>", "Name", or `absent`."""
    if not isinstance(author, dict):
        return absent
    name = str(author.get("name") or "").strip()
    email = str(author.get("email") or "").strip()
    if name and email:
        return f"{name} <{email}>"
    return name or email or absent


def find_plugin_manifest(path) -> Path | None:
    """The nearest .claude-plugin/plugin.json at or above `path`, or None."""
    for d in [Path(path), *Path(path).parents]:
        candidate = d / ".claude-plugin" / "plugin.json"
        if candidate.is_file():
            return candidate
    return None


def legacy_top_level(fm) -> list[str]:
    """GF fields written at the top level instead of under `metadata:`."""
    if not isinstance(fm, dict):
        return []
    return [f for f in GF_METADATA_FIELDS if f in fm]


def classify_path(rel) -> tuple[str, tuple[str, ...]] | None:
    """Return (plugin_root, parts_below_that_root), or None outside every root.

        "plugins/groups/idm/software-tools"  -> ("plugins/groups", ("idm", "software-tools"))
        "scripts/deploy.py"                  -> None

    Accepts str or Path; backslashes are normalised, so Windows and CI agree.
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
    """Build {declared plugin name: path} for every registered plugin.

    Keyed by declared name, since directory names repeat across teams.
    Unregistered plugins are left out. `marketplace_file` is for tests.
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
                f"Set it in your shell (bash: export {name}=..., PowerShell: "
                f"$env:{name} = \"...\"); .env.example lists the variables."
            )
        return value

    def get(self, name: str, default: str = "") -> str:
        return os.environ.get(name, default)

    def anthropic_key(self) -> str:
        return self.require("ANTHROPIC_API_KEY")
