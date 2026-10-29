const LIVEKIT_CLIENT_URL = "https://cdn.jsdelivr.net/npm/livekit-client@2.15.7/+esm";

function transcriptRole(participant, ParticipantKind) {
  if (!participant) return "agent";
  if (participant.kind === ParticipantKind.SIP) return "user";
  if (participant.kind === ParticipantKind.AGENT) return "agent";
  const identity = participant.identity || "";
  if (/^\+?\d{8,}$/.test(identity)) return "user";
  return "agent";
}

export async function connectLivekitTranscript({ serverUrl, token, isCurrent, onSegment }) {
  const { Room, RoomEvent, ParticipantKind } = await import(LIVEKIT_CLIENT_URL);
  const room = new Room();
  room.on(RoomEvent.TranscriptionReceived, (segments, participant) => {
    if (!isCurrent()) return;
    const role = transcriptRole(participant, ParticipantKind);
    for (const segment of segments) {
      const text = (segment.text || "").trim();
      if (!text) continue;
      onSegment({
        id: segment.id || `${role}-${text.slice(0, 24)}`,
        role,
        text,
        final: Boolean(segment.final),
      });
    }
  });
  await room.connect(serverUrl, token);
  return {
    stop: async () => {
      await room.disconnect();
    },
  };
}
