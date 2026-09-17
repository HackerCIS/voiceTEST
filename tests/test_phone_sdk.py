"""Verify the installed optional SDK's hangup request without contacting ClawOps."""

import asyncio
import json

import httpx
import pytest

from app.phone_session import end_phone_call


def test_remote_hangup_uses_call_control_api(monkeypatch):
    clawops = pytest.importorskip("clawops")
    sdk_client = clawops.AsyncClawOps
    requests = []

    def handler(request):
        requests.append(request)
        assert request.method == "POST"
        assert request.url.path == "/v1/accounts/account_test/calls/CA_test"
        assert request.headers["Authorization"] == "Bearer clawops_test_key"
        assert json.loads(request.content) == {"Status": "completed"}
        return httpx.Response(200, json={"callId": "CA_test", "status": "completed"})

    def make_client(**kwargs):
        return sdk_client(
            api_key="clawops_test_key",
            account_id="account_test",
            http_client=httpx.AsyncClient(
                base_url="https://api.claw-ops.com", transport=httpx.MockTransport(handler)
            ),
            **kwargs,
        )

    monkeypatch.setattr(clawops, "AsyncClawOps", make_client)
    asyncio.run(end_phone_call("CA_test"))
    assert len(requests) == 1
