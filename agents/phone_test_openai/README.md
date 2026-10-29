# Mode 05 — `phone-test-openai` (Realtime 안부)

LiveKit Agent 워커. 인바운드·아웃바운드 **시스템 프롬프트는 코드** (`app/prompts/anbu_realtime.py`)에 있습니다.

## 실행

```bash
# repo root
export PYTHONPATH=.
# LIVEKIT_* / OPENAI_API_KEY / CLAWOPS_FROM_NUMBER / LIVEKIT_OUTBOUND_TRUNK_ID
cd agents/phone_test_openai
python agent.py dev
```

`LIVEKIT_AGENT_NAME` 기본값: `phone-test-openai` (Dispatch rule과 동일해야 함).

## 프롬프트 오버라이드 (선택)

| 변수 | 의미 |
|------|------|
| _(없음)_ | 코드 기본 (인·아웃 별도) |
| `OPENAI_REALTIME_INSTRUCTIONS` | 인·아웃 공통 덮어쓰기 |
| `OPENAI_REALTIME_INSTRUCTIONS_INBOUND` | 인바운드만 |
| `OPENAI_REALTIME_INSTRUCTIONS_OUTBOUND` | 아웃바운드만 |
| `ANBU_CENTER_NAME` | 센터 호칭 (기본: 복지관) |
| `ANBU_ELDER_NAME` | 어르신 호칭 |
| `ANBU_STAFF_NAME` | 담당 호칭 |

이전에 `SeniorCall/TestAgent`에서 돌리던 워커와 동일 역할입니다. 앞으로는 이 폴더를 쓰세요.
