from config import APP_MODE, PRODUCTS_PATH
from .base import MarketplaceMeta
from .stubs import MarketplaceNotImplementedClient
from .wildberries import WildberriesClient


MARKETPLACES = {
    "wildberries": MarketplaceMeta(
        id="wildberries",
        name="Wildberries",
        token_label="API токен Wildberries",
        token_help='Нужен токен категории "Цены и скидки" с чтением и записью.',
        implemented=True,
    ),
    "ozon": MarketplaceMeta(
        id="ozon",
        name="Ozon",
        token_label="API ключ Ozon",
        token_help="Для Ozon обычно нужны Client-Id и Api-Key. Адаптер добавляется отдельным модулем.",
        implemented=False,
    ),
    "yandex_market": MarketplaceMeta(
        id="yandex_market",
        name="Яндекс Маркет",
        token_label="API токен Яндекс Маркета",
        token_help="Адаптер Яндекс Маркета добавляется отдельным модулем после уточнения доступа к API.",
        implemented=False,
    ),
}


def get_marketplace_meta(marketplace_id):
    return MARKETPLACES.get(marketplace_id, MARKETPLACES["wildberries"])


def create_marketplace_client(marketplace_id, token=None):
    meta = get_marketplace_meta(marketplace_id)

    if meta.id == "wildberries":
        return WildberriesClient(
            token=token,
            products_path=PRODUCTS_PATH,
            demo=(APP_MODE == "DEMO"),
        )

    return MarketplaceNotImplementedClient(meta.name)
