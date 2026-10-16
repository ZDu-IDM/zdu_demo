#!/usr/bin/env python3
"""
preview_askops_request.py — the AskOps email a PR would send, without sending it.

Runs pr-brief.yml's AskOps job locally for one PR: the PR's base tree, changed
files and head copies from GitHub, then askops_gate.py and, if a request is
due, askops_request.py. Uses this checkout's scripts. Nothing is posted.

Usage:
  # the email, without a brief ("What the change does" reads "not analysed")
  python scripts/preview_askops_request.py 73

  # generate the brief first, as the brief job would (2 API calls; needs
  # ANTHROPIC_API_KEY and anthropic<1)
  python scripts/preview_askops_request.py 73 --brief

  # use a brief card you already have
  python scripts/preview_askops_request.py 73 --card path/to/brief-card.json

Writes to askops-preview/<PR>/: email.txt, askops-request.json (the payload),
askops-reasons.txt, and the inputs the job reads.
"""
import argparse
import base64
import io
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tarfile

SCRIPTS = pathlib.Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS.parent
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def gh(*args, ok_fail=False) -> str:
    # Explicit UTF-8: the Windows locale codec cannot decode GitHub's text.
    r = subprocess.run(["gh", "api", *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        if ok_fail:
            return ""
        sys.exit(f"gh api {args[0]} failed:\n{r.stderr.strip()}")
    return r.stdout


def gh_lines(*args) -> list:
    return [ln for ln in gh(*args).splitlines() if ln.strip()]


def head_copy(repo, path, sha, dest: pathlib.Path) -> bool:
    """Fetch a file at the PR head as data; False if it is not there (e.g. deleted)."""
    content = gh(f"repos/{repo}/contents/{path}?ref={sha}", "--jq", ".content", ok_fail=True)
    if not content.strip():
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(base64.b64decode("".join(content.split())))
    return True


def base_tree(sha, dest: pathlib.Path):
    """The base commit's files, as the workflow's checkout would have them."""
    if subprocess.run(["git", "-C", str(REPO_ROOT), "cat-file", "-e", f"{sha}^{{commit}}"],
                      capture_output=True).returncode != 0:
        subprocess.run(["git", "-C", str(REPO_ROOT), "fetch", "-q", "origin", sha], check=True)
    tar = subprocess.run(["git", "-C", str(REPO_ROOT), "archive", sha],
                         capture_output=True, check=True).stdout
    with tarfile.open(fileobj=io.BytesIO(tar)) as t:
        t.extractall(dest, filter="data")


def write(path: pathlib.Path, lines):
    path.write_text("".join(f"{ln}\n" for ln in lines), encoding="utf-8", newline="\n")


def run(script, *args, cwd, env=None):
    return subprocess.run([sys.executable, str(SCRIPTS / script), *args], cwd=cwd,
                          capture_output=True, text=True, encoding="utf-8", errors="replace",
                          env={**os.environ, "PYTHONIOENCODING": "utf-8", **(env or {})})


def make_brief(repo, pr, out: pathlib.Path):
    """The brief job: the diff and title, then pr_brief.py without --post."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("--brief needs ANTHROPIC_API_KEY set")
    diff = out / "pr.diff"
    diff.write_text(gh(f"repos/{repo}/pulls/{pr}", "-H", "Accept: application/vnd.github.v3.diff"),
                    encoding="utf-8", newline="\n")
    title = gh(f"repos/{repo}/pulls/{pr}", "--jq", ".title").strip()
    card = out / "brief-card.json"
    r = run("pr_brief.py", "--diff", str(diff), "--pr-number", str(pr), "--pr-title", title,
            "--pr-url", f"https://github.com/{repo}/pull/{pr}", "--output", str(card), cwd=out)
    if r.returncode != 0:
        print("brief failed - continuing without one, as the AskOps job would:\n"
              + r.stderr.strip().splitlines()[-1])
        return None
    return card


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pr", type=int, help="PR number")
    ap.add_argument("--repo", default="", help="owner/name (default: current repo)")
    ap.add_argument("--brief", action="store_true",
                    help="generate the brief first (2 API calls; needs ANTHROPIC_API_KEY)")
    ap.add_argument("--card", default="", help="a brief-card.json to use instead")
    ap.add_argument("--mode", choices=["open", "push"], default="open",
                    help="push: rules only, as on a later push")
    ap.add_argument("--out-dir", default="askops-preview",
                    help="where to write (a <PR> folder is made inside)")
    args = ap.parse_args()

    repo = args.repo or json.loads(gh("repos/{owner}/{repo}"))["full_name"]
    out = pathlib.Path(args.out_dir).resolve() / str(args.pr)
    if out.exists():
        shutil.rmtree(out)
    base = out / "base"
    base.mkdir(parents=True)

    pull = json.loads(gh(f"repos/{repo}/pulls/{args.pr}"))
    base_tree(pull["base"]["sha"], base)

    # What the workflow's fetch step writes.
    files = f"repos/{repo}/pulls/{args.pr}/files"
    changed = gh_lines(files, "--paginate", "--jq", ".[].filename")
    write(base / "changed_files.txt", changed)
    write(base / "removed_files.txt",
          gh_lines(files, "--paginate", "--jq", '.[] | select(.status == "removed") | .filename'))
    (base / "pr_body.md").write_text(pull.get("body") or "", encoding="utf-8")
    head_sha = pull["head"]["sha"]
    if ".claude-plugin/marketplace.json" in changed:
        head_copy(repo, ".claude-plugin/marketplace.json", head_sha, base / "marketplace_head.json")
    for f in changed:
        if f.endswith("/SKILL.md") or f.endswith("/.claude-plugin/plugin.json"):
            head_copy(repo, f, head_sha, base / "head" / f)
    (base / "head").mkdir(exist_ok=True)

    card = make_brief(repo, args.pr, out) if args.brief else (
        pathlib.Path(args.card).resolve() if args.card else None)
    if card:
        shutil.copy(card, base / "brief-card.json")

    print(f"PR #{args.pr}: {pull['title']}")
    print(f"  {len(changed)} changed file(s); brief: {card or 'none'}; mode: {args.mode}")
    if not any(f.startswith("plugins/") or f == ".claude-plugin/marketplace.json"
               for f in changed):
        print("  The workflow would NOT run: nothing under plugins/** or marketplace.json.")
        print("\nNo email would be sent.")
        return 0

    output = base / "gh_output.txt"
    output.write_text("", encoding="utf-8")
    gate = run("askops_gate.py", "--changed-files", "changed_files.txt",
               "--removed-files", "removed_files.txt", "--head-root", "head",
               "--marketplace-head", "marketplace_head.json", "--brief", "brief-card.json",
               "--mode", args.mode, "--already-sent", "0", "--reasons-out", "askops-reasons.txt",
               cwd=base, env={"GITHUB_OUTPUT": str(output)})
    print("  " + (gate.stdout.strip() or gate.stderr.strip()))
    if "send=true" not in output.read_text(encoding="utf-8"):
        print("\nNo email would be sent.")
        return 0

    req = run("askops_request.py", "--pr-body", "pr_body.md", "--pr-number", str(args.pr),
              "--pr-url", pull["html_url"], "--pr-author", pull["user"]["login"],
              "--changed-files", "changed_files.txt", "--removed-files", "removed_files.txt",
              "--head-root", "head", "--marketplace-head", "marketplace_head.json",
              "--brief", "brief-card.json", "--reasons", "askops-reasons.txt",
              "--output", str(out / "askops-request.json"), "--plain", cwd=base)
    if req.returncode != 0:
        sys.exit(f"askops_request.py failed:\n{req.stderr}")
    (out / "email.txt").write_text(req.stdout, encoding="utf-8", newline="\n")
    shutil.copy(base / "askops-reasons.txt", out / "askops-reasons.txt")
    print("\n" + req.stdout.rstrip())
    print(f"\nWritten to {out}/ (email.txt, askops-request.json). Nothing was sent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
