import { act, fireEvent, render, renderHook, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import LearningWorkspace from '../learning/LearningWorkspace'
import { LearningApiError, parseOperation } from '../learning/client'
import { useLearningFlow } from '../learning/useLearningFlow'
import type { LearningReview, ReviewResult } from '../learning/types'
import { learningFeedback, learningSession, mockLearningApi, operation, transcriptionOperation } from './learning-fixtures'
import { deferred, emitAudio, tone } from './setup'

const review: LearningReview = { review_item_id: 'review-1', session_id: 'session-1', focus_id: 'past-tense', prompt: 'Tell another friend about a quiet weekend.', question_id: 'quiet-weekend', prompt_available: true, due_at: '2026-10-26T08:00:00Z', revision: 1, cycle: 0, used_hint: false, revealed_answer: false, independent_variant_available: true, availability_reason: '' }

function reviewAnswer(nextReview: LearningReview = { ...review, revision: 2, cycle: 1, prompt: null, question_id: null, prompt_available: false, due_at: '2026-10-29T08:00:00Z', availability_reason: '新问题将在到期后显示。' }): ReviewResult {
  return { review_item_id: review.review_item_id, used_hint: false, next_due_at: nextReview.due_at, review: nextReview, attempt: {
    attempt_id: 'review-attempt-1', session_id: review.session_id, review_item_id: review.review_item_id, source_turn_id: 'turn-1', focus_id: review.focus_id,
    parent_attempt_id: null, mode: 'free_response', transcript: 'I read at home alone and felt rested.', feedback: null, created_at: '2026-10-26T08:01:00Z',
    evaluation: { status: 'available', result: 'met', source: 'model', reason: '本次原话说明了活动、同行和感受。' },
    question_id: review.question_id!, prompt: review.prompt!, used_hint: false, revealed_answer: false, independent: true, delayed: true,
    schedule_advanced: true, variant_consumed: true, evidence_kind: 'delayed_transfer',
  } }
}

beforeEach(() => {
  const items = new Map<string, string>()
  Object.defineProperty(window, 'localStorage', { configurable: true, value: {
    getItem: (key: string) => items.get(key) ?? null,
    setItem: (key: string, value: string) => { items.set(key, value) },
    removeItem: (key: string) => { items.delete(key) },
  } })
})

async function openReview(api = mockLearningApi(), item = review) {
  vi.mocked(api.reviews).mockResolvedValue([item])
  const rendered = render(<LearningWorkspace active api={api} />)
  fireEvent.click(await screen.findByRole('button', { name: '到期复习（1）' }))
  fireEvent.click(screen.getByRole('button', { name: '开始这项复习' }))
  return { api, ...rendered }
}

it('explicitly saves a focus and a selected attempt using pinned source IDs', async () => {
  const api = mockLearningApi()
  const session = learningSession()
  session.status = 'completed'; session.revision = 4; session.feedback = structuredClone(learningFeedback)
  session.turns.push({ turn_id: 'turn-1', role: 'user', text: 'I go hiking yesterday.' })
  session.attempts.push({ attempt_id: 'attempt-1', session_id: session.session_id, source_turn_id: 'turn-1', focus_id: 'past-tense', parent_attempt_id: null, mode: 'free_response', transcript: 'I went hiking yesterday.', feedback: null, created_at: session.created_at, evaluation: { status: 'available', result: 'met', source: 'text', reason: '文字达成。' } })
  vi.mocked(api.session).mockResolvedValue(session)
  vi.mocked(api.mutate).mockImplementation(async (path, _payload, requestId) => path === '/reviews'
    ? { ...operation(), request_id: requestId, session_id: session.session_id, result_type: 'review_saved', result: review }
    : { ...operation(session), request_id: requestId })
  render(<LearningWorkspace active api={api} />)
  fireEvent.click(await screen.findByRole('button', { name: '开始描述周末' }))
  fireEvent.click(await screen.findByRole('button', { name: '保存这个表达到复习' }))
  await waitFor(() => expect(api.mutate).toHaveBeenCalledWith('/reviews', { session_id: 'session-1', source_revision: 4, source_turn_id: 'turn-1', focus_id: 'past-tense' }, expect.any(String)))
  fireEvent.click(screen.getByRole('button', { name: '针对这一句重练' }))
  fireEvent.click(screen.getByRole('button', { name: '保存这次尝试到复习' }))
  await waitFor(() => expect(api.mutate).toHaveBeenLastCalledWith('/reviews', { session_id: 'session-1', source_revision: 4, source_turn_id: 'turn-1', focus_id: 'past-tense', attempt_id: 'attempt-1' }, expect.any(String)))
})

it('waits for durable reveal before showing the answer and submits with its updated revision', async () => {
  const api = mockLearningApi()
  const reveal = deferred<ReturnType<typeof operation>>()
  vi.mocked(api.mutate).mockReturnValueOnce(reveal.promise)
  await openReview(api)
  fireEvent.change(screen.getByRole('textbox', { name: '我的复习回答' }), { target: { value: 'Keep my draft while revealing.' } })
  fireEvent.click(screen.getByRole('button', { name: '显示参考表达并记录提示' }))
  expect(screen.queryByText('I went hiking.')).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: '提交复习回答' })).toBeDisabled()
  await act(async () => reveal.resolve({ ...operation(), request_id: vi.mocked(api.mutate).mock.calls[0][2], session_id: 'session-1', result_type: 'review_reveal', result: { ...review, revision: 2, used_hint: true, revealed_answer: true, answer: 'I went hiking.' } }))
  expect(await screen.findByText('I went hiking.')).toBeVisible()
  expect(screen.getByRole('textbox', { name: '我的复习回答' })).toHaveValue('Keep my draft while revealing.')
  vi.mocked(api.mutate).mockImplementation(async (_path, _payload, requestId) => ({ ...operation(), request_id: requestId, result_type: 'review_deferred', result: { ...review, revision: 3 } }))
  fireEvent.click(screen.getByRole('button', { name: '提交复习回答' }))
  await waitFor(() => expect(api.mutate).toHaveBeenLastCalledWith('/reviews/review-1/attempts', { expected_revision: 2, text: 'Keep my draft while revealing.' }, expect.any(String)))
  expect(vi.mocked(api.mutate).mock.calls[1][1]).not.toHaveProperty('used_hint')
})

it('submits recorded audio with a review revision and makes defer an explicit separate action', async () => {
  const { api } = await openReview()
  vi.mocked(api.mutate).mockImplementation(async (_path, _payload, requestId) => ({ ...operation(), request_id: requestId, result_type: 'review_deferred', result: { ...review, revision: 2, due_at: '2026-10-27T08:00:00Z' } }))
  fireEvent.click(screen.getByRole('button', { name: '开始录音' }))
  await screen.findByRole('button', { name: '停止录音' })
  act(() => emitAudio([tone()]))
  fireEvent.click(screen.getByRole('button', { name: '停止录音' }))
  await screen.findByRole('button', { name: '试听本次录音' })
  vi.mocked(api.mutate).mockImplementationOnce(async (_path, _payload, requestId) => transcriptionOperation({ kind: 'review', review_item_id: review.review_item_id, expected_revision: review.revision }, requestId))
  fireEvent.click(screen.getByRole('button', { name: '识别并核对录音' }))
  await screen.findByRole('textbox', { name: '核对录音文字' })
  const payload = vi.mocked(api.mutate).mock.calls[0][1] as FormData
  expect(JSON.parse(String(payload.get('context')))).toEqual({ kind: 'review', review_item_id: 'review-1', expected_revision: 1 })
  expect((payload.get('audio') as File).type).toBe('audio/wav')
  expect(payload.has('text')).toBe(false)
  fireEvent.click(screen.getByRole('button', { name: '确认文字并提交' }))
  await waitFor(() => expect(api.mutate).toHaveBeenCalledTimes(2))
  expect(vi.mocked(api.mutate).mock.calls[1][1]).toEqual({ expected_revision: 1, transcription_id: 'transcription-1', confirmed_text: 'I go hiking yesterday.' })
  await waitFor(() => expect(screen.getByRole('button', { name: '明天再复习' })).toBeEnabled())
  fireEvent.click(screen.getByRole('button', { name: '明天再复习' }))
  await waitFor(() => expect(api.mutate).toHaveBeenLastCalledWith('/reviews/review-1/defer', { expected_revision: 2, days: 1 }, expect.any(String)))
})

it('refreshes a conflicting review before retry while retaining the unsent answer', async () => {
  const { api } = await openReview()
  vi.mocked(api.mutate).mockRejectedValueOnce(new LearningApiError('Another tab revealed this review', 'revision_conflict', false, 409))
  vi.mocked(api.review).mockResolvedValueOnce({ ...review, revision: 2, used_hint: true, revealed_answer: true } as LearningReview)
  fireEvent.change(screen.getByRole('textbox', { name: '我的复习回答' }), { target: { value: 'A retained review draft.' } })
  fireEvent.click(screen.getByRole('button', { name: '提交复习回答' }))
  fireEvent.click(await screen.findByRole('button', { name: '刷新本次记录后继续' }))
  await waitFor(() => expect(screen.getByRole('button', { name: '提交复习回答' })).toBeEnabled())
  expect(screen.getByRole('textbox', { name: '我的复习回答' })).toHaveValue('A retained review draft.')
  expect(api.review).toHaveBeenCalledWith('review-1')
  expect(screen.getByText(/本轮已经看过参考表达/)).toBeVisible()
})

it('records a hint before showing it and sends the next answer at the committed revision', async () => {
  const { api } = await openReview()
  vi.mocked(api.mutate).mockImplementation(async (_path, _payload, requestId) => ({ ...operation(), request_id: requestId, result_type: 'review_hint', result: { ...review, revision: 2, used_hint: true, hint: 'Last weekend, I ...' } }))
  fireEvent.change(screen.getByRole('textbox', { name: '我的复习回答' }), { target: { value: 'My draft survives a hint.' } })
  fireEvent.click(screen.getByRole('button', { name: '给我一个复习开头并记录提示' }))
  expect(await screen.findByText('Last weekend, I ...')).toBeVisible()
  expect(screen.getByRole('textbox', { name: '我的复习回答' })).toHaveValue('My draft survives a hint.')
  fireEvent.click(screen.getByRole('button', { name: '提交复习回答' }))
  await waitFor(() => expect(api.mutate).toHaveBeenLastCalledWith('/reviews/review-1/attempts', { expected_revision: 2, text: 'My draft survives a hint.' }, expect.any(String)))
})

it('hides the next prompt after an assessed response and does not submit another answer before due', async () => {
  const { api } = await openReview()
  const result = reviewAnswer()
  vi.mocked(api.reviews).mockResolvedValue([])
  vi.mocked(api.mutate).mockImplementation(async (_path, _payload, requestId) => ({ ...operation(), request_id: requestId, result_type: 'review', result }))
  fireEvent.change(screen.getByRole('textbox', { name: '我的复习回答' }), { target: { value: result.attempt.transcript } })
  fireEvent.click(screen.getByRole('button', { name: '提交复习回答' }))
  expect(await screen.findByRole('heading', { name: '等待到期的新问题' })).toBeVisible()
  expect(screen.getByText(/已保存文字达成证据/)).toBeVisible()
  expect(screen.getByRole('button', { name: '提交复习回答' })).toBeDisabled()
  expect(screen.getByRole('button', { name: '显示参考表达并记录提示' })).toBeDisabled()
  expect(screen.getByRole('textbox', { name: '我的复习回答' })).toHaveValue('')
})

it('offers repeated practice when authored variants are exhausted without claiming independence', async () => {
  await openReview(mockLearningApi(), { ...review, cycle: 2, independent_variant_available: false, availability_reason: '已用完当前内容中的新问题；可以继续重复练习，本次不会增加独立迁移记录。' })
  expect(screen.getByText(/本次不会增加独立迁移记录/)).toBeVisible()
  fireEvent.change(screen.getByRole('textbox', { name: '我的复习回答' }), { target: { value: 'Another repeated answer.' } })
  expect(screen.getByRole('button', { name: '提交复习回答' })).toBeEnabled()
})

it('shows saved early and unavailable answers as separate progress facts', async () => {
  const api = mockLearningApi()
  vi.mocked(api.progress).mockResolvedValue({ sessions_completed: 1, attempts_count: 2, retries_count: 0, reviews_completed: 0, independent_transfer_count: 0,
    review_attempts_count: 2, early_practice_count: 1, unassessed_reviews_count: 1,
    evidence: [{ attempt_id: 'early', session_id: review.session_id, review_item_id: review.review_item_id, kind: 'practice', status: 'available', description: '到期前的练习，未计为延迟迁移。' },
      { attempt_id: 'unavailable', session_id: review.session_id, review_item_id: review.review_item_id, kind: 'practice', status: 'unavailable', description: '评价暂不可用，排程和新问题均保留。' }] })
  render(<LearningWorkspace active api={api} />)
  fireEvent.click(await screen.findByRole('button', { name: '练习进度' }))
  expect(await screen.findByText('到期前的练习，未计为延迟迁移。')).toBeVisible()
  expect(screen.getByText('评价暂不可用，排程和新问题均保留。')).toBeVisible()
  expect(screen.getByText('到期且已评估的回答').nextElementSibling).toHaveTextContent('0')
  expect(screen.getByText('独立迁移的记录').nextElementSibling).toHaveTextContent('0')
})

it('opens the exact saved review answers from progress after a refresh', async () => {
  const api = mockLearningApi()
  const result = reviewAnswer()
  vi.mocked(api.review).mockResolvedValue({ ...result.review, attempts: [result.attempt] })
  vi.mocked(api.progress).mockResolvedValue({ sessions_completed: 1, attempts_count: 1, retries_count: 0, reviews_completed: 1, independent_transfer_count: 1,
    evidence: [{ attempt_id: result.attempt.attempt_id, session_id: review.session_id, review_item_id: review.review_item_id, kind: 'transfer', status: 'available', description: '本次文字达成已有依据。' }] })
  render(<LearningWorkspace active api={api} />)
  fireEvent.click(await screen.findByRole('button', { name: '练习进度' }))
  fireEvent.click(await screen.findByRole('button', { name: '查看对应尝试' }))
  expect(await screen.findByRole('region', { name: '已保存的复习回答' })).toBeVisible()
  expect(screen.getByText(result.attempt.transcript)).toBeVisible()
  expect(screen.getByText(new RegExp(result.attempt.evaluation.reason))).toBeVisible()
  expect(api.review).toHaveBeenCalledWith('review-1')
})

describe('review response validation', () => {
  it.each(['review_saved', 'review_reveal', 'review_hint', 'review_deferred', 'review'])('rejects malformed %s success so the original request remains recoverable', result_type => {
    expect(() => parseOperation({ ...operation(), result_type, result: { review_item_id: 'review-1' } })).toThrow(LearningApiError)
  })
})

it('keeps identity recovery while reviewing and reloads the queue for the same learner', async () => {
  const api = mockLearningApi()
  vi.mocked(api.reviews).mockResolvedValue([review])
  const { result } = renderHook(() => useLearningFlow(api))
  await waitFor(() => expect(result.current.identity).not.toBeNull())
  vi.mocked(api.mutate).mockRejectedValueOnce(new LearningApiError('Expired identity', 'identity_required', false, 401))
  await act(async () => result.current.mutate('review', '/reviews/review-1/attempts', { expected_revision: 1, text: 'My new answer' }, 'session-1'))
  expect(result.current.recovery).toBe('connect')
  await act(async () => result.current.retryPending())
  expect(result.current.recovery).toBeNull()
  expect(result.current.reviews[0].revision).toBe(1)
})
