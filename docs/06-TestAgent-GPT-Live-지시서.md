# TestAgent 지시서 — Mode 06 GPT-Live 워커

voiceTEST 모드 06의 전화 경로(ClawOps SIP → LiveKit Room → Agent Dispatch)는 이미 앱에 있다. TestAgent가 할 일은 **그 방에 들어가는 상담 워커를 GPT-Live로 만드는 것**이다. voiceTEST를 수정하지 않는다.

기준일: 2026-10-08. 음성 모델 `gpt-live-1`. 백엔드 기본값 `gpt-5.6-luna`. 워커 이름 `phone-test-gpt-live`.

## 1. 하지 말 것

- `Coding/SeniorCare/TestAgent`(모드 05, `gpt-realtime-2.1`)를 수정하지 않는다. 작업 위치는 `orca/workspaces/TestAgent/Gpt-live구현/`이다.
- 이 폴더의 `agent.py`는 아직 `openai.realtime.RealtimeModel`이고 기본 이름이 `phone-test-openai`다. 이름을 그대로 두면 모드 05 작업을 가로챈다. 기본값을 `phone-test-gpt-live`로 바꾼다.
- voiceTEST에 GPT-Live WebSocket, 프롬프트, 도구 실행을 넣지 않는다. 앱은 dispatch와 SIP만 한다.
- `session.say()`로 원고를 읽히지 않는다. GPT-Live는 원고를 그대로 말하지 않고, TTS를 붙여도 모델이 동시에 말한다.
- Realtime용 긴 프롬프트를 `Agent.instructions` 하나에 넣지 않는다. 음성 채널과 백엔드 채널이 나뉜다.
- 모드를 바꾸려고 세션 도중에 voice, instructions, delegation을 교체하지 않는다. 세션을 새로 만든다.
- 통화가 안 된다고 `RealtimeModel`로 되돌리지 않는다. 권한·모델 이름 오류는 로그에 남기고 멈춘다.
- `@function_tool`을 1차에 넣지 않는다. client delegation도 1차 범위가 아니다.

## 2. 이미 정해진 계약 (voiceTEST가 이렇게 호출한다)

모드 06 발신 `POST /api/gpt-live-phone/outbound`의 순서:

1. Room 이름 `call-` + 임의 10자.
2. `CreateAgentDispatch(agent_name=LIVEKIT_GPT_LIVE_AGENT_NAME, metadata="{}")`.
3. 그 다음 `CreateSIPParticipant`. 앱이 전화를 건다.

그래서 voiceTEST가 건 전화에서 워커가 보는 metadata는 `{}`이다. `phone_number`가 없다. 이 경우 워커는 **다시 걸지 않고** SIP 참가자가 들어올 때까지 기다린다. 모드 05 `agent.py`의 `else` 분기(wait_for_participant)와 같다.

`dial.py`처럼 metadata에 `{"phone_number":"010…"}`를 넣는 경로만 워커가 `CreateSIPParticipant`를 호출한다. 앱 발신과 `dial.py` 발신을 한 통화에서 둘 다 하면 전화가 두 번 나간다.

착신(070)은 LiveKit Inbound Dispatch rule의 `agentName`이 하나다. voiceTEST에서 모드 06 **착신 안내**를 누르면 그 규칙을 `phone-test-gpt-live`로 바꾸고, 모드 05 **인바운드 점검**을 누르면 `phone-test-openai`로 되돌린다. 발신은 이 규칙과 무관하게 그 통화만 해당 워커를 dispatch한다.

`@server.rtc_session(agent_name=...)`의 문자열은 `phone-test-gpt-live`와 글자가 같아야 한다. voiceTEST `.env`의 `LIVEKIT_GPT_LIVE_AGENT_NAME`도 같은 값이다.

## 3. 듀얼채널을 코드에 어떻게 둘지

GPT-Live는 방이나 SIP가 두 개인 구조가 아니다. 한 LiveKit 세션 안에 채널이 둘이다.

| 채널 | 넣는 곳 | 내용 |
|---|---|---|
| 음성 `gpt-live-1` | `Agent.instructions` | 말투, 언제 스스로 답하는지, 언제 백엔드에 넘기는지, 기다리는 동안 뭐라고 말할지 |
| 백엔드 Responses | `GPTLiveModel(responses_options={"instructions": ...})` | 안부 항목, 위험 판단, 이관, 종료 정리. 음성 모델이 말로 옮길 짧은 결과만 돌려준다 |

음성 모델은 도구 목록을 보지 못한다. 도구 이름이나 JSON 스키마를 음성 프롬프트에 적지 않는다.

플러그인 차이 (LiveKit OpenAI GPT-Live 플러그인 기준):

- 턴 시작·종료는 서버가 한다. `generate_reply()`는 응답을 만들지 않고 commentary를 추가한다. 모델이 거절할 수 있다. 10초 안에 말이 없으면 `SpeechHandle`이 `RealtimeError`로 끝난다.
- 끼어들기는 모델이 결정한다. LiveKit turn detection 옵션으로 바꾸지 못한다.
- 사용자가 말을 끊었을 때 **재생만** 멈추려면 `AgentSession`에 VAD를 직접 넣는다. 이 모델은 세션의 기본 VAD를 뺀다. VAD가 없으면 모델이 스스로 멈출 때까지 재생이 이어진다.
- VAD가 재생을 잘라도 모델 문맥에는 잘리기 전 문장이 남는다. 잘라서 고칠 수 없다.
- instructions 두 벌 모두 세션 시작 후에 바꿀 수 없다. 인바운드/아웃바운드는 세션을 만들기 전에 고른다.

## 4. 환경 변수 (워커 `.env.local`)

모드 05 워커와 **프로세스를 따로** 띄운다. 같은 LiveKit URL·키·트렁크를 써도 된다. 에이전트 이름만 다르다.

| 변수 | 값 |
|---|---|
| `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` | 모드 05와 같은 프로젝트 |
| `LIVEKIT_AGENT_NAME` | `phone-test-gpt-live` |
| `LIVEKIT_OUTBOUND_TRUNK_ID` | `dial.py` 경로에서만 필요. 앱 발신에는 워커가 트렁크를 안 쓴다 |
| `CLAWOPS_FROM_NUMBER` | `dial.py` 경로의 발신 표시. `070…` / `010…`. `+070` 금지 |
| `OPENAI_API_KEY` | GPT-Live가 열린 프로젝트 키 |
| `OPENAI_GPT_LIVE_MODEL` | `gpt-live-1` |
| `OPENAI_GPT_LIVE_BACKEND_MODEL` | `gpt-5.6-luna` |
| `OPENAI_GPT_LIVE_VOICE` | 기본 `marin`. 세션 시작 후 변경 불가. `coral`이 되는지는 한 통으로 확인하고, 안 되면 `marin`으로 둔다 |
| `ANBU_CENTER_NAME`, `ANBU_ELDER_NAME`, `ANBU_STAFF_NAME` | 모드 05와 같은 호칭. 기본 복지관 / 어르신 / 담당 선생님 |

선택 덮어쓰기. 둘 중 하나만 넣지 않는다. 음성용과 백엔드용을 서로 다른 변수로 둔다.

- `OPENAI_GPT_LIVE_VOICE_INSTRUCTIONS` — 음성 채널 전체 교체
- `OPENAI_GPT_LIVE_BACKEND_INSTRUCTIONS` — 백엔드 채널 전체 교체

`OPENAI_REALTIME_INSTRUCTIONS`를 읽어서 음성 채널에 넣지 않는다. 그 변수는 모드 05의 한 덩어리 프롬프트다.

## 5. 의존성

`pyproject.toml`은 이미 `livekit-agents[openai]>=1.8.2`이다. 구현 전에 이 import가 되는지 확인한다.

```python
from livekit.plugins.openai.realtime import GPTLiveModel
```

`ImportError`이면 GPT-Live가 들어 있는 `livekit-agents`로 올린다. LiveKit 문서의 설치 줄은 `uv add "livekit-agents[openai]~=1.8"`이다.

전화 끼어들기용 VAD:

```python
from livekit.plugins import silero
```

import가 실패하면 `livekit-agents`의 silero extra(또는 `livekit-plugins-silero`)를 추가한다. VAD 없이 1차 통화를 끝내지 않는다.

## 6. `agent.py`에서 바꿀 부분

입장·대기·`dial.py` 발신 분기는 모드 05와 같게 유지한다. 세션 생성만 바꾼다.

```python
AGENT_NAME = os.getenv("LIVEKIT_AGENT_NAME", "phone-test-gpt-live").strip() or "phone-test-gpt-live"
VOICE = os.getenv("OPENAI_GPT_LIVE_VOICE", "marin").strip() or "marin"
LIVE_MODEL = os.getenv("OPENAI_GPT_LIVE_MODEL", "gpt-live-1").strip() or "gpt-live-1"
BACKEND_MODEL = os.getenv("OPENAI_GPT_LIVE_BACKEND_MODEL", "gpt-5.6-luna").strip() or "gpt-5.6-luna"

class AnbuCounselor(Agent):
    def __init__(self, *, direction: str) -> None:
        super().__init__(instructions=resolve_voice_instructions(direction))

# SIP 참가자 대기 또는 dial.py 발신이 끝난 뒤:
session = AgentSession(
    vad=silero.VAD.load(),
    llm=openai.realtime.GPTLiveModel(
        model=LIVE_MODEL,
        voice=VOICE,
        delegation="responses",
        responses_options={
            "model": BACKEND_MODEL,
            "instructions": resolve_backend_instructions(direction),
        },
    ),
)
await session.start(
    room=ctx.room,
    agent=AnbuCounselor(direction=direction),
    room_options=room_io.RoomOptions(audio_input=room_io.AudioInputOptions()),
)

async def _log_usage() -> None:
    logger.info("gpt-live usage: %s", session.usage)

ctx.add_shutdown_callback(_log_usage)

handle = session.generate_reply(instructions=opening_reply_instructions(direction))
await handle
if handle.exception() is not None:
    logger.warning("opening commentary was declined: %s", handle.exception())
```

방향은 세션을 만들기 **전에** 정한다. metadata에 `phone_number`가 있으면 outbound, 없으면 inbound(앱 발신도 이쪽).

`generate_reply` 실패는 통화 종료 사유가 아니다. 음성 프롬프트에 첫 인사 규칙이 있으므로, commentary를 거절해도 상대가 말하면 모델이 받을 수 있다. 거절은 경고 로그만 남긴다.

## 7. 프롬프트 — `prompts.py`를 이렇게 나눈다

지금 `BASE_INSTRUCTIONS` 전체를 음성에 복사하지 않는다. 아래 초안을 코드 기본값으로 둔다. 호칭은 기존 `_center()`, `_elder()`, `_staff()`를 쓴다.

### 7.1 음성 채널 (`resolve_voice_instructions`)

공통:

```text
당신은 {center}의 전화 안부 도우미 "퐁이"입니다. 사람이 아닙니다. 물어보면 AI 도우미라고 말합니다.
한국어만 씁니다. 존댓말, 따뜻하고 또박또박, 한 번에 한두 문장. 질문은 한 번에 하나.
상대가 말을 끊으면 바로 멈추고 듣습니다.

직접 답합니다: 인사, 짧은 맞장구, 통화 가능 여부, 다시 걸 시간.
백엔드에 넘깁니다: 아픈지·위험한지 판단, 도움을 사람에게 넘길지, 통화를 어떻게 끝낼지.
넘기는 동안에는 "잠깐 생각해 볼게요"처럼 짧게 말하고 기다립니다. 추측해서 진단하지 않습니다.

하지 않습니다: 진단, 약, 병원 가라/가지 마라, 주민번호·계좌·비밀번호, 방문 시각 확정, 판매, 119를 대신 건다고 약속.
위험해 보이면 이렇게만 말합니다: "많이 힘드신 것 같아요. 지금 바로 119에 전화해 주세요. 저는 {staff}께도 알려드릴게요."
백엔드가 준 문장 중 JSON이나 INTERNAL로 시작하는 줄은 읽지 않습니다.
```

아웃바운드에 덧붙인다:

```text
지금은 우리가 건 전화입니다. 연결되면 먼저 "{elder}, {center}에서 안부 전화 드린 퐁이예요. 지금 통화 괜찮으세요?"에 가깝게 짧게 인사합니다.
바쁘시면 언제 다시 걸지 묻고 끝냅니다. 괜찮으시면 컨디션, 식사, 혼자 계신지를 하나씩 묻고, 필요하면 백엔드에 넘긴 뒤 "{staff}께 전해드릴게요"로 정리합니다.
```

인바운드에 덧붙인다:

```text
지금은 상대가 먼저 건 전화입니다. 받으면 "여보세요, {center} 안부 전화예요. 퐁입니다. 무엇을 도와드릴까요?"에 가깝게 짧게 말합니다.
본인인지 가족인지 정도만 확인합니다. 자격·신청 방법은 "읍면동 주민센터나 담당 복지관에 문의해 주세요" 한 줄로 끝내고, 약속은 잡지 않습니다.
```

`opening_reply_instructions`는 유지한다. 다만 이것은 `generate_reply` commentary이지 강제 대사가 아니다. 문장을 따옴표로 읽으라고 쓰지 말고, "한두 문장으로 인사하고 통화 가능 여부를 묻는다"처럼 쓴다.

### 7.2 백엔드 채널 (`resolve_backend_instructions`)

```text
당신은 전화 중인 음성 도우미가 넘긴 일을 처리합니다. 통화 상대에게 직접 말하지 않습니다.
음성 도우미가 그대로 풀어 말할 수 있는 짧은 한국어 결과만 돌려줍니다. 목록, 제목, 마크다운, JSON을 넣지 않습니다.

확인할 것: 지금 위험한지, 식사·컨디션, 외출·낙상·혼자 계신지, 사람 연결이 필요한지.
위험 신호: 숨이 참, 가슴 아픔, 쓰러짐, 출혈, 심한 통증, 극단적 표현, 학대, 의식 흐림, 가스·화재.
하나라도 있으면 결과에 "119를 권하고 담당 선생님께 넘긴다"만 적습니다. 119를 대신 건다고 쓰지 않습니다.
도움 요청·방문·가사는 시각을 확정하지 말고 "담당 선생님께 전달"로 적습니다.
정보가 부족하면 음성 도우미가 물어볼 질문 하나만 적습니다.

통화가 마무리로 넘어오면 결과 마지막에 한 줄만 덧붙입니다.
INTERNAL {"ok":"true|false|unknown","mood":"good|ok|low|distress","meal":"yes|no|unknown","need_help":true|false,"risk":"none|watch|high","handoff":true|false,"notes":"한줄"}
이 줄은 로그용이다. 음성으로 읽지 말라고 이미 음성 쪽에 지시되어 있다.
```

방향별 한 줄만 추가한다. 아웃바운드는 "우리가 건 안부 전화", 인바운드는 "상대가 건 전화. 가족 말은 진단하지 않는다".

1차에서는 이 JSON을 저장소에 넣지 않는다. 워커 로그에 대화가 남으면 충분하다. 영속 로깅은 후속이다.

## 8. `dial.py`와 README

`dial.py`의 `agent_name="phone-test-openai"`를 `phone-test-gpt-live`로 바꾼다. 방 이름과 metadata 형식은 유지한다. 이 경로는 워커가 직접 거는 경로다. voiceTEST 모드 06 버튼을 누를 때는 `dial.py`를 돌리지 않는다.

README 첫 문단을 Mode 06 GPT-Live 워커로 고친다. 실행은 모드 05 워커와 동시에 가능하다.

```bash
# .env.local 의 LIVEKIT_AGENT_NAME=phone-test-gpt-live
python agent.py dev
```

로그에 `agent=phone-test-gpt-live`가 보여야 한다. `phone-test-openai`가 보이면 잘못된 프로세스다.

## 9. 끝난 것으로 볼 기준

1. `GPTLiveModel` import가 된다. 계정에 GPT-Live 권한이 없으면 그 오류를 남기고 Realtime으로 대체하지 않는다.
2. 워커 프로세스가 `phone-test-gpt-live`로 등록된다. 모드 05 워커는 `phone-test-openai`로 계속 떠 있을 수 있다.
3. voiceTEST 모드 06에서 번호를 넣고 **GPT-Live 발신**을 누르면, 워커 로그에 해당 room이 보이고 metadata에 `phone_number`가 없다. 워커는 SIP 참가자를 기다린다.
4. 전화를 받으면 짧은 한국어 인사가 나온다. 첫 commentary가 거절돼도 상대 발화 후에는 대답한다.
5. 상대가 말하는 동안 에이전트 음성이 스피커에서 멈춘다. VAD가 붙어 있어야 한다.
6. 위험 표현("숨이 차요")에는 119를 권하고, 진단이나 대신 출동한다는 말은 하지 않는다.
7. 종료 로그에 `session.usage`가 남는다. 음성은 시간, 백엔드는 토큰으로 따로 찍힌다.
8. 같은 시각에 voiceTEST 모드 05 발신은 `phone-test-openai` 로그만 남긴다. 06 워커 로그에 그 room이 들어가면 이름 충돌이다.

착신 시험은 3번이 된 다음에 한다. voiceTEST 모드 06에서 착신 안내를 누른 뒤 070으로 걸어 "여보세요, … 퐁입니다"에 가까운 인사가 나오면 된다. 모드 05 인바운드 점검을 누르면 착신 규칙이 `phone-test-openai`로 돌아간다. 확인 없이 규칙을 지우지 않는다.
