#!/usr/bin/env python3
"""
run_evals.py — Run evals.json against a live Claude model to validate skill behaviour.

Requires ANTHROPIC_API_KEY environment variable.

Usage:
  # Run all evals across all skills
  python scripts/run_evals.py

  # Run evals for a specific plugin
  python scripts/run_evals.py --plugin idm-software-tools
  python scripts/run_evals.py --plugin idm-software-tools

  # Run evals for a specific skill
  python scripts/run_evals.py --plugin idm-software-tools --skill idm-pkg-install

  # Run a single eval by ID
  python scripts/run_evals.py --plugin idm-software-tools --skill idm-pkg-install --eval-id pkg-001

   # Run a single eval by ID with details and write to output
  python scripts/run_evals.py --plugin idm-software-tools --skill idm-pkg-install --eval-id pkg-workflow-001 --verbose --output wf.json

  # Use a different model
  python scripts/run_evals.py --model claude-haiku-4-5-20251001

  # Show full responses (default: show summary only)
  python scripts/run_evals.py --verbose

  # Save results to a JSON file
  python scripts/run_evals.py --output results.json

  # Only run evals for skills changed vs main (for CI)
  python scripts/run_evals.py --changed-from origin/main

  # Override with a fixture file instead of evals.json (dev / PII testing)
  python scripts/run_evals.py --plugin idm-software-tools --skill idm-pkg-install --fixture edge_cases.json

  # Use real data from env var and mask PII in saved results
  python scripts/run_evals.py --plugin idm-software-tools --skill idm-pkg-install --real --mask-output --output results.json

NOTE: This script calls the Anthropic API for every eval case. Each call costs tokens.
      Run targeted evals during development; run all evals before merging to main.
"""

import argparse
from collections import defaultdict
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

try:
    import anthropic
except ImportError:
    print("ERROR: anthropic package not installed. Run: pip install anthropic")
    sys.exit(1)

try:
    import yaml
except ImportError:
    print("ERROR: pyyaml not installed. Run: pip install pyyaml")
    sys.exit(1)

REPO_ROOT = Path(__file__).parent.parent

# load_plugins is required (builds PLUGINS from marketplace.json);
# SensitiveDataMasker is optional (only used with --mask-output).
from utils import load_plugins
try:
    from utils import SensitiveDataMasker
except ImportError:
    SensitiveDataMasker = None

# PLUGINS is built dynamically from .claude-plugin/marketplace.json — see
# load_plugins() in scripts/utils.py. Only plugins registered there are valid
# --plugin targets.
#
# Legacy hard-coded map, kept for reference only:
# PLUGINS = {
#     "foundation-wide":    REPO_ROOT / "foundation-wide",
#     "idm":                REPO_ROOT / "groups" / "idm",
#     "global-health":      REPO_ROOT / "groups" / "global-health",
#     "global-development": REPO_ROOT / "groups" / "global-development",
#     "policy":             REPO_ROOT / "groups" / "policy",
#     "finance":            REPO_ROOT / "groups" / "finance",
#     "it":                 REPO_ROOT / "groups" / "it",
# }
PLUGINS = load_plugins()


def pair_changed_skills(changed_files, plugins, root: Path):
    """Return (pairs, skipped) for a list of changed file paths.

    `pairs` is [(plugin_name, skill_name), ...] for skills that sit inside a
    REGISTERED plugin, and feeds the eval run. `skipped` is the repo-relative
    directory of every changed skill that no registered plugin claims.

    Skipped skills used to vanish here with nothing recorded. That is the
    "built but not published" state -- legitimate under the publication policy,
    since registration in marketplace.json is a separate act from merging -- so
    it must not fail. But it must not be invisible either: the caller passed a
    changed skill and got a green check having run none of its evals. Returning
    them lets the caller say so.

    Pure and injectable so this is testable without git or a real catalog: the
    silent drop was only reachable through a git diff against a live registry,
    which is why no test caught it.
    """
    pairs, skipped = set(), set()
    for filepath in changed_files:
        if not filepath.is_relative_to(root):
            continue
        current = filepath.parent
        while current != root and current.parent != current:
            if (current / "SKILL.md").exists():
                owner = None
                for pname, ppath in plugins.items():
                    try:
                        current.relative_to(ppath)
                    except ValueError:
                        continue
                    owner = pname
                    break
                if owner:
                    pairs.add((owner, current.name))
                else:
                    skipped.add(current.relative_to(root).as_posix())
                break
            current = current.parent
    return sorted(pairs), sorted(skipped)


def get_changed_skills(base_ref: str):
    """Return (pairs, skipped) for skills with files changed vs base_ref."""
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", f"{base_ref}...HEAD"],
            capture_output=True, text=True, check=True,
            cwd=str(REPO_ROOT),
        )
    except subprocess.CalledProcessError as e:
        sys.exit(f"ERROR: git diff failed for ref '{base_ref}'. Is this a git repo?\n{e.stderr.strip()}")
    changed_files = [REPO_ROOT / f for f in result.stdout.strip().splitlines() if f]

    return pair_changed_skills(changed_files, PLUGINS, REPO_ROOT)


def _read_fixture(path: Path) -> list[dict]:
    """Read a fixture file, normalising wrapped {evals:[...]} or bare [...] format."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = data.get("evals", data)
    if not isinstance(data, list):
        raise ValueError(f"Fixture at {path} must be a JSON array or {{\"evals\":[...]}} object")
    return data


def _legacy_to_rubric(expected: dict) -> dict:
    """Convert eval_runner.py legacy expected-dict format to rubric format."""
    rubric: dict = {}
    if "risk_level" in expected:
        rubric["must_include_one_of"] = [expected["risk_level"]]
    if "fields_flagged" in expected:
        rubric["must_include"] = list(expected["fields_flagged"])
    if expected.get("pii_detected") is False:
        rubric.setdefault("must_not_include", []).append("pii detected")
    return rubric


DEFAULT_MODEL = "claude-sonnet-4-6"
DEFAULT_JUDGE_MODEL = DEFAULT_MODEL   # judges workflow-eval transcripts
PASS  = "PASS"
FAIL  = "FAIL"
ERROR = "ERROR"


# ── Helpers ────────────────────────────────────────────────────────────────

def ok(msg):    print(f"  ✓  {msg}")
def fail(msg):  print(f"  ✗  {msg}")
def warn(msg):  print(f"  ⚠  {msg}")
def info(msg):  print(f"  →  {msg}")


def get_skills(plugin_path: Path, skill_filter: str = None) -> list[Path]:
    skills_root = plugin_path / "skills"
    if not skills_root.exists():
        return []
    found = sorted([p.parent for p in skills_root.rglob("SKILL.md")])
    if skill_filter:
        found = [s for s in found if s.name == skill_filter]
    return found


def load_skill_md(skill_dir: Path) -> str:
    # encoding="utf-8" is required: SKILL.md is the system prompt, and Python's
    # platform default is cp1252 on Windows, which mangles non-ASCII (—, →) into
    # mojibake before it reaches the model.
    return (skill_dir / "SKILL.md").read_text(encoding="utf-8")


def load_evals(
    skill_dir: Path,
    fixture: str | None = None,
    use_real: bool = False,
) -> dict | None:
    # Real data via env var (requires TEST_REAL_FIXTURES_PATH)
    if use_real:
        real_path_str = os.environ.get("TEST_REAL_FIXTURES_PATH")
        if real_path_str:
            print("[real data] ⚠  Loading from TEST_REAL_FIXTURES_PATH — ensure logs are not stored")
            return {"evals": _read_fixture(Path(real_path_str))}
        warn("--real set but TEST_REAL_FIXTURES_PATH is not in env; falling back to evals.json")

    # Named fixture file: a path, or a bare filename looked up per-skill.
    #
    # The per-skill location is the one docs/SKILL_FOLDER_TEMPLATE.md documents
    # (`evals/fixtures/synthetic/` INSIDE the skill folder), and it was
    # previously unreachable: this searched REPO_ROOT/evals/fixtures/, a
    # directory that has never existed in this repo. `skill_dir` was passed in
    # and never used, so a contributor who followed the template got
    # FileNotFoundError naming a path they had no way to satisfy.
    #
    # REPO_ROOT is still searched after it, so an absolute-free filename kept
    # anywhere central keeps working.
    if fixture:
        direct = Path(fixture).resolve()
        if direct.exists():
            return {"evals": _read_fixture(direct)}
        tried = []
        for base in (skill_dir, REPO_ROOT):
            for subdir in ("synthetic", "real"):
                candidate = base / "evals" / "fixtures" / subdir / fixture
                tried.append(candidate)
                if candidate.exists():
                    return {"evals": _read_fixture(candidate)}
        # Name every path actually tried. The old message said
        # "evals/fixtures/synthetic/" without saying relative to what, which is
        # exactly the ambiguity that hid the bug above.
        locations = "".join(f"\n  {c}" for c in [direct, *tried])
        raise FileNotFoundError(
            f"Fixture '{fixture}' not found. Looked in:{locations}"
        )

    # Default: skill's own evals/evals.json
    evals_path = skill_dir / "evals" / "evals.json"
    if not evals_path.exists():
        return None
    return json.loads(evals_path.read_text(encoding="utf-8"))


def parse_frontmatter(skill_md: str) -> dict:
    parts = skill_md.split("---", 2)
    if len(parts) < 3:
        return {}
    try:
        return yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        return {}


# ── Rubric checker ─────────────────────────────────────────────────────────

def check_rubric(response_text: str, rubric: dict) -> tuple[str, list[str]]:
    """
    Evaluate a response against a rubric. Returns (PASS|FAIL, [reasons]).

    Supported rubric fields:
      pass_condition        — human-readable description (not machine-checked)
      must_include          — list of strings, ALL must appear in the response
      must_include_one_of   — list of strings, at least ONE must appear
      must_include_groups   — list of groups; each group needs at least one match
      must_not_include      — list of strings, NONE may appear in the response
    """
    reasons = []
    text_lower = response_text.lower()

    # must_include — every term must appear
    for term in rubric.get("must_include", []):
        if term.lower() not in text_lower:
            reasons.append(f"missing required: '{term}'")

    # must_include_one_of
    one_of = rubric.get("must_include_one_of", [])
    if one_of:
        if not any(term.lower() in text_lower for term in one_of):
            reasons.append(f"must include at least one of: {one_of}")

    # must_include_groups (list of one-of groups — each group requires at least one match)
    for group in rubric.get("must_include_groups", []):
        if not any(term.lower() in text_lower for term in group):
            reasons.append(f"must include at least one of: {group}")

    # must_not_include
    for term in rubric.get("must_not_include", []):
        if term.lower() in text_lower:
            reasons.append(f"must NOT include: '{term}'")

    status = FAIL if reasons else PASS
    return status, reasons


# ── Single eval runner ─────────────────────────────────────────────────────

def judge_transcript(client, judge_model, skill_name, transcript, pass_condition):
    """LLM-judge a multi-turn transcript against a natural-language pass condition."""
    convo = "\n\n".join(
        f"USER: {t['user']}\nASSISTANT: {t['assistant']}" for t in transcript
    )
    judge_system = (
        "You are a strict, fair evaluator of an AI assistant's behavior. Given a PASS "
        "CONDITION and a conversation TRANSCRIPT, decide whether the assistant satisfied "
        "the condition across the whole interaction. Judge the trajectory and final "
        "state, not just wording. Reply with ONLY a JSON object: "
        '{"verdict": "PASS" | "FAIL", "reason": "<one sentence>"}.'
    )
    judge_user = f"SKILL: {skill_name}\nPASS CONDITION: {pass_condition}\n\nTRANSCRIPT:\n{convo}"
    resp = client.messages.create(
        model=judge_model, max_tokens=512, temperature=0,
        system=judge_system, messages=[{"role": "user", "content": judge_user}],
    )
    text = resp.content[0].text.strip()
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return False, f"judge returned no JSON verdict: {text[:150]}"
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return False, f"judge JSON parse error: {text[:150]}"
    return str(data.get("verdict", "")).upper() == "PASS", data.get("reason", "")


def run_workflow_eval(client, skill_name, skill_md, eval_case, model, judge_model,
                      verbose, masker=None):
    """Multi-turn, LLM-judged eval for agentic/workflow skills. Advisory (non-gating)."""
    eval_id   = eval_case["id"]
    eval_name = eval_case["name"]
    steps     = eval_case.get("steps") or []
    pass_condition = eval_case.get("pass_condition", "")
    started_at = time.time()
    try:
        messages, transcript = [], []
        for step in steps:
            messages.append({"role": "user", "content": step})
            resp = client.messages.create(
                model=model, max_tokens=4096, temperature=0,
                system=skill_md, messages=messages,
            )
            text = resp.content[0].text
            messages.append({"role": "assistant", "content": text})
            transcript.append({"user": step, "assistant": text})

        verdict, reason = judge_transcript(client, judge_model, skill_name, transcript, pass_condition)
        status = PASS if verdict else FAIL
        elapsed = round(time.time() - started_at, 1)

        if verbose:
            for t in transcript:
                print(f"\n    USER: {t['user']}")
                print(f"    ASSISTANT: {t['assistant'][:300]}")
            print(f"    JUDGE: {reason}")

        if masker:
            transcript = [{"user": masker.mask_string(t["user"]),
                           "assistant": masker.mask_string(t["assistant"])} for t in transcript]

        return {
            "eval_id": eval_id, "eval_name": eval_name, "type": "workflow",
            "advisory": True, "status": status,
            "reasons": [] if verdict else [reason],
            "judge_reason": reason,
            "elapsed_s": elapsed, "steps": len(steps),
            "transcript": transcript, "pass_condition": pass_condition,
        }
    except Exception as e:
        return {
            "eval_id": eval_id, "eval_name": eval_name, "type": "workflow",
            "advisory": True, "status": ERROR, "reasons": [str(e)],
            "elapsed_s": round(time.time() - started_at, 1),
        }


def run_eval(
    client: anthropic.Anthropic,
    skill_name: str,
    skill_md: str,
    eval_case: dict,
    model: str,
    verbose: bool,
    masker=None,
    judge_model: str | None = None,
) -> dict:
    if eval_case.get("type") == "workflow":
        return run_workflow_eval(client, skill_name, skill_md, eval_case, model,
                                 judge_model or model, verbose, masker=masker)
    eval_id    = eval_case["id"]
    eval_name  = eval_case["name"]
    input_text = eval_case["prompt"]

    # Resolve rubric — supports new `rubric` key, nested `expected.rubric`, and legacy flat `expected`
    rubric = eval_case.get("rubric") or {}
    if not rubric:
        expected = eval_case.get("expected") or {}
        if isinstance(expected, dict):
            rubric = expected.get("rubric") or _legacy_to_rubric(expected)

    started_at = time.time()

    try:
        response = client.messages.create(
            model=model,
            # Long-form skills can exceed 4096 tokens and get truncated before
            # the tail of the output — which made keyword rubrics that look
            # for a closing section (Limitations, etc.) flaky.
            max_tokens=8192,
            temperature=0,
            system=skill_md,
            messages=[{"role": "user", "content": input_text}]
        )
        response_text = response.content[0].text
        elapsed = round(time.time() - started_at, 1)

        status, reasons = check_rubric(response_text, rubric)

        if verbose:
            print(f"\n    Input:    {input_text}")
            print(f"    Response: {response_text[:300]}{'...' if len(response_text) > 300 else ''}")
            if reasons:
                for r in reasons:
                    print(f"    Reason:   {r}")

        safe_response = masker.mask_string(response_text) if masker else response_text

        return {
            "eval_id":       eval_id,
            "eval_name":     eval_name,
            "status":        status,
            "reasons":       reasons,
            "elapsed_s":     elapsed,
            "prompt":        input_text,
            "response":      safe_response,
            "pass_condition": rubric.get("pass_condition", "")
        }

    except Exception as e:
        elapsed = round(time.time() - started_at, 1)
        return {
            "eval_id":   eval_id,
            "eval_name": eval_name,
            "status":    ERROR,
            "reasons":   [str(e)],
            "elapsed_s": elapsed,
            "prompt":    input_text,
            "response":  ""
        }


# ── Skill eval runner ──────────────────────────────────────────────────────

def run_skill_evals(
    client: anthropic.Anthropic,
    skill_dir: Path,
    plugin_name: str,
    model: str,
    eval_id_filter: str | None,
    verbose: bool,
    fixture: str | None = None,
    use_real: bool = False,
    masker=None,
    judge_model: str | None = None,
    plugin_path: Path | None = None,
) -> dict:
    skill_md   = load_skill_md(skill_dir)
    fm         = parse_frontmatter(skill_md)
    skill_name = fm.get("name", skill_dir.name)
    evals_data = load_evals(skill_dir, fixture=fixture, use_real=use_real)

    # Passed in rather than looked up: --path targets a plugin that is not in
    # the registry at all, so PLUGINS[plugin_name] would raise KeyError. The
    # registry stays the fallback for callers that resolved through it.
    base = (plugin_path or PLUGINS[plugin_name]) / "skills"
    rel = skill_dir.relative_to(base)

    if evals_data is None:
        warn(f"{rel} — no evals/evals.json, skipping")
        return {"skill": skill_name, "skipped": True, "results": []}

    eval_cases = evals_data.get("evals", [])
    if eval_id_filter:
        eval_cases = [e for e in eval_cases if e["id"] == eval_id_filter]
        if not eval_cases:
            warn(f"{rel} — eval ID '{eval_id_filter}' not found")
            return {"skill": skill_name, "skipped": True, "results": []}

    print(f"\n  {rel}  ({len(eval_cases)} eval(s))")

    results = []
    for ev in eval_cases:
        result = run_eval(client, skill_name, skill_md, ev, model, verbose,
                          masker=masker, judge_model=judge_model)
        results.append(result)

        tag = " [workflow·advisory]" if result.get("advisory") else ""
        if result["status"] == PASS:
            ok(f"{ev['id']}  {ev['name']}{tag}  ({result['elapsed_s']}s)")
        elif result["status"] == FAIL:
            (warn if result.get("advisory") else fail)(f"{ev['id']}  {ev['name']}{tag}  ({result['elapsed_s']}s)")
            if not result.get("judge_reason"):
                for r in result["reasons"]:
                    print(f"       → {r}")
        else:
            warn(f"{ev['id']}  {ev['name']}{tag}  ERROR: {result['reasons'][0]}")
        if result.get("judge_reason"):
            print(f"       judge: {result['judge_reason']}")

    gating   = [r for r in results if not r.get("advisory")]
    advisory = [r for r in results if r.get("advisory")]
    passed  = sum(1 for r in gating if r["status"] == PASS)
    failed  = sum(1 for r in gating if r["status"] == FAIL)
    errored = sum(1 for r in gating if r["status"] == ERROR)

    return {
        "skill":   skill_name,
        "path":    str(rel),
        "skipped": False,
        "passed":  passed,
        "failed":  failed,
        "errored": errored,
        "total":   len(gating),
        "advisory": {
            "passed":  sum(1 for r in advisory if r["status"] == PASS),
            "failed":  sum(1 for r in advisory if r["status"] == FAIL),
            "errored": sum(1 for r in advisory if r["status"] == ERROR),
            "total":   len(advisory),
        },
        "results": results
    }


# ── Main ───────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Run evals.json against a live Claude model to validate skill behaviour"
    )
    # --plugin's choices come from marketplace.json, so it cannot name a plugin
    # that is not registered. --changed-from covers that while the work differs
    # from main, and stops the moment it merges -- leaving a merged-but-
    # unpublished plugin's evals unreachable by either. Mirrors deploy.py --path.
    parser.add_argument("--path", default=None, metavar="DIR",
                        help="Plugin directory to run evals for, bypassing the "
                             "registry — use this for a plugin that is not "
                             "published yet")
    parser.add_argument("--plugin",   choices=list(PLUGINS.keys()),
                        help="Target a specific plugin (default: all)")
    parser.add_argument("--skill",    default=None,
                        help="Target a single skill by name")
    parser.add_argument("--eval-id",  default=None,
                        help="Run a single eval case by ID (e.g. pkg-001)")
    parser.add_argument("--model",    default=DEFAULT_MODEL,
                        help=f"Anthropic model to use (default: {DEFAULT_MODEL})")
    parser.add_argument("--judge-model", default=DEFAULT_JUDGE_MODEL,
                        help="Model that judges workflow-eval transcripts")
    parser.add_argument("--strict-workflow", action="store_true",
                        help="Gate on workflow evals too (default: advisory, non-gating)")
    parser.add_argument("--verbose",  action="store_true",
                        help="Show full inputs and responses")
    parser.add_argument("--output",   default=None,
                        help="Save full results to a JSON file")
    parser.add_argument(
        "--changed-from",
        default=None,
        metavar="REF",
        help="Only run evals for skills changed vs REF (e.g. origin/main)",
    )
    parser.add_argument(
        "--fixture",
        default=None,
        metavar="FILE",
        help="Override evals source: path to a JSON fixture file, or a filename searched "
             "in evals/fixtures/synthetic/ (requires --plugin and --skill)",
    )
    parser.add_argument(
        "--real",
        action="store_true",
        help="Load real fixtures from TEST_REAL_FIXTURES_PATH env var (requires --plugin and --skill)",
    )
    parser.add_argument(
        "--mask-output",
        action="store_true",
        help="Mask PII patterns in saved --output JSON (uses evals/shared/utils.py)",
    )
    args = parser.parse_args()

    if args.path and args.plugin:
        parser.error("--path and --plugin both select a plugin; use one")

    # Resolved here rather than in the selection branch below, because the
    # API-key check sits between the two and a bad --path was reporting a
    # missing key instead of the real problem.
    path_plugin = None
    if args.path:
        ppath = Path(args.path).resolve()
        manifest = ppath / ".claude-plugin" / "plugin.json"
        if not manifest.exists():
            parser.error(f"{args.path} has no .claude-plugin/plugin.json — point "
                         "--path at a plugin directory, not a skill or a root")
        try:
            declared = (json.loads(manifest.read_text(encoding="utf-8")) or {}).get("name")
        except json.JSONDecodeError as e:
            parser.error(f"{manifest} is not valid JSON: {e}")
        # The declared name, not the directory name: it is what the registry
        # would publish and what every other report keys on.
        path_plugin = (declared or ppath.name, ppath)
    if (args.fixture or args.real) and not ((args.plugin or args.path) and args.skill):
        parser.error("--fixture and --real require --skill, plus --plugin or --path")

    # Check API key
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY environment variable not set.")
        print("       export ANTHROPIC_API_KEY=sk-ant-...")
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)

    masker = None
    if args.mask_output:
        if SensitiveDataMasker is None:
            warn("--mask-output requested but evals/shared/utils.py could not be imported; masking disabled")
        else:
            masker = SensitiveDataMasker()

    print(f"\nRunning evals  model={args.model}  {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}\n")
    print("─" * 60)

    all_skill_results = []
    total_passed = total_failed = total_errored = total_skipped = 0
    total_adv_passed = total_adv_failed = total_adv_errored = 0

    # Build run list and execute evals
    if args.changed_from:
        changed_pairs, skipped_skills = get_changed_skills(args.changed_from)

        # Report before running. A changed skill whose plugin is unregistered
        # gets no evals, and this check still passes -- which is correct policy
        # but was previously indistinguishable from "everything was tested".
        if skipped_skills:
            warn(f"{len(skipped_skills)} changed skill(s) had NO evals run — their "
                 "plugin is not registered in .claude-plugin/marketplace.json:")
            for s in skipped_skills:
                warn(f"      {s}")
            warn("This is the 'built but not published' state, not a failure, so this "
                 "check still passes — but nothing here has been eval'd.")
            warn("To eval them: add the marketplace entry, or run "
                 "scripts/run_evals.py --plugin <name> locally.")

        if not changed_pairs:
            if skipped_skills:
                # Distinct from the message below: changes WERE detected, then
                # skipped. Saying "no changes detected" here would be false.
                print("No evals to run — every changed skill belongs to an "
                      "unregistered plugin (listed above).")
            else:
                print("No skill changes detected — nothing to eval.")
            sys.exit(0)
        if args.plugin or args.skill:
            warn("--plugin and --skill are ignored when --changed-from is set")
        # No `pname in PLUGINS` guard: pair_changed_skills only emits names it
        # already resolved against PLUGINS, so the old guard could never fire
        # and hid that the real filtering happened upstream.
        grouped: dict[str, list[str]] = defaultdict(list)
        for pname, sname in changed_pairs:
            grouped[pname].append(sname)
        run_list = [(pname, PLUGINS[pname], snames) for pname, snames in grouped.items()]

        for pname, plugin_path, skill_names in run_list:
            skills = []
            for sname in skill_names:
                skills.extend(get_skills(plugin_path, sname))
            if not skills:
                continue

            print(f"\n── {pname} ──")

            for skill_dir in skills:
                result = run_skill_evals(
                    client, skill_dir, pname,
                    args.model, args.eval_id, args.verbose,
                    masker=masker,
                    judge_model=args.judge_model,
                )
                result["plugin"] = pname
                all_skill_results.append(result)

                if result.get("skipped"):
                    total_skipped += 1
                else:
                    total_passed  += result.get("passed", 0)
                    total_failed  += result.get("failed", 0)
                    total_errored += result.get("errored", 0)
                    adv = result.get("advisory") or {}
                    total_adv_passed  += adv.get("passed", 0)
                    total_adv_failed  += adv.get("failed", 0)
                    total_adv_errored += adv.get("errored", 0)
    elif path_plugin:
        run_list = [(path_plugin[0], path_plugin[1], args.skill)]
    else:
        target_plugins = {args.plugin: PLUGINS[args.plugin]} if args.plugin else PLUGINS
        run_list = [(pname, ppath, args.skill) for pname, ppath in target_plugins.items()]

    # Outside the branches above, not inside `else`: it was nested there before
    # --path existed, which left --path building run_list and nothing iterating
    # it -- the run reported "0 run" while get_skills found the skill fine.
    for pname, plugin_path, skill_filter in run_list:
        skills = get_skills(plugin_path, skill_filter)
        if not skills:
            continue

        print(f"\n── {pname} ──")

        for skill_dir in skills:
            result = run_skill_evals(
                client, skill_dir, pname,
                args.model, args.eval_id, args.verbose,
                fixture=args.fixture,
                use_real=args.real,
                masker=masker,
                judge_model=args.judge_model,
                plugin_path=plugin_path,
            )
            result["plugin"] = pname
            all_skill_results.append(result)

            if result.get("skipped"):
                total_skipped += 1
            else:
                total_passed  += result.get("passed", 0)
                total_failed  += result.get("failed", 0)
                total_errored += result.get("errored", 0)
                adv = result.get("advisory") or {}
                total_adv_passed  += adv.get("passed", 0)
                total_adv_failed  += adv.get("failed", 0)
                total_adv_errored += adv.get("errored", 0)

    # ── Summary ────────────────────────────────────────────────────────────
    total_run = total_passed + total_failed + total_errored
    print(f"\n{'─' * 60}")
    print(f"\nResults:  {total_run} run  "
          f"✓ {total_passed} passed  "
          f"✗ {total_failed} failed  "
          f"⚠ {total_errored} errored  "
          f"— {total_skipped} skipped (no evals)\n")
    if (total_adv_passed + total_adv_failed + total_adv_errored) > 0:
        gate = "  [--strict-workflow: GATING]" if args.strict_workflow else "  (non-gating)"
        print(f"Workflow evals{gate}:  ✓ {total_adv_passed} passed  "
              f"✗ {total_adv_failed} failed  ⚠ {total_adv_errored} errored\n")

    # Failed skill summary
    failed_skills = [
        r for r in all_skill_results
        if not r.get("skipped") and (r.get("failed", 0) + r.get("errored", 0)) > 0
    ]
    if failed_skills:
        print("Failed skills:")
        for r in failed_skills:
            print(f"  {r['plugin']}/{r['path']}  "
                  f"({r['failed']} failed, {r['errored']} errored)")
        print()

    # Save output
    if args.output:
        output = {
            "run_at":  datetime.now(timezone.utc).isoformat(),
            "model":   args.model,
            "summary": {
                "passed":  total_passed,
                "failed":  total_failed,
                "errored": total_errored,
                "skipped": total_skipped,
                "total":   total_run
            },
            "skills": all_skill_results
        }
        Path(args.output).write_text(json.dumps(output, indent=2), encoding="utf-8")
        print(f"Results saved to: {args.output}\n")

    gate_fail = (total_failed + total_errored) > 0
    adv_fail  = (total_adv_failed + total_adv_errored) > 0
    sys.exit(1 if (gate_fail or (args.strict_workflow and adv_fail)) else 0)


if __name__ == "__main__":
    main()
