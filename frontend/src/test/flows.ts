import { act, fireEvent, screen } from '@testing-library/react'
import { emitAudio, tone } from './setup'

export async function recordPronunciation() {
  fireEvent.click(screen.getByRole('button', { name: /开始录音/ }))
  await screen.findByRole('button', { name: /停止录音/ })
  act(() => emitAudio([tone()]))
  fireEvent.click(screen.getByRole('button', { name: /停止录音/ }))
  await screen.findByRole('button', { name: /分析语音/ })
}

export const basicResult = { overall_score: 78, recognized_text: 'I went home.', corrections: [], suggestions: ['Slow down.'] }
export const aiResult = {
  overall_score: 81,
  recognized_text: 'We has a meeting.',
  grammar_corrections: [{ type: 'grammar', original: 'has', corrected: 'have', explanation: 'Use the plural verb.' }],
  pronunciation_feedback: [{ type: 'pronunciation', original: 'meeting', corrected: 'meeting', explanation: 'Stress the first syllable.' }],
  scenario_suggestions: [{ scenario_type: 'daily_conversation', difficulty_level: 'intermediate', reason: 'Practise everyday language.' }],
  personalized_tips: ['Read aloud slowly.'],
  difficulty_level: 'intermediate',
  next_scenario: 'daily_conversation-intermediate',
  confidence_score: 0.7,
  detailed_analysis: 'AI 未配置，本次回退基础分析。',
}
export const scenarios = [
  { scenario_type: 'daily_conversation', difficulty_level: 'beginner', display_name: 'Scenario A' },
  { scenario_type: 'business_meeting', difficulty_level: 'intermediate', display_name: 'Scenario B' },
  { scenario_type: 'daily_conversation', difficulty_level: 'intermediate', display_name: 'Scenario C' },
]
export const scenarioContext = (description: string) => ({ context_description: description, sample_dialogues: [], key_vocabulary: [], grammar_focus: [], pronunciation_targets: [] })
