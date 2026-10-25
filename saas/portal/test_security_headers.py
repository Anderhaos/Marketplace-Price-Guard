from django.http import HttpResponse, JsonResponse
from django.test import Client, SimpleTestCase

from portal.security_headers import NoSaleSecurityHeadersMiddleware, CSP


class SecurityHeadersTests(SimpleTestCase):
    def test_dynamic_html_has_compatible_restrictive_policy(self):
        policy = NoSaleSecurityHeadersMiddleware(
            lambda request: HttpResponse("<h1>NoSale</h1>", content_type="text/html; charset=utf-8")
        )(None)["Content-Security-Policy"]
        for required in (
            "default-src 'self'", "script-src 'self' 'unsafe-inline'",
            "object-src 'none'", "frame-ancestors 'none'", "form-action 'self'",
            "connect-src 'self'", "img-src 'self' data: https:",
        ):
            self.assertIn(required, policy)
        self.assertNotIn("script-src *", policy)
        self.assertNotIn("object-src 'self'", policy)

    def test_api_json_not_modified(self):
        response = NoSaleSecurityHeadersMiddleware(
            lambda request: JsonResponse({"ok": True})
        )(None)
        self.assertNotIn("Content-Security-Policy", response.headers)

    def test_login_get_includes_policy(self):
        response = Client().get("/login/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Content-Security-Policy"], CSP)
        self.assertEqual(response.headers["X-Frame-Options"], "DENY")
