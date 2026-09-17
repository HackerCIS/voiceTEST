"""ClawOps Trial phone path: ClawOpsAgent + OpenAI Realtime (no SIP trunk).

Inbound: ``python -m app.clawops_phone`` then dial the Trial 070 number.
Outbound: ``python -m app.clawops_phone --to 010...`` or set CLAWOPS_TEST_TO_NUMBER.

Agent construction is shared with the FastAPI browser lab in ``app.main``.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import shlex
import sys
from pathlib import Path

from dotenv import load_dotenv

# Repo-root .env (same convention as app.main)
_REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_REPO_ROOT / ".env")

logger = logging.getLogger("clawops_phone")

# Keep elder-care defaults aligned with app.main / .env.example
DEFAULT_REALTIME_INSTRUCTIONS = """어르신들에게 따뜻한 안부 인사 전화를 걸어 드리는 역할입니다.

부드럽고 다정하며 친근한 말투로 대화해 주세요. 한 번에 짧은 문장으로, 기다리지 않게 빠르게 이야기하세요. 어르신이 불편한 점이나 도움이 필요한지 간단히 물어봐 주세요.

(실제 대화는 곧고 따뜻하게, 5~7턴의 짧은 왕복 대화로 진행됩니다.)

# 참고사항

- 너무 길거나 어려운 말은 사용하지 마세요.
- 밝고 정감 있게, 천천히 또박또박 말해 주세요.
- 건강, 식사, 생활 편의 등에 대해 간단히 안부를 묻고, 대화 흐름을 자연스럽게 이어가세요."""


class ClawOpsConfigurationError(RuntimeError):
    """A phone setup error that callers can report without exiting the server."""


def _require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ClawOpsConfigurationError(f"Missing required env var: {name}")
    return value


def _optional_env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip() or default


def _build_realtime(**kwargs):
    # Keep the optional SDK import lazy so the browser-only lab still starts.
    from app.clawops_realtime import PhoneOpenAIRealtime

    return PhoneOpenAIRealtime(**kwargs)


def build_agent(*, mode: str = "inbound"):
    """Construct ClawOpsAgent + OpenAIRealtime from environment."""
    try:
        from clawops.agent import ClawOpsAgent

        from_number = _require_env("CLAWOPS_FROM_NUMBER")
        # CLAWOPS_API_KEY / CLAWOPS_ACCOUNT_ID are read by the SDK from the environment.
        _require_env("CLAWOPS_API_KEY")
        _require_env("CLAWOPS_ACCOUNT_ID")
        _require_env("OPENAI_API_KEY")

        instructions = _optional_env(
            "OPENAI_REALTIME_INSTRUCTIONS",
            DEFAULT_REALTIME_INSTRUCTIONS,
        )
        voice = _optional_env("OPENAI_REALTIME_VOICE", "cedar")
        language = _optional_env("OPENAI_REALTIME_LANGUAGE", "ko")
        model = _optional_env("OPENAI_REALTIME_MODEL", "gpt-realtime-2.1")

        eagerness = "low"
        if mode == "outbound":
            eagerness = _optional_env("CLAWOPS_OUTBOUND_VAD_EAGERNESS", "high").lower()
            if eagerness not in {"low", "medium", "high", "auto"}:
                raise ClawOpsConfigurationError(
                    "CLAWOPS_OUTBOUND_VAD_EAGERNESS는 low, medium, high, auto 중 하나여야 합니다."
                )

        session = _build_realtime(
            system_prompt=instructions,
            voice=voice,
            language=language,
            model=model,
            turn_detection={
                "type": "semantic_vad",
                "eagerness": eagerness,
                "create_response": True,
                "interrupt_response": True,
            },
        )
        agent = ClawOpsAgent(
            from_=from_number,
            session=session,
            prewarm_enabled=True,
        )
        return agent, from_number
    except ImportError as exc:
        # OpenAIRealtime also checks its optional dependencies in its constructor.
        command = shlex.join([
            sys.executable, "-m", "pip", "install", "-r",
            str(_REPO_ROOT / "requirements-clawops.txt"),
        ])
        raise ClawOpsConfigurationError(
            "ClawOps 전화용 패키지가 설치되지 않았거나 불완전합니다. "
            f"서버와 같은 Python 환경에 설치한 뒤 다시 시작해 주세요:\n{command}"
        ) from exc


async def serve_inbound() -> None:
    agent, from_number = build_agent()
    logger.info(
        "ClawOps inbound listening on %s (Trial: call this 070 number). "
        "SIP/LiveKit not used.",
        from_number,
    )
    await agent.serve()


async def place_outbound(to_number: str) -> None:
    agent, from_number = build_agent(mode="outbound")
    logger.info("ClawOps outbound: %s -> %s", from_number, to_number)
    try:
        call = await agent.call(to_number)
        logger.info("Outbound call queued; waiting until hangup...")
        await call.wait()
        logger.info("Outbound call finished.")
    finally:
        await agent.disconnect()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="ClawOps Trial phone (ClawOpsAgent + OpenAI Realtime, no SIP).",
    )
    parser.add_argument(
        "--to",
        dest="to_number",
        default=None,
        help="Outbound destination (E.164 or KR local). "
        "Defaults to CLAWOPS_TEST_TO_NUMBER when set.",
    )
    parser.add_argument(
        "--outbound-only",
        action="store_true",
        help="Place one outbound call and exit (do not serve inbound).",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Debug logging.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    to_number = (args.to_number or os.getenv("CLAWOPS_TEST_TO_NUMBER", "")).strip()

    try:
        if args.outbound_only or to_number:
            if not to_number:
                raise ClawOpsConfigurationError(
                    "Outbound requested but no number given. "
                    "Pass --to or set CLAWOPS_TEST_TO_NUMBER."
                )
            asyncio.run(place_outbound(to_number))
            return

        asyncio.run(serve_inbound())
    except ClawOpsConfigurationError as exc:
        # Only the standalone CLI owns the process exit status.
        raise SystemExit(str(exc)) from None


if __name__ == "__main__":
    main(sys.argv[1:])
