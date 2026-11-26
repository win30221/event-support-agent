"""Process-local pacing for the NVIDIA trial endpoint, shared by chat and embeddings."""
import logging
import math
import os
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from threading import Lock
from time import monotonic, sleep

ENV = "EVENT_SUPPORT_AGENT_NVIDIA_RPM"
DEFAULT_BATCH_RPM = 20
logger = logging.getLogger(__name__)


def configured_rpm():
    raw = os.getenv(ENV, "0").strip()
    if not raw.isdecimal():
        raise ValueError(f"{ENV} must be a nonnegative integer (0 disables pacing).")
    return int(raw)


def retry_after_seconds(headers):
    if not headers:
        return None
    raw = headers.get("Retry-After")
    if not raw:
        return None
    try:
        seconds = float(raw)
    except (ValueError, TypeError):
        try:
            date = parsedate_to_datetime(raw)
            if date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)
            seconds = (date - datetime.now(timezone.utc)).total_seconds()
        except (ValueError, TypeError, OverflowError):
            return None
    return max(0, seconds) if math.isfinite(seconds) else None


class RequestPacer:
    """等距放行而非瞬間用完整分鐘額度；跨執行緒與 asyncio.run 共用。"""
    def __init__(self, *, clock=monotonic, sleeper=sleep):
        self.clock, self.sleeper = clock, sleeper
        self.lock = Lock()
        self.last_start = None
        self.not_before = 0.0

    def wait(self, rpm):
        if not rpm:
            return 0.0
        with self.lock:
            start = self.clock()
            target = max(self.not_before, start if self.last_start is None
                         else self.last_start + 60.0 / rpm)
            delay = max(0.0, target - start)
            if delay > 0:
                logger.warning("NVIDIA pacing: waiting %.2fs (limit %d RPM)", delay, rpm)
                self.sleeper(delay)
            self.last_start = self.clock()
            return delay

    def defer(self, seconds):
        with self.lock:
            self.not_before = max(self.not_before, self.clock() + seconds)


_pacer = RequestPacer()


def pace_request(provider):
    if provider == "nvidia":
        return _pacer.wait(configured_rpm())
    return 0.0


def note_rate_limit(provider, headers=None):
    if provider == "nvidia" and configured_rpm():
        # 429 未提供 Retry-After 時等待一個分鐘窗口；不新增 LLM 重試。
        seconds = retry_after_seconds(headers)
        delay = 60.0 if seconds is None else seconds
        _pacer.defer(delay)
        logger.warning("NVIDIA rate limit: next request deferred %.2fs", delay)
