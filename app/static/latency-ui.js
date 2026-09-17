import {
  STORAGE_KEY, MAX_RECORDS, INPUT_LABELS, STATUS_LABELS,
  TurnLatency, formatDuration, parseRecords, summarize,
} from "./latency.js?v=20260917-1";

const $ = (selector) => document.querySelector(selector);
const create = (tag, className, text) => {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
};

export function createLatencyDashboard({ modeInfo, getModel, onChange }) {
  let records = [];
  let storageAvailable = true;
  let selectedMode = "elevenlabs";
  let inputKind = "voice";
  let latestRecord = null;
  let undoRecords = null;
  try {
    records = parseRecords(localStorage.getItem(STORAGE_KEY));
  } catch {
    storageAvailable = false;
  }
  inputKind = records.at(-1)?.kind || "voice";

  const tracker = new TurnLatency({
    onRecord(record) {
      records.push(record);
      records = records.slice(-MAX_RECORDS);
      latestRecord = record;
      // Follow the measurement that just arrived, so a transcript-only provider
      // cannot silently contribute to the speech-end comparison.
      inputKind = record.kind;
      persist();
      refresh();
      $("#measurement-announcement").textContent = record.status === "complete"
        ? `${modeInfo[record.mode].label}, 응답 지연 ${formatDuration(record.latencyMs)}`
        : `이번 턴은 ${STATUS_LABELS[record.status]}로 평균에서 제외했습니다.`;
      onChange();
    },
  });

  function persist() {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify({ version: 1, records }));
      storageAvailable = true;
    } catch {
      storageAvailable = false;
    }
  }

  function renderLive() {
    const supportsLatency = modeInfo[selectedMode].supportsLatency !== false;
    const pending = tracker.pending;
    const record = latestRecord;
    const complete = record?.status === "complete";
    $("#live-panel").dataset.phase = pending ? "waiting" : complete ? "complete" : "idle";
    $("#live-state").textContent = pending ? "응답 기다리는 중" : complete ? "측정 완료" : "측정 대기";
    $("#live-value").textContent = pending
      ? formatDuration(tracker.now() - pending.startedAt)
      : complete ? formatDuration(record.latencyMs) : "—";
    $("#live-caption").textContent = pending
      ? `${INPUT_LABELS[pending.kind]}부터 첫 음성 신호까지 측정 중`
      : complete ? `${modeInfo[record.mode].label} · ${INPUT_LABELS[record.kind]} 기준`
        : record ? `${STATUS_LABELS[record.status]} · 평균에서 제외했습니다`
          : "대화를 시작하고 질문하면 자동으로 측정합니다";
    $("#live-provider").textContent = modeInfo[selectedMode].label;
    $("#live-turn").textContent = pending ? `TURN ${String(pending.turn).padStart(2, "0")}`
      : record ? `TURN ${String(record.turn).padStart(2, "0")}` : "READY";
    $("#phase-input").dataset.active = String(Boolean(pending || complete));
    $("#phase-wait").dataset.active = String(Boolean(pending || complete));
    $("#phase-audio").dataset.active = String(!pending && complete);
    if (!supportsLatency) {
      $("#live-state").textContent = "전화 모드";
      $("#live-caption").textContent = "전화 응답 지연은 브라우저에서 측정하지 않습니다.";
    }
  }

  function filteredRecords() {
    return records.filter((record) => record.kind === inputKind);
  }

  function renderComparison() {
    const filtered = filteredRecords();
    const summaries = Object.entries(modeInfo).filter(([, info]) => info.supportsLatency !== false).map(([mode, info]) => {
      const matching = filtered.filter((record) => record.mode === mode && record.model === getModel(mode));
      return { mode, info, ...summarize(matching), excluded: matching.filter((r) => r.status !== "complete").length };
    });
    const maxMean = Math.max(1, ...summaries.map((summary) => summary.mean || 0));
    const grid = $("#comparison-grid");
    grid.replaceChildren();
    summaries.forEach((summary, index) => {
      const card = create("article", "comparison-card");
      card.dataset.mode = summary.mode;
      card.classList.toggle("selected", summary.mode === selectedMode);
      const header = create("div", "comparison-card-heading");
      header.append(create("span", "model-index", `0${index + 1}`), create("h3", "", summary.info.label));
      card.append(header, create("p", "comparison-model", getModel(summary.mode)));
      const average = create("div", "comparison-average");
      average.append(create("strong", "", formatDuration(summary.mean)), create("span", "", "평균"));
      card.append(average);
      const track = create("div", "comparison-bar");
      track.setAttribute("aria-hidden", "true");
      const fill = create("span");
      fill.style.width = summary.count ? `${(summary.mean / maxMean) * 100}%` : "0%";
      track.append(fill);
      card.append(track);
      const stats = create("dl", "comparison-stats");
      [["최근", summary.latest], ["중앙값", summary.median], ["P95", summary.p95]].forEach(([label, value]) => {
        const entry = create("div");
        entry.append(create("dt", "", label), create("dd", "", formatDuration(value)));
        stats.append(entry);
      });
      card.append(stats);
      const sample = create("p", "sample-count");
      sample.textContent = summary.count ? `${summary.count}회 측정${summary.count < 10 ? " · 10회 이상 권장" : ""}` : "아직 측정값이 없어요";
      if (summary.excluded) sample.textContent += ` · 제외 ${summary.excluded}회`;
      card.append(sample);
      grid.append(card);
    });
    $("#comparison-basis").textContent = inputKind === "transcript"
      ? "전사 수신 → 첫 음성 신호 · 발화 종료 기준과 분리한 참고값입니다."
      : inputKind === "text" ? "텍스트 전송 → 첫 음성 신호 · 같은 문장으로 비교하세요."
        : "발화 종료 감지 → 첫 음성 신호 · 짧을수록 빠른 응답입니다.";
    document.querySelectorAll("[data-input-kind]").forEach((button) => {
      const active = button.dataset.inputKind === inputKind;
      button.classList.toggle("active", active);
      button.setAttribute("aria-pressed", String(active));
      const count = records.filter((r) => r.kind === button.dataset.inputKind && r.status === "complete").length;
      button.querySelector("span").textContent = String(count);
    });
  }

  function renderHistory() {
    const mode = $("#history-mode").value;
    const filtered = filteredRecords().filter((record) => mode === "all" || record.mode === mode);
    const recent = filtered.slice(-20).reverse();
    const maxLatency = Math.max(1000, ...recent.map((record) => record.latencyMs || 0));
    const body = $("#history-body");
    body.replaceChildren();
    $("#history-empty").hidden = recent.length > 0;
    $("#history-table").hidden = !recent.length;
    $("#history-count").textContent = `${filtered.length}개 중 최근 ${recent.length}개`;
    recent.forEach((record) => {
      const row = create("tr");
      row.dataset.mode = record.mode;
      row.append(create("td", "history-time", new Date(record.recordedAt).toLocaleTimeString("ko-KR", { hour12: false })));
      const model = create("td", "history-model");
      model.append(create("strong", "", modeInfo[record.mode].label), create("small", "", `${record.model} · 턴 ${record.turn}`));
      row.append(model);
      const value = create("td", "history-value");
      if (record.status === "complete") {
        value.append(create("strong", "", formatDuration(record.latencyMs)));
        const track = create("span", "history-bar");
        track.style.width = `${(record.latencyMs / maxLatency) * 100}%`;
        track.setAttribute("aria-hidden", "true");
        value.append(track);
      } else {
        value.append(create("span", "excluded-label", STATUS_LABELS[record.status]), create("small", "", "평균 제외"));
      }
      row.append(value);
      body.append(row);
    });
  }

  function refresh() {
    renderLive();
    renderComparison();
    renderHistory();
    $("#record-total").textContent = records.filter((r) => r.status === "complete").length;
    $("#export-measurements").disabled = records.length === 0;
    $("#reset-measurements").disabled = records.length === 0 || Boolean(tracker.context);
    $("#storage-note").textContent = storageAvailable
      ? `이 브라우저에 최근 ${MAX_RECORDS}개 자동 저장 · 대화 내용은 저장하지 않습니다.`
      : "브라우저 저장이 제한되어 이번 화면에서만 유지됩니다. 내보내기로 보관하세요.";
  }

  document.querySelectorAll("[data-input-kind]").forEach((button) => {
    button.addEventListener("click", () => {
      inputKind = button.dataset.inputKind;
      refresh();
    });
  });
  $("#history-mode").addEventListener("change", renderHistory);
  $("#export-measurements").addEventListener("click", () => {
    const data = {
      version: 1, exportedAt: new Date().toISOString(), unit: "milliseconds",
      description: "Client-observed first-audio latency. Group by mode, model and kind; only complete records contribute to statistics.",
      records,
    };
    const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }));
    const link = create("a");
    link.href = url;
    link.download = `voice-latency-${new Date().toISOString().replace(/[:.]/g, "-")}.json`;
    link.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
  $("#reset-measurements").addEventListener("click", () => {
    if (tracker.context) return;
    undoRecords = [...records];
    records = [];
    latestRecord = null;
    $("#reset-notice").hidden = false;
    persist();
    refresh();
  });
  $("#undo-reset").addEventListener("click", () => {
    const merged = new Map([...(undoRecords || []), ...records].map((record) => [record.id, record]));
    records = [...merged.values()].sort((a, b) => Date.parse(a.recordedAt) - Date.parse(b.recordedAt)).slice(-MAX_RECORDS);
    undoRecords = null;
    $("#reset-notice").hidden = true;
    persist();
    refresh();
  });

  window.setInterval(() => {
    if (!tracker.pending) return;
    tracker.expire();
    renderLive();
  }, 100);

  return {
    tracker, refresh,
    selectMode(mode) {
      selectedMode = mode;
      latestRecord = null;
      refresh();
    },
    selectInput(kind) {
      inputKind = kind;
      refresh();
    },
    beginSession(context) {
      latestRecord = null;
      tracker.beginSession(context);
      refresh();
    },
    endSession() {
      tracker.endSession();
      refresh();
    },
  };
}
