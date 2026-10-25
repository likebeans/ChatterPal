import { act, fireEvent, render, renderHook, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import LearningWorkspace from '../learning/LearningWorkspace'
import { LearningApiError } from '../learning/client'
import { useLearningFlow } from '../learning/useLearningFlow'
import type { LearningOperation, LearningSession } from '../learning/types'
import { learningBootstrap, learningFeedback, learningSession, mockLearningApi, operation } from './learning-fixtures'
import { deferred } from './setup'

beforeEach(() => {
  const items = new Map<string, string>()
  Object.defineProperty(window, 'localStorage', { configurable: true, value: {
    getItem: (key: string) => items.get(key) ?? null,
    setItem: (key: string, value: string) => { items.set(key, value) },
    removeItem: (key: string) => { items.delete(key) },
  } })
})

function pendingOperation(): LearningOperation {
  return { ...operation(undefined, 'pending'), session_id: 'session-1' }
}

async function beginNextTurn(api: ReturnType<typeof mockLearningApi>, result: { current: ReturnType<typeof useLearningFlow> }) {
  vi.mocked(api.mutate).mockImplementationOnce(async (_path, _payload, requestId) => ({
    operation_id: 'next-operation', request_id: requestId, session_id: 'session-1', status: 'pending',
  }))
  await act(async () => result.current.mutate('turn', '/sessions/session-1/turns', { expected_revision: 2, text: 'It was fun.' }, 'session-1'))
  await waitFor(() => expect(result.current.pending?.operation_id).toBe('next-operation'))
  return result.current.pending
}

describe('overlapping operation recovery', () => {
  it.each(['401', '404', '409'])('ignores a late %s from an older query after the next turn has started', async code => {
    const api = mockLearningApi()
    const lateQuery = deferred<LearningOperation>()
    const nextQuery = deferred<LearningOperation>()
    vi.mocked(api.bootstrap).mockResolvedValue({ ...learningBootstrap, pending_operations: [pendingOperation()] })
    vi.mocked(api.operation).mockResolvedValueOnce(pendingOperation()).mockReturnValueOnce(lateQuery.promise).mockResolvedValueOnce(operation(learningSession())).mockReturnValue(nextQuery.promise)
    const { result } = renderHook(() => useLearningFlow(api))
    await waitFor(() => expect(api.operation).toHaveBeenCalledOnce())
    act(() => { void result.current.retryPending() })
    await waitFor(() => expect(api.operation).toHaveBeenCalledTimes(2))
    await act(async () => result.current.retryPending())
    expect(result.current.pending).toBeNull()
    const nextPending = await beginNextTurn(api, result)

    await act(async () => lateQuery.reject(new LearningApiError('Late query failure', code)))

    expect(result.current.pending).toEqual(nextPending)
    expect(result.current.recovery).toBeNull()
    expect(result.current.error).toBe('')
    expect(result.current.pollingPaused).toBe(false)
  })

  it('ignores a late snapshot read failure after another query confirmed the result and the next turn started', async () => {
    const api = mockLearningApi()
    const lateSnapshot = deferred<LearningSession>()
    const nextQuery = deferred<LearningOperation>()
    const hint = { ...operation(), session_id: 'session-1', result_type: 'hint', result: { hint: 'Last weekend, I…' } }
    vi.mocked(api.bootstrap).mockResolvedValue({ ...learningBootstrap, pending_operations: [pendingOperation()] })
    vi.mocked(api.operation).mockImplementation(async id => id === 'next-operation' ? nextQuery.promise : hint)
    vi.mocked(api.session).mockReturnValueOnce(lateSnapshot.promise).mockResolvedValue({ ...learningSession(), revision: 2 })
    const { result } = renderHook(() => useLearningFlow(api))
    await waitFor(() => expect(api.session).toHaveBeenCalledOnce())
    await act(async () => result.current.retryPending())
    expect(result.current.pending).toBeNull()
    const nextPending = await beginNextTurn(api, result)

    await act(async () => lateSnapshot.reject(new LearningApiError('No longer available', '404')))

    expect(result.current.pending).toEqual(nextPending)
    expect(result.current.recovery).toBeNull()
    expect(result.current.error).toBe('')
    expect(result.current.pollingPaused).toBe(false)
  })

  it('ignores a late POST failure after request lookup confirmed the write and another turn started', async () => {
    const api = mockLearningApi()
    const lostResponse = deferred<LearningOperation>()
    const nextQuery = deferred<LearningOperation>()
    vi.mocked(api.mutate).mockReturnValueOnce(lostResponse.promise)
    vi.mocked(api.operation).mockReturnValue(nextQuery.promise)
    const { result } = renderHook(() => useLearningFlow(api))
    await waitFor(() => expect(result.current.identity).not.toBeNull())
    act(() => { void result.current.mutate('turn', '/sessions/session-1/turns', { expected_revision: 1, text: 'I went hiking.' }, 'session-1') })
    const requestId = result.current.pending!.request_id
    vi.mocked(api.requestStatus).mockResolvedValueOnce({ ...operation(learningSession()), request_id: requestId })
    await act(async () => result.current.retryPending())
    const nextPending = await beginNextTurn(api, result)

    await act(async () => lostResponse.reject(new LearningApiError('Old response failed', '409')))

    expect(result.current.pending).toEqual(nextPending)
    expect(result.current.recovery).toBeNull()
    expect(result.current.error).toBe('')
  })

  it('does not resume polling when a request lookup returns after the identity expired', async () => {
    const api = mockLearningApi()
    const lateLookup = deferred<LearningOperation>()
    const query = deferred<LearningOperation>()
    vi.mocked(api.mutate).mockRejectedValueOnce(new LearningApiError('Response lost', 'network', true))
    vi.mocked(api.requestStatus).mockReturnValueOnce(lateLookup.promise).mockRejectedValueOnce(new LearningApiError('Identity expired', '401'))
    vi.mocked(api.operation).mockReturnValue(query.promise)
    const { result } = renderHook(() => useLearningFlow(api))
    await waitFor(() => expect(result.current.identity).not.toBeNull())
    await act(async () => result.current.mutate('turn', '/sessions/session-1/turns', { expected_revision: 1, text: 'I went hiking.' }, 'session-1'))
    const requestId = result.current.pending!.request_id
    act(() => { void result.current.retryPending() })
    await act(async () => result.current.retryPending())
    expect(result.current.recovery).toBe('connect')

    await act(async () => lateLookup.resolve({ ...pendingOperation(), request_id: requestId }))

    expect(result.current.recovery).toBe('connect')
    expect(result.current.pollingPaused).toBe(true)
    expect(api.operation).not.toHaveBeenCalled()
  })

  it('keeps the connection recovery visible if an already running query succeeds after a 401', async () => {
    const api = mockLearningApi()
    const lateSuccess = deferred<LearningOperation>()
    vi.mocked(api.bootstrap).mockResolvedValue({ ...learningBootstrap, pending_operations: [pendingOperation()] })
    vi.mocked(api.operation).mockReturnValueOnce(lateSuccess.promise).mockRejectedValueOnce(new LearningApiError('Identity expired', '401'))
    const { result } = renderHook(() => useLearningFlow(api))
    await waitFor(() => expect(api.operation).toHaveBeenCalledOnce())
    await act(async () => result.current.retryPending())
    expect(result.current.recovery).toBe('connect')

    await act(async () => lateSuccess.resolve(operation(learningSession())))

    expect(result.current.recovery).toBe('connect')
    expect(result.current.error).toMatch(/重新连接/)
    expect(result.current.pollingPaused).toBe(true)
  })
})

describe('practice links after refreshed feedback', () => {
  it('restores the source sentence audio state when the authoritative record marks it expired', async () => {
    const api = mockLearningApi()
    const saved = learningSession()
    saved.status = 'completed'; saved.revision = 3; saved.feedback = structuredClone(learningFeedback)
    saved.turns.push({ turn_id: 'turn-1', role: 'user', text: 'I go hiking yesterday.', audio: { state: 'available', audio_asset_id: 'original-audio' } })
    vi.mocked(api.mutate).mockImplementationOnce(async (_path, _payload, requestId) => ({ ...operation(saved), request_id: requestId }))
    render(<LearningWorkspace active api={api} />)
    fireEvent.click(await screen.findByRole('button', { name: '开始描述周末' }))
    fireEvent.click(await screen.findByRole('button', { name: '针对这一句重练' }))
    expect(screen.getByRole('button', { name: '播放首次原音' })).toBeVisible()
    vi.mocked(api.session).mockResolvedValueOnce({ ...saved, turns: saved.turns.map(turn => turn.turn_id === 'turn-1' ? { ...turn, audio: { state: 'expired', audio_asset_id: 'original-audio', url: null } } : turn) })

    fireEvent.click(screen.getByRole('button', { name: '刷新本次记录' }))
    await waitFor(() => expect(screen.queryByRole('button', { name: '回放这句原音' })).not.toBeInTheDocument())

    expect(screen.queryByRole('button', { name: '播放首次原音' })).not.toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: '我的新表达' })).toBeVisible()
  })
})

describe('deletion invalidates record reads', () => {
  it('cannot revive a session opened while deletion is in flight, including after reconnect', async () => {
    const api = mockLearningApi()
    const deletion = deferred<void>()
    const lateRead = deferred<LearningSession>()
    vi.mocked(api.deleteData).mockReturnValueOnce(deletion.promise)
    vi.mocked(api.session).mockReturnValueOnce(lateRead.promise)
    const { result } = renderHook(() => useLearningFlow(api))
    await waitFor(() => expect(result.current.identity).not.toBeNull())
    act(() => { void result.current.deleteData() })
    act(() => { void result.current.openSession('deleted-session') })
    await act(async () => deletion.resolve())
    await act(async () => lateRead.resolve(learningSession('deleted-session')))

    expect(result.current.identity).toBeNull()
    expect(result.current.session).toBeNull()
    vi.mocked(api.bootstrap).mockResolvedValueOnce({ ...learningBootstrap, learner_id: 'new-owner' })
    await act(async () => result.current.connect())
    expect(result.current.session).toBeNull()
  })
})

describe('visible recovery while browsing records', () => {
  it('retains the handshake entry and draft when refreshing a record after a 401', async () => {
    const api = mockLearningApi()
    render(<LearningWorkspace active api={api} />)
    fireEvent.click(await screen.findByRole('button', { name: '开始描述周末' }))
    const input = await screen.findByRole('textbox', { name: '我的任务回答' })
    fireEvent.change(input, { target: { value: 'My weekend draft' } })
    vi.mocked(api.mutate).mockRejectedValueOnce(new LearningApiError('Identity expired', '401'))
    fireEvent.click(screen.getByRole('button', { name: '发送任务回答' }))
    await screen.findByRole('button', { name: '重新连接学习服务' })

    fireEvent.click(screen.getByRole('button', { name: '刷新本次记录' }))
    await waitFor(() => expect(api.session).toHaveBeenCalledWith('session-1'))

    fireEvent.click(await screen.findByRole('button', { name: '重新连接学习服务' }))
    await waitFor(() => expect(screen.getByRole('button', { name: '发送任务回答' })).toBeEnabled())
    expect(screen.getByRole('textbox', { name: '我的任务回答' })).toHaveValue('My weekend draft')
  })

  it('keeps a failed deletion recoverable after identity expiry without losing the draft', async () => {
    const api = mockLearningApi()
    render(<LearningWorkspace active api={api} />)
    fireEvent.click(await screen.findByRole('button', { name: '开始描述周末' }))
    fireEvent.change(await screen.findByRole('textbox', { name: '我的任务回答' }), { target: { value: 'Keep this draft until deletion succeeds' } })
    vi.mocked(api.mutate).mockRejectedValueOnce(new LearningApiError('Identity expired', '401'))
    fireEvent.click(screen.getByRole('button', { name: '发送任务回答' }))
    await screen.findByRole('button', { name: '重新连接学习服务' })
    fireEvent.click(screen.getByRole('button', { name: '学习设置' }))
    vi.mocked(api.deleteData).mockRejectedValueOnce(new LearningApiError('Identity expired', '401'))
    fireEvent.click(screen.getByRole('button', { name: '删除我的学习数据' }))
    fireEvent.click(screen.getByRole('button', { name: '确认删除全部学习数据' }))
    await waitFor(() => expect(api.deleteData).toHaveBeenCalledOnce())

    fireEvent.click(await screen.findByRole('button', { name: '重新连接学习服务' }))
    await waitFor(() => expect(api.bootstrap).toHaveBeenCalledTimes(2))
    fireEvent.click(screen.getByRole('button', { name: '返回本次练习' }))
    expect(screen.getByRole('textbox', { name: '我的任务回答' })).toHaveValue('Keep this draft until deletion succeeds')
  })
})
