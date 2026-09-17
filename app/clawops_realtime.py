"""ClawOps 0.56 Realtime fixes and content-free phone timing logs.

The SDK exposes no Realtime event callback, so this small subclass uses its
_handle_event / _cleanup hooks. Keep compatibility tests with the optional SDK.
Audio remains PCMU; ClawOps continues to own playback pacing.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from typing import Any

from clawops.agent import OpenAIRealtime

# Uvicorn configures this logger; plain CLI runs inherit basicConfig instead.
logger = logging.getLogger("uvicorn.error.phone")


class PhoneOpenAIRealtime(OpenAIRealtime):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._interrupted_items: deque[str] = deque(maxlen=32)
        self._reset_diagnostics()

    def _reset_diagnostics(self) -> None:
        self._speech_stopped_at: float | None = None
        self._last_input_at: float | None = None
        self._last_delta_at: float | None = None
        self._max_input_gap_ms = 0.0
        self._max_input_send_ms = 0.0
        self._max_delta_gap_ms = 0.0
        self._response_audio_bytes = 0
        self._interruptions = 0
        self._dropped_audio_chunks = 0
        self._stop_logged = False

    def _log_timing(self, phase: str, **values: Any) -> None:
        # Never log phone numbers, transcripts, instructions, audio, or API keys.
        logger.info("[PHONE-RT] %s", json.dumps({
            "phase": phase,
            "callId": getattr(self._call, "call_id", "prewarm"),
            **values,
        }, ensure_ascii=False))

    async def prewarm(self) -> None:
        # start() can retry after a failed prewarm, or reuse an inbound session.
        if self._connection is not None or self._client is not None or self._tasks:
            await self.stop()
        self._interrupted_items.clear()
        self._playback = None
        self._reset_diagnostics()
        started = time.monotonic()
        try:
            await super().prewarm()
        except BaseException:
            await self.stop()
            raise
        self._log_timing("openai_ready", elapsedMs=round((time.monotonic() - started) * 1000))

    async def attach(self, call: Any) -> None:
        await super().attach(call)
        self._log_timing("attached", direction=call.direction)

    async def feed_audio(self, audio: bytes, timestamp: int) -> None:
        started = time.monotonic()
        if self._last_input_at is not None:
            self._max_input_gap_ms = max(self._max_input_gap_ms, (started - self._last_input_at) * 1000)
        self._last_input_at = started
        await super().feed_audio(audio, timestamp)
        self._max_input_send_ms = max(self._max_input_send_ms, (time.monotonic() - started) * 1000)

    async def _handle_event(self, event: Any) -> None:
        event_type = event.type
        now = time.monotonic()
        item_id = getattr(event, "item_id", None)

        if event_type in {"response.output_audio.delta", "response.output_audio.done"}:
            # An interrupted response can still have deltas in flight. The SDK
            # otherwise recreates playback after clear_audio() and replays them.
            if item_id and item_id in self._interrupted_items:
                if event_type.endswith(".delta"):
                    self._dropped_audio_chunks += 1
                return

        if event_type == "input_audio_buffer.speech_started":
            if self._playback and self._playback.item_id:
                self._interrupted_items.append(self._playback.item_id)
                if self._playback.generating or self._playback.elapsed_ms(self._latest_media_ts) < self._playback.total_audio_ms:
                    self._interruptions += 1
            self._speech_stopped_at = None
            self._log_timing("speech_started", interruptions=self._interruptions)
        elif event_type == "input_audio_buffer.speech_stopped":
            self._speech_stopped_at = now
            self._log_timing("speech_stopped")
        elif event_type == "response.created":
            self._last_delta_at = None
            self._max_delta_gap_ms = 0.0
            self._response_audio_bytes = 0
        elif event_type == "response.output_audio.delta":
            # A tool continuation/new response may arrive without speech_started.
            # Do not inherit a completed response's remainder/generating state.
            if self._playback and item_id and item_id != self._playback.item_id:
                self._playback = None
            if self._last_delta_at is None:
                delay = (now - self._speech_stopped_at) * 1000 if self._speech_stopped_at is not None else None
                self._log_timing("first_audio", speechStopToAudioMs=round(delay) if delay is not None else None)
                self._speech_stopped_at = None
            else:
                self._max_delta_gap_ms = max(self._max_delta_gap_ms, (now - self._last_delta_at) * 1000)
            self._last_delta_at = now
            padding = len(event.delta) - len(event.delta.rstrip("="))
            self._response_audio_bytes += len(event.delta) * 3 // 4 - padding
        elif event_type == "response.done":
            self._log_timing(
                "response_done",
                status=event.response.status,
                generatedAudioMs=round(self._response_audio_bytes / 8),
                maxDeltaGapMs=round(self._max_delta_gap_ms),
                maxInputGapMs=round(self._max_input_gap_ms),
                maxInputSendMs=round(self._max_input_send_ms),
                droppedAudioChunks=self._dropped_audio_chunks,
            )
        await super()._handle_event(event)

    async def _close_resources(self) -> None:
        # stop() and the receive-loop finally block can run concurrently. Detach
        # first so they never both close the same WebSocket, and also release
        # the SDK's HTTP client. A slow peer can still hit the close timeout.
        connection, self._connection = self._connection, None
        client, self._client = self._client, None
        try:
            if connection is not None:
                try:
                    await asyncio.wait_for(connection.close(), timeout=2.0)
                except Exception as error:
                    self._log_timing("close_error", errorType=type(error).__name__)
        finally:
            if client is not None:
                await client.close()

    async def _cleanup(self) -> None:
        try:
            await self._close_resources()
        finally:
            self._close_llm_span()

    async def stop(self) -> None:
        try:
            await self._close_resources()
        finally:
            await super().stop()
        if not self._stop_logged:
            self._stop_logged = True
            self._log_timing("stopped", interruptions=self._interruptions, droppedAudioChunks=self._dropped_audio_chunks)
