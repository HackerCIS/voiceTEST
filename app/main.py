from __future__ import annotations

import json
import logging
import os
import secrets
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator, Literal

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

load_dotenv()

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

ELEVENLABS_TOKEN_URL = "https://api.elevenlabs.io/v1/convai/conversation/token"
OPENAI_REALTIME_SECRET_URL = "https://api.openai.com/v1/realtime/client_secrets"
OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"


@dataclass(frozen=True)
class RuntimeConfig:
    elevenlabs_agent_id: str
    elevenlabs_openai_agent_id: str
    elevenlabs_api_key: str
    openai_api_key: str
    openai_project_id: str
    openai_org_id: str
    openai_safety_identifier: str
    openai_realtime_model: str
    openai_realtime_voice: str
    openai_realtime_transcription_model: str
    openai_realtime_language: str
    openai_realtime_instructions: str
    openai_llm_model: str
    openai_prompt_id: str
    openai_prompt_version: str
    openai_prompt_variables_json: str
    custom_llm_shared_secret: str
    public_base_url: str

    @property
    def elevenlabs_is_private(self) -> bool:
        return bool(self.elevenlabs_api_key)


def get_runtime_config() -> RuntimeConfig:
    return RuntimeConfig(
        elevenlabs_agent_id=os.getenv("ELEVENLABS_AGENT_ID", "").strip(),
        elevenlabs_openai_agent_id=os.getenv(
            "ELEVENLABS_OPENAI_AGENT_ID", ""
        ).strip(),
        elevenlabs_api_key=os.getenv("ELEVENLABS_API_KEY", "").strip(),
        openai_api_key=os.getenv("OPENAI_API_KEY", "").strip(),
        openai_project_id=os.getenv("OPENAI_PROJECT_ID", "").strip(),
        openai_org_id=os.getenv("OPENAI_ORG_ID", "").strip(),
        openai_safety_identifier=os.getenv(
            "OPENAI_SAFETY_IDENTIFIER", ""
        ).strip(),
        openai_realtime_model=os.getenv(
            "OPENAI_REALTIME_MODEL", "gpt-realtime-2.1"
        ).strip(),
        openai_realtime_voice=os.getenv(
            "OPENAI_REALTIME_VOICE", "marin"
        ).strip(),
        openai_realtime_transcription_model=os.getenv(
            "OPENAI_REALTIME_TRANSCRIPTION_MODEL", "gpt-transcribe"
        ).strip(),
        openai_realtime_language=os.getenv(
            "OPENAI_REALTIME_LANGUAGE", "ko"
        ).strip(),
        openai_realtime_instructions=os.getenv(
            "OPENAI_REALTIME_INSTRUCTIONS",
            "한국어로 자연스럽고 간결하게 답하는 음성 어시스턴트입니다.",
        ).strip(),
        openai_llm_model=os.getenv("OPENAI_LLM_MODEL", "gpt-5.4").strip(),
        openai_prompt_id=os.getenv("OPENAI_PROMPT_ID", "").strip(),
        openai_prompt_version=os.getenv("OPENAI_PROMPT_VERSION", "").strip(),
        openai_prompt_variables_json=os.getenv(
            "OPENAI_PROMPT_VARIABLES_JSON", ""
        ).strip(),
        custom_llm_shared_secret=os.getenv(
            "CUSTOM_LLM_SHARED_SECRET", ""
        ).strip(),
        public_base_url=os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/"),
    )


def openai_headers(
    config: RuntimeConfig,
    *,
    include_safety_identifier: bool = False,
) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {config.openai_api_key}",
        "Content-Type": "application/json",
    }
    if config.openai_project_id:
        headers["OpenAI-Project"] = config.openai_project_id
    if config.openai_org_id:
        headers["OpenAI-Organization"] = config.openai_org_id
    if include_safety_identifier and config.openai_safety_identifier:
        headers["OpenAI-Safety-Identifier"] = config.openai_safety_identifier
    return headers


def upstream_http_error(
    provider: str,
    response: httpx.Response,
) -> HTTPException:
    logger.warning(
        "%s request failed: status=%s body=%s",
        provider,
        response.status_code,
        response.text[:500],
    )
    return HTTPException(
        status_code=502,
        detail=f"{provider} 요청에 실패했습니다 (HTTP {response.status_code}).",
    )


async def fetch_conversation_token(
    client: httpx.AsyncClient,
    *,
    agent_id: str,
    api_key: str,
) -> tuple[str, str | None]:
    """Exchange the server-side ElevenLabs key for a short-lived WebRTC token."""
    try:
        response = await client.get(
            ELEVENLABS_TOKEN_URL,
            params={"agent_id": agent_id},
            headers={"xi-api-key": api_key},
        )
        response.raise_for_status()
        payload = response.json()
    except httpx.TimeoutException as exc:
        raise HTTPException(
            status_code=504,
            detail="ElevenLabs 세션 토큰 발급 시간이 초과됐습니다.",
        ) from exc
    except httpx.HTTPStatusError as exc:
        raise upstream_http_error("ElevenLabs", exc.response) from exc
    except (httpx.RequestError, ValueError) as exc:
        logger.exception("ElevenLabs token request failed")
        raise HTTPException(
            status_code=502,
            detail="ElevenLabs 세션 토큰 응답을 처리하지 못했습니다.",
        ) from exc

    token = payload.get("token")
    if not isinstance(token, str) or not token:
        logger.error("ElevenLabs token response did not contain a token")
        raise HTTPException(
            status_code=502,
            detail="ElevenLabs 응답에 세션 토큰이 없습니다.",
        )

    conversation_id = payload.get("conversation_id")
    return token, conversation_id if isinstance(conversation_id, str) else None


def build_realtime_secret_body(config: RuntimeConfig) -> dict[str, Any]:
    audio_input: dict[str, Any] = {
        "noise_reduction": {"type": "near_field"},
        "turn_detection": {
            "type": "semantic_vad",
            "eagerness": "auto",
            "create_response": True,
            "interrupt_response": True,
        },
    }
    if config.openai_realtime_transcription_model:
        transcription: dict[str, str] = {
            "model": config.openai_realtime_transcription_model,
        }
        if config.openai_realtime_language:
            transcription["language"] = config.openai_realtime_language
        audio_input["transcription"] = transcription

    return {
        "expires_after": {"anchor": "created_at", "seconds": 60},
        "session": {
            "type": "realtime",
            "model": config.openai_realtime_model,
            "instructions": config.openai_realtime_instructions,
            "audio": {
                "input": audio_input,
                "output": {"voice": config.openai_realtime_voice},
            },
        },
    }


async def fetch_openai_realtime_secret(
    client: httpx.AsyncClient,
    config: RuntimeConfig,
) -> dict[str, Any]:
    try:
        response = await client.post(
            OPENAI_REALTIME_SECRET_URL,
            headers=openai_headers(config, include_safety_identifier=True),
            json=build_realtime_secret_body(config),
        )
        response.raise_for_status()
        payload = response.json()
    except httpx.TimeoutException as exc:
        raise HTTPException(
            status_code=504,
            detail="OpenAI Realtime 토큰 발급 시간이 초과됐습니다.",
        ) from exc
    except httpx.HTTPStatusError as exc:
        raise upstream_http_error("OpenAI Realtime", exc.response) from exc
    except (httpx.RequestError, ValueError) as exc:
        logger.exception("OpenAI Realtime client secret request failed")
        raise HTTPException(
            status_code=502,
            detail="OpenAI Realtime 토큰 응답을 처리하지 못했습니다.",
        ) from exc

    value = payload.get("value")
    if not isinstance(value, str) or not value:
        logger.error("OpenAI Realtime response did not contain a client secret")
        raise HTTPException(
            status_code=502,
            detail="OpenAI 응답에 Realtime client secret이 없습니다.",
        )
    return payload


def prompt_config(config: RuntimeConfig) -> dict[str, Any] | None:
    if not config.openai_prompt_id:
        return None

    prompt: dict[str, Any] = {"id": config.openai_prompt_id}
    if config.openai_prompt_version:
        prompt["version"] = config.openai_prompt_version
    if config.openai_prompt_variables_json:
        try:
            variables = json.loads(config.openai_prompt_variables_json)
        except json.JSONDecodeError as exc:
            raise HTTPException(
                status_code=503,
                detail="OPENAI_PROMPT_VARIABLES_JSON이 올바른 JSON이 아닙니다.",
            ) from exc
        if not isinstance(variables, dict):
            raise HTTPException(
                status_code=503,
                detail="OPENAI_PROMPT_VARIABLES_JSON은 JSON 객체여야 합니다.",
            )
        prompt["variables"] = variables
    return prompt


def custom_llm_request_is_authorized(
    request: Request,
    shared_secret: str,
) -> bool:
    authorization = request.headers.get("authorization", "")
    bearer = authorization[7:] if authorization.lower().startswith("bearer ") else ""
    candidate = bearer or request.headers.get("x-api-key", "")
    return bool(candidate) and secrets.compare_digest(candidate, shared_secret)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    standard_timeout = httpx.Timeout(15.0)
    stream_timeout = httpx.Timeout(connect=10.0, read=None, write=30.0, pool=10.0)
    async with (
        httpx.AsyncClient(timeout=standard_timeout) as api_client,
        httpx.AsyncClient(timeout=stream_timeout) as stream_client,
    ):
        app.state.api_client = api_client
        app.state.stream_client = stream_client
        yield


app = FastAPI(
    title="Voice Model Comparison Lab",
    version="0.2.0",
    lifespan=lifespan,
)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
async def health() -> dict[str, Any]:
    config = get_runtime_config()
    hybrid_configured = bool(
        config.elevenlabs_openai_agent_id
        and config.openai_api_key
        and config.custom_llm_shared_secret
    )
    proxy_url = (
        f"{config.public_base_url}/v1/responses"
        if config.public_base_url
        else None
    )
    return {
        "status": "ok",
        "authMode": "private" if config.elevenlabs_is_private else "public",
        "modes": {
            "elevenlabs": {
                "configured": bool(config.elevenlabs_agent_id),
                "model": "ElevenLabs Agent 설정값",
                "missing": []
                if config.elevenlabs_agent_id
                else ["ELEVENLABS_AGENT_ID"],
            },
            "openai_realtime": {
                "configured": bool(config.openai_api_key),
                "model": config.openai_realtime_model,
                "missing": [] if config.openai_api_key else ["OPENAI_API_KEY"],
            },
            "hybrid": {
                "configured": hybrid_configured,
                "model": config.openai_llm_model,
                "proxyUrl": proxy_url,
                "missing": [
                    name
                    for name, value in (
                        (
                            "ELEVENLABS_OPENAI_AGENT_ID",
                            config.elevenlabs_openai_agent_id,
                        ),
                        ("OPENAI_API_KEY", config.openai_api_key),
                        (
                            "CUSTOM_LLM_SHARED_SECRET",
                            config.custom_llm_shared_secret,
                        ),
                    )
                    if not value
                ],
            },
        },
    }


@app.post("/api/session")
async def create_elevenlabs_session(
    request: Request,
    mode: Literal["elevenlabs", "hybrid"] = Query("elevenlabs"),
) -> JSONResponse:
    config = get_runtime_config()
    if mode == "hybrid":
        agent_id = config.elevenlabs_openai_agent_id
        variable_name = "ELEVENLABS_OPENAI_AGENT_ID"
    else:
        agent_id = config.elevenlabs_agent_id
        variable_name = "ELEVENLABS_AGENT_ID"

    if not agent_id:
        raise HTTPException(
            status_code=503,
            detail=f"서버의 {variable_name}가 설정되지 않았습니다.",
        )

    if config.elevenlabs_is_private:
        token, conversation_id = await fetch_conversation_token(
            request.app.state.api_client,
            agent_id=agent_id,
            api_key=config.elevenlabs_api_key,
        )
        payload: dict[str, str] = {
            "mode": mode,
            "authMode": "private",
            "conversationToken": token,
        }
        if conversation_id:
            payload["conversationId"] = conversation_id
    else:
        payload = {
            "mode": mode,
            "authMode": "public",
            "agentId": agent_id,
        }

    return JSONResponse(payload, headers={"Cache-Control": "no-store"})


@app.post("/api/openai/realtime-token")
async def create_openai_realtime_token(request: Request) -> JSONResponse:
    config = get_runtime_config()
    if not config.openai_api_key:
        raise HTTPException(
            status_code=503,
            detail="서버의 OPENAI_API_KEY가 설정되지 않았습니다.",
        )

    secret_payload = await fetch_openai_realtime_secret(
        request.app.state.api_client,
        config,
    )
    session = secret_payload.get("session")
    session_id = session.get("id") if isinstance(session, dict) else None
    payload: dict[str, Any] = {
        "clientSecret": secret_payload["value"],
        "expiresAt": secret_payload.get("expires_at"),
        "model": config.openai_realtime_model,
        "voice": config.openai_realtime_voice,
    }
    if isinstance(session_id, str):
        payload["sessionId"] = session_id
    return JSONResponse(payload, headers={"Cache-Control": "no-store"})


@app.post("/responses", response_model=None, include_in_schema=False)
@app.post("/v1/responses", response_model=None)
async def openai_responses_proxy(request: Request) -> JSONResponse | StreamingResponse:
    """OpenAI-compatible SSE proxy used by the ElevenLabs Custom LLM agent.

    ElevenLabs deployments may call either the canonical ``/v1/responses``
    endpoint or append ``/responses`` to the configured server URL. Supporting
    both paths keeps the proxy compatible with either dashboard configuration.
    """
    config = get_runtime_config()
    if not config.openai_api_key:
        raise HTTPException(status_code=503, detail="OPENAI_API_KEY가 설정되지 않았습니다.")
    if not config.custom_llm_shared_secret:
        raise HTTPException(
            status_code=503,
            detail="CUSTOM_LLM_SHARED_SECRET이 설정되지 않았습니다.",
        )
    if not custom_llm_request_is_authorized(
        request, config.custom_llm_shared_secret
    ):
        raise HTTPException(status_code=401, detail="Custom LLM 인증에 실패했습니다.")

    try:
        body = await request.json()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="JSON 요청이 필요합니다.") from exc
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="JSON 객체 요청이 필요합니다.")

    body["model"] = config.openai_llm_model
    body["stream"] = True
    body.pop("elevenlabs_extra_body", None)
    body.pop("user_id", None)
    configured_prompt = prompt_config(config)
    if configured_prompt:
        body["prompt"] = configured_prompt
    if config.openai_safety_identifier:
        body["safety_identifier"] = config.openai_safety_identifier

    headers = openai_headers(config)
    headers["Accept"] = "text/event-stream"
    upstream_request = request.app.state.stream_client.build_request(
        "POST",
        OPENAI_RESPONSES_URL,
        headers=headers,
        json=body,
    )

    try:
        upstream = await request.app.state.stream_client.send(
            upstream_request,
            stream=True,
        )
    except httpx.TimeoutException as exc:
        raise HTTPException(
            status_code=504,
            detail="OpenAI LLM 연결 시간이 초과됐습니다.",
        ) from exc
    except httpx.RequestError as exc:
        logger.exception("OpenAI Responses proxy connection failed")
        raise HTTPException(
            status_code=502,
            detail="OpenAI LLM에 연결하지 못했습니다.",
        ) from exc

    if upstream.status_code >= 400:
        error_body = await upstream.aread()
        logger.warning(
            "OpenAI Responses request failed: status=%s body=%s",
            upstream.status_code,
            error_body.decode(errors="replace")[:500],
        )
        await upstream.aclose()
        return JSONResponse(
            {"error": {"message": "OpenAI LLM 요청에 실패했습니다."}},
            status_code=upstream.status_code,
        )

    async def stream_response() -> AsyncIterator[bytes]:
        saw_done = False
        trailing = b""
        try:
            async for chunk in upstream.aiter_raw():
                combined = trailing + chunk
                if b"data: [DONE]" in combined:
                    saw_done = True
                trailing = combined[-64:]
                yield chunk
            if not saw_done:
                yield b"data: [DONE]\n\n"
        finally:
            await upstream.aclose()

    return StreamingResponse(
        stream_response(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )
