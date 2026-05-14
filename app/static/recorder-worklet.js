// AudioWorklet processor: takes float32 mono audio at the AudioContext's
// native rate, downsamples to 16 kHz, converts to 16-bit little-endian PCM,
// and posts ArrayBuffers of ~20ms (640 bytes = 320 samples) to the main thread.
class RecorderProcessor extends AudioWorkletProcessor {
  constructor(opts) {
    super();
    const { sourceRate } = opts.processorOptions;
    this.sourceRate = sourceRate;
    this.targetRate = 16000;
    this.ratio = sourceRate / this.targetRate; // e.g. 48000/16000 = 3
    this.frameSamples = 320; // 20 ms @ 16 kHz
    this.outBuf = new Int16Array(this.frameSamples);
    this.outIdx = 0;
    this.acc = 0;
    this.accCount = 0;
    this.sampleSinceLast = 0;
  }

  process(inputs) {
    const input = inputs[0];
    if (!input || input.length === 0) return true;
    const channel = input[0];
    if (!channel) return true;

    for (let i = 0; i < channel.length; i++) {
      this.acc += channel[i];
      this.accCount += 1;
      this.sampleSinceLast += 1;
      if (this.sampleSinceLast >= this.ratio) {
        const avg = this.acc / this.accCount;
        const s = Math.max(-1, Math.min(1, avg));
        this.outBuf[this.outIdx++] = s < 0 ? s * 0x8000 : s * 0x7fff;
        this.acc = 0;
        this.accCount = 0;
        this.sampleSinceLast -= this.ratio;
        if (this.outIdx >= this.frameSamples) {
          this.port.postMessage(this.outBuf.slice().buffer);
          this.outIdx = 0;
        }
      }
    }
    return true;
  }
}

registerProcessor("recorder-processor", RecorderProcessor);
