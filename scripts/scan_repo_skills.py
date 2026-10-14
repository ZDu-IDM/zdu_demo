"""Scan a repo for Claude skills and emit a single, self-contained HTML page.

A "skill" is any directory containing a ``SKILL.md`` file. This script finds every
``SKILL.md`` in a repo — either a **GitHub repo** (via the GitHub API, no clone) or
an **already-cloned local directory** (walked on disk) — reads each skill's
``SKILL.md`` frontmatter, classifies it as a **plugin** or **standalone** skill (via
``.claude-plugin/plugin.json`` and ``marketplace.json``), records copy-pasteable
**installation steps**, and writes one static ``repo_skills.html`` that lets you:

    * search / filter the skills live (client-side),
    * switch between a Cards grid and a sortable Table (with a click-to-open
      detail modal), toggle a day / night theme,
    * expand each skill's **Installation Steps** (with a Copy button), and
    * export the listing as JSON or CSV.

Only ``SKILL.md`` and the plugin/marketplace manifests are read; no supporting-file
bytes are embedded, and ``license`` / ``allowed-tools`` / ``argument-hint``
frontmatter is deliberately omitted from the page.

The scanning core (repo-input parsing, GitHub access, SKILL.md frontmatter parsing,
plugin detection, install-step synthesis) is ported from the companion tools at
``C:\\work\\AI\\zdu_skills\\scripts`` (scan_repo.py) and
``C:\\work\\claude_demo\\repo-skills`` (app.py). The difference is the *output*:
instead of writing skills.json or serving a site, this produces a single HTML file
you can email, commit, or open by double-clicking.

Usage:
    python scan_repo_skills.py --repo anthropics/skills
    python scan_repo_skills.py --repo https://github.com/owner/repo --output skills.html
    python scan_repo_skills.py --repo C:\\work\\AI\\starsim_ai -o skills.html  # local clone

Set GITHUB_TOKEN to raise the API rate limit (60 -> 5000 req/hr) and scan private repos.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse

import requests

try:
    import yaml  # PyYAML — parses SKILL.md frontmatter, including lists/dicts.
except ImportError:  # pragma: no cover - script still runs; list values degrade.
    yaml = None

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SKILL_FILE = "SKILL.md"
GITHUB_API = "https://api.github.com"
GITHUB_RAW = "https://raw.githubusercontent.com"
API_TIMEOUT = 15
RAW_TIMEOUT = 20

# Frontmatter keys that are intentionally *not* carried into the page: the
# title/description are promoted to their own fields, and license / allowed-tools
# / argument-hint are deliberately omitted.
_METADATA_EXCLUDE = {
    "name", "description", "license",
    "allowed-tools", "allowed_tools", "argument-hint", "argument_hint",
}


# ---------------------------------------------------------------------------
# SKILL.md frontmatter parsing
# ---------------------------------------------------------------------------

_FRONTMATTER_RE = re.compile(r"\A---\s*\n(?P<body>.*?)\n---\s*(?:\n|$)", re.DOTALL)
_FRONTMATTER_KV_RE = re.compile(
    r"^(?P<key>[A-Za-z_][\w-]*)\s*:\s*(?P<value>.*?)\s*$", re.MULTILINE
)


def _strip_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def _parse_frontmatter_block(block: str) -> dict:
    """Parse a YAML frontmatter block into a dict (PyYAML, regex fallback)."""
    if yaml is not None:
        try:
            data = yaml.safe_load(block)
        except yaml.YAMLError:
            data = None
        if isinstance(data, dict):
            return data

    out: dict = {}
    for kv in _FRONTMATTER_KV_RE.finditer(block):
        out[kv.group("key")] = _strip_quotes(kv.group("value"))
    return out


def _as_text(value) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _jsonsafe(value):
    """Coerce a parsed-YAML value into a JSON-serializable one."""
    if isinstance(value, dict):
        return {str(k): _jsonsafe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonsafe(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def parse_skill_md(text: str) -> tuple[str | None, str, dict]:
    """Return ``(name, description, metadata)`` for SKILL.md contents."""
    name: str | None = None
    description = ""
    metadata: dict = {}
    body = text

    fm_match = _FRONTMATTER_RE.match(text)
    if fm_match:
        fm_block = fm_match.group("body")
        body = text[fm_match.end():]
        data = _parse_frontmatter_block(fm_block)
        lowered = {str(k).lower(): v for k, v in data.items()}
        name = _as_text(lowered.get("name")) or None
        description = _as_text(lowered.get("description"))
        metadata = {
            k: v
            for k, v in data.items()
            if str(k).lower() not in _METADATA_EXCLUDE
        }

    if not description:
        for line in body.splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                description = stripped
                break

    return name, description, metadata


# ---------------------------------------------------------------------------
# Repo input parsing
# ---------------------------------------------------------------------------

class ScanError(Exception):
    """User-facing error."""


def parse_repo_input(text: str) -> tuple[str, str, str | None]:
    """Parse a repo reference into ``(owner, repo, ref_or_None)``."""
    text = (text or "").strip()
    if not text:
        raise ScanError("Please provide a GitHub repository URL or owner/repo.")

    if "github.com" in text:
        if not re.match(r"^https?://", text):
            text = "https://" + text
        path = urlparse(text).path
    else:
        path = text

    parts = [p for p in path.strip("/").split("/") if p]
    if len(parts) < 2:
        raise ScanError(
            "Could not read 'owner/repo' from that input. "
            "Try e.g. anthropics/skills or https://github.com/anthropics/skills."
        )

    owner, repo = parts[0], parts[1]
    repo = repo[:-4] if repo.endswith(".git") else repo

    ref: str | None = None
    if len(parts) >= 4 and parts[2] == "tree":
        ref = "/".join(parts[3:])

    return owner, repo, ref


# ---------------------------------------------------------------------------
# GitHub access
# ---------------------------------------------------------------------------

def _auth_headers() -> dict[str, str]:
    headers = {"Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _github_get_json(url: str) -> dict:
    try:
        resp = requests.get(url, headers=_auth_headers(), timeout=API_TIMEOUT)
    except requests.RequestException as exc:
        raise ScanError(f"Network error contacting GitHub: {exc}")

    if resp.status_code == 404:
        raise ScanError("Repository or branch not found.")
    if resp.status_code in (403, 429) and resp.headers.get("X-RateLimit-Remaining") == "0":
        raise ScanError(
            "GitHub API rate limit exceeded. Set a GITHUB_TOKEN environment "
            "variable to raise the limit, then try again."
        )
    if not resp.ok:
        raise ScanError(f"GitHub API error ({resp.status_code}): {resp.text[:200]}")
    return resp.json()


def resolve_ref(owner: str, repo: str, ref: str | None) -> str:
    if ref:
        return ref
    data = _github_get_json(f"{GITHUB_API}/repos/{owner}/{repo}")
    branch = data.get("default_branch")
    if not branch:
        raise ScanError("Could not determine the repository's default branch.")
    return branch


def list_tree(owner: str, repo: str, ref: str) -> tuple[list[str], bool]:
    """Return ``(all_blob_paths, truncated)`` by listing the repo tree once."""
    url = f"{GITHUB_API}/repos/{owner}/{repo}/git/trees/{ref}?recursive=1"
    data = _github_get_json(url)
    truncated = bool(data.get("truncated"))
    blobs = [
        item["path"]
        for item in data.get("tree", [])
        if item.get("type") == "blob" and item.get("path")
    ]
    return blobs, truncated


def fetch_raw_bytes(owner: str, repo: str, ref: str, path: str) -> bytes | None:
    """Fetch one file's raw bytes from raw.githubusercontent.com (no rate limit)."""
    url = f"{GITHUB_RAW}/{owner}/{repo}/{ref}/{path}"
    try:
        resp = requests.get(url, headers=_auth_headers(), timeout=RAW_TIMEOUT)
        if resp.ok:
            return resp.content
    except requests.RequestException:
        pass
    return None


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def _dirname(path: str) -> str:
    return path.rsplit("/", 1)[0] if "/" in path else ""


def _skill_name_from_path(path: str) -> str:
    parent = _dirname(path)
    return parent.rsplit("/", 1)[-1] if parent else SKILL_FILE


# ---------------------------------------------------------------------------
# Local clone access  (filesystem mirror of the GitHub access layer above)
# ---------------------------------------------------------------------------

def is_local_repo(repo_input: str) -> bool:
    """True if ``repo_input`` points at an existing directory on disk.

    A GitHub reference like ``owner/repo`` is never a real directory, so an
    ``isdir`` check cleanly distinguishes the two input styles.
    """
    return bool(repo_input) and os.path.isdir(os.path.expanduser(repo_input))


def git_remote_slug(root: str) -> str | None:
    """Return the ``owner/repo`` slug of a local clone's ``origin`` remote, if any.

    Lets a locally-scanned clone still emit GitHub-style install steps
    (``git clone https://github.com/owner/repo.git``) instead of a machine-local
    path. Returns ``None`` when there's no git, no remote, or no GitHub origin.
    """
    try:
        url = subprocess.check_output(
            ["git", "-C", str(root), "config", "--get", "remote.origin.url"],
            text=True, stderr=subprocess.DEVNULL,
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return None
    match = re.search(r"github\.com[:/](?P<slug>[^/]+/[^/]+?)(?:\.git)?/?$", url)
    return match.group("slug") if match else None


def list_local_tree(root: str) -> list[str]:
    """Return every file under ``root`` as a repo-relative, forward-slash path."""
    blobs: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        # Prune VCS/noise dirs in place so os.walk doesn't descend into them.
        dirnames[:] = [d for d in dirnames if d not in (".git", "__pycache__")]
        for filename in filenames:
            full = os.path.join(dirpath, filename)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            blobs.append(rel)
    return blobs


# ---------------------------------------------------------------------------
# Plugin / marketplace detection + install steps
# ---------------------------------------------------------------------------

PLUGIN_MANIFEST = ".claude-plugin/plugin.json"
MARKETPLACE_MANIFEST = ".claude-plugin/marketplace.json"


def _plugin_roots_and_names(blobs: list[str], read_text) -> dict[str, str]:
    """Map each plugin *root* dir to its plugin name.

    A plugin root is the grandparent of a ``.claude-plugin/plugin.json`` file
    (manifest lives at ``<root>/.claude-plugin/plugin.json``). The name comes from
    the manifest's ``name`` field, falling back to the root dir's basename.
    """
    roots = {
        str(PurePosixPath(p).parent.parent)
        for p in blobs
        if p.endswith(PLUGIN_MANIFEST)
    }
    names: dict[str, str] = {}
    for root in roots:
        manifest = PLUGIN_MANIFEST if root == "." else f"{root}/{PLUGIN_MANIFEST}"
        name = None
        text = read_text(manifest)
        if text:
            try:
                name = json.loads(text).get("name")
            except json.JSONDecodeError:
                name = None
        names[root] = name or PurePosixPath(root).name
    return names


def _marketplace_name(blobs: list[str], read_text) -> str | None:
    """Return marketplace.json's top-level ``name`` (the install handle), or None.

    ``/plugin install <plugin>@<handle>`` resolves ``<handle>`` against the
    marketplace's own name, which is this top-level field.
    """
    if MARKETPLACE_MANIFEST not in set(blobs):
        return None
    text = read_text(MARKETPLACE_MANIFEST)
    if not text:
        return None
    try:
        return json.loads(text).get("name")
    except json.JSONDecodeError:
        return None


def _strip_dotslash(path) -> str:
    """Normalize a manifest path: drop leading ``./`` segments and surrounding slashes.

    Defensive against non-string inputs (a plugin ``source`` can be a structured
    object rather than a path string); anything that isn't a str yields ``""``.
    """
    if not isinstance(path, str):
        return ""
    p = path.strip().replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    return p.strip("/")


def _marketplace_plugin_skill_map(blobs: list[str], read_text) -> dict[str, str]:
    """Map each skill *directory* to its plugin name via marketplace.json's ``plugins``.

    Some repos (notably anthropics/skills) declare plugins *inline* in
    ``marketplace.json`` — each plugin lists its member skills by path in a
    ``skills`` array (e.g. ``"./skills/xlsx"``) — instead of dropping a
    ``plugin.json`` inside each plugin root. Those skills have no ``plugin.json``
    ancestor, so :func:`_find_owning_plugin` can't see them. This resolves the
    by-reference style: ``{<skill_dir>: <plugin name>}``, where ``<skill_dir>`` is
    the plugin ``source`` joined with each ``skills`` entry (both ``./``-relative).
    """
    if MARKETPLACE_MANIFEST not in set(blobs):
        return {}
    text = read_text(MARKETPLACE_MANIFEST)
    if not text:
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {}

    mapping: dict[str, str] = {}
    for plugin in data.get("plugins", []) or []:
        if not isinstance(plugin, dict):
            continue
        name = plugin.get("name")
        if not name:
            continue
        # A plugin's ``source`` may be a structured object (e.g. an external
        # ``{"source": "git", "url": ...}``) instead of a local relative path.
        # Those skills live in another repo, so there's nothing to map locally.
        raw_source = plugin.get("source")
        if raw_source is not None and not isinstance(raw_source, str):
            continue
        source = _strip_dotslash(raw_source or "")
        for skill_ref in plugin.get("skills", []) or []:
            ref = _strip_dotslash(skill_ref if isinstance(skill_ref, str) else "")
            skill_dir = "/".join(p for p in (source, ref) if p)
            if skill_dir:
                mapping[skill_dir] = name
    return mapping


def _find_owning_plugin(skill_dir: str, plugin_roots) -> str | None:
    """Return the nearest ancestor (or self) that is a plugin root, else None."""
    current = PurePosixPath(skill_dir)
    while True:
        text = str(current)
        if text in plugin_roots:
            return text
        if text in (".", ""):
            return None
        current = current.parent


def _install_steps(
    skill_name: str,
    skill_dir: str,
    plugin_name: str | None,
    *,
    market_source: str,
    market_name: str | None,
    clone_url: str,
    local_root: str | None,
) -> list[str]:
    """Return the copy-pasteable install steps for one skill.

    Plugin skills install via the ``/plugin`` marketplace flow; standalone skills
    are copied into ``~/.claude/skills`` (from a clone, or directly from disk when
    the repo was scanned locally).
    """
    if plugin_name is not None:
        return [
            f"/plugin marketplace add {market_source}",
            f"/plugin install {plugin_name}@{market_name or plugin_name}",
            f"# ships with plugin '{plugin_name}'; invoke as /{plugin_name}:{skill_name}",
        ]

    src_dir = skill_dir or "."
    if local_root:
        # Files are already on disk; copy straight from the local repo root.
        on_disk = (Path(local_root) / src_dir).as_posix()
        return [
            f"mkdir -p ~/.claude/skills/{skill_name}                 # personal scope (all projects)",
            f'cp -r "{on_disk}" ~/.claude/skills/{skill_name}',
            f'# OR project scope: cp -r "{on_disk}" <project>/.claude/skills/{skill_name}',
            f"# then invoke as /{skill_name}",
        ]
    return [
        f"git clone {clone_url} /tmp/repo",
        f"mkdir -p ~/.claude/skills/{skill_name}                 # personal scope (all projects)",
        f"cp -r /tmp/repo/{src_dir} ~/.claude/skills/{skill_name}",
        f"# OR project scope: cp -r /tmp/repo/{src_dir} <project>/.claude/skills/{skill_name}",
        f"# then invoke as /{skill_name}",
    ]


# ---------------------------------------------------------------------------
# Scan: build the skill records from each SKILL.md's frontmatter
# ---------------------------------------------------------------------------

def _build_skills(
    blobs: list[str],
    read_text,           # (rel_path) -> str | None  (decoded file contents)
    make_url,            # (rel_path) -> str
    install_ctx: dict,   # market_source / clone_url / local_root for install steps
) -> list[dict]:
    """Turn a flat file list into skill records from each ``SKILL.md``.

    Backend-agnostic: ``read_text`` and ``make_url`` abstract over "fetch from
    GitHub" vs. "read from disk", so the GitHub and local paths share this loop.
    Only ``SKILL.md`` (and the plugin/marketplace manifests) are read — no
    supporting files are fetched. Each skill is classified as ``plugin`` or
    ``standalone`` and stamped with copy-pasteable installation steps.
    """
    skill_md_paths = sorted(p for p in blobs if p.rsplit("/", 1)[-1] == SKILL_FILE)
    plugin_names = _plugin_roots_and_names(blobs, read_text)
    plugin_roots = set(plugin_names)
    market_name = _marketplace_name(blobs, read_text)
    market_skill_map = _marketplace_plugin_skill_map(blobs, read_text)

    skills = []
    for md_path in skill_md_paths:
        skill_dir = _dirname(md_path)
        skill_md_text = read_text(md_path) or ""
        fm_name, description, metadata = parse_skill_md(skill_md_text)
        name = fm_name or _skill_name_from_path(md_path)

        # Prefer a plugin.json-based owning root; fall back to marketplace.json's
        # by-reference skill list for repos that declare plugins inline.
        owning_root = _find_owning_plugin(skill_dir, plugin_roots)
        plugin_name = plugin_names.get(owning_root) if owning_root else None
        if plugin_name is None:
            plugin_name = market_skill_map.get(skill_dir)
        installation = _install_steps(
            name, skill_dir, plugin_name,
            market_source=install_ctx["market_source"],
            market_name=market_name,
            clone_url=install_ctx["clone_url"],
            local_root=install_ctx.get("local_root"),
        )

        skills.append({
            "name": name,
            "description": description,
            "path": md_path,
            "url": make_url(md_path),
            "type": "plugin" if plugin_name else "standalone",
            "plugin": plugin_name,        # None for standalone skills
            "installation": installation,
            "metadata": _jsonsafe(metadata),
        })

    skills.sort(key=lambda s: s["name"].lower())
    return skills


def scan_repo(repo_input: str) -> dict:
    """Scan a GitHub repo *or* a local clone, depending on the input.

    If ``repo_input`` is an existing directory it is walked on disk; otherwise it
    is treated as a GitHub reference (``owner/repo`` or a URL).
    """
    if is_local_repo(repo_input):
        return scan_local_repo(repo_input)
    return scan_github_repo(repo_input)


def scan_github_repo(repo_input: str) -> dict:
    """Detect every skill via the GitHub API, reading each SKILL.md's frontmatter."""
    owner, repo, requested_ref = parse_repo_input(repo_input)
    ref = resolve_ref(owner, repo, requested_ref)
    blobs, truncated = list_tree(owner, repo, ref)

    def read_text(path: str) -> str | None:
        content = fetch_raw_bytes(owner, repo, ref, path)
        return content.decode("utf-8", errors="replace") if content is not None else None

    def make_url(path: str) -> str:
        return f"https://github.com/{owner}/{repo}/blob/{ref}/{path}"

    install_ctx = {
        "market_source": f"{owner}/{repo}",
        "clone_url": f"https://github.com/{owner}/{repo}.git",
        "local_root": None,
    }
    skills = _build_skills(blobs, read_text, make_url, install_ctx)
    return {
        "owner": owner,
        "repo": repo,
        "ref": ref,
        "local": False,
        "source": f"{owner}/{repo}",
        "truncated": truncated,
        "count": len(skills),
        "skills": skills,
    }


def scan_local_repo(repo_input: str) -> dict:
    """Detect every skill by walking a local clone, reading each SKILL.md's frontmatter."""
    root = os.path.abspath(os.path.expanduser(repo_input))
    if not os.path.isdir(root):
        raise ScanError(f"Local path is not a directory: {root}")

    repo = os.path.basename(root.rstrip(os.sep)) or root
    blobs = list_local_tree(root)

    def read_text(path: str) -> str | None:
        try:
            with open(os.path.join(root, path), "rb") as fh:
                return fh.read().decode("utf-8", errors="replace")
        except OSError:
            return None

    def make_url(path: str) -> str:
        # A file:// URI so the link opens the actual file from the browser.
        return (Path(root) / path).as_uri()

    # Prefer GitHub-style install steps when the clone has a github.com origin;
    # otherwise fall back to copying straight from the local path on disk.
    slug = git_remote_slug(root)
    install_ctx = {
        "market_source": slug or root,
        "clone_url": f"https://github.com/{slug}.git" if slug else root,
        "local_root": root,
    }
    skills = _build_skills(blobs, read_text, make_url, install_ctx)
    return {
        "owner": "",
        "repo": repo,
        "ref": "local clone",
        "local": True,
        "source": root,
        "truncated": False,
        "count": len(skills),
        "skills": skills,
    }


# ---------------------------------------------------------------------------
# HTML generation
# ---------------------------------------------------------------------------

def render_html(data: dict) -> str:
    """Render the single self-contained HTML page from a scan result dict."""
    # Embed the dataset as JSON in a <script type="application/json"> block.
    # Escaping "</" -> "<\\/" prevents a stray "</script>" inside string data
    # from prematurely closing the script element.
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    title = html.escape(f"Skills in {data.get('source') or data['repo']}")
    return _HTML_TEMPLATE.replace("__TITLE__", title).replace("__DATA__", payload)


# The page is intentionally one file with no external dependencies: all styles are
# inline, so it works offline with no CDN. Each skill card shows its type (plugin
# vs. standalone) and an Expand/Collapse "Installation Steps" section driven by the
# ``type``/``plugin``/``installation`` fields in the scan result.
_HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
  /* Dark theme is the default; the light palette below overrides these when
     <html data-theme="light"> is set. Every color routes through a variable so
     switching themes is just swapping this block of values. */
  :root {
    --bg:#0d1117; --card:#161b22; --border:#30363d; --fg:#e6edf3; --muted:#8b949e;
    --accent:#2f81f7; --accent2:#238636;
    --tag-bg:#21262d; --code-bg:#0d1117; --code-fg:#e6edf3;
    --warn:#d29922; --shadow:rgba(0,0,0,.45);
    /* Roles split out from --accent so dark mode can be brighter/more varied:
       links (repo name + SKILL.md), the card hover edge, and the repo badge. */
    --link:#79c0ff; --card-glow:#7d8cff;
    --badge-bg:#2ea88418; --badge-border:#5ed3b3; --badge-fg:#86e6cb;
  }
  [data-theme="light"] {
    --bg:#f4f5f7; --card:#ffffff; --border:#dfe2e8; --fg:#20242e; --muted:#69707c;
    --accent:#5257e8; --accent2:#1f8a45;
    --tag-bg:#eef0f3; --code-bg:#1c2333; --code-fg:#e6edf3;
    --warn:#9a6700; --shadow:rgba(60,66,87,.12);
    /* Light mode keeps the original indigo look for these roles. */
    --link:#5257e8; --card-glow:#5257e8;
    --badge-bg:#5257e814; --badge-border:#5257e8; --badge-fg:#5257e8;
  }
  * { box-sizing: border-box; }
  body { margin:0; font-family: -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; background:var(--bg); color:var(--fg); transition:background .2s ease, color .2s ease; }
  header { padding:24px 20px 12px; border-bottom:1px solid var(--border); position:sticky; top:0; background:var(--bg); z-index:5; }
  h1 { margin:0 0 4px; font-size:20px; }
  h1 a { color:var(--accent); text-decoration:none; }
  .meta { color:var(--muted); font-size:13px; }
  .warn { color:var(--warn); font-size:13px; margin-top:6px; }
  .toolbar { display:flex; flex-wrap:wrap; gap:8px; align-items:center; margin-top:14px; }
  input[type=search] { flex:1 1 280px; min-width:200px; padding:9px 12px; border-radius:8px; border:1px solid var(--border); background:var(--card); color:var(--fg); font-size:14px; }
  button { padding:8px 13px; border-radius:8px; border:1px solid var(--border); background:var(--card); color:var(--fg); font-size:13px; cursor:pointer; }
  button:hover { border-color:var(--accent); }
  button.primary { background:var(--accent2); border-color:var(--accent2); color:#fff; }
  button:disabled { opacity:0.4; cursor:default; border-color:var(--border); }
  select { padding:8px 10px; border-radius:8px; border:1px solid var(--border); background:var(--card); color:var(--fg); font-size:13px; cursor:pointer; }
  label.ctrl { color:var(--muted); font-size:12px; display:flex; align-items:center; gap:5px; }
  .count { color:var(--muted); font-size:13px; margin-left:auto; }
  .pager { display:flex; flex-wrap:wrap; gap:10px; align-items:center; justify-content:center; margin-top:18px; color:var(--muted); font-size:13px; }
  main { padding:18px 20px 60px; }
  .grid { display:grid; grid-template-columns:repeat(auto-fill, minmax(330px, 1fr)); gap:14px; }
  .card { background:var(--card); border:1px solid var(--border); border-radius:10px; padding:14px; display:flex; flex-direction:column; gap:8px; transition:border-color .15s ease, box-shadow .15s ease, transform .15s ease; }
  .card:hover { border-color:var(--card-glow); box-shadow:0 0 0 1px var(--card-glow), 0 8px 22px var(--shadow); transform:translateY(-2px); }
  .card-head { display:flex; align-items:center; gap:8px; flex-wrap:wrap; }
  .card h2 { margin:0; font-size:16px; }
  .card h2 a { color:var(--fg); text-decoration:none; transition:color .12s ease; }
  .card h2 a:hover, .card h2 a:focus-visible { color:var(--link); text-decoration:underline; outline:none; }
  .card .desc { color:var(--fg); font-size:13px; line-height:1.45; }
  .desc-toggle { background:none; border:none; padding:0 0 0 4px; margin:0; color:var(--link); cursor:pointer; font-size:13px; }
  .desc-toggle:hover { text-decoration:underline; border-color:transparent; }
  .card .path { color:var(--muted); font-size:12px; word-break:break-all; }
  .card .path a { color:var(--link); text-decoration:none; }
  .tags { display:flex; flex-wrap:wrap; gap:5px; }
  .tag { font-size:11px; background:var(--tag-bg); border:1px solid var(--border); border-radius:999px; padding:2px 8px; color:var(--muted); }
  .repo-badge { font-size:11px; background:var(--badge-bg); border:1px solid var(--badge-border); border-radius:999px; padding:2px 8px; color:var(--badge-fg); align-self:flex-start; }
  details { font-size:12px; color:var(--muted); }
  details summary { cursor:pointer; }
  details ul { margin:6px 0 0; padding-left:18px; }
  .install-type { margin:8px 0 4px; font-size:12px; color:var(--muted); }
  .install-type b { color:var(--fg); font-weight:600; }
  pre.install-steps { margin:4px 0 0; padding:9px 11px; background:var(--code-bg); border:1px solid var(--border); border-radius:6px; overflow-x:auto; font-size:12px; line-height:1.5; color:var(--code-fg); white-space:pre; font-family: ui-monospace, "SFMono-Regular", Consolas, "Liberation Mono", monospace; }
  .type-badge { font-size:11px; border-radius:999px; padding:1px 8px; border:1px solid var(--border); white-space:nowrap; }
  .type-badge.plugin { background:#8957e522; border-color:#8957e5; color:#a371f7; }
  .type-badge.standalone { background:#23863622; border-color:var(--accent2); color:#3fb950; }
  .empty { color:var(--muted); padding:40px; text-align:center; }
  #themeToggle { display:inline-flex; align-items:center; gap:5px; }
  /* View toggle: a small segmented control (Table | Cards). */
  .viewtoggle { display:inline-flex; }
  .viewtoggle .viewbtn { border-radius:0; }
  .viewtoggle .viewbtn:first-child { border-radius:8px 0 0 8px; }
  .viewtoggle .viewbtn:last-child { border-radius:0 8px 8px 0; border-left:none; }
  .viewbtn.active { background:var(--accent); border-color:var(--accent); color:#fff; }
  .viewbtn.active:hover { border-color:var(--accent); }
  /* Table view */
  .table-wrap { overflow-x:auto; }
  table.skills-table { width:100%; border-collapse:collapse; font-size:13px; }
  .skills-table th { text-align:left; padding:10px 12px; color:var(--muted); font-size:12px; text-transform:uppercase; letter-spacing:.03em; border-bottom:1px solid var(--border); white-space:nowrap; }
  .skills-table th[data-sort] { cursor:pointer; user-select:none; }
  .skills-table th[data-sort]:hover { color:var(--fg); }
  .sort-ind { font-size:10px; margin-left:4px; }
  .skills-table td { padding:12px; border-bottom:1px solid var(--border); vertical-align:middle; }
  .skills-table tbody tr { cursor:pointer; transition:background .12s ease; }
  .skills-table tbody tr:hover { background:var(--tag-bg); }
  .skills-table td.name { font-weight:600; color:var(--fg); }
  .skills-table td.repo { color:var(--muted); }
  /* Modal (opened when a table row is clicked) */
  .modal-backdrop { position:fixed; inset:0; background:rgba(0,0,0,.5); display:none; align-items:flex-start; justify-content:center; z-index:50; padding:40px 16px; overflow-y:auto; }
  .modal-backdrop.open { display:flex; }
  body.modal-open { overflow:hidden; }
  .modal { background:var(--card); border:1px solid var(--border); border-radius:12px; max-width:640px; width:100%; padding:22px 24px; box-shadow:0 12px 40px var(--shadow); }
  .modal-head { display:flex; align-items:flex-start; justify-content:space-between; gap:12px; }
  .modal-head h2 { margin:0; font-size:22px; }
  .modal-head h2 a { color:var(--fg); text-decoration:none; }
  .modal-head h2 a:hover { color:var(--accent); text-decoration:underline; }
  .modal-close { background:none; border:none; color:var(--accent); font-size:24px; line-height:1; cursor:pointer; padding:0 2px; }
  .modal .badges { display:flex; align-items:center; gap:8px; flex-wrap:wrap; margin:12px 0; color:var(--muted); font-size:13px; }
  .modal .modal-desc { font-size:14px; line-height:1.55; color:var(--fg); white-space:pre-wrap; }
  .modal .path a { color:var(--link); text-decoration:none; }
  .modal .path a:hover { text-decoration:underline; }
  .modal .steps-head { display:flex; align-items:center; justify-content:space-between; margin:20px 0 8px; }
  .modal .steps-head h3 { margin:0; font-size:16px; }
  .copy-btn { background:none; border:none; color:var(--accent); cursor:pointer; font-size:14px; padding:0; }
  .copy-btn:hover { text-decoration:underline; }
  /* Export: one button that reveals JSON / CSV on hover or click. */
  .export-menu { position:relative; display:inline-flex; }
  .export-options { position:absolute; top:100%; left:0; min-width:100%; margin-top:2px; padding:4px; background:var(--card); border:1px solid var(--border); border-radius:8px; box-shadow:0 8px 22px var(--shadow); flex-direction:column; gap:2px; display:none; z-index:10; }
  .export-menu:hover .export-options, .export-menu.open .export-options { display:flex; }
  .export-options button { border:none; background:none; width:100%; text-align:left; border-radius:6px; padding:6px 12px; }
  .export-options button:hover { background:var(--tag-bg); border-color:transparent; }
</style>
<script>
  /* Apply the saved (or OS-preferred) theme before first paint to avoid a flash
     of the wrong theme. The toggle handler further down keeps it in sync. */
  (function () {
    try {
      var t = localStorage.getItem("skills-theme");
      if (t !== "light" && t !== "dark") {
        t = (window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches) ? "light" : "dark";
      }
      document.documentElement.setAttribute("data-theme", t);
    } catch (e) { /* localStorage blocked -> fall back to CSS default (dark) */ }
  })();
</script>
</head>
<body>
<header>
  <h1 id="title"></h1>
  <div class="meta" id="subtitle"></div>
  <div class="warn" id="truncated" style="display:none"></div>
  <div class="toolbar">
    <span class="viewtoggle">
      <button id="viewTable" class="viewbtn" type="button">☰ Table</button>
      <button id="viewCards" class="viewbtn" type="button">▦ Cards</button>
    </span>
    <input type="search" id="filter" placeholder="Filter by name, description, path, metadata, or file…" autofocus>
    <label class="ctrl" id="repoFilterWrap" style="display:none">Repo
      <select id="repoFilter"><option value="">All repos</option></select>
    </label>
    <label class="ctrl">Sort
      <select id="sort">
        <option value="name-asc">Name (A→Z)</option>
        <option value="name-desc">Name (Z→A)</option>
      </select>
    </label>
    <label class="ctrl">Per page
      <select id="perPage">
        <option value="6">6</option>
        <option value="12" selected>12</option>
        <option value="24">24</option>
        <option value="48">48</option>
        <option value="all">All</option>
      </select>
    </label>
    <span class="export-menu" id="exportMenu">
      <button id="exportBtn" type="button" aria-haspopup="true" aria-expanded="false">Export ▾</button>
      <div class="export-options" id="exportOptions" role="menu">
        <button id="exportJson" type="button" role="menuitem">JSON</button>
        <button id="exportCsv" type="button" role="menuitem">CSV</button>
      </div>
    </span>
    <button id="themeToggle" type="button" title="Toggle day / night mode" aria-label="Toggle day / night mode"></button>
    <span class="count" id="count"></span>
  </div>
</header>
<main>
  <div class="grid" id="grid"></div>
  <div class="table-wrap" id="tableWrap" style="display:none">
    <table class="skills-table">
      <thead><tr id="tableHead"></tr></thead>
      <tbody id="tableBody"></tbody>
    </table>
  </div>
  <div class="empty" id="empty" style="display:none">No skills match your filter.</div>
  <div class="pager" id="pager" style="display:none">
    <button id="prev">‹ Prev</button>
    <span id="pageInfo"></span>
    <button id="next">Next ›</button>
  </div>
</main>

<div class="modal-backdrop" id="modal" role="dialog" aria-modal="true" aria-hidden="true">
  <div class="modal" id="modalPanel"></div>
</div>

<script type="application/json" id="data">__DATA__</script>
<script>
"use strict";
const DATA = JSON.parse(document.getElementById("data").textContent);

function download(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = filename;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/* ---- export helpers ----------------------------------------------------- */
// Filename stem: "owner_repo" for GitHub, just "repo" for a local clone.
const EXPORT_STEM = DATA.aggregate ? "all_repos" : ((DATA.owner ? DATA.owner + "_" : "") + DATA.repo);
function exportJson() {
  download(new Blob([JSON.stringify(DATA, null, 2)], { type: "application/json" }),
           EXPORT_STEM + "_skills.json");
}
function csvCell(v) { return '"' + String(v).replace(/"/g, '""') + '"'; }
function exportCsv() {
  // In aggregate mode prepend a "repo" column so rows stay attributable.
  const head = (DATA.aggregate ? ["repo"] : []).concat(
    ["name", "description", "path", "url", "metadata"]);
  const rows = [head.join(",")];
  for (const s of DATA.skills) {
    const cells = DATA.aggregate ? [csvCell(s.repo || "")] : [];
    rows.push(cells.concat([
      csvCell(s.name), csvCell(s.description), csvCell(s.path), csvCell(s.url),
      csvCell(JSON.stringify(s.metadata || {})),
    ]).join(","));
  }
  // UTF-8 BOM so Excel reads non-ASCII correctly.
  download(new Blob(["﻿" + rows.join("\r\n")], { type: "text/csv;charset=utf-8" }),
           EXPORT_STEM + "_skills.csv");
}

/* ---- rendering ---------------------------------------------------------- */
const esc = s => String(s).replace(/[&<>"']/g, c =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

// Full "owner/repo" label: repos can come from different owners, so the short
// repo name alone can collide. Falls back to the short name for local scans.
const repoLabel = s => s.source || s.repo || "";

// Frontmatter keys we never surface as tags (kept out of the page on purpose).
const META_HIDDEN = new Set(["license", "allowed-tools", "allowed_tools", "argument-hint", "argument_hint"]);

// Flatten a skill to one lowercase string for substring filtering.
function haystack(s) {
  const parts = [s.name, s.description, s.path, s.repo || "", s.source || "",
                 s.type || "", s.plugin || "", JSON.stringify(s.metadata || {})];
  return parts.join("  ").toLowerCase();
}
DATA.skills.forEach(s => { s._hay = haystack(s); });

function metaTags(md) {
  if (!md || typeof md !== "object") return "";
  const tags = [];
  for (const [k, v] of Object.entries(md)) {
    if (META_HIDDEN.has(String(k).toLowerCase())) continue;
    const val = Array.isArray(v) ? v.join(", ") : (typeof v === "object" ? JSON.stringify(v) : v);
    tags.push('<span class="tag">' + esc(k) + ": " + esc(val) + "</span>");
  }
  return tags.length ? '<div class="tags">' + tags.join("") + "</div>" : "";
}

// Expand/Collapse section showing how to install the skill: a type line
// (plugin vs. standalone) followed by the copy-pasteable steps from the scan.
function installSteps(s) {
  const steps = Array.isArray(s.installation) ? s.installation : [];
  const typeLine = s.type === "plugin"
    ? "<b>Plugin skill</b>" + (s.plugin ? " — ships with plugin <b>" + esc(s.plugin) + "</b>" : "")
    : "<b>Standalone skill</b>";
  const body = steps.length
    ? '<pre class="install-steps">' + steps.map(esc).join("\n") + "</pre>"
    : '<p class="install-type">No installation steps available.</p>';
  return "<details><summary>Installation Steps</summary>" +
    '<div class="install-type">' + typeLine + "</div>" + body + "</details>";
}

// Small pill showing the skill's type at a glance, above the card body.
function typeBadge(s) {
  if (!s.type) return "";
  return '<span class="type-badge ' + esc(s.type) + '">' + esc(s.type) + "</span>";
}

// Descriptions longer than this are clamped, with a "…more" toggle to reveal the
// rest. The full text lives in data-full so search/CSV still see everything.
const DESC_LIMIT = 400;
function descHtml(s) {
  const full = s.description || "";
  if (!full) return "";
  if (full.length <= DESC_LIMIT) return '<div class="desc">' + esc(full) + "</div>";
  const short = full.slice(0, DESC_LIMIT).replace(/\s+$/, "");
  return '<div class="desc" data-full="' + esc(full) + '">' +
    '<span class="desc-text">' + esc(short) + "</span>" +
    '<button type="button" class="desc-toggle" data-expanded="0">…more</button></div>';
}

// Turn a SKILL.md file URL into its containing *folder* URL: drop the trailing
// filename, and (for GitHub) swap the file-view "/blob/" for the dir-view "/tree/".
function folderUrl(s) {
  if (!s.url) return "";
  return s.url.replace(/\/[^\/]*$/, "").replace("/blob/", "/tree/");
}

function card(s, i) {
  // The skill name links to its source folder on GitHub (the skill directory,
  // not the SKILL.md file). Local scans expose a file:// folder URL instead;
  // fall back to plain text when no URL is available.
  const dirUrl = folderUrl(s);
  const nameHtml = dirUrl
    ? '<a href="' + esc(dirUrl) + '" target="_blank" rel="noopener" title="Open ' + esc(s.name) + ' skill folder on GitHub">' + esc(s.name) + "</a>"
    : esc(s.name);
  return '<div class="card">' +
    '<div class="card-head"><h2>' + nameHtml + "</h2>" + typeBadge(s) + "</div>" +
    (s.repo ? '<span class="repo-badge" data-source="' + esc(repoLabel(s)) + '">' + esc(repoLabel(s)) + "</span>" : "") +
    descHtml(s) +
    '<div class="path"><a href="' + esc(s.url) + '" target="_blank" rel="noopener">' + esc(s.path) + "</a></div>" +
    metaTags(s.metadata) +
    installSteps(s) +
  "</div>";
}

const grid = document.getElementById("grid");
const emptyEl = document.getElementById("empty");
const countEl = document.getElementById("count");
const filterEl = document.getElementById("filter");
const sortEl = document.getElementById("sort");
const perPageEl = document.getElementById("perPage");
const repoFilterEl = document.getElementById("repoFilter");
const repoFilterWrap = document.getElementById("repoFilterWrap");
const pagerEl = document.getElementById("pager");
const pageInfoEl = document.getElementById("pageInfo");
const prevEl = document.getElementById("prev");
const nextEl = document.getElementById("next");
const tableWrap = document.getElementById("tableWrap");
const tableHead = document.getElementById("tableHead");
const tableBody = document.getElementById("tableBody");
const viewTableBtn = document.getElementById("viewTable");
const viewCardsBtn = document.getElementById("viewCards");
const modalEl = document.getElementById("modal");
const modalPanel = document.getElementById("modalPanel");

let page = 1;  // 1-based; reset to 1 whenever the matched set changes.
// Cards is the default view; a saved choice (or last click) overrides it.
let view = "cards";
try { const v = localStorage.getItem("skills-view"); if (v === "table" || v === "cards") view = v; } catch (e) { /* ignore */ }

// Comparators keyed by the Sort dropdown's value. Each pair is {s, i} where
// i is the original index into DATA.skills.
const SORTERS = {
  "name-asc":   (a, b) => a.s.name.toLowerCase().localeCompare(b.s.name.toLowerCase()),
  "name-desc":  (a, b) => b.s.name.toLowerCase().localeCompare(a.s.name.toLowerCase()),
};

function matched() {
  const q = filterEl.value.trim().toLowerCase();
  const terms = q ? q.split(/\s+/) : [];
  const repo = repoFilterEl ? repoFilterEl.value : "";
  const list = DATA.skills
    .map((s, i) => ({ s, i }))
    .filter(({ s }) => (!repo || repoLabel(s) === repo) && terms.every(t => s._hay.includes(t)));
  list.sort(SORTERS[sortEl.value] || SORTERS["name-asc"]);
  return list;
}

function render() {
  const list = matched();
  const per = perPageEl.value === "all" ? list.length || 1 : parseInt(perPageEl.value, 10);
  const pages = Math.max(1, Math.ceil(list.length / per));
  if (page > pages) page = pages;          // clamp after filtering shrinks the set
  const start = (page - 1) * per;
  const slice = list.slice(start, start + per);

  // Same filtered/sorted/paginated slice, rendered as cards or a table row set.
  if (view === "table") {
    grid.style.display = "none";
    tableWrap.style.display = list.length ? "block" : "none";
    renderTable(slice);
  } else {
    tableWrap.style.display = "none";
    grid.style.display = "grid";
    grid.innerHTML = slice.map(({ s, i }) => card(s, i)).join("");
  }
  emptyEl.style.display = list.length ? "none" : "block";
  countEl.textContent = list.length + " of " + DATA.skills.length + " skill(s)";

  // Pagination bar only appears when the matched set exceeds one page.
  if (list.length > per) {
    pagerEl.style.display = "flex";
    pageInfoEl.textContent = "Page " + page + " of " + pages +
      "  ·  showing " + (start + 1) + "–" + (start + slice.length);
    prevEl.disabled = page <= 1;
    nextEl.disabled = page >= pages;
  } else {
    pagerEl.style.display = "none";
  }
}

// Changing the filter, sort, or page size returns the user to page 1.
function resetAndRender() { page = 1; render(); }

/* ---- table view -------------------------------------------------------- */
// Columns: Name (sortable) · Type · Repository (only when aggregating repos,
// where the value actually varies). Each row carries data-idx into DATA.skills
// so a click can open the detail modal.
function renderTable(slice) {
  const dir = sortEl.value === "name-desc" ? "▼" : "▲";
  tableHead.innerHTML =
    '<th data-sort="name">Name <span class="sort-ind">' + dir + "</span></th>" +
    "<th>Type</th>" +
    (DATA.aggregate ? "<th>Repository</th>" : "");
  tableBody.innerHTML = slice.map(({ s, i }) =>
    '<tr data-idx="' + i + '">' +
      '<td class="name">' + esc(s.name) + "</td>" +
      "<td>" + typeBadge(s) + "</td>" +
      (DATA.aggregate ? '<td class="repo">' + esc(repoLabel(s)) + "</td>" : "") +
    "</tr>"
  ).join("");
}

/* ---- detail modal ------------------------------------------------------ */
function copyText(text, btn) {
  const done = () => { btn.textContent = "Copied!"; setTimeout(() => { btn.textContent = "Copy"; }, 1500); };
  const fail = () => { btn.textContent = "Copy failed"; setTimeout(() => { btn.textContent = "Copy"; }, 1500); };
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(done, () => fallbackCopy(text, done, fail));
  } else {
    fallbackCopy(text, done, fail);
  }
}
// file:// pages often can't use the async clipboard API; fall back to a hidden
// textarea + execCommand so Copy still works when opened straight from disk.
function fallbackCopy(text, done, fail) {
  try {
    const ta = document.createElement("textarea");
    ta.value = text; ta.style.position = "fixed"; ta.style.opacity = "0";
    document.body.appendChild(ta); ta.focus(); ta.select();
    const ok = document.execCommand("copy");
    ta.remove();
    ok ? done() : fail();
  } catch (e) { fail(); }
}

function openModal(idx) {
  const s = DATA.skills[idx];
  if (!s) return;
  const dirUrl = folderUrl(s);
  const nameHtml = dirUrl
    ? '<a href="' + esc(dirUrl) + '" target="_blank" rel="noopener">' + esc(s.name) + "</a>"
    : esc(s.name);
  const pluginTxt = (s.type === "plugin" && s.plugin)
    ? "<span>plugin: " + esc(s.plugin) + "</span>" : "";
  const steps = Array.isArray(s.installation) ? s.installation : [];
  const stepsBlock = steps.length
    ? '<div class="steps-head"><h3>Installation steps</h3>' +
        '<button type="button" class="copy-btn" id="copySteps">Copy</button></div>' +
        '<pre class="install-steps">' + steps.map(esc).join("\n") + "</pre>"
    : "";
  const pathBlock = s.url
    ? '<div class="path" style="margin-top:14px"><a href="' + esc(s.url) +
        '" target="_blank" rel="noopener">' + esc(s.path) + "</a></div>"
    : '<div class="path" style="margin-top:14px">' + esc(s.path || "") + "</div>";

  modalPanel.innerHTML =
    '<div class="modal-head"><h2>' + nameHtml + "</h2>" +
      '<button type="button" class="modal-close" id="modalClose" aria-label="Close">×</button></div>' +
    '<div class="badges">' + typeBadge(s) + pluginTxt +
      (s.repo ? '<span class="repo-badge">' + esc(repoLabel(s)) + "</span>" : "") + "</div>" +
    '<div class="modal-desc">' + esc(s.description || "") + "</div>" +
    pathBlock + stepsBlock;

  modalEl.classList.add("open");
  modalEl.setAttribute("aria-hidden", "false");
  document.body.classList.add("modal-open");

  document.getElementById("modalClose").addEventListener("click", closeModal);
  const copyBtn = document.getElementById("copySteps");
  if (copyBtn) copyBtn.addEventListener("click", () => copyText(steps.join("\n"), copyBtn));
}

function closeModal() {
  modalEl.classList.remove("open");
  modalEl.setAttribute("aria-hidden", "true");
  document.body.classList.remove("modal-open");
}

/* ---- view switch ------------------------------------------------------- */
function applyView(v) {
  view = v;
  viewTableBtn.classList.toggle("active", v === "table");
  viewCardsBtn.classList.toggle("active", v === "cards");
  render();
}

/* ---- wire up ------------------------------------------------------------ */
if (DATA.aggregate) {
  // Combined view across many registered repos.
  const repos = Array.isArray(DATA.repos) ? DATA.repos : [];
  document.getElementById("title").innerHTML = "All registered skills";
  document.getElementById("subtitle").textContent =
    DATA.count + " skill(s) across " + repos.length + " repo(s)";
  // Populate and reveal the repo filter, sorted by repo name.
  repoFilterWrap.style.display = "flex";
  const repoName = r => r.source || r.repo || "";
  for (const r of repos.slice().sort((a, b) => repoName(a).toLowerCase().localeCompare(repoName(b).toLowerCase()))) {
    const opt = document.createElement("option");
    opt.value = repoName(r);
    opt.textContent = repoName(r) + " (" + r.count + ")";
    repoFilterEl.appendChild(opt);
  }
  repoFilterEl.addEventListener("change", resetAndRender);
} else if (DATA.local) {
  // Local clone: no GitHub URL to link to, so show the source path as plain text.
  document.getElementById("title").innerHTML = "Skills in " + esc(DATA.repo);
} else {
  document.getElementById("title").innerHTML =
    'Skills in <a href="https://github.com/' + esc(DATA.owner) + "/" + esc(DATA.repo) +
    '" target="_blank" rel="noopener">' + esc(DATA.owner) + "/" + esc(DATA.repo) + "</a>";
}
if (!DATA.aggregate) {
  document.getElementById("subtitle").textContent =
    DATA.count + " skill(s) · " + (DATA.local ? "local clone: " + (DATA.source || DATA.repo)
                                              : "branch/ref: " + DATA.ref);
}
if (DATA.truncated) {
  const w = document.getElementById("truncated");
  w.style.display = "block";
  w.textContent = "⚠ The repo tree was truncated by GitHub (very large repo) — some skills may be missing.";
}
filterEl.addEventListener("input", resetAndRender);
sortEl.addEventListener("change", resetAndRender);
perPageEl.addEventListener("change", resetAndRender);
prevEl.addEventListener("click", () => { if (page > 1) { page--; render(); window.scrollTo(0, 0); } });
nextEl.addEventListener("click", () => { page++; render(); window.scrollTo(0, 0); });
document.getElementById("exportJson").addEventListener("click", exportJson);
document.getElementById("exportCsv").addEventListener("click", exportCsv);

// Export dropdown: click toggles it open (for touch/keyboard); CSS handles hover.
// An option click, or any click elsewhere, bubbles to document and closes it.
const exportMenu = document.getElementById("exportMenu");
const exportBtn = document.getElementById("exportBtn");
exportBtn.addEventListener("click", e => {
  e.stopPropagation();
  const open = exportMenu.classList.toggle("open");
  exportBtn.setAttribute("aria-expanded", open ? "true" : "false");
});
document.addEventListener("click", () => {
  exportMenu.classList.remove("open");
  exportBtn.setAttribute("aria-expanded", "false");
});

/* ---- day / night theme toggle ------------------------------------------- */
// The head script already applied the initial theme; here we just label the
// button to match and let clicks flip + persist the choice.
const themeToggleEl = document.getElementById("themeToggle");
function currentTheme() { return document.documentElement.getAttribute("data-theme") === "light" ? "light" : "dark"; }
function syncThemeButton() {
  // Show the theme you'd switch *to*, matching common toggle UX.
  themeToggleEl.textContent = currentTheme() === "light" ? "🌙 Dark" : "☀️ Light";
}
syncThemeButton();
themeToggleEl.addEventListener("click", () => {
  const next = currentTheme() === "light" ? "dark" : "light";
  document.documentElement.setAttribute("data-theme", next);
  try { localStorage.setItem("skills-theme", next); } catch (e) { /* ignore */ }
  syncThemeButton();
});
grid.addEventListener("click", e => {
  // Expand/collapse a clamped description.
  const toggle = e.target.closest(".desc-toggle");
  if (toggle) {
    const wrap = toggle.closest(".desc");
    const textEl = wrap.querySelector(".desc-text");
    if (toggle.dataset.expanded === "1") {
      textEl.textContent = wrap.dataset.full.slice(0, DESC_LIMIT).replace(/\s+$/, "");
      toggle.textContent = "…more";
      toggle.dataset.expanded = "0";
    } else {
      textEl.textContent = wrap.dataset.full;
      toggle.textContent = " show less";
      toggle.dataset.expanded = "1";
    }
    return;
  }
  // Clicking a repo badge (aggregate view) filters down to that repo.
  const badge = e.target.closest(".repo-badge[data-source]");
  if (badge && DATA.aggregate && repoFilterEl) {
    repoFilterEl.value = badge.dataset.source;
    resetAndRender();
  }
});

/* ---- table + modal + view-toggle events -------------------------------- */
viewTableBtn.addEventListener("click", () => { applyView("table"); try { localStorage.setItem("skills-view", "table"); } catch (e) {} });
viewCardsBtn.addEventListener("click", () => { applyView("cards"); try { localStorage.setItem("skills-view", "cards"); } catch (e) {} });
// Clicking the Name header toggles the shared sort order (asc <-> desc).
tableHead.addEventListener("click", e => {
  if (!e.target.closest("th[data-sort]")) return;
  sortEl.value = sortEl.value === "name-asc" ? "name-desc" : "name-asc";
  render();
});
// A row click opens the detail modal (ignore clicks on links inside the row).
tableBody.addEventListener("click", e => {
  if (e.target.closest("a")) return;
  const tr = e.target.closest("tr[data-idx]");
  if (tr) openModal(parseInt(tr.dataset.idx, 10));
});
// Close the modal by clicking the backdrop or pressing Escape.
modalEl.addEventListener("click", e => { if (e.target === modalEl) closeModal(); });
document.addEventListener("keydown", e => { if (e.key === "Escape" && modalEl.classList.contains("open")) closeModal(); });

applyView(view);  // sets the toggle's active button and does the first render
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Scan a GitHub repo or local clone for Claude skills and write a self-contained HTML page."
    )
    parser.add_argument(
        "--repo", required=True,
        help=(
            "GitHub repo ('owner/repo', a URL, or a '/tree/<ref>' URL to pin a "
            "branch/tag) OR a path to an already-cloned local directory."
        ),
    )
    parser.add_argument(
        "--output", "-o", default="repo_skills.html",
        help="Output HTML file (default: repo_skills.html).",
    )
    args = parser.parse_args(argv)

    try:
        print(f"Scanning {args.repo} …", file=sys.stderr)
        data = scan_repo(args.repo)
    except ScanError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    page = render_html(data)
    with open(args.output, "w", encoding="utf-8") as fh:
        fh.write(page)

    print(
        f"Found {data['count']} skill(s) in {data.get('source') or data['repo']} "
        f"(ref: {data['ref']}). Wrote {args.output}.",
        file=sys.stderr,
    )
    if data["truncated"]:
        print("Note: GitHub truncated the file tree; some skills may be missing.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
