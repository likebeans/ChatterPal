import { act, fireEvent, render, renderHook, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import LearningWorkspace from '../learning/LearningWorkspace'
import { LearningApiError, transcriptionBody } from '../learning/client'
import { practiceDraftKey, usePracticeDrafts } from '../learning/usePracticeDrafts'
import { useLearningFlow } from '../learning/useLearningFlow'
import { TRANSCRIPTION_RECEIPTS_KEY } from '../learning/transcriptionReceipts'
import type { LearningOperation, LearningReview, TranscriptionContext } from '../learning/types'
import { learningBootstrap, learningFeedback, learningSession, learningTranscription, mockLearningApi, operation, transcriptionOperation } from './learning-fixtures'
import { deferred, emitAudio, tone } from './setup'

beforeEach(() => {
  const items = new Map<string, string>()
  Object.defineProperty(window, 'localStorage', { configurable: true, value: {
    getItem: (key: string) => items.get(key) ?? null,
    setItem: (key: string, value: string) => { items.set(key, value) },
    removeItem: (key: string) => { items.delete(key) },
  } })
})

async function start(api = mockLearningApi()) {
  const view = render(<LearningWorkspace active api={api} />)
  fireEvent.click(await screen.findByRole('button', { name: '开始描述周末' }))
  await screen.findByRole('region', { name: '当前任务练习' })
  return { api, view }
}
async function record() {
  fireEvent.click(screen.getByRole('button', { name: '开始录音' }))
  await screen.findByRole('button', { name: '停止录音' })
  act(() => emitAudio([tone()]))
  fireEvent.click(screen.getByRole('button', { name: '停止录音' }))
  await screen.findByRole('button', { name: '识别并核对录音' })
}
async function preview(api: ReturnType<typeof mockLearningApi>) {
  vi.mocked(api.mutate).mockImplementationOnce(async (_path, payload, requestId) => transcriptionOperation(JSON.parse(String((payload as FormData).get('context'))), requestId))
  fireEvent.click(screen.getByRole('button', { name: '识别并核对录音' }))
  return screen.findByRole('textbox', { name: '核对录音文字' })
}
const review: LearningReview = { review_item_id: 'review-1', session_id: 'session-1', focus_id: 'past-tense', prompt: 'Tell me about a different weekend.', question_id: 'other-weekend', prompt_available: true, due_at: '2026-10-26T08:00:00Z', revision: 1, cycle: 0, used_hint: false, revealed_answer: false, independent_variant_available: true, availability_reason: '' }

describe('recording confirmation', () => {
  it('keeps the independent text draft, edits machine text, and clears only after final success', async () => {
    const { api } = await start()
    fireEvent.change(screen.getByRole('textbox', { name: '我的任务回答' }), { target: { value: 'Separate unsent text.' } })
    await record()
    const field = await preview(api)
    expect(field).toHaveFocus()
    expect(screen.getByRole('textbox', { name: '我的任务回答' })).toHaveValue('Separate unsent text.')
    expect(api.session).not.toHaveBeenCalled()
    const stored = JSON.parse(window.localStorage.getItem(TRANSCRIPTION_RECEIPTS_KEY)!)[0]
    expect(Object.keys(stored).sort()).toEqual(['draft_key', 'draft_version', 'learner_id', 'operation_id', 'request_id', 'transcription_id'])
    expect(JSON.stringify(stored)).not.toContain('hiking')
    fireEvent.change(field, { target: { value: 'I went hiking yesterday.' } })
    fireEvent.click(screen.getByText('查看机器识别原文'))
    expect(screen.getByText('I go hiking yesterday.')).toBeVisible()
    const saved = learningSession(); saved.revision = 2
    saved.turns.push({ turn_id: 'turn-1', role: 'user', text: 'I went hiking yesterday.', machine_transcript: 'I go hiking yesterday.', confirmed_text: 'I went hiking yesterday.', transcription_id: 'transcription-1', confirmed_at: saved.created_at })
    vi.mocked(api.mutate).mockImplementationOnce(async (_path, payload, requestId) => {
      expect(payload).toEqual({ expected_revision: 1, transcription_id: 'transcription-1', confirmed_text: 'I went hiking yesterday.' })
      return { ...operation(saved), request_id: requestId }
    })
    fireEvent.click(screen.getByRole('button', { name: '确认文字并提交' }))
    await waitFor(() => expect(screen.queryByRole('textbox', { name: '核对录音文字' })).not.toBeInTheDocument())
    expect(screen.getByText('已核对的文字')).toBeVisible()
    expect(window.localStorage.getItem(TRANSCRIPTION_RECEIPTS_KEY)).toBeNull()
  })

  it('retains edited text and the ready receipt after final provider failure, using a new final request without re-ASR', async () => {
    const { api } = await start()
    await record()
    fireEvent.change(await preview(api), { target: { value: 'Yesterday I went hiking with friends.' } })
    vi.mocked(api.mutate).mockImplementationOnce(async (_path, _payload, requestId) => ({ ...operation(undefined, 'failed'), request_id: requestId, error: { code: 'provider_unavailable', message: '教练暂时不可用，请重试。' } }))
    fireEvent.click(screen.getByRole('button', { name: '确认文字并提交' }))
    await screen.findByText('教练暂时不可用，请重试。')
    expect(screen.getByRole('textbox', { name: '核对录音文字' })).toHaveValue('Yesterday I went hiking with friends.')
    expect(window.localStorage.getItem(TRANSCRIPTION_RECEIPTS_KEY)).not.toBeNull()
    fireEvent.click(screen.getByRole('button', { name: '学习记录' }))
    fireEvent.click(screen.getByRole('button', { name: '返回本次练习' }))
    expect(screen.getByRole('textbox', { name: '核对录音文字' })).toHaveValue('Yesterday I went hiking with friends.')
    fireEvent.click(screen.getByRole('button', { name: '确认文字并提交' }))
    await waitFor(() => expect(api.mutate).toHaveBeenCalledTimes(4))
    const calls = vi.mocked(api.mutate).mock.calls
    expect(calls.filter(([path]) => path === '/transcriptions')).toHaveLength(1)
    expect(calls[2][2]).not.toBe(calls[3][2])
    expect(calls[3][1]).toEqual(calls[2][1])
  })

  it('shows a failed DELETE inline without losing edits, and restores text input after confirmed discard', async () => {
    const { api } = await start()
    fireEvent.change(screen.getByRole('textbox', { name: '我的任务回答' }), { target: { value: 'Text stays separate.' } })
    await record()
    fireEvent.change(await preview(api), { target: { value: 'Edited confirmation.' } })
    vi.mocked(api.deleteTranscription).mockRejectedValueOnce(new LearningApiError('撤销服务暂不可用。', 'network', true))
    fireEvent.click(screen.getByRole('button', { name: '放弃本次录音' }))
    await screen.findByText(/无法放弃本次录音.*撤销服务暂不可用.*核对文字已保留/)
    expect(screen.getByRole('textbox', { name: '核对录音文字' })).toHaveValue('Edited confirmation.')
    expect(window.localStorage.getItem(TRANSCRIPTION_RECEIPTS_KEY)).not.toBeNull()
    fireEvent.click(screen.getByRole('button', { name: '放弃本次录音' }))
    await waitFor(() => expect(screen.queryByRole('textbox', { name: '核对录音文字' })).not.toBeInTheDocument())
    expect(screen.getByRole('textbox', { name: '我的任务回答' })).toHaveValue('Text stays separate.')
    expect(screen.getByRole('textbox', { name: '我的任务回答' })).toHaveFocus()
    expect(window.localStorage.getItem(TRANSCRIPTION_RECEIPTS_KEY)).toBeNull()
  })

  it('cancels an in-flight preview before rerecording, ignores the old result and allocates a new draft version', async () => {
    const { api } = await start()
    await record()
    const old = deferred<LearningOperation>()
    vi.mocked(api.mutate).mockReturnValueOnce(old.promise)
    fireEvent.click(screen.getByRole('button', { name: '识别并核对录音' }))
    const oldReceipt = JSON.parse(window.localStorage.getItem('chatterpal.learning.pending.v1')!)
    fireEvent.click(screen.getByRole('button', { name: '重新录音' }))
    await screen.findByRole('button', { name: '停止录音' })
    expect(api.deleteTranscriptionRequest).toHaveBeenCalledWith(oldReceipt.request_id)
    act(() => emitAudio([tone()]))
    fireEvent.click(screen.getByRole('button', { name: '停止录音' }))
    await screen.findByRole('button', { name: '识别并核对录音' })
    await act(async () => old.resolve(transcriptionOperation(undefined, oldReceipt.request_id, 'old-transcription')))
    expect(screen.queryByRole('textbox', { name: '核对录音文字' })).not.toBeInTheDocument()
    expect(window.localStorage.getItem(TRANSCRIPTION_RECEIPTS_KEY)).toBeNull()
    await preview(api)
    const receipt = JSON.parse(window.localStorage.getItem(TRANSCRIPTION_RECEIPTS_KEY)!)[0]
    expect(receipt.draft_version).toBeGreaterThan(oldReceipt.draft_version)
    expect(receipt.request_id).not.toBe(oldReceipt.request_id)
  })

  it('retains an uncertain pending receipt when cancellation races a missing original request', async () => {
    const { api } = await start()
    await record()
    const pending = deferred<LearningOperation>()
    vi.mocked(api.mutate).mockReturnValueOnce(pending.promise)
    fireEvent.click(screen.getByRole('button', { name: '识别并核对录音' }))
    const receipt = window.localStorage.getItem('chatterpal.learning.pending.v1')
    vi.mocked(api.deleteTranscriptionRequest).mockRejectedValueOnce(new LearningApiError('Not yet found', '404'))
    vi.mocked(api.requestStatus).mockRejectedValueOnce(new LearningApiError('Original request is uncertain', '404'))
    fireEvent.click(screen.getByRole('button', { name: '放弃本次录音' }))
    await screen.findByText(/无法放弃本次录音.*Original request is uncertain.*核对文字已保留/)
    expect(api.requestStatus).toHaveBeenCalledWith(JSON.parse(receipt!).request_id)
    expect(window.localStorage.getItem('chatterpal.learning.pending.v1')).toBe(receipt)
    expect(screen.getByRole('button', { name: '识别并核对录音' })).toBeDisabled()
    expect(api.mutate).toHaveBeenCalledTimes(2)
  })

  it('confirms a pending request after a DELETE404 race and cancels it before allowing another recording', async () => {
    const { api } = await start()
    await record()
    const pending = deferred<LearningOperation>()
    vi.mocked(api.mutate).mockReturnValueOnce(pending.promise)
    fireEvent.click(screen.getByRole('button', { name: '识别并核对录音' }))
    const receipt = JSON.parse(window.localStorage.getItem('chatterpal.learning.pending.v1')!)
    vi.mocked(api.deleteTranscriptionRequest).mockRejectedValueOnce(new LearningApiError('Not yet found', '404'))
    vi.mocked(api.requestStatus).mockResolvedValueOnce({ ...transcriptionOperation(undefined, receipt.request_id), operation_id: 'found-operation' })
    fireEvent.click(screen.getByRole('button', { name: '放弃本次录音' }))
    await waitFor(() => expect(screen.getByRole('button', { name: '开始录音' })).toBeEnabled())
    expect(api.deleteTranscriptionRequest).toHaveBeenCalledTimes(2)
    expect(api.requestStatus).toHaveBeenCalledWith(receipt.request_id)
    expect(window.localStorage.getItem('chatterpal.learning.pending.v1')).toBeNull()
    await act(async () => pending.resolve(transcriptionOperation(undefined, receipt.request_id)))
    expect(screen.queryByRole('textbox', { name: '核对录音文字' })).not.toBeInTheDocument()
  })

  it('recovers ready text and server audio after reload without another ASR call', async () => {
    const { api, view } = await start()
    await record()
    fireEvent.change(await preview(api), { target: { value: 'This unconfirmed edit may be lost.' } })
    view.unmount()
    vi.mocked(api.mutate).mockClear()
    const restored = render(<LearningWorkspace active api={api} />)
    fireEvent.click(await screen.findByRole('button', { name: '继续核对任务录音' }))
    expect(await screen.findByRole('textbox', { name: '核对录音文字' })).toHaveValue('I go hiking yesterday.')
    expect(api.getTranscription).toHaveBeenCalledWith('transcription-1', expect.any(AbortSignal))
    expect(api.mutate).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: '试听本次录音' }))
    expect(restored.container.querySelector('audio')).toHaveAttribute('src', '/api/learning/audio/draft-audio')
    expect(screen.getByRole('button', { name: '确认文字并提交' })).toBeEnabled()
  })

  it('keeps a late preview on its original draft through navigation', async () => {
    const { api } = await start()
    await record()
    const response = deferred<LearningOperation>()
    vi.mocked(api.mutate).mockReturnValueOnce(response.promise)
    fireEvent.click(screen.getByRole('button', { name: '识别并核对录音' }))
    const requestId = vi.mocked(api.mutate).mock.calls[1][2]
    fireEvent.click(screen.getByRole('button', { name: '学习记录' }))
    await act(async () => response.resolve(transcriptionOperation(undefined, requestId)))
    expect(screen.queryByRole('textbox', { name: '核对录音文字' })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '返回本次练习' }))
    expect(screen.getByRole('textbox', { name: '核对录音文字' })).toHaveValue('I go hiking yesterday.')
  })

  it('rejects a late preview after the learner changes and clears recovery receipts', async () => {
    const api = mockLearningApi()
    const { result } = renderHook(() => useLearningFlow(api))
    await waitFor(() => expect(result.current.identity).not.toBeNull())
    const response = deferred<LearningOperation>()
    vi.mocked(api.mutate).mockReturnValueOnce(response.promise)
    act(() => { void result.current.mutate('transcribe', '/transcriptions', transcriptionBody({ kind: 'turn', session_id: 'session-1', expected_revision: 1 }, new Blob(['wav'])), 'session-1', { draft_key: practiceDraftKey('learner-1', 'session-1', 'turn'), draft_version: 2 }) })
    const requestId = vi.mocked(api.mutate).mock.calls[0][2]
    vi.mocked(api.bootstrap).mockResolvedValueOnce({ ...learningBootstrap, learner_id: 'new-learner' })
    await act(async () => result.current.connect())
    await act(async () => response.resolve(transcriptionOperation(undefined, requestId)))
    expect(result.current.readyTranscriptions).toEqual([])
    expect(window.localStorage.getItem(TRANSCRIPTION_RECEIPTS_KEY)).toBeNull()
    expect(result.current.pending).toBeNull()
  })

  it('does not re-ASR or silently rebind a ready review after a hint changes its context', async () => {
    const api = mockLearningApi()
    vi.mocked(api.reviews).mockResolvedValue([review])
    render(<LearningWorkspace active api={api} />)
    fireEvent.click(await screen.findByRole('button', { name: '到期复习（1）' }))
    fireEvent.click(screen.getByRole('button', { name: '开始这项复习' }))
    await record()
    fireEvent.change(await preview(api), { target: { value: 'My edited review answer.' } })
    vi.mocked(api.mutate).mockImplementationOnce(async (_path, _payload, requestId) => ({ ...operation(), request_id: requestId, result_type: 'review_hint', result: { ...review, revision: 2, used_hint: true, hint: 'Last weekend...' } }))
    fireEvent.click(screen.getByRole('button', { name: '给我一个复习开头并记录提示' }))
    await screen.findByText('Last weekend...')
    expect(screen.getByRole('textbox', { name: '核对录音文字' })).toHaveValue('My edited review answer.')
    expect(screen.getByRole('button', { name: '确认文字并提交' })).toBeDisabled()
    expect(screen.getByText(/这段录音对应的练习内容已有更新/)).toBeVisible()
    expect(vi.mocked(api.mutate).mock.calls.filter(([path]) => path === '/transcriptions')).toHaveLength(1)
    expect(window.localStorage.getItem(TRANSCRIPTION_RECEIPTS_KEY)).not.toBeNull()
  })

  it('uses two steps for focused attempts with pinned source IDs, read-aloud target and parent chain', async () => {
    const api = mockLearningApi()
    const saved = learningSession(); saved.revision = 4; saved.status = 'completed'; saved.feedback = structuredClone(learningFeedback)
    saved.turns.push({ turn_id: 'turn-1', role: 'user', text: 'I go hiking yesterday.' })
    vi.mocked(api.mutate).mockImplementationOnce(async (_path, _payload, requestId) => ({ ...operation(saved), request_id: requestId }))
    await start(api)
    fireEvent.click(screen.getByRole('button', { name: '针对这一句重练' }))
    fireEvent.click(screen.getByRole('button', { name: '跟读练习' }))
    await record()
    const field = await preview(api)
    const context: TranscriptionContext = { kind: 'attempt', session_id: 'session-1', source_revision: 4, source_turn_id: 'turn-1', focus_id: 'past-tense', parent_attempt_id: null, mode: 'read_aloud', target_text: 'I went hiking yesterday.' }
    expect(JSON.parse(String((vi.mocked(api.mutate).mock.calls[1][1] as FormData).get('context')))).toEqual(context)
    fireEvent.change(field, { target: { value: 'I went hiking yesterday.' } })
    const attempt = { attempt_id: 'attempt-confirmed', session_id: 'session-1', source_turn_id: 'turn-1', focus_id: 'past-tense', parent_attempt_id: null, mode: 'read_aloud' as const,
      transcript: 'I went hiking yesterday.', machine_transcript: 'I go hiking yesterday.', confirmed_text: 'I went hiking yesterday.', transcription_id: 'transcription-1', confirmed_at: saved.updated_at,
      feedback: null, created_at: saved.updated_at, evaluation: { status: 'available' as const, result: 'met' as const, source: 'text' as const, reason: '文字依据。' } }
    vi.mocked(api.mutate).mockImplementationOnce(async (_path, _payload, requestId) => ({ ...operation({ ...saved, revision: 5, attempts: [attempt] }), request_id: requestId }))
    fireEvent.click(screen.getByRole('button', { name: '确认文字并提交' }))
    await screen.findByText('已核对的文字')
    expect(vi.mocked(api.mutate).mock.calls[2][1]).toEqual({ session_id: 'session-1', source_revision: 4, source_turn_id: 'turn-1', focus_id: 'past-tense', parent_attempt_id: null, mode: 'read_aloud', target_text: 'I went hiking yesterday.', transcription_id: 'transcription-1', confirmed_text: 'I went hiking yesterday.' })
  })

  it('preserves a ready recording on a normal revision conflict and marks stale after authoritative refresh', async () => {
    const { api } = await start()
    await record()
    fireEvent.change(await preview(api), { target: { value: 'Retain my confirmation.' } })
    vi.mocked(api.mutate).mockRejectedValueOnce(new LearningApiError('Revision updated', 'revision_conflict', false, 409))
    fireEvent.click(screen.getByRole('button', { name: '确认文字并提交' }))
    const refresh = await screen.findByRole('button', { name: '刷新本次记录后继续' })
    expect(screen.getByRole('textbox', { name: '核对录音文字' })).toHaveValue('Retain my confirmation.')
    vi.mocked(api.session).mockResolvedValueOnce({ ...learningSession(), revision: 2 })
    fireEvent.click(refresh)
    await screen.findByText(/这段录音对应的练习内容已有更新/)
    expect(screen.getByRole('textbox', { name: '核对录音文字' })).toHaveValue('Retain my confirmation.')
    expect(window.localStorage.getItem(TRANSCRIPTION_RECEIPTS_KEY)).not.toBeNull()
  })

  it.each([
    ['transcription_context_changed', 409, '练习内容已有更新'],
    ['transcription_expired', 410, '录音已过期'],
    ['transcription_consumed', 409, '录音已经提交'],
  ] as const)('preserves the edited confirmation for terminal %s feedback', async (code, status, expected) => {
    const { api } = await start()
    await record()
    fireEvent.change(await preview(api), { target: { value: 'Retain this edited confirmation.' } })
    const receipt = window.localStorage.getItem(TRANSCRIPTION_RECEIPTS_KEY)
    vi.mocked(api.mutate).mockRejectedValueOnce(new LearningApiError('Server transcription status changed', code, false, status))
    fireEvent.click(screen.getByRole('button', { name: '确认文字并提交' }))
    await screen.findByText(new RegExp(expected))
    expect(screen.getByRole('textbox', { name: '核对录音文字' })).toHaveValue('Retain this edited confirmation.')
    expect(window.localStorage.getItem(TRANSCRIPTION_RECEIPTS_KEY)).toBe(receipt)
    expect(vi.mocked(api.mutate).mock.calls.filter(([path]) => path === '/transcriptions')).toHaveLength(1)
  })

  it('clears ready receipts on full data deletion', async () => {
    const { api } = await start()
    await record(); await preview(api)
    fireEvent.click(screen.getByRole('button', { name: '学习设置' }))
    fireEvent.click(screen.getByRole('button', { name: '删除我的学习数据' }))
    fireEvent.click(screen.getByRole('button', { name: '确认删除全部学习数据' }))
    await waitFor(() => expect(window.localStorage.getItem(TRANSCRIPTION_RECEIPTS_KEY)).toBeNull())
    expect(screen.queryByRole('button', { name: '继续核对任务录音' })).not.toBeInTheDocument()
  })
})

describe('discard lifecycle guards', () => {
  it.each([404, 409])('retains a ready receipt when DELETE returns HTTP %s', async status => {
    const { api } = await start()
    await record()
    fireEvent.change(await preview(api), { target: { value: 'Retain on delete rejection.' } })
    const receipt = window.localStorage.getItem(TRANSCRIPTION_RECEIPTS_KEY)
    vi.mocked(api.deleteTranscription).mockRejectedValueOnce(new LearningApiError('服务端未确认撤销', String(status)))
    fireEvent.click(screen.getByRole('button', { name: '放弃本次录音' }))
    await screen.findByText(/服务端未确认撤销.*核对文字已保留/)
    expect(window.localStorage.getItem(TRANSCRIPTION_RECEIPTS_KEY)).toBe(receipt)
    expect(screen.getByRole('textbox', { name: '核对录音文字' })).toHaveValue('Retain on delete rejection.')
    expect(screen.getByRole('button', { name: '放弃本次录音' })).toBeEnabled()
  })

  it('clears the original draft after a successful DELETE completes on another page', async () => {
    const { api } = await start()
    await record(); await preview(api)
    const deletion = deferred<void>()
    vi.mocked(api.deleteTranscription).mockReturnValueOnce(deletion.promise)
    fireEvent.click(screen.getByRole('button', { name: '放弃本次录音' }))
    fireEvent.click(screen.getByRole('button', { name: '学习记录' }))
    await act(async () => deletion.resolve())
    expect(window.localStorage.getItem(TRANSCRIPTION_RECEIPTS_KEY)).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: '返回本次练习' }))
    expect(screen.queryByRole('textbox', { name: '核对录音文字' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '开始录音' })).toBeEnabled()
  })

  it('keeps the receipt and an inline failure when DELETE rejects after navigating away', async () => {
    const { api } = await start()
    await record()
    fireEvent.change(await preview(api), { target: { value: 'Keep across pages.' } })
    const before = window.localStorage.getItem(TRANSCRIPTION_RECEIPTS_KEY)
    const deletion = deferred<void>()
    vi.mocked(api.deleteTranscription).mockReturnValueOnce(deletion.promise)
    fireEvent.click(screen.getByRole('button', { name: '放弃本次录音' }))
    fireEvent.click(screen.getByRole('button', { name: '学习记录' }))
    await act(async () => deletion.reject(new LearningApiError('撤销尚未完成', '503', true)))
    expect(screen.getByText(/无法放弃本次录音.*撤销尚未完成/)).toBeVisible()
    expect(window.localStorage.getItem(TRANSCRIPTION_RECEIPTS_KEY)).toBe(before)
    fireEvent.click(screen.getByRole('button', { name: '返回本次练习' }))
    expect(screen.getByRole('textbox', { name: '核对录音文字' })).toHaveValue('Keep across pages.')
    expect(screen.getByRole('button', { name: '放弃本次录音' })).toBeEnabled()
  })

  it.each([false, true])('settles deletion while the top-level view is inactive (failure=%s)', async failed => {
    const { api, view } = await start()
    await record(); await preview(api)
    const deletion = deferred<void>()
    vi.mocked(api.deleteTranscription).mockReturnValueOnce(deletion.promise)
    fireEvent.click(screen.getByRole('button', { name: '放弃本次录音' }))
    view.rerender(<LearningWorkspace active={false} api={api} />)
    await act(async () => { if (failed) deletion.reject(new LearningApiError('撤销失败', '503', true)); else deletion.resolve() })
    view.rerender(<LearningWorkspace active api={api} />)
    expect(screen.queryByText('正在撤销录音草稿…')).not.toBeInTheDocument()
    if (failed) {
      expect(screen.getByRole('button', { name: '放弃本次录音' })).toBeEnabled()
      expect(window.localStorage.getItem(TRANSCRIPTION_RECEIPTS_KEY)).not.toBeNull()
      expect(screen.getByText(/无法放弃本次录音.*核对文字已保留/)).toBeVisible()
    } else {
      expect(screen.getByRole('button', { name: '开始录音' })).toBeEnabled()
      expect(window.localStorage.getItem(TRANSCRIPTION_RECEIPTS_KEY)).toBeNull()
    }
  })

  it('ignores a late successful DELETE after replacing the learner identity', async () => {
    const { api } = await start()
    await record(); await preview(api)
    const deletion = deferred<void>()
    vi.mocked(api.deleteTranscription).mockReturnValueOnce(deletion.promise)
    fireEvent.click(screen.getByRole('button', { name: '放弃本次录音' }))
    vi.mocked(api.session).mockRejectedValueOnce(new LearningApiError('身份失效', '401'))
    vi.mocked(api.bootstrap).mockResolvedValueOnce({ ...learningBootstrap, learner_id: 'replacement-owner' })
    fireEvent.click(screen.getByRole('button', { name: '刷新本次记录' }))
    fireEvent.click(await screen.findByRole('button', { name: '重新连接学习服务' }))
    await screen.findByRole('button', { name: '开始描述周末' })
    await act(async () => deletion.resolve())
    fireEvent.click(screen.getByRole('button', { name: '开始描述周末' }))
    expect(await screen.findByRole('textbox', { name: '我的任务回答' })).toHaveValue('')
    expect(screen.queryByRole('textbox', { name: '核对录音文字' })).not.toBeInTheDocument()
    expect(window.localStorage.getItem(TRANSCRIPTION_RECEIPTS_KEY)).toBeNull()
  })

  it('rejects stale discard callbacks and retains a newer draft version', () => {
    const { result, rerender } = renderHook(({ owner }) => usePracticeDrafts(owner, null, []), { initialProps: { owner: 'learner-1' } })
    const key = practiceDraftKey('learner-1', 'session-1', 'turn')
    act(() => result.current.update(key, { audio: new Blob(['first']) }))
    const oldVersion = result.current.read(key).version
    act(() => result.current.update(key, { audio: new Blob(['newer']) }))
    const newer = result.current.read(key)
    act(() => result.current.discard(key, oldVersion))
    expect(result.current.read(key)).toBe(newer)
    const staleUpdate = result.current.update
    const staleDiscard = result.current.discard
    rerender({ owner: 'replacement-owner' })
    const replacementKey = practiceDraftKey('replacement-owner', 'session-1', 'turn')
    act(() => result.current.update(replacementKey, { text: 'Replacement learner draft.' }))
    act(() => { staleUpdate(key, { text: 'Old learner update' }); staleDiscard(key, newer.version) })
    expect(result.current.read(replacementKey).text).toBe('Replacement learner draft.')
  })
})

it('recovers a completed pending transcription receipt after reload without changing the session revision', async () => {
  const api = mockLearningApi()
  const key = practiceDraftKey('learner-1', 'session-1', 'turn')
  const receipt = { request_id: 'pending-request', operation_id: 'pending-operation', action: 'transcribe', learner_id: 'learner-1', session_id: 'session-1', draft_key: key, draft_version: 7 }
  window.localStorage.setItem('chatterpal.learning.pending.v1', JSON.stringify(receipt))
  vi.mocked(api.bootstrap).mockResolvedValueOnce({ ...learningBootstrap, pending_operations: [{ ...operation(undefined, 'pending'), action: 'transcribe', request_id: receipt.request_id, operation_id: receipt.operation_id }] })
  vi.mocked(api.operation).mockResolvedValueOnce({ ...transcriptionOperation(undefined, receipt.request_id), operation_id: receipt.operation_id })
  render(<LearningWorkspace active api={api} />)
  fireEvent.click(await screen.findByRole('button', { name: '继续核对任务录音' }))
  expect(await screen.findByRole('textbox', { name: '核对录音文字' })).toHaveValue(learningTranscription().machine_transcript)
  expect(api.mutate).not.toHaveBeenCalled()
  expect(JSON.parse(window.localStorage.getItem(TRANSCRIPTION_RECEIPTS_KEY)!)[0].draft_version).toBe(7)
})
