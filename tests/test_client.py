import json

import httpx
import pytest

from braze_toolkit import BrazeAPIError, BrazeClient, BrazeRateLimitError, TrackBatch, chunked


def make_client(handler, **kw):
    return BrazeClient("key", "https://rest.example.braze.com", transport=httpx.MockTransport(handler), sleep=lambda s: None, **kw)


def test_chunked():
    assert list(chunked([1, 2, 3, 4, 5], 2)) == [[1, 2], [3, 4], [5]]
    with pytest.raises(ValueError):
        list(chunked([1], 0))


def test_track_batch_splits_each_array_at_75():
    b = TrackBatch(
        attributes=[{"external_id": str(i)} for i in range(160)],
        events=[{"external_id": "x", "name": "e"}] * 10,
    )
    reqs = list(b.requests())
    assert len(reqs) == 3
    assert [len(r["attributes"]) for r in reqs] == [75, 75, 10]
    assert len(reqs[0]["events"]) == 10 and "events" not in reqs[1]


def test_track_sends_auth_and_bodies():
    seen = []

    def handler(req):
        assert req.headers["Authorization"] == "Bearer key"
        seen.append(json.loads(req.content))
        return httpx.Response(201, json={"message": "success"})

    c = make_client(handler)
    out = c.track(TrackBatch(attributes=[{"external_id": str(i)} for i in range(80)]))
    assert len(out) == 2 and len(seen[1]["attributes"]) == 5


def test_retries_on_429_then_succeeds():
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(429, json={"message": "rate limited"})
        return httpx.Response(201, json={"message": "success"})

    assert make_client(handler).request("POST", "/users/track", json={})["message"] == "success"
    assert calls["n"] == 3


def test_rate_limit_error_after_exhausting_retries():
    c = make_client(lambda r: httpx.Response(429, json={}), max_retries=1)
    with pytest.raises(BrazeRateLimitError):
        c.request("GET", "/x")


def test_400_not_retried():
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return httpx.Response(400, json={"message": "bad"})

    with pytest.raises(BrazeAPIError) as e:
        make_client(handler).request("POST", "/users/track", json={})
    assert e.value.status_code == 400 and calls["n"] == 1


def test_catalog_upsert_chunks_and_validates_ids():
    seen = []

    def handler(req):
        seen.append((req.method, req.url.path, len(json.loads(req.content)["items"])))
        return httpx.Response(202, json={"message": "success"})

    c = make_client(handler)
    c.upsert_catalog_items("products", [{"id": str(i), "price": i} for i in range(120)])
    assert seen == [("PUT", "/catalogs/products/items", 50), ("PUT", "/catalogs/products/items", 50), ("PUT", "/catalogs/products/items", 20)]
    with pytest.raises(ValueError):
        c.upsert_catalog_items("products", [{"price": 1}])


def test_send_campaign_body():
    def handler(req):
        body = json.loads(req.content)
        assert body["recipients"] == [{"external_user_id": "u1"}]
        assert body["trigger_properties"] == {"coupon": "X"}
        return httpx.Response(201, json={"dispatch_id": "d"})

    assert make_client(handler).send_campaign("cmp", ["u1"], {"coupon": "X"})["dispatch_id"] == "d"
