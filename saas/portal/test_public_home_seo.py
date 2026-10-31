"""SEO and public entry regression tests for NoSale."""
import xml.etree.ElementTree as ET
from unittest.mock import patch

from django.contrib.auth.models import User
from django.http import HttpResponse
from django.test import TestCase, override_settings


@override_settings(
    NOSALE_PUBLIC_BASE_URL="https://nosale.pro",
    NOSALE_YM_COUNTER_ID="",
    NOSALE_YANDEX_VERIFICATION="",
    NOSALE_GOOGLE_VERIFICATION="",
    SAAS_PIN_AUTH_ENABLED=True,
    REQUIRE_EMAIL_VERIFICATION=True,
)
class NoSalePublicSEOTests(TestCase):
    def test_visitor_gets_public_root_not_login_redirect(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Скидки под контролем.")
        self.assertContains(response, 'id="features"')
        self.assertContains(response, 'id="pricing"')
        self.assertContains(response, 'href="/login/"')
        self.assertNotContains(response, "?next=/")
        self.assertContains(response, 'rel="canonical" href="https://nosale.pro/"')
        self.assertContains(response, "application/ld+json")
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertNotIn("X-Robots-Tag", response)

    def test_authenticated_dashboard_remains_unchanged(self):
        user = User.objects.create_user(username="seller@example.test", password="2674")
        self.client.force_login(user)
        with patch("guard.views.dashboard", return_value=HttpResponse("seller area")) as dashboard:
            response = self.client.get("/")
        dashboard.assert_called_once()
        self.assertContains(response, "seller area")
        self.assertEqual(response["X-Robots-Tag"], "noindex, nofollow, noarchive")

    def test_private_login_noindex_and_preserves_admin_callback(self):
        response = self.client.get("/login/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["X-Robots-Tag"], "noindex, nofollow, noarchive")
        self.assertContains(response, 'content="noindex, nofollow, noarchive"')
        response = self.client.get("/login/?next=/admin/")
        self.assertEqual(response.status_code, 200)

    def test_robots_and_sitemap_only_list_public_urls(self):
        robots = self.client.get("/robots.txt")
        self.assertEqual(robots.status_code, 200)
        self.assertIn("text/plain", robots["Content-Type"])
        self.assertIn(b"Disallow: /admin/", robots.content)
        self.assertIn(b"Disallow: /billing/", robots.content)
        self.assertIn(b"Sitemap: https://nosale.pro/sitemap.xml", robots.content)
        sitemap = self.client.get("/sitemap.xml")
        self.assertEqual(sitemap.status_code, 200)
        self.assertIn("application/xml", sitemap["Content-Type"])
        root = ET.fromstring(sitemap.content)
        urls = {node.text for node in root.iter() if node.tag.endswith("loc")}
        self.assertEqual(urls, {
            "https://nosale.pro/",
            "https://nosale.pro/offer/",
            "https://nosale.pro/privacy/",
            "https://nosale.pro/contacts/",
        })

    def test_legal_pages_have_canonical_and_are_indexable(self):
        for path in ("/offer/", "/privacy/", "/contacts/"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, f'<link rel="canonical" href="https://nosale.pro{path}">', html=False)
                self.assertNotIn("X-Robots-Tag", response)

    def test_metrika_only_with_separate_counter_and_explicit_consent(self):
        response = self.client.get("/")
        self.assertNotContains(response, "/static/public-metrika.js")
        with override_settings(NOSALE_YM_COUNTER_ID="123456789"):
            response = self.client.get("/")
            self.assertContains(response, 'data-metrika-id="123456789"')
            self.assertContains(response, 'id="nosale-consent"')
            self.assertNotContains(response, "mc.yandex.ru/metrika/tag.js?id=")

    def test_non_public_pages_never_indexed(self):
        for path in ("/register/", "/password-reset/"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response["X-Robots-Tag"], "noindex, nofollow, noarchive")

    def test_unsafe_request_to_public_home_rejected(self):
        response = self.client.post("/", {"dummy": "value"})
        self.assertEqual(response.status_code, 405)
