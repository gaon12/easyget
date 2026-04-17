from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class ErrorDetails:
    """
    Structured diagnostic payload designed for both humans and LLM agents.
    """
    code: str
    message: str
    hint: Optional[str] = None
    context: Optional[Dict[str, Any]] = None
    retryable: bool = False

    def to_dict(self, compact: bool = False) -> Dict[str, Any]:
        if compact:
            payload: Dict[str, Any] = {"c": self.code, "m": self.message}
            if self.hint:
                payload["h"] = self.hint
            if self.context:
                payload["x"] = self.context
            if self.retryable:
                payload["r"] = True
            return payload

        payload = {
            "code": self.code,
            "message": self.message,
            "hint": self.hint,
            "context": self.context or {},
            "retryable": self.retryable,
        }
        return payload


class EasyGetError(Exception):
    """Base exception for easyget with machine-readable diagnostics."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "EASYGET_ERROR",
        hint: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
        retryable: bool = False,
    ):
        super().__init__(message)
        self.details = ErrorDetails(
            code=code,
            message=message,
            hint=hint,
            context=context,
            retryable=retryable,
        )

    @property
    def code(self) -> str:
        return self.details.code

    @property
    def hint(self) -> Optional[str]:
        return self.details.hint

    @property
    def context(self) -> Dict[str, Any]:
        return self.details.context or {}

    @property
    def retryable(self) -> bool:
        return self.details.retryable

    def to_dict(self, compact: bool = False) -> Dict[str, Any]:
        return self.details.to_dict(compact=compact)

    def __str__(self) -> str:
        return self.details.message


class DownloadError(EasyGetError):
    """Raised when a download operation fails."""

    def __init__(self, message: str, **kwargs):
        kwargs.setdefault("code", "DOWNLOAD_FAILED")
        kwargs.setdefault("retryable", True)
        super().__init__(message, **kwargs)


class IntegrityError(DownloadError):
    """Raised when downloaded data integrity cannot be guaranteed."""

    def __init__(self, message: str, **kwargs):
        kwargs.setdefault("code", "INTEGRITY_ERROR")
        kwargs.setdefault("retryable", False)
        super().__init__(message, **kwargs)


class RequestError(EasyGetError):
    """Raised for network and transport failures in HTTP requests."""

    def __init__(self, message: str, **kwargs):
        kwargs.setdefault("code", "REQUEST_ERROR")
        kwargs.setdefault("retryable", True)
        super().__init__(message, **kwargs)


class HTTPStatusError(DownloadError):
    """Raised when HTTP status code indicates failure."""

    def __init__(self, message: str, **kwargs):
        kwargs.setdefault("code", "HTTP_STATUS_ERROR")
        kwargs.setdefault("retryable", False)
        super().__init__(message, **kwargs)

