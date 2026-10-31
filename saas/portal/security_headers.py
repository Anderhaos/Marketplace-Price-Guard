"""Conservative CSP for existing server-rendered NoSale pages.

Legacy server-rendered templates still contain inline styles and scripts. Keep
those temporarily permitted while preventing arbitrary remote JS, embedded
objects and third-party framing. Replace unsafe-inline with nonce/hash policy
once legacy templates and Django admin inline code are refactored.
"""

CSP = "; ".join((
    "default-src 'self'",
    "base-uri 'self'",
    "object-src 'none'",
    "frame-ancestors 'none'",
    "frame-src 'none'",
    "form-action 'self'",
    "script-src 'self' 'unsafe-inline' https://mc.yandex.ru https://mc.yandex.com",
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: https: https://mc.yandex.ru",
    "font-src 'self' data:",
    "connect-src 'self' https://mc.yandex.ru https://mc.yandex.com",
    "worker-src 'self'",
    "manifest-src 'self'",
))


class NoSaleSecurityHeadersMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        # Avoid interfering with API responses, service workers and downloads.
        if response.get("Content-Type", "").lower().startswith("text/html"):
            response["Content-Security-Policy"] = CSP
            # Some middleware-level tests call with a None request. Treat that
            # context as private rather than crashing or allowing indexing.
            request_path = getattr(request, "path", "")
            public_path = request_path in ("/", "/offer/", "/privacy/", "/contacts/")
            if not public_path or (request_path == "/" and request.user.is_authenticated):
                response["X-Robots-Tag"] = "noindex, nofollow, noarchive"
        return response
