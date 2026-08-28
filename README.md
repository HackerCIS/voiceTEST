# Voice Model Comparison Lab

같은 브라우저 화면에서 세 가지 음성 Agent 구성을 바꿔가며 비교하는 FastAPI 프로젝트입니다.

| 버튼 | 음성·턴 제어 | 답변 생성 |
|---|---|---|
| ElevenLabs | ElevenLabs Agent | 현재 Agent 설정 LLM |
| OpenAI Realtime | OpenAI Realtime | `gpt-realtime-2.1` |
| ElevenLabs + OpenAI | ElevenLabs Agent | FastAPI 프록시를 통한 `gpt-5.4` |

화면에는 연결 시간, 평균 응답 지연, 사용자 턴 수와 OpenAI Realtime 토큰 사용량이 표시됩니다.

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
OPENAI_REALTIME_VOICE=marin
OPENAI_REALTIME_TRANSCRIPTION_MODEL=gpt-transcribe
OPENAI_REALTIME_LANGUAGE=ko
OPENAI_REALTIME_INSTRUCTIONS=한국어로 자연스럽고 간결하게 답하는 음성 어시스턴트입니다.
```

FastAPI가 표준 OpenAI key로 단기 client secret을 발급하고, 브라우저는 그 secret으로 OpenAI Realtime WebRTC에 연결합니다. 표준 API key는 브라우저에 노출되지 않습니다.

### ElevenLabs Agent + OpenAI GPT-5.4

```dotenv
ELEVENLABS_OPENAI_AGENT_ID=agent_hybrid_xxx
OPENAI_LLM_MODEL=gpt-5.4
CUSTOM_LLM_SHARED_SECRET=충분히_긴_임의의_비밀값
PUBLIC_BASE_URL=https://your-domain-or-ngrok.app
```

OpenAI Platform에 저장한 Prompt를 사용한다면 다음 값도 입력합니다.

```dotenv
OPENAI_PROMPT_ID=pmpt_xxx
OPENAI_PROMPT_VERSION=1
OPENAI_PROMPT_VARIABLES_JSON={"brand":"Example"}
```

Prompt ID를 비워두면 ElevenLabs Agent가 보내는 system prompt와 대화 내역을 그대로 GPT-5.4에 전달합니다.

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
```

## 4. 공정하게 비교하는 방법

1. 세 모드에 같은 역할, 금지사항, 답변 길이 지침을 넣습니다.
2. 조용한 동일 공간과 같은 마이크에서 같은 질문 세트를 사용합니다.
3. 각 모드를 최소 10회 반복하고 평균뿐 아니라 느린 응답도 기록합니다.
4. 첫 응답 지연, 끼어들기, 한국어 고유명사 인식, 답변 정확도와 분당 비용을 함께 비교합니다.

브라우저의 `평균 응답`은 사용자 발화 종료 또는 텍스트 전송부터 Agent 응답 시작까지의 근사치입니다. 네트워크·브라우저 이벤트 시점이 서로 달라 절대적인 벤치마크보다는 동일 환경의 상대 비교용으로 사용하세요.

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
