from gateway_ratelimit import RateLimiter


def test_allows_up_to_limit_then_blocks():
    rl = RateLimiter(limit=3, window_seconds=60)
    assert [rl.allow(1, now=t) for t in (0, 1, 2)] == [True, True, True]
    assert rl.allow(1, now=3) is False


def test_window_slides():
    rl = RateLimiter(limit=2, window_seconds=60)
    assert rl.allow(1, now=0) and rl.allow(1, now=30)
    assert rl.allow(1, now=59) is False
    assert rl.allow(1, now=61) is True  # the t=0 hit has aged out


def test_users_are_independent():
    rl = RateLimiter(limit=1, window_seconds=60)
    assert rl.allow(1, now=0) is True
    assert rl.allow(2, now=0) is True
    assert rl.allow(1, now=1) is False
