const WAV_SAMPLE_RATE = 16000

// A windowed-sinc low-pass filter removes frequencies above the new Nyquist limit.
// Merely changing the WAV header (or dropping samples) changes pitch or aliases speech.
function resample(samples: Float32Array, sourceRate: number): Float32Array {
  if (sourceRate === WAV_SAMPLE_RATE) return samples
  const ratio = sourceRate / WAV_SAMPLE_RATE
  const length = Math.max(1, Math.round(samples.length / ratio))
  const output = new Float32Array(length)
  const cutoff = Math.min(1, 1 / ratio) * 0.95
  const radius = Math.ceil(24 / cutoff)

  for (let index = 0; index < length; index++) {
    const position = index * ratio
    let value = 0
    let weightSum = 0
    const first = Math.max(0, Math.ceil(position - radius))
    const last = Math.min(samples.length - 1, Math.floor(position + radius))
    for (let input = first; input <= last; input++) {
      const distance = position - input
      const phase = Math.PI * distance * cutoff
      const sinc = phase === 0 ? 1 : Math.sin(phase) / phase
      const window = 0.5 * (1 + Math.cos(Math.PI * distance / radius))
      const weight = cutoff * sinc * window
      value += samples[input] * weight
      weightSum += weight
    }
    output[index] = weightSum === 0 ? 0 : value / weightSum
  }
  return output
}

export function encodeWav(chunks: Float32Array[], sourceRate: number): Blob {
  const length = chunks.reduce((total, chunk) => total + chunk.length, 0)
  if (length === 0 || !Number.isFinite(sourceRate) || sourceRate <= 0) {
    throw new Error('未采集到音频，请稍等片刻后停止录音并重试')
  }
  const input = new Float32Array(length)
  let offset = 0
  for (const chunk of chunks) {
    input.set(chunk, offset)
    offset += chunk.length
  }
  const samples = resample(input, sourceRate)
  const buffer = new ArrayBuffer(44 + samples.length * 2)
  const view = new DataView(buffer)
  const text = (position: number, value: string) => {
    for (let index = 0; index < value.length; index++) view.setUint8(position + index, value.charCodeAt(index))
  }
  text(0, 'RIFF')
  view.setUint32(4, buffer.byteLength - 8, true)
  text(8, 'WAVE')
  text(12, 'fmt ')
  view.setUint32(16, 16, true)
  view.setUint16(20, 1, true)
  view.setUint16(22, 1, true)
  view.setUint32(24, WAV_SAMPLE_RATE, true)
  view.setUint32(28, WAV_SAMPLE_RATE * 2, true)
  view.setUint16(32, 2, true)
  view.setUint16(34, 16, true)
  text(36, 'data')
  view.setUint32(40, samples.length * 2, true)
  for (let index = 0; index < samples.length; index++) {
    const sample = Number.isFinite(samples[index]) ? Math.max(-1, Math.min(1, samples[index])) : 0
    view.setInt16(44 + index * 2, Math.round(sample * (sample < 0 ? 32768 : 32767)), true)
  }
  return new Blob([buffer], { type: 'audio/wav' })
}

export function audioSource(value: string): string {
  const source = value.trim()
  if (/^(blob:|data:audio\/|https?:\/\/|\/|\.\.?\/)/i.test(source)) return source
  // API audio_url values may also be relative paths. Bare Base64 has no extension.
  if (/\.(wav|mp3|ogg|webm|m4a|mp4)([?#].*)?$/i.test(source)) return source
  return `data:audio/mpeg;base64,${source}`
}
