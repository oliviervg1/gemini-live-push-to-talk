// Plays incoming 16-bit PCM frames at 24 kHz mono via chained AudioBufferSourceNodes.
// flush() stops everything immediately for barge-in.
export class Player {
  constructor(ctx) {
    this.ctx = ctx;
    this.rate = 24000;
    this.nextStart = 0;       // AudioContext time when the next buffer should play
    this.active = new Set();  // currently scheduled BufferSourceNodes
  }

  enqueue(arrayBuffer) {
    const i16 = new Int16Array(arrayBuffer);
    const f32 = new Float32Array(i16.length);
    for (let i = 0; i < i16.length; i++) f32[i] = i16[i] / 0x8000;

    const buf = this.ctx.createBuffer(1, f32.length, this.rate);
    buf.getChannelData(0).set(f32);

    const src = this.ctx.createBufferSource();
    src.buffer = buf;
    src.connect(this.ctx.destination);

    const now = this.ctx.currentTime;
    const startAt = Math.max(now + 0.1, this.nextStart);  // 100 ms initial jitter buffer
    src.start(startAt);
    this.nextStart = startAt + buf.duration;

    this.active.add(src);
    src.onended = () => this.active.delete(src);
  }

  flush() {
    for (const src of this.active) {
      try { src.stop(); } catch (_) { /* already stopped */ }
    }
    this.active.clear();
    this.nextStart = this.ctx.currentTime;
  }
}
