"""Mode 05 LiveKit phone worker — OpenAI Realtime 안부콜.

Run from repo root (with livekit-agents[openai] installed):

  cd agents/phone_test_openai
  # or: PYTHONPATH=. from repo root
  python agent.py dev

Uses the same coded prompts as ClawOps / browser lab
(`app.prompts.anbu_realtime`). Optional env overrides still work.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# Repo root on sys.path so `app.prompts` resolves when run from this folder.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

load_dotenv(_REPO_ROOT / ".env")
load_dotenv(Path(__file__).resolve().parent / ".env.local", override=True)

from livekit import agents, api
from livekit.agents import Agent, AgentServer, AgentSession, room_io
from livekit.plugins import openai

from app.prompts.anbu_realtime import (
    build_instructions,
    opening_reply_instructions,
    resolve_instructions,
)

logger = logging.getLogger("phone-test")
logging.basicConfig(level=logging.INFO)

AGENT_NAME = os.getenv("LIVEKIT_AGENT_NAME", "phone-test-openai").strip() or "phone-test-openai"
OUTBOUND_TRUNK_ID = os.getenv("LIVEKIT_OUTBOUND_TRUNK_ID", "").strip()
FROM_NUMBER = os.getenv("CLAWOPS_FROM_NUMBER", "07052767794").strip()
REALTIME_VOICE = os.getenv("OPENAI_REALTIME_VOICE", "coral").strip() or "coral"


class AnbuCounselor(Agent):
    def __init__(self, *, direction: str) -> None:
        super().__init__(instructions=resolve_instructions(direction))  # type: ignore[arg-type]


server = AgentServer()


@server.rtc_session(agent_name=AGENT_NAME)
async def phone_test(ctx: agents.JobContext):
    raw = ctx.job.metadata or "{}"
    try:
        dial_info = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        dial_info = {}

    phone_number = (dial_info.get("phone_number") or "").strip() or None
    is_outbound = phone_number is not None
    direction = "outbound" if is_outbound else "inbound"
    logger.info("session direction=%s agent=%s", direction, AGENT_NAME)

    if is_outbound:
        if not OUTBOUND_TRUNK_ID:
            logger.error("LIVEKIT_OUTBOUND_TRUNK_ID 가 비어 있음")
            ctx.shutdown()
            return

        identity = phone_number
        logger.info("outbound dial start → %s (from %s)", phone_number, FROM_NUMBER)

        try:
            req = api.CreateSIPParticipantRequest(
                room_name=ctx.room.name,
                sip_trunk_id=OUTBOUND_TRUNK_ID,
                sip_call_to=phone_number,
                sip_number=FROM_NUMBER,
                participant_identity=identity,
                participant_name="어르신",
                wait_until_answered=True,
                play_dialtone=True,
            )
            await ctx.api.sip.create_sip_participant(req)
            logger.info("call answered")
        except api.SipCallError as e:
            logger.error(
                "SIP call failed: %s %s",
                getattr(e, "sip_status_code", None),
                getattr(e, "sip_status", None),
            )
            ctx.shutdown()
            return
        except Exception as e:
            logger.exception("create_sip_participant error: %s", e)
            ctx.shutdown()
            return

        participant = await ctx.wait_for_participant(identity=identity)
    else:
        logger.info("inbound wait for SIP participant")
        participant = await ctx.wait_for_participant()
        logger.info("inbound joined: %s", participant.identity)

    session = AgentSession(
        llm=openai.realtime.RealtimeModel(voice=REALTIME_VOICE),
    )

    await session.start(
        room=ctx.room,
        agent=AnbuCounselor(direction=direction),
        room_options=room_io.RoomOptions(
            audio_input=room_io.AudioInputOptions(),
        ),
    )

    await session.generate_reply(
        instructions=opening_reply_instructions(direction),  # type: ignore[arg-type]
    )


if __name__ == "__main__":
    # Touch coded prompts so import errors surface early in `dev`.
    _ = build_instructions("inbound")
    _ = build_instructions("outbound")
    agents.cli.run_app(server)
