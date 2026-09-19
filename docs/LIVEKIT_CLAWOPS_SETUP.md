# Mode 05 · LiveKit Phone 설정 체크리스트

전화 레이어만 ClawOps SIP, 상담 AI는 **기존 LiveKit Agent 워커**를 유지합니다.

```
휴대폰 ↔ ClawOps 070 ↔ SIP TLS:5061 → LiveKit inbound trunk
  → dispatch rule → 기존 LiveKit Agent 워커 (STT-LLM-TTS)
```

모드 04(ClawOpsAgent + OpenAI Realtime)와 **번호 라우팅이 충돌**합니다.  
같은 070 하나면 05 테스트 시 인바운드 라우팅을 **SIP**로 바꿔야 합니다.

## 당신이 할 일 (콘솔)

### A. ClawOps
1. Business(또는 유료) + **SIP 트렁크 연결** 부가 활성화
2. SIP 엔드포인트에 LiveKit Cloud URI 등록  
   예: `sip:<subdomain>.sip.livekit.cloud:5061` · transport **TLS**
3. 070 인바운드 라우팅: **SIP** → 위 엔드포인트  
   (에이전트/웹훅/소프트폰 아님)
4. (선택) 아웃바운드 공동검증용 digest 자격증명·발신번호 요청

고정 IP (LiveKit trunk `allowed_addresses`용):
- 시그널링 `34.47.65.45/32`
- RTP `34.64.155.183/32` (참고)

### B. LiveKit Cloud
1. Inbound SIP trunk: `numbers` = ClawOps가 넘기는 형식과 **완전 일치**  
   (`07052767794` vs `+827052767794` — 한 통으로 확인)
2. `allowed_addresses`에 ClawOps 시그널링 IP
3. Dispatch rule: 통화당 새 룸 + **기존 에이전트 이름**
4. (선택) Outbound trunk → ClawOps digest (CreateSIPParticipant는 미검증)

### C. 워커
기존 LiveKit Agent 워커를 기동. voiceTEST 앱이 워커를 대신 띄우지 않습니다.

### D. `.env` (voiceTEST)
`LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`,  
`LIVEKIT_SIP_URI` 또는 `LIVEKIT_SIP_SUBDOMAIN` + `LIVEKIT_INBOUND_TRUNK_ID`,  
`LIVEKIT_AGENT_NAME`, `CLAWOPS_FROM_NUMBER`, (선택) `CLAWOPS_SIP_ENDPOINT_ID`,  
`LIVEKIT_OUTBOUND_TRUNK_ID`

설치: `pip install -r requirements-livekit.txt`

## 앱에서 하는 일
- 헬스/체크리스트 표시
- 인바운드: 070 안내 + (가능하면) 모니터 룸 토큰
- 아웃바운드 PoC: `POST /api/livekit-phone/outbound` → CreateSIPParticipant

세션 배경: [livekit-clawops-session.md](./livekit-clawops-session.md)

## Mode 05 아웃바운드 (대화까지)

앱의 `POST /api/livekit-phone/outbound`는 다음 순서로 동작합니다.

1. `LIVEKIT_AGENT_NAME` 에이전트를 room에 **Agent Dispatch**
2. 같은 room으로 **CreateSIPParticipant** (ClawOps Outbound trunk, TLS)

필수 env: `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`, `LIVEKIT_OUTBOUND_TRUNK_ID`, `LIVEKIT_AGENT_NAME`, `CLAWOPS_FROM_NUMBER`  
앱 밖에서 Agent 워커(`python agent.py dev`)가 떠 있어야 대화됩니다.  
번호는 `070…` / `010…` (또는 `+82…`). `+070` / `+010` 금지. trunk Signaling은 **TLS**.


전체 구조·인/아웃 콘솔 순서: [MODE05_CLAWOPS_LIVEKIT_GUIDE.md](./MODE05_CLAWOPS_LIVEKIT_GUIDE.md)
