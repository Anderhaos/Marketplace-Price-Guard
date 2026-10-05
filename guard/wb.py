import base64
import binascii
import json
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


TOKEN_SCOPE_NAMES = (
    (1, "Контент"),
    (2, "Аналитика"),
    (3, "Цены и скидки"),
    (4, "Маркетплейс"),
    (5, "Статистика"),
    (6, "Продвижение"),
    (7, "Вопросы и отзывы"),
    (9, "Чат с покупателями"),
    (10, "Поставки"),
    (11, "Возвраты"),
    (12, "Документы"),
    (13, "Финансы"),
    (16, "Пользователи"),
)


def get_token_permissions(token):
    """Return non-secret WB JWT permissions suitable for displaying in the UI."""
    try:
        encoded_payload = token.split(".")[1]
        encoded_payload += "=" * (-len(encoded_payload) % 4)
        payload = json.loads(base64.urlsafe_b64decode(encoded_payload).decode("utf-8"))
        scopes = int(payload["s"])
    except (IndexError, KeyError, TypeError, ValueError, UnicodeDecodeError, binascii.Error, json.JSONDecodeError):
        return None

    return {
        "categories": [name for bit, name in TOKEN_SCOPE_NAMES if scopes & (1 << bit)],
        "access": "Только чтение" if scopes & (1 << 30) else "Чтение и запись",
    }


class WBApiError(Exception):
    def __init__(self, message, status=None, retry_after=None):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


class WBClient:
    BASE_URL = "https://discounts-prices-api.wildberries.ru"
    CONTENT_BASE_URL = "https://content-api.wildberries.ru"

    def __init__(self, token):
        if not token:
            raise ValueError("WB token is empty")
        self.token = token
        self._last_request = 0.0

    def _request(self, method, path, params=None, body=None):
        return self._request_to(self.BASE_URL, method, path, params, body)

    def _request_to(self, base_url, method, path, params=None, body=None):
        url = base_url + path
        if params:
            url += "?" + urlencode(params)
        payload = json.dumps(body).encode("utf-8") if body is not None else None
        for attempt in range(4):
            elapsed = time.monotonic() - self._last_request
            if elapsed < 0.7:
                time.sleep(0.7 - elapsed)
            request = Request(url, data=payload, method=method, headers={
                "Authorization": self.token, "Content-Type": "application/json",
            })
            self._last_request = time.monotonic()
            try:
                with urlopen(request, timeout=25) as response:
                    result = json.load(response)
            except HTTPError as error:
                message = error.read(1000).decode("utf-8", errors="replace")
                retry_after = error.headers.get("Retry-After")
                try:
                    retry_after = int(retry_after) if retry_after else None
                except ValueError:
                    retry_after = None
                if error.code == 429:
                    raise WBApiError("Wildberries temporarily limited requests", error.code, retry_after) from error
                if error.code in {500, 502, 503, 504} and attempt < 3:
                    time.sleep(min(2 ** attempt * 2, 12))
                    continue
                raise WBApiError(f"WB HTTP {error.code}: {message}", error.code, retry_after) from error
            except URLError as error:
                if attempt < 3:
                    time.sleep(min(2 ** attempt * 2, 12))
                    continue
                raise WBApiError(f"WB connection error: {error.reason}") from error
            if not isinstance(result, dict):
                raise WBApiError("WB returned an unexpected response")
            if result.get("error"):
                raise WBApiError(f"WB API error: {result.get('errorText', 'unknown')}")
            return result
        raise WBApiError("WB request retry limit reached")

    def get_product_cards(self):
        cards = []
        cursor = {}
        while True:
            result = self._request_to(self.CONTENT_BASE_URL, "POST", "/content/v2/get/cards/list", body={
                "settings": {"cursor": {"limit": 100, **cursor}, "filter": {"withPhoto": -1}},
            })
            if isinstance(result.get("cards"), list):
                page = result["cards"]
                next_cursor = result.get("cursor") or {}
            else:
                data = result.get("data", {})
                page = data.get("cards") if isinstance(data, dict) else None
                next_cursor = data.get("cursor") if isinstance(data, dict) else {}
            if not isinstance(page, list):
                raise WBApiError("WB product cards are missing")
            cards.extend(page)
            if not page or not next_cursor.get("nmID"):
                return cards
            cursor = {"updatedAt": next_cursor.get("updatedAt"), "nmID": next_cursor["nmID"]}

    def validate_token(self):
        self._request("GET", "/ping")
        self._request("GET", "/api/v2/list/goods/filter", {"limit": 1, "offset": 0})

    def get_products(self):
        products = []
        offset = 0
        while True:
            result = self._request("GET", "/api/v2/list/goods/filter", {"limit": 1000, "offset": offset})
            page = result.get("data", {}).get("listGoods")
            if not isinstance(page, list):
                raise WBApiError("WB product list is missing")
            if not page:
                return products
            products.extend(page)
            offset += 1000

    def submit_discounts(self, changes):
        if not 1 <= len(changes) <= 1000:
            raise ValueError("Expected 1 to 1000 discounts")
        result = self._request("POST", "/api/v2/upload/task", body={
            "data": [{"nmID": int(nm_id), "discount": int(discount)} for nm_id, discount in changes]
        })
        upload_id = result.get("data", {}).get("id")
        if not upload_id:
            raise WBApiError("WB did not return an upload ID")
        return int(upload_id)

    def get_upload_state(self, upload_id):
        try:
            result = self._request("GET", "/api/v2/history/tasks", {"uploadID": upload_id})
        except WBApiError as error:
            if error.status not in {400, 404}:
                raise
            result = self._request("GET", "/api/v2/buffer/tasks", {"uploadID": upload_id})
        data = result.get("data")
        if not isinstance(data, dict) or "status" not in data:
            raise WBApiError("WB upload status is missing")
        return int(data["status"])

    def get_upload_details(self, upload_id):
        details = []
        offset = 0
        while True:
            result = self._request("GET", "/api/v2/history/goods/task", {
                "uploadID": upload_id, "limit": 1000, "offset": offset,
            })
            page = result.get("data", {}).get("historyGoods")
            if not isinstance(page, list):
                raise WBApiError("WB upload details are missing")
            if not page:
                return details
            details.extend(page)
            offset += len(page)
