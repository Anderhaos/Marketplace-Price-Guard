# NoSale SaaS — binding implementation and release rules

## Canonical repository, branch and source of truth
- Canonical repository: `Anderhaos/Marketplace-Price-Guard`. **`main` is the one canonical development and deployment branch**; legacy `master` is a historical reference only and must not receive new SaaS development. Do not branch from stale `master` or another unfinished `feature/*`.
- **One task → one branch → one PR → CI/review → merge into `main` → deploy from `main`.** Normally create `feature/<issue>-<scope>` from the latest `origin/main`, keep the branch scoped to a single GitHub issue, and merge immediately after acceptance. Start the next task again from updated `main`.
- Because the owner is currently the sole developer, **small, self-contained changes may be committed directly to `main` when the owner authorizes this**. Such changes still require CI checks and immutable SHA deployment. Prefer a focused PR when reviewing risky code, auth, money, encryption, migrations, or WB access.
- **Never build chained / stacked feature branches or leave completed work in a long-lived Draft PR chain.** A PR must target `main`; a Draft PR is only for work still underway. Mark it ready and merge when the code/tests/contract are accepted. Close superseded duplicate PRs rather than merging competing implementations.
- GitHub's default branch must be `main`. If an integration cannot change that repository setting, flag it explicitly instead of quietly returning to `master`.
- **All SaaS source code, migrations, documentation and configuration are authored in GitHub through the GitHub Connector**, not by editing code on E5. Record task closure and acceptance in the linked GitHub Issue/PR. Server inspection is read-only; deploy only the exact reviewed GitHub SHA from `main`.
- Before each deployment verify: the SHA is reachable from `origin/main`; required GitHub CI checks are successful; tests and migration safety have been reviewed; a distinct PostgreSQL staging backup/rollback is available. **Do not deploy feature-branch SHA directly.** Roll back to the previous known-good immutable `main` SHA if needed.
- After deploying staging: record `main` SHA, staging SHA, CI, migration and database test status, and HTTP health of both new SaaS and legacy. **`main`, GitHub and staging must converge at the same tested SHA.** The history of previously tested commits remains in Git, not in abandoned feature branches.

## Environments — strict separation
- Legacy live seller: `https://mpnosale.svdev.pro/`, `/srv/mpnosale`, SQLite, `127.0.0.1:8891`. **Never restart, migrate, alter credentials, or change its live seller data** while delivering SaaS work.
- Isolated SaaS staging: `https://nosale.svdev.pro/`, `/srv/mpnosale-saas/source/saas`, PostgreSQL, `127.0.0.1:8892`, distinct Compose/secrets.
- On SaaS staging keep `SAAS_WB_NETWORK_ENABLED=0`, AI disabled until a verified, budgeted NoSale project credential, and merchant acquiring disabled until independently approved provider, seller terms and 54-ФЗ fiscalization. **Code merge and staging deployment do not authorize paid production launch.**
- Enforcement of seller/account isolation, entitlements, billing and marketplace write gates belongs on the server. Do not rely on hidden UI buttons.

## Reuse rather than duplicate
- Reuse existing WB adapter patterns without copying real production tokens or data.
- Follow checked BeClients patterns for verified Tochka settlement, idempotency, grants and receipts; never copy its merchant secret into NoSale.
- Follow existing MiniBook AI Platform owner connection and catalog patterns; NoSale has its own encrypted project service token, project budgets and scoped allowed models. No standalone Telegram bot is needed in NoSale MVP.
- Existing UI Kit/design, portal and deterministic WB guard remain the source of truth; avoid parallel replacement implementations.

## Acceptance gates for each task
- GitHub Actions Django validation, migration drift, unit and isolation tests, and Docker smoke pass for the actual SHA.
- Any schema, subscription, auth or multi-store change gets relevant **isolated PostgreSQL integration tests**, a verified backup, and a tested rollback path before deployment.
- Verify `nosale.svdev.pro/health/` and `mpnosale.svdev.pro/health/` after each deployment. Do not infer application acceptance from HTTP 200 alone.
- A paid/public release needs complete email/PIN onboarding, genuine owner authorization, WB commercial API permission, independently budgeted AI Platform enrollment, verified bank checkout/refunds and fiscal receipts, privacy/legal acceptance and end-to-end browser smoke.
- Incomplete external integrations stay **OFF by default** and clearly marked open in the release tracker; they must not block merging otherwise finished, guarded code into `main`.

## Consolidation record
- 2026-10-24: owner directed the end of stacked feature branches, adoption of `main`, one-task/one-branch/merge, and staging-only deployments from `main`. The 16 accepted PRs #1, #3–#5, #13–#17, #19–#25 were merged in order to `main`. Superseded PR #18 was intentionally not merged.
