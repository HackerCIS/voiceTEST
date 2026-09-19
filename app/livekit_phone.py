"""LiveKit Phone (mode 05): ClawOps SIP → LiveKit Cloud trunk helpers.

Counseling STT-LLM-TTS stays in the separate LiveKit Agent worker.
This module only covers credentials health, monitor tokens, and outbound
CreateSIPParticipant experiments.
"""

from __future__ import annotations

import logging
import os
import re
import uuid
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

CLAWOPS_SIP_SIGNALING_IP_DEFAULT = "34.47.65.45"
CLAWOPS_SIP_RTP_IP_DEFAULT = "34.64.155.183"


@dataclass(frozen=True)
class LiveKitPhoneConfig:
    url: str
    api_key: str
    api_secret: str
    sip_uri: str
    inbound_trunk_id: str
    outbound_trunk_id: str
    room_prefix: str
    agent_name: str
    clawops_from_number: str
    clawops_sip_endpoint_id: str
    clawops_sip_signaling_ip: str
    clawops_sip_rtp_ip: str

    @classmethod
    def from_env(cls) -> "LiveKitPhoneConfig":
        subdomain = os.getenv("LIVEKIT_SIP_SUBDOMAIN", "").strip()
        sip_uri = os.getenv("LIVEKIT_SIP_URI", "").strip()
        if not sip_uri and subdomain:
            sip_uri = f"sip:{subdomain}.sip.livekit.cloud:5061"
        return cls(
            url=os.getenv("LIVEKIT_URL", "").strip(),
            api_key=os.getenv("LIVEKIT_API_KEY", "").strip(),
            api_secret=os.getenv("LIVEKIT_API_SECRET", "").strip(),
            sip_uri=sip_uri,
            inbound_trunk_id=os.getenv("LIVEKIT_INBOUND_TRUNK_ID", "").strip(),
            outbound_trunk_id=os.getenv("LIVEKIT_OUTBOUND_TRUNK_ID", "").strip(),
            room_prefix=os.getenv("LIVEKIT_ROOM_PREFIX", "call-").strip() or "call-",
            agent_name=os.getenv("LIVEKIT_AGENT_NAME", "").strip(),
            clawops_from_number=(
                os.getenv("CLAWOPS_FROM_NUMBER", "").strip()
                or os.getenv("CLAWOPS_PHONE_NUMBER", "").strip()
            ),
            clawops_sip_endpoint_id=os.getenv("CLAWOPS_SIP_ENDPOINT_ID", "").strip(),
            clawops_sip_signaling_ip=os.getenv(
                "CLAWOPS_SIP_SIGNALING_IP", CLAWOPS_SIP_SIGNALING_IP_DEFAULT
            ).strip()
            or CLAWOPS_SIP_SIGNALING_IP_DEFAULT,
            clawops_sip_rtp_ip=os.getenv(
                "CLAWOPS_SIP_RTP_IP", CLAWOPS_SIP_RTP_IP_DEFAULT
            ).strip()
            or CLAWOPS_SIP_RTP_IP_DEFAULT,
        )

    @property
    def credentials_ready(self) -> bool:
        return bool(self.url and self.api_key and self.api_secret)

    @property
    def configured(self) -> bool:
        return self.credentials_ready and bool(self.sip_uri or self.inbound_trunk_id)

    def missing(self) -> list[str]:
        items: list[str] = []
        if not self.url:
            items.append("LIVEKIT_URL")
        if not self.api_key:
            items.append("LIVEKIT_API_KEY")
        if not self.api_secret:
            items.append("LIVEKIT_API_SECRET")
        if not self.sip_uri and not self.inbound_trunk_id:
            items.append("LIVEKIT_SIP_URI or LIVEKIT_INBOUND_TRUNK_ID")
        return items


def normalize_kr_phone(value: str) -> str:
    digits = re.sub(r"\D+", "", (value or "").strip())
    if digits.startswith("82") and len(digits) >= 11:
        digits = "0" + digits[2:]
    if not digits:
        raise ValueError("전화번호가 비어 있습니다.")
    if not digits.startswith("0"):
        raise ValueError("국내 번호는 0으로 시작해야 합니다 (예: 010…, 070…).")
    return digits


def checklist(config: LiveKitPhoneConfig | None = None) -> list[dict[str, Any]]:
    cfg = config or LiveKitPhoneConfig.from_env()
    return [
        {
            "id": "livekit_credentials",
            "label": "LiveKit URL / API Key / Secret",
            "done": cfg.credentials_ready,
            "owner": "env",
        },
        {
            "id": "livekit_sip_uri",
            "label": "LiveKit SIP URI 또는 inbound trunk id",
            "done": bool(cfg.sip_uri or cfg.inbound_trunk_id),
            "detail": cfg.sip_uri or cfg.inbound_trunk_id or None,
            "owner": "env",
        },
        {
            "id": "clawops_sip_addon",
            "label": "ClawOps SIP 트렁크 부가 활성화 (수동)",
            "done": bool(cfg.clawops_sip_endpoint_id),
            "owner": "clawops-console",
        },
        {
            "id": "clawops_route_tls",
            "label": "ClawOps 번호 라우팅 = SIP + TLS → LiveKit URI (수동)",
            "done": bool(cfg.clawops_sip_endpoint_id and cfg.sip_uri),
            "owner": "clawops-console",
        },
        {
            "id": "livekit_inbound_trunk",
            "label": "LiveKit inbound trunk numbers + allowed_addresses",
            "done": bool(cfg.inbound_trunk_id),
            "detail": (
                f"allow {cfg.clawops_sip_signaling_ip} "
                f"(RTP note {cfg.clawops_sip_rtp_ip})"
            ),
            "owner": "livekit-console",
        },
        {
            "id": "livekit_dispatch",
            "label": "LiveKit dispatch rule → 기존 Agent 워커",
            "done": bool(cfg.agent_name),
            "detail": cfg.agent_name or None,
            "owner": "livekit-console",
        },
        {
            "id": "worker_running",
            "label": "기존 LiveKit Agent 워커 프로세스 기동 (앱 밖)",
            "done": False,
            "owner": "ops-manual",
        },
        {
            "id": "outbound_trunk",
            "label": "Outbound trunk TLS (CreateSIPParticipant)",
            "done": bool(cfg.outbound_trunk_id),
            "detail": cfg.outbound_trunk_id or None,
            "owner": "livekit-console",
        },
        {
            "id": "outbound_agent",
            "label": "LIVEKIT_AGENT_NAME + 워커 기동 (아웃바운드 대화)",
            "done": bool(cfg.agent_name),
            "detail": cfg.agent_name or None,
            "owner": "env+ops",
        },
    ]


def create_monitor_token(
    *,
    room_name: str,
    identity: str | None = None,
    config: LiveKitPhoneConfig | None = None,
) -> dict[str, str]:
    cfg = config or LiveKitPhoneConfig.from_env()
    if not cfg.credentials_ready:
        raise RuntimeError("LIVEKIT_URL / LIVEKIT_API_KEY / LIVEKIT_API_SECRET 필요")
    try:
        from livekit import api
    except ImportError as exc:
        raise RuntimeError(
            'livekit-api 미설치. pip install -r requirements-livekit.txt'
        ) from exc

    identity = identity or f"lab-monitor-{uuid.uuid4().hex[:8]}"
    token = (
        api.AccessToken(cfg.api_key, cfg.api_secret)
        .with_identity(identity)
        .with_name("Voice Lab Monitor")
        .with_grants(
            api.VideoGrants(
                room_join=True,
                room=room_name,
                can_subscribe=True,
                can_publish=False,
                can_publish_data=False,
            )
        )
        .to_jwt()
    )
    return {
        "serverUrl": cfg.url,
        "roomName": room_name,
        "participantIdentity": identity,
        "participantToken": token,
    }


async def create_outbound_sip_participant(
    *,
    to_number: str,
    config: LiveKitPhoneConfig | None = None,
    room_name: str | None = None,
) -> dict[str, Any]:
    """Place an outbound SIP call and dispatch the counseling agent into the same room.

    CreateSIPParticipant alone only puts the phone into a room. Without Agent
    Dispatch, the callee hears silence. Order: dispatch worker first, then dial.
    """
    cfg = config or LiveKitPhoneConfig.from_env()
    if not cfg.credentials_ready:
        raise RuntimeError("LIVEKIT_URL / LIVEKIT_API_KEY / LIVEKIT_API_SECRET 필요")
    if not cfg.outbound_trunk_id:
        raise RuntimeError(
            "LIVEKIT_OUTBOUND_TRUNK_ID 없음. LiveKit Outbound trunk(TLS) ID를 "
            "설정하세요."
        )
    if not cfg.agent_name:
        raise RuntimeError(
            "LIVEKIT_AGENT_NAME 없음. 대화하려면 에이전트 이름"
            "(예: phone-test-openai)을 넣고, 해당 LiveKit Agent 워커를 기동하세요."
        )

    try:
        from livekit import api
    except ImportError as exc:
        raise RuntimeError(
            "livekit-api 미설치. pip install -r requirements-livekit.txt"
        ) from exc

    to_number = normalize_kr_phone(to_number)
    from_number = (
        normalize_kr_phone(cfg.clawops_from_number) if cfg.clawops_from_number else ""
    )
    room = room_name or f"{cfg.room_prefix}{uuid.uuid4().hex[:10]}"
    identity = to_number

    lk = api.LiveKitAPI(cfg.url, cfg.api_key, cfg.api_secret)
    dispatch_id = None
    try:
        # 1) Agent joins the room and waits for the SIP participant (inbound path).
        dispatch = await lk.agent_dispatch.create_dispatch(
            api.CreateAgentDispatchRequest(
                agent_name=cfg.agent_name,
                room=room,
                metadata="{}",
            )
        )
        dispatch_id = getattr(dispatch, "id", None) or getattr(
            dispatch, "dispatch_id", None
        )
        logger.info(
            "dispatched agent %s to room %s (dispatch=%s)",
            cfg.agent_name,
            room,
            dispatch_id,
        )

        # 2) Dial callee into the same room via ClawOps outbound trunk.
        request_kwargs: dict[str, Any] = {
            "sip_trunk_id": cfg.outbound_trunk_id,
            "sip_call_to": to_number,
            "room_name": room,
            "participant_identity": identity,
            "participant_name": "Callee",
            "wait_until_answered": False,
            "play_dialtone": True,
        }
        if from_number:
            request_kwargs["sip_number"] = from_number
        try:
            request = api.CreateSIPParticipantRequest(**request_kwargs)
        except TypeError:
            # Older SDKs may not accept play_dialtone / sip_number in ctor.
            request_kwargs.pop("play_dialtone", None)
            request = api.CreateSIPParticipantRequest(**{
                k: v for k, v in request_kwargs.items() if k != "sip_number"
            })
            if from_number and hasattr(request, "sip_number"):
                request.sip_number = from_number

        result = await lk.sip.create_sip_participant(request)
    finally:
        await lk.aclose()

    participant_id = getattr(result, "participant_id", None) or getattr(
        result, "participant_identity", None
    )
    sip_call_id = getattr(result, "sip_call_id", None)
    return {
        "mode": "outbound",
        "roomName": room,
        "toNumber": to_number,
        "fromNumber": from_number or None,
        "trunkId": cfg.outbound_trunk_id,
        "agentName": cfg.agent_name,
        "dispatchId": dispatch_id,
        "participantId": participant_id or identity,
        "sipCallId": sip_call_id,
        "note": (
            f"에이전트 `{cfg.agent_name}`를 room에 dispatch한 뒤 SIP 발신했습니다. "
            "워커(python agent.py dev)가 떠 있어야 대화가 됩니다. "
            "번호는 070…/010… 형식( +070 / +010 금지 ). trunk는 TLS여야 합니다."
        ),
    }


def health_payload(config: LiveKitPhoneConfig | None = None) -> dict[str, Any]:
    cfg = config or LiveKitPhoneConfig.from_env()
    missing = cfg.missing()
    try:
        import livekit.api  # noqa: F401
        sdk_installed = True
    except ImportError:
        sdk_installed = False
        if "livekit-api package" not in missing:
            missing = [*missing, "livekit-api package (pip install -r requirements-livekit.txt)"]

    return {
        "configured": cfg.configured and sdk_installed,
        "model": "LiveKit Agent · ClawOps SIP",
        "sipUri": cfg.sip_uri or None,
        "fromNumber": cfg.clawops_from_number or None,
        "inboundTrunkId": cfg.inbound_trunk_id or None,
        "outboundTrunkId": cfg.outbound_trunk_id or None,
        "agentName": cfg.agent_name or None,
        "roomPrefix": cfg.room_prefix,
        "signalingIp": cfg.clawops_sip_signaling_ip,
        "rtpIp": cfg.clawops_sip_rtp_ip,
        "sdkInstalled": sdk_installed,
        "checklist": checklist(cfg),
        "missing": missing,
    }
