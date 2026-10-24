"""Ozon adapter placeholder: it must not simulate seller data or APIs."""


def status(account):
    return {
        "marketplace": "ozon",
        "connected": False,
        "available": False,
        "state": "not_implemented",
        "message": "Ozon API adapter has not been connected to NoSale MCP.",
    }
