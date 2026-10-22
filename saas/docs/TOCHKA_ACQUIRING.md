# NoSale payment acquiring — MiniBook pattern (Tochka)

As of 2026-10-22 this is **implemented, test-covered, and disabled** until separately approved merchant credentials, tariff and fiscalization are provisioned for NoSale. It is not safe to reuse MiniBook's merchant credentials automatically.

## Design

Reference: `MiniBook/backend/src/modules/billing/`:

- `tochka.service.ts` / `billing.service.ts`: create unique payment-link operation and persist its returned bank URL and operation id
- `billing-webhooks.routes.ts` / `tochka-webhook.service.ts`: read a raw `text/plain` RS256 JWT, validate with the **Tochka public key**, then process the matched operation only
- `activatePaymentAndGrant`: idempotent payment-to-access ledger with serialized writes, never successful by browser redirect alone

NoSale implementation:

- `billing/tochka.py`: dedicated bank transport; no MiniBook secrets; no proxy, 12s timeout, trusted HTTPS provider payment host validation, signed JWT verification
- `billing/service.py`: purchase reuse within 30 minutes; pending/failed/paid/refunded; serial payment-to-grant atomic transactions; month starts at end of existing access or now; duplicate webhook never grants twice
- `billing/views.py`: `/billing/`, POST `/billing/checkout/`, GET `/billing/return/`, webhook `/billing/tochka/webhook/`
- `billing/models.py` and migration `0002_acquiring_payment_link`: durable order/link/status
- `billing/test_acquiring.py`: signed JWT using a generated RSA key, invalid signature, wrong merchant/operation/amount, no accidental activation, duplicate/replay, refund, trial + payment stacking

A successful return from the bank **never confirms payment**. Only an RS256 verified `acquiringInternetPayment` event with matched merchant, customer, `paymentLinkId`, bank `operationId`, amount and `APPROVED` state can grant subscription access. `AUTHORIZED` is preauthorization, **not** payment; `REFUNDED` revokes that payment's grant. Invalid incoming JWTs do not reveal details. Transient processing failures return 503 so bank can retry.

One payment purchases **one calendar month**. This is a one-time payment link, **not automatic recurring charges**. If a valid trial or paid access exists, the paid grant starts after that active grant expires; otherwise, immediately. All stores of one seller share access.

## Activation prerequisites — never invent values

Set via a private `.env` on E5 only, never Git:

```env
NOSALE_TOCHKA_ENABLED=0
NOSALE_MONTHLY_PRICE_RUB=0
NOSALE_FISCALIZATION_CONFIRMED=0
NOSALE_TOCHKA_JWT_TOKEN=
NOSALE_TOCHKA_CUSTOMER_CODE=
NOSALE_TOCHKA_MERCHANT_ID=
NOSALE_TOCHKA_WEBHOOK_JWK_JSON=
NOSALE_TOCHKA_API_BASE=https://enter.tochka.com/uapi
```

Activation checklist:
1. Confirm NoSale's legal selling entity / merchant is authorized to use the Tochka integration (not necessarily the MiniBook merchant).
2. Confirm internet acquiring and **fiscal receipts** are enabled in Tochka or external 54-FZ fiscalization. Checkout uses the non-receipt endpoint so the seller must provide fiscalization externally, or the integration must be upgraded to receipt API.
3. Approve the NoSale RUB monthly price; default 0 keeps payment disabled.
4. Place the separate merchant JWT, `customerCode` and `merchantId` in private environment. Permissions: `MakeAcquiringOperation`, `ReadAcquiringData`, `ReadCustomerData`; `ManageWebhookData` for setting up webhooks.
5. Provision and verify official Tochka **public RSA JWK** for webhook signature validation in `NOSALE_TOCHKA_WEBHOOK_JWK_JSON`; do not trust key locations from inbound JWTs.
6. Confirm Tochka will deliver `acquiringInternetPayment` webhooks at `https://nosale.svdev.pro/billing/tochka/webhook/` using HTTPS, `text/plain`. Check route availability without leaking information.
7. Run test/sandbox webhook signature and payment trials, then one legitimate small live charge/refund as Tochka documentation directs, and explicitly enable acquiring after verification.

Production never accepts `confirm-dev` endpoint, never activates a grant from the callback URL, never uses staging/mocked prices and never reuses legacy seller DB. All merchant inputs are server-side. The existing 5-day trial is unaffected.

## Current boundary

- Actual plan price: **not approved / not supplied**.
- Authorized merchant credentials and signed webhook public key for NoSale: **not provisioned**.
- Fiscalization: **not verified**.
- Therefore payment remains disabled on the user-facing `/billing/` page, with a clear explanation. Do not claim real customer payments work yet.

References:
- MiniBook `backend/src/modules/billing`
- https://developers.tochka.com/docs/tochka-api/internet-acquiring-integration
- https://developers.tochka.com/docs/tochka-api/opisanie-metodov/platyozhnye-ssylki
- https://developers.tochka.com/docs/tochka-api/opisanie-metodov/vebhuki
