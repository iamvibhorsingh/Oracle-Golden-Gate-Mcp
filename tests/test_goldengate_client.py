"""GoldenGate HTTP client: retries, errors, exception chaining."""

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from goldengate_mcp_server.goldengate_client import (
    GoldenGateAPIError,
    GoldenGateClient,
)


@pytest.fixture
def client():
    return GoldenGateClient(
        base_url="https://gg.example:9000",
        username="u",
        password="p",
        verify_ssl=True,
        timeout=5,
        cache_ttl=0,
    )


@pytest.mark.asyncio
async def test_request_retries_transient_503(client):
    ok = MagicMock()
    ok.status_code = 200
    ok.text = '{"ok": true}'
    ok.json = lambda: {"ok": True}

    bad = MagicMock()
    bad.status_code = 503
    bad.text = ""
    bad.json = MagicMock(return_value={})

    client.client.request = AsyncMock(side_effect=[bad, ok])

    out = await client._request("GET", "/test", use_cache=False)
    assert out == {"ok": True}
    assert client.client.request.await_count == 2


@pytest.mark.asyncio
async def test_api_error_has_status(client):
    resp = MagicMock()
    resp.status_code = 404
    resp.text = "nope"
    resp.json = MagicMock(return_value={"message": "missing"})

    client.client.request = AsyncMock(return_value=resp)

    with pytest.raises(GoldenGateAPIError) as ei:
        await client._request("GET", "/missing", use_cache=False)

    assert ei.value.http_status == 404


@pytest.mark.asyncio
async def test_connection_error_chaining(client):
    client.client.request = AsyncMock(
        side_effect=httpx.ConnectError("oops", request=MagicMock())
    )

    with pytest.raises(GoldenGateAPIError) as ei:
        await client._request("GET", "/x", use_cache=False)

    assert isinstance(ei.value.__cause__, httpx.ConnectError)
