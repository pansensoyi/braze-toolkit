from typing import Any


class BrazeAPIError(Exception):
    """Raised when Braze returns a non-retryable error or retries are exhausted."""

    def __init__(self, status_code: int, body: Any, path: str) -> None:
        self.status_code = status_code
        self.body = body
        self.path = path
        super().__init__(f"Braze API {status_code} on {path}: {body}")


class BrazeRateLimitError(BrazeAPIError):
    """Raised when rate limiting persists after all retries."""
