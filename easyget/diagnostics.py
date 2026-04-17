from __future__ import annotations

from typing import Any, Dict

from .exceptions import EasyGetError


def error_payload(exc: Exception, *, compact: bool = False) -> Dict[str, Any]:
    """
    Convert exceptions into stable, machine-readable payloads.
    Compact mode is tuned for low-token AI pipelines.
    """
    if isinstance(exc, EasyGetError):
        data = exc.to_dict(compact=compact)
        if compact:
            return {"ok": 0, "e": data}
        return {"ok": False, "error": data}

    if compact:
        return {"ok": 0, "e": {"c": "UNEXPECTED_ERROR", "m": str(exc)}}
    return {
        "ok": False,
        "error": {
            "code": "UNEXPECTED_ERROR",
            "message": str(exc),
            "hint": None,
            "context": {},
            "retryable": False,
        },
    }
