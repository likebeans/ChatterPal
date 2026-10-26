import { act, fireEvent, render, renderHook, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import LearningWorkspace from '../learning/LearningWorkspace'
import { LearningApiError } from '../learning/client'
import { useLearningFlow } from '../learning/useLearningFlow'
import type { LearningOperation, LearningSession } from '../learning/types'
import { learningBootstrap, learningFeedback, learningSession, mockLearningApi, operation, transcriptionOperation } from './learning-fixtures'
import { deferred, emitAudio, objectUrls, tone } from './setup'

beforeEach(() => {
  const items = new Map<string, string>()
  Object.defineProperty(window, 'localStorage', { configurable: true, value: {
    getItem: (key: string) => items.get(key) ?? null,
    setItem: (key: string, value: string) => { items.set(key, value) },
    removeItem: (key: string) => { items.delete(key) },
  } })
})
afterEach(() => { vi.useRealTimers() })

function finished(): LearningSession {
  const session = learningSession()
  session.status = 'completed'; session.revision = 3
  session.turns.push({ turn_id: 'turn-1', role: 'user', text: 'I go hiking yesterday.', audio: null })
  session.feedback = structuredClone(learningFeedback)
  session.attempts.push({ attempt_id: 'original-1', session_id: session.session_id, source_turn_id: 'turn-1', focus_id: 'past-tense', parent_attempt_id: null,
    mode: 'free_response', transcript: 'I go hiking yesterday.', feedback: null, audio: null, created_at: session.created_at,
    evaluation: { status: 'unavailable', result: 'unverified', source: 'text', reason: '保存了对话原句，尚未评价重练。' } })
  return session
}
async function open(api = mockLearningApi()) {
  const view = render(<LearningWorkspace active api={api} />)
  fireEvent.click(await screen.findByRole('button', { name: '开始描述周末' }))
  await screen.findByRole('region', { name: '当前任务练习' })
  return { api, view }
}
async function recordDraft() {
  fireEvent.click(screen.getByRole('button', { name: '开始录音' }))
  await screen.findByRole('button', { name: '停止录音' })
  act(() => emitAudio([tone()]))
  fireEvent.click(screen.getByRole('button', { name: '停止录音' }))
  await screen.findByRole('button', { name: '试听本次录音' })
}

describe('drafts and authoritative revisions', () => {
  it.each(['hint', 'record', 'result'] as const)('refreshes a %s hint result without clearing text, including internal history navigation', async resultType => {
    const { api } = await open()
    const updated = learningSession(); updated.revision += 1
    vi.mocked(api.session).mockResolvedValue(updated)
    vi.mocked(api.mutate).mockImplementationOnce(async (_path, _payload, requestId) => ({ ...operation(), request_id: requestId, session_id: updated.session_id, result_type: resultType, result: { hint: 'Last weekend, I…' } }))
    fireEvent.change(screen.getByRole('textbox', { name: '我的任务回答' }), { target: { value: 'My unfinished weekend answer' } })
    fireEvent.click(screen.getByRole('button', { name: '给我一个开头' }))
    await screen.findByText('Last weekend, I…')
    expect(api.session).toHaveBeenCalledWith('session-1')
    expect(screen.getByRole('textbox', { name: '我的任务回答' })).toHaveValue('My unfinished weekend answer')
    fireEvent.click(screen.getByRole('button', { name: '学习记录' }))
    fireEvent.click(screen.getByRole('button', { name: '返回本次练习' }))
    expect(screen.getByRole('textbox', { name: '我的任务回答' })).toHaveValue('My unfinished weekend answer')
    vi.mocked(api.mutate).mockImplementationOnce(async (_path, payload, requestId) => {
      expect(payload).toMatchObject({ expected_revision: updated.revision, text: 'My unfinished weekend answer' })
      return { ...operation({ ...updated, revision: updated.revision + 1 }), request_id: requestId }
    })
    fireEvent.click(screen.getByRole('button', { name: '发送任务回答' }))
    await waitFor(() => expect(screen.getByRole('textbox', { name: '我的任务回答' })).toHaveValue(''))
  })

  it('retains a prepared recording through hints, history and a top-level tab switch until its own turn succeeds', async () => {
    const { api, view } = await open()
    await recordDraft()
    const blob = [...objectUrls.values()][0]
    const updated = learningSession(); updated.revision += 1
    vi.mocked(api.session).mockResolvedValue(updated)
    vi.mocked(api.mutate).mockImplementationOnce(async (_path, _payload, requestId) => ({ ...operation(), request_id: requestId, session_id: 'session-1', result_type: 'hint', result: { hint: 'You can start with last weekend.' } }))
    fireEvent.click(screen.getByRole('button', { name: '给我一个开头' }))
    await screen.findByText('You can start with last weekend.')
    expect(screen.getByRole('button', { name: '试听本次录音' })).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: '学习记录' }))
    fireEvent.click(screen.getByRole('button', { name: '返回本次练习' }))
    view.rerender(<LearningWorkspace active={false} api={api} />)
    view.rerender(<LearningWorkspace active api={api} />)
    fireEvent.click(screen.getByRole('button', { name: '试听本次录音' }))
    expect([...objectUrls.values()]).toContain(blob)
    expect(window.localStorage.getItem('chatterpal.learning.pending.v1')).toBeNull()
    // Hint revisions never silently rebind an earlier recording.
    expect(screen.getByRole('button', { name: '识别并核对录音' })).toBeDisabled()
    expect(screen.getByText(/这段录音对应的练习内容已有更新/)).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: '放弃本次录音' }))
    await waitFor(() => expect(screen.queryByRole('button', { name: '试听本次录音' })).not.toBeInTheDocument())
    await recordDraft()
    vi.mocked(api.mutate).mockImplementationOnce(async (_path, payload, requestId) => {
      expect(JSON.parse(String((payload as FormData).get('context'))).expected_revision).toBe(updated.revision)
      return transcriptionOperation({ kind: 'turn', session_id: updated.session_id, expected_revision: updated.revision }, requestId)
    })
    fireEvent.click(screen.getByRole('button', { name: '识别并核对录音' }))
    await screen.findByRole('textbox', { name: '核对录音文字' })
    vi.mocked(api.mutate).mockImplementationOnce(async (_path, payload, requestId) => {
      expect(payload).toMatchObject({ expected_revision: updated.revision, transcription_id: 'transcription-1' })
      return { ...operation({ ...updated, revision: updated.revision + 1 }), request_id: requestId }
    })
    fireEvent.click(screen.getByRole('button', { name: '确认文字并提交' }))
    await waitFor(() => expect(screen.queryByRole('button', { name: '试听本次录音' })).not.toBeInTheDocument())
  })

  it('keeps drafts separate between task answers, practice modes, sessions and history', async () => {
    const api = mockLearningApi()
    const saved = finished(); saved.status = 'active'
    vi.mocked(api.mutate).mockImplementation(async (_path, _payload, requestId) => ({ ...operation(saved), request_id: requestId }))
    vi.mocked(api.history).mockResolvedValue({ items: [
      { session_id: 'second', task_id: 'weekend', title: '另一次周末', status: 'active', updated_at: saved.updated_at },
      { session_id: saved.session_id, task_id: saved.task_id, title: saved.task.title, status: 'active', updated_at: saved.updated_at },
    ], next_cursor: null })
    vi.mocked(api.session).mockImplementation(async id => id === saved.session_id ? structuredClone(saved) : learningSession(id))
    await open(api)
    fireEvent.change(screen.getByRole('textbox', { name: '我的任务回答' }), { target: { value: 'Task draft' } })
    fireEvent.click(screen.getByRole('button', { name: '针对这一句重练' }))
    fireEvent.change(screen.getByRole('textbox', { name: '我的新表达' }), { target: { value: 'Free response draft' } })
    fireEvent.click(screen.getByRole('button', { name: '跟读练习' }))
    fireEvent.change(screen.getByRole('textbox', { name: '我的跟读' }), { target: { value: 'Reading draft' } })
    fireEvent.click(screen.getByRole('button', { name: '学习记录' }))
    fireEvent.click(screen.getAllByRole('button', { name: '打开练习记录' })[0])
    expect(await screen.findByRole('textbox', { name: '我的任务回答' })).toHaveValue('')
    fireEvent.change(screen.getByRole('textbox', { name: '我的任务回答' }), { target: { value: 'Second task draft' } })
    fireEvent.click(screen.getByRole('button', { name: '学习记录' }))
    fireEvent.click(screen.getAllByRole('button', { name: '打开练习记录' })[1])
    expect(await screen.findByRole('textbox', { name: '我的跟读' })).toHaveValue('Reading draft')
    fireEvent.click(screen.getByRole('button', { name: '自由表达' }))
    expect(screen.getByRole('textbox', { name: '我的新表达' })).toHaveValue('Free response draft')
    fireEvent.click(screen.getByRole('button', { name: '返回任务对话' }))
    expect(screen.getByRole('textbox', { name: '我的任务回答' })).toHaveValue('Task draft')
  })

  it('refreshes the current session after 409 and preserves the draft for submission at the new revision', async () => {
    const { api } = await open()
    fireEvent.change(screen.getByRole('textbox', { name: '我的任务回答' }), { target: { value: 'My weekend draft' } })
    vi.mocked(api.mutate).mockRejectedValueOnce(new LearningApiError('Revision changed', '409'))
    fireEvent.click(screen.getByRole('button', { name: '发送任务回答' }))
    const refresh = await screen.findByRole('button', { name: /刷新.*继续/ })
    expect(screen.getByRole('textbox', { name: '我的任务回答' })).toHaveValue('My weekend draft')
    const updated = learningSession(); updated.revision = 7
    vi.mocked(api.session).mockResolvedValueOnce(updated)
    fireEvent.click(refresh)
    await waitFor(() => expect(api.session).toHaveBeenCalledWith('session-1'))
    await waitFor(() => expect(screen.getByRole('button', { name: '发送任务回答' })).toBeEnabled())
    vi.mocked(api.mutate).mockImplementationOnce(async (_path, payload, requestId) => {
      expect(payload).toMatchObject({ expected_revision: 7, text: 'My weekend draft' })
      return { ...operation({ ...updated, revision: 8 }), request_id: requestId }
    })
    fireEvent.click(screen.getByRole('button', { name: '发送任务回答' }))
    await waitFor(() => expect(screen.getByRole('textbox', { name: '我的任务回答' })).toHaveValue(''))
    expect(api.bootstrap).toHaveBeenCalledOnce()
  })

  it('clears a successful practice draft without clearing a separate task answer', async () => {
    const api = mockLearningApi()
    const saved = finished(); saved.status = 'active'
    vi.mocked(api.mutate).mockImplementation(async (_path, _payload, requestId) => ({ ...operation(saved), request_id: requestId }))
    await open(api)
    fireEvent.change(screen.getByRole('textbox', { name: '我的任务回答' }), { target: { value: 'Separate task draft' } })
    fireEvent.click(screen.getByRole('button', { name: '针对这一句重练' }))
    fireEvent.change(screen.getByRole('textbox', { name: '我的新表达' }), { target: { value: 'I went hiking yesterday.' } })
    fireEvent.click(screen.getByRole('button', { name: '保存这次重练' }))
    await waitFor(() => expect(screen.getByRole('textbox', { name: '我的新表达' })).toHaveValue(''))
    fireEvent.click(screen.getByRole('button', { name: '返回任务对话' }))
    expect(screen.getByRole('textbox', { name: '我的任务回答' })).toHaveValue('Separate task draft')
  })
})

describe('identity and terminal operation recovery', () => {
  it('stops polling on 401 and reconnects directly, removing the old identity pending submission', async () => {
    const api = mockLearningApi()
    const next = deferred<LearningOperation>()
    vi.mocked(api.bootstrap).mockResolvedValueOnce({ ...learningBootstrap, pending_operations: [{ ...operation(undefined, 'pending'), session_id: 'session-1' }] })
      .mockResolvedValue({ ...learningBootstrap, learner_id: 'new-owner' })
    vi.mocked(api.operation).mockReturnValueOnce(next.promise)
    render(<LearningWorkspace active api={api} />)
    await waitFor(() => expect(api.operation).toHaveBeenCalledOnce())
    await act(async () => next.reject(new LearningApiError('Identity expired', '401')))
    const reconnect = screen.getByRole('button', { name: '重新连接学习服务' })
    vi.useFakeTimers()
    await act(async () => vi.advanceTimersByTime(6000))
    expect(api.operation).toHaveBeenCalledOnce()
    vi.useRealTimers()
    fireEvent.click(reconnect)
    await waitFor(() => expect(api.bootstrap).toHaveBeenCalledTimes(2))
    await waitFor(() => expect(screen.queryByRole('button', { name: '查询原操作' })).not.toBeInTheDocument())
    expect(window.localStorage.getItem('chatterpal.learning.pending.v1')).toBeNull()
    expect(api.mutate).not.toHaveBeenCalled()
  })

  it.each(['404', '410'])('stops polling terminal %s responses instead of retrying forever', async code => {
    const api = mockLearningApi()
    vi.mocked(api.bootstrap).mockResolvedValue({ ...learningBootstrap, pending_operations: [{ ...operation(undefined, 'pending'), session_id: 'session-1' }] })
    vi.mocked(api.operation).mockRejectedValue(new LearningApiError('No longer available', code))
    const { result } = renderHook(() => useLearningFlow(api))
    await waitFor(() => expect(result.current.error).toMatch(/停止自动查询/))
    expect(result.current.pending).toBeNull()
    vi.useFakeTimers()
    await act(async () => vi.advanceTimersByTime(6000))
    expect(api.operation).toHaveBeenCalledOnce()
  })

  it('keeps the same owner recording through an expired-cookie handshake but clears it when the owner changes', async () => {
    const { api } = await open()
    await recordDraft()
    vi.mocked(api.mutate).mockRejectedValueOnce(new LearningApiError('Identity expired', '401'))
    fireEvent.click(screen.getByRole('button', { name: '识别并核对录音' }))
    fireEvent.click(await screen.findByRole('button', { name: '重新连接学习服务' }))
    await waitFor(() => expect(api.bootstrap).toHaveBeenCalledTimes(2))
    expect(screen.getByRole('button', { name: '试听本次录音' })).toBeVisible()
    vi.mocked(api.bootstrap).mockResolvedValueOnce({ ...learningBootstrap, learner_id: 'replacement-owner' })
    fireEvent.click(screen.getByRole('button', { name: '刷新本次记录' }))
    // The server can also report an expired identity while reading history.
    vi.mocked(api.session).mockRejectedValueOnce(new LearningApiError('Identity expired', '401'))
    fireEvent.click(screen.getByRole('button', { name: '刷新本次记录' }))
    fireEvent.click(await screen.findByRole('button', { name: '重新连接学习服务' }))
    await screen.findByRole('button', { name: '开始描述周末' })
    expect(objectUrls.size).toBe(0)
    fireEvent.click(screen.getByRole('button', { name: '开始描述周末' }))
    await screen.findByRole('textbox', { name: '我的任务回答' })
    expect(screen.queryByRole('button', { name: '试听本次录音' })).not.toBeInTheDocument()
  })

  it('clears drafts after confirmed full deletion before reconnecting with the same test owner', async () => {
    const { api } = await open()
    await recordDraft()
    fireEvent.click(screen.getByRole('button', { name: '学习设置' }))
    fireEvent.click(screen.getByRole('button', { name: '删除我的学习数据' }))
    fireEvent.click(within(screen.getByRole('group', { name: '确认删除学习数据' })).getByRole('button', { name: '确认删除全部学习数据' }))
    fireEvent.click(await screen.findByRole('button', { name: '连接学习服务' }))
    fireEvent.click(await screen.findByRole('button', { name: '开始描述周末' }))
    await screen.findByRole('textbox', { name: '我的任务回答' })
    expect(screen.queryByRole('button', { name: '试听本次录音' })).not.toBeInTheDocument()
    expect(api.deleteData).toHaveBeenCalledOnce()
  })
})

describe('actual first attempt and unavailable services', () => {
  it('shows the original attempt and uses a separate parent chain for read aloud', async () => {
    const api = mockLearningApi()
    const saved = finished()
    vi.mocked(api.mutate).mockImplementation(async (_path, _payload, requestId) => ({ ...operation(saved), request_id: requestId }))
    await open(api)
    fireEvent.click(screen.getByRole('button', { name: '针对这一句重练' }))
    const practice = screen.getByRole('region', { name: '单句重练' })
    expect(within(practice).getByText(/首次.*原句|原句.*首次/)).toBeVisible()
    expect(within(practice).getAllByText('I go hiking yesterday.').length).toBeGreaterThan(0)
    fireEvent.click(screen.getByRole('button', { name: '跟读练习' }))
    fireEvent.change(screen.getByRole('textbox', { name: '我的跟读' }), { target: { value: 'I went hiking yesterday.' } })
    fireEvent.click(screen.getByRole('button', { name: '提交这次练习' }))
    await waitFor(() => expect(api.mutate).toHaveBeenCalledTimes(2))
    expect(vi.mocked(api.mutate).mock.calls[1][1]).toMatchObject({ mode: 'read_aloud', focus_id: 'past-tense', parent_attempt_id: null, target_text: 'I went hiking yesterday.' })
    await waitFor(() => expect(screen.getByRole('button', { name: '自由表达' })).toBeEnabled())
    fireEvent.click(screen.getByRole('button', { name: '自由表达' }))
    fireEvent.change(screen.getByRole('textbox', { name: '我的新表达' }), { target: { value: 'Yesterday I went hiking.' } })
    fireEvent.click(screen.getByRole('button', { name: '保存这次重练' }))
    await waitFor(() => expect(api.mutate).toHaveBeenCalledTimes(3))
    expect(vi.mocked(api.mutate).mock.calls[2][1]).toMatchObject({ mode: 'free_response', parent_attempt_id: 'original-1', target_text: null })
  })

  it('retries unavailable feedback at the current revision while retaining a prepared practice recording', async () => {
    const api = mockLearningApi()
    const saved = finished()
    saved.feedback = { ...learningFeedback, status: 'unavailable', reason: '反馈服务暂不可用。', strength: '', focuses: [], criteria: [] }
    saved.attempts = []
    vi.mocked(api.mutate).mockImplementationOnce(async (_path, _payload, requestId) => ({ ...operation(saved), request_id: requestId }))
    await open(api)
    fireEvent.click(screen.getByRole('button', { name: '练这一句' }))
    await recordDraft()
    const provider = deferred<LearningOperation>()
    vi.mocked(api.mutate).mockReturnValueOnce(provider.promise)
    fireEvent.click(screen.getByRole('button', { name: '重新生成本次反馈' }))
    expect(vi.mocked(api.mutate).mock.calls[1][0]).toBe('/sessions/session-1/feedback')
    expect(vi.mocked(api.mutate).mock.calls[1][1]).toEqual({ expected_revision: saved.revision })
    expect(screen.getByRole('button', { name: '试听本次录音' })).toBeVisible()
    const updated = { ...saved, revision: saved.revision + 1, feedback: learningFeedback }
    await act(async () => provider.resolve(operation(updated)))
    expect(screen.getByRole('button', { name: '试听本次录音' })).toBeVisible()
    expect(screen.queryByRole('button', { name: '重新生成本次反馈' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '针对这一句重练' })).toBeVisible()
  })

  it('shows review 503 as unimplemented and retains practice from an unavailable feedback with no focuses', async () => {
    const api = mockLearningApi()
    const saved = finished()
    saved.feedback = { ...learningFeedback, status: 'unavailable', reason: '反馈服务暂不可用。', strength: '', focuses: [], criteria: [] }
    saved.attempts = []
    vi.mocked(api.reviews).mockRejectedValue(new LearningApiError('Reviews not integrated', 'review_unavailable', false, 503))
    vi.mocked(api.mutate).mockImplementation(async (_path, _payload, requestId) => ({ ...operation(saved), request_id: requestId }))
    await open(api)
    expect(screen.getByText('反馈服务暂不可用。')).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: '练这一句' }))
    expect(screen.getByRole('textbox', { name: '我的新表达' })).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: '到期复习' }))
    expect(screen.getAllByText(/复习功能尚未接入/).some(element => element.getAttribute('role') === 'status')).toBe(true)
    expect(screen.queryByText(/目前没有到期复习/)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '开始这项复习' })).not.toBeInTheDocument()
  })
})
