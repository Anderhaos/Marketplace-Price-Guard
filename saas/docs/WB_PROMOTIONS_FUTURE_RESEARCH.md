# Wildberries promotions and seller proceeds — research backlog

Source: audited local legacy checkout `D:\Marketplace Price Guard\MARKETPLACE_ROADMAP.md` (unsaved notes, preserved 2026-10-23).

This is research, **not** a feature implemented in NoSale and not a release blocker for the current discount guard.

- Review current WB Promotions Calendar API: list campaigns; distinguish voluntary and automatic promotions; determine what supported endpoints can control participation.
- Separate seller-funded discounts, WB-funded buyer discounts, WB Club discounts and promotional participation.
- Display seller price, visible buyer price, estimated proceeds and commissions only when available from supported source fields. Do not equate a buyer-side discount to reduced seller proceeds without evidence.
- Optional alerts for promotions and payout changes; any automatic actions must be off by default.
- Verify WB API rights with an explicitly authorized test seller before implementing promotion withdrawal or exclusion.
- Require audit trail, WB status confirmation, rate-limit handling and tests for concurrent price changes.
