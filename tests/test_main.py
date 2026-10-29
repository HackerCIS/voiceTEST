import asyncio
import json

import httpx
from fastapi.testclient import TestClient

from app.main import (
    DEFAULT_HYBRID_INSTRUCTIONS,
    DEFAULT_REALTIME_INSTRUCTIONS,
    ELEVENLABS_TOKEN_URL,
    OPENAI_REALTIME_SECRET_URL,
    app,
    fetch_conversation_token,
    fetch_openai_realtime_secret,
    get_runtime_config,
)


def test_default_voice_prompts_do_not_include_examples() -> None:
    for prompt in (DEFAULT_REALTIME_INSTRUCTIONS, DEFAULT_HYBRID_INSTRUCTIONS):
        assert "# 예시" not in prompt
        assert "사용자:" not in prompt
        assert "AI:" not in prompt


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


def test_v4_turn_uses_scribe_then_prompt_file_then_v4_turbo(monkeypatch) -> None:
    monkeypatch.setenv("ELEVENLABS_API_KEY", "xi_secret")
    monkeypatch.setenv("ELEVENLABS_V4_VOICE_ID", "voice_v4")
    monkeypatch.setenv("OPENAI_API_KEY", "openai_secret")
    monkeypatch.setenv("OPENAI_LLM_MODEL", "gpt-5.4")
    from app import v4_pipeline

    v4_pipeline.clear_sessions()
    seen: list[tuple[str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/speech-to-text"):
            body = request.content
            assert b"scribe_v2" in body
            assert b"language_code" in body and b"ko" in body
            return httpx.Response(200, json={"text": "오늘 운동했어요"})
        if path.endswith("/responses"):
            payload = json.loads(request.content)
            seen.append(("llm", payload))
            return httpx.Response(
                200,
                json={
                    "output": [
                        {
                            "type": "message",
                            "content": [{"type": "output_text", "text": "[excited] 다녀왔네!"}],
                        }
                    ]
                },
            )
        return httpx.Response(404, json={"detail": path})

    class FakeTurboSocket:
        def __init__(self, uri: str) -> None:
            self.uri = uri
            self.sent: list[dict] = []
            self._incoming = [
                json.dumps({"audio": "AQA="}),
                json.dumps({"is_final": True}),
            ]

        async def __aenter__(self) -> "FakeTurboSocket":
            seen.append(("tts-url", {"uri": self.uri}))
            return self

        async def __aexit__(self, *args: object) -> bool:
            return False

        async def send(self, data: str) -> None:
            self.sent.append(json.loads(data))
            seen.append(("tts", self.sent[-1]))

        async def recv(self) -> str:
            return self._incoming.pop(0)

    monkeypatch.setattr(
        v4_pipeline.websockets,
        "connect",
        lambda uri, **kwargs: FakeTurboSocket(uri),
    )

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with TestClient(app) as client:
        app.state.stream_client = mock_client
        response = client.post(
            "/api/v4/turn",
            files={"audio": ("speech.webm", b"not-empty-audio", "audio/webm")},
        )
    asyncio.run(mock_client.aclose())

    assert response.status_code == 200
    body = response.json()
    assert body["userText"] == "오늘 운동했어요"
    assert body["agentText"] == "[excited] 다녀왔네!"
    assert seen[0][0] == "llm"
    assert "<voice_delivery>" in seen[0][1]["instructions"]
    assert seen[0][1]["model"] == "gpt-5.4"
    assert "model_id=eleven_v4_turbo" in seen[1][1]["uri"]
    assert "model_id=eleven_v4&" not in seen[1][1]["uri"]
    assert seen[2][1] == {"voices": ["voice_v4"]}
    assert seen[3][1]["inputs"] == [
        {"text": "[excited] 다녀왔네!", "voice_id": "voice_v4"}
    ]
    assert seen[3][1]["flush"] is True


def test_v4_turn_uses_http_dialogue_not_turbo_socket(monkeypatch) -> None:
    monkeypatch.setenv("ELEVENLABS_API_KEY", "xi_secret")
    monkeypatch.setenv("ELEVENLABS_V4_VOICE_ID", "voice_v4")
    monkeypatch.setenv("OPENAI_API_KEY", "openai_secret")
    monkeypatch.setenv("OPENAI_LLM_MODEL", "gpt-5.4")
    from app import v4_pipeline

    v4_pipeline.clear_sessions()
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/speech-to-text"):
            return httpx.Response(200, json={"text": "밥 먹었어요"})
        if path.endswith("/responses"):
            return httpx.Response(
                200,
                json={
                    "output": [
                        {
                            "type": "message",
                            "content": [{"type": "output_text", "text": "잘 드셨군요."}],
                        }
                    ]
                },
            )
        if path.endswith("/text-to-dialogue"):
            seen.append(json.loads(request.content))
            assert request.url.params["output_format"] == "pcm_24000"
            return httpx.Response(200, content=b"\x01\x00")
        return httpx.Response(404, json={"detail": path})

    def fail_socket(*args: object, **kwargs: object) -> None:
        raise AssertionError("mode 08 must not open the turbo websocket")

    monkeypatch.setattr(v4_pipeline.websockets, "connect", fail_socket)
    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with TestClient(app) as client:
        app.state.stream_client = mock_client
        response = client.post(
            "/api/v4/turn",
            data={"text": "밥 먹었어요", "tts": "v4"},
        )
    asyncio.run(mock_client.aclose())

    assert response.status_code == 200
    assert seen == [
        {
            "model_id": "eleven_v4",
            "inputs": [{"text": "잘 드셨군요.", "voice_id": "voice_v4"}],
        }
    ]


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
    monkeypatch.setenv("OPENAI_REALTIME_VOICE", "cedar")
    monkeypatch.setenv(
        "OPENAI_REALTIME_INSTRUCTIONS",
        DEFAULT_REALTIME_INSTRUCTIONS,
    )
    config = get_runtime_config()

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == OPENAI_REALTIME_SECRET_URL
        assert request.headers["authorization"] == "Bearer openai_secret"
        assert request.headers["openai-project"] == "proj_test"
        assert request.headers["openai-organization"] == "org_test"
        body = json.loads(request.content)
        assert body["session"]["model"] == "gpt-realtime-2.1"
        assert body["session"]["instructions"] == DEFAULT_REALTIME_INSTRUCTIONS
        assert body["session"]["audio"]["output"]["voice"] == "cedar"
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
    monkeypatch.setenv("OPENAI_LLM_INSTRUCTIONS", "elder check-in prompt")
    monkeypatch.setenv("CUSTOM_LLM_SHARED_SECRET", "proxy_secret")

    class EventStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"event: response.completed\ndata: {\"type\":\"response.completed\"}\n\n"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer openai_secret"
        body = json.loads(request.content)
        assert body["model"] == "gpt-5.4"
        assert body["stream"] is True
        assert body["instructions"] == "elder check-in prompt"
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
            json={
                "model": "ignored",
                "instructions": "ignored ElevenLabs instructions",
                "input": "hello",
                "stream": False,
            },
        )

    asyncio.run(mock_client.aclose())
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "response.completed" in response.text
    assert response.text.endswith("data: [DONE]\n\n")


def test_v4_proxy_adds_voice_delivery_only_when_asked(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "openai_secret")
    monkeypatch.setenv("OPENAI_LLM_MODEL", "gpt-5.4")
    monkeypatch.setenv(
        "OPENAI_LLM_INSTRUCTIONS",
        "elder check-in prompt. 마크다운 표기는 사용하지 마세요.",
    )
    monkeypatch.setenv("CUSTOM_LLM_SHARED_SECRET", "proxy_secret")
    seen: list[dict] = []

    class EventStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"data: [DONE]\n\n"

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(
            200,
            headers={"Content-Type": "text/event-stream"},
            stream=EventStream(),
        )

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    headers = {"Authorization": "Bearer proxy_secret"}
    with TestClient(app) as client:
        app.state.stream_client = mock_client
        plain = client.post(
            "/v1/responses",
            headers=headers,
            json={"input": "hello", "elevenlabs_extra_body": {"other": 1}},
        )
        tagged = client.post(
            "/v1/responses",
            headers=headers,
            json={"input": "hello", "elevenlabs_extra_body": {"voice_delivery": True}},
        )
        by_path = client.post("/v4/v1/responses", headers=headers, json={"input": "hello"})

    asyncio.run(mock_client.aclose())
    assert plain.status_code == tagged.status_code == by_path.status_code == 200
    assert seen[0]["instructions"] == "elder check-in prompt. 마크다운 표기는 사용하지 마세요."
    assert "elevenlabs_extra_body" not in seen[0]
    for body in seen[1:]:
        assert "<voice_delivery>" in body["instructions"]
        assert "[playful]" in body["instructions"]
        assert "오디오 태그만 예외입니다." in body["instructions"]
        assert "elevenlabs_extra_body" not in body
        assert body["instructions"].startswith("elder check-in prompt.")
