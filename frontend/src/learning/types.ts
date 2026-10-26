// INT2 HTTP views. Session revisions are persisted record revisions, starting at 1.
export type CorrectionMode = 'immediate' | 'after_session'
export type PracticeMode = 'read_aloud' | 'free_response'
export type Availability = 'available' | 'partial' | 'unavailable'

export interface LearningProfile {
  goal: 'daily' | 'travel' | 'work'
  daily_minutes: number
  correction_mode: CorrectionMode
  retain_audio: boolean
  onboarding_completed: boolean
}

export interface LearningTask {
  task_id: string
  content_version: string
  title: string
  description: string
  estimated_minutes: number
  learner_role: string
  coach_role: string
  objectives: string[]
  examples: string[]
  target_expressions: string[]
  criteria: Array<{ criterion_id: string; description: string }>
}

export interface LearningAudio {
  audio_asset_id?: string | null
  url?: string | null
  state: 'available' | 'not_saved' | 'expired' | 'unavailable'
  expires_at?: string | null
}

export interface LearningTurn {
  turn_id: string
  role: 'user' | 'assistant'
  text: string
  audio?: LearningAudio | null
}

export interface LearningTextSuggestion {
  original: string
  corrected: string
  explanation: string
  category: 'grammar' | 'vocabulary' | 'clarity'
  source: 'text'
}

export interface LearningCoaching {
  source_turn_id: string
  status: 'available' | 'unavailable' | 'deferred'
  reason: string
  suggestions: LearningTextSuggestion[]
}

export interface LearningFocus {
  focus_id: string
  source_turn_id: string
  original: string
  suggested: string
  explanation: string
  category: string
  source: 'text' | 'acoustic' | 'model' | 'self_report'
}

export interface LearningFeedback {
  feedback_id: string
  source_revision: number
  status: Availability
  reason: string
  strength: string
  focuses: LearningFocus[]
  criteria: Array<{
    criterion_id: string
    description: string
    result: 'met' | 'not_met' | 'unverified'
    evidence: string
  }>
}

export interface LearningEvaluation {
  status: Availability
  result: 'met' | 'not_met' | 'unverified'
  reason: string
  source: 'text' | 'acoustic' | 'model' | 'self_report'
}

export interface LearningAttempt {
  attempt_id: string
  session_id: string
  source_turn_id: string
  focus_id: string | null
  parent_attempt_id: string | null
  mode: PracticeMode
  transcript: string
  feedback: LearningFeedback | null
  evaluation: LearningEvaluation
  audio?: LearningAudio | null
  created_at: string
}

export interface LearningSession {
  session_id: string
  task_id: string
  task: LearningTask
  status: 'active' | 'completed' | 'archived'
  revision: number
  turns: LearningTurn[]
  coaching?: LearningCoaching[]
  feedback: LearningFeedback | null
  attempts: LearningAttempt[]
  correction_mode: CorrectionMode
  created_at: string
  updated_at: string
  pending_operations?: LearningOperation[]
}

export interface LearningSessionSummary {
  session_id: string
  task_id: string
  title: string
  status: LearningSession['status']
  updated_at: string
}

export interface LearningReview {
  review_item_id: string
  session_id: string
  focus_id: string | null
  prompt: string | null
  question_id: string | null
  prompt_available: boolean
  due_at: string
  revision: number
  cycle: number
  independent_variant_available: boolean
  availability_reason: string
  attempts?: LearningReviewAttempt[]
  used_hint: boolean
  revealed_answer: boolean
}

export interface ReviewReveal extends LearningReview {
  answer: string
  answer_context?: string
}

export interface ReviewHint extends LearningReview { hint: string }
export type ReviewEvidenceKind = 'shown_answer' | 'hinted' | 'early_practice' | 'source_repeat' | 'repeated_prompt' | 'delayed_transfer'
export interface LearningReviewAttempt extends LearningAttempt {
  review_item_id: string
  question_id: string
  prompt: string
  used_hint: boolean
  revealed_answer: boolean
  independent: boolean
  delayed: boolean
  schedule_advanced: boolean
  variant_consumed: boolean
  evidence_kind: ReviewEvidenceKind
}

export interface ReviewResult {
  review_item_id: string
  attempt: LearningReviewAttempt
  next_due_at: string
  used_hint: boolean
  review: LearningReview
}

export interface LearningProgress {
  sessions_completed: number
  attempts_count: number
  retries_count: number
  reviews_completed: number
  independent_transfer_count: number
  review_attempts_count?: number
  early_practice_count?: number
  source_repeat_count?: number
  hinted_review_count?: number
  repeated_prompt_count?: number
  unassessed_reviews_count?: number
  evidence: Array<{
    attempt_id: string
    session_id: string
    description: string
    kind: 'same_task' | 'transfer' | 'practice'
    status: Availability
    review_item_id?: string
    evidence_kind?: ReviewEvidenceKind
    used_hint?: boolean
    revealed_answer?: boolean
    independent?: boolean
    delayed?: boolean
    schedule_advanced?: boolean
    result?: LearningEvaluation['result']
  }>
}

export interface LearningOperation {
  operation_id: string
  request_id: string
  status: 'pending' | 'succeeded' | 'failed' | 'interrupted'
  session_id?: string | null
  // Known results are validated by their discriminator; unknown result kinds refresh the session.
  result_type?: string | null
  result_id?: string | null
  result?: unknown
  error?: { code: string; message: string } | null
}

export interface LearningBootstrap {
  learner_id: string
  profile: LearningProfile
  pending_operations: LearningOperation[]
}

export type LearningSubmission = { text: string; audio?: never } | { audio: Blob; text?: never }

export interface AttemptInput {
  mode: PracticeMode
  source_turn_id: string
  focus_id: string | null
  parent_attempt_id: string | null
  target_text: string | null
  submission: LearningSubmission
}

export type LearningAction = 'start' | 'turn' | 'hint' | 'finish' | 'feedback' | 'attempt' | 'example' | 'save-review' | 'review' | 'reveal' | 'review-hint' | 'defer-review' | 'profile' | 'delete'

export interface LearningPending {
  request_id: string
  operation_id?: string
  action: LearningAction
  session_id?: string | null
  review_item_id?: string
  learner_id: string
  draft_key?: string
  draft_version?: number
}

export interface LearningHint { hint: string }
export interface PracticeDraft { text: string; audio: Blob | null; version: number }
export interface DraftReceipt { draft_key: string; draft_version: number }
export interface LearningConfirmation {
  request_id: string
  action: LearningAction
  session_id?: string | null
  draft_key?: string
  draft_version?: number
}

export const DEFAULT_PROFILE: LearningProfile = {
  goal: 'daily', daily_minutes: 8, correction_mode: 'after_session',
  retain_audio: false, onboarding_completed: false,
}
