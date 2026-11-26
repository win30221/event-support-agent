"""Opt-in request-scoped transport events; no credentials or payloads."""
from contextlib import contextmanager
from contextvars import ContextVar
from threading import Lock

_current = ContextVar("transport_observation", default=None)


@contextmanager
def capture_transport():
    # asyncio.to_thread 繼承 ContextVar；每輪獨立且同輪多工具執行緒安全。
    data = {"version": 1, "events": []}
    token = _current.set((data, Lock()))
    try:
        yield data
    finally:
        _current.reset(token)


def record_transport(provider, kind, event, *, http_status=None, error_type=None,
                     seconds=None, attempt=None):
    active = _current.get()
    if active is None:
        return
    row = {"provider": provider, "kind": kind, "event": event}
    for key, value in (("http_status", http_status), ("error_type", error_type),
                       ("seconds", seconds), ("attempt", attempt)):
        if value is not None:
            row[key] = value
    data, lock = active
    with lock:
        data["events"].append(row)
