"""The E5 preview may read actual WB data but can never change prices."""
import io
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from .wb import WBApiError, WBClient


@override_settings(SAAS_WB_READ_ONLY=True)
class ReadOnlyTransportTests(SimpleTestCase):
    def test_upload_blocked_before_network_even_for_service_token(self):
        client = WBClient("synthetic-test-token")
        with patch("guard.wb.urlopen") as urlopen:
            with self.assertRaisesMessage(WBApiError, "запрещено в тестовом режиме"):
                client.submit_discounts([(123456, 10)])
            with self.assertRaisesMessage(WBApiError, "запрещено в тестовом режиме"):
                client._request("POST", "/api/v2/upload/task", body={"data": []})
            with self.assertRaisesMessage(WBApiError, "запрещено в тестовом режиме"):
                client._request_to(client.CONTENT_BASE_URL, "POST", "/content/v2/cards/update", body={})
            urlopen.assert_not_called()

    def test_get_token_verification_and_inventory_listing_are_allowed(self):
        client = WBClient("synthetic-test-token")
        with patch("guard.wb.urlopen", side_effect=[
            io.BytesIO(b'{"data":{}}'),
            io.BytesIO(b'{"data":{"listGoods":[]}}'),
        ]) as urlopen:
            client.validate_token(probe_prices=False)
            self.assertEqual(client.get_products(), [])
            self.assertEqual(urlopen.call_count, 2)
            self.assertEqual(urlopen.call_args_list[0].args[0].get_method(), "GET")

    def test_readonly_post_for_card_listing_is_allowed(self):
        client = WBClient("synthetic-test-token")
        with patch("guard.wb.urlopen", return_value=io.BytesIO(b'{"cards":[]}')) as urlopen:
            self.assertEqual(client.get_product_cards(), [])
            self.assertEqual(urlopen.call_count, 1)
            request = urlopen.call_args.args[0]
            self.assertEqual(request.get_method(), "POST")
            self.assertIn("/content/v2/get/cards/list", request.full_url)
