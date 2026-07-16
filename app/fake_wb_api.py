import json
from pathlib import Path


class FakeWildberriesApi:
    """Safe demo replacement for WB API."""

    def __init__(self, products_path):
        self.products_path = Path(products_path)
        self.products = self._load_products()

    def _load_products(self):
        with self.products_path.open("r", encoding="utf-8") as file:
            return json.load(file)

    def get_products(self):
        return self.products

    def update_discount(self, nm_id, new_discount, price=None):
        for product in self.products:
            if str(product["nm_id"]) == str(nm_id):
                product["current_discount"] = int(new_discount)
                if price is not None:
                    product["price"] = int(float(price))
                product["discounted_price"] = self._discounted_price(product)
                self.save()
                return {"status": "demo", "nmID": product["nm_id"], "discount": int(new_discount)}

        raise ValueError(f"Product with nm_id={nm_id} not found")

    def _discounted_price(self, product):
        price = float(product.get("price", 0) or 0)
        discount = float(product.get("current_discount", 0) or 0)
        return round(price * (100 - discount) / 100, 2)

    def save(self):
        with self.products_path.open("w", encoding="utf-8") as file:
            json.dump(self.products, file, ensure_ascii=False, indent=2)
