import { fireEvent, render, screen, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import LearningWorkspace from '../learning/LearningWorkspace'
import { learningSession, mockLearningApi, operation } from './learning-fixtures'
import type { LearningCoaching, LearningSession } from '../learning/types'

beforeEach(() => {
  const items = new Map<string, string>()
  Object.defineProperty(window, 'localStorage', { configurable: true, value: {
    getItem: (key: string) => items.get(key) ?? null,
    setItem: (key: string, value: string) => { items.set(key, value) },
    removeItem: (key: string) => { items.delete(key) },
  } })
})

function coachedSession(status: LearningCoaching['status'] = 'available'): LearningSession & { coaching: LearningCoaching[] } {
  const session = learningSession()
  session.correction_mode = status === 'deferred' ? 'after_session' : 'immediate'
  session.revision = 2
  session.turns.push({ turn_id: 'user-1', role: 'user', text: 'I go hiking yesterday.' }, { turn_id: 'coach-1', role: 'assistant', text: 'Who did you go with?' })
  return { ...session, coaching: [{ source_turn_id: 'user-1', status,
    reason: status === 'available' ? 'Advice uses quoted text; acoustic pronunciation was not assessed.'
      : status === 'deferred' ? 'Text suggestions are deferred until the learner ends the session.' : 'Text advice could not be validated; the coach reply is available.',
    suggestions: status === 'available' ? [{ original: 'go hiking', corrected: 'went hiking', explanation: '昨天发生的活动使用过去时。', category: 'grammar', source: 'text' }] : [],
  }] }
}

async function open(session = coachedSession()) {
  const api = mockLearningApi()
  vi.mocked(api.mutate).mockImplementation(async (_path, _payload, requestId) => ({ ...operation(session), request_id: requestId }))
  render(<LearningWorkspace active api={api} />)
  fireEvent.click(await screen.findByRole('button', { name: '开始描述周末' }))
  await screen.findByRole('region', { name: '当前任务练习' })
  return api
}

describe('grounded per-turn coaching', () => {
  it('shows immediate text suggestions beside the referenced user turn and keeps them after browsing history', async () => {
    await open()
    const suggestion = screen.getByRole('region', { name: '本句文字建议' })
    const turn = suggestion.closest('article')!
    expect(within(turn).getByText('I go hiking yesterday.')).toBeVisible()
    expect(within(suggestion).getByText('go hiking')).toBeVisible()
    expect(within(suggestion).getByText('went hiking')).toBeVisible()
    expect(within(suggestion).getByText('昨天发生的活动使用过去时。')).toBeVisible()
    expect(within(suggestion).getByText(/未评估发音/)).toBeVisible()
    expect(screen.getByText('Who did you go with?')).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: '学习记录' }))
    fireEvent.click(screen.getByRole('button', { name: '返回本次练习' }))
    expect(screen.getByText('went hiking')).toBeVisible()
  })

  it.each([
    ['unavailable', '本句文字建议暂不可用'],
    ['deferred', '本句建议将在结束对话后集中反馈'],
  ] as const)('explains %s coaching without hiding the successful reply or inventing suggestions', async (status, label) => {
    await open(coachedSession(status))
    const suggestion = screen.getByRole('region', { name: '本句文字建议' })
    expect(within(suggestion).getByText(new RegExp(label))).toBeVisible()
    expect(screen.getByText('I go hiking yesterday.')).toBeVisible()
    expect(screen.getByText('Who did you go with?')).toBeVisible()
    expect(screen.queryByText('went hiking')).not.toBeInTheDocument()
  })

  it('treats an empty validated suggestion list as no text adjustment instead of a score', async () => {
    const session = coachedSession()
    session.coaching[0].suggestions = []
    await open(session)
    expect(screen.getByText(/本句暂无需要提醒的文字调整/)).toBeVisible()
    expect(screen.queryByText(/得分|准确率/)).not.toBeInTheDocument()
  })
})
