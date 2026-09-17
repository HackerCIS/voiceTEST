"""Exercise real SDK audio buffering/interrupt/cleanup paths without a phone call."""

import asyncio
import base64
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("clawops.agent")
pytest.importorskip("openai")

from app.clawops_realtime import PhoneOpenAIRealtime


class Call:
    call_id = "CA_test"
    direction = "outbound"

    def __init__(self):
        self.chunks = []
        self.clears = 0

    async def send_audio(self, chunk):
        self.chunks.append(chunk)

    async def clear_audio(self):
        self.clears += 1


def audio_event(item_id, audio):
    return SimpleNamespace(
        type="response.output_audio.delta", item_id=item_id,
        delta=base64.b64encode(audio).decode(),
    )


class Connection:
    def __init__(self):
        self.session = SimpleNamespace(update=AsyncMock())
        self.response = SimpleNamespace(create=AsyncMock())
        self.input_audio_buffer = SimpleNamespace(append=AsyncMock())
        self.events = asyncio.Queue()
        self.close = AsyncMock(side_effect=self.finish)

    async def finish(self):
        self.events.put_nowait(None)
        # Let the receive loop run its finally block while close is in flight.
        await asyncio.sleep(0)

    def __aiter__(self):
        return self

    async def __anext__(self):
        event = await self.events.get()
        if event is None:
            raise StopAsyncIteration
        return event


def make_session(monkeypatch):
    session = PhoneOpenAIRealtime(api_key="test-only")
    connection = Connection()
    client = SimpleNamespace(close=AsyncMock())

    async def open_connection():
        session._client = client
        return connection

    monkeypatch.setattr(session, "_open_connection", open_connection)
    return session, connection, client


def test_interrupted_audio_cannot_reappear_and_new_reply_still_plays():
    async def run():
        session = PhoneOpenAIRealtime(api_key="test-only")
        call = Call()
        session._call = call
        await session._handle_event(audio_event("old", b"a" * 160))
        await session._handle_event(SimpleNamespace(type="input_audio_buffer.speech_started"))
        await session._handle_event(audio_event("old", b"b" * 160))
        await session._handle_event(audio_event("new", b"c" * 160))
        # A late old done must not finish the new response's playback state.
        await session._handle_event(SimpleNamespace(type="response.output_audio.done", item_id="old"))
        assert call.chunks == [b"a" * 160, b"c" * 160]
        assert call.clears == 1
        assert session._dropped_audio_chunks == 1
        assert session._playback.generating is True

    asyncio.run(run())


def test_next_response_does_not_inherit_previous_remainder_or_completed_state():
    async def run():
        session = PhoneOpenAIRealtime(api_key="test-only")
        call = Call()
        session._call = call
        await session._handle_event(audio_event("first", b"a" * 170))
        await session._handle_event(SimpleNamespace(type="response.output_audio.done", item_id="first"))
        await session._handle_event(audio_event("second", b"b" * 160))
        assert call.chunks == [b"a" * 160, b"a" * 10 + b"\xff" * 150, b"b" * 160]
        assert session._playback.generating is True
        assert session._playback.sent_chunks == 1

    asyncio.run(run())


def test_speech_after_finished_playback_is_not_counted_as_an_interruption():
    async def run():
        session = PhoneOpenAIRealtime(api_key="test-only")
        session._call = Call()
        await session._handle_event(audio_event("finished", b"a" * 160))
        await session._handle_event(SimpleNamespace(type="response.output_audio.done", item_id="finished"))
        session._latest_media_ts = 1000
        await session._handle_event(SimpleNamespace(type="input_audio_buffer.speech_started"))
        assert session._interruptions == 0

    asyncio.run(run())


def test_prewarm_keeps_audio_order_and_stop_closes_each_resource_once(monkeypatch):
    async def run():
        session, connection, client = make_session(monkeypatch)
        call = Call()
        await session.prewarm()
        connection.response.create.assert_awaited_once()
        await session._handle_event(audio_event("greeting", b"a" * 160))
        await session._handle_event(audio_event("greeting", b"b" * 160))
        assert not call.chunks
        await session.attach(call)
        await session._handle_event(audio_event("greeting", b"c" * 160))
        assert call.chunks == [b"a" * 160, b"b" * 160, b"c" * 160]
        tasks = list(session._tasks)
        await asyncio.wait_for(session.stop(), timeout=0.5)
        await session.stop()
        connection.close.assert_awaited_once()
        client.close.assert_awaited_once()
        assert all(task.done() for task in tasks)
        assert session._connection is None
        assert session._client is None

    asyncio.run(run())


@pytest.mark.parametrize("cancelled", [False, True])
def test_failed_or_cancelled_prewarm_releases_resources_before_retry(monkeypatch, cancelled):
    async def run():
        session, connection, client = make_session(monkeypatch)
        error = asyncio.CancelledError if cancelled else RuntimeError
        connection.session.update.side_effect = error("setup interrupted")
        with pytest.raises(error):
            await session.prewarm()
        connection.close.assert_awaited_once()
        client.close.assert_awaited_once()
        assert not session._tasks
        assert session._connection is None

    asyncio.run(run())


def test_phone_logs_contain_timings_without_audio_or_transcripts(monkeypatch, caplog):
    async def run():
        session = PhoneOpenAIRealtime(api_key="secret-test-key")
        session._call = Call()
        caplog.set_level(logging.INFO, logger="uvicorn.error.phone")
        await session._handle_event(SimpleNamespace(type="input_audio_buffer.speech_stopped"))
        await session._handle_event(audio_event("answer", b"not-real-private-audio" * 8))
        await session._handle_event(SimpleNamespace(type="response.done", response=SimpleNamespace(status="completed")))
        assert "speechStopToAudioMs" in caplog.text
        assert "maxDeltaGapMs" in caplog.text
        assert "secret-test-key" not in caplog.text
        assert base64.b64encode(b"not-real-private-audio" * 8).decode() not in caplog.text

    asyncio.run(run())
