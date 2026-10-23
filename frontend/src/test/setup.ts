import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach, beforeEach, vi } from 'vitest'

export function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej })
  return { promise, resolve, reject }
}

export const streams: Array<{ getTracks: () => Array<{ stop: ReturnType<typeof vi.fn> }> }> = []
export const contexts: MockAudioContext[] = []
export const getUserMedia = vi.fn()
export const objectUrls = new Map<string, Blob>()
export let sourceSampleRate = 48000
export function setSampleRate(rate: number) { sourceSampleRate = rate }

export function microphoneStream() {
  const track = { stop: vi.fn(), addEventListener: vi.fn(), removeEventListener: vi.fn() }
  const stream = { getTracks: () => [track] }
  streams.push(stream)
  return stream as unknown as MediaStream
}

class MockNode {
  connect = vi.fn()
  disconnect = vi.fn()
}

export class MockAudioContext {
  sampleRate = sourceSampleRate
  state = 'running'
  destination = new MockNode()
  source = new MockNode()
  processor = Object.assign(new MockNode(), { onaudioprocess: null as ((event: AudioProcessingEvent) => void) | null })
  gain = Object.assign(new MockNode(), { gain: { value: 1 } })
  createMediaStreamSource = vi.fn(() => this.source)
  createScriptProcessor = vi.fn(() => this.processor)
  createGain = vi.fn(() => this.gain)
  resume = vi.fn(async () => { this.state = 'running' })
  close = vi.fn(async () => { this.state = 'closed' })
  constructor() { contexts.push(this) }
}

// The browser's MediaRecorder emits WebM bytes. Labelling those bytes WAV must fail decoding.
class MockMediaRecorder {
  ondataavailable: ((event: { data: Blob }) => void) | null = null
  onstop: (() => void) | null = null
  state = 'inactive'
  start() { this.state = 'recording' }
  stop() {
    this.state = 'inactive'
    this.ondataavailable?.({ data: new Blob([new Uint8Array([0x1a, 0x45, 0xdf, 0xa3, 0, 1])], { type: 'audio/webm' }) })
    this.onstop?.()
  }
}

export function emitAudio(channels: Float32Array[]) {
  contexts[contexts.length - 1]?.processor.onaudioprocess?.({
    inputBuffer: {
      length: channels[0].length,
      numberOfChannels: channels.length,
      sampleRate: sourceSampleRate,
      getChannelData: (channel: number) => channels[channel],
    },
  } as unknown as AudioProcessingEvent)
}

export function tone(rate = sourceSampleRate, frequency = 440, seconds = 0.1) {
  return Float32Array.from({ length: Math.round(rate * seconds) }, (_, index) => Math.sin(2 * Math.PI * frequency * index / rate) * 0.5)
}

export function bytes(blob: Blob): Promise<ArrayBuffer> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(reader.result as ArrayBuffer)
    reader.onerror = reject
    reader.readAsArrayBuffer(blob)
  })
}

beforeEach(() => {
  streams.length = 0
  contexts.length = 0
  objectUrls.clear()
  sourceSampleRate = 48000
  getUserMedia.mockReset().mockImplementation(async () => microphoneStream())
  Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: { getUserMedia } })
  vi.stubGlobal('AudioContext', MockAudioContext)
  vi.stubGlobal('MediaRecorder', MockMediaRecorder)
  vi.stubGlobal('scrollTo', vi.fn())
  Object.defineProperty(Element.prototype, 'scrollIntoView', { configurable: true, value: vi.fn() })
  vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue()
  vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => {})
  vi.spyOn(HTMLMediaElement.prototype, 'load').mockImplementation(() => {})
  let urlIndex = 0
  Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: vi.fn((blob: Blob) => {
    const url = `blob:recording-${++urlIndex}`
    objectUrls.set(url, blob)
    return url
  }) })
  Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: vi.fn((url: string) => { objectUrls.delete(url) }) })
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  Reflect.deleteProperty(window, 'currentRecording')
})
