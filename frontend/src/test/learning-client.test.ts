import { beforeEach, describe, expect, it, vi } from 'vitest'
import { bootstrapLearning, learningApi, LearningApiError, parseOperation, parseSession, resetLearningIdentity, submissionBody } from '../learning/client'
import { bytes, deferred } from './setup'
import { learningBootstrap, learningSession, operation } from './learning-fixtures'
import type { LearningSession } from '../learning/types'

const { transportRequest } = vi.hoisted(() => ({ transportRequest: vi.fn() }))
vi.mock('axios', async importOriginal => {
  const actual = await importOriginal<typeof import('axios')>()
  return { ...actual, default: { ...actual.default, create: () => ({ request: transportRequest }) } }
})

beforeEach(() => { resetLearningIdentity(); transportRequest.mockReset() })

describe('learning HTTP boundary', () => {
  it('shares an in-flight bootstrap and fetches current identity and preferences on reconnect', async () => {
    const handshake = deferred<{ data: typeof learningBootstrap }>()
    transportRequest.mockReturnValueOnce(handshake.promise)
    const first = bootstrapLearning()
    const strictMode = bootstrapLearning()
    expect(first).toBe(strictMode)
    expect(transportRequest).toHaveBeenCalledOnce()
    handshake.resolve({ data: structuredClone(learningBootstrap) })
    await first
    const updated = { ...learningBootstrap, learner_id: 'replacement', profile: { ...learningBootstrap.profile, retain_audio: true } }
    transportRequest.mockResolvedValueOnce({ data: updated })
    expect(await bootstrapLearning()).toEqual(updated)
    expect(transportRequest).toHaveBeenCalledTimes(2)
  })

  it.each([{ status: 'succeeded' }, { ...operation(learningSession()), request_id: 'another-request' }])('keeps a committed POST uncertain when its operation response is invalid', async data => {
    transportRequest.mockResolvedValueOnce({ data })
    await expect(learningApi.mutate('/sessions', { task_id: 'weekend' }, 'original-request')).rejects.toMatchObject({ code: 'invalid_response', uncertain: true })
    expect(transportRequest).toHaveBeenCalledWith(expect.objectContaining({ method: 'post', data: { task_id: 'weekend', request_id: 'original-request' } }))
  })

  it('maps the A2 record wrapper while keeping record IDs separate from session IDs', async () => {
    const session = learningSession('saved-session')
    const data = { items: [{ record_id: 'unrelated-record', session_id: session.session_id, data: session, updated_at: session.updated_at }], next_cursor: 'next-page' }
    transportRequest.mockResolvedValue({ data })
    expect(await learningApi.history()).toEqual({ items: [{ session_id: 'saved-session', task_id: 'weekend', title: '描述周末', status: 'active', updated_at: session.updated_at }], next_cursor: 'next-page' })
    expect(await learningApi.session('saved-session')).toEqual(session)
    expect(transportRequest).toHaveBeenLastCalledWith(expect.objectContaining({ url: '/records?kind=session&session_id=saved-session&limit=1' }))
  })

  it('accepts A2 expired audio with a null URL while retaining the text record', async () => {
    const session = learningSession()
    session.turns[0].audio = { audio_asset_id: 'expired-audio', state: 'expired', url: null, expires_at: '2026-10-20T08:00:00Z' }
    transportRequest.mockResolvedValueOnce({ data: { items: [{ data: session }], next_cursor: null } })
    expect((await learningApi.session(session.session_id)).turns[0].audio?.state).toBe('expired')
  })

  it('rejects incomplete nested task and session JSON before rendering', async () => {
    const session = learningSession()
    transportRequest.mockResolvedValueOnce({ data: { items: [{ data: { ...session, task: { ...session.task, objectives: null } } }], next_cursor: null } })
    await expect(learningApi.history()).rejects.toBeInstanceOf(LearningApiError)
    transportRequest.mockResolvedValueOnce({ data: [{ ...session.task, criteria: [{}] }] })
    await expect(learningApi.tasks()).rejects.toMatchObject({ code: 'invalid_response' })
  })

  it('looks up an existing request and exports text JSON', async () => {
    transportRequest.mockResolvedValueOnce({ data: operation(learningSession()) })
    expect((await learningApi.requestStatus('request-1')).operation_id).toBe('operation-1')
    expect(transportRequest).toHaveBeenLastCalledWith(expect.objectContaining({ method: 'get', url: '/requests/request-1' }))
    const exported = { learner_id: 'learner-1', records: [{ data: learningSession() }] }
    transportRequest.mockResolvedValueOnce({ data: exported })
    const blob = await learningApi.exportData()
    expect(blob.type).toBe('application/json')
    expect(JSON.parse(new TextDecoder().decode(await bytes(blob)))).toEqual(exported)
  })

  it('accepts real INT2 session snapshots and null audio metadata with HTTP revisions from 1', async () => {
    const session = learningSession()
    session.turns[0].audio = { state: 'not_saved', url: null, audio_asset_id: null, expires_at: null }
    session.attempts.push({ attempt_id: 'original', session_id: session.session_id, source_turn_id: 'turn-1', focus_id: null, parent_attempt_id: null,
      mode: 'free_response', transcript: 'I went hiking.', feedback: null, audio: null, created_at: session.created_at,
      evaluation: { status: 'unavailable', result: 'unverified', source: 'text', reason: 'Evaluation unavailable' } })
    const result = { ...operation(session), result_id: session.session_id, error: null }
    transportRequest.mockResolvedValueOnce({ data: result })
    expect(await learningApi.mutate('/sessions', { task_id: 'weekend' }, result.request_id)).toEqual(result)
    expect(() => parseSession({ ...session, revision: 0 })).toThrow(LearningApiError)
    expect(() => parseSession({ ...session, revision: 1.5 })).toThrow(LearningApiError)
    expect(() => parseSession({ ...session, turns: [{ ...session.turns[0], audio: { state: 'available', url: 12 } }] })).toThrow(LearningApiError)
  })

  it('validates hint, attempt and example audio operation results before rendering', () => {
    const envelope = { ...operation(), session_id: 'session-1', error: null, result_id: null }
    expect(parseOperation({ ...envelope, result_type: 'hint', result: { hint: 'Last weekend, I…' } }).result_type).toBe('hint')
    expect(parseOperation({ ...envelope, result_type: 'result', result: { hint: 'Last weekend, I…' } }).result).toEqual({ hint: 'Last weekend, I…' })
    expect(parseOperation({ ...envelope, result_type: 'audio', result: { state: 'available', audio_asset_id: 'example-audio', url: '/api/learning/audio/example-audio', expires_at: null } }).result_type).toBe('audio')
    expect(() => parseOperation({ ...envelope, result_type: 'session', result: { session_id: 'missing-snapshot' } })).toThrow(LearningApiError)
    expect(() => parseOperation({ ...envelope, result_type: 'hint', result: { hint: null } })).toThrow(LearningApiError)
    expect(() => parseOperation({ ...envelope, result_type: 'audio', result: { state: 'available', audio_asset_id: false } })).toThrow(LearningApiError)
    expect(() => parseOperation({ ...envelope, result_type: 'attempt', result: { attempt_id: 'missing-fields' } })).toThrow(LearningApiError)
  })

  it.each([
    [401, 'identity_required'], [409, 'revision_conflict'], [503, 'review_unavailable'],
  ])('preserves HTTP %s and the server recovery code %s', async (status, code) => {
    transportRequest.mockRejectedValueOnce({ isAxiosError: true, response: { status, data: { detail: { code, message: 'Server explanation' } } } })
    await expect(learningApi.reviews()).rejects.toMatchObject({ status, code, message: 'Server explanation', uncertain: false })
  })

  it('sends real attempt audio multipart without null optional fields or a simultaneous text answer', async () => {
    const form = submissionBody({ session_id: 'session-1', source_revision: 4, source_turn_id: 'turn-1', focus_id: null, parent_attempt_id: null,
      mode: 'free_response', target_text: null, text: undefined }, new Blob(['recording'], { type: 'audio/wav' }))
    transportRequest.mockResolvedValueOnce({ data: { ...operation(), request_id: 'attempt-request' } })
    await learningApi.mutate('/attempts', form, 'attempt-request')
    expect(form).toBeInstanceOf(FormData)
    const data = form as FormData
    expect(data.get('request_id')).toBe('attempt-request')
    expect(data.get('source_revision')).toBe('4')
    expect(data.has('target_text')).toBe(false)
    expect(data.has('parent_attempt_id')).toBe(false)
    expect(data.has('focus_id')).toBe(false)
    expect(data.has('text')).toBe(false)
    expect((data.get('audio') as File).type).toBe('audio/wav')
  })

  it('accepts optional coaching from real session record and operation snapshots', async () => {
    const session: LearningSession = { ...learningSession(), coaching: [{ source_turn_id: 'user-1', status: 'available',
      reason: 'Advice uses quoted text; acoustic pronunciation was not assessed.',
      suggestions: [{ original: 'go hiking', corrected: 'went hiking', explanation: 'Use past tense.', category: 'grammar', source: 'text' }],
    }] }
    session.turns.push({ turn_id: 'user-1', role: 'user', text: 'I go hiking yesterday.' })
    transportRequest.mockResolvedValueOnce({ data: { items: [{ record_id: 'record-1', revision: session.revision, data: session }], next_cursor: null } })
    expect(await learningApi.session(session.session_id)).toMatchObject({ coaching: session.coaching })
    expect(parseOperation(operation(session)).result).toMatchObject({ coaching: session.coaching })
    expect(() => parseSession(learningSession())).not.toThrow()
  })

  it.each([
    { source_turn_id: 'user-1', status: 'available', reason: 'Advice', suggestions: null },
    { source_turn_id: 'user-1', status: 'unknown', reason: 'Advice', suggestions: [] },
    { source_turn_id: 'user-1', status: 'available', reason: null, suggestions: [] },
    { source_turn_id: 'missing-turn', status: 'available', reason: 'Advice', suggestions: [] },
    { source_turn_id: 'coach-0', status: 'available', reason: 'Advice', suggestions: [] },
    { source_turn_id: 'user-1', status: 'available', reason: 'Advice', suggestions: [{ original: 'invented quote', corrected: 'went hiking', explanation: 'Advice', category: 'grammar', source: 'text' }] },
    { source_turn_id: 'user-1', status: 'available', reason: 'Advice', suggestions: [{ original: 'go hiking', corrected: 'go hiking', explanation: 'Advice', category: 'grammar', source: 'text' }] },
    { source_turn_id: 'user-1', status: 'available', reason: 'Advice', suggestions: [{ original: 'go hiking', corrected: 'went hiking', explanation: 'Advice', category: 'grammar', source: 'acoustic' }] },
    { source_turn_id: 'user-1', status: 'available', reason: 'Advice', suggestions: [{ original: 'go hiking', corrected: 'went hiking', explanation: 'Advice', category: 'pronunciation', source: 'text' }] },
    { source_turn_id: 'user-1', status: 'unavailable', reason: 'Advice', suggestions: [{ original: 'go hiking', corrected: 'went hiking', explanation: 'Advice', category: 'grammar', source: 'text' }] },
  ])('rejects malformed or ungrounded coaching before rendering: %j', coaching => {
    const session = learningSession()
    session.turns.push({ turn_id: 'user-1', role: 'user', text: 'I go hiking yesterday.' })
    expect(() => parseSession({ ...session, coaching: [coaching] })).toThrow(LearningApiError)
  })

  it('patches only profile fields accepted by A2', async () => {
    transportRequest.mockResolvedValueOnce({ data: learningBootstrap.profile })
    await learningApi.profile(learningBootstrap.profile)
    expect(transportRequest).toHaveBeenCalledWith(expect.objectContaining({ method: 'patch', url: '/profile', data: learningBootstrap.profile }))
    expect(transportRequest.mock.calls[0][0].data).not.toHaveProperty('request_id')
  })
})
