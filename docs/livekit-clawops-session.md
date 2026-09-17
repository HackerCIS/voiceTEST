# LiveKit · ClawOps 전화 연동 — 세션 정리

- 일시: 2026-09-17 (목)
- 참여: 최인설 (㈜퐁 CTO) · Claude
- 맥락: 웹앱으로 운영 중인 LiveKit 기반 AI 상담 서비스를, 앱 없이 **전화**로 노인에게 제공하는 지자체 실증(올해 50명 → 2027년 200명 본사업)을 준비하면서 LiveKit 개념 정리 → 국내 전화망 연동 방식 판단 → ClawOps와의 메일 교환 해석까지 진행한 대화.

---

## 0. 한눈에 보는 결론

| 항목 | 결론 |
|---|---|
| 전화 레이어 구성 | **ClawOps SIP 트렁크 → LiveKit Cloud SIP 인바운드 트렁크**. 전화가 LiveKit 방에 참가자로 들어오므로 기존 에이전트 코드가 그대로 전화를 받음 |
| 인바운드 | ClawOps가 **운영 환경에서 실통화로 검증**했다고 회신 (LiveKit Cloud TLS 5061 엔드포인트로 라우팅 중) |
| 아웃바운드 | LiveKit `CreateSIPParticipant` → ClawOps 트렁크는 "구조상 성립하지만 미검증" → ClawOps가 테스트 자격증명 발급·공동 검증 제안. 안 되면 REST 발신 + `<Connect><Stream>` WebSocket 브리지(플랜 B) |
| 비추천 | ClawOps 자체 Voice Agent SDK / 관리형 Agent — 상담 엔진이 두 런타임으로 갈라짐 |
| 비용(공시 기준) | Business 99,000원/월 + SIP 트렁크 연결 99,000원/월 ≈ **월 20만 원 고정** + 초과 발신 60원/분 · 수신 4원/분 |

---

## 1. LiveKit 이해 — "왜 일반 API 대신 Room인가"

첫 질문은 LiveKit을 쉽게 설명하고, 일반 API가 아니라 Room을 쓰는 이유를 시각화해 달라는 것이었다. 다이어그램 3장을 담은 설명 페이지를 아티팩트로 만들었다.

- 아티팩트: https://claude.ai/artifact/Wf5NtWvQ74ghEAyQULeB8C ("왜 LiveKit Room인가")
- 그림 1 Room 구조(참가자·SFU·토큰·모델 API), 그림 2 일반 API vs Room 아키텍처, 그림 3 한 턴의 시간축 비교(순차 vs 겹침·끼어들기), 용어 10개

### 1.1 한 문장 정의
LiveKit은 실시간 음성·영상을 주고받는 **방(Room)** 을 만들어 주는 오픈소스 WebRTC 인프라이고, 그 방에 AI를 사람처럼 **참가자**로 들여보내는 Agents 프레임워크가 함께 온다. 셀프호스팅 또는 LiveKit Cloud. ChatGPT 고급 음성 모드가 이 위에 만들어진 것으로 알려져 있다.

비유: 일반 API는 "질문 보내고 답 받기", Room은 "회의실에 들어가서 계속 대화하기". AI 에이전트는 호출하는 엔드포인트가 아니라 회의실에 함께 앉아 있는 참가자다.

### 1.2 핵심 구조
- **Room**: 대화가 일어나는 방. 이름으로 식별, 참가자가 모두 나가면 닫힘.
- **Participant**: 앱·AI 에이전트·사람 상담사·전화(SIP)·녹음기(Egress)가 모두 같은 자격.
- **Track**: 참가자가 발행(publish)하는 오디오·영상 스트림. 듣고 싶은 트랙을 구독(subscribe).
- **SFU**: 소리를 섞지 않고 구독자에게 중계. 참가자가 늘어도 각자는 서버 한 곳에만 연결.
- **Token(JWT)**: 우리 백엔드가 서명해 발급하는 입장권(방 이름·권한·만료).
- **Worker / Job / AgentSession**: 에이전트 프로세스 풀 · 방 하나에 배정된 실행 단위 · 한 대화를 관리하는 객체.
- **Data channel**: 자막·상태·RPC. **Egress/Ingress**: 녹음·송출 / 외부 스트림 유입. **SIP**: 전화망 연결(수신·발신).

### 1.3 일반 API와의 차이
- 일반 API(요청·응답): 앱이 녹음 파일 업로드 → 백엔드가 STT → LLM → TTS 순차 호출 → 음성 파일 다운로드 → 재생. 말이 끝난 뒤 시작, 단계별 지연 누적, 끼어들기 불가, VAD·재시도·재접속·키 관리가 앱 몫.
- Room: 백엔드는 토큰만 발급, 앱은 WebRTC로 한 번 접속. LiveKit이 에이전트를 자동 배정(dispatch)해 방에 참가시키고, 에이전트가 서버 안에서 모델 API를 호출. 이후 오디오는 계속 흐름.
- **핵심**: LLM API는 그대로 쓴다. Room이 대체하는 것은 모델 호출이 아니라 "앱 ↔ 서버" 실시간 통로와 대화 흐름 제어다.

### 1.4 Room이 편한 여섯 가지 이유
1. **연결 품질** — WebRTC(UDP·Opus·지터 버퍼·패킷 손실 보정·적응 비트레이트). HTTP/WebSocket은 TCP여서 패킷 하나가 늦으면 뒤 오디오가 밀림(HOL blocking). LTE·지하철·와이파이 전환에서 차이.
2. **스트리밍 기본** — 말하는 동안 STT, 첫 토큰부터 TTS·재생 시작. 파일 단위 요청·응답은 구조적으로 불가.
3. **턴 감지·끼어들기** — VAD + 의미 기반 턴 감지, barge-in 처리 내장. 직접 만들면 가장 오래 걸리고 자주 어긋나는 부분.
4. **키·보안** — 앱은 수명 짧은 Room 토큰만. 모델 키는 에이전트 프로세스에만. 공급자 교체 시 앱 재배포 없음.
5. **참가자 하나 더 = 기능 하나 더** — 사람 상담사 합류, 감독자 청취, 전화(SIP) 유입, 녹음(Egress), 에이전트 간 인계가 전부 "참가자 추가".
6. **운영** — 워커 자동 배정, 네트워크 전환 시 SDK 재접속, 자막·상태 데이터 채널 동기화.

### 1.5 그래도 일반 API가 맞는 경우
- 텍스트 채팅만(REST/SSE로 충분), 비실시간 처리(녹음 후 분석, 알림 TTS 단발 재생), 운영 여력 부족(LiveKit 서버 또는 Cloud 요금, 워커 프로세스, WebRTC 디버깅이라는 새 층).
- 판단 기준: **사람처럼 말을 끊고 끼어드는 대화가 필요하면 Room, 물어보고 답 받는 구조면 API.**
- 흔한 혼합 구성: 텍스트 상담은 API, "음성으로 이야기하기" 진입 시 Room. 앱 없는 사용자는 전화(SIP)로 같은 방에.

### 1.6 참고한 최신 상태
- LiveKit Agents 1.7.x (2026-08). AgentSession, Jobs/Agent Servers, STT-LLM-TTS 파이프라인 또는 Realtime 모델(OpenAI/Gemini), 턴 감지·끼어들기, SIP 수신·발신, Agent Builder, LiveKit Inference(키 없이 모델 호출 위임), 멀티 에이전트 인계.

---

## 2. 국내 전화 연동 — ClawOps API vs SIP 판단

### 2.1 문제 정의
- LiveKit이 공식 테스트한 SIP 트렁크 업체(Twilio·Telnyx·Plivo 등)에 국내 업체가 없음.
- 후보: **ClawOps(클로옵스)** — 국내 AI 에이전트용 전화·문자 인프라.
- 질문: "ClawOps API 형태 + LiveKit" vs "ClawOps SIP + LiveKit" 중 어디로 가야 하는가.

### 2.2 ClawOps 조사 결과 (문서 기준, 2026-09-17)
- 정체: "5분 만에 시작하는 AI 에이전트 전화·문자 인프라". GCP asia-northeast3(서울) 고정 IP 운영.
- 제공: REST API(음성·SMS), 070 즉시 발급, 15xx/18xx 대표번호, 통화 녹음·전사·요약, 카카오 알림톡, WebRTC 브라우저 통화, SIP 트렁크(RFC 3261), HMAC-SHA256 웹훅, 콜플로우 빌더(IVR).
- 연동 방식 3종
  - **VoiceML**(Twilio TwiML 유사 XML, webhook 응답): Say·Play·Gather·Record·Dial·Connect·Hangup·Redirect 8개 verb. `<Connect><Stream>`으로 외부 AI 서버 연결. 문서상 "전체 검증 전".
  - **Stream**(WebSocket 미디어): G.711 μ-law 8kHz 모노, 20ms(160바이트) 프레임을 base64 JSON으로. 이벤트 `connected/start/media/mark/dtmf/stop`(서버→클라), `media/mark/clear/dtmf`(클라→서버). `clear`가 barge-in용. 플랫폼이 20ms 페이싱 정규화, 3초 무음 시 페이싱 중단. "검증 전" 표기.
  - **Voice Agent SDK**(Python `pip install clawops[agent,openai]`): SDK가 ClawOps로 WS 역접속(Control WS + 통화별 Media WS). 세션 타입 OpenAI Realtime / Gemini Realtime / Pipeline(DeepgramSTT, 다수 LLM, ElevenLabsTTS). OpenAI Realtime만 완전 검증, 나머지 프리뷰.
- **SIP 트렁크**(부가서비스): 070 인바운드를 외부 SIP URI로 직접 전달. 미디어가 ClawOps 통화 엔진을 거치지 않음. 문서의 지원 패턴 예시는 OpenAI Realtime SIP(`sip:proj_xxx@sip.api.openai.com:5061;transport=tls`)와 ElevenLabs Agents 두 가지만 — 커스텀 URI 허용 여부는 문서에 명시 없음(→ 메일로 확인, 3절).
- **네트워크 고정 IP**: SIP 시그널링 `34.47.65.45/32`(UDP 5060), RTP 미디어 `34.64.155.183/32`(UDP), 웹훅/Stream `34.64.147.48/32`(TCP 443). 시그널링과 RTP IP가 달라 둘 다 허용해야 함(한쪽만 열면 단방향 무음). 사내 PBX·FreeSWITCH·Asterisk 착신 포워딩 언급.
- 공시 요금: Trial 3일 무료 / Individual 19,000원(동시 1·번호 1) / Business 99,000원(동시 10·번호 10). AI 에이전트 부가 30–140원/분, 전사 10원/분, 요약 10원/분.
- 신문서(docs.claw-ops.com)의 관리형 Agents는 OpenAI 스택 중심(gpt-realtime, Cartesia·xAI TTS)이며 LiveKit 언급 없음. 통합 페이지는 Claude Code MCP 연동만 기술.

### 2.3 LiveKit SIP 측 조건
- "모든 SIP 프로바이더와 동작하도록 설계, 호환성 테스트는 목록 업체에 한정."
- 인바운드 트렁크: `numbers`, `allowed_addresses`(IP 허용, 활성화 필요), `allowed_numbers`, `auth_username/password`, `krisp_enabled`(노이즈 제거), 메타데이터·속성·SIP 헤더 매핑.
- 아웃바운드 트렁크: 프로바이더 주소·번호·인증. `CreateSIPParticipant`에 인라인 트렁크 가능.
- 디스패치 룰로 인바운드 통화를 방에 배정(통화당 새 방 또는 공용 방, 에이전트 지정).
- 전송 UDP/TCP/TLS, RTP/SRTP, DTMF(RFC 2833/4733), 전환(REFER 콜드·웜). 셀프호스팅 시 SIP 서비스 별도 배포.

### 2.4 세 조합 비교
| 조합 | 구조 | 평가 |
|---|---|---|
| ① ClawOps SIP + LiveKit | 070 착신 → ClawOps SIP 게이트웨이 → LiveKit SIP 인바운드 트렁크 → 디스패치 → 기존 워커 | **추천.** 홉 최소, 코드 변경 최소. 지터·DTMF·전환·Krisp·녹음을 LiveKit이 처리 |
| ② ClawOps Stream(API) + LiveKit | `<Connect><Stream>` WS ↔ 브리지 프로세스(룸 참가자) ↔ LiveKit | **플랜 B.** 브리지(μ-law↔PCM 트랜스코딩·페이싱·`clear`) 200~300줄 자체 유지, 프로토콜 "검증 전" |
| ③ ClawOps Voice Agent SDK / 관리형 Agent | ClawOps 런타임에서 STT·LLM·TTS 직접 | **비추천.** LiveKit 밖, 상담 엔진 이중화, 웹·전화 기능 격차. 데모용만 |

### 2.5 ①의 구현 순서 (제안)
1. ClawOps: SIP 트렁크 부가서비스 활성화 → SIP 엔드포인트에 LiveKit Cloud SIP URI 등록(TLS 5061) → 번호 인바운드 라우팅을 SIP로 전환.
2. LiveKit: 인바운드 트렁크(`numbers`=070, `allowed_addresses`=34.47.65.45, `krisp_enabled=true`) + 디스패치 룰(통화당 새 룸, 에이전트 이름 지정).
3. 에이전트: 참가자가 SIP이면 전화 모드 분기 — 먼저 인사(무음이면 끊음), 8kHz에 강한 STT, 턴 감지 임계 조정, DTMF 메뉴, 사람 상담사 연결(룸 참가 또는 SIP 전환).
4. 녹음·전사: (당초 우려) SIP 경로가 ClawOps 엔진을 우회하므로 LiveKit Egress로 → 3절에서 ClawOps 기록·녹음이 남는다고 확인되어 **선택 사항**으로 변경.
5. 아웃바운드: LiveKit 아웃바운드 트렁크 → ClawOps 가능 여부 확인, 불가 시 API 발신 + Stream.

### 2.6 ClawOps에 확인할 것으로 정리한 5가지
커스텀 SIP URI 허용 / 코덱(PCMU·PCMA)·TLS·SRTP / From 헤더 발신번호 형식(+82) / 아웃바운드 INVITE 수신(트렁크 인증) / SIP 라우팅 시 과금·동시통화 기준.

### 2.7 함께 고려할 것
- 070은 스팸 표시가 잦음 → 15xx/18xx 대표번호가 신뢰에 유리.
- 통화 시작 시 "AI 상담·녹음" 고지.
- 전화 8kHz는 감정 뉘앙스·한국어 인식률이 웹 Opus보다 낮음 → 전화 녹음으로 STT 벤치마크 별도 수행.
- 위기 상황에서 사람으로 넘기는 경로를 처음부터 설계에 포함.

---

## 3. ClawOps 메일 교환과 해석

### 3.1 발신 메일 요지 (㈜퐁 → ClawOps)
- 목적: 지자체 노인복지 실증. 앱 없이 AI 상담사가 전화를 걸고(아웃바운드) 받는(인바운드) 방식. **AI 코어는 자체 유지**, 화이트라벨 콜봇 불원, 전화 레이어만 외부 사용.
- 현황: STT→LLM→TTS 파이프라인을 LiveKit으로 감싸 운영, 앱/웹 서비스 중.
- 질문: ① 인바운드 070/대표번호 → SIP 트렁크 → 외부 SIP URI 라우팅·TLS 지원 ② VoiceML `<Dial><Sip>`이 UDP 전용이라는 이해가 맞는지, LiveKit Cloud(TLS)면 트렁크 라우팅을 써야 하는지 ③ 아웃바운드 권장 방법, `CreateSIPParticipant` → ClawOps 아웃바운드 트렁크 지원·검증 여부 ④ LiveKit·FreeSWITCH 등 연동 사례 ⑤ 가격(올해 50명 실증, 2027년 200명 본사업).

### 3.2 ClawOps 회신 요지
- **구성 지원**: AI 코어 유지 + 전화 레이어만 제공, 미디어를 고객사 SIP로 넘기는 형태는 **이미 운영 중인 구성**.
- **1. 인바운드 (가능)**: SIP 트렁크 연결 부가서비스 활성화 → 070 인바운드 라우팅을 고객사 SIP URI로 전환. 라우트마다 전송 `udp`/`tls` 지정. **LiveKit Cloud TLS 5061 엔드포인트(`sip:...@<프로젝트>.sip.livekit.cloud:5061`)로 라우팅하는 구성은 현재 운영 환경에서 실제 통화가 흐르고 있음.** 라우트 다중 등록(우선순위·가중치), 엔드포인트 단위 동시통화 상한. 이 경로로 넘긴 통화도 **ClawOps 쪽 통화기록·녹음은 남음**.
- **2. `<Dial><Sip>` UDP 전용 (맞음)**: `sips:`·`;transport=tls` 목적지 거절, 사설·내부 IP 거절(SSRF 방지). `<Sip>`은 통화 도중 사내 PBX로 전환하는 용도(ClawOps 엔진이 미디어 경로에 남음), 트렁크 라우팅은 착신을 통째로 넘기는 용도. `<Sip>` TLS 지원 계획 없음.
- **3. 아웃바운드 두 경로**
  - (1) **ClawOps API 주도(검증됨)**: REST 발신 → `<Connect><Stream>`으로 고객사 WebSocket에 실시간 양방향 오디오. G.711 μ-law 8kHz 모노 20ms base64 JSON. 지터 버퍼·재생 페이싱은 ClawOps가 처리.
  - (2) **LiveKit 주도(`CreateSIPParticipant` → ClawOps 트렁크, 구조상 성립·미검증)**: 게이트웨이는 표준 digest 인증 INVITE 수신(REGISTER 불필요), 발신번호가 계정 보유 070이면 발신. 입구 TLS 5061·TCP 5060. 자격증명별 허용 IP 대역·발신번호 제한 가능. 여러 고객사 PBX·소프트폰이 이 경로로 발신 중이나 **LiveKit과 짝지어 검증한 기록은 없음**. 테스트용 SIP 자격증명·번호 발급 및 공동 검증, SIP 로그로 원인 분석 제안.
- **4. 사례**: 고객사 레퍼런스 제공 불가. 공개 가이드(OpenAI Realtime 연동 — webhook 핸들러 코드, ElevenLabs Agents 연동 — 번호 import) 참고. 자체 SIP 게이트웨이 운영 시 시그널링·RTP 고정 IP 허용 필요.
- **5. 가격**: AI 부가서비스 불필요. 요금제 + SIP 트렁크 연결 + 통화량.

| 항목 | 금액 |
|---|---|
| Business 요금제 | 99,000원/월 (동시통화 10 · 번호 10 · 발신 1,000분 · 수신 10,000분 포함) |
| SIP 트렁크 연결 | 99,000원/월 |
| 동시통화 상향(선택) | 100,000원/월 · 동시통화 100건까지 |
| 초과 사용 | 발신 60원/분 · 수신 4원/분 · 번호 1,000원/개 |

  50명 실증은 Business(동시 10)로 충분, 2027년 200명은 시간대 집중도에 따라 상향. 실증 중 실측 동시통화량으로 함께 결정. 금액은 공시 기준, 조정 가능.

### 3.3 해석 — "LiveKit Cloud SIP URI를 엔드포인트로 등록 가능한가?"
**예.** 회신 1번이 그대로 확인해 준 것으로, 가능성 수준이 아니라 **운영 검증된 경로**다. 인바운드는 조합 ①로 바로 진행.

이전 판단에서 바뀐 점:
- 녹음·통화기록이 ClawOps에 남으므로 LiveKit Egress는 필수 → 선택. (상담 내용 분석은 LiveKit 트랜스크립트가 여전히 유용)
- 아웃바운드는 `CreateSIPParticipant` → ClawOps 트렁크가 미검증. ClawOps의 공동 검증 제안 수락 권장. 성공 시 발신·수신이 LiveKit 한 코드로 통합, 실패 시 발신만 Stream 브리지(플랜 B).

### 3.4 다음 단계
1. ClawOps SIP 트렁크 부가서비스 활성화 → 라우트에 LiveKit Cloud URI, 전송 `tls`, 동시통화 상한 지정.
2. LiveKit 인바운드 트렁크: `numbers`는 ClawOps가 Request-URI/To에 넣는 번호 형식과 **정확히 일치**해야 함(`07012345678` vs `+827012345678` — ClawOps에 확인). `allowed_addresses`=시그널링 IP `34.47.65.45`.
3. 디스패치 룰(통화당 새 룸 + 에이전트 지정) → 기존 워커로 인바운드 수신 테스트.
4. 아웃바운드 테스트: ClawOps digest 자격증명·발신번호 발급 → LiveKit 아웃바운드 트렁크(TLS 5061, 인증, `numbers`=070) → `CreateSIPParticipant`. LiveKit Cloud 발신 IP가 고정인지 확인, 고정이 아니면 IP 제한 없이 인증만으로 열어달라고 요청.

### 3.5 비용 추정 (공시 요금 기준, 개략)
- 고정: 99,000 + 99,000 ≈ **월 약 20만 원**.
- 노인 50명 × 주 1회 × 10분 AI 발신 ≈ 월 2,000분 → 포함 1,000분 초과 1,000분 × 60원 ≈ **+6만 원**.
- 200명 규모(≈ 월 8,000분 발신)에서는 초과 발신 요금(≈ 42만 원)이 고정비를 넘어가므로 **통화 길이·발신 빈도**가 비용을 결정. 수신(4원/분)이 발신(60원/분)보다 훨씬 저렴하다는 점도 설계에 반영할 여지.

---

## 4. 확정 사항 · 미확정 사항 · 액션

**확정(ClawOps 회신으로 확인)**
- 070 인바운드 → LiveKit Cloud SIP(TLS 5061) 라우팅 운영 검증됨.
- 라우트별 udp/tls 선택, 다중 라우트, 엔드포인트 동시통화 상한.
- SIP 경로에서도 ClawOps 통화기록·녹음 유지.
- `<Dial><Sip>`은 UDP 전용, TLS 계획 없음 → TLS 목적지는 트렁크 라우팅으로.
- 아웃바운드 게이트웨이: digest 인증 INVITE, TLS 5061/TCP 5060, 자격증명별 IP·발신번호 제한.
- 요금: Business 99,000 + SIP 트렁크 99,000 + 초과 발신 60원/분·수신 4원/분.

**미확정(테스트·확인 필요)**
- `CreateSIPParticipant` → ClawOps 아웃바운드 실통화.
- ClawOps가 보내는 착신 번호 형식(LiveKit `numbers` 매칭용)과 From 헤더 발신번호 형식.
- LiveKit Cloud 발신 IP 고정 여부(ClawOps IP 허용 정책과 맞물림).
- 코덱 협상(PCMU/PCMA 예상)·SRTP 여부 — 회신에 명시 없음.
- 전화 8kHz 환경에서 한국어 STT 인식률·감정 뉘앙스 — 실녹음으로 벤치마크 필요.

**액션**
- [ ] ClawOps에 테스트용 SIP 자격증명·번호 요청, 공동 검증 일정 잡기
- [ ] 착신 번호 형식·From 형식 질의
- [ ] SIP 트렁크 부가서비스 활성화 및 인바운드 라우트 설정
- [ ] LiveKit 인바운드 트렁크·디스패치 룰 구성, 전화 모드 분기(첫 인사·턴 감지·DTMF·사람 연결) 구현
- [ ] 대표번호(15xx/18xx) 사용 여부, 통화 시작 고지 문구, 위기 대응 인계 경로 결정
- [ ] 실증 중 동시통화·발신 분량 실측 → 2027년 요금제 결정

---

## 5. 참고 링크
- 설명 아티팩트: https://claude.ai/artifact/Wf5NtWvQ74ghEAyQULeB8C
- LiveKit Agents 문서: https://docs.livekit.io/agents/
- LiveKit SIP 개요: https://docs.livekit.io/sip/ · 인바운드 트렁크: https://docs.livekit.io/sip/trunk-inbound/
- livekit/agents 릴리스: https://github.com/livekit/agents/releases
- ClawOps: https://claw-ops.com/
- ClawOps SIP 트렁크: https://platform.claw-ops.com/docs/sip-trunk
- ClawOps 네트워크(고정 IP): https://platform.claw-ops.com/docs/network
- ClawOps Stream: https://platform.claw-ops.com/docs/build/stream · Stream 프로토콜: https://platform.claw-ops.com/docs/voiceml-stream-protocol
- ClawOps Voice Agent SDK: https://platform.claw-ops.com/docs/voice-agent
- ClawOps 신문서: https://docs.claw-ops.com
