from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable


_executor = ThreadPoolExecutor(max_workers=5, thread_name_prefix="telegram-dashboard")
_qr_states: dict[str, dict[str, Any]] = {}
_qr_lock = threading.RLock()
_account_locks: dict[int, threading.Lock] = {}
_account_locks_guard = threading.Lock()


def submit_job(app, func: Callable, *args, **kwargs):
    """Run a background job with a Flask application context."""

    def runner():
        with app.app_context():
            return func(*args, **kwargs)

    return _executor.submit(runner)


def set_qr_state(token: str, **values: Any) -> None:
    with _qr_lock:
        state = _qr_states.setdefault(token, {})
        state.update(values)


def get_qr_state(token: str) -> dict[str, Any]:
    with _qr_lock:
        return dict(_qr_states.get(token, {"status": "preparing"}))


def remove_qr_state(token: str) -> None:
    with _qr_lock:
        _qr_states.pop(token, None)


def get_account_lock(account_id: int) -> threading.Lock:
    with _account_locks_guard:
        return _account_locks.setdefault(account_id, threading.Lock())



def executor_snapshot() -> dict[str, int]:
    """Return a lightweight diagnostic snapshot for the internal worker."""
    try:
        queued = _executor._work_queue.qsize()
    except Exception:
        queued = 0
    return {
        "max_workers": getattr(_executor, "_max_workers", 0),
        "queued_jobs": queued,
        "qr_states": len(_qr_states),
        "account_locks": len(_account_locks),
    }
