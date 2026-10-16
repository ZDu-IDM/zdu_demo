# Testing the Governed PR Flow

How to verify the review/enforcement pipeline (CI + CODEOWNERS + branch protection) **without**
the real `@gatesfoundation/*` teams — useful while the repo is still on a personal account.

Key facts this plan works around:
- CODEOWNERS accepts **individual users** (`@username`), not just teams. A code owner must have
  **Write** access to be auto-requested.
- You **cannot approve your own PR** — testing the approval step needs a second identity.
- **Free GitHub orgs support unlimited teams**, so a throwaway org can mirror production exactly.

---

## Level 1 — CI only (no teams, no protection)

Verifies the automated half of the gate. Nothing to set up.

1. Open a PR that edits a skill under `plugins/foundation-wide/**/skills/`,
   `plugins/groups/**/skills/`, or `plugins/shared-workflows/**/skills/`.
2. Confirm all five blocking checks run: **Validate skill structure and metadata**,
   **Run evals for changed skills**, **Scan skills for security red flags**, **Lint Python**,
   and **YARA rule tests**. (**Static analysis (Semgrep)** also runs but is advisory.)
3. Push a deliberately broken skill (bad metadata) and confirm `validate` **fails**.

✅ Pass = CI runs on skill PRs and fails on bad input.

### Previewing the Teams cards

A PR only ever renders the one governance card matching its actual state, so
checking all six would need six differently-shaped PRs. Instead:

```bash
# all six to disk, nothing posted (no gh, no network)
python scripts/preview_governance_cards.py --synthetic

# from a real PR's data
python scripts/preview_governance_cards.py 41

# send them to a TEST channel
python scripts/preview_governance_cards.py 41 --post --webhook https://...
```

Each `<stage>.card.json` is a bare Adaptive Card, so it can be pasted straight
into [adaptivecards.io/designer](https://adaptivecards.io/designer/). Use
`--post` only against a test channel — it sends one message per stage.

`scripts/preview_infosec_cards.py` does the same for the cards posted on merge.

> **PowerShell:** use `--out-dir`, not `>`. Redirection writes a UTF-8 BOM that
> `json.loads` rejects, and reading a card back needs
> `Get-Content -Raw -Encoding utf8` or the emoji come out as `ðŸ”µ`.

---

## Level 2 — CODEOWNERS routing (individual user)

Verifies path → owner mapping. Use a **scratch branch** — do NOT commit test usernames to `main`.

1. On a test branch, edit `.github/CODEOWNERS` to point a path at yourself:
   ```
   /plugins/foundation-wide/    @shchen-idmod
   /plugins/groups/idm/         @shchen-idmod
   ```
2. Open a PR editing a file under that path.
3. Confirm GitHub shows *"review requested from @shchen-idmod."*

✅ Pass = the correct owner is auto-requested for the changed path.

---

## Level 3 — Branch-protection block (admin bypass)

Verifies the merge gate blocks unreviewed / failing PRs. Needs no second person.

1. Apply branch protection (see [BRANCH-PROTECTION.md](BRANCH-PROTECTION.md)) with
   `enforce_admins: false`.
2. Open a PR; confirm it is **not mergeable** without approval + passing checks.
3. As admin, merge anyway (admin bypass) to confirm the escape hatch works.

✅ Pass = merge is blocked for a normal user; admin can override.

---

## Level 4 — Full end-to-end (throwaway test org)

The only way to exercise the real CODEOWNERS-team → required-review → merge path.

1. Create a **free GitHub org** (e.g. `sharon-skills-test`) and push a copy of this repo into it.
2. Create the real team names and give them Write access:
   ```bash
   ORG=sharon-skills-test
   for T in skills-working-group idm-leads; do
     gh api -X POST orgs/$ORG/teams -f name="$T" -f privacy=closed
     gh api -X PUT orgs/$ORG/teams/$T/repos/$ORG/gf-agent-publish -f permission=push
   done
   ```
3. Add a **second GitHub account** (your approver) to a team:
   ```bash
   gh api -X PUT orgs/$ORG/teams/skills-working-group/memberships/<second-account>
   ```
4. Keep CODEOWNERS pointing at `@sharon-skills-test/...` teams; apply branch protection.
5. Open a PR from a third identity (or the author account), have the **second account approve**,
   and confirm merge unlocks only after approval + green checks.

✅ Pass = a PR merges only with a real code-owner-team approval and passing CI.

> When the repo moves to `gatesfoundation`, only the org/team handles change — the flow is identical.

---

## Recommended sequence

- **Now (personal repo):** Level 1 + Level 3 — proves CI runs and the block works.
- **Before rollout:** Level 4 in a test org — proves the full human-approval path end to end.
