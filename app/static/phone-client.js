export function normalizePhoneNumber(value) {
  const number = value.replace(/[\s()-]/g, "");
  if (!/^(?:0[1-9][0-9]{7,9}|\+[1-9][0-9]{7,14})$/.test(number)) {
    throw new Error("받는 분의 전화번호를 확인해 주세요. 예: 010-1234-5678 또는 +82 10-1234-5678");
  }
  return number;
}

const STATUS_LABELS = {
  connecting: ["전화 연결 준비 중…", "준비 중", "connecting"],
  listening: ["수신 대기 중", "수신 대기", "connected"],
  queued: ["발신 요청됨 · 응답 대기", "발신 중", "connecting"],
  ringing: ["벨 울리는 중 · 응답 대기", "발신 중", "connecting"],
  "in-progress": ["통화 중", "통화 중", "connected"],
  completed: ["통화가 종료됐습니다", "종료", "idle"],
  "no-answer": ["상대방이 전화를 받지 않았습니다", "응답 없음", "idle"],
  busy: ["상대방이 다른 통화 중입니다", "통화 중 종료", "idle"],
  rejected: ["상대방이 전화를 거절했습니다", "수신 거절", "idle"],
  canceled: ["발신이 취소됐습니다", "취소", "idle"],
  failed: ["전화 연결에 실패했습니다", "연결 실패", "idle"],
  stopped: ["전화 세션이 종료됐습니다", "종료", "idle"],
  idle: ["전화 세션이 종료됐습니다", "종료", "idle"],
};

export function phoneStatusLabel(data) {
  const [label, badge, state] = STATUS_LABELS[data.status] || ["전화 상태 확인 중…", "확인 중", "connecting"];
  return { label, badge, state };
}

export function createPhoneSession(requestJson, initial, {
  onStatus, onError, setTimer = setTimeout, clearTimer = clearTimeout,
}) {
  let closed = false;
  let stopping = false;
  let timer = null;
  const schedule = () => {
    clearTimer(timer);
    if (!closed && !stopping) timer = setTimer(poll, 1000);
  };
  const applyStatus = (data) => {
    if (!data.active) closed = true;
    onStatus(data);
  };
  const poll = async () => {
    if (closed || stopping) return;
    try {
      const data = await requestJson("/api/clawops/status");
      if (closed || stopping) return;
      if (data.sessionId !== initial.sessionId) {
        applyStatus({ ...initial, active: false, status: "stopped", error: null });
      } else {
        applyStatus(data);
      }
    } catch (error) {
      if (!closed && !stopping) onError(error);
    } finally {
      schedule();
    }
  };
  return {
    id: initial.sessionId,
    start() {
      applyStatus(initial);
      schedule();
    },
    async end() {
      if (closed) return;
      stopping = true;
      clearTimer(timer);
      try {
        await requestJson("/api/clawops/stop", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ sessionId: initial.sessionId }),
        });
        closed = true;
      } catch (error) {
        // Keep polling and allow another stop attempt if the remote hangup failed.
        stopping = false;
        schedule();
        throw error;
      }
    },
  };
}
