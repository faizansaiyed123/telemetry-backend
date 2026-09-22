"""Tests for machine credential handling."""

from app.services.api_key_service import create_secret, hash_api_key


def test_api_key_secret_is_prefixed_and_hashed_one_way():
    secret = create_secret()
    assert secret.startswith("tlm_")
    digest = hash_api_key(secret)

    assert digest != secret
    assert len(digest) == 64
    assert hash_api_key(secret) == digest
