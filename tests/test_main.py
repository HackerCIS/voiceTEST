import asyncio
import json

import httpx
from fastapi.testclient import TestClient

from app.main import (
    ELEVENLABS_TOKEN_URL,
    OPENAI_REALTIME_SECRET_URL,
    app,
    fetch_conversation_token,
    fetch_openai_realtime_secret,
    get_runtime_config,
)


def test_session_requires_agent_id(monkeypatch) -> None:
    monkeypatch.delenv("ELEVENLABS_AGENT_ID", raising=False)
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)

    with TestClient(app) as client:
        response = client.post("/api/session")

    assert response.status_code == 503
    assert "ELEVENLABS_AGENT_ID" in response.json()["detail"]


def test_public_session_returns_current_agent_id(monkeypatch) -> None:
    monkeypatch.setenv("ELEVENLABS_AGENT_ID", "agent_current")
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)

    with TestClient(app) as client:
        response = client.post("/api/session?mode=elevenlabs")

    assert response.status_code == 200
    assert response.json() == {
        "mode": "elevenlabs",
        "authMode": "public",
        "agentId": "agent_current",
    }
    assert response.headers["cache-control"] == "no-store"


def test_hybrid_session_uses_separate_agent_id(monkeypatch) -> None:
    monkeypatch.setenv("ELEVENLABS_AGENT_ID", "agent_current")
    monkeypatch.setenv("ELEVENLABS_OPENAI_AGENT_ID", "agent_hybrid")
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)

    with TestClient(app) as client:
        response = client.post("/api/session?mode=hybrid")

    assert response.status_code == 200
    assert response.json()["agentId"] == "agent_hybrid"
    assert response.json()["mode"] == "hybrid"


def test_fetch_conversation_token() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url).startswith(ELEVENLABS_TOKEN_URL)
        assert request.url.params["agent_id"] == "agent_test"
        assert request.headers["xi-api-key"] == "secret"
        return httpx.Response(
            200,
            json={"token": "token_test", "conversation_id": "conv_test"},
        )

    async def run() -> tuple[str, str | None]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await fetch_conversation_token(
                client,
                agent_id="agent_test",
                api_key="secret",
            )

    assert asyncio.run(run()) == ("token_test", "conv_test")


def test_fetch_openai_realtime_secret_uses_latest_config(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "openai_secret")
    monkeypatch.setenv("OPENAI_PROJECT_ID", "proj_test")
    monkeypatch.setenv("OPENAI_ORG_ID", "org_test")
    monkeypatch.setenv("OPENAI_REALTIME_MODEL", "gpt-realtime-2.1")
    monkeypatch.setenv("OPENAI_REALTIME_VOICE", "marin")
    config = get_runtime_config()

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == OPENAI_REALTIME_SECRET_URL
        assert request.headers["authorization"] == "Bearer openai_secret"
        assert request.headers["openai-project"] == "proj_test"
        assert request.headers["openai-organization"] == "org_test"
        body = json.loads(request.content)
        assert body["session"]["model"] == "gpt-realtime-2.1"
        assert body["session"]["audio"]["output"]["voice"] == "marin"
        assert body["session"]["audio"]["input"]["turn_detection"]["type"] == "semantic_vad"
        return httpx.Response(
            200,
            json={
                "value": "ek_test",
                "expires_at": 1234567890,
                "session": {"id": "sess_test"},
            },
        )

    async def run() -> dict:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await fetch_openai_realtime_secret(client, config)

    assert asyncio.run(run())["value"] == "ek_test"


def test_custom_llm_proxy_rejects_missing_shared_secret_header(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "openai_secret")
    monkeypatch.setenv("CUSTOM_LLM_SHARED_SECRET", "proxy_secret")

    with TestClient(app) as client:
        response = client.post("/v1/responses", json={"input": "hello"})

    assert response.status_code == 401


def test_custom_llm_proxy_accepts_elevenlabs_responses_path(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "openai_secret")
    monkeypatch.setenv("CUSTOM_LLM_SHARED_SECRET", "proxy_secret")

    with TestClient(app) as client:
        response = client.post("/responses", json={"input": "hello"})

    assert response.status_code == 401


def test_custom_llm_proxy_forces_model_and_streams_sse(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "openai_secret")
    monkeypatch.setenv("OPENAI_LLM_MODEL", "gpt-5.4")
    monkeypatch.setenv("CUSTOM_LLM_SHARED_SECRET", "proxy_secret")

    class EventStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"event: response.completed\ndata: {\"type\":\"response.completed\"}\n\n"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer openai_secret"
        body = json.loads(request.content)
        assert body["model"] == "gpt-5.4"
        assert body["stream"] is True
        return httpx.Response(
            200,
            headers={"Content-Type": "text/event-stream"},
            stream=EventStream(),
        )

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with TestClient(app) as client:
        app.state.stream_client = mock_client
        response = client.post(
            "/v1/responses",
            headers={"Authorization": "Bearer proxy_secret"},
            json={"model": "ignored", "input": "hello", "stream": False},
        )

    asyncio.run(mock_client.aclose())
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "response.completed" in response.text
    assert response.text.endswith("data: [DONE]\n\n")
