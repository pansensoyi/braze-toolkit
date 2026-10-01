"""Typed, batch-aware client for the Braze REST API."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Iterator, Mapping, Sequence

import httpx

from .errors import BrazeAPIError, BrazeRateLimitError

# Braze documented limits
TRACK_MAX_PER_ARRAY = 75  # attributes / events / purchases per /users/track call
CATALOG_MAX_ITEMS = 50  # items per catalog items request


def chunked(items: Sequence[Any], size: int) -> Iterator[list[Any]]:
    """Yield successive lists of at most ``size`` elements."""
    if size < 1:
        raise ValueError("size must be >= 1")
    for i in range(0, len(items), size):
        yield list(items[i : i + size])


@dataclass
class TrackBatch:
    """Accumulates /users/track payload objects and splits them into valid requests."""

    attributes: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    purchases: list[dict[str, Any]] = field(default_factory=list)

    def requests(self, max_per_array: int = TRACK_MAX_PER_ARRAY) -> Iterator[dict[str, Any]]:
        """Yield request bodies where each array holds at most ``max_per_array`` objects."""
        a = list(chunked(self.attributes, max_per_array)) if self.attributes else []
        e = list(chunked(self.events, max_per_array)) if self.events else []
        p = list(chunked(self.purchases, max_per_array)) if self.purchases else []
        for i in range(max(len(a), len(e), len(p))):
            body: dict[str, Any] = {}
            if i < len(a):
                body["attributes"] = a[i]
            if i < len(e):
                body["events"] = e[i]
            if i < len(p):
                body["purchases"] = p[i]
            yield body


class BrazeClient:
    """Minimal Braze REST client with retries on 429/5xx.

    Args:
        api_key: REST API key with the required permissions.
        rest_endpoint: Your instance URL, e.g. ``https://rest.iad-01.braze.com``.
        max_retries: Retries for rate-limited or 5xx responses.
        backoff: Base seconds for exponential backoff when no reset header is given.
        transport: Optional httpx transport (useful for testing).
        sleep: Injectable sleep function (useful for testing).
    """

    def __init__(
        self,
        api_key: str,
        rest_endpoint: str,
        *,
        max_retries: int = 3,
        backoff: float = 1.0,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not api_key:
            raise ValueError("api_key is required")
        self._http = httpx.Client(
            base_url=rest_endpoint.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            timeout=timeout,
            transport=transport,
        )
        self.max_retries = max_retries
        self.backoff = backoff
        self._sleep = sleep

    # ------------------------------------------------------------------ core
    def request(self, method: str, path: str, json: Any | None = None, params: Mapping[str, Any] | None = None) -> dict[str, Any]:
        attempt = 0
        while True:
            resp = self._http.request(method, path, json=json, params=params)
            if resp.status_code < 400:
                return resp.json() if resp.content else {}
            retryable = resp.status_code == 429 or resp.status_code >= 500
            if not retryable or attempt >= self.max_retries:
                if resp.status_code == 429:
                    raise BrazeRateLimitError(resp.status_code, _safe_json(resp), path)
                raise BrazeAPIError(resp.status_code, _safe_json(resp), path)
            self._sleep(self._retry_delay(resp, attempt))
            attempt += 1

    def _retry_delay(self, resp: httpx.Response, attempt: int) -> float:
        reset = resp.headers.get("X-RateLimit-Reset")
        if reset and reset.isdigit():
            return max(0.0, float(reset) - time.time())
        return self.backoff * (2**attempt)

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "BrazeClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ---------------------------------------------------------------- users
    def track(self, batch: TrackBatch) -> list[dict[str, Any]]:
        """Send a TrackBatch, splitting it into as many /users/track calls as needed."""
        return [self.request("POST", "/users/track", json=body) for body in batch.requests()]

    def export_by_ids(self, external_ids: Sequence[str], fields_to_export: Sequence[str] | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {"external_ids": list(external_ids)}
        if fields_to_export:
            body["fields_to_export"] = list(fields_to_export)
        return self.request("POST", "/users/export/ids", json=body)

    def delete_users(self, external_ids: Sequence[str]) -> dict[str, Any]:
        return self.request("POST", "/users/delete", json={"external_ids": list(external_ids)})

    # ------------------------------------------------------------- catalogs
    def upsert_catalog_items(self, catalog: str, items: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
        """Create or replace catalog items (PUT), 50 per request. Each item needs an ``id``."""
        return self._catalog_items("PUT", catalog, items)

    def edit_catalog_items(self, catalog: str, items: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
        """Partially update existing items (PATCH), 50 per request."""
        return self._catalog_items("PATCH", catalog, items)

    def delete_catalog_items(self, catalog: str, ids: Iterable[str]) -> list[dict[str, Any]]:
        return self._catalog_items("DELETE", catalog, [{"id": i} for i in ids])

    def _catalog_items(self, method: str, catalog: str, items: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
        rows = [dict(i) for i in items]
        for r in rows:
            if "id" not in r:
                raise ValueError(f"catalog item missing 'id': {r!r}")
        path = f"/catalogs/{catalog}/items"
        return [self.request(method, path, json={"items": c}) for c in chunked(rows, CATALOG_MAX_ITEMS)]

    # -------------------------------------------------------------- messaging
    def send_campaign(self, campaign_id: str, external_ids: Sequence[str], trigger_properties: Mapping[str, Any] | None = None) -> dict[str, Any]:
        recipients = [{"external_user_id": e} for e in external_ids]
        body: dict[str, Any] = {"campaign_id": campaign_id, "recipients": recipients}
        if trigger_properties:
            body["trigger_properties"] = dict(trigger_properties)
        return self.request("POST", "/campaigns/trigger/send", json=body)


def _safe_json(resp: httpx.Response) -> Any:
    try:
        return resp.json()
    except ValueError:
        return resp.text
