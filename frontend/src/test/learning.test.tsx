import { StrictMode } from 'react'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import LearningWorkspace from '../learning/LearningWorkspace'
import { LearningApiError, resetLearningIdentity } from '../learning/client'
import { useLearningFlow } from '../learning/useLearningFlow'
import { renderHook } from '@testing-library/react'
import { contexts, deferred, emitAudio, streams, tone } from './setup'
import { learningBootstrap, learningFeedback, learningSession, mockLearningApi, operation, transcriptionOperation } from './learning-fixtures'
import type { LearningOperation, LearningSession } from '../learning/types'

beforeEach(() => {
  const items = new Map<string, string>()
  Object.defineProperty(window, 'localStorage', { configurable: true, value: {
    getItem: (key: string) => items.get(key) ?? null,
    setItem: (key: string, value: string) => { items.set(key, value) },
    removeItem: (key: string) => { items.delete(key) },
  } })
  resetLearningIdentity()
})
async function start(api = mockLearningApi()) {
  const view = render(<LearningWorkspace active api={api} />)
  fireEvent.click(await screen.findByRole('button', { name: '开始描述周末' }))
  await screen.findByRole('textbox', { name: '我的任务回答' })
  return { api, view }
}
function completedSession(): LearningSession {
  const session = learningSession()
  session.status = 'completed'; session.revision = 2; session.feedback = structuredClone(learningFeedback)
  session.turns.push({ turn_id: 'turn-1', role: 'user', text: 'I go hiking yesterday.', audio: { state: 'available', audio_asset_id: 'original-audio' } })
  return session
}

// These flow tests use a candidate HTTP port, not a replacement persistence layer.
// Actual A2/INT2 integration has a separate browser acceptance boundary.
describe('learning workflow', () => {
  it('does not read personal data before bootstrap completes, including StrictMode', async () => {
    const api = mockLearningApi()
    const handshake = deferred<typeof learningBootstrap>()
    vi.mocked(api.bootstrap).mockReturnValue(handshake.promise)
    render(<StrictMode><LearningWorkspace active api={api} /></StrictMode>)
    expect(api.history).not.toHaveBeenCalled()
    expect(api.reviews).not.toHaveBeenCalled()
    await act(async () => handshake.resolve(structuredClone(learningBootstrap)))
    await screen.findByRole('button', { name: '开始描述周末' })
    expect(api.history).toHaveBeenCalledTimes(1)
  })

  it('completes a task, opens focused practice without reference input and retains both retries', async () => {
    const api = mockLearningApi()
    let saved = learningSession()
    vi.mocked(api.session).mockImplementation(async () => structuredClone(saved))
    vi.mocked(api.mutate).mockImplementation(async (path, payload, requestId) => {
      if (path.endsWith('/turns')) { saved = completedSession(); saved.status = 'active'; saved.feedback = null }
      else if (path.endsWith('/finish')) saved = completedSession()
      else if (path === '/attempts') {
        const fields = payload as Record<string, unknown>
        expect(fields.target_text).toBeNull()
        expect(fields.parent_attempt_id).toBe(saved.attempts.length ? 'attempt-1' : null)
        saved.attempts.push({ attempt_id: `attempt-${saved.attempts.length + 1}`, session_id: saved.session_id, source_turn_id: 'turn-1', focus_id: 'past-tense', parent_attempt_id: saved.attempts.length ? 'attempt-1' : null,
          mode: 'free_response', transcript: String(fields.text), feedback: null, created_at: '2026-10-23T08:00:00Z',
          evaluation: { status: 'available', result: 'met', source: 'text', reason: '使用了过去时。' }, audio: { state: 'not_saved' } })
      }
      return { ...operation(structuredClone(saved)), request_id: requestId }
    })
    await start(api)
    fireEvent.change(screen.getByRole('textbox', { name: '我的任务回答' }), { target: { value: 'I go hiking yesterday.' } })
    fireEvent.click(screen.getByRole('button', { name: '发送任务回答' }))
    await screen.findByText('I go hiking yesterday.', { selector: '.learning-turn p' })
    fireEvent.click(screen.getByRole('button', { name: '结束对话，查看反馈' }))
    fireEvent.click(await screen.findByRole('button', { name: '针对这一句重练' }))
    expect(screen.queryByLabelText('参考文本')).not.toBeInTheDocument()
    fireEvent.change(screen.getByRole('textbox', { name: '我的新表达' }), { target: { value: 'I went hiking yesterday.' } })
    fireEvent.click(screen.getByRole('button', { name: '提交这次练习' }))
    await screen.findByText('第一次练习')
    fireEvent.change(screen.getByRole('textbox', { name: '我的新表达' }), { target: { value: 'Yesterday I went hiking with friends.' } })
    fireEvent.click(screen.getByRole('button', { name: '保存这次重练' }))
    expect(await screen.findByText('第 2 次练习')).toBeVisible()
    expect(screen.getByText('第一次练习')).toBeVisible()
    expect(screen.getByText('Yesterday I went hiking with friends.')).toBeVisible()
  })

  it.each(['network', 'invalid_response'])('uses the original request ID after uncertain %s and guards duplicate sends', async code => {
    const api = mockLearningApi()
    const unknown = deferred<LearningOperation>()
    vi.mocked(api.requestStatus).mockRejectedValue(new LearningApiError('Not found', '404'))
    vi.mocked(api.mutate).mockReturnValueOnce(unknown.promise).mockResolvedValue(operation(learningSession()))
    render(<LearningWorkspace active api={api} />)
    const button = await screen.findByRole('button', { name: '开始描述周末' })
    fireEvent.click(button); fireEvent.click(button)
    expect(api.mutate).toHaveBeenCalledOnce()
    await act(async () => unknown.reject(new LearningApiError('提交结果尚未确认', code, true)))
    fireEvent.click(screen.getByRole('button', { name: '查询或重试原操作' }))
    await screen.findByRole('textbox', { name: '我的任务回答' })
    expect(vi.mocked(api.mutate).mock.calls[1][2]).toBe(vi.mocked(api.mutate).mock.calls[0][2])
  })

  it('restores a server operation on reload without creating a second session', async () => {
    const api = mockLearningApi()
    vi.mocked(api.bootstrap).mockResolvedValue({ ...learningBootstrap, pending_operations: [{ ...operation(undefined, 'pending'), session_id: 'session-1' }] })
    vi.mocked(api.operation).mockResolvedValue(operation(learningSession()))
    render(<LearningWorkspace active api={api} />)
    await waitFor(() => expect(api.operation).toHaveBeenCalledWith('operation-1', expect.any(AbortSignal)))
    expect(api.mutate).not.toHaveBeenCalled()
    fireEvent.click(await screen.findByRole('button', { name: '返回本次练习' }))
    expect(await screen.findByRole('textbox', { name: '我的任务回答' })).toBeVisible()
  })

  it('stops microphone and playback when the learning page becomes inactive', async () => {
    const { api, view } = await start()
    fireEvent.click(screen.getByRole('button', { name: '播放教练回复' }))
    const audio = view.container.querySelector('audio')!
    fireEvent.click(screen.getByRole('button', { name: '开始录音' }))
    await screen.findByRole('button', { name: '停止录音' })
    view.rerender(<LearningWorkspace active={false} api={api} />)
    expect(streams[0].getTracks()[0].stop).toHaveBeenCalledOnce()
    expect(contexts[0].close).toHaveBeenCalledOnce()
    expect(audio).not.toHaveAttribute('src')
  })

  it('uses actual WAV recording and requires an explicit send after preview', async () => {
    const { api } = await start()
    vi.mocked(api.mutate).mockClear()
    fireEvent.click(screen.getByRole('button', { name: '开始录音' }))
    await screen.findByRole('button', { name: '停止录音' })
    act(() => emitAudio([tone()]))
    fireEvent.click(screen.getByRole('button', { name: '停止录音' }))
    expect(await screen.findByRole('button', { name: '试听本次录音' })).toBeVisible()
    expect(api.mutate).not.toHaveBeenCalled()
    vi.mocked(api.mutate).mockImplementationOnce(async (_path, _payload, requestId) => transcriptionOperation(undefined, requestId))
    fireEvent.click(screen.getByRole('button', { name: '识别并核对录音' }))
    const confirmation = await screen.findByRole('textbox', { name: '核对录音文字' })
    expect(confirmation).toHaveValue('I go hiking yesterday.')
    expect(api.session).not.toHaveBeenCalled()
    const form = vi.mocked(api.mutate).mock.calls[0][1] as FormData
    expect((form.get('audio') as File).type).toBe('audio/wav')
    expect(JSON.parse(String(form.get('context')))).toEqual({ kind: 'turn', session_id: 'session-1', expected_revision: 1 })
    fireEvent.change(confirmation, { target: { value: 'I went hiking yesterday.' } })
    fireEvent.click(screen.getByRole('button', { name: '确认文字并提交' }))
    await waitFor(() => expect(api.mutate).toHaveBeenCalledTimes(2))
    expect(vi.mocked(api.mutate).mock.calls[1][1]).toEqual({ expected_revision: 1, transcription_id: 'transcription-1', confirmed_text: 'I went hiking yesterday.' })
  })

  it('starts another task without inheriting the previous session when an async response omits its session ID', async () => {
    const api = mockLearningApi()
    const second = learningSession('second-session')
    const provider = deferred<LearningOperation>()
    await start(api)
    vi.mocked(api.mutate).mockResolvedValueOnce({ operation_id: 'second-operation', request_id: 'second-request', status: 'pending' })
    vi.mocked(api.operation).mockReturnValueOnce(provider.promise)
    fireEvent.click(screen.getByRole('button', { name: '今日任务' }))
    fireEvent.click(await screen.findByRole('button', { name: '开始描述周末' }))
    await screen.findByText(/正在处理这次练习/)
    await act(async () => provider.resolve({ operation_id: 'second-operation', request_id: 'second-request', status: 'succeeded', result_type: 'session', result: second }))
    await screen.findByRole('textbox', { name: '我的任务回答' })
    vi.mocked(api.mutate).mockClear()
    fireEvent.change(screen.getByRole('textbox', { name: '我的任务回答' }), { target: { value: 'A new weekend.' } })
    fireEvent.click(screen.getByRole('button', { name: '发送任务回答' }))
    await waitFor(() => expect(api.mutate).toHaveBeenCalledOnce())
    expect(vi.mocked(api.mutate).mock.calls[0][0]).toBe('/sessions/second-session/turns')
  })

  it('keeps example audio attached to its practice attempt when selecting another sentence', async () => {
    const api = mockLearningApi()
    const session = completedSession()
    session.turns.push({ turn_id: 'turn-2', role: 'user', text: 'It was fun.' })
    session.attempts.push({ attempt_id: 'attempt-1', session_id: session.session_id, source_turn_id: 'turn-1', focus_id: 'past-tense', parent_attempt_id: null,
      mode: 'free_response', transcript: 'I went hiking yesterday.', feedback: null, created_at: session.created_at, evaluation: { status: 'available', result: 'met', source: 'text', reason: '使用了过去时。' } })
    vi.mocked(api.mutate).mockImplementation(async (path, _payload, requestId) => path.includes('example-audio')
      ? { ...operation(), request_id: requestId, session_id: session.session_id, result_type: 'audio', result: { state: 'available', url: '/example-a.wav' } }
      : { ...operation(session), request_id: requestId })
    vi.mocked(api.session).mockResolvedValue(session)
    render(<LearningWorkspace active api={api} />)
    fireEvent.click(await screen.findByRole('button', { name: '开始描述周末' }))
    fireEvent.click(await screen.findByRole('button', { name: '针对这一句重练' }))
    fireEvent.click(screen.getByRole('button', { name: '获取目标范读' }))
    expect(await screen.findByRole('button', { name: '播放目标范读' })).toBeVisible()
    fireEvent.click(screen.getAllByRole('button', { name: '练这一句' })[1])
    expect(screen.queryByRole('button', { name: '播放目标范读' })).not.toBeInTheDocument()
  })

  it('applies the selected speed after loading each audio source', async () => {
    // Chrome load() restores defaultPlaybackRate; reproduce that browser behavior.
    vi.spyOn(HTMLMediaElement.prototype, 'load').mockImplementation(function (this: HTMLMediaElement) { this.playbackRate = this.defaultPlaybackRate })
    const { view } = await start()
    const audio = view.container.querySelector('audio')!
    fireEvent.change(screen.getByRole('combobox', { name: '学习音频播放速度' }), { target: { value: '0.75' } })
    fireEvent.click(screen.getByRole('button', { name: '播放教练回复' }))
    expect(audio.playbackRate).toBe(0.75)
    fireEvent.click(screen.getByRole('button', { name: '停止学习音频' }))
    fireEvent.click(screen.getByRole('button', { name: '播放教练回复' }))
    expect(audio.playbackRate).toBe(0.75)
  })

  it('keeps review answers hidden until the server records reveal', async () => {
    const api = mockLearningApi()
    const review = { review_item_id: 'review-1', session_id: 'session-1', focus_id: 'past-tense', prompt: 'Tell me about your last trip.', question_id: 'trip', prompt_available: true, due_at: '2026-10-24T08:00:00Z', revision: 1, cycle: 0, used_hint: false, revealed_answer: false, independent_variant_available: true, availability_reason: '' }
    vi.mocked(api.reviews).mockResolvedValue([review])
    vi.mocked(api.mutate).mockImplementation(async (_path, _payload, requestId) => ({ ...operation(), request_id: requestId,
      result_type: 'review_reveal', result: { ...review, revision: 2, used_hint: true, revealed_answer: true, answer: 'I went to the mountains.' } }))
    render(<LearningWorkspace active api={api} />)
    await screen.findByRole('button', { name: '开始描述周末' })
    fireEvent.click(screen.getByRole('button', { name: '到期复习（1）' }))
    fireEvent.click(screen.getByRole('button', { name: '开始这项复习' }))
    expect(screen.queryByText('I went to the mountains.')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '显示参考表达并记录提示' }))
    expect(await screen.findByText('I went to the mountains.')).toBeVisible()
    expect(vi.mocked(api.mutate).mock.calls[0][0]).toBe('/reviews/review-1/reveal')
    expect(vi.mocked(api.mutate).mock.calls[0][1]).not.toHaveProperty('used_hint')
  })

  it('opens persisted history and shows unavailable evaluation without invented scores', async () => {
    const api = mockLearningApi()
    vi.mocked(api.history).mockResolvedValue({ items: [{ session_id: 'saved', task_id: 'weekend', title: '已保存的周末练习', status: 'completed', updated_at: '2026-10-23T08:00:00Z' }], next_cursor: null })
    const session = completedSession()
    session.session_id = 'saved'; session.feedback = { ...learningFeedback, status: 'unavailable', reason: '评估提供商不可用。', focuses: [], criteria: [] }
    vi.mocked(api.session).mockResolvedValue(session)
    render(<LearningWorkspace active api={api} />)
    fireEvent.click(await screen.findByRole('button', { name: '学习记录' }))
    fireEvent.click(await screen.findByRole('button', { name: '打开练习记录' }))
    expect(await screen.findByText('评估提供商不可用。')).toBeVisible()
    expect(screen.queryByText('75')).not.toBeInTheDocument()
  })
})

describe('learning request ownership', () => {
  it('ignores a late history detail response after opening another session', async () => {
    const api = mockLearningApi()
    const first = deferred<LearningSession>()
    vi.mocked(api.session).mockImplementation(id => id === 'first' ? first.promise : Promise.resolve(learningSession('second')))
    const { result } = renderHook(() => useLearningFlow(api))
    await waitFor(() => expect(result.current.identity).not.toBeNull())
    act(() => { void result.current.openSession('first') })
    await act(async () => result.current.openSession('second'))
    await act(async () => first.resolve(learningSession('first')))
    expect(result.current.session?.session_id).toBe('second')
  })

  it('does not replace a completed mutation with an older history refresh', async () => {
    const api = mockLearningApi()
    const refresh = deferred<LearningSession>()
    const provider = deferred<LearningOperation>()
    vi.mocked(api.session).mockResolvedValueOnce(learningSession()).mockReturnValueOnce(refresh.promise)
    vi.mocked(api.mutate).mockReturnValueOnce(provider.promise)
    const { result } = renderHook(() => useLearningFlow(api))
    await waitFor(() => expect(result.current.identity).not.toBeNull())
    await act(async () => result.current.openSession('session-1'))
    act(() => { void result.current.mutate('turn', '/sessions/session-1/turns', { expected_revision: 1, text: 'Yesterday I went hiking.' }, 'session-1') })
    act(() => { void result.current.openSession('session-1') })
    const updated = completedSession()
    await act(async () => provider.resolve(operation(updated)))
    await act(async () => refresh.resolve(learningSession()))
    expect(result.current.session?.revision).toBe(2)
    expect(result.current.session?.turns.some(turn => turn.turn_id === 'turn-1')).toBe(true)
  })

  it('keeps an in-memory pending request when browser storage is blocked during reconnect', async () => {
    Object.defineProperty(window, 'localStorage', { configurable: true, get: () => { throw new DOMException('Storage blocked', 'SecurityError') } })
    const api = mockLearningApi()
    vi.mocked(api.mutate).mockRejectedValueOnce(new LearningApiError('响应丢失', 'network', true))
    const { result } = renderHook(() => useLearningFlow(api))
    await waitFor(() => expect(result.current.identity).not.toBeNull())
    await act(async () => result.current.mutate('start', '/sessions', { task_id: 'weekend' }))
    const requestId = result.current.pending!.request_id
    await act(async () => result.current.connect())
    expect(result.current.pending?.request_id).toBe(requestId)
    await act(async () => result.current.mutate('start', '/sessions', { task_id: 'weekend' }))
    expect(api.mutate).toHaveBeenCalledOnce()
  })

  it('clears previous learner state when reconnect creates a replacement identity', async () => {
    const api = mockLearningApi()
    const { result } = renderHook(() => useLearningFlow(api))
    await waitFor(() => expect(result.current.identity).not.toBeNull())
    await act(async () => result.current.openSession('saved-session'))
    expect(result.current.session?.session_id).toBe('saved-session')
    vi.mocked(api.bootstrap).mockResolvedValueOnce({ ...learningBootstrap, learner_id: 'replacement-learner' })
    await act(async () => result.current.connect())
    expect(result.current.identity?.learner_id).toBe('replacement-learner')
    expect(result.current.session).toBeNull()
    expect(result.current.pending).toBeNull()
    expect(result.current.lastOperation).toBeNull()
  })

  it('recovers a lost POST response by request lookup without repeating the write', async () => {
    const api = mockLearningApi()
    vi.mocked(api.mutate).mockRejectedValueOnce(new LearningApiError('响应丢失', 'network', true))
    const { result } = renderHook(() => useLearningFlow(api))
    await waitFor(() => expect(result.current.identity).not.toBeNull())
    await act(async () => result.current.mutate('start', '/sessions', { task_id: 'weekend' }))
    const requestId = result.current.pending!.request_id
    vi.mocked(api.requestStatus).mockResolvedValueOnce({ ...operation(learningSession()), request_id: requestId })
    await act(async () => result.current.retryPending())
    expect(result.current.session?.session_id).toBe('session-1')
    expect(result.current.pending).toBeNull()
    expect(api.mutate).toHaveBeenCalledOnce()
  })

  it('does not revive results when data is deleted during a pending provider request', async () => {
    const api = mockLearningApi()
    const provider = deferred<LearningOperation>()
    vi.mocked(api.mutate).mockReturnValue(provider.promise)
    const { result } = renderHook(() => useLearningFlow(api))
    await waitFor(() => expect(result.current.identity).not.toBeNull())
    act(() => { void result.current.mutate('start', '/sessions', { task_id: 'weekend' }) })
    await act(async () => result.current.deleteData())
    await act(async () => provider.resolve(operation(learningSession())))
    expect(result.current.session).toBeNull()
    expect(result.current.identity).toBeNull()
    expect(result.current.pending).toBeNull()
  })

  it('shows explicit confirmation before deleting records', async () => {
    const api = mockLearningApi()
    render(<LearningWorkspace active api={api} />)
    await screen.findByRole('button', { name: '开始描述周末' })
    fireEvent.click(screen.getByRole('button', { name: '学习设置' }))
    fireEvent.click(screen.getByRole('button', { name: '删除我的学习数据' }))
    expect(api.deleteData).not.toHaveBeenCalled()
    const confirm = screen.getByRole('group', { name: '确认删除学习数据' })
    fireEvent.click(within(confirm).getByRole('button', { name: '确认删除全部学习数据' }))
    await waitFor(() => expect(api.deleteData).toHaveBeenCalledOnce())
    expect(screen.queryByText('描述周末', { selector: 'h4' })).not.toBeInTheDocument()
  })
})
