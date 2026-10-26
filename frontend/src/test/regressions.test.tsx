import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import axios from 'axios'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as wav from 'wav-decoder'
import App from '../App'
import AudioRecorder from '../components/AudioRecorder'
import ChatInterface from '../components/ChatInterface'
import PronunciationCorrection from '../components/PronunciationCorrection'
import { bytes, contexts, deferred, emitAudio, getUserMedia, microphoneStream, MockAudioContext, objectUrls, streams, tone } from './setup'

beforeEach(() => {
  vi.spyOn(axios, 'get').mockResolvedValue({ data: [] })
})

async function recordPronunciation() {
  fireEvent.click(screen.getByRole('button', { name: /开始录音/ }))
  await screen.findByRole('button', { name: /停止录音/ })
  act(() => emitAudio([tone()]))
  fireEvent.click(screen.getByRole('button', { name: /停止录音/ }))
  await screen.findByRole('button', { name: /分析语音/ })
}

const basicResult = { overall_score: 78, recognized_text: 'I went home.', corrections: [], suggestions: ['Slow down.'] }
const scenarioContext = (description: string) => ({ context_description: description, sample_dialogues: [], key_vocabulary: [], grammar_focus: [], pronunciation_targets: [] })

describe('recording boundary regressions', () => {
  it('releases microphone and audio nodes when recorder unmounts', async () => {
    const view = render(<AudioRecorder onAudioReady={vi.fn()} onError={vi.fn()} />)
    fireEvent.click(screen.getByTitle('开始录音'))
    await screen.findByTitle('停止录音')
    const context = contexts[0]
    view.unmount()
    expect(streams[0].getTracks()[0].stop).toHaveBeenCalledOnce()
    expect(context.source.disconnect).toHaveBeenCalled()
    expect(context.processor.disconnect).toHaveBeenCalled()
    expect(context.close).toHaveBeenCalledOnce()
  })

  it('releases a stream whose permission arrives after unmount', async () => {
    const permission = deferred<MediaStream>()
    getUserMedia.mockReturnValueOnce(permission.promise)
    const ready = vi.fn()
    const view = render(<AudioRecorder onAudioReady={ready} onError={vi.fn()} />)
    fireEvent.click(screen.getByTitle('开始录音'))
    view.unmount()
    const stream = microphoneStream()
    await act(async () => permission.resolve(stream))
    expect(stream.getTracks()[0].stop).toHaveBeenCalledOnce()
    expect(contexts).toHaveLength(0)
    expect(ready).not.toHaveBeenCalled()
  })

  it('releases partially initialized resources and allows retry', async () => {
    vi.stubGlobal('AudioContext', class extends MockAudioContext {
      constructor() {
        super()
        this.createScriptProcessor.mockImplementationOnce(() => { throw new Error('unsupported') })
      }
    })
    const onError = vi.fn()
    render(<AudioRecorder onAudioReady={vi.fn()} onError={onError} />)
    fireEvent.click(screen.getByTitle('开始录音'))
    await waitFor(() => expect(onError).toHaveBeenCalled())
    expect(streams[0].getTracks()[0].stop).toHaveBeenCalledOnce()
    expect(contexts[0].close).toHaveBeenCalledOnce()
  })

  it('pronunciation upload is decodable PCM16 mono 16kHz WAV with the original duration', async () => {
    const post = vi.spyOn(axios, 'post').mockResolvedValue({ data: basicResult })
    render(<PronunciationCorrection />)
    fireEvent.change(screen.getByLabelText('参考文本'), { target: { value: 'I went home.' } })
    await recordPronunciation()
    fireEvent.click(screen.getByRole('button', { name: /分析语音/ }))
    await waitFor(() => expect(post).toHaveBeenCalledOnce())
    const file = (post.mock.calls[0][1] as FormData).get('audio') as File
    const buffer = await bytes(file)
    const decoded = await wav.decode(buffer)
    expect(decoded.sampleRate).toBe(16000)
    expect(decoded.channelData).toHaveLength(1)
    expect(decoded.channelData[0]).toHaveLength(1600)
    expect(new DataView(buffer).getUint16(34, true)).toBe(16)
    expect(Math.max(...decoded.channelData[0])).toBeGreaterThan(0.4)
  })
})

describe('chat boundary regressions', () => {
  it('plays the recorded blob URL directly and releases it on clear', async () => {
    vi.spyOn(axios, 'post').mockResolvedValue({ data: { recognized_text: 'Hello.', response: 'Hi.', session_id: 'session-a', audio_url: '/api/audio/reply.wav' } })
    const view = render(<ChatInterface />)
    fireEvent.click(screen.getByTitle('开始录音'))
    await screen.findByTitle('停止录音')
    act(() => emitAudio([tone()]))
    fireEvent.click(screen.getByTitle('停止录音'))
    await screen.findByText('Hello.')
    fireEvent.click(view.container.querySelector('.message-bubble.user .audio-play-btn')!)
    expect(view.container.querySelector('audio[src]')?.getAttribute('src')).toBe('blob:recording-1')
    fireEvent.click(screen.getByRole('button', { name: /清除对话/ }))
    expect(objectUrls.size).toBe(0)
  })

  it('clear aborts pending chat and ignores a late response including its session id', async () => {
    const late = deferred<{ data: { response: string; session_id: string } }>()
    const post = vi.spyOn(axios, 'post').mockReturnValueOnce(late.promise).mockResolvedValue({ data: { response: 'new reply', session_id: 'new-session' } })
    render(<ChatInterface />)
    fireEvent.change(screen.getByPlaceholderText('输入您想要练习的对话内容...'), { target: { value: 'old message' } })
    fireEvent.click(screen.getByRole('button', { name: '发送' }))
    fireEvent.click(screen.getByRole('button', { name: /清除对话/ }))
    await act(async () => late.resolve({ data: { response: 'stale reply', session_id: 'old-session' } }))
    expect(screen.queryByText('stale reply')).not.toBeInTheDocument()
    expect(post.mock.calls[0][2]?.signal?.aborted).toBe(true)
    fireEvent.change(screen.getByPlaceholderText('输入您想要练习的对话内容...'), { target: { value: 'new message' } })
    fireEvent.click(screen.getByRole('button', { name: '发送' }))
    await screen.findByText('new reply')
    expect(post.mock.calls[1][1]).toMatchObject({ session_id: null })
  })

  it('keeps completed conversation and its session when switching tabs', async () => {
    const post = vi.spyOn(axios, 'post').mockResolvedValue({ data: { response: 'saved reply', session_id: 'saved-session' } })
    render(<App initialTab="chat" />)
    fireEvent.change(screen.getByPlaceholderText('输入您想要练习的对话内容...'), { target: { value: 'Hello' } })
    fireEvent.click(screen.getByRole('button', { name: '发送' }))
    await screen.findByText('saved reply')
    fireEvent.click(screen.getByRole('button', { name: '口语纠正' }))
    fireEvent.click(screen.getByRole('button', { name: '对话练习' }))
    expect(screen.getByText('saved reply')).toBeVisible()
    fireEvent.change(screen.getByPlaceholderText('输入您想要练习的对话内容...'), { target: { value: 'Again' } })
    fireEvent.click(screen.getByRole('button', { name: '发送' }))
    await waitFor(() => expect(post).toHaveBeenCalledTimes(2))
    expect(post.mock.calls[1][1]).toMatchObject({ session_id: 'saved-session' })
  })
})

describe('pronunciation result and request regressions', () => {
  it('renders the frozen basic fields, suggestions and uncalibrated score notice', async () => {
    vi.spyOn(axios, 'post').mockResolvedValue({ data: basicResult })
    render(<PronunciationCorrection />)
    fireEvent.change(screen.getByLabelText('参考文本'), { target: { value: 'I went home.' } })
    await recordPronunciation()
    fireEvent.click(screen.getByRole('button', { name: /分析语音/ }))
    expect(await screen.findByText('I went home.', { selector: 'p' })).toBeVisible()
    expect(screen.queryByText('78')).not.toBeInTheDocument()
    expect(screen.getByText(/服务未提供测量依据/)).toBeVisible()
    expect(screen.getByText('Slow down.')).toBeVisible()
    expect(screen.getByText(/评分尚未校准/)).toBeVisible()
  })

  it('re-record invalidates an in-flight analysis even if cancellation is ignored', async () => {
    const late = deferred<{ data: typeof basicResult }>()
    const post = vi.spyOn(axios, 'post').mockReturnValueOnce(late.promise)
    render(<PronunciationCorrection />)
    fireEvent.change(screen.getByLabelText('参考文本'), { target: { value: 'I went home.' } })
    await recordPronunciation()
    fireEvent.click(screen.getByRole('button', { name: /分析语音/ }))
    fireEvent.click(screen.getByRole('button', { name: /重新录制/ }))
    await act(async () => late.resolve({ data: basicResult }))
    expect(screen.queryByText('I went home.', { selector: 'p' })).not.toBeInTheDocument()
    expect(post.mock.calls[0][2]?.signal?.aborted).toBe(true)
  })

  it('scenario B remains selected when scenario A context arrives last', async () => {
    const first = deferred<{ data: ReturnType<typeof scenarioContext> }>()
    const second = deferred<{ data: ReturnType<typeof scenarioContext> }>()
    vi.mocked(axios.get).mockImplementation((url) => {
      if (url === '/api/scenarios') return Promise.resolve({ data: [
        { scenario_type: 'daily_conversation', difficulty_level: 'beginner', display_name: 'Scenario A' },
        { scenario_type: 'business_meeting', difficulty_level: 'intermediate', display_name: 'Scenario B' },
      ] })
      if (url === '/api/scenarios/daily_conversation/beginner') return first.promise
      return second.promise
    })
    render(<PronunciationCorrection />)
    fireEvent.click(screen.getByRole('button', { name: 'AI智能分析' }))
    await screen.findByRole('option', { name: 'Scenario A' })
    fireEvent.change(screen.getByLabelText('练习场景（可选）'), { target: { value: 'daily_conversation-beginner' } })
    fireEvent.change(screen.getByLabelText('练习场景（可选）'), { target: { value: 'business_meeting-intermediate' } })
    await act(async () => second.resolve({ data: scenarioContext('Context B') }))
    await act(async () => first.resolve({ data: scenarioContext('Context A') }))
    expect(screen.getByText('Context B')).toBeVisible()
    expect(screen.queryByText('Context A')).not.toBeInTheDocument()
  })
})
