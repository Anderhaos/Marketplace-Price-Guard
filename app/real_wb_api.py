import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class RealWildberriesApi:
    """Клиент WB API для категории "Цены и скидки"."""

    BASE_URL = "https://discounts-prices-api.wildberries.ru"

    def __init__(self, token):
        if not token or token == "PASTE_YOUR_WB_TOKEN_HERE":
            raise ValueError("WB API токен не передан. Вставь токен на странице настроек.")

        self.token = token

    def _request(self, method, path, params=None, body=None):
        url = f"{self.BASE_URL}{path}"

        if params:
            url = f"{url}?{urlencode(params)}"

        data = None
        headers = {
            "Authorization": self.token,
            "Content-Type": "application/json",
        }

        if body is not None:
            data = json.dumps(body).encode("utf-8")

        request = Request(url, data=data, headers=headers, method=method)

        try:
            with urlopen(request, timeout=20) as response:
                raw = response.read().decode("utf-8")
                if not raw:
                    return {"status": response.status}
                return json.loads(raw)
        except HTTPError as error:
            message = error.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"WB API вернул ошибку {error.code}: {message}") from error
        except URLError as error:
            raise RuntimeError(f"Не удалось подключиться к WB API: {error.reason}") from error

    def ping(self):
        return self._request("GET", "/ping")

    def get_products(self, limit=100, offset=0):
        response = self._request(
            "GET",
            "/api/v2/list/goods/filter",
            params={"limit": limit, "offset": offset},
        )
        goods = response.get("data", {}).get("listGoods", [])
        return [self._normalize_product(item) for item in goods]

    def _normalize_product(self, item):
        sizes = item.get("sizes") or []
        first_size = sizes[0] if sizes else {}

        return {
            "name": item.get("vendorCode") or f"Товар {item.get('nmID')}",
            "nm_id": item.get("nmID"),
            "price": first_size.get("price", 0),
            "discounted_price": first_size.get("discountedPrice", 0),
            "club_discounted_price": first_size.get("clubDiscountedPrice", 0),
            "current_discount": item.get("discount", 0),
            "club_discount": item.get("clubDiscount", 0),
            "allowed_discount": 0,
            "currency": item.get("currencyIsoCode4217", "RUB"),
            "source": "WB_API",
        }

    def update_discount(self, nm_id, new_discount, price=None):
        payload_item = {
            "nmID": int(nm_id),
            "discount": int(new_discount),
        }
        if price is not None:
            payload_item["price"] = int(float(price))

        return self._request(
            "POST",
            "/api/v2/upload/task",
            body={"data": [payload_item]},
        )
