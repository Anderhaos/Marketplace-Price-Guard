# WB Finance read-only v1 — NoSale

- Separate **service-category Finance token** from the seller's price/discount token. No personal/Solo token in SaaS.
- Local JWT claims check token type/categories, but does not prove signature, seller identity, or live access. WB validates actual token only when the operator enables the separate Finance gate and the seller initiates the manual read.
- Production defaults `NOSALE_WB_FINANCE_ENABLED=0`, independent of `SAAS_WB_NETWORK_ENABLED=0`; live WB financial calls cannot occur unless **both** are enabled by deployment owner.
- The only implemented method is `POST https://finance-api.wildberries.ru/api/finance/v1/sales-reports/list`, body includes an RFC3339 date range (<=31 days), `weekly`, `limit=50`, `offset=0`. The operation is read-only despite POST. No redirects, proxies, arbitrary hosts, retry loops, price writes, or recipient-selected endpoints.
- Server-side 75s cooldown across a NoSale user's stores, persisted across token rotation/revocation, DB locking, 120s lock, and protection against publishing results under revoked or rotated credentials. WB actually applies limits per seller identity; seller identity across *different* NoSale users is not verified, so the deployment stays disabled until authorized seller testing and limits are validated. One page per manual refresh; 50 rows means `partial`, never complete history.
- Store **only** `report_id`, date range, ISO 4217 currency, raw report-supplied `forPaySum` / `retailAmountSum` as decimal strings, fixed source and `funding=unknown`, `profit_verified=false`; never raw API responses, buyer details, seller finance name, commissions, tax identifiers or body data.
- These aggregates are **not** proof of WB-funded discounts, per-SKU profitability or net merchant margin. Connecting WB Finance neither turns on AI nor revises pricing safeguards. Separate per-SKU detail reconciliation and direct seller-bank validation are out of scope until owner provides an authorized Service token and a redacted official report specimen.
- Owner consent actions: opt-in token connect, manual refresh, revoke & purge even when Finance is disabled. A non-secret per-user cooldown timestamp survives revocation solely for rate-limit protection. No automatic Finance polling.

Official references:
- https://dev.wildberries.ru/docs/openapi/documents-and-accounting
- https://dev.wildberries.ru/knowledge-base/articles/019d50e0-4e66-71d7-a147-be50f8dda5e9/pagination-and-working-with-large-datasets-in-wb-api

## R5.2 — bounded report details (experimental, disabled in public deployment)

- After a *seller-consented* manual list refresh, the seller may choose an ID strictly from **their own stored report list**. It cannot submit arbitrary report IDs. This is the first page only, 30 records maximum, single HTTP request; further pages are **not** fetched or represented as complete.
- Official read-only `POST /api/finance/v1/sales-reports/detailed/{reportId}`; fields whitelist: `reportId, rrdId, nmId, currency, saleDt, retailAmount, forPay, sellerPromoId, sellerPromoDiscount`. Never request/store `srid`, buyer identity, order identifiers, seller names, barcodes, tax details or raw body.
- Store only sanitized first-page evidence for one report per shop, reset it on token rotation/list refresh, purge on revoke. Gate shared with list queries (durable 75-second cooldown); no retries, batch jobs or pricing writes. Disable with `NOSALE_WB_FINANCE_ENABLED=0` / global WB network switch.
- `sellerPromoId` and `sellerPromoDiscount` indicate that a seller promotion exists in a report row; these DO NOT establish financing of *every* price discount or profitability. Per-product margin requires seller cost basis and verified finance rows, returns, delivery, commissions and final bank reconciliation. All output retains `funding=unknown`, `profit_verified=false`.
- **No live test / deployment enablement** until approved seller account scopes and per-seller 1/min shared budget are verified. Synthetic CI checks only.

Reference: https://dev.wildberries.ru/docs/openapi/financial-reports-and-accounting#tag/financialReports
