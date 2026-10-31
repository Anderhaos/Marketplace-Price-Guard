"""Public SEO endpoints with the original seller entry design retained."""
from xml.etree.ElementTree import Element, SubElement, tostring

from django.conf import settings
from django.http import HttpResponse
from django.urls import reverse
from django.views.decorators.http import require_safe



def home_or_dashboard(request):
    """Keep the original NoSale entry design; only add public SEO at /.

    Both / and /login/ use the existing email-first gateway, including PIN
    and registration. This avoids any replacement marketing layout.
    """
    if request.user.is_authenticated:
        from guard.views import dashboard
        return dashboard(request)
    from guard.email_pin import login_entry
    response = login_entry(request)
    response["Cache-Control"] = "no-store"
    return response

@require_safe
def robots_txt(request):
    # Robots are for indexing hints only; actual access is enforced by auth.
    lines = [
        "User-agent: *", "Allow: /",
        "Disallow: /login/", "Disallow: /register/",
        "Disallow: /auth/", "Disallow: /admin/",
        "Disallow: /stores/", "Disallow: /settings/",
        "Disallow: /products/", "Disallow: /billing/",
        "Disallow: /notifications/", "Disallow: /history/",
        "Disallow: /connect/", "Disallow: /assistant/",
        "Disallow: /advisor/", "Disallow: /promotions/",
        "Disallow: /finance/", "Disallow: /password-reset/",
        "Disallow: /verify/", "Disallow: /push/",
        f"Sitemap: {settings.NOSALE_PUBLIC_BASE_URL}/sitemap.xml",
    ]
    response = HttpResponse("\n".join(lines) + "\n", content_type="text/plain; charset=utf-8")
    response["Cache-Control"] = "public, max-age=3600"
    return response


@require_safe
def sitemap_xml(request):
    urls = Element("urlset", {"xmlns": "http://www.sitemaps.org/schemas/sitemap/0.9"})
    for route in ("dashboard", "public_offer", "public_privacy", "public_contacts"):
        entry = SubElement(urls, "url")
        SubElement(entry, "loc").text = settings.NOSALE_PUBLIC_BASE_URL + reverse(route)
    body = b'<?xml version="1.0" encoding="UTF-8"?>\n' + tostring(urls, encoding="utf-8")
    response = HttpResponse(body, content_type="application/xml; charset=utf-8")
    response["Cache-Control"] = "public, max-age=3600"
    return response
