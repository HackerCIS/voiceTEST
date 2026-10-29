"""Mode 07: ElevenLabs speech-to-text, OpenAI reply, Eleven v4 Turbo speech."""

from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx
import websockets
from fastapi import HTTPException
from websockets.exceptions import ConnectionClosed

logger = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "v4_turbo.txt"
OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
ELEVENLABS_STT_URL = "https://api.elevenlabs.io/v1/speech-to-text"
ELEVENLABS_TURBO_WS_URL = "wss://api.elevenlabs.io/v1/text-to-dialogue/stream-input"
ELEVENLABS_DIALOGUE_URL = "https://api.elevenlabs.io/v1/text-to-dialogue"
STT_MODEL_ID = "scribe_v2"
TTS_TURBO_MODEL_ID = "eleven_v4_turbo"
TTS_V4_MODEL_ID = "eleven_v4"
TTS_MODELS = {
    "turbo": TTS_TURBO_MODEL_ID,
    "v4": TTS_V4_MODEL_ID,
}
TTS_OUTPUT_FORMAT = "pcm_24000"
MAX_HISTORY = 40

GREETING_CUE = (
    "지금 대화를 먼저 시작하세요. 어르신께 짧고 따뜻한 안부 인사를 건네고, "
    "오늘 기분이나 상태를 묻는 질문 하나만 하세요. "
    "특정 문구를 그대로 반복하지 말고 자연스럽게 표현을 달리하세요. "
    "오디오 태그는 voice_delivery 규칙이 맞을 때만 넣으세요."
)

_sessions: dict[str, list[dict[str, str]]] = {}


@dataclass(frozen=True)
class V4Turn:
    session_id: str
    user_text: str
    agent_text: str
    audio: bytes


def clear_sessions() -> None:
    _sessions.clear()


def load_prompt() -> str:
    """The mode 07 system prompt. Edit app/prompts/v4_turbo.txt."""
    text = PROMPT_PATH.read_text(encoding="utf-8").strip()
    if not text:
        raise HTTPException(
            status_code=500,
            detail="app/prompts/v4_turbo.txt 가 비어 있습니다.",
        )
    return text


def response_text(payload: dict[str, Any]) -> str:
    chunks: list[str] = []
    for item in payload.get("output") or []:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for part in item.get("content") or []:
            if isinstance(part, dict) and part.get("type") in {"output_text", "text"}:
                chunks.append(str(part.get("text") or ""))
    text = "".join(chunks).strip()
    if not text and isinstance(payload.get("output_text"), str):
        text = payload["output_text"].strip()
    return text


def _fail(provider: str, response: httpx.Response) -> HTTPException:
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


async def transcribe(
    client: httpx.AsyncClient,
    *,
    api_key: str,
    audio: bytes,
    filename: str,
    content_type: str,
) -> str:
    response = await client.post(
        ELEVENLABS_STT_URL,
        headers={"xi-api-key": api_key},
        data={"model_id": STT_MODEL_ID, "language_code": "ko"},
        files={"file": (filename or "speech.webm", audio, content_type or "audio/webm")},
    )
    if response.status_code >= 400:
        raise _fail("ElevenLabs", response)
    text = response.json().get("text")
    return text.strip() if isinstance(text, str) else ""


async def complete(
    client: httpx.AsyncClient,
    *,
    api_key: str,
    model: str,
    instructions: str,
    messages: list[dict[str, str]],
    project_id: str = "",
    org_id: str = "",
) -> str:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    if project_id:
        headers["OpenAI-Project"] = project_id
    if org_id:
        headers["OpenAI-Organization"] = org_id
    response = await client.post(
        OPENAI_RESPONSES_URL,
        headers=headers,
        json={
            "model": model,
            "instructions": instructions,
            "input": messages,
            "stream": False,
        },
    )
    if response.status_code >= 400:
        raise _fail("OpenAI", response)
    text = response_text(response.json())
    if not text:
        raise HTTPException(status_code=502, detail="OpenAI가 답 문장을 만들지 않았습니다.")
    return text


def turbo_socket_url() -> str:
    """Eleven v4 Turbo is the text-to-dialogue WebSocket, not the batch v4 call."""
    query = urlencode(
        {
            "model_id": TTS_TURBO_MODEL_ID,
            "output_format": TTS_OUTPUT_FORMAT,
            "language_code": "ko",
        }
    )
    return f"{ELEVENLABS_TURBO_WS_URL}?{query}"


def resolve_tts_model(choice: str) -> str:
    model_id = TTS_MODELS.get(choice.strip())
    if model_id is None:
        raise HTTPException(status_code=400, detail="음성 모델은 turbo 또는 v4여야 합니다.")
    return model_id


async def speak_v4(
    client: httpx.AsyncClient,
    *,
    api_key: str,
    voice_id: str,
    text: str,
) -> bytes:
    response = await client.post(
        ELEVENLABS_DIALOGUE_URL,
        params={"output_format": TTS_OUTPUT_FORMAT},
        headers={"xi-api-key": api_key, "Content-Type": "application/json"},
        json={
            "model_id": TTS_V4_MODEL_ID,
            "inputs": [{"text": text, "voice_id": voice_id}],
        },
    )
    if response.status_code >= 400:
        raise _fail("ElevenLabs", response)
    if not response.content:
        raise HTTPException(status_code=502, detail="변환된 음성이 없습니다.")
    return response.content


async def speak(
    client: httpx.AsyncClient,
    *,
    api_key: str,
    voice_id: str,
    text: str,
    tts_model: str = TTS_TURBO_MODEL_ID,
) -> bytes:
    if tts_model == TTS_V4_MODEL_ID:
        return await speak_v4(
            client,
            api_key=api_key,
            voice_id=voice_id,
            text=text,
        )
    audio = bytearray()
    try:
        async with websockets.connect(
            turbo_socket_url(),
            additional_headers={"xi-api-key": api_key},
            open_timeout=20,
            close_timeout=5,
        ) as socket:
            await socket.send(json.dumps({"voices": [voice_id]}))
            await socket.send(
                json.dumps(
                    {
                        "inputs": [{"text": text, "voice_id": voice_id}],
                        "flush": True,
                        "close_socket": True,
                    }
                )
            )
            while True:
                message = json.loads(await socket.recv())
                if message.get("error"):
                    detail = message.get("message") or message.get("error")
                    raise HTTPException(
                        status_code=502,
                        detail=f"ElevenLabs {tts_model} 음성 합성에 실패했습니다. {detail}",
                    )
                chunk = message.get("audio")
                if isinstance(chunk, str) and chunk:
                    audio.extend(base64.b64decode(chunk))
                if message.get("is_final"):
                    break
    except ConnectionClosed as exc:
        if not audio:
            raise HTTPException(
                status_code=502,
                detail=f"ElevenLabs {tts_model} 연결이 음성 없이 끊겼습니다.",
            ) from exc
    if not audio:
        raise HTTPException(status_code=502, detail="변환된 음성이 없습니다.")
    return bytes(audio)


async def run_turn(
    client: httpx.AsyncClient,
    *,
    session_id: str,
    api_key: str,
    openai_api_key: str,
    voice_id: str,
    llm_model: str,
    tts_model: str = TTS_TURBO_MODEL_ID,
    project_id: str = "",
    org_id: str = "",
    text: str = "",
    audio: bytes = b"",
    filename: str = "speech.webm",
    content_type: str = "audio/webm",
    greet: bool = False,
) -> V4Turn:
    history = _sessions.setdefault(session_id, [])
    instructions = load_prompt()
    user_text = ""
    if greet:
        cue = GREETING_CUE
    else:
        user_text = text.strip()
        if not user_text and audio:
            user_text = await transcribe(
                client,
                api_key=api_key,
                audio=audio,
                filename=filename,
                content_type=content_type,
            )
        if not user_text:
            raise HTTPException(status_code=400, detail="말씀을 알아듣지 못했습니다.")
        cue = user_text

    agent_text = await complete(
        client,
        api_key=openai_api_key,
        model=llm_model,
        instructions=instructions,
        messages=[*history, {"role": "user", "content": cue}],
        project_id=project_id,
        org_id=org_id,
    )
    pcm = await speak(
        client,
        api_key=api_key,
        voice_id=voice_id,
        text=agent_text,
        tts_model=tts_model,
    )
    if not greet:
        history.append({"role": "user", "content": user_text})
    history.append({"role": "assistant", "content": agent_text})
    del history[:-MAX_HISTORY]
    return V4Turn(session_id, "" if greet else user_text, agent_text, pcm)
