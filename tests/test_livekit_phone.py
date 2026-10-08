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


def _set_livekit_env(monkeypatch):
    monkeypatch.setenv("LIVEKIT_URL", "wss://example.livekit.cloud")
    monkeypatch.setenv("LIVEKIT_API_KEY", "key")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "secret")
    monkeypatch.setenv("LIVEKIT_SIP_URI", "sip:example.sip.livekit.cloud:5061")
    monkeypatch.setenv("LIVEKIT_OUTBOUND_TRUNK_ID", "ST_test")
    monkeypatch.setenv("LIVEKIT_AGENT_NAME", "phone-test-openai")
    monkeypatch.setenv("LIVEKIT_GPT_LIVE_AGENT_NAME", "phone-test-gpt-live")


def test_gpt_live_health_uses_its_own_agent_name(monkeypatch):
    _set_livekit_env(monkeypatch)
    monkeypatch.delenv("LIVEKIT_GPT_LIVE_AGENT_NAME", raising=False)
    monkeypatch.delenv("OPENAI_GPT_LIVE_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_GPT_LIVE_BACKEND_MODEL", raising=False)
    from app import livekit_phone

    missing = livekit_phone.health_payload_gpt_live()
    assert missing["configured"] is False
    assert "LIVEKIT_GPT_LIVE_AGENT_NAME" in missing["missing"]
    assert "LIVEKIT_GPT_LIVE_AGENT_NAME" not in livekit_phone.health_payload()["missing"]

    monkeypatch.setenv("LIVEKIT_GPT_LIVE_AGENT_NAME", "phone-test-gpt-live")
    ready = livekit_phone.health_payload_gpt_live()
    assert ready["agentName"] == "phone-test-gpt-live"
    assert ready["voiceModel"] == "gpt-live-1"
    assert ready["backendModel"] == "gpt-5.6-luna"
    assert ready["model"] == "gpt-live-1 · gpt-5.6-luna"
    assert ready["configured"] is ready["sdkInstalled"]
    labels = {item["id"]: item["label"] for item in ready["checklist"]}
    assert "GPT-Live" in labels["outbound_agent"]
    assert livekit_phone.health_payload()["agentName"] == "phone-test-openai"


class _Request:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class _Dispatch:
    def __init__(self, calls):
        self.calls = calls

    async def create_dispatch(self, request):
        self.calls.append(("dispatch", request.agent_name, request.metadata))
        return type("Dispatch", (), {"id": "AD_test"})()


class _Sip:
    def __init__(self, calls):
        self.calls = calls

    async def create_sip_participant(self, request):
        self.calls.append(("sip", request.room_name, request.sip_call_to))
        return type("SipResult", (), {"participant_id": "p", "sip_call_id": "c"})()


class _LiveKitAPI:
    def __init__(self, *args, **kwargs):
        self.calls = _LiveKitAPI.calls
        self.agent_dispatch = _Dispatch(self.calls)
        self.sip = _Sip(self.calls)

    async def aclose(self):
        return None


def _patch_livekit_api(monkeypatch):
    import livekit.api as lkapi

    _LiveKitAPI.calls = []
    monkeypatch.setattr(lkapi, "LiveKitAPI", _LiveKitAPI)
    monkeypatch.setattr(lkapi, "CreateAgentDispatchRequest", _Request)
    monkeypatch.setattr(lkapi, "CreateSIPParticipantRequest", _Request)
    return _LiveKitAPI.calls


def test_outbound_modes_dispatch_different_agents(monkeypatch):
    _set_livekit_env(monkeypatch)
    calls = _patch_livekit_api(monkeypatch)
    from app import livekit_phone

    realtime = asyncio.run(
        livekit_phone.create_outbound_sip_participant(to_number="01012345678")
    )
    gpt_live = asyncio.run(
        livekit_phone.create_gpt_live_outbound(to_number="01012345678")
    )

    assert realtime["agentName"] == "phone-test-openai"
    assert gpt_live["agentName"] == "phone-test-gpt-live"
    assert gpt_live["voiceModel"] == "gpt-live-1"
    assert realtime["roomName"] != gpt_live["roomName"]
    assert calls == [
        ("dispatch", "phone-test-openai", "{}"),
        ("sip", realtime["roomName"], "01012345678"),
        ("dispatch", "phone-test-gpt-live", "{}"),
        ("sip", gpt_live["roomName"], "01012345678"),
    ]


class _Named:
    def __init__(self, agent_name):
        self.agent_name = agent_name


class _AgentList(list):
    def add(self):
        item = _Named("")
        self.append(item)
        return item


class _InboundRule:
    def __init__(self, rule_id, prefix, names):
        self.sip_dispatch_rule_id = rule_id
        self.name = "clawops-inbound-to-agent"
        self.rule = type(
            "Inner",
            (),
            {
                "dispatch_rule_individual": type("Individual", (), {"room_prefix": prefix})(),
                "HasField": staticmethod(lambda name: name == "dispatch_rule_individual"),
            },
        )()
        self.room_config = type("Room", (), {"agents": _AgentList(_Named(name) for name in names)})()


class _InboundApi:
    rules = []
    calls = []

    def __init__(self, *args, **kwargs):
        self.sip = self

    async def list_dispatch_rule(self, request):
        return type("Listed", (), {"items": list(_InboundApi.rules)})()

    async def update_dispatch_rule(self, rule_id, rule):
        names = [agent.agent_name for agent in rule.room_config.agents]
        _InboundApi.calls.append((rule_id, names))
        return rule

    async def aclose(self):
        return None


def test_inbound_modes_point_the_shared_rule_at_different_workers(monkeypatch):
    _set_livekit_env(monkeypatch)
    import livekit.api as lkapi

    rule = _InboundRule("SDR_test", "call-", ["phone-test-openai"])
    _InboundApi.rules = [rule]
    _InboundApi.calls = []
    monkeypatch.setattr(lkapi, "LiveKitAPI", _InboundApi)
    from app import livekit_phone

    gpt_live = asyncio.run(livekit_phone.prepare_gpt_live_inbound())
    realtime = asyncio.run(livekit_phone.prepare_realtime_inbound())

    assert gpt_live["agentName"] == "phone-test-gpt-live"
    assert gpt_live["previousAgentName"] == "phone-test-openai"
    assert gpt_live["voiceModel"] == "gpt-live-1"
    assert realtime["agentName"] == "phone-test-openai"
    assert realtime["previousAgentName"] == "phone-test-gpt-live"
    assert _InboundApi.calls == [
        ("SDR_test", ["phone-test-gpt-live"]),
        ("SDR_test", ["phone-test-openai"]),
    ]


def test_gpt_live_outbound_does_not_fall_back_to_realtime_agent(monkeypatch):
    _set_livekit_env(monkeypatch)
    monkeypatch.setenv("LIVEKIT_GPT_LIVE_AGENT_NAME", "")
    from app import livekit_phone

    with pytest.raises(RuntimeError, match="LIVEKIT_GPT_LIVE_AGENT_NAME"):
        asyncio.run(livekit_phone.create_gpt_live_outbound(to_number="01012345678"))
