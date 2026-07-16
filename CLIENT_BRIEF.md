# Client Brief

## What the client wants

The seller needs a web tool that monitors marketplace discounts through official APIs and removes unsafe seller discounts automatically.

Current working integration:

- Wildberries prices and discounts API.

Prepared future integrations:

- Ozon.
- Yandex Market.

## Questions before production use

1. Which marketplace should be connected first?
2. How many products should be monitored?
3. Should the tool only reset seller discount to `0%`, or should every product have its own allowed discount?
4. What interval is safe for checks: 5, 10, 30, or 60 minutes?
5. What is the maximum number of automatic fixes per cycle?
6. Should email notifications be enabled?
7. Will the tool run locally on the seller's PC or 24/7 on a server?

## Safe first delivery

- Local web interface.
- Marketplace selector.
- API token entered on the site.
- Wildberries adapter working.
- Ozon and Yandex Market prepared as adapter slots.
- Manual fix button.
- Automatic check cycle.
- Date-based action history.
- `READ_ONLY` mode for safe testing.
- `LIVE` mode for real API changes.
