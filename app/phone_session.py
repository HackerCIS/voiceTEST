"""One browser-controlled ClawOps phone session, including queued-call cancellation."""

from __future__ import annotations

import asyncio
import logging
import re
import secrets
from typing import Any

logger = logging.getLogger(__name__)
START_TIMEOUT = 20.0
TERMINAL_STATUSES = {"completed", "no-answer", "busy", "rejected", "canceled", "failed"}


def normalize_phone_number(value: str) -> str:
    number = re.sub(r"[\s()\-]", "", value)
    if not re.fullmatch(r"(?:0[1-9][0-9]{7,9}|\+[1-9][0-9]{7,14})", number):
        raise ValueError("받는 분의 전화번호를 확인해 주세요. 예: 010-1234-5678 또는 +82 10-1234-5678")
    return number


async def end_phone_call(call_id: str) -> None:
    # CallSession.hangup() has no transport while a call is still ringing.
    # The REST control API also cancels queued and ringing outbound calls.
    from clawops import AsyncClawOps

    async with AsyncClawOps(timeout=8.0, max_retries=0) as client:
        await client.calls.update(call_id, status="completed")


def connection_error_message(error: Exception) -> str:
    if isinstance(error, TimeoutError):
        return "ClawOps 연결 시간이 초과됐습니다. 네트워크와 계정 설정을 확인해 주세요."
    status = getattr(error, "status", None) or getattr(error, "status_code", None)
    if status in (401, 403):
        return "ClawOps 인증 또는 발신 권한을 확인해 주세요. API 키와 계정 설정을 확인해야 합니다."
    if status in (400, 404, 422):
        return "ClawOps가 전화 요청을 거절했습니다. 발신 번호와 받는 분의 번호를 확인해 주세요."
    if status == 429:
        return "ClawOps 이용 한도에 도달했습니다. 계정의 통화 한도를 확인해 주세요."
    return "ClawOps 전화 연결에 실패했습니다. 서버 로그를 확인해 주세요."


class PhoneSession:
    def __init__(self, agent: Any, from_number: str, to_number: str = "") -> None:
        self.agent = agent
        self.from_number = from_number
        self.to_number = to_number
        self.mode = "outbound" if to_number else "inbound"
        self.session_id = secrets.token_urlsafe(18)
        self.status = "connecting"
        self.error: str | None = None
        self.call: Any = None
        self.task: asyncio.Task[None] | None = None
        self._startup_finished = asyncio.Event()
        self._stop_requested = False
        agent.on("call_start")(self._call_started)
        agent.on("call_end")(self._call_ended)

    async def _call_started(self, call: Any) -> None:
        self.call = call
        if not self._stop_requested:
            self.status = "in-progress"

    async def _call_ended(self, call: Any) -> None:
        if self.mode == "inbound" and self.call is call:
            self.call = None
            if not self._stop_requested and self.status != "failed":
                self.status = "listening"

    def snapshot(self) -> dict[str, Any]:
        active = bool(self.task and not self.task.done())
        status = self.status
        if self.mode == "outbound" and self.call is not None and status not in {"failed", "stopped"}:
            status = self.call.status
        return {
            "sessionId": self.session_id,
            "mode": self.mode,
            "fromNumber": self.from_number,
            "toNumber": self.to_number or None,
            "callId": self.call.call_id if self.call else None,
            "status": status,
            "active": active,
            "error": self.error,
        }

    async def run(self) -> None:
        try:
            async with asyncio.timeout(START_TIMEOUT):
                # Do not call agent.serve(): FastAPI owns the process signals.
                await self.agent.connect()
                if self._stop_requested:
                    self.status = "stopped"
                    return
                if self.mode == "outbound":
                    self.call = await self.agent.call(self.to_number, timeout=30)
                    self.status = self.call.status
                else:
                    self.status = "listening"
            self._startup_finished.set()
            if self.mode == "outbound":
                await self.call.wait()
                self.status = self.call.status
            else:
                await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.status = "stopped"
            raise
        except Exception as error:
            self.status = "failed"
            self.error = connection_error_message(error)
            logger.exception("ClawOps phone session failed")
        finally:
            self._startup_finished.set()
            try:
                await self.agent.disconnect()
            except Exception:
                logger.exception("ClawOps phone cleanup failed")

    async def stop(self) -> None:
        if self.task is None or self.task.done():
            return
        self._stop_requested = True
        # If stop arrives during originate, wait for its call ID so we can cancel
        # the actual telephone call instead of leaving a phone ringing remotely.
        await self._startup_finished.wait()
        try:
            if self.call is not None and self.call.status not in TERMINAL_STATUSES:
                await end_phone_call(self.call.call_id)
        except Exception:
            # Keep the session available for a second stop attempt.
            self._stop_requested = False
            raise
        # The call may have ended while REST hangup was in flight. Let its
        # existing disconnect finish rather than canceling that cleanup midway.
        if self.status not in TERMINAL_STATUSES | {"failed", "stopped"}:
            self.task.cancel()
        try:
            await self.task
        except asyncio.CancelledError:
            pass
