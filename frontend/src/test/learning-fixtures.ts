import { vi } from 'vitest'
import type { LearningApi } from '../learning/client'
import type { LearningBootstrap, LearningFeedback, LearningOperation, LearningSession, LearningTask, LearningTranscription, TranscriptionContext } from '../learning/types'

export const learningTask: LearningTask = {
  task_id: 'weekend', content_version: '1', title: '描述周末', description: '告诉教练你做了什么，和谁一起，有什么感受。', estimated_minutes: 8,
  learner_role: '分享周末的朋友', coach_role: '朋友', objectives: ['说清做了什么', '说明和谁一起', '表达自己的感受'],
  examples: ['I went hiking with my friends.', 'It was really enjoyable.'], target_expressions: ['I went hiking.'],
  criteria: [{ criterion_id: 'activity', description: '说清做了什么' }],
}
export const learningFeedback: LearningFeedback = {
  feedback_id: 'feedback-1', source_revision: 2, status: 'available', reason: '基于文字表达提供建议，未评估音素发音。',
  strength: '清楚说明了和朋友一起活动。',
  focuses: [{ focus_id: 'past-tense', source_turn_id: 'turn-1', original: 'I go hiking yesterday.', suggested: 'I went hiking yesterday.', explanation: '昨天发生的活动使用过去时。', category: 'grammar', source: 'text' }],
  criteria: [{ criterion_id: 'activity', description: '说清做了什么', result: 'met', evidence: 'I go hiking yesterday.' }],
}
export function learningSession(id = 'session-1'): LearningSession {
  return { session_id: id, task_id: learningTask.task_id, task: learningTask, status: 'active', revision: 1, turns: [
    { turn_id: 'coach-0', role: 'assistant', text: 'What did you do over the weekend?', audio: { state: 'available', audio_asset_id: 'coach-audio' } },
  ], feedback: null, attempts: [], correction_mode: 'after_session', created_at: '2026-10-23T08:00:00Z', updated_at: '2026-10-23T08:00:00Z' }
}
export const learningBootstrap: LearningBootstrap = {
  learner_id: 'learner-1', profile: { goal: 'daily', daily_minutes: 8, correction_mode: 'after_session', retain_audio: false, onboarding_completed: true }, pending_operations: [],
}
export function operation(session?: LearningSession, status: LearningOperation['status'] = 'succeeded'): LearningOperation {
  return { operation_id: 'operation-1', request_id: 'request-1', status, session_id: session?.session_id,
    result_type: session ? 'session' : undefined, result: session }
}
export function learningTranscription(context: TranscriptionContext = { kind: 'turn', session_id: 'session-1', expected_revision: 1 }, id = 'transcription-1'): LearningTranscription {
  return { transcription_id: id, status: 'ready', context, machine_transcript: 'I go hiking yesterday.',
    audio: { state: 'available', audio_asset_id: 'draft-audio', url: '/api/learning/audio/draft-audio' }, received_at: '2026-10-26T08:00:00Z', expires_at: '2026-10-26T09:00:00Z' }
}
export function transcriptionOperation(context?: TranscriptionContext, requestId = 'transcription-request', id?: string): LearningOperation {
  return { operation_id: 'transcription-operation', request_id: requestId, status: 'succeeded', action: 'transcribe', result_type: 'transcription', result: learningTranscription(context, id) }
}
export function mockLearningApi(): LearningApi {
  return {
    bootstrap: vi.fn(async () => structuredClone(learningBootstrap)),
    tasks: vi.fn(async () => [structuredClone(learningTask)]),
    history: vi.fn(async () => ({ items: [], next_cursor: null })),
    session: vi.fn(async (id: string) => learningSession(id)),
    reviews: vi.fn(async () => []),
    review: vi.fn(async () => { throw new Error('No review lookup configured') }),
    progress: vi.fn(async () => ({ sessions_completed: 0, attempts_count: 0, retries_count: 0, reviews_completed: 0, independent_transfer_count: 0, evidence: [] })),
    profile: vi.fn(async profile => profile),
    mutate: vi.fn(async () => operation(learningSession())),
    getTranscription: vi.fn(async (id: string) => learningTranscription(undefined, id)),
    deleteTranscription: vi.fn(async () => {}),
    deleteTranscriptionRequest: vi.fn(async () => {}),
    operation: vi.fn(async () => operation(learningSession())),
    requestStatus: vi.fn(async () => { throw new Error('No request lookup configured') }),
    exportData: vi.fn(async () => new Blob(['{"records":[]}'], { type: 'application/json' })),
    deleteData: vi.fn(async () => {}),
  }
}
