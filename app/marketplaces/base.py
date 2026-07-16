from dataclasses import dataclass


@dataclass(frozen=True)
class MarketplaceMeta:
    id: str
    name: str
    token_label: str
    token_help: str
    implemented: bool = True


class MarketplaceClient:
    """Common interface for marketplace price and discount clients."""

    def ping(self):
        raise NotImplementedError

    def get_products(self):
        raise NotImplementedError

    def update_discount(self, product_id, new_discount, price=None):
        raise NotImplementedError

