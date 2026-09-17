# Voice Model Comparison Lab

같은 브라우저 화면에서 세 가지 음성 Agent 구성을 비교하고, ClawOps 실제 전화도 테스트하는 FastAPI 프로젝트입니다.

| 버튼 | 음성·턴 제어 | 답변 생성 |
|---|---|---|
| ElevenLabs | ElevenLabs Agent | 현재 Agent 설정 LLM |
| OpenAI Realtime | OpenAI Realtime | `gpt-realtime-2.1` |
| ElevenLabs + OpenAI | ElevenLabs Agent | FastAPI 프록시를 통한 `gpt-5.4` |
| ClawOps Phone | ClawOps 070 전화 | OpenAI Realtime |

화면에는 연결 시간, 평균 응답 지연, 사용자 턴 수와 OpenAI Realtime 토큰 사용량이 표시됩니다.
브라우저 음성 모드의 턴별 응답 지연은 누적·비교할 수 있습니다. ClawOps 전화 모드는 브라우저 마이크·텍스트 입력과 응답 지연 측정을 지원하지 않습니다.

## 1. 환경 변수

필요한 변수는 기존 `.env`와 [.env.example](./.env.example)에 선언되어 있습니다. API 키는 브라우저로 전달되지 않습니다.

### ElevenLabs 현재 Agent

```dotenv
ELEVENLABS_AGENT_ID=agent_xxx
ELEVENLABS_API_KEY=sk_xxx
```

`ELEVENLABS_API_KEY`는 Agent의 `Security → Enable authentication`을 켠 경우 필요합니다.

### OpenAI 공통 설정

```dotenv
OPENAI_API_KEY=sk-proj-xxx
OPENAI_PROJECT_ID=proj_xxx
OPENAI_ORG_ID=org_xxx
```

Project 전용 API key라면 `OPENAI_PROJECT_ID`와 `OPENAI_ORG_ID`는 대개 생략할 수 있습니다. 여러 organization/project를 쓰는 legacy key라면 입력합니다.

### OpenAI Realtime

```dotenv
OPENAI_REALTIME_MODEL=gpt-realtime-2.1
OPENAI_REALTIME_VOICE=cedar
OPENAI_REALTIME_TRANSCRIPTION_MODEL=gpt-transcribe
OPENAI_REALTIME_LANGUAGE=ko
OPENAI_REALTIME_INSTRUCTIONS="어르신들에게 따뜻한 안부 인사 전화를 걸어 드리는 역할입니다. ..."
```

전체 기본 프롬프트는 `.env.example`에 들어 있습니다. FastAPI가 표준 OpenAI
key로 단기 client secret을 발급하고, 브라우저는 그 secret으로 OpenAI Realtime
WebRTC에 연결합니다. 서버의 `session.created` 준비 완료 이벤트를 받은 뒤 브라우저가
`response.create`를 한 번 보내 AI의 첫 안부 인사를 생성합니다. 첫 인사가 끝날 때까지
마이크 입력을 잠시 멈춰 VAD가 첫 응답을 끊지 않도록 합니다. 표준 API key는 브라우저에
노출되지 않습니다.

### ElevenLabs Agent + OpenAI GPT-5.4

```dotenv
ELEVENLABS_OPENAI_AGENT_ID=agent_hybrid_xxx
OPENAI_LLM_MODEL=gpt-5.4
OPENAI_LLM_INSTRUCTIONS="당신은 ElevenLabs 음성 에이전트의 대화를 담당하는 GPT-5.4입니다. ..."
CUSTOM_LLM_SHARED_SECRET=충분히_긴_임의의_비밀값
PUBLIC_BASE_URL=https://your-domain-or-ngrok.app
```

OpenAI Platform에 저장한 Prompt를 사용한다면 다음 값도 입력합니다.

```dotenv
OPENAI_PROMPT_ID=pmpt_xxx
OPENAI_PROMPT_VERSION=1
OPENAI_PROMPT_VARIABLES_JSON={"brand":"Example"}
```

전체 기본 프롬프트는 `.env.example`에 들어 있습니다. 서버는
`OPENAI_LLM_INSTRUCTIONS`를 GPT-5.4의 `instructions`로 설정하고, ElevenLabs
Agent가 보내는 대화 내역은 그대로 전달합니다. Prompt ID는 OpenAI Platform에
저장된 Prompt를 추가로 사용할 때만 입력합니다.

## 2. Hybrid Agent 한 번만 설정하기

현재 Agent와 hybrid 구성을 동시에 보존하려면 ElevenLabs에서 현재 Agent를 복제합니다.

1. 복제한 Agent의 ID를 `ELEVENLABS_OPENAI_AGENT_ID`에 입력합니다.
2. 복제 Agent의 LLM에서 `Custom LLM`을 선택합니다.
3. API 형식은 `Responses API`를 선택합니다.
4. Server URL을 `https://공개주소/v1/responses`로 설정합니다. ElevenLabs UI가
   선택한 API 형식에 따라 `/responses`를 자동으로 덧붙이는 경우에는
   `https://공개주소/v1`을 입력합니다. 프록시는 호환성을 위해
   `/v1/responses`와 `/responses`를 모두 받습니다.
5. Model ID는 `gpt-5.4`로 설정합니다. 서버에서도 `OPENAI_LLM_MODEL` 값으로 강제됩니다.
6. API key에는 `.env`의 `CUSTOM_LLM_SHARED_SECRET`과 동일한 값을 Secret으로 등록합니다.
7. Agent를 Publish합니다.

ElevenLabs 서버가 Custom LLM을 호출하므로 localhost 주소는 사용할 수 없습니다. 로컬 실험에서는 FastAPI 실행 후 ngrok 같은 터널을 사용할 수 있습니다.

```bash
ngrok http 8000
```

출력된 HTTPS 주소를 `PUBLIC_BASE_URL`과 ElevenLabs Custom LLM Server URL에 사용합니다. `CUSTOM_LLM_SHARED_SECRET`은 외부인이 프록시를 OpenAI 무단 호출용으로 쓰지 못하게 막습니다.

## 3. 실행

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

<http://127.0.0.1:8000>을 열고 모드 버튼을 선택한 뒤 `대화 시작`을 누릅니다.

설정 상태 확인:

```bash
curl http://127.0.0.1:8000/api/health
```

테스트:

```bash
pip install -r requirements-dev.txt
pytest -q
node --test tests/latency.test.mjs
```

## 4. 응답 레이턴시 비교 화면

1. 엔진을 선택하고 **대화 시작**을 누릅니다.
2. 마이크로 질문하거나 **공통 테스트 문장**을 보냅니다. 문장은 엔진을 바꿔도 유지됩니다.
3. **첫 음성 응답까지**에서 대기 시간을 실시간으로 확인합니다. 첫 음성 시작 신호가 오면 측정값이 확정됩니다.
4. 대화를 종료하고 다른 엔진에서 같은 질문을 반복합니다. **모델별 응답 지연**에 평균·최근·중앙값·P95·표본 수가 누적됩니다.
5. **턴별 측정 기록**에서 최근 20개를 확인하거나 **JSON 내보내기**로 전체 기록을 저장합니다. 초기화는 대화 종료 후 가능하며 **되돌리기**로 복구할 수 있습니다.

측정은 브라우저의 단조 시계인 `performance.now()`를 사용합니다. 입력 기준이 다른 값은 별도 탭에서 집계합니다.

| 비교 탭 | 시작 시점 | 끝 시점 |
|---|---|---|
| 음성 | OpenAI `input_audio_buffer.speech_stopped` 수신 / ElevenLabs VAD 점수가 발화(≥0.6)에서 침묵(≤0.35)으로 전환된 시점 | OpenAI `output_audio_buffer.started` 수신 / ElevenLabs `onModeChange`의 `speaking` 전환 |
| 텍스트 | 브라우저에서 텍스트 전송 직전 | 위와 같은 첫 음성 시작 신호 |
| 전사 · 참고 | ElevenLabs의 발화 종료 신호가 없을 때 사용자 전사 수신 | ElevenLabs의 첫 `speaking` 전환 |

전사 메시지만 도착해서는 음성 응답이 완료된 것으로 기록하지 않습니다. OpenAI WebRTC는 오디오 델타가 아닌 [오디오 버퍼 시작 이벤트](https://developers.openai.com/api/reference/resources/realtime/server-events)를 사용합니다. ElevenLabs는 현재 사용 중인 [JavaScript SDK](https://elevenlabs.io/docs/eleven-agents/libraries/java-script)의 음성 모드와 VAD 콜백을 사용합니다. 발화 종료 기준을 확인할 수 없는 값은 음성 통계에 섞지 않습니다.

첫 인사는 표본으로 만들지 않습니다. 응답 전에 발화를 재개하거나, 연결을 종료하거나, 응답 오류·45초 초과가 발생한 턴은 사유를 기록하고 통계에서 제외합니다. 음성이 시작된 뒤 전사만 도착하여 시작 시점을 복원할 수 없으면 `시작 기준 없음`으로 제외합니다. 같은 입력/응답 이벤트는 중복 집계하지 않으며, 종료한 세션의 늦은 이벤트도 새 세션에 반영하지 않습니다. 음성이 이미 시작된 뒤의 끼어들기는 확정된 첫 응답 시간을 유지합니다.

모델명·입력 기준·시간·결과만 브라우저 `localStorage`에 최근 600개까지 저장합니다. 대화 내용과 API 키는 측정 기록에 저장하지 않습니다. 새로고침과 엔진 전환 후에도 기록이 유지되며, 저장을 사용할 수 없는 브라우저에서는 현재 화면에서 측정하고 JSON으로 내보낼 수 있습니다. 통계 카드는 현재 설정된 모델만 집계하고 변경 전 모델의 기록은 내역과 내보내기에 보존합니다. P95는 nearest-rank 방식이며 표본이 적으면 최댓값과 같을 수 있습니다.

## 5. 공정하게 비교하는 방법

1. 세 모드에 같은 역할, 금지사항, 답변 길이 지침을 넣습니다.
2. 조용한 동일 공간과 같은 마이크에서 같은 질문 세트를 사용합니다.
3. 각 모드를 최소 10회 반복하고 평균뿐 아니라 느린 응답도 기록합니다.
4. 첫 응답 지연, 끼어들기, 한국어 고유명사 인식, 답변 정확도와 분당 비용을 함께 비교합니다.

측정값은 네트워크·음성 처리 경로를 포함한 **브라우저 관측 기준의 근사 지연**입니다. 실제 스피커 재생 시각이나 순수 LLM 연산 시간과 다르며, 공급자마다 발화 종료 및 음성 시작 이벤트의 전달 시점도 다릅니다. 동일 환경·동일 입력 기준의 상대 비교용으로 사용하세요. ElevenLabs Agent의 내부 LLM 변경은 현재 표시되는 Agent 설정 모델명만으로 구분할 수 없으므로 Agent 설정을 바꿨다면 기록을 내보낸 뒤 초기화해서 비교하세요.

## 연결 구조

```text
1) ElevenLabs
Browser ── WebRTC ──> ElevenLabs Agent (ASR + LLM + TTS)

2) OpenAI Realtime
Browser ── WebRTC ──> OpenAI gpt-realtime-2.1
              ▲
       FastAPI가 단기 client secret 발급

3) ElevenLabs + OpenAI
Browser ── WebRTC ──> ElevenLabs Agent (ASR + turn + TTS)
                              │ text/SSE
                              ▼
                    FastAPI /v1/responses
                              │
                              ▼
                        OpenAI GPT-5.4
```

## 공식 문서

- [OpenAI GPT-Realtime-2.1](https://developers.openai.com/api/docs/models/gpt-realtime-2.1)
- [OpenAI Realtime WebRTC](https://developers.openai.com/api/docs/guides/realtime-webrtc)
- [OpenAI Realtime client secrets](https://developers.openai.com/api/reference/resources/realtime/subresources/client_secrets/methods/create)
- [ElevenLabs JavaScript SDK](https://elevenlabs.io/docs/eleven-agents/libraries/java-script)
- [ElevenLabs Custom LLM](https://elevenlabs.io/docs/eleven-agents/customization/llm/custom-llm)

## 5. ClawOps Trial phone (070, no SIP)

Available through the **ClawOps Phone** mode in the browser lab, or as a standalone module that uses
**ClawOpsAgent + OpenAI Realtime** so you can receive (and optionally place)
real KR 070 calls during a ClawOps Trial. SIP trunks / LiveKit / ngrok are
**out of scope** for this path — the agent SDK keeps a websocket to ClawOps.

### Trial limits (as of ClawOps Trial docs)

| Item | Limit |
|---|---|
| Duration | 3 days |
| Card | Not required |
| Numbers | 1 |
| Concurrent calls | 1 |
| Outbound | ~10 minutes total |
| Inbound | Unlimited within the trial |

### Env vars

Documented in [`.env.example`](./.env.example):

```dotenv
CLAWOPS_API_KEY=
CLAWOPS_ACCOUNT_ID=
CLAWOPS_FROM_NUMBER=070xxxxxxxx
CLAWOPS_TEST_TO_NUMBER=010xxxxxxxx   # optional outbound target

# Reused from the OpenAI Realtime browser lab:
OPENAI_API_KEY=
OPENAI_REALTIME_INSTRUCTIONS=...
OPENAI_REALTIME_VOICE=cedar
OPENAI_REALTIME_LANGUAGE=ko
OPENAI_REALTIME_MODEL=gpt-realtime-2
```

### Install (optional dependency)

```bash
pip install -r requirements-clawops.txt
# or: pip install "clawops[agent,openai]"
# or: pip install ".[clawops]"
```

The base `requirements.txt` / FastAPI browser lab stays unchanged if you skip this.

### Inbound (call your Trial 070)

```bash
python -m app.clawops_phone
# equivalent: python scripts/clawops_trial_phone.py
```

Leave the process running, then dial `CLAWOPS_FROM_NUMBER` from a mobile phone.
No public URL or SIP registration is required.

### Outbound test

```bash
python -m app.clawops_phone --to 01012345678
# or set CLAWOPS_TEST_TO_NUMBER and:
python -m app.clawops_phone --outbound-only
```

Outbound burns the Trial’s short outbound budget — prefer inbound for most demos.

### Scope notes

- Does **not** modify or break the FastAPI browser lab (`uvicorn app.main:app`).
- Does **not** set up ClawOps SIP endpoints, LiveKit, or BYOC trunks.
