// Microphone capture for the Voice Agent API: mono PCM16 at 24 kHz, sent in
// ~50 ms chunks. The AudioContext runs at whatever rate the device gives
// (usually 44.1 or 48 kHz), so this resamples with linear interpolation.
// https://www.assemblyai.com/docs/voice-agents/voice-agent-api/audio-format
const TARGET_RATE = 24000
const CHUNK = 1200 // 50 ms at 24 kHz

class MicProcessor extends AudioWorkletProcessor {
  constructor() {
    super()
    this.ratio = sampleRate / TARGET_RATE
    this.pos = 0 // fractional read position into the carried input
    this.carry = new Float32Array(0)
    this.out = new Int16Array(CHUNK)
    this.fill = 0
  }

  process(inputs) {
    const ch = inputs[0] && inputs[0][0]
    if (!ch) return true
    // Prepend what was left over from the last block.
    const buf = new Float32Array(this.carry.length + ch.length)
    buf.set(this.carry)
    buf.set(ch, this.carry.length)
    let peak = 0
    while (this.pos + 1 < buf.length) {
      const i = Math.floor(this.pos)
      const f = this.pos - i
      const s = buf[i] + (buf[i + 1] - buf[i]) * f
      const a = Math.abs(s)
      if (a > peak) peak = a
      this.out[this.fill++] = Math.max(-32768, Math.min(32767, Math.round(s * 32767)))
      if (this.fill === CHUNK) {
        this.port.postMessage({ pcm: this.out.buffer.slice(0), peak })
        this.fill = 0
        peak = 0
      }
      this.pos += this.ratio
    }
    const keep = Math.floor(this.pos)
    this.carry = buf.slice(keep)
    this.pos -= keep
    return true
  }
}

registerProcessor('mic-processor', MicProcessor)
