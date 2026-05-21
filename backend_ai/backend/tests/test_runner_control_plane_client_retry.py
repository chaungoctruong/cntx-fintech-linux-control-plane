from __future__ import annotations

import asyncio

import httpx

from app.runner.control_plane_client import MT5RunnerControlPlaneClient


class _FakeAsyncClient:
    def __init__(self, items):
        self.items = list(items)
        self.calls = []

    async def request(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, "kwargs": kwargs})
        item = self.items.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _response(status_code: int, *, method: str = "POST", url: str = "https://backend/api/v2/runner/heartbeat"):
    return httpx.Response(
        status_code,
        json={"ok": status_code < 400},
        request=httpx.Request(method, url),
    )


def test_runner_client_retries_gateway_502_once_then_succeeds():
    fake = _FakeAsyncClient([_response(502), _response(200)])
    client = MT5RunnerControlPlaneClient(
        base_url="https://backend",
        api_key="secret",
        client=fake,
        retry_attempts=2,
        retry_base_delay_sec=0,
        retry_max_delay_sec=0,
    )

    result = asyncio.run(client.heartbeat({"runner_id": "runner-win-01"}))

    assert result == {"ok": True}
    assert len(fake.calls) == 2
    assert fake.calls[0]["kwargs"]["headers"]["X-Backend-Api-Key"] == "secret"


def test_runner_client_retries_network_error_then_succeeds():
    request = httpx.Request("POST", "https://backend/api/v2/runner/heartbeat")
    fake = _FakeAsyncClient([httpx.ConnectError("temporary gateway outage", request=request), _response(200)])
    client = MT5RunnerControlPlaneClient(
        base_url="https://backend",
        api_key="secret",
        client=fake,
        retry_attempts=2,
        retry_base_delay_sec=0,
        retry_max_delay_sec=0,
    )

    result = asyncio.run(client.heartbeat({"runner_id": "runner-win-01"}))

    assert result == {"ok": True}
    assert len(fake.calls) == 2


def test_runner_client_does_not_retry_auth_401():
    fake = _FakeAsyncClient([_response(401)])
    client = MT5RunnerControlPlaneClient(
        base_url="https://backend",
        api_key="bad",
        client=fake,
        retry_attempts=3,
        retry_base_delay_sec=0,
        retry_max_delay_sec=0,
    )

    try:
        asyncio.run(client.heartbeat({"runner_id": "runner-win-01"}))
    except httpx.HTTPStatusError as exc:
        assert exc.response.status_code == 401
    else:
        raise AssertionError("expected HTTPStatusError")
    assert len(fake.calls) == 1
