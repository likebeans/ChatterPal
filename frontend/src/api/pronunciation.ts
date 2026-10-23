export interface Correction {
  type: string
  original: string
  corrected: string
  explanation: string
}
export interface BasicResult {
  overall_score: number
  recognized_text: string
  corrections: Correction[]
  suggestions: string[]
}
export interface AIResult {
  overall_score: number
  recognized_text: string
  grammar_corrections: Correction[]
  pronunciation_feedback: Correction[]
  scenario_suggestions: Array<{ scenario_type: string; difficulty_level: string; reason: string }>
  personalized_tips: string[]
  difficulty_level: string
  next_scenario: string | null
  confidence_score: number
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
  return value !== null && typeof value === 'object'
}
function strings(value: unknown): value is string[] {
  return Array.isArray(value) && value.every(item => typeof item === 'string')
}
function corrections(value: unknown): value is Correction[] {
  return Array.isArray(value) && value.every(item => isRecord(item) && ['type', 'original', 'corrected', 'explanation'].every(key => typeof item[key] === 'string'))
}
function scoreAndTranscript(value: unknown): value is Record<string, unknown> {
  return isRecord(value) && typeof value.overall_score === 'number' && Number.isFinite(value.overall_score) && typeof value.recognized_text === 'string'
}
export function isBasicResult(value: unknown): value is BasicResult {
  return scoreAndTranscript(value) && corrections(value.corrections) && strings(value.suggestions)
}
export function isAIResult(value: unknown): value is AIResult {
  return scoreAndTranscript(value) && corrections(value.grammar_corrections) && corrections(value.pronunciation_feedback)
    && Array.isArray(value.scenario_suggestions) && value.scenario_suggestions.every(item => isRecord(item) && ['scenario_type', 'difficulty_level', 'reason'].every(key => typeof item[key] === 'string'))
    && strings(value.personalized_tips) && typeof value.difficulty_level === 'string'
    && (value.next_scenario === null || typeof value.next_scenario === 'string')
    && typeof value.confidence_score === 'number' && Number.isFinite(value.confidence_score) && typeof value.detailed_analysis === 'string'
}
