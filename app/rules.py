def check_discount_rule(product):
    current_discount = product["current_discount"]
    allowed_discount = product["allowed_discount"]

    if current_discount > allowed_discount:
        return {
            "needs_fix": True,
            "new_discount": allowed_discount,
            "message": (
                f"{product['name']} ({product['nm_id']}): "
                f"\u0441\u043a\u0438\u0434\u043a\u0430 \u0431\u044b\u043b\u0430 {current_discount}%, "
                f"\u0432\u0435\u0440\u043d\u0443\u043b \u043d\u0430 {allowed_discount}%"
            ),
        }

    return {
        "needs_fix": False,
        "new_discount": current_discount,
        "message": (
            f"{product['name']} ({product['nm_id']}): "
            f"\u0441\u043a\u0438\u0434\u043a\u0430 \u0432 \u043d\u043e\u0440\u043c\u0435"
        ),
    }
