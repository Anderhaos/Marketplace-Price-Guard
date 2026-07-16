# Marketplace Adapter Roadmap

The project is now built as a universal marketplace price guard.

## Implemented

### Wildberries

- Read goods, prices, discounted prices, and seller discount.
- Check whether seller discount is higher than allowed.
- Send discount reset task in `LIVE` mode.

## Next adapters

### Ozon

Needed:

- Study seller API methods for product prices and discounts.
- Add credentials format, probably `Client-Id` + `Api-Key`.
- Create `app/marketplaces/ozon.py`.
- Normalize Ozon products to the common product shape.
- Implement discount or price update method after testing with a seller account.

### Yandex Market

Needed:

- Study campaign/business API access.
- Add token/account settings.
- Create `app/marketplaces/yandex_market.py`.
- Normalize products to the common product shape.
- Implement safe update method after testing with a seller account.

## Common product shape

Every adapter should return products like this:

```python
{
    "name": "seller article",
    "nm_id": "marketplace product id",
    "price": 1000,
    "discounted_price": 900,
    "current_discount": 10,
    "allowed_discount": 0,
    "currency": "RUB",
    "source": "MARKETPLACE_API",
}
```

The site and rules should not depend on one marketplace directly.
