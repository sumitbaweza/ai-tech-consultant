from app.crawler.ratelimit import HostRateLimiter


class FakeClock:
    """Time that only moves when the limiter sleeps, so tests run instantly."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


async def test_requests_to_one_host_are_spaced_out() -> None:
    clock = FakeClock()
    limiter = HostRateLimiter(1.0, clock=clock, sleep=clock.sleep)

    for _ in range(3):
        await limiter.wait("https://help.example/page")

    assert clock.sleeps == [1.0, 1.0]  # the first request goes at once


async def test_each_host_has_its_own_limit() -> None:
    clock = FakeClock()
    limiter = HostRateLimiter(1.0, clock=clock, sleep=clock.sleep)

    await limiter.wait("https://help.example/page")
    await limiter.wait("https://cdn.example/image.png")

    assert clock.sleeps == []


async def test_a_host_can_have_a_faster_rate() -> None:
    clock = FakeClock()
    limiter = HostRateLimiter(1.0, per_host={"cdn.example": 2.0}, clock=clock, sleep=clock.sleep)

    await limiter.wait("https://cdn.example/1.png")
    await limiter.wait("https://cdn.example/2.png")

    assert clock.sleeps == [0.5]


async def test_no_wait_once_the_interval_has_passed() -> None:
    clock = FakeClock()
    limiter = HostRateLimiter(1.0, clock=clock, sleep=clock.sleep)

    await limiter.wait("https://help.example/1")
    clock.now += 5  # the worker spent 5 seconds parsing
    await limiter.wait("https://help.example/2")

    assert clock.sleeps == []
