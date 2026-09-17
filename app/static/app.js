import { formatDuration } from "./latency.js?v=20260909-1";
import { createLatencyDashboard } from "./latency-ui.js?v=20260909-1";

const MODE_INFO = {
  elevenlabs: {
    label: "ElevenLabs",
    provider: "ElevenLabs Agent",
    fallbackModel: "Agent 설정 모델",
    description: "ElevenLabs가 음성 인식, LLM, 음성 합성과 턴 제어를 모두 담당합니다.",
  },
  openai_realtime: {
    label: "OpenAI Realtime",
    provider: "OpenAI Realtime",
    fallbackModel: "gpt-realtime-2.1",
    description: "OpenAI Realtime 모델이 WebRTC로 음성을 직접 듣고 바로 음성으로 응답합니다.",
  },
  hybrid: {
    label: "ElevenLabs + OpenAI",
    provider: "ElevenLabs Agent + OpenAI LLM",
    fallbackModel: "gpt-5.4",
    description: "ElevenLabs가 ASR·TTS·턴 제어를 맡고, FastAPI 프록시를 통해 OpenAI GPT-5.4가 답변을 생성합니다.",
  },
};

const elements = {
  modeButtons: [...document.querySelectorAll(".mode-button")],
  providerLabel: document.querySelector("#provider-label"),
  modelLabel: document.querySelector("#model-label"),
  modeDescription: document.querySelector("#mode-description"),
  start: document.querySelector("#start-button"),
  stop: document.querySelector("#stop-button"),
  mute: document.querySelector("#mute-button"),
  clear: document.querySelector("#clear-button"),
  statusDot: document.querySelector("#status-dot"),
  statusText: document.querySelector("#status-text"),
  sessionId: document.querySelector("#session-id"),
  modeBadge: document.querySelector("#mode-badge"),
  error: document.querySelector("#error-message"),
  transcript: document.querySelector("#transcript"),
  messageForm: document.querySelector("#message-form"),
  messageInput: document.querySelector("#message-input"),
  send: document.querySelector("#send-button"),
  connectMetric: document.querySelector("#connect-metric"),
  turnMetric: document.querySelector("#turn-metric"),
  tokenMetric: document.querySelector("#token-metric"),
};

let selectedMode = "elevenlabs";
let activeSession = null;
let currentRunId = null;
let isConnecting = false;
let uiConnected = false;
let isMuted = false;
let healthData = null;
let isAssistantSpeaking = false;

const metrics = {
  startedAt: 0,
  turns: 0,
  tokens: 0,
};

const dashboard = createLatencyDashboard({
  modeInfo: MODE_INFO,
  getModel: modeModel,
  onChange: updateControls,
});

function resetMetrics() {
  metrics.startedAt = 0;
  metrics.turns = 0;
  metrics.tokens = 0;
  elements.connectMetric.textContent = "—";
  elements.turnMetric.textContent = "0";
  elements.tokenMetric.textContent = "—";
}

function beginMetrics() {
  resetMetrics();
  metrics.startedAt = performance.now();
}

function markConnectedMetric() {
  if (metrics.startedAt) {
    elements.connectMetric.textContent = formatDuration(
      performance.now() - metrics.startedAt,
    );
  }
}

function noteUserTurn(kind, startSource, { key, at } = {}) {
  const turn = dashboard.tracker.start({ kind, startSource, key, at });
  if (!turn) return null;
  metrics.turns = dashboard.tracker.turns;
  elements.turnMetric.textContent = String(metrics.turns);
  dashboard.selectInput(kind);
  updateControls();
  return turn;
}

function noteAssistantStarted(endSource, responseId) {
  isAssistantSpeaking = true;
  dashboard.tracker.finish(endSource, responseId);
  updateControls();
}

function addTokenUsage(usage) {
  const total = usage?.total_tokens;
  if (Number.isFinite(total)) {
    metrics.tokens += total;
    elements.tokenMetric.textContent = metrics.tokens.toLocaleString("ko-KR");
  }
}

function setStatus(text, state = "idle") {
  elements.statusText.textContent = text;
  elements.statusDot.dataset.state = state;
}

function showError(message) {
  elements.error.textContent = message;
  elements.error.hidden = !message;
}

function isModeConfigured(mode = selectedMode) {
  return healthData?.modes?.[mode]?.configured ?? true;
}

function updateControls() {
  const busy = isConnecting || uiConnected;
  elements.start.disabled = busy || !isModeConfigured();
  elements.stop.disabled = !busy;
  elements.mute.disabled = !uiConnected;
  elements.send.disabled = !activeSession || !uiConnected || Boolean(dashboard.tracker.pending) || isAssistantSpeaking;
  elements.send.textContent = dashboard.tracker.pending ? "측정 중…" : "이 문장 보내기";
  elements.modeButtons.forEach((button) => {
    button.disabled = busy;
  });
}

function clearTranscript() {
  elements.transcript.replaceChildren();
  const emptyMessage = document.createElement("p");
  emptyMessage.className = "empty-message";
  emptyMessage.textContent = "대화를 시작하면 메시지가 여기에 표시됩니다.";
  elements.transcript.append(emptyMessage);
}

function appendMessage(role, message) {
  const text = typeof message === "string" ? message.trim() : "";
  if (!text) return;

  elements.transcript.querySelector(".empty-message")?.remove();
  const item = document.createElement("article");
  item.className = `message ${role === "user" ? "user" : "agent"}`;

  const label = document.createElement("span");
  label.className = "message-role";
  label.textContent = role === "user" ? "나" : MODE_INFO[selectedMode].provider;

  const content = document.createElement("p");
  content.textContent = text;

  item.append(label, content);
  elements.transcript.append(item);
  elements.transcript.scrollTop = elements.transcript.scrollHeight;
}

function resetSessionUi(reason = "연결 종료") {
  activeSession = null;
  currentRunId = null;
  isConnecting = false;
  uiConnected = false;
  isMuted = false;
  isAssistantSpeaking = false;
  dashboard.endSession();
  elements.mute.textContent = "마이크 끄기";
  elements.modeBadge.textContent = "IDLE";
  elements.sessionId.textContent = "세션 없음";
  setStatus(reason, "idle");
  updateControls();
}

function markConnected(sessionId) {
  uiConnected = true;
  isConnecting = false;
  elements.sessionId.textContent = sessionId || "세션 연결됨";
  elements.modeBadge.textContent = "듣는 중";
  setStatus("연결됨", "connected");
  markConnectedMetric();
  updateControls();
}

function handleUnexpectedDisconnect(runId, reason = "연결 종료") {
  if (runId !== currentRunId) return;
  resetSessionUi(reason);
}

function modeModel(mode) {
  return healthData?.modes?.[mode]?.model || MODE_INFO[mode].fallbackModel;
}

function selectMode(mode) {
  if (!MODE_INFO[mode] || isConnecting || uiConnected) return;
  selectedMode = mode;
  elements.modeButtons.forEach((button) => {
    const active = button.dataset.mode === mode;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", String(active));
  });

  elements.providerLabel.textContent = MODE_INFO[mode].provider;
  elements.modelLabel.textContent = modeModel(mode);
  const missing = healthData?.modes?.[mode]?.missing || [];
  elements.modeDescription.textContent = missing.length
    ? `${MODE_INFO[mode].description} 설정 필요: ${missing.join(", ")}`
    : MODE_INFO[mode].description;
  showError("");
  clearTranscript();
  resetMetrics();
  dashboard.selectMode(mode);
  setStatus("연결 대기", "idle");
  updateControls();
}

async function requestJson(url, options = {}) {
  const response = await fetch(url, {
    ...options,
    headers: { Accept: "application/json", ...options.headers },
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || `요청 실패 (HTTP ${response.status})`);
  }
  return data;
}

async function getElevenLabsSessionConfig(mode) {
  return requestJson(`/api/session?mode=${encodeURIComponent(mode)}`, {
    method: "POST",
  });
}

function buildElevenLabsOptions(session, runId) {
  let vadSpeaking = false;
  let lastSpeechEnd = null;
  let speechSinceLastTurn = false;
  let awaitingUserTranscript = false;
  let hasAgentSpoken = false;
  let textEcho = null;
  const seenUserEvents = new Set();
  const startVoiceTurn = (key) => {
    const hasSpeechEnd = speechSinceLastTurn && lastSpeechEnd !== null
      && performance.now() - lastSpeechEnd < 15000;
    const turn = noteUserTurn(
      hasSpeechEnd ? "voice" : "transcript",
      hasSpeechEnd ? "elevenlabs.vad_score.speech_end" : "elevenlabs.user_transcript",
      { key, at: hasSpeechEnd ? lastSpeechEnd : undefined },
    );
    speechSinceLastTurn = false;
    return turn;
  };
  const callbacks = {
    connectionType: "webrtc",
    onConnect: ({ conversationId }) => {
      if (runId === currentRunId) markConnected(conversationId);
    },
    onDisconnect: () => handleUnexpectedDisconnect(runId),
    onError: (message) => {
      if (runId !== currentRunId) return;
      dashboard.tracker.cancel("failed");
      showError(
        typeof message === "string" ? message : "ElevenLabs 대화 중 오류가 발생했습니다.",
      );
    },
    onOutgoingEvent: (event) => {
      if (runId === currentRunId && event.type === "user_message") {
        textEcho = { text: event.text, at: performance.now() };
      }
    },
    onVadScore: ({ vadScore }) => {
      if (runId !== currentRunId || isMuted) return;
      if (vadScore >= 0.6 && !vadSpeaking) {
        vadSpeaking = true;
        lastSpeechEnd = null;
        speechSinceLastTurn = true;
        dashboard.tracker.cancel("interrupted");
      } else if (vadScore <= 0.35 && vadSpeaking) {
        vadSpeaking = false;
        lastSpeechEnd = performance.now();
      }
    },
    onMessage: ({ message, role, source, event_id }) => {
      if (runId !== currentRunId) return;
      const isUser = role === "user" || source === "user";
      if (isUser) {
        if (!message?.trim()) return;
        if (event_id != null && seenUserEvents.has(event_id)) return;
        if (event_id != null) seenUserEvents.add(event_id);
        if (textEcho?.text === message && performance.now() - textEcho.at < 30000) {
          textEcho = null;
          return;
        }
        if (awaitingUserTranscript) {
          awaitingUserTranscript = false;
        } else if (!isAssistantSpeaking || dashboard.tracker.pending) {
          startVoiceTurn(event_id == null ? undefined : `elevenlabs:${event_id}`);
        } else if (!speechSinceLastTurn) {
          // Audio is already playing and no speech boundary was observed.
          // A late transcript cannot produce a trustworthy first-audio value.
          noteUserTurn("transcript", "elevenlabs.user_transcript", {
            key: event_id == null ? undefined : `elevenlabs:${event_id}`,
          });
          dashboard.tracker.cancel("unavailable");
        }
      } else if (!hasAgentSpoken && !dashboard.tracker.pending) {
        // Lock text submission during an unsolicited greeting's generation,
        // before its speaking callback has arrived.
        isAssistantSpeaking = true;
        updateControls();
      }
      appendMessage(isUser ? "user" : "agent", message);
    },
    onModeChange: ({ mode }) => {
      if (runId !== currentRunId) return;
      elements.modeBadge.textContent =
        mode === "speaking" ? "AGENT 말하는 중" : "듣는 중";
      isAssistantSpeaking = mode === "speaking";
      if (isAssistantSpeaking) {
        hasAgentSpoken = true;
        // Audio may precede final transcription. An observed VAD boundary can
        // still anchor that turn; the later transcript must not arm it twice.
        if (!dashboard.tracker.pending && speechSinceLastTurn && lastSpeechEnd !== null) {
          startVoiceTurn();
          awaitingUserTranscript = true;
        }
        noteAssistantStarted("elevenlabs.onModeChange.speaking");
      }
      updateControls();
    },
    onStatusChange: ({ status }) => {
      if (runId !== currentRunId) return;
      if (status === "connecting") setStatus("연결 중…", "connecting");
      if (status === "disconnecting") setStatus("종료 중…", "connecting");
    },
  };

  if (session.conversationToken) {
    return { ...callbacks, conversationToken: session.conversationToken };
  }
  if (session.agentId) {
    return { ...callbacks, agentId: session.agentId };
  }
  throw new Error("서버가 올바른 ElevenLabs 연결 정보를 반환하지 않았습니다.");
}

async function startElevenLabsSession(mode, runId) {
  const sessionConfig = await getElevenLabsSessionConfig(mode);
  const { Conversation } = await import("https://esm.sh/@elevenlabs/client@1.23.0");
  if (runId !== currentRunId) throw new Error("연결이 취소되었습니다.");

  if (!navigator.mediaDevices?.getUserMedia) {
    throw new Error("이 브라우저에서는 마이크를 사용할 수 없습니다.");
  }
  const permissionStream = await navigator.mediaDevices.getUserMedia({ audio: true });
  permissionStream.getTracks().forEach((track) => track.stop());
  if (runId !== currentRunId) throw new Error("연결이 취소되었습니다.");

  const conversation = await Conversation.startSession(
    buildElevenLabsOptions(sessionConfig, runId),
  );
  return {
    id: conversation.getId?.() || sessionConfig.conversationId,
    end: () => conversation.endSession(),
    setMuted: (muted) => conversation.setMicMuted(muted),
    sendText: (message) => conversation.sendUserMessage(message),
  };
}

function sendRealtimeEvent(dataChannel, event) {
  if (dataChannel.readyState !== "open") {
    throw new Error("OpenAI Realtime 데이터 채널이 열려 있지 않습니다.");
  }
  dataChannel.send(JSON.stringify(event));
}

function requestInitialRealtimeGreeting(dataChannel) {
  sendRealtimeEvent(dataChannel, {
    type: "response.create",
    response: {
      input: [],
      output_modalities: ["audio"],
      metadata: { purpose: "initial_greeting" },
      instructions:
        "지금 대화를 먼저 시작하세요. 어르신께 짧고 따뜻한 안부 인사를 건네고, 오늘 기분이나 상태를 묻는 질문 하나만 하세요. 특정 문구를 그대로 반복하지 말고 자연스럽게 표현을 달리하세요.",
    },
  });
}

async function startOpenAIRealtimeSession(runId) {
  if (!navigator.mediaDevices?.getUserMedia) {
    throw new Error("이 브라우저에서는 마이크를 사용할 수 없습니다.");
  }

  const token = await requestJson("/api/openai/realtime-token", { method: "POST" });
  if (runId !== currentRunId) throw new Error("연결이 취소되었습니다.");
  const mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true });
  if (runId !== currentRunId) {
    mediaStream.getTracks().forEach((track) => track.stop());
    throw new Error("연결이 취소되었습니다.");
  }
  const peerConnection = new RTCPeerConnection();
  const dataChannel = peerConnection.createDataChannel("oai-events");
  const audioElement = document.createElement("audio");
  audioElement.autoplay = true;
  audioElement.hidden = true;
  document.body.append(audioElement);

  const microphoneTrack = mediaStream.getAudioTracks()[0];
  // Prevent ambient sound/VAD from interrupting the assistant's first greeting.
  microphoneTrack.enabled = false;
  peerConnection.addTrack(microphoneTrack, mediaStream);
  peerConnection.ontrack = (event) => {
    audioElement.srcObject = event.streams[0];
    audioElement.play().catch((error) => {
      console.error("OpenAI Realtime audio playback failed", error);
      showError("브라우저가 Realtime 음성 재생을 막았습니다. 연결을 종료한 뒤 다시 시작해 주세요.");
    });
  };

  let closed = false;
  let sessionId = token.sessionId;
  let initialGreetingRequested = false;
  let initialGreetingCompleted = false;
  let activeAudioResponseId = null;
  const assistantDrafts = new Map();
  const deliveredAssistantItems = new Set();

  const cleanup = () => {
    if (closed) return;
    closed = true;
    dataChannel.close();
    peerConnection.close();
    mediaStream.getTracks().forEach((track) => track.stop());
    audioElement.srcObject = null;
    audioElement.remove();
  };

  const deliverAssistant = (event, fallbackText = "") => {
    const key = event.item_id || event.response_id || `response-${deliveredAssistantItems.size}`;
    if (deliveredAssistantItems.has(key)) return;
    const text = event.transcript || event.text || fallbackText;
    if (!text?.trim()) return;
    deliveredAssistantItems.add(key);
    assistantDrafts.delete(key);
    appendMessage("agent", text);
  };

  dataChannel.onmessage = (messageEvent) => {
    if (closed || runId !== currentRunId) return;
    let event;
    try {
      event = JSON.parse(messageEvent.data);
    } catch {
      return;
    }

    if (event.type === "session.created" || event.type === "session.updated") {
      sessionId = event.session?.id || sessionId;
      if (sessionId) elements.sessionId.textContent = sessionId;
    }
    if (event.type === "session.created" && !initialGreetingRequested) {
      initialGreetingRequested = true;
      elements.modeBadge.textContent = "첫 인사 준비 중";
      try {
        requestInitialRealtimeGreeting(dataChannel);
      } catch (error) {
        microphoneTrack.enabled = !isMuted;
        showError(
          error instanceof Error
            ? error.message
            : "OpenAI Realtime 첫 인사를 요청하지 못했습니다.",
        );
      }
    }

    if (event.type === "input_audio_buffer.speech_started") {
      elements.modeBadge.textContent = "사용자 말하는 중";
      dashboard.tracker.cancel("interrupted");
    }
    if (event.type === "input_audio_buffer.speech_stopped") {
      elements.modeBadge.textContent = "응답 대기";
      noteUserTurn("voice", "openai.input_audio_buffer.speech_stopped", { key: event.item_id });
    }
    if (event.type === "conversation.item.input_audio_transcription.completed") {
      appendMessage("user", event.transcript);
    }
    if (event.type === "response.created") {
      elements.modeBadge.textContent = "생성 중";
      const greeting = event.response?.metadata?.purpose === "initial_greeting"
        || (initialGreetingRequested && !initialGreetingCompleted && !dashboard.tracker.pending);
      dashboard.tracker.bindResponse(event.response?.id, { greeting });
      isAssistantSpeaking = true;
      updateControls();
    }
    if (event.type === "output_audio_buffer.started") {
      elements.modeBadge.textContent = "AGENT 말하는 중";
      activeAudioResponseId = event.response_id;
      noteAssistantStarted("openai.output_audio_buffer.started", event.response_id);
    }
    if (event.type === "output_audio_buffer.stopped" || event.type === "output_audio_buffer.cleared") {
      if (!activeAudioResponseId || activeAudioResponseId === event.response_id) {
        activeAudioResponseId = null;
        isAssistantSpeaking = false;
        elements.modeBadge.textContent = "듣는 중";
        if (initialGreetingRequested && !initialGreetingCompleted) {
          initialGreetingCompleted = true;
          microphoneTrack.enabled = !isMuted;
        }
        updateControls();
      }
    }
    if (
      event.type === "response.output_audio_transcript.delta" ||
      event.type === "response.output_text.delta"
    ) {
      const key = event.item_id || event.response_id || "current";
      assistantDrafts.set(key, `${assistantDrafts.get(key) || ""}${event.delta || ""}`);
    }
    if (
      event.type === "response.output_audio_transcript.done" ||
      event.type === "response.output_text.done"
    ) {
      const key = event.item_id || event.response_id || "current";
      deliverAssistant(event, assistantDrafts.get(key) || "");
    }
    if (event.type === "response.done") {
      addTokenUsage(event.response?.usage);
      if (["failed", "cancelled", "incomplete"].includes(event.response?.status)) {
        dashboard.tracker.cancel("failed", event.response?.id);
        isAssistantSpeaking = false;
        elements.modeBadge.textContent = "듣는 중";
        if (!initialGreetingCompleted) {
          initialGreetingCompleted = true;
          microphoneTrack.enabled = !isMuted;
        }
        if (event.response?.status === "failed") showError(event.response?.status_details?.error?.message || "응답 생성에 실패했습니다.");
        updateControls();
      }
    }
    if (event.type === "error") {
      dashboard.tracker.cancel("failed");
      isAssistantSpeaking = false;
      if (initialGreetingRequested && !initialGreetingCompleted) {
        microphoneTrack.enabled = !isMuted;
      }
      console.error("OpenAI Realtime event error", event);
      showError(event.error?.message || "OpenAI Realtime 오류가 발생했습니다.");
      updateControls();
    }
  };

  peerConnection.onconnectionstatechange = () => {
    const state = peerConnection.connectionState;
    if (["failed", "disconnected", "closed"].includes(state) && !closed) {
      cleanup();
      handleUnexpectedDisconnect(runId, `Realtime ${state}`);
    }
  };

  const channelReady = new Promise((resolve, reject) => {
    const timeoutId = window.setTimeout(
      () => reject(new Error("OpenAI Realtime 연결 시간이 초과됐습니다.")),
      15000,
    );
    dataChannel.addEventListener(
      "open",
      () => {
        window.clearTimeout(timeoutId);
        resolve();
      },
      { once: true },
    );
    dataChannel.addEventListener(
      "error",
      () => {
        window.clearTimeout(timeoutId);
        reject(new Error("OpenAI Realtime 데이터 채널 연결에 실패했습니다."));
      },
      { once: true },
    );
  });

  try {
    const offer = await peerConnection.createOffer();
    await peerConnection.setLocalDescription(offer);
    const sdpResponse = await fetch("https://api.openai.com/v1/realtime/calls", {
      method: "POST",
      body: offer.sdp,
      headers: {
        Authorization: `Bearer ${token.clientSecret}`,
        "Content-Type": "application/sdp",
      },
    });
    if (!sdpResponse.ok) {
      const detail = await sdpResponse.text();
      throw new Error(
        `OpenAI Realtime SDP 연결 실패 (HTTP ${sdpResponse.status}): ${detail.slice(0, 180)}`,
      );
    }
    await peerConnection.setRemoteDescription({
      type: "answer",
      sdp: await sdpResponse.text(),
    });
    await channelReady;
  } catch (error) {
    cleanup();
    throw error;
  }

  if (runId === currentRunId) markConnected(sessionId || `${token.model} · ${token.voice}`);
  return {
    id: sessionId,
    end: async () => cleanup(),
    setMuted: (muted) => {
      microphoneTrack.enabled = !muted;
    },
    sendText: (message) => {
      sendRealtimeEvent(dataChannel, {
        type: "conversation.item.create",
        item: {
          type: "message",
          role: "user",
          content: [{ type: "input_text", text: message }],
        },
      });
      sendRealtimeEvent(dataChannel, { type: "response.create" });
    },
  };
}

async function startSelectedMode() {
  if (activeSession || isConnecting || !isModeConfigured()) return;

  const runId = crypto.randomUUID();
  currentRunId = runId;
  isConnecting = true;
  showError("");
  beginMetrics();
  dashboard.beginSession({ mode: selectedMode, model: modeModel(selectedMode), sessionId: runId });
  setStatus("연결 준비 중…", "connecting");
  elements.modeBadge.textContent = "CONNECTING";
  updateControls();

  try {
    const session =
      selectedMode === "openai_realtime"
        ? await startOpenAIRealtimeSession(runId)
        : await startElevenLabsSession(selectedMode, runId);

    if (currentRunId !== runId) {
      await session.end();
      return;
    }
    activeSession = session;
    if (!uiConnected) markConnected(session.id);
    updateControls();
  } catch (error) {
    if (currentRunId !== runId) return;
    console.error(error);
    showError(error instanceof Error ? error.message : "음성 엔진 연결에 실패했습니다.");
    resetSessionUi("연결 실패");
  }
}

async function stopSession() {
  if (!activeSession && !isConnecting) return;
  const session = activeSession;
  currentRunId = null;
  activeSession = null;
  elements.stop.disabled = true;
  setStatus("종료 중…", "connecting");
  try {
    await session?.end();
  } catch (error) {
    console.error(error);
    showError("세션을 정상적으로 종료하지 못했습니다.");
  } finally {
    resetSessionUi();
  }
}

async function loadHealth() {
  try {
    healthData = await requestJson("/api/health");
    elements.modeButtons.forEach((button) => {
      const configured = isModeConfigured(button.dataset.mode);
      const state = button.querySelector(".config-state");
      state.textContent = configured ? "준비됨" : "설정 필요";
      state.classList.toggle("ready", configured);
    });
    selectMode(selectedMode);
  } catch (error) {
    console.error(error);
    showError("서버 설정 상태를 확인하지 못했습니다.");
    updateControls();
  }
}

elements.modeButtons.forEach((button) => {
  button.addEventListener("click", () => selectMode(button.dataset.mode));
});

elements.start.addEventListener("click", startSelectedMode);
elements.stop.addEventListener("click", stopSession);

elements.mute.addEventListener("click", () => {
  if (!activeSession) return;
  isMuted = !isMuted;
  activeSession.setMuted(isMuted);
  elements.mute.textContent = isMuted ? "마이크 켜기" : "마이크 끄기";
});

elements.messageForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const message = elements.messageInput.value.trim();
  if (!activeSession || !message || elements.send.disabled) return;
  try {
    noteUserTurn("text", "client.text_send");
    activeSession.sendText(message);
    appendMessage("user", message);
    elements.messageInput.focus();
  } catch (error) {
    dashboard.tracker.cancel("failed");
    showError(error instanceof Error ? error.message : "메시지를 보내지 못했습니다.");
  }
});

document.querySelectorAll("[data-prompt]").forEach((button) => {
  button.addEventListener("click", () => {
    elements.messageInput.value = button.dataset.prompt;
    elements.messageInput.focus();
  });
});

elements.clear.addEventListener("click", clearTranscript);

window.addEventListener("beforeunload", () => {
  dashboard.endSession();
  activeSession?.end();
});

resetMetrics();
dashboard.refresh();
updateControls();
loadHealth();
