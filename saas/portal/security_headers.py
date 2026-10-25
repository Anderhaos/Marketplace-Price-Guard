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
    "script-src 'self' 'unsafe-inline'",
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: https:",
    "font-src 'self' data:",
    "connect-src 'self'",
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
        return response
