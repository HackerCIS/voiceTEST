import test from "node:test";
import assert from "node:assert/strict";
import { TurnLatency, summarize, parseRecords, MAX_RECORDS, TURN_TIMEOUT_MS } from "../app/static/latency.js";

function harness() {
  let time = 0;
  const records = [];
  const tracker = new TurnLatency({ now: () => time, onRecord: (record) => records.push(record) });
  tracker.beginSession({ mode: "openai_realtime", model: "test-model", sessionId: "session-1" });
  return { tracker, records, advance: (ms) => { time += ms; } };
}
const voice = (key) => ({ kind: "voice", startSource: "speech_stopped", key });

test("first greeting and duplicate audio events never become extra samples", () => {
  const { tracker, records, advance } = harness();
  tracker.bindResponse("greeting", { greeting: true });
  assert.equal(tracker.finish("audio", "greeting"), null);
  tracker.start(voice("user-1"));
  tracker.bindResponse("response-1");
  advance(420);
  assert.equal(tracker.finish("audio", "greeting"), null);
  tracker.finish("audio", "response-1");
  tracker.finish("audio", "response-1");
  assert.equal(records.length, 1);
  assert.equal(records[0].latencyMs, 420);
});

test("zero on a monotonic clock is a valid turn start; duplicate input does not reset it", () => {
  const { tracker, records, advance } = harness();
  tracker.start(voice("user-1"));
  advance(600);
  assert.equal(tracker.start(voice("user-1")), null);
  tracker.finish("elevenlabs-speaking");
  assert.equal(tracker.turns, 1);
  assert.equal(records[0].latencyMs, 600);
});

test("late audio and errors from an interrupted response cannot settle the next turn", () => {
  const { tracker, records, advance } = harness();
  tracker.start(voice("user-1"));
  tracker.bindResponse("old-response");
  advance(100);
  tracker.cancel("interrupted");
  tracker.start(voice("user-2"));
  tracker.bindResponse("new-response");
  advance(200);
  assert.equal(tracker.finish("audio", "old-response"), null);
  assert.equal(tracker.cancel("failed", "old-response"), null);
  tracker.finish("audio", "new-response");
  assert.deepEqual(records.map((r) => [r.status, r.latencyMs]), [["interrupted", null], ["complete", 200]]);
  assert.equal(summarize(records).count, 1);
});

test("disconnect drops pending correlation and the next model starts a fresh session", () => {
  const { tracker, records, advance } = harness();
  tracker.start(voice("user-1"));
  tracker.bindResponse("old-response");
  tracker.endSession();
  assert.equal(tracker.start(voice("unconnected")), null);
  tracker.beginSession({ mode: "hybrid", model: "other-model", sessionId: "session-2" });
  tracker.start({ kind: "text", startSource: "text_send" });
  advance(800);
  assert.equal(tracker.finish("audio", "old-response"), null);
  tracker.finish("elevenlabs-speaking");
  assert.equal(records[0].status, "cancelled");
  assert.equal(records[1].mode, "hybrid");
  assert.equal(records[1].turn, 1);
  assert.equal(records[1].kind, "text");
});

test("a confirmed speech boundary retains time spent waiting for transcription", () => {
  const { tracker, records, advance } = harness();
  advance(700);
  tracker.start({ ...voice("user-1"), at: 200 });
  advance(100);
  tracker.finish("audio");
  assert.equal(records[0].latencyMs, 600);
});

test("timeouts and failed requests stay visible but never contribute zero-valued latency", () => {
  const { tracker, records, advance } = harness();
  tracker.start(voice("user-1"));
  advance(TURN_TIMEOUT_MS);
  tracker.expire();
  tracker.start(voice("user-2"));
  tracker.cancel("failed");
  tracker.start(voice("user-3"));
  advance(TURN_TIMEOUT_MS + 1);
  tracker.finish("audio");
  assert.deepEqual(records.map((r) => r.status), ["timeout", "failed", "timeout"]);
  assert.equal(summarize(records).count, 0);
  assert.ok(records.every((r) => r.latencyMs === null));
});

test("statistics use chronological latest, even-sample median and nearest-rank P95", () => {
  const records = [900, 100, 500, 300].map((latencyMs) => ({ status: "complete", latencyMs }));
  records.push({ status: "cancelled", latencyMs: null });
  assert.deepEqual(summarize(records), { count: 4, mean: 450, median: 400, p95: 900, latest: 300 });
  const twenty = Array.from({ length: 20 }, (_, i) => ({ status: "complete", latencyMs: (i + 1) * 100 }));
  assert.equal(summarize(twenty).p95, 1900);
});

test("stored records reject corrupt values, strip extra fields and bound retention", () => {
  const { tracker, records, advance } = harness();
  tracker.start(voice("user-1"));
  advance(400);
  tracker.finish("audio");
  const record = records[0];
  const raw = JSON.stringify({ version: 1, records: [
    null, { ...record, latencyMs: -1 }, { ...record, mode: "__proto__" },
    { ...record, status: "unknown" }, { ...record, kind: "toString" },
    { ...record, privateTranscript: "must not persist" }, record,
  ] });
  assert.deepEqual(parseRecords(raw), [record]);
  assert.deepEqual(parseRecords("broken json"), []);
  assert.deepEqual(parseRecords(JSON.stringify({ version: 2, records })), []);
  const many = Array.from({ length: MAX_RECORDS + 5 }, (_, i) => ({ ...record, id: `sample-${i}` }));
  const restored = parseRecords(JSON.stringify({ version: 1, records: many }));
  assert.equal(restored.length, MAX_RECORDS);
  assert.equal(restored[0].id, "sample-5");
});
