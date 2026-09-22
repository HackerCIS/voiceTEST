"""Coded 안부 prompts for Mode 05 / ClawOps."""

import os

from app.prompts import anbu_realtime as prompts
from app.prompts.anbu_realtime import (
    DEFAULT_REALTIME_INSTRUCTIONS,
    build_instructions,
    opening_reply_instructions,
    resolve_instructions,
)


def test_inbound_and_outbound_differ() -> None:
    inbound = build_instructions("inbound")
    outbound = build_instructions("outbound")
    assert inbound != outbound
    assert "방향: inbound" in inbound
    assert "방향: outbound" in outbound
    assert "퐁이" in inbound and "퐁이" in outbound
    assert "119" in inbound
    assert "# 예시" not in inbound
    assert "사용자:" not in outbound


def test_default_matches_outbound() -> None:
    assert DEFAULT_REALTIME_INSTRUCTIONS == build_instructions("outbound")


def test_resolve_common_env_override(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_REALTIME_INSTRUCTIONS", "공통 오버라이드")
    assert resolve_instructions("inbound") == "공통 오버라이드"
    assert resolve_instructions("outbound") == "공통 오버라이드"


def test_resolve_direction_env_override(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_REALTIME_INSTRUCTIONS", raising=False)
    monkeypatch.setenv("OPENAI_REALTIME_INSTRUCTIONS_INBOUND", "인만")
    monkeypatch.setenv("OPENAI_REALTIME_INSTRUCTIONS_OUTBOUND", "아웃만")
    assert resolve_instructions("inbound") == "인만"
    assert resolve_instructions("outbound") == "아웃만"


def test_opening_reply_mentions_direction() -> None:
    assert "통화해도" in opening_reply_instructions("outbound")
    assert "도와드릴" in opening_reply_instructions("inbound")


def test_center_name_substitution(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_REALTIME_INSTRUCTIONS", raising=False)
    monkeypatch.delenv("OPENAI_REALTIME_INSTRUCTIONS_OUTBOUND", raising=False)
    monkeypatch.setenv("ANBU_CENTER_NAME", "성수복지관")
    # rebuild (module caches DEFAULT at import — call build_instructions fresh)
    text = prompts.build_instructions("outbound")
    assert "성수복지관" in text
