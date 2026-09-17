export const STORAGE_KEY = "voice-lab.latency.v1";
export const MAX_RECORDS = 600;
export const TURN_TIMEOUT_MS = 45000;
export const INPUT_LABELS = {
  voice: "발화 종료",
  text: "텍스트 전송",
  transcript: "전사 수신 · 참고",
};
export const STATUS_LABELS = {
  complete: "측정 완료",
  interrupted: "발화 재개",
  cancelled: "연결 종료",
  failed: "응답 오류",
  timeout: "45초 초과",
  unavailable: "시작 기준 없음",
};

const MODES = new Set(["elevenlabs", "openai_realtime", "hybrid"]);

export function formatDuration(ms) {
  if (!Number.isFinite(ms)) return "—";
  return ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(2)} s`;
}

export function summarize(records) {
  const values = records
    .filter((record) => record.status === "complete" && Number.isFinite(record.latencyMs))
    .map((record) => record.latencyMs)
    .sort((a, b) => a - b);
  if (!values.length) return { count: 0, mean: null, median: null, p95: null, latest: null };
  const middle = Math.floor(values.length / 2);
  const completed = records.filter((record) => record.status === "complete");
  return {
    count: values.length,
    mean: values.reduce((sum, value) => sum + value, 0) / values.length,
    median: values.length % 2 ? values[middle] : (values[middle - 1] + values[middle]) / 2,
    // Nearest-rank percentile; a small sample's P95 is often its maximum.
    p95: values[Math.ceil(values.length * 0.95) - 1],
    latest: completed.at(-1).latencyMs,
  };
}

export function parseRecords(raw) {
  try {
    const data = JSON.parse(raw);
    if (data?.version !== 1 || !Array.isArray(data.records)) return [];
    const seen = new Set();
    return data.records.filter((record) => {
      if (!record || typeof record !== "object" || seen.has(record.id)) return false;
      const valid = typeof record.id === "string" && record.id.length <= 100
        && MODES.has(record.mode) && typeof record.model === "string" && record.model.length <= 200
        && Object.hasOwn(INPUT_LABELS, record.kind) && Object.hasOwn(STATUS_LABELS, record.status)
        && typeof record.sessionId === "string" && record.sessionId.length <= 100
        && Number.isInteger(record.turn) && record.turn > 0
        && typeof record.recordedAt === "string" && record.recordedAt.length <= 40
        && Number.isFinite(Date.parse(record.recordedAt))
        && typeof record.startSource === "string" && record.startSource.length <= 100
        && typeof record.endSource === "string" && record.endSource.length <= 100
        && (record.status === "complete"
          ? Number.isFinite(record.latencyMs) && record.latencyMs >= 0 && record.latencyMs < TURN_TIMEOUT_MS
          : record.latencyMs === null);
      if (valid) seen.add(record.id);
      return valid;
    }).slice(-MAX_RECORDS).map((record) => ({
      id: record.id, mode: record.mode, model: record.model, kind: record.kind,
      sessionId: record.sessionId, turn: record.turn, recordedAt: record.recordedAt,
      startSource: record.startSource, endSource: record.endSource,
      status: record.status, latencyMs: record.latencyMs,
    }));
  } catch {
    return [];
  }
}

// Only an explicit user turn can arm a measurement. Transcript delivery never
// completes it: the provider's first audio event is the sole completion signal.
export class TurnLatency {
  constructor({ now = () => performance.now(), onRecord = () => {} } = {}) {
    this.now = now;
    this.onRecord = onRecord;
    this.pending = null;
    this.context = null;
    this.turns = 0;
    this.seenInputs = new Set();
    this.responses = new Map();
  }

  beginSession(context) {
    this.cancel("cancelled");
    this.context = { ...context };
    this.turns = 0;
    this.seenInputs.clear();
    this.responses.clear();
  }

  start({ kind, startSource, key, at = this.now() }) {
    if (!this.context || (key && this.seenInputs.has(key))) return null;
    if (!Number.isFinite(at) || at > this.now()) return null;
    if (key) this.seenInputs.add(key);
    this.cancel("interrupted");
    this.turns += 1;
    this.pending = {
      ...this.context, id: `${this.context.sessionId}:${this.turns}`,
      turn: this.turns, kind, startSource, startedAt: at,
    };
    return this.pending;
  }

  bindResponse(responseId, { greeting = false } = {}) {
    if (responseId && !this.responses.has(responseId)) {
      this.responses.set(responseId, greeting ? null : (this.pending?.id ?? null));
    }
  }

  matchesResponse(responseId) {
    return Boolean(this.pending) && (!responseId || this.responses.get(responseId) === this.pending.id);
  }

  finish(endSource, responseId) {
    if (!this.matchesResponse(responseId)) return null;
    const elapsed = this.now() - this.pending.startedAt;
    if (elapsed >= TURN_TIMEOUT_MS) return this.cancel("timeout");
    if (elapsed < 0) return null;
    return this.settle("complete", elapsed, endSource);
  }

  expire() {
    if (this.pending && this.now() - this.pending.startedAt >= TURN_TIMEOUT_MS) {
      return this.cancel("timeout");
    }
    return null;
  }

  cancel(status = "cancelled", responseId) {
    if (!this.pending || (responseId && !this.matchesResponse(responseId))) return null;
    return this.settle(status, null, "");
  }

  settle(status, latencyMs, endSource) {
    const { startedAt, ...turn } = this.pending;
    this.pending = null;
    const record = { ...turn, status, latencyMs, endSource, recordedAt: new Date().toISOString() };
    this.onRecord(record);
    return record;
  }

  endSession() {
    this.cancel("cancelled");
    this.context = null;
    this.responses.clear();
  }
}
