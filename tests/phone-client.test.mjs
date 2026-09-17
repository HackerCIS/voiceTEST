import test from "node:test";
import assert from "node:assert/strict";
import { createPhoneSession, normalizePhoneNumber, phoneStatusLabel } from "../app/static/phone-client.js";

const initial = { sessionId: "session-1", mode: "outbound", active: true, status: "connecting" };

function harness(request) {
  let scheduled = null;
  const statuses = [];
  const errors = [];
  const session = createPhoneSession(request, initial, {
    onStatus: (data) => statuses.push(data),
    onError: (error) => errors.push(error),
    setTimer: (fn) => { scheduled = fn; return 1; },
    clearTimer: () => { scheduled = null; },
  });
  session.start();
  return {
    session, statuses, errors,
    scheduled: () => Boolean(scheduled),
    async tick() {
      const callback = scheduled;
      scheduled = null;
      assert.ok(callback, "a status poll should be scheduled");
      await callback();
    },
  };
}

test("normalizes formatted phone numbers and rejects incomplete or malformed input", () => {
  assert.equal(normalizePhoneNumber("010-1234-5678"), "01012345678");
  assert.equal(normalizePhoneNumber("+82 (10) 1234-5678"), "+821012345678");
  for (const value of ["", "abc", "119", "010;12345678", "+0123456789"]) {
    assert.throws(() => normalizePhoneNumber(value), /전화번호/);
  }
});

test("ringing is displayed separately from a connected call and unanswered call", () => {
  assert.equal(phoneStatusLabel({ status: "ringing" }).state, "connecting");
  assert.equal(phoneStatusLabel({ status: "in-progress" }).label, "통화 중");
  assert.match(phoneStatusLabel({ status: "no-answer" }).label, /받지 않았/);
});

test("polls through ringing and connected states and stops after completion", async () => {
  const updates = [
    { ...initial, status: "ringing" },
    { ...initial, status: "in-progress" },
    { ...initial, status: "completed", active: false },
  ];
  const h = harness(async () => updates.shift());
  await h.tick();
  await h.tick();
  await h.tick();
  assert.deepEqual(h.statuses.map((data) => data.status), ["connecting", "ringing", "in-progress", "completed"]);
  assert.equal(h.scheduled(), false);
});

test("a superseded session stops polling without hanging up a newer call", async () => {
  const requests = [];
  const h = harness(async (url) => {
    requests.push(url);
    return { ...initial, sessionId: "session-2" };
  });
  await h.tick();
  await h.session.end();
  assert.equal(h.statuses.at(-1).status, "stopped");
  assert.deepEqual(requests, ["/api/clawops/status"]);
  assert.equal(h.scheduled(), false);
});

test("stop identifies the exact session and ignores an in-flight status response", async () => {
  let resolveStatus;
  const requests = [];
  const h = harness(async (url, options) => {
    requests.push({ url, options });
    if (url.endsWith("/status")) return new Promise((resolve) => { resolveStatus = resolve; });
    return { status: "stopped" };
  });
  const poll = h.tick();
  await h.session.end();
  resolveStatus({ ...initial, status: "ringing" });
  await poll;
  const stop = requests.find((item) => item.url.endsWith("/stop"));
  assert.equal(stop.options.method, "POST");
  assert.deepEqual(JSON.parse(stop.options.body), { sessionId: "session-1" });
  assert.equal(h.statuses.length, 1);
  assert.equal(h.scheduled(), false);
});

test("network errors preserve polling and the ability to stop the call", async () => {
  const h = harness(async (url) => {
    if (url.endsWith("/status")) throw new Error("offline");
    return { status: "stopped" };
  });
  await h.tick();
  assert.equal(h.errors.length, 1);
  assert.equal(h.scheduled(), true);
  await h.session.end();
  assert.equal(h.scheduled(), false);
});

test("a failed hangup can be retried", async () => {
  let attempts = 0;
  const h = harness(async () => {
    if (++attempts === 1) throw new Error("hangup failed");
    return { status: "stopped" };
  });
  await assert.rejects(h.session.end(), /hangup failed/);
  assert.equal(h.scheduled(), true);
  await h.session.end();
  assert.equal(attempts, 2);
  assert.equal(h.scheduled(), false);
});
