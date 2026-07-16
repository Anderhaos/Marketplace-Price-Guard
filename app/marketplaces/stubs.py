class MarketplaceNotImplementedClient:
    def __init__(self, marketplace_name):
        self.marketplace_name = marketplace_name

    def ping(self):
        raise NotImplementedError(
            f"{self.marketplace_name} пока не подключен. "
            "Архитектура проекта готова, но нужен отдельный API-адаптер."
        )

    def get_products(self):
        raise NotImplementedError(
            f"{self.marketplace_name} пока не подключен. "
            "Сейчас рабочий адаптер есть для Wildberries."
        )

    def update_discount(self, product_id, new_discount, price=None):
        raise NotImplementedError(
            f"{self.marketplace_name} пока не подключен. "
            "Реальные изменения для этого маркетплейса еще не реализованы."
        )

