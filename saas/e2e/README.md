# NoSale: seller browser acceptance (synthetic only)

Runs an actual headless Chrome against a disposable, self-contained Django
fixture, **not** E5 production, preview or any real marketplace account.

## Test coverage

1. Email-first entry, code confirmation and PIN setup; code is synthetic 0007.
2. First-run WB onboarding; rejects an invalid JWT without issuing a trial.
3. Accepts a synthetic basic token and runs the real scheduler/model import
   against two patched WB products; never sends discount changes.
4. Confirms first completed import opens the dashboard and grants Standard trial.
5. Checks settings, routine-check history, and no false important notification.
6. Verifies the 390px mobile dashboard has no horizontal overflow.
7. Logs out and back into the existing seller with their PIN.
8. A **second isolated seller** receives simulated HTTP 429 on first import:
   retry cooldown is displayed; no cross-seller products are shown.
9. Fails on unhandled browser JavaScript exceptions.

## Safety boundary

`fixture_server.py` refuses to run without `NOSALE_SYNTHETIC_BROWSER_E2E=1`,
debug/PIN auth, isolated SQLite, read-only WB, disabled finance and billing,
and locmem email backend. Django uses a temporary SQLite database under
`/tmp`. The only outgoing socket connection permitted is loopback.
`WBClient` is replaced in-process with a synthetic fixture. Browser requests
outside its local test origin are aborted.

The CI job `browser-e2e` executes the test on GitHub-hosted Ubuntu using
Chrome and Puppeteer-core, and publishes synthetic-only screenshots and
diagnostics. The existing `django-tests` and `postgres-tests` jobs remain
mandatory independently.

To execute on an authorized Linux test host: set the safety environment
variables exactly as in the CI job, install requirements and Puppeteer-core,
run `python -u e2e/fixture_server.py` in one terminal, and
`node e2e/seller_first_run.cjs` in another, with
`NOSALE_E2E_CHROME`, `NOSALE_E2E_URL=http://127.0.0.1:8941`,
and `NODE_PATH` configured.

Real email inbox delivery, WB API authorization, actual inventory and any
write effects require separate explicit owner approval and cannot be inferred
from this synthetic test.
