"""Unit tests for Meta webhook authenticity checks: HMAC-SHA256 body
signature (x-hub-signature-256) and the GET subscription handshake token.
Both fail closed."""

import hashlib
import hmac

from app.webhooks.whatsapp.security import verify_meta_signature, verify_meta_verify_token


def _sign(body: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def test_verify_signature_accepts_valid_signature() -> None:
    body = b'{"entry": []}'
    secret = "app-secret-1"
    assert verify_meta_signature(body, _sign(body, secret), secret) is True


def test_verify_signature_rejects_tampered_body() -> None:
    body = b'{"entry": []}'
    secret = "app-secret-1"
    signature = _sign(body, secret)
    tampered_body = b'{"entry": [{"malicious": true}]}'
    assert verify_meta_signature(tampered_body, signature, secret) is False


def test_verify_signature_rejects_missing_header() -> None:
    assert verify_meta_signature(b"{}", None, "app-secret-1") is False


def test_verify_signature_rejects_malformed_header() -> None:
    assert verify_meta_signature(b"{}", "not-sha256-prefixed", "app-secret-1") is False


def test_verify_verify_token_accepts_match() -> None:
    assert verify_meta_verify_token("correct-token", "correct-token") is True


def test_verify_verify_token_rejects_mismatch() -> None:
    assert verify_meta_verify_token("wrong-token", "correct-token") is False


def test_verify_verify_token_rejects_none() -> None:
    assert verify_meta_verify_token(None, "correct-token") is False
