"""Inventory chips and sorting operate on the selected seller's actual product rows."""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import GuardAccount, Product, Upload


@override_settings(SAAS_PIN_AUTH_ENABLED=False, SAAS_WB_NETWORK_ENABLED=False,
                   SAAS_WB_READ_ONLY=True, SAAS_ACCESS_ENFORCED=False)
class InventoryFilterChipTests(TestCase):
    def setUp(self):
        self.seller = get_user_model().objects.create_user(
            username="inventory-chips@example.test", password="qa-pass"
        )
        self.store = GuardAccount.objects.create(
            user=self.seller, name="Selected WB", default_allowed_discount=13,
            apply_requested_ids=[105],
        )
        data = [
            (101, "Zeta", 25, 10, "80.00", "100.00"),
            (102, "Alpha", 0, 0, "50.00", "90.00"),
            (103, "Beta", 8, 5, "90.00", "100.00"),
            (104, "Gamma", 2, None, None, None),
            (105, "Epsilon", 13, None, "65.00", "90.00"),
            (106, "Delta", 0, None, None, "150.00"),
        ]
        for nm_id, title, discount, override, discounted, price in data:
            Product.objects.create(
                account=self.store, nm_id=nm_id, vendor_code=title,
                title=title, current_discount=discount,
                allowed_discount_override=override,
                discounted_price=Decimal(discounted) if discounted else None,
                price=Decimal(price) if price else None,
            )
        Upload.objects.create(
            account=self.store, upload_id=10101, status="success",
            corrected_count=3, product_ids=[101, 104, 101],
        )
        Upload.objects.create(
            account=self.store, upload_id=10102, status="pending",
            product_ids=[103],
        )
        other = get_user_model().objects.create_user(
            username="other-inventory@example.test", password="qa-pass"
        )
        other_store = GuardAccount.objects.create(user=other, name="Other WB")
        Product.objects.create(
            account=other_store, nm_id=777, title="Another seller's product",
            current_discount=99, allowed_discount_override=0,
        )
        Upload.objects.create(
            account=other_store, upload_id=777, status="success", product_ids=[777],
        )
        self.client.force_login(self.seller)

    def get_inventory(self, **params):
        response = self.client.get(reverse("dashboard"), params)
        self.assertEqual(response.status_code, 200)
        return response, [p.nm_id for p in response.context["products"]]

    def test_six_filters_are_real_and_store_scoped(self):
        expected = {
            "all": {101, 102, 103, 104, 105, 106},
            "over_limit": {101, 103},
            "corrected": {101, 104},
            "no_discount": {102},
            "custom_limit": {101, 103},
            "pending": {103, 105},
        }
        for filter_name, ids in expected.items():
            with self.subTest(filter_name=filter_name):
                response, visible = self.get_inventory(filter=filter_name)
                self.assertEqual(set(visible), ids)
                self.assertEqual(response.context["shown_count"], len(ids))
                self.assertEqual(response.context["filter_name"], filter_name)
                self.assertNotIn(777, visible)
                chips = {c["key"]: c["count"] for c in response.context["filter_chips"]}
                self.assertEqual(chips, {key: len(rows) for key, rows in expected.items()})
                self.assertContains(response, 'aria-label="Фильтры текущего списка товаров"')

    def test_empty_chips_are_hidden_except_all_and_over_limit(self):
        Upload.objects.filter(account=self.store).delete()
        self.store.apply_requested_ids = []
        self.store.save(update_fields=["apply_requested_ids"])
        Product.objects.filter(account=self.store).update(
            allowed_discount_override=None, current_discount=0,
        )
        response, _ = self.get_inventory()
        chips = [c["key"] for c in response.context["filter_chips"]]
        self.assertEqual(chips, ["all", "over_limit"])
        response, visible = self.get_inventory(filter="no_discount")
        self.assertEqual(visible, [])
        self.assertEqual([c["key"] for c in response.context["filter_chips"]],
                         ["all", "over_limit", "no_discount"])

    def test_sorting_after_filtering_uses_displayed_price_and_stable_order(self):
        expected = {
            "discount": [101, 105, 103, 104, 102, 106],
            "price": [102, 105, 101, 103, 106, 104],
            "name": [102, 103, 106, 105, 104, 101],
        }
        for sort_name, ids in expected.items():
            with self.subTest(sort=sort_name):
                response, visible = self.get_inventory(sort=sort_name)
                self.assertEqual(visible, ids)
                self.assertEqual(response.context["sort_name"], sort_name)
                self.assertContains(response, 'name="sort"')
        _, visible = self.get_inventory(filter="over_limit", sort="price")
        self.assertEqual(visible, [101, 103])

    def test_invalid_query_is_whitelisted_and_filter_links_preserve_sort(self):
        response, visible = self.get_inventory(filter="<script>", sort="../../x")
        self.assertEqual(response.context["sort_name"], "default")
        self.assertEqual(response.context["filter_name"], "all")
        self.assertEqual(len(visible), 6)
        response, _ = self.get_inventory(filter="no_discount", sort="name")
        self.assertContains(
            response, 'href="/?filter=custom_limit&amp;sort=name"'
        )
        self.assertContains(response, 'name="dashboard_sort" value="name"')
        self.assertContains(response, 'name="dashboard_filter" value="no_discount"')

    def test_product_rule_post_retains_filter_and_sort_without_wb_network(self):
        response = self.client.post(
            reverse("product_rule", args=[103]),
            {"no_discount": "on",
             "dashboard_filter": "no_discount", "dashboard_sort": "name"},
        )
        self.assertRedirects(
            response, "/?filter=no_discount&sort=name", fetch_redirect_response=False
        )
        self.assertEqual(
            Product.objects.get(account=self.store, nm_id=103).allowed_discount_override, 0
        )
        filtered, visible = self.get_inventory(filter="no_discount", sort="name")
        self.assertEqual(visible, [102, 103])
        self.assertEqual(filtered.context["shown_count"], 2)
