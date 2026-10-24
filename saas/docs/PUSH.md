# Push notifications and daily summary

This is a **separate** SaaS-only subsystem: `push/` with a dedicated notifications worker.
It never modifies WB prices. The legacy application does not share these models, keys or workers.

## Production configuration

Supply **both** `PUSH_VAPID_PUBLIC_KEY` (URL-safe base64 uncompressed P-256 public point, no padding)
and `PUSH_VAPID_PRIVATE_KEY` (URL-safe base64 DER EC private key), generated together.
Never commit a private key. Use a stable key pair: rotating it invalidates existing browser subscriptions.
Configure `PUSH_VAPID_SUBJECT` as `mailto:` followed by a real monitored address.

One-time key generation, on a trusted admin computer:

```python
import base64
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization

key = ec.generate_private_key(ec.SECP256R1())
public = key.public_key().public_bytes(
    encoding=serialization.Encoding.X962,
    format=serialization.PublicFormat.UncompressedPoint,
)
private = key.private_bytes(
    encoding=serialization.Encoding.DER,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption(),
)
print("PUSH_VAPID_PUBLIC_KEY=" + base64.urlsafe_b64encode(public).decode().rstrip("="))
print("PUSH_VAPID_PRIVATE_KEY=" + base64.urlsafe_b64encode(private).decode().rstrip("="))
```

Run the standalone worker with `python manage.py run_notifications`. Its independent
Docker Compose service runs every 30 seconds and includes outbox retries. If keys are
missing, the UI explicitly reports Push unavailable, and no outbound Push calls are
made. In-app events and daily summaries still work.

Only HTTPS endpoints at the approved FCM, Mozilla, Apple and Windows notification
provider domains are accepted. Credentials and personal marketplace data are never
cached by the service worker. Requests to register/unregister devices and change
settings require an authenticated Django session and CSRF token.

Users can opt out per-device, disable instant Push, disable evening reports and
select a timezone. Explicit logout deactivates the registered device subscription.
The evening report is created at or after 20:00 in the selected timezone, at most
once per calendar date, across the user's shops. After a long outage, a delayed
report may reflect partial-day data; delivery is best effort (as are all Web Push
providers).

Important alerts are only based on `confirmed`, `error` and `upload_error`
events; a submitted WB upload is **not** an accepted correction. Queue and
delivery never run inside WB API operations.

## Testing/release
- GitHub CI tests run with fake endpoint/keys and mocked Web Push transport.
- Run staging with no real WB token and `SAAS_WB_NETWORK_ENABLED=0`.
- Generate and configure keys using server secret storage, verify permissions on
  an installed PWA with HTTPS, and perform one end-to-end delivery test before
  advertising Push delivery as production ready.
- On iPhone, install the web app to the home screen before requesting permission.
