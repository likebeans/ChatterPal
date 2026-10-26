export interface Correction {
  type: string
  original: string
  corrected: string
  explanation: string
}
export interface AssessmentDimension {
  score: number | null
  status: 'measured' | 'estimated' | 'unavailable'
  source: string
  reason: string
  evidence: Record<string, unknown>
  unit?: string | null
  calibrated?: boolean
}
export interface AssessmentMetadata {
  dimensions?: Record<string, AssessmentDimension>
  calibrated?: boolean
  scope?: string
}
export interface BasicResult extends AssessmentMetadata {
  overall_score: number | null
  recognized_text: string
  corrections: Correction[]
  suggestions: string[]
}
export interface AIResult extends AssessmentMetadata {
  overall_score: number | null
  recognized_text: string
  grammar_corrections: Correction[]
  pronunciation_feedback: Correction[]
  scenario_suggestions: Array<{ scenario_type: string; difficulty_level: string; reason: string }>
  personalized_tips: string[]
  difficulty_level: string | null
  next_scenario: string | null
  confidence_score: number | null
  detailed_analysis: string
}
export interface Scenario {
  scenario_type: string
  difficulty_level: string
  display_name: string
}
export interface ScenarioContext {
  scenario_type: string
  difficulty_level: string
  context_description: string
  sample_dialogues: string[]
  key_vocabulary: string[]
  grammar_focus: string[]
  pronunciation_targets: string[]
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}
function score(value: unknown, maximum = 100): boolean {
  return value === null || (typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= maximum)
}
function finiteEvidence(value: unknown): boolean {
  if (typeof value === 'number') return Number.isFinite(value)
  if (Array.isArray(value)) return value.every(finiteEvidence)
  if (isRecord(value)) return Object.values(value).every(finiteEvidence)
  return value === null || typeof value === 'string' || typeof value === 'boolean'
}
function metadata(value: Record<string, unknown>): boolean {
  if (value.calibrated !== undefined && typeof value.calibrated !== 'boolean') return false
  if (value.scope !== undefined && typeof value.scope !== 'string') return false
  if (value.dimensions === undefined) return true
  return isRecord(value.dimensions) && Object.values(value.dimensions).every(item => isRecord(item)
    && score(item.score) && typeof item.status === 'string' && ['measured', 'estimated', 'unavailable'].includes(item.status)
    && ['source', 'reason'].every(key => typeof item[key] === 'string' && String(item[key]).trim().length > 0)
    && isRecord(item.evidence) && finiteEvidence(item.evidence)
    && (item.unit === undefined || item.unit === null || typeof item.unit === 'string')
    && (item.calibrated === undefined || typeof item.calibrated === 'boolean')
    && (item.status !== 'unavailable' || item.score === null))
}
function strings(value: unknown): value is string[] {
  return Array.isArray(value) && value.every(item => typeof item === 'string')
}
function corrections(value: unknown): value is Correction[] {
  return Array.isArray(value) && value.every(item => isRecord(item) && ['type', 'original', 'corrected', 'explanation'].every(key => typeof item[key] === 'string'))
}
function scoreAndTranscript(value: unknown): value is Record<string, unknown> {
  return isRecord(value) && score(value.overall_score) && typeof value.recognized_text === 'string' && metadata(value)
}
export function isBasicResult(value: unknown): value is BasicResult {
  return scoreAndTranscript(value) && corrections(value.corrections) && strings(value.suggestions)
}
export function isAIResult(value: unknown): value is AIResult {
  return scoreAndTranscript(value) && corrections(value.grammar_corrections) && corrections(value.pronunciation_feedback)
    && Array.isArray(value.scenario_suggestions) && value.scenario_suggestions.every(item => isRecord(item) && ['scenario_type', 'difficulty_level', 'reason'].every(key => typeof item[key] === 'string'))
    && strings(value.personalized_tips) && (value.difficulty_level === null || typeof value.difficulty_level === 'string')
    && (value.next_scenario === null || typeof value.next_scenario === 'string')
    && score(value.confidence_score, 1) && typeof value.detailed_analysis === 'string'
}
