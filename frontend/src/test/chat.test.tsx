import { act, fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import axios from 'axios'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import App from '../App'
import ChatInterface from '../components/ChatInterface'
import { contexts, deferred, emitAudio, objectUrls, streams, tone } from './setup'

beforeEach(() => { vi.spyOn(axios, 'get').mockResolvedValue({ data: [] }) })

function send(text = 'Hello') {
  fireEvent.change(screen.getByRole('textbox', { name: '对话内容' }), { target: { value: text } })
  fireEvent.click(screen.getByRole('button', { name: '发送' }))
}
async function record() {
  fireEvent.click(screen.getByRole('button', { name: '开始录音' }))
  await screen.findByRole('button', { name: '停止录音' })
  act(() => emitAudio([tone()]))
  fireEvent.click(screen.getByRole('button', { name: '停止录音' }))
}
const chat = { response: 'Hi.', session_id: 'current-session', recognized_text: 'Hello.', audio_url: '/api/audio/reply.wav' }

describe('chat concurrency and resource ownership', () => {
  it('keyboard submit supports Shift+Enter, ignores IME composition and prevents duplicate sends', async () => {
    const late = deferred<{ data: typeof chat }>()
    const post = vi.spyOn(axios, 'post').mockReturnValue(late.promise)
    const user = userEvent.setup()
    render(<ChatInterface />)
    const input = screen.getByRole('textbox', { name: '对话内容' })
    await user.type(input, 'Hello')
    await user.keyboard('{Shift>}{Enter}{/Shift}')
    expect(post).not.toHaveBeenCalled()
    fireEvent.keyDown(input, { key: 'Enter', isComposing: true })
    expect(post).not.toHaveBeenCalled()
    await user.keyboard('{Enter}{Enter}')
    expect(post).toHaveBeenCalledOnce()
    expect(screen.getByRole('button', { name: '发送中...' })).toBeDisabled()
    await act(async () => late.resolve({ data: chat }))
  })

  it('an obsolete finally cannot enable inputs while a newer request is pending', async () => {
    const first = deferred<{ data: typeof chat }>()
    const second = deferred<{ data: typeof chat }>()
    vi.spyOn(axios, 'post').mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise)
    render(<ChatInterface />)
    send('first')
    fireEvent.click(screen.getByRole('button', { name: '清除对话' }))
    send('second')
    await act(async () => first.resolve({ data: { ...chat, response: 'Obsolete' } }))
    expect(screen.getByRole('button', { name: '发送中...' })).toBeDisabled()
    expect(screen.queryByText('Obsolete')).not.toBeInTheDocument()
    await act(async () => second.resolve({ data: chat }))
    expect(screen.getByText('Hi.')).toBeVisible()
    expect(screen.getByRole('textbox', { name: '对话内容' })).toBeEnabled()
  })

  it('clear before voice response ignores stale audio and session without leaving object URLs', async () => {
    const late = deferred<{ data: typeof chat }>()
    const post = vi.spyOn(axios, 'post').mockReturnValueOnce(late.promise).mockResolvedValue({ data: chat })
    render(<ChatInterface />)
    await record()
    fireEvent.click(screen.getByRole('button', { name: '清除对话' }))
    expect(post.mock.calls[0][2]?.signal?.aborted).toBe(true)
    expect(objectUrls.size).toBe(0)
    await act(async () => late.resolve({ data: chat }))
    expect(screen.queryByText('Hi.')).not.toBeInTheDocument()
    expect(screen.queryByText('Hello.')).not.toBeInTheDocument()
    expect(objectUrls.size).toBe(0)
    send('new message')
    await screen.findByText('Hi.')
    expect(post.mock.calls[1][1]).toMatchObject({ session_id: null })
  })

  it('tab change keeps submitted turns and their session consistent while releasing recording', async () => {
    const second = deferred<{ data: typeof chat }>()
    const post = vi.spyOn(axios, 'post').mockResolvedValueOnce({ data: chat }).mockReturnValueOnce(second.promise).mockResolvedValue({ data: { ...chat, response: 'Third reply' } })
    render(<App initialTab="chat" />)
    send('first')
    await screen.findByText('Hi.')
    send('second')
    fireEvent.click(screen.getByRole('button', { name: '口语纠正' }))
    expect(post.mock.calls[1][2]?.signal?.aborted).toBe(false)
    await act(async () => second.resolve({ data: { ...chat, response: 'Second reply' } }))
    fireEvent.click(screen.getByRole('button', { name: '对话练习' }))
    expect(screen.getByText('Second reply')).toBeVisible()
    send('third')
    await screen.findByText('Third reply')
    expect(post.mock.calls[2][1]).toMatchObject({ session_id: 'current-session' })
    fireEvent.click(screen.getByRole('button', { name: '开始录音' }))
    await screen.findByRole('button', { name: '停止录音' })
    fireEvent.click(screen.getByRole('button', { name: '口语纠正' }))
    expect(streams[0].getTracks()[0].stop).toHaveBeenCalledOnce()
    expect(contexts[0].close).toHaveBeenCalledOnce()
  })

  it('unmount cancels pending chat and ignores late completion', async () => {
    const late = deferred<{ data: typeof chat }>()
    const post = vi.spyOn(axios, 'post').mockReturnValue(late.promise)
    const view = render(<ChatInterface />)
    send()
    view.unmount()
    expect(post.mock.calls[0][2]?.signal?.aborted).toBe(true)
    await act(async () => late.resolve({ data: chat }))
    expect(objectUrls.size).toBe(0)
  })

  it('failed voice request frees its URL and controls recover for another recording', async () => {
    vi.spyOn(axios, 'post').mockRejectedValueOnce(new Error('Offline')).mockResolvedValue({ data: chat })
    render(<ChatInterface />)
    await record()
    await screen.findByRole('alert')
    expect(objectUrls.size).toBe(0)
    expect(screen.getByRole('button', { name: '开始录音' })).toBeEnabled()
    await record()
    await screen.findByText('Hello.')
    expect(objectUrls.size).toBe(1)
  })

  it('ASR unavailable does not invent recognized text or silently show a success', async () => {
    vi.spyOn(axios, 'post').mockResolvedValue({ data: { ...chat, recognized_text: '' } })
    render(<ChatInterface />)
    await record()
    expect(await screen.findByRole('alert')).toHaveTextContent('语音识别或回复暂不可用')
    expect(screen.queryByText('Hi.')).not.toBeInTheDocument()
    expect(objectUrls.size).toBe(0)
  })

  it.each([
    ['blob:recording-remote', 'blob:recording-remote'],
    ['/api/audio/reply.wav', '/api/audio/reply.wav'],
    ['https://example.test/audio.mp3', 'https://example.test/audio.mp3'],
    ['data:audio/wav;base64,UklGRg==', 'data:audio/wav;base64,UklGRg=='],
    ['UklGRg==', 'data:audio/mpeg;base64,UklGRg=='],
  ])('plays %s with the appropriate URL or Base64 source', async (audio_url, expected) => {
    vi.spyOn(axios, 'post').mockResolvedValue({ data: { ...chat, audio_url } })
    const view = render(<ChatInterface />)
    send()
    await screen.findByText('Hi.')
    fireEvent.click(screen.getByRole('button', { name: '播放助手语音' }))
    expect(view.container.querySelector('audio')?.getAttribute('src')).toBe(expected)
    fireEvent.click(screen.getByRole('button', { name: '暂停助手语音' }))
    expect(view.container.querySelector('audio')).not.toHaveAttribute('src')
  })

  it('old play rejection cannot report an error or stop a newer playback', async () => {
    const oldPlay = deferred<void>()
    vi.spyOn(axios, 'post').mockResolvedValue({ data: chat })
    render(<ChatInterface />)
    await record()
    await screen.findByText('Hi.')
    vi.mocked(HTMLMediaElement.prototype.play).mockReturnValueOnce(oldPlay.promise)
    fireEvent.click(screen.getByRole('button', { name: '播放用户录音' }))
    fireEvent.click(screen.getByRole('button', { name: '播放助手语音' }))
    await act(async () => oldPlay.reject(new Error('Interrupted')))
    expect(screen.getByRole('button', { name: '暂停助手语音' })).toBeVisible()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('playback failure offers recovery, and unmount pauses the audio before revoking its URLs', async () => {
    vi.spyOn(axios, 'post').mockResolvedValue({ data: chat })
    const view = render(<ChatInterface />)
    await record()
    await screen.findByText('Hello.')
    vi.mocked(HTMLMediaElement.prototype.play).mockRejectedValueOnce(new Error('Unsupported audio'))
    fireEvent.click(screen.getByRole('button', { name: '播放用户录音' }))
    await screen.findByRole('alert')
    expect(screen.getByRole('button', { name: '播放用户录音' })).toBeEnabled()
    fireEvent.click(screen.getByRole('button', { name: '播放用户录音' }))
    await act(async () => {})
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    const audio = view.container.querySelector('audio')!
    const pause = vi.spyOn(audio, 'pause')
    view.unmount()
    expect(pause).toHaveBeenCalled()
    expect(objectUrls.size).toBe(0)
  })

  it('late successful play completion after clear cannot restore playback state', async () => {
    const playing = deferred<void>()
    vi.spyOn(axios, 'post').mockResolvedValue({ data: chat })
    const view = render(<ChatInterface />)
    send()
    await screen.findByText('Hi.')
    vi.mocked(HTMLMediaElement.prototype.play).mockReturnValueOnce(playing.promise)
    fireEvent.click(screen.getByRole('button', { name: '播放助手语音' }))
    fireEvent.click(screen.getByRole('button', { name: '清除对话' }))
    await act(async () => playing.resolve())
    expect(view.container.querySelector('audio')).not.toHaveAttribute('src')
    expect(screen.queryByRole('button', { name: '暂停助手语音' })).not.toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('unmount while play is pending ignores late rejection and removes playback handlers', async () => {
    const playing = deferred<void>()
    vi.spyOn(axios, 'post').mockResolvedValue({ data: chat })
    const view = render(<ChatInterface />)
    send()
    await screen.findByText('Hi.')
    vi.mocked(HTMLMediaElement.prototype.play).mockReturnValueOnce(playing.promise)
    fireEvent.click(screen.getByRole('button', { name: '播放助手语音' }))
    const audio = view.container.querySelector('audio')!
    view.unmount()
    await act(async () => playing.reject(new Error('Detached')))
    expect(audio.onended).toBeNull()
    expect(audio.onerror).toBeNull()
    expect(audio).not.toHaveAttribute('src')
  })
})
