from fake_wb_api import FakeWildberriesApi
from real_wb_api import RealWildberriesApi


class WildberriesClient:
    def __init__(self, token=None, products_path=None, demo=False):
        self.demo = demo
        self.client = FakeWildberriesApi(products_path) if demo else RealWildberriesApi(token)

    def ping(self):
        if self.demo:
            return {"status": "demo"}
        return self.client.ping()

    def get_products(self):
        return self.client.get_products()

    def update_discount(self, product_id, new_discount, price=None):
        return self.client.update_discount(product_id, new_discount, price)

