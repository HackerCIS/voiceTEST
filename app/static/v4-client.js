const SAMPLE_RATE = 24000;

export function decodeBase64(value) {
  const binary = atob(value);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index);
  }
  return bytes;
}

function pcm16ToFloat32(bytes) {
  const length = bytes.byteLength - (bytes.byteLength % 2);
  const view = new DataView(bytes.buffer, bytes.byteOffset, length);
  const samples = new Float32Array(length / 2);
  for (let index = 0; index < samples.length; index += 1) {
    samples[index] = view.getInt16(index * 2, true) / 32768;
  }
  return samples;
}

export function playPcmBytes(bytes, { audioContext, sampleRate = SAMPLE_RATE, onStarted }) {
  let nextTime = 0;
  let started = false;
  const samples = pcm16ToFloat32(bytes);
  const buffer = audioContext.createBuffer(1, samples.length, sampleRate);
  buffer.getChannelData(0).set(samples);
  const source = audioContext.createBufferSource();
  source.buffer = buffer;
  source.connect(audioContext.destination);
  const startAt = Math.max(audioContext.currentTime + 0.03, nextTime);
  source.start(startAt);
  nextTime = startAt + buffer.duration;
  started = samples.length > 0;
  if (started) onStarted?.();
  return {
    started: () => started,
    whenFinished() {
      const waitMs = Math.max(0, (nextTime - audioContext.currentTime) * 1000);
      return new Promise((resolve) => {
        setTimeout(resolve, waitMs + 40);
      });
    },
    close() {
      try {
        source.stop();
      } catch {
        // Already finished.
      }
    },
  };
}
