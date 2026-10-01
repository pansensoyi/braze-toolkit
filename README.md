# braze-toolkit

![CI](https://github.com/pansensoyi/braze-toolkit/actions/workflows/ci.yml/badge.svg)
![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)

A small, typed Python client for the [Braze REST API](https://www.braze.com/docs/api/basics/) that handles the boring parts of production integrations:

- **Automatic batching** — `/users/track` payloads are split so each array (attributes, events, purchases) stays within Braze's 75-object limit; Catalog item writes are chunked at 50.
- **Rate-limit aware retries** — retries `429` and `5xx` responses, honouring `X-RateLimit-Reset` when present, with exponential backoff otherwise.
- **Catalogs helpers** — upsert (PUT), partial edit (PATCH) and delete for catalog items, with `id` validation before anything is sent.
- **Testable** — inject an `httpx` transport and sleep function; no network needed in tests.

> Unofficial library. Not affiliated with or endorsed by Braze, Inc.

## Install

```bash
pip install git+https://github.com/pansensoyi/braze-toolkit.git
```

## Usage

```python
from braze_toolkit import BrazeClient, TrackBatch

with BrazeClient(api_key="YOUR_REST_KEY", rest_endpoint="https://rest.iad-01.braze.com") as braze:
    batch = TrackBatch(
        attributes=[{"external_id": u["id"], "tier": u["tier"]} for u in users],   # any size
        events=[{"external_id": "u1", "name": "checkout_started", "time": "2026-10-01T10:00:00Z"}],
    )
    braze.track(batch)  # sends as many /users/track calls as needed

    # Sync a product feed into a Catalog
    braze.upsert_catalog_items("products", [{"id": "sku-1", "price": 19.99, "in_stock": True}])

    # API-triggered campaign
    braze.send_campaign("CAMPAIGN_ID", ["u1"], trigger_properties={"coupon": "WELCOME10"})
```

## Error handling

| Exception | When |
|---|---|
| `BrazeAPIError` | Non-retryable 4xx, or 5xx after retries are exhausted. Has `.status_code`, `.body`, `.path`. |
| `BrazeRateLimitError` | Still rate-limited after `max_retries`. Subclass of `BrazeAPIError`. |

## Development

```bash
pip install -e ".[dev]"
pytest
```

## License

MIT
