from app.livekit_phone import LiveKitPhoneConfig, checklist, normalize_kr_phone


def test_normalize_kr_phone_strips_and_localizes():
    assert normalize_kr_phone("010-1234-5678") == "01012345678"
    assert normalize_kr_phone("+82 10-1234-5678") == "01012345678"


def test_checklist_marks_missing_credentials(monkeypatch):
    monkeypatch.delenv("LIVEKIT_URL", raising=False)
    monkeypatch.delenv("LIVEKIT_API_KEY", raising=False)
    monkeypatch.delenv("LIVEKIT_API_SECRET", raising=False)
    cfg = LiveKitPhoneConfig.from_env()
    items = {item["id"]: item for item in checklist(cfg)}
    assert items["livekit_credentials"]["done"] is False


import asyncio
import pytest


def test_outbound_requires_agent_name(monkeypatch):
    monkeypatch.setenv("LIVEKIT_URL", "wss://example.livekit.cloud")
    monkeypatch.setenv("LIVEKIT_API_KEY", "key")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "secret")
    monkeypatch.setenv("LIVEKIT_OUTBOUND_TRUNK_ID", "ST_test")
    monkeypatch.delenv("LIVEKIT_AGENT_NAME", raising=False)
    from app import livekit_phone

    with pytest.raises(RuntimeError, match="LIVEKIT_AGENT_NAME"):
        asyncio.run(
            livekit_phone.create_outbound_sip_participant(to_number="01012345678")
        )
