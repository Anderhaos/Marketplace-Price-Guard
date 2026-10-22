# MPNO Sale SaaS — implementation rules

## Source of truth and development workflow
- All SaaS code, tests, migrations, documentation and configuration changes must be authored through the **GitHub Connector directly in GitHub**. The canonical repository is `Anderhaos/Marketplace-Price-Guard`.
- Work on a feature branch / draft PR. Do not merge to `master` or change legacy production without a separate acceptance.
- Run and review GitHub Actions before deployment. Do not declare a change ready when checks are red or unavailable.
- **Desktop Commander is not a code editor or implementation transport.** Use it only for exceptional read-only local/server inspection or for E5 deployment/verification of a GitHub commit. Never implement code in a local worktree or patch source files on E5.
- Deploy an immutable, tested GitHub commit through the established E5 flow. No hotfixes on the server.

## Environments
- Legacy installation: `https://mpnosale.svdev.pro/`, `/srv/mpnosale`, SQLite, `127.0.0.1:8891`. This installation protects a real seller and must not be restarted, migrated or edited as part of SaaS development.
- SaaS staging: `/srv/mpnosale-saas/source/saas`, own PostgreSQL, `127.0.0.1:8892`, distinct Docker Compose project and encryption secrets.
- Staging `SAAS_WB_NETWORK_ENABLED=0`; never use real seller tokens or call live WB write endpoints during testing.
- Seller isolation and subscription authorization must be enforced in views, jobs and marketplace operations, not merely by hiding controls.

## Reuse before reimplementation
- Reuse the proven existing WB adapter from legacy without copying production secrets or seller data.
- Study BeClients' working Tochka billing, signature verification, webhook idempotency, access grants, reconciliation and receipt workflow before implementing payments. Adapt explicitly for Django and add tests.
- Evaluate other existing svdev projects before creating new infrastructure or libraries. Keep externally facing SaaS independent from legacy.

## Release gates
- Migrations and unit/integration tests pass on GitHub Actions.
- Isolated PostgreSQL staging smoke and rollback path verified.
- Confirm legacy `/health/` remains healthy.
- Keep this PR in draft until onboarding, commercial marketplace access, verified payments and notifications are production-ready.
