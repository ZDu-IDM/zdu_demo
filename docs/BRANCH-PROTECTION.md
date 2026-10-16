# Branch Protection & Enforcement Setup

`CONTRIBUTING.md` describes the approval policy and CI runs checks on every PR. But policy
+ CI are only *advisory* until `main` is protected — nothing stops a merge on its own. This doc
explains the two mechanisms involved, how they layer together, and how to turn them on.

> **Prerequisite:** the repo must be in the **gatesfoundation** GitHub org (not a personal
> account) so `CODEOWNERS` can reference org teams. Create these teams first:
> `skills-working-group`, `idm-leads`. Each new team that ships a group under `plugins/groups/`
> needs its own `<team>-leads` team, added to `CODEOWNERS` (commented out until the team exists
> and has repo write access) by the PR that creates the group's directory — see
> [docs/GROUP-ONBOARDING.md](GROUP-ONBOARDING.md).
>
> If that line is forgotten, the group is not left unowned: `CODEOWNERS` carries a
> `/plugins/groups/` catch-all routing to `skills-working-group`, and the per-team lines below it
> override it (last match wins). A missing team line costs a broader reviewer, not no reviewer.

---

## Two mechanisms, two different jobs

It's easy to conflate these — they sound similar but each answers a different question, and
**neither blocks a merge by itself** except where noted.

| Mechanism | Answers | Lives in | Blocks merge on its own? |
|---|---|---|---|
| **CODEOWNERS** | *Who* should see a given path | `.github/CODEOWNERS` | ❌ No — just auto-requests a reviewer |
| **Merge restriction** | *Who* can merge into `main` | Branch protection setting | ✅ Yes — only repo admins |

**GitHub approvals are not the approval gate.** The business owner, the IT owner and
IT AI x InfoSec x Legal Review approve outside GitHub (see [CONTRIBUTING.md § Approval
process](../CONTRIBUTING.md#approval-process)), and not every approver has a GitHub account. The
required approval count is therefore **0**, and code-owner review is not required. CODEOWNERS
still routes PRs to the right people so they see them.

**The merge restriction is what enforces the approval process.** Only repo admins can merge, and an
admin merges only after the IT owner has submitted the attestation form.

### How they combine on this repo

```
PR opened / marked ready for review
  ├─ CODEOWNERS auto-requests reviewers for visibility
  ├─ AI-AskOps triage → business owner + IT owner + IT AI x InfoSec x Legal Review approve (outside GitHub)
  ├─ IT owner submits the attestation form
  └─ Repo admin merges once ALL required checks are green
```

> ⚠️ **What GitHub can't check:** whether the attestation exists. That check is the repo admin's
> job, and it's why merge rights are limited to admins.

---

## Set it up (GitHub UI)

Go to **Settings → Branches → Add branch protection rule** (or **Rules → Rulesets** on newer
repos) for `main`:

1. **Require a pull request before merging** — turn on. This blocks direct pushes to `main`.
2. **Require approvals** — set to `0`. Approval happens outside GitHub.
3. **Require review from Code Owners** — leave off.
4. **Require status checks to pass before merging** — turn on, then add these checks (type the
   exact job `name:` from each workflow into the search box):
   - `Validate skill structure and metadata`
   - `Run evals for changed skills`
   - `Scan skills for security red flags`
   - `Lint Python`
   - `YARA rule tests`
   - Tick **"Require branches to be up to date before merging"** (this is the `strict` option).

   These five are the checks README lists as **blocking**. `Static analysis (Semgrep)` is the one
   PR check deliberately left out: it runs report-only while it is calibrated, so requiring it as a
   status check would add a gate that cannot fail. Promote it here and in README's table together,
   so the two lists cannot drift apart.

   > **Separately, Semgrep may be enforced through a code-scanning rule** rather than as a status
   > check — a ruleset can require results from a named tool. That mechanism only works while code
   > scanning is enabled on the repo: with it disabled, `upload-sarif` fails, no results ever
   > arrive, and the rule blocks every PR while waiting for them. If code scanning is off, drop the
   > code-scanning rule rather than leaving a requirement nothing can satisfy.
5. **Restrict who can push to matching branches** — turn on, with no users or teams added. That
   leaves repo admins as the only people who can merge. (In a ruleset, use **Restrict updates**
   with repo admins on the bypass list.)
6. Save.

> A status check only appears in step 4's search box after it has run at least once on this repo.
> If one is missing, open a PR that triggers it first, then come back and add it.

### Or via the `gh` CLI

Same settings, scripted. Replace `<ORG>` with `gatesfoundation`:

```bash
gh api -X PUT repos/<ORG>/gf-agent-publish/branches/main/protection --input - <<'JSON'
{
  "required_status_checks": {
    "strict": true,
    "contexts": [
      "Validate skill structure and metadata",
      "Run evals for changed skills",
      "Scan skills for security red flags",
      "Lint Python",
      "YARA rule tests"
    ]
  },
  "enforce_admins": false,
  "required_pull_request_reviews": {
    "require_code_owner_reviews": false,
    "required_approving_review_count": 0
  },
  "restrictions": { "users": [], "teams": [], "apps": [] }
}
JSON
```

(The `contexts` strings must match the **job `name:` fields** in the workflows exactly. Empty
`restrictions` lists leave only repo admins able to merge. That only works for org-owned repos.)

---

## What this guarantees (and what it doesn't)

- **Guaranteed:** nothing reaches `main` without a PR, and only a repo admin can merge one.
  Non-admins cannot merge with failing checks.
- **Not guaranteed by GitHub:** that the business owner, IT owner and IT AI x InfoSec x Legal Review actually
  approved. That evidence is the IT owner's attestation, and the repo admin checks for it before
  merging.
- **Not guaranteed either:** green checks on an admin merge. With `enforce_admins: false`, an
  admin *can* bypass a failing check, so merging only on green is admin discipline.

---

## Verify

Open a test PR that edits a skill and confirm:
- A non-admin with write access cannot merge it, even with green checks.
- GitHub shows the PR as blocked while a required check is failing or pending.
- Direct `git push origin main` is rejected.

See [docs/TESTING.md](TESTING.md) for a fuller test plan (including how to exercise this without
a full set of org teams).
