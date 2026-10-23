import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import AudioRecorder from '../components/AudioRecorder'
import { encodeWav } from '../audio/wav'
import { contexts, emitAudio, streams, tone } from './setup'

class EncodingWorker {
  static instances: EncodingWorker[] = []
  onmessage: ((event: MessageEvent) => void) | null = null
  onerror: (() => void) | null = null
  onmessageerror: (() => void) | null = null
  postMessage = vi.fn()
  terminate = vi.fn()
  constructor() { EncodingWorker.instances.push(this) }
}
beforeEach(() => {
  EncodingWorker.instances = []
  vi.stubGlobal('Worker', EncodingWorker)
})
async function stopLongRecording() {
  fireEvent.click(screen.getByRole('button', { name: '开始录音' }))
  await screen.findByRole('button', { name: '停止录音' })
  act(() => emitAudio([tone(48000, 440, 2)]))
  fireEvent.click(screen.getByRole('button', { name: '停止录音' }))
  await screen.findByRole('button', { name: '取消音频处理' })
  return EncodingWorker.instances[EncodingWorker.instances.length - 1]
}

describe('off-thread recording conversion', () => {
  it('releases capture before processing and terminates the worker after successful encoding', async () => {
    const ready = vi.fn()
    render(<AudioRecorder onAudioReady={ready} onError={vi.fn()} />)
    const worker = await stopLongRecording()
    expect(streams[0].getTracks()[0].stop).toHaveBeenCalledOnce()
    expect(contexts[0].close).toHaveBeenCalledOnce()
    expect(ready).not.toHaveBeenCalled()
    const [payload, transfers] = worker.postMessage.mock.calls[0]
    expect(transfers).toEqual(payload.chunks.map((chunk: Float32Array) => chunk.buffer))
    const blob = encodeWav(payload.chunks, payload.sourceRate)
    await act(async () => worker.onmessage?.({ data: { blob } } as MessageEvent))
    expect(ready).toHaveBeenCalledWith(blob)
    expect(worker.terminate).toHaveBeenCalledOnce()
    expect(screen.getByRole('button', { name: '开始录音' })).toBeEnabled()
  })

  it.each(['cancel', 'deactivate', 'unmount'] as const)('%s terminates conversion and ignores a late worker result', async action => {
    const ready = vi.fn()
    const error = vi.fn()
    const view = render(<AudioRecorder onAudioReady={ready} onError={error} />)
    const worker = await stopLongRecording()
    const queued = worker.onmessage!
    if (action === 'cancel') fireEvent.click(screen.getByRole('button', { name: '取消音频处理' }))
    else if (action === 'deactivate') view.rerender(<AudioRecorder active={false} onAudioReady={ready} onError={error} />)
    else view.unmount()
    expect(worker.terminate).toHaveBeenCalledOnce()
    await act(async () => queued({ data: { blob: new Blob(['obsolete']) } } as MessageEvent))
    expect(ready).not.toHaveBeenCalled()
    expect(error).not.toHaveBeenCalled()
    expect(worker.onmessage).toBeNull()
  })

  it('worker failure offers recovery without retaining resources', async () => {
    const error = vi.fn()
    render(<AudioRecorder onAudioReady={vi.fn()} onError={error} />)
    const worker = await stopLongRecording()
    await act(async () => worker.onerror?.())
    await waitFor(() => expect(error).toHaveBeenCalledWith('音频编码失败，请重新录制'))
    expect(worker.terminate).toHaveBeenCalledOnce()
    expect(screen.getByRole('button', { name: '开始录音' })).toBeEnabled()
  })
})
