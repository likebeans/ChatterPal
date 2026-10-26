import type { LearningAttempt, LearningAudio, LearningBootstrap, LearningCoaching, LearningFeedback, LearningFocus, LearningHint, LearningOperation, LearningProfile, LearningProgress, LearningReview, LearningReviewAttempt, LearningSession, LearningTask, LearningTextSuggestion, LearningTranscription, LearningTurn, TranscriptionContext, ReviewHint, ReviewResult, ReviewReveal } from './types'

type ObjectValue = Record<string, unknown>
export function isObject(value: unknown): value is ObjectValue { return value !== null && typeof value === 'object' && !Array.isArray(value) }
const text = (value: unknown): value is string => typeof value === 'string'
const number = (value: unknown) => typeof value === 'number' && Number.isFinite(value) && value >= 0
const revision = (value: unknown) => typeof value === 'number' && Number.isInteger(value) && value >= 1
const array = <T,>(value: unknown, check: (item: unknown) => item is T): value is T[] => Array.isArray(value) && value.every(check)
const choice = (value: unknown, options: string[]) => text(value) && options.includes(value)
const nullableText = (value: unknown) => value === null || text(value)
const optionalText = (value: unknown) => value == null || text(value)
const available = (value: unknown) => choice(value, ['available', 'partial', 'unavailable'])
const source = (value: unknown) => choice(value, ['text', 'acoustic', 'model', 'self_report'])

export function isProfile(value: unknown): value is LearningProfile {
  return isObject(value) && choice(value.goal, ['daily', 'travel', 'work']) && number(value.daily_minutes)
    && choice(value.correction_mode, ['immediate', 'after_session']) && typeof value.retain_audio === 'boolean' && typeof value.onboarding_completed === 'boolean'
}
export function isTask(value: unknown): value is LearningTask {
  return isObject(value) && ['task_id', 'content_version', 'title', 'description', 'learner_role', 'coach_role'].every(key => text(value[key]))
    && number(value.estimated_minutes) && array(value.objectives, text) && array(value.examples, text) && array(value.target_expressions, text)
    && Array.isArray(value.criteria) && value.criteria.every(item => isObject(item) && text(item.criterion_id) && text(item.description))
}
export function isAudio(value: unknown): value is LearningAudio {
  // A2 metadata may include media_type, retention and integrity fields as well.
  return isObject(value) && choice(value.state, ['available', 'not_saved', 'expired', 'unavailable'])
    && optionalText(value.url) && optionalText(value.audio_asset_id) && optionalText(value.expires_at)
}
export function isTranscriptionContext(value: unknown): value is TranscriptionContext {
  if (!isObject(value)) return false
  if (value.kind === 'turn') return text(value.session_id) && revision(value.expected_revision)
  if (value.kind === 'review') return text(value.review_item_id) && revision(value.expected_revision)
  return value.kind === 'attempt' && text(value.session_id) && revision(value.source_revision) && text(value.source_turn_id)
    && nullableText(value.focus_id) && nullableText(value.parent_attempt_id) && choice(value.mode, ['free_response', 'read_aloud']) && nullableText(value.target_text)
}
export function isTranscription(value: unknown): value is LearningTranscription {
  return isObject(value) && text(value.transcription_id) && choice(value.status, ['ready', 'consumed', 'expired'])
    && isTranscriptionContext(value.context) && text(value.machine_transcript) && isAudio(value.audio) && text(value.received_at) && text(value.expires_at)
}
function isConfirmedText(value: ObjectValue) {
  return ['machine_transcript', 'confirmed_text', 'transcription_id', 'confirmed_at'].every(key => optionalText(value[key]))
}
export function sameTranscriptionContext(first: TranscriptionContext, second: TranscriptionContext) {
  return Object.keys(first).length === Object.keys(second).length && Object.entries(first).every(([key, value]) => second[key as keyof TranscriptionContext] === value)
}
export function isTurn(value: unknown): value is LearningTurn {
  return isObject(value) && text(value.turn_id) && choice(value.role, ['user', 'assistant']) && text(value.text)
    && (value.audio == null || isAudio(value.audio)) && isConfirmedText(value)
}
function isTextSuggestion(value: unknown): value is LearningTextSuggestion {
  return isObject(value) && ['original', 'corrected', 'explanation'].every(key => text(value[key]) && Boolean(value[key].trim()))
    && value.original !== value.corrected && choice(value.category, ['grammar', 'vocabulary', 'clarity']) && value.source === 'text'
}
export function isCoaching(value: unknown): value is LearningCoaching {
  return isObject(value) && text(value.source_turn_id) && choice(value.status, ['available', 'unavailable', 'deferred'])
    && text(value.reason) && array(value.suggestions, isTextSuggestion) && value.suggestions.length <= 1
    && (value.status === 'available' || value.suggestions.length === 0)
}
export function isFocus(value: unknown): value is LearningFocus {
  return isObject(value) && ['focus_id', 'source_turn_id', 'original', 'suggested', 'explanation', 'category'].every(key => text(value[key])) && source(value.source)
}
export function isFeedback(value: unknown): value is LearningFeedback {
  return isObject(value) && text(value.feedback_id) && revision(value.source_revision) && available(value.status)
    && text(value.reason) && text(value.strength) && array(value.focuses, isFocus) && value.focuses.length <= 2 && Array.isArray(value.criteria)
    && value.criteria.every(item => isObject(item) && text(item.criterion_id) && text(item.description) && choice(item.result, ['met', 'not_met', 'unverified']) && text(item.evidence))
}
export function isAttempt(value: unknown): value is LearningAttempt {
  return isObject(value) && ['attempt_id', 'session_id', 'source_turn_id', 'transcript', 'created_at'].every(key => text(value[key]))
    && nullableText(value.focus_id) && nullableText(value.parent_attempt_id) && choice(value.mode, ['read_aloud', 'free_response'])
    && (value.feedback === null || isFeedback(value.feedback)) && isObject(value.evaluation) && available(value.evaluation.status)
    && choice(value.evaluation.result, ['met', 'not_met', 'unverified']) && source(value.evaluation.source) && text(value.evaluation.reason)
    && (value.audio == null || isAudio(value.audio)) && isConfirmedText(value)
}
export function isSession(value: unknown): value is LearningSession {
  if (!isObject(value) || !array(value.turns, isTurn)) return false
  const turns = value.turns
  return ['session_id', 'task_id', 'created_at', 'updated_at'].every(key => text(value[key]))
    && choice(value.status, ['active', 'completed', 'archived']) && choice(value.correction_mode, ['immediate', 'after_session'])
    && revision(value.revision) && isTask(value.task) && array(value.attempts, isAttempt)
    && (value.coaching === undefined || (array(value.coaching, isCoaching) && value.coaching.every(coaching => turns.some(turn => turn.role === 'user'
      && turn.turn_id === coaching.source_turn_id && coaching.suggestions.every(suggestion => turn.text.includes(suggestion.original))))))
    && (value.feedback === null || isFeedback(value.feedback))
}
export function isHint(value: unknown): value is LearningHint { return isObject(value) && text(value.hint) }
export function isOperation(value: unknown): value is LearningOperation {
  if (!isObject(value) || !text(value.operation_id) || !text(value.request_id) || !choice(value.status, ['pending', 'succeeded', 'failed', 'interrupted'])
    || !optionalText(value.session_id) || !optionalText(value.result_id)
    || !optionalText(value.result_type)
    || !(value.error == null || (isObject(value.error) && text(value.error.code) && text(value.error.message)))) return false
  if (value.status !== 'succeeded') return true
  if (value.result_type === 'transcription') return isTranscription(value.result)
  if (value.result_type === 'session') return isSession(value.result)
  if (value.result_type === 'hint') return isHint(value.result)
  if (value.result_type === 'attempt') return isAttempt(value.result)
  if (value.result_type === 'audio') return isAudio(value.result)
  if (value.result_type === 'review_saved' || value.result_type === 'review_deferred') return isReview(value.result)
  if (value.result_type === 'review_reveal') return isReviewReveal(value.result)
  if (value.result_type === 'review_hint') return isReviewHint(value.result)
  if (value.result_type === 'review') return isReviewResult(value.result)
  return true
}
export function isBootstrap(value: unknown): value is LearningBootstrap {
  return isObject(value) && text(value.learner_id) && isProfile(value.profile) && array(value.pending_operations, isOperation)
}
export function isReview(value: unknown): value is LearningReview {
  return isObject(value) && ['review_item_id', 'session_id', 'due_at', 'availability_reason'].every(key => text(value[key]))
    && nullableText(value.focus_id) && revision(value.revision) && Number.isInteger(value.cycle) && number(value.cycle)
    && typeof value.prompt_available === 'boolean' && typeof value.independent_variant_available === 'boolean'
    && typeof value.used_hint === 'boolean' && typeof value.revealed_answer === 'boolean'
    && (value.prompt_available ? text(value.prompt) && Boolean(value.prompt.trim()) && text(value.question_id) : value.prompt === null && value.question_id === null)
    && (!value.revealed_answer || value.used_hint)
    && (value.attempts === undefined || array(value.attempts, isReviewAttempt))
}
export function isReviewReveal(value: unknown): value is ReviewReveal {
  return isReview(value) && isObject(value) && text(value.answer) && value.used_hint && value.revealed_answer
}
export function isReviewHint(value: unknown): value is ReviewHint {
  return isReview(value) && isObject(value) && text(value.hint) && value.used_hint
}
export function isReviewAttempt(value: unknown): value is LearningReviewAttempt {
  return isAttempt(value) && isObject(value) && ['review_item_id', 'question_id', 'prompt'].every(key => text(value[key]))
    && ['used_hint', 'revealed_answer', 'independent', 'delayed', 'schedule_advanced', 'variant_consumed'].every(key => typeof value[key] === 'boolean')
    && choice(value.evidence_kind, ['shown_answer', 'hinted', 'early_practice', 'source_repeat', 'repeated_prompt', 'delayed_transfer'])
    && (!value.revealed_answer || value.used_hint === true)
    && (!value.independent || value.delayed === true && !value.used_hint && value.evidence_kind === 'delayed_transfer')
    && (!value.schedule_advanced || value.independent === true && value.evaluation.status === 'available' && value.evaluation.result === 'met')
}
export function isReviewResult(value: unknown): value is ReviewResult {
  return isObject(value) && text(value.review_item_id) && isReviewAttempt(value.attempt)
    && text(value.next_due_at) && typeof value.used_hint === 'boolean' && isReview(value.review)
    && value.review.review_item_id === value.review_item_id && value.attempt.review_item_id === value.review_item_id
    && value.used_hint === value.attempt.used_hint && value.review.due_at === value.next_due_at
}
export function isProgress(value: unknown): value is LearningProgress {
  return isObject(value) && ['sessions_completed', 'attempts_count', 'retries_count', 'reviews_completed', 'independent_transfer_count'].every(key => number(value[key]))
    && ['review_attempts_count', 'early_practice_count', 'source_repeat_count', 'hinted_review_count', 'repeated_prompt_count', 'unassessed_reviews_count'].every(key => value[key] === undefined || number(value[key]))
    && Array.isArray(value.evidence) && value.evidence.every(item => isObject(item) && text(item.attempt_id) && text(item.session_id) && text(item.description)
      && choice(item.kind, ['same_task', 'transfer', 'practice']) && available(item.status))
}
