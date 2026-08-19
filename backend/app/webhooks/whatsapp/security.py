"""Meta WhatsApp webhook authenticity checks, per tenant.

Unlike Superfone's SFVoPI stream, Meta DOES support HMAC-SHA256 body
signature verification (x-hub-signature-256) and a GET handshake
verify_token, exactly as documented at
https://developers.facebook.com/docs/graph-api/webhooks/getting-started.
Both fail closed: a missing/invalid credential is rejected before any DB
write, matching app/webhooks/superfone/security.py's established pattern.
"""

import hashlib
import hmac


def verify_meta_signature(raw_body: bytes, signature_header: str | None, app_secret: str) -> bool:
    """Verify the x-hub-signature-256 header against the raw request body,
    using the tenant's own app_secret. Returns False (never raises) --
    callers decide what HTTP status a failed verification maps to."""
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected_hex = signature_header[len("sha256=") :]
    actual_hex = hmac.new(app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(actual_hex, expected_hex)


def verify_meta_verify_token(token: str | None, expected: str) -> bool:
    """Verify the hub.verify_token query param on the GET subscription
    handshake against the tenant's stored verify_token."""
    if not token:
        return False
    return hmac.compare_digest(token, expected)
