"""Unit tests for security rate limiting."""

from app.core.rate_limit import RateLimitExceeded, SlidingWindowRateLimiter


def test_rate_limiter_blocks_after_limit_and_can_clear() -> None:
    limiter = SlidingWindowRateLimiter()
    limiter.check("login:test", 2, 60)
    limiter.check("login:test", 2, 60)

    try:
        limiter.check("login:test", 2, 60)
    except RateLimitExceeded as exc:
        assert exc.retry_after >= 1
    else:
        raise AssertionError("third request should have been rate limited")

    limiter.clear("login:test")
    limiter.check("login:test", 2, 60)
