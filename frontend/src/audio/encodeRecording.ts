import { encodeWav } from './wav'

// Keep tiny recordings synchronous, but move paragraph-length conversion off the
// UI thread. Transferring the captured buffers avoids cloning the sample data.
export function encodeRecording(chunks: Float32Array[], sourceRate: number, signal: AbortSignal): Blob | Promise<Blob> {
  const samples = chunks.reduce((total, chunk) => total + chunk.length, 0)
  if (samples <= sourceRate) return encodeWav(chunks, sourceRate)

  return new Promise<Blob>((resolve, reject) => {
    if (signal.aborted) { reject(new DOMException('Cancelled', 'AbortError')); return }
    const worker = new Worker(new URL('./wav.worker.ts', import.meta.url), { type: 'module' })
    const cleanup = () => {
      signal.removeEventListener('abort', abort)
      worker.onmessage = null
      worker.onerror = null
      worker.onmessageerror = null
      worker.terminate()
    }
    const abort = () => { cleanup(); reject(new DOMException('Cancelled', 'AbortError')) }
    signal.addEventListener('abort', abort, { once: true })
    worker.onmessage = (event: MessageEvent<{ blob?: Blob; error?: string }>) => {
      cleanup()
      if (event.data.blob instanceof Blob) resolve(event.data.blob)
      else reject(new Error(event.data.error || '音频编码失败，请重新录制'))
    }
    worker.onerror = () => { cleanup(); reject(new Error('音频编码失败，请重新录制')) }
    worker.onmessageerror = () => { cleanup(); reject(new Error('音频编码失败，请重新录制')) }
    try {
      worker.postMessage({ chunks, sourceRate }, chunks.map(chunk => chunk.buffer as ArrayBuffer))
    } catch (error) {
      cleanup()
      reject(error)
    }
  })
}
