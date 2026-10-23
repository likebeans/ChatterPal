import { StrictMode } from 'react'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import * as wav from 'wav-decoder'
import AudioRecorder from '../components/AudioRecorder'
import { bytes, contexts, deferred, emitAudio, getUserMedia, microphoneStream, MockAudioContext, setSampleRate, streams, tone } from './setup'

async function startRecording() {
  fireEvent.click(screen.getByRole('button', { name: '开始录音' }))
  await screen.findByRole('button', { name: '停止录音' })
}
function released() {
  for (const stream of streams) expect(stream.getTracks()[0].stop).toHaveBeenCalledOnce()
  for (const context of contexts) {
    expect(context.processor.onaudioprocess).toBeNull()
    expect(context.source.disconnect).toHaveBeenCalled()
    expect(context.processor.disconnect).toHaveBeenCalled()
    expect(context.gain.disconnect).toHaveBeenCalled()
    expect(context.close).toHaveBeenCalledOnce()
  }
}

describe('recording lifecycle and encoding', () => {
  it('supports keyboard start/stop, repeated recording and distinct component resources', async () => {
    const user = userEvent.setup()
    const ready = vi.fn()
    const view = render(<StrictMode><AudioRecorder onAudioReady={ready} onError={vi.fn()} /></StrictMode>)
    screen.getByRole('button', { name: '开始录音' }).focus()
    await user.keyboard('{Enter}')
    await screen.findByRole('button', { name: '停止录音' })
    act(() => emitAudio([tone()]))
    await user.keyboard(' ')
    expect(ready).toHaveBeenCalledOnce()
    released()
    await startRecording()
    act(() => emitAudio([tone()]))
    fireEvent.click(screen.getByRole('button', { name: '停止录音' }))
    expect(ready).toHaveBeenCalledTimes(2)
    released()
    expect(Reflect.has(window, 'currentRecording')).toBe(false)
    view.unmount()
    released()
  })

  it('denied permission has an understandable error and can be retried', async () => {
    getUserMedia.mockRejectedValueOnce(new DOMException('Permission denied', 'NotAllowedError'))
    const error = vi.fn()
    render(<AudioRecorder onAudioReady={vi.fn()} onError={error} />)
    fireEvent.click(screen.getByRole('button', { name: '开始录音' }))
    await waitFor(() => expect(error).toHaveBeenCalledWith(expect.stringContaining('权限')))
    expect(screen.getByRole('button', { name: '开始录音' })).toBeEnabled()
    await startRecording()
    fireEvent.click(screen.getByRole('button', { name: '停止录音' }))
    released()
  })

  it('cancels a permission prompt, starts another recording, and closes only the late stream', async () => {
    const permission = deferred<MediaStream>()
    getUserMedia.mockReturnValueOnce(permission.promise)
    const ready = vi.fn()
    render(<AudioRecorder onAudioReady={ready} onError={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: '开始录音' }))
    fireEvent.click(screen.getByRole('button', { name: '取消录音请求' }))
    await startRecording()
    const activeContext = contexts[0]
    const lateStream = microphoneStream()
    await act(async () => permission.resolve(lateStream))
    expect(lateStream.getTracks()[0].stop).toHaveBeenCalledOnce()
    expect(activeContext.close).not.toHaveBeenCalled()
    expect(contexts).toHaveLength(1)
    act(() => emitAudio([tone()]))
    fireEvent.click(screen.getByRole('button', { name: '停止录音' }))
    expect(ready).toHaveBeenCalledOnce()
    released()
  })

  it('deactivation stops recording and ignores audio events already queued by the browser', async () => {
    const ready = vi.fn()
    const view = render(<AudioRecorder active onAudioReady={ready} onError={vi.fn()} />)
    await startRecording()
    const queued = contexts[0].processor.onaudioprocess!
    view.rerender(<AudioRecorder active={false} onAudioReady={ready} onError={vi.fn()} />)
    act(() => queued({ inputBuffer: { length: 4800, numberOfChannels: 1, getChannelData: () => tone() } } as unknown as AudioProcessingEvent))
    released()
    expect(ready).not.toHaveBeenCalled()
    view.rerender(<AudioRecorder active onAudioReady={ready} onError={vi.fn()} />)
    expect(screen.getByRole('button', { name: '开始录音' })).toBeEnabled()
  })

  it('deactivation before permission completion releases the late microphone without starting audio', async () => {
    const permission = deferred<MediaStream>()
    getUserMedia.mockReturnValueOnce(permission.promise)
    const ready = vi.fn()
    const view = render(<AudioRecorder active onAudioReady={ready} onError={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: '开始录音' }))
    view.rerender(<AudioRecorder active={false} onAudioReady={ready} onError={vi.fn()} />)
    await act(async () => permission.resolve(microphoneStream()))
    expect(contexts).toHaveLength(0)
    expect(streams[0].getTracks()[0].stop).toHaveBeenCalledOnce()
    expect(ready).not.toHaveBeenCalled()
  })

  it('empty recording reports an error instead of uploading a header-only WAV', async () => {
    const ready = vi.fn()
    const error = vi.fn()
    render(<AudioRecorder onAudioReady={ready} onError={error} />)
    await startRecording()
    fireEvent.click(screen.getByRole('button', { name: '停止录音' }))
    expect(ready).not.toHaveBeenCalled()
    expect(error).toHaveBeenCalledWith(expect.stringContaining('未采集到音频'))
    released()
  })

  it('initialization can fail, release all partial resources and succeed on the next attempt', async () => {
    let failed = false
    vi.stubGlobal('AudioContext', class extends MockAudioContext {
      constructor() {
        super()
        if (!failed) {
          failed = true
          this.createMediaStreamSource.mockImplementationOnce(() => { throw new Error('Device disappeared') })
        }
      }
    })
    const error = vi.fn()
    const ready = vi.fn()
    render(<AudioRecorder onAudioReady={ready} onError={error} />)
    fireEvent.click(screen.getByRole('button', { name: '开始录音' }))
    await waitFor(() => expect(error).toHaveBeenCalledOnce())
    expect(contexts[0].close).toHaveBeenCalledOnce()
    expect(streams[0].getTracks()[0].stop).toHaveBeenCalledOnce()
    await startRecording()
    act(() => emitAudio([tone()]))
    fireEvent.click(screen.getByRole('button', { name: '停止录音' }))
    expect(ready).toHaveBeenCalledOnce()
    expect(contexts[1].close).toHaveBeenCalledOnce()
  })

  it.each([16000, 44100, 48000])('actually resamples %iHz input to a decodable 100ms 440Hz mono PCM16 WAV', async rate => {
    setSampleRate(rate)
    const ready = vi.fn()
    render(<AudioRecorder onAudioReady={ready} onError={vi.fn()} />)
    await startRecording()
    act(() => emitAudio([tone(rate)]))
    fireEvent.click(screen.getByRole('button', { name: '停止录音' }))
    const buffer = await bytes(ready.mock.calls[0][0] as Blob)
    const { channelData, sampleRate } = await wav.decode(buffer)
    expect(sampleRate).toBe(16000)
    expect(channelData).toHaveLength(1)
    expect(channelData[0]).toHaveLength(1600)
    expect(new DataView(buffer).getUint16(34, true)).toBe(16)
    let positiveCrossings = 0
    for (let index = 1; index < channelData[0].length; index++) {
      if (channelData[0][index - 1] <= 0 && channelData[0][index] > 0) positiveCrossings++
    }
    expect(positiveCrossings).toBeGreaterThanOrEqual(43)
    expect(positiveCrossings).toBeLessThanOrEqual(45)
    released()
  })

  it('averages stereo channels rather than doubling amplitude and filters aliasing above 8kHz', async () => {
    const ready = vi.fn()
    render(<AudioRecorder onAudioReady={ready} onError={vi.fn()} />)
    await startRecording()
    act(() => emitAudio([tone(48000), tone(48000).map(sample => -sample)]))
    fireEvent.click(screen.getByRole('button', { name: '停止录音' }))
    const stereo = await wav.decode(await bytes(ready.mock.calls[0][0] as Blob))
    expect(Math.max(...stereo.channelData[0].map(Math.abs))).toBe(0)
    await startRecording()
    act(() => emitAudio([tone(48000, 12000)]))
    fireEvent.click(screen.getByRole('button', { name: '停止录音' }))
    const filtered = await wav.decode(await bytes(ready.mock.calls[1][0] as Blob))
    // Ignore the 5ms edge transient; a 12kHz tone must not become a 4kHz speech tone.
    expect(Math.max(...filtered.channelData[0].slice(80, -80).map(Math.abs))).toBeLessThan(0.01)
  })

  it('a disconnected microphone releases capture and permits another attempt', async () => {
    const ready = vi.fn()
    const error = vi.fn()
    render(<AudioRecorder onAudioReady={ready} onError={error} />)
    await startRecording()
    const track = streams[0].getTracks()[0] as unknown as { addEventListener: ReturnType<typeof vi.fn> }
    const ended = track.addEventListener.mock.calls.find(([event]) => event === 'ended')![1] as () => void
    act(() => ended())
    expect(error).toHaveBeenCalledWith('麦克风已断开，请连接设备后重新录音')
    expect(ready).not.toHaveBeenCalled()
    released()
    await startRecording()
    act(() => emitAudio([tone()]))
    fireEvent.click(screen.getByRole('button', { name: '停止录音' }))
    expect(ready).toHaveBeenCalledOnce()
    released()
  })

  it('a current resume failure releases the partial graph and restores controls', async () => {
    vi.stubGlobal('AudioContext', class extends MockAudioContext {
      constructor() {
        super()
        this.state = 'suspended'
        this.resume.mockRejectedValueOnce(new Error('Audio device unavailable'))
      }
    })
    const error = vi.fn()
    render(<AudioRecorder onAudioReady={vi.fn()} onError={error} />)
    fireEvent.click(screen.getByRole('button', { name: '开始录音' }))
    await waitFor(() => expect(error).toHaveBeenCalledOnce())
    released()
    expect(screen.getByRole('button', { name: '开始录音' })).toBeEnabled()
  })

  it('cancellation while AudioContext.resume is pending remains released when resume rejects', async () => {
    const resumed = deferred<void>()
    vi.stubGlobal('AudioContext', class extends MockAudioContext {
      constructor() {
        super()
        this.state = 'suspended'
        this.resume.mockImplementationOnce(() => resumed.promise)
      }
    })
    const error = vi.fn()
    const view = render(<AudioRecorder onAudioReady={vi.fn()} onError={error} />)
    fireEvent.click(screen.getByRole('button', { name: '开始录音' }))
    await waitFor(() => expect(contexts[0]?.resume).toHaveBeenCalledOnce())
    view.unmount()
    await act(async () => resumed.reject(new Error('Closed during resume')))
    released()
    expect(error).not.toHaveBeenCalled()
  })
})
