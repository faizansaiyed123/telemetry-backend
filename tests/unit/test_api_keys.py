"""Unit tests for machine credential helpers."""

from app.services.api_keys import hash_api_key, generate_api_key, has_scope


def test_generated_api_keys_are_unique_and_hashable():
    first = generate_api_key()
    second = generate_api_key()

    assert first[0] != second[0]
    assert first[1].startswith("tm_live_")
    assert len(first[2]) == 64
    assert hash_api_key(first[0]) == first[2]


def test_scope_parser_is_exact():
    assert has_scope("telemetry:write", "telemetry:write")
    assert has_scope("telemetry:write,other", "telemetry:write")
    assert not has_scope("telemetry:read", "telemetry:write")
