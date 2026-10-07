"""Process-local admission before Gmail and a rolling model-attempt budget.

The deployed API has one process. Other model consumers share the AWS quota;
leave headroom and reassess these limits before adding processes/replicas.
"""

import math
import threading
import time
from collections import deque
from contextlib import contextmanager
from functools import lru_cache

from app.api.errors import ApiError
from app.config import get_settings


def busy(seconds=5):
    return ApiError(429, "classification_busy", "Try classification again later.",
                    headers={"Retry-After": str(max(1, math.ceil(seconds)))})


class ClassificationLimits:
    def __init__(self, concurrency, requests_per_minute, *, clock=time.monotonic):
        self.slots = threading.BoundedSemaphore(concurrency)
        self.requests_per_minute = requests_per_minute
        self.clock = clock
        self.attempts = deque()
        self.lock = threading.Lock()

    def check_rate(self, *, consume=False):
        with self.lock:
            now = self.clock()
            while self.attempts and self.attempts[0] <= now - 60:
                self.attempts.popleft()
            if len(self.attempts) >= self.requests_per_minute:
                raise busy(self.attempts[0] + 60 - now)
            if consume:
                self.attempts.append(now)

    @contextmanager
    def admission(self):
        self.check_rate()
        if not self.slots.acquire(blocking=False):
            raise busy()
        try:
            yield
        finally:
            self.slots.release()


@lru_cache
def get_limits():
    settings = get_settings()
    return ClassificationLimits(settings.classification_max_concurrency,
                                settings.classification_requests_per_minute)
