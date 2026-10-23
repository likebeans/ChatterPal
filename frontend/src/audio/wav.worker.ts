import { encodeWav } from './wav'

self.onmessage = (event: MessageEvent<{ chunks: Float32Array[]; sourceRate: number }>) => {
  try {
    self.postMessage({ blob: encodeWav(event.data.chunks, event.data.sourceRate) })
  } catch (error) {
    self.postMessage({ error: error instanceof Error ? error.message : '音频编码失败，请重新录制' })
  }
}
